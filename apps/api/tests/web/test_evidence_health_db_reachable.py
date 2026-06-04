"""Tests for the ``db_reachable`` field on ``/health/evidence``.

Background
----------
On 2026-05-19 the homepage's Live-Evidence strip silently rendered
all-zeros after Docker Desktop was shut down — indistinguishable
from "DB up but empty". The /goal-page and /open-mcp-opportunities
endpoints surfaced 500s, so the operator was getting two
contradictory signals at once: "evidence pipeline reports zero"
(suggesting empty data) and "some pages can't fetch" (suggesting
DB down). Diagnosing which was true took ~15 minutes.

Fix: ``/health/evidence`` now carries ``db_reachable: bool`` and
``db_error: str | None``. The contract is "never raise, but always
tell the truth" — the endpoint still returns 200 with safe-zero
counts when the DB is unreachable, but the flag makes that state
visibly distinct from a healthy-but-empty database.

These tests pin three invariants the fix relies on:

1. Healthy DSN + reachable DB → ``db_reachable=True`` and
   ``db_error is None``.
2. Non-Postgres DSN (dev shells using SQLite/JSON stores) →
   ``db_reachable=True`` (it's a config choice, not a failure)
   and counts default to zero.
3. Postgres DSN that points at a closed port → ``db_reachable=False``
   and ``db_error`` populated with a short, UI-renderable string.
   No HTTP 5xx. Counts still default to zero.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))


class EvidenceHealthDbReachableTests(unittest.TestCase):
    """Verify the db_reachable / db_error flags reflect reality."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self._previous_env: dict[str, str | None] = {}

    def tearDown(self) -> None:
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tempdir.cleanup()
        # Drop the global cache between tests so one test's evidence
        # answer cannot leak into the next (the endpoint TTL-caches
        # by default; we set the TTL to 0 in each test as a belt).
        from planmyagents_api.web import app as web_app

        web_app._EVIDENCE_HEALTH_CACHE = None

    def _set_env(self, key: str, value: str) -> None:
        self._previous_env.setdefault(key, os.environ.get(key))
        os.environ[key] = value

    def test_non_postgres_dsn_reports_db_reachable_true_with_zeros(self) -> None:
        # A SQLite/JSON DSN is a config choice, not a failure. The
        # endpoint should report db_reachable=True so the UI does not
        # show an alarming "DB down" banner for an intentional setup.
        sqlite_path = self.tempdir / "discovery.sqlite"
        self._set_env("PLANMYAGENTS_DISCOVERY_STORE_URL", str(sqlite_path))
        self._set_env("PLANMYAGENTS_BENCHMARK_STORE_URL", str(sqlite_path))
        self._set_env("PLANMYAGENTS_VERIFICATION_STORE_URL", str(sqlite_path))
        self._set_env("PLANMYAGENTS_HEALTH_EVIDENCE_CACHE_S", "0")

        from planmyagents_api.web.app import _compute_evidence_health

        response = _compute_evidence_health()

        self.assertTrue(
            response.db_reachable,
            "non-Postgres DSN must report db_reachable=True (it's a config "
            "choice, not a failure — pre-fix this was conflated with "
            "actual DB outages)",
        )
        self.assertIsNone(response.db_error)
        # All counts default to zero in non-Postgres mode.
        self.assertEqual(response.benchmark_runs_total, 0)
        self.assertEqual(response.verification_records_total, 0)

    def test_postgres_dsn_pointing_at_closed_port_reports_db_unreachable(
        self,
    ) -> None:
        # Use port 1 — reserved, never bindable; psycopg.connect will
        # raise immediately. The regression we guard against is the
        # endpoint silently returning all-zeros with db_reachable
        # implicitly true (the pre-fix behavior).
        unreachable_dsn = "postgresql://planmyagents:planmyagents@127.0.0.1:1/planmyagents"
        self._set_env("PLANMYAGENTS_DISCOVERY_STORE_URL", unreachable_dsn)
        self._set_env("PLANMYAGENTS_BENCHMARK_STORE_URL", unreachable_dsn)
        self._set_env("PLANMYAGENTS_VERIFICATION_STORE_URL", unreachable_dsn)
        self._set_env("PLANMYAGENTS_HEALTH_EVIDENCE_CACHE_S", "0")

        from planmyagents_api.web.app import _compute_evidence_health

        response = _compute_evidence_health()

        self.assertFalse(
            response.db_reachable,
            "Postgres DSN that fails to connect must surface "
            "db_reachable=False so the UI can distinguish 'system "
            "alive but empty' from 'system down'",
        )
        self.assertIsNotNone(response.db_error)
        assert response.db_error is not None  # for type narrowing
        self.assertGreater(
            len(response.db_error),
            0,
            "db_error must carry a non-empty message — empty-string "
            "would be indistinguishable from 'no error'",
        )
        # The endpoint must NOT raise; counts are safe-zero in this case
        # so the homepage stays renderable (just with the unreachable
        # banner instead of the count grid).
        self.assertEqual(response.benchmark_runs_total, 0)
        # The error string is truncated to keep it UI-renderable.
        self.assertLessEqual(len(response.db_error), 240)

    def test_db_error_is_single_line_safe_for_inline_ui(self) -> None:
        # Ensure no embedded newlines so the strip's tight layout
        # doesn't blow up; the full multi-line stack is in server log.
        unreachable_dsn = "postgresql://planmyagents:planmyagents@127.0.0.1:1/planmyagents"
        self._set_env("PLANMYAGENTS_DISCOVERY_STORE_URL", unreachable_dsn)
        self._set_env("PLANMYAGENTS_BENCHMARK_STORE_URL", unreachable_dsn)
        self._set_env("PLANMYAGENTS_VERIFICATION_STORE_URL", unreachable_dsn)
        self._set_env("PLANMYAGENTS_HEALTH_EVIDENCE_CACHE_S", "0")

        from planmyagents_api.web.app import _compute_evidence_health

        response = _compute_evidence_health()
        assert response.db_error is not None  # for type narrowing
        self.assertNotIn("\n", response.db_error)


if __name__ == "__main__":
    unittest.main()
