"""Tests for capability label store resolution.

Background
----------
On 2026-05-19 the audit found 15 high-frequency capability labels
orphaned in ``data/capability_labels.json`` because
``PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH`` was unset in production.
The pre-fix resolver fell back to the JSON file regardless of where
the rest of the evidence pipeline pointed, so the
``capability_labels`` Postgres table stayed at zero rows despite real
/goal traffic. The dashboard reported "no vocabulary growth" while
labels like ``identity_verification`` (88 hits) were silently
accumulating in a sidecar file no production reader ever opens.

The fix replaces the resolver's strict "default to JSON, opt in
explicitly" policy with a layered default: if the discovery store
URL is Postgres, the label store inherits it; otherwise we keep the
JSON file fallback for dev/test.

These tests pin three invariants the fix relies on:

1. Explicit ``PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH`` env var
   wins over every other signal (tempfile path or alternate DSN).
2. When the env var is unset AND ``discovery_store_url()`` returns
   a Postgres DSN, the resolver inherits that DSN. This is the
   exact production failure mode that hid the orphaned labels.
3. When the env var is unset AND ``discovery_store_url()`` does not
   point at Postgres (dev/test using JSON), the resolver still
   returns the JSON file path so a fresh clone with no Postgres
   keeps working.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))


class CapabilityLabelResolverTests(unittest.TestCase):
    """Resolver must follow the layered-default contract."""

    def setUp(self) -> None:
        self._previous_env: dict[str, str | None] = {}

    def tearDown(self) -> None:
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set_env(self, key: str, value: str | None) -> None:
        self._previous_env.setdefault(key, os.environ.get(key))
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def test_explicit_env_var_wins_over_every_other_signal(self) -> None:
        explicit = "postgresql://user:pw@example.invalid:5432/explicit"
        # Set the discovery URL to something different so we can prove
        # the explicit override actually wins.
        self._set_env(
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            "postgresql://user:pw@example.invalid:5432/discovery",
        )
        self._set_env("PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH", explicit)

        from planmyagents_api.planner.capability_label_recorder import (
            resolve_capability_label_store_path,
        )

        self.assertEqual(
            resolve_capability_label_store_path(),
            explicit,
            "explicit PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH must always "
            "win; this guarantees test tempfiles and unusual prod topologies "
            "can pin the path even when the discovery DSN is Postgres",
        )

    def test_unset_env_with_postgres_discovery_inherits_postgres_dsn(self) -> None:
        # The exact regression: env var unset, discovery URL is Postgres.
        # Pre-fix this returned a JSON file path; post-fix it must
        # return the discovery DSN so labels follow the evidence
        # pipeline instead of orphaning in a sidecar file.
        postgres_dsn = "postgresql://user:pw@example.invalid:5432/planmyagents"
        self._set_env("PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH", None)
        self._set_env("PLANMYAGENTS_DISCOVERY_STORE_URL", postgres_dsn)

        from planmyagents_api.planner.capability_label_recorder import (
            resolve_capability_label_store_path,
        )

        self.assertEqual(
            resolve_capability_label_store_path(),
            postgres_dsn,
            "missing PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH with a "
            "Postgres discovery DSN must inherit the discovery DSN; "
            "otherwise the vocabulary growth loop silently orphans "
            "labels in a JSON sidecar file (the 2026-05-19 audit bug)",
        )

    def test_unset_env_with_non_postgres_discovery_falls_back_to_json(
        self,
    ) -> None:
        # Dev/test scenario: discovery is a JSON file path. The
        # resolver must NOT inherit (the inherited path would point
        # at the discovery JSON, which is the wrong schema entirely);
        # it must fall back to the labels JSON.
        self._set_env("PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH", None)
        self._set_env(
            "PLANMYAGENTS_DISCOVERY_STORE_URL", "/tmp/dev-discovery.json"
        )

        from planmyagents_api.planner.capability_label_recorder import (
            resolve_capability_label_store_path,
        )

        resolved = resolve_capability_label_store_path()
        self.assertTrue(
            resolved.endswith("capability_labels.json"),
            f"expected JSON fallback when discovery URL is not Postgres; "
            f"got {resolved!r}",
        )
        self.assertNotIn(
            "/tmp/dev-discovery.json",
            resolved,
            "resolver must NOT silently inherit a non-Postgres discovery "
            "path — that would point label storage at the wrong schema",
        )


if __name__ == "__main__":
    unittest.main()
