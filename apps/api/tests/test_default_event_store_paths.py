"""Regression tests for the default event-store path resolvers.

The audit flagged that four resolvers used ``Path(__file__).resolve().parents[3]``
which resolves to ``apps/`` — one level *above* the repo root. The bug
was silent: the JSONL file just landed in a sibling of the repo, the
dashboard read-side never saw it, and the leaderboard tile stayed
permanently empty.

We pin all four resolvers (demand, discovery-gaps, run-log, capability-
label) to the actual repo root by asserting the resolved default path
sits under the directory that contains the ``Makefile``. This is the
cheapest invariant that catches off-by-one regressions and is also
robust to whether the resolver returns a string or Path.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.demand_recorder import (  # noqa: E402
    resolve_demand_store_path,
)
from planmyagents_api.discovery.discovery_gaps_recorder import (  # noqa: E402
    resolve_discovery_gaps_store_path,
)
from planmyagents_api.planner.capability_label_recorder import (  # noqa: E402
    resolve_capability_label_store_path,
)


class _ScopedEnvUnset:
    """Context manager that unsets a list of env vars while in scope."""

    def __init__(self, *names: str) -> None:
        self._names = names
        self._original: dict[str, str | None] = {}

    def __enter__(self) -> _ScopedEnvUnset:
        for name in self._names:
            self._original[name] = os.environ.get(name)
            os.environ.pop(name, None)
        return self

    def __exit__(self, *args: object) -> None:
        for name, value in self._original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class DefaultEventStorePathsLiveUnderRepoRootTest(unittest.TestCase):
    """One test per resolver — failures will name the offending module."""

    def setUp(self) -> None:
        self.assertTrue(
            (ROOT / "Makefile").exists(),
            "ROOT must point at the repo root for these regressions to mean anything",
        )
        self.expected_data_dir = (ROOT / "data").resolve()

    def _assert_under_data_dir(self, resolved: Path, label: str) -> None:
        self.assertEqual(
            resolved.parent,
            self.expected_data_dir,
            f"{label} default path escaped repo root: {resolved}",
        )

    def test_demand_recorder_default_resolves_under_repo_root(self) -> None:
        with _ScopedEnvUnset("PLANMYAGENTS_DEMAND_STORE_PATH"):
            path = Path(resolve_demand_store_path()).resolve()
        self._assert_under_data_dir(path, "demand_recorder")

    def test_discovery_gaps_recorder_default_resolves_under_repo_root(self) -> None:
        with _ScopedEnvUnset("PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH"):
            path = Path(resolve_discovery_gaps_store_path()).resolve()
        self._assert_under_data_dir(path, "discovery_gaps_recorder")

    def test_capability_label_recorder_default_resolves_under_repo_root(self) -> None:
        # The resolver's layered default (added 2026-05-19) inherits the
        # discovery store URL when it's a Postgres DSN. To exercise the
        # JSON-file branch this test originally pinned, we have to force
        # the discovery URL to a non-Postgres value as well. Setting it
        # to a local JSON file mirrors how a fresh clone without
        # Postgres provisioned would resolve.
        with _ScopedEnvUnset("PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH"):
            previous = os.environ.get("PLANMYAGENTS_DISCOVERY_STORE_URL")
            os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(
                ROOT / "data" / "discovery.json"
            )
            try:
                path = Path(resolve_capability_label_store_path()).resolve()
            finally:
                if previous is None:
                    os.environ.pop("PLANMYAGENTS_DISCOVERY_STORE_URL", None)
                else:
                    os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = previous
        self._assert_under_data_dir(path, "capability_label_recorder")

    def test_capability_label_recorder_inherits_postgres_discovery_dsn(self) -> None:
        # Sibling to the test above. When the discovery URL IS Postgres
        # (the production case), the resolver must return that DSN
        # verbatim — bypassing the JSON file path entirely. Pre-fix
        # this returned a JSON file path even with Postgres available
        # and silently orphaned 15 capability labels in
        # data/capability_labels.json (the 2026-05-19 audit bug; see
        # apps/api/planmyagents_api/planner/capability_label_recorder.py).
        with _ScopedEnvUnset("PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH"):
            previous = os.environ.get("PLANMYAGENTS_DISCOVERY_STORE_URL")
            postgres_dsn = "postgresql://user:pw@example.invalid:5432/planmyagents"
            os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = postgres_dsn
            try:
                resolved = resolve_capability_label_store_path()
            finally:
                if previous is None:
                    os.environ.pop("PLANMYAGENTS_DISCOVERY_STORE_URL", None)
                else:
                    os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = previous
        self.assertEqual(
            resolved,
            postgres_dsn,
            "resolver must return the Postgres DSN verbatim — not "
            "concatenate it with the repo data dir or fall through to "
            "the JSON fallback",
        )

    def test_run_log_default_resolves_under_repo_root(self) -> None:
        # The run-log path resolution is buried in DiscoveryRunLogger.from_env.
        # We exercise it the same way the API does — instantiate from env
        # with the path env var unset, then read the resulting store's
        # path attribute. The store interface exposes ``path`` for the
        # JSONL backend, which is what the resolver hits in this default
        # branch.
        from planmyagents_api.discovery.run_log import DiscoveryRunLogger

        with _ScopedEnvUnset(
            "PLANMYAGENTS_RUN_LOG_STORE_PATH",
            "PLANMYAGENTS_RUN_LOG_ENABLED",
        ):
            logger = DiscoveryRunLogger.default(store_url=None, enabled=True)
        store = logger._store  # noqa: SLF001 - private but stable; tests own it
        self.assertIsNotNone(store, "run-log default branch should construct a store")
        store_path = getattr(store, "path", None)
        self.assertIsNotNone(
            store_path,
            "run-log default branch should produce a JSONL backend with a `.path`",
        )
        self._assert_under_data_dir(Path(store_path).resolve(), "run_log")


if __name__ == "__main__":
    unittest.main()
