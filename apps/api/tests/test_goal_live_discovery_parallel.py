"""Regression test for parallel per-capability dispatch in /goal.

Background
----------
2026-05-20: the request-time scout fleet grew from 14 → 17 (added
Glama, npm, GitHub awesome-lists). The Playwright `/goal refuses
honestly` end-to-end test then started timing out at the 4-minute
budget because:

1. `github_awesome_lists` was sized for cold-cache safety (25 s
   per-scout budget), higher than the prior fleet ceiling of 18 s
   (smithery). It became the per-capability bottleneck.
2. `_live_discovery` in ``apps/api/planmyagents_api/web/app.py``
   walked ``for capability in selected:`` sequentially. Total
   wall-clock was therefore ``sum(per_dispatch_times)`` not
   ``max(...)``. With 3 missing capabilities each taking ~18-22 s,
   that's ~58 s of pure outer-loop serial wait — enough to push
   the /goal response past the test's 240 s budget.

The fix landed in the same commit as this test:

* Budgets for the new scouts tightened to fit the prior fleet
  baseline (``github_awesome_lists`` 25 s → 12 s,
  ``npm_mcp_packages`` 12 s → 8 s) and their per-scout work
  reduced (``max_entries_per_list``, ``page_size`` / ``max_pages``)
  so the scouts can actually return useful results inside the
  smaller budgets instead of always timing out.
* The outer per-capability loop in ``_live_discovery`` now runs
  in a ``ThreadPoolExecutor`` so K capabilities cost
  ``max(per_dispatch_times)`` instead of ``sum(...)``.

This file is the regression guard for the second item. Sequential
re-introduction (e.g., someone reverts the executor for "clarity")
would make this test fail loudly.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402
from planmyagents_api.discovery.normalizer import normalize_candidate  # noqa: E402
from planmyagents_api.discovery.scouts import Scout  # noqa: E402
from planmyagents_api.discovery.store import JsonDiscoveryStore  # noqa: E402

_SLEEP_PER_DISPATCH_SECONDS = 0.6


def _candidate_for_capability(capability_id: str, *, suffix: str) -> object:
    """Build a normalized candidate the scout fleet can return.

    We tag each candidate with the capability the planner is asking
    for so the response shape carries through to the per-capability
    summaries the test asserts on.
    """

    return normalize_candidate(
        {
            "id": f"{capability_id}-{suffix}",
            "display_name": f"Fake provider for {capability_id} ({suffix})",
            "vendor": "parallel-dispatch-test.example",
            "vendor_url": "https://parallel-dispatch-test.example",
            "provider_type": "ai_agent",
            "capabilities": [{"id": capability_id, "confidence": 0.7}],
        },
        source="hacker_news_agent_watch",
    )


@dataclass
class _SlowFakeSource:
    """A fake scout source that sleeps ``sleep_seconds`` per call.

    Records its start time so the test can prove that multiple
    concurrent dispatches actually overlap in wall-clock time.
    """

    sleep_seconds: float
    call_start_log: list[float]
    call_start_log_lock: threading.Lock

    def search(self, *, capabilities, task_description):  # noqa: ARG002
        with self.call_start_log_lock:
            self.call_start_log.append(time.monotonic())
        time.sleep(self.sleep_seconds)
        if not capabilities:
            return []
        return [_candidate_for_capability(next(iter(capabilities)), suffix="row")]


class GoalLiveDiscoveryParallelTests(unittest.TestCase):
    """Wall-clock proof that per-capability dispatch is parallel."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        self.benchmark_path = self.tempdir / "benchmarks.json"
        self.verification_path = self.tempdir / "verification.json"

        JsonDiscoveryStore(self.discovery_path).save([])

        os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(self.discovery_path)
        os.environ["PLANMYAGENTS_BENCHMARK_STORE_URL"] = str(self.benchmark_path)
        os.environ["PLANMYAGENTS_VERIFICATION_STORE_URL"] = str(
            self.verification_path
        )
        os.environ["PLANMYAGENTS_PLANNER"] = "rules"
        os.environ["PLANMYAGENTS_INTENT_MAPPER"] = "off"
        for key in (
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
        ):
            os.environ[key] = "false"
        os.environ["PLANMYAGENTS_LIVE_DISCOVERY"] = "true"
        os.environ["PLANMYAGENTS_CANDIDATE_JUDGE"] = "off"
        self.demand_store_path = self.tempdir / "demand.jsonl"
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = str(self.demand_store_path)
        self.run_log_path = self.tempdir / "discovery_run_events.jsonl"
        os.environ["PLANMYAGENTS_RUN_LOG_STORE_PATH"] = str(self.run_log_path)

        from planmyagents_api.web.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self._tempdir.cleanup()
        for var in (
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            "PLANMYAGENTS_BENCHMARK_STORE_URL",
            "PLANMYAGENTS_VERIFICATION_STORE_URL",
            "PLANMYAGENTS_PLANNER",
            "PLANMYAGENTS_INTENT_MAPPER",
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
            "PLANMYAGENTS_LIVE_DISCOVERY",
            "PLANMYAGENTS_CANDIDATE_JUDGE",
            "PLANMYAGENTS_DEMAND_STORE_PATH",
            "PLANMYAGENTS_RUN_LOG_STORE_PATH",
        ):
            os.environ.pop(var, None)

    def test_multi_capability_dispatches_run_in_parallel(self) -> None:
        # Each scout.search() sleeps SLEEP_PER_DISPATCH_SECONDS.
        # With K=2 capabilities the rules-planner triggers, sequential
        # outer-loop wall-clock is approximately 2 × SLEEP; parallel
        # wall-clock is approximately 1 × SLEEP. The threshold below
        # is the midpoint, with generous slack for FastAPI overhead.
        call_start_log: list[float] = []
        call_start_log_lock = threading.Lock()
        slow_scout = Scout(
            scout_id="hacker_news_agent_watch",
            source=_SlowFakeSource(
                sleep_seconds=_SLEEP_PER_DISPATCH_SECONDS,
                call_start_log=call_start_log,
                call_start_log_lock=call_start_log_lock,
            ),
        )

        def _fake_default_scouts() -> list[Scout]:
            return [slow_scout]

        with patch(
            "planmyagents_api.web.app.default_scouts", _fake_default_scouts
        ), patch(
            "planmyagents_api.web.app._maybe_chat_client_for_query_expansion",
            lambda: None,
        ):
            # KYC AML goal decomposes into ≥ 2 missing capabilities
            # via the rules planner (kyc_aml_check + identity_verification
            # / risk_assessment depending on planner version). The
            # specific slug set is not load-bearing; what matters is
            # that the planner produces ≥ 2 missing capabilities so
            # the outer per-capability loop has multiple iterations to
            # parallelise.
            response = self.client.post(
                "/goal",
                json={
                    "goal": (
                        "Complete KYC AML verification AND payment "
                        "authorization for a UAE retail buyer."
                    ),
                    "execute": False,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        live_discovery = (
            (body.get("plan") or {}).get("discovery") or {}
        ).get("live_discovery") or {}
        per_capability = live_discovery.get("per_capability") or []

        # Sanity: the planner produced more than one missing
        # capability — otherwise the parallelism claim is moot.
        self.assertGreaterEqual(
            len(per_capability), 2,
            "rules planner must produce ≥2 missing capabilities for this "
            f"goal so the parallel-dispatch claim is testable; got "
            f"{len(per_capability)}: "
            f"{[entry.get('capability') for entry in per_capability]}",
        )

        # The load-bearing assertion: at least two scout.search()
        # calls must have started within `_SLEEP_PER_DISPATCH_SECONDS / 2`
        # of each other. If the outer per-capability dispatch were
        # sequential, the gaps between consecutive search() starts
        # would all be ≥ _SLEEP_PER_DISPATCH_SECONDS (the prior call
        # had to finish before the next capability iteration started).
        # Parallel dispatch starts them ~simultaneously from the
        # ThreadPoolExecutor.
        self.assertGreaterEqual(
            len(call_start_log), 2,
            "fake scout must have been invoked at least twice "
            "(one per missing capability)",
        )
        ordered = sorted(call_start_log)
        smallest_gap = min(b - a for a, b in zip(ordered, ordered[1:]))
        self.assertLess(
            smallest_gap,
            _SLEEP_PER_DISPATCH_SECONDS / 2,
            f"per-capability dispatch appears sequential: smallest gap "
            f"between consecutive scout.search() starts was "
            f"{smallest_gap:.3f}s; expected < "
            f"{_SLEEP_PER_DISPATCH_SECONDS / 2:.3f}s (half the sleep) "
            "if the outer loop is parallel. See "
            "apps/api/planmyagents_api/web/app.py:_live_discovery — "
            "the ThreadPoolExecutor wrapper around _expand_and_dispatch "
            "is what enforces this contract.",
        )


if __name__ == "__main__":
    unittest.main()
