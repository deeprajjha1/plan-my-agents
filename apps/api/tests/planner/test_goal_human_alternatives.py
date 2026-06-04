"""Integration test for Slice 1 wiring: human-fallback suggester +
demand-per-capability snapshot in the /goal refusal payload.

Why this lives in its own file: the live-discovery test already
exercises a refusal path but its assertions are about scout dispatch
shape. Keeping the human-fallback assertions separate makes the
intent of each test obvious and means a future regression in the
suggester wiring fails one focused test rather than hiding inside a
500-line dispatcher harness.

Coverage:

* The refusal payload includes ``discovery.human_alternatives`` with
  the wire-contract keys (``status``, ``alternatives``, ``reason``).
* The refusal payload includes ``discovery.demand.per_capability_summary``
  with one entry per missing capability that this request just
  recorded demand for. Verifies the recorder→loader→response join
  works end-to-end.
* The env-var off-switch produces ``status="disabled"`` and short-
  circuits before any LLM tier is touched.
* When the suggester returns alternatives, they round-trip to the
  HTTP response with the keys the frontend expects (``url``, ``name``,
  ``why``).
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

from fastapi.testclient import TestClient  # noqa: E402
from planmyagents_api.discovery.store import JsonDiscoveryStore  # noqa: E402
from planmyagents_api.planner.human_fallback_suggester import (  # noqa: E402
    HumanAlternative,
    HumanFallbackSuggestion,
)


class GoalHumanAlternativesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        self.benchmark_path = self.tempdir / "benchmarks.json"
        self.verification_path = self.tempdir / "verification.json"

        # Empty store → every capability missing → refusal path runs.
        JsonDiscoveryStore(self.discovery_path).save([])

        os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(self.discovery_path)
        os.environ["PLANMYAGENTS_BENCHMARK_STORE_URL"] = str(self.benchmark_path)
        os.environ["PLANMYAGENTS_VERIFICATION_STORE_URL"] = str(
            self.verification_path
        )
        # The .env file (loaded at module import) points the promoted-
        # provider store at Postgres on :55433; in unit tests we don't
        # want to require a running DB. Disable the DB-backed merge and
        # null out the URL so the router never tries to connect.
        os.environ["PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB"] = "false"
        os.environ["PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL"] = ""
        os.environ["PLANMYAGENTS_PLANNER"] = "rules"
        os.environ["PLANMYAGENTS_INTENT_MAPPER"] = "off"
        # Disable network discovery — same logic as test_goal_live_discovery.
        for key in (
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
        ):
            os.environ[key] = "false"
        os.environ["PLANMYAGENTS_LIVE_DISCOVERY"] = "false"
        os.environ["PLANMYAGENTS_CANDIDATE_JUDGE"] = "off"
        # Disable the slug canonicaliser and label reconciler so the
        # stubbed decomposer output flows through unmodified. See
        # test_goal_research_backstop.py for the full rationale; in
        # short, Fix 1's catalog-descriptions change made the live LLM
        # correctly REUSE router-supported slugs for these test goals,
        # producing zero missing capabilities and skipping the
        # human-alternatives surface entirely.
        os.environ["PLANMYAGENTS_SLUG_CANONICALIZATION"] = "false"
        os.environ["PLANMYAGENTS_LABEL_RECONCILER"] = "off"
        # Demand log redirected into tempdir to avoid polluting repo data/.
        self.demand_store_path = self.tempdir / "demand.jsonl"
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = str(self.demand_store_path)
        # Discovery-gap log: redirect into the tempdir so each test
        # gets a clean slate.
        self.run_log_path = self.tempdir / "discovery_run_events.jsonl"
        os.environ["PLANMYAGENTS_RUN_LOG_STORE_PATH"] = str(self.run_log_path)
        # Discovery gaps store path lives next to demand events.
        self.gaps_store_path = self.tempdir / "discovery_gaps.jsonl"
        os.environ["PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH"] = str(
            self.gaps_store_path
        )

        # Stub the decomposer so the test is hermetic AND so the
        # decomposition always carries at least one coined slug — the
        # precondition the human-alternatives + demand-recorder
        # assertions rely on. See test_goal_research_backstop.py for
        # the full Fix-1 rationale.
        from planmyagents_api.planner.goal_decomposer import (
            DecomposedSubTask,
            GoalDecomposition,
        )

        self._decomposer_patcher = patch(
            "planmyagents_api.web.planning.decompose_goal",
            return_value=GoalDecomposition(
                intent_summary="Stubbed decomposition for alternatives test.",
                sub_tasks=[
                    DecomposedSubTask(
                        description=(
                            "Find liquor stores in the southern Indian region."
                        ),
                        user_facing_step="Find regional liquor stores.",
                        search_query="regional liquor store directory",
                        acceptance_criteria=(
                            "Returns liquor-permit-holding retail stores by city."
                        ),
                        suggested_capability_id=(
                            "regional_liquor_store_directory"
                        ),
                    ),
                ],
                confidence=0.9,
                catalog_reused_capabilities=[],
                new_capabilities=["regional_liquor_store_directory"],
            ),
        )
        self._decomposer_patcher.start()
        self.addCleanup(self._decomposer_patcher.stop)

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
            "PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH",
            "PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER",
            "PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB",
            "PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL",
            "PLANMYAGENTS_SLUG_CANONICALIZATION",
            "PLANMYAGENTS_LABEL_RECONCILER",
        ):
            os.environ.pop(var, None)

    def _post_goal(self, goal: str) -> dict:
        response = self.client.post(
            "/goal",
            json={"goal": goal, "execute": False},
        )
        # The refusal path returns 200 with status="unsupported" inside
        # plan; only an unrelated planner failure would yield non-200.
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_disabled_flag_short_circuits_to_disabled_status(self) -> None:
        os.environ["PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER"] = "off"
        body = self._post_goal(
            "Find liquor stores in southern India and compare prices on single malts"
        )
        plan = body.get("plan") or {}
        discovery = plan.get("discovery") or {}
        ha = discovery.get("human_alternatives")
        self.assertIsInstance(
            ha, dict, f"expected human_alternatives dict in {discovery.keys()}"
        )
        self.assertEqual(ha.get("status"), "disabled")
        self.assertEqual(ha.get("alternatives"), [])

    def test_applied_alternatives_round_trip_to_response(self) -> None:
        canned = HumanFallbackSuggestion(
            status="applied",
            alternatives=[
                HumanAlternative(
                    url="https://livingliquidz.com",
                    name="Living Liquidz",
                    why="India-wide retailer with browseable inventory.",
                ),
                HumanAlternative(
                    url="https://tonique.in",
                    name="Tonique",
                    why="Bangalore retailer; prices listed online.",
                ),
            ],
        )

        with patch(
            "planmyagents_api.web.app.suggest_human_alternatives",
            return_value=canned,
        ):
            body = self._post_goal(
                "Find liquor stores in southern India and compare prices"
            )

        plan = body.get("plan") or {}
        discovery = plan.get("discovery") or {}
        ha = discovery.get("human_alternatives") or {}
        self.assertEqual(ha.get("status"), "applied")
        alts = ha.get("alternatives") or []
        self.assertEqual(len(alts), 2)
        # Wire-contract keys — frontend reads exactly these names. If
        # we ever rename one we want a test failure here.
        self.assertEqual(set(alts[0].keys()), {"url", "name", "why"})
        names = {alt["name"] for alt in alts}
        self.assertEqual(names, {"Living Liquidz", "Tonique"})

    def test_demand_per_capability_summary_populated_after_recording(self) -> None:
        # Disable suggester so the test stays isolated to the demand
        # surface — the previous test already exercises the suggester
        # path.
        os.environ["PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER"] = "off"
        body = self._post_goal(
            "Find liquor stores in southern India and compare prices"
        )
        plan = body.get("plan") or {}
        discovery = plan.get("discovery") or {}
        demand = discovery.get("demand") or {}
        # `events_recorded` is the recorder's own count for THIS request;
        # `per_capability_summary` is the loader's view of every recorded
        # event including this request. Both should be non-empty.
        self.assertGreaterEqual(int(demand.get("events_recorded") or 0), 1)
        per_cap = demand.get("per_capability_summary") or []
        self.assertIsInstance(per_cap, list)
        self.assertGreaterEqual(
            len(per_cap),
            1,
            f"expected at least one per-capability entry; got {per_cap}",
        )
        # Each entry MUST carry the exact wire-contract keys the UI
        # reads — adding/renaming a key here is a frontend-breaking
        # change and we want a loud failure.
        first = per_cap[0]
        self.assertEqual(
            set(first.keys()),
            {
                "capability_id",
                "request_count",
                "distinct_requester_count",
                "last_requested_at",
                "sample_goals",
                "has_local_match_count",
                "has_apis_without_agents_count",
            },
        )
        self.assertGreaterEqual(int(first.get("request_count") or 0), 1)


if __name__ == "__main__":
    unittest.main()
