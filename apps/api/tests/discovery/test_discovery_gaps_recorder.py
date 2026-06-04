"""Tests for the defensive discovery-gaps recorder.

The recorder must satisfy two contracts:

1. *Best-effort writes never raise.* The ``/goal`` route calls it
   inline; an exception thrown here would corrupt a user response for
   a metric-only side-effect, which is unacceptable.
2. *Env-var opt-out is honoured.* Tests in the broader suite set
   ``PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED=false`` to keep
   their runs hermetic. A regression that ignored that flag would
   silently leak gap rows into developer JSONL files during CI.

We exercise the recorder against the JSON backend (via env var
override) so the tests are hermetic and don't need a Postgres or
SQLite fixture spun up.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.discovery_gaps_recorder import (  # noqa: E402
    discovery_gaps_recording_enabled,
    load_discovery_gaps_summary,
    record_discovery_gap,
    resolve_discovery_gaps_store_path,
)


class _PatchedEnv:
    """Tiny scoped env patcher — same shape as the one used by the
    escalating-client tests so the suite stays consistent."""

    def __init__(self, **values: str | None) -> None:
        self._values = values
        self._original: dict[str, str | None] = {}

    def __enter__(self) -> _PatchedEnv:
        for key, value in self._values.items():
            self._original[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *args: object) -> None:
        for key, value in self._original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class RecordingEnabledFlagTests(unittest.TestCase):
    def test_default_is_on(self) -> None:
        # The user explicitly asked for gap signal to be persistent.
        # Off-by-default would make the leaderboard tile permanently
        # empty in fresh installs.
        with _PatchedEnv(PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED=None):
            self.assertTrue(discovery_gaps_recording_enabled())

    def test_explicit_false_disables(self) -> None:
        for falsy in ("false", "0", "no", "off", "FALSE", "  False  "):
            with _PatchedEnv(
                PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED=falsy
            ):
                self.assertFalse(
                    discovery_gaps_recording_enabled(),
                    f"expected {falsy!r} to disable recording",
                )

    def test_explicit_true_keeps_enabled(self) -> None:
        for truthy in ("true", "1", "yes", "on", "TRUE", "  yes  "):
            with _PatchedEnv(
                PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED=truthy
            ):
                self.assertTrue(
                    discovery_gaps_recording_enabled(),
                    f"expected {truthy!r} to keep recording on",
                )


class StorePathResolutionTests(unittest.TestCase):
    def test_default_is_repo_local_jsonl(self) -> None:
        with _PatchedEnv(PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH=None):
            path = resolve_discovery_gaps_store_path()
            # Must end with the documented default filename so operators
            # can grep the repo for "discovery_gap_events.jsonl" and
            # find the file.
            self.assertTrue(
                path.endswith("discovery_gap_events.jsonl"),
                f"unexpected default path: {path}",
            )

    def test_default_resolves_under_repo_root(self) -> None:
        # Audit regression: the recorder previously used parents[4]
        # which resolves to `apps/`, not the repo root, so the default
        # JSONL was written to a sibling of the repo. We pin the
        # resolved path to the actual repo root by checking that it
        # lives under the directory that contains the Makefile.
        with _PatchedEnv(PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH=None):
            path = Path(resolve_discovery_gaps_store_path()).resolve()
        repo_root = ROOT  # apps/api/tests/../../../  i.e. the actual repo root
        self.assertTrue(
            (repo_root / "Makefile").exists(),
            "test fixture is wrong: ROOT does not point at repo root",
        )
        self.assertEqual(
            path.parent, (repo_root / "data").resolve(),
            f"discovery-gaps default path escaped repo root: {path}",
        )

    def test_env_override_wins(self) -> None:
        with _PatchedEnv(
            PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH="/tmp/custom.jsonl"
        ):
            self.assertEqual(
                resolve_discovery_gaps_store_path(), "/tmp/custom.jsonl"
            )

    def test_env_override_is_stripped(self) -> None:
        # Operators sometimes paste DSNs with trailing whitespace from
        # secret managers — strip defensively.
        with _PatchedEnv(
            PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH="   /tmp/x.jsonl  "
        ):
            self.assertEqual(
                resolve_discovery_gaps_store_path(), "/tmp/x.jsonl"
            )


class RecordDiscoveryGapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmpdir.name) / "events.jsonl")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_records_one_event_per_missing_capability(self) -> None:
        with _PatchedEnv(
            PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH=self.path,
            PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED="true",
        ):
            written = record_discovery_gap(
                goal="some user goal text",
                missing_capabilities=["cap_alpha", "cap_beta"],
                judge_accepted_by_capability={
                    "cap_alpha": 0,
                    "cap_beta": 0,
                },
                judge_evaluated_by_capability={
                    "cap_alpha": 11,
                    "cap_beta": 0,
                },
                scout_dispatch_by_capability={
                    "cap_alpha": {"dispatched": 6, "returned_zero": 5},
                },
            )
            self.assertEqual(written, 2)

            summary = load_discovery_gaps_summary()
            ids = sorted(s.capability_id for s in summary)
            self.assertEqual(ids, ["cap_alpha", "cap_beta"])
            for entry in summary:
                # judge_accepted == 0 for both → both are zero-yield.
                self.assertEqual(entry.zero_yield_count, 1)
                self.assertEqual(entry.observation_count, 1)

    def test_recording_skipped_when_disabled(self) -> None:
        with _PatchedEnv(
            PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH=self.path,
            PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED="false",
        ):
            written = record_discovery_gap(
                goal="g",
                missing_capabilities=["x"],
                judge_accepted_by_capability={"x": 0},
                judge_evaluated_by_capability={"x": 0},
            )
            self.assertEqual(written, 0)
            # No file created — the JSON backend only creates the file
            # on first write.
            self.assertFalse(Path(self.path).exists())

    def test_recording_skipped_when_no_missing_capabilities(self) -> None:
        # Common case for executable plans: planner produced a fully
        # routable plan, no capability is missing. We must not log
        # an empty event row.
        with _PatchedEnv(
            PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH=self.path,
            PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED="true",
        ):
            self.assertEqual(
                record_discovery_gap(
                    goal="g",
                    missing_capabilities=[],
                    judge_accepted_by_capability={},
                    judge_evaluated_by_capability={},
                ),
                0,
            )

    def test_store_failure_returns_zero_and_does_not_raise(self) -> None:
        # The brutal contract: even if the underlying store throws
        # (Postgres unreachable, disk full, JSON file permission
        # denied), the route handler must keep going. Simulate a
        # failure by patching the store factory to raise.
        with _PatchedEnv(
            PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH=self.path,
            PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED="true",
        ):
            with patch(
                "planmyagents_api.discovery.discovery_gaps_recorder.get_discovery_gaps_store"
            ) as mock_store:
                mock_store.side_effect = RuntimeError("disk full")
                written = record_discovery_gap(
                    goal="g",
                    missing_capabilities=["x"],
                    judge_accepted_by_capability={"x": 0},
                    judge_evaluated_by_capability={"x": 0},
                )
                self.assertEqual(written, 0)


class LoadDiscoveryGapsSummaryTests(unittest.TestCase):
    def test_returns_empty_list_when_store_unreachable(self) -> None:
        # A fresh install has no JSONL file. The summary loader must
        # return [] not raise so the leaderboard tile renders the
        # "no gap signal yet" empty state.
        with _PatchedEnv(
            PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH="/nonexistent/path/x.jsonl"
        ):
            self.assertEqual(load_discovery_gaps_summary(), [])

    def test_load_swallows_underlying_errors(self) -> None:
        # Defense in depth: even if .summarize() itself throws,
        # the loader returns []. Tile rendering is more important
        # than logging a stack trace.
        with patch(
            "planmyagents_api.discovery.discovery_gaps_recorder.get_discovery_gaps_store"
        ) as mock_store:
            mock_store.return_value.summarize.side_effect = RuntimeError(
                "boom"
            )
            self.assertEqual(load_discovery_gaps_summary(), [])


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
