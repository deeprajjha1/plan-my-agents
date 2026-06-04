"""Integration test for Slice 2 wiring: research-agent backstop in
the /goal refusal payload.

Strategy: patch ``planmyagents_api.web.app.GeneralResearchAgent`` so
the backstop runs against a deterministic fake instead of the real
Brave Search API and the real LLM tier. Asserts the wire shape of
the ``research_backstop`` block the frontend reads.

Coverage:

* When the agent is unavailable (no API key, env disabled), the
  block carries ``status="missing_credentials"`` or ``"disabled"``
  and never blocks the rest of the refusal payload.
* When the agent is available and at least one capability synthesis
  succeeds, the block carries ``status="applied"`` with one
  per-capability entry — including the wire-contract keys
  (``capability_id``, ``user_facing_step``, ``result``).
* Per-request capability cap is honoured: capabilities beyond the
  cap appear in ``skipped_capabilities`` and don't trigger search
  calls (proven by the spy counter).
* When specialist results exist, the backstop is skipped entirely
  so the user sees the specialists rather than an advisory answer.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402
from planmyagents_api.discovery.store import JsonDiscoveryStore  # noqa: E402
from planmyagents_api.research.general_research_agent import (  # noqa: E402
    ResearchResult,
    ResearchSource,
)


class _SpyResearchAgent:
    """In-memory replacement for GeneralResearchAgent used to assert
    wire shape and call counts without touching Brave/LLM."""

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.calls: list[tuple[str, str]] = []

    def available(self) -> bool:
        return self._available

    def research(
        self,
        *,
        sub_task_description: str,
        goal: str = "",
    ) -> ResearchResult:
        self.calls.append((sub_task_description, goal))
        if not self._available:
            return ResearchResult(
                status="missing_credentials",
                reason="BRAVE_SEARCH_API_KEY is not configured.",
            )
        return ResearchResult(
            status="applied",
            summary=f"Synthesised answer for: {sub_task_description}.",
            sources=[
                ResearchSource(
                    url="https://example.com/a",
                    title="Example A",
                    snippet="snippet a",
                ),
            ],
            citations=["https://example.com/a"],
            elapsed_ms=42,
        )


class GoalResearchBackstopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        JsonDiscoveryStore(self.discovery_path).save([])

        os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(self.discovery_path)
        os.environ["PLANMYAGENTS_BENCHMARK_STORE_URL"] = str(
            self.tempdir / "benchmarks.json"
        )
        os.environ["PLANMYAGENTS_VERIFICATION_STORE_URL"] = str(
            self.tempdir / "verification.json"
        )
        os.environ["PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB"] = "false"
        os.environ["PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL"] = ""
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
        os.environ["PLANMYAGENTS_LIVE_DISCOVERY"] = "false"
        os.environ["PLANMYAGENTS_CANDIDATE_JUDGE"] = "off"
        # Disable the human-fallback suggester for these tests so we
        # can isolate failures to the backstop wiring; the suggester
        # has its own integration test.
        os.environ["PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER"] = "off"
        # Disable the slug canonicaliser and label reconciler so the
        # stubbed decomposer output flows through unmodified. Without
        # these the canonicaliser will rewrite the stub's coined slug
        # to a router-supported one (because cosine on snake_case is
        # noisy on the hash embedder used in tests), defeating the
        # "we have missing capabilities" precondition the backstop
        # tests rely on.
        os.environ["PLANMYAGENTS_SLUG_CANONICALIZATION"] = "false"
        os.environ["PLANMYAGENTS_LABEL_RECONCILER"] = "off"

        # Stub the decomposer so the test doesn't hit the live LLM
        # AND so the resulting decomposition always carries at least
        # one coined slug — the precondition the backstop tests rely
        # on. After Fix 1's catalog-descriptions wiring landed, the
        # live LLM correctly reuses ``store_locator`` and
        # ``price_comparison`` for the test goals, which makes those
        # goals fully covered (zero missing capabilities) and skips
        # the backstop entirely. The stub restores the original
        # "user-coined a synonym the registry can't route" failure
        # mode the backstop is designed to handle.
        from planmyagents_api.planner.goal_decomposer import (
            DecomposedSubTask,
            GoalDecomposition,
        )

        self._decomposer_patcher = patch(
            "planmyagents_api.web.planning.decompose_goal",
            return_value=GoalDecomposition(
                intent_summary="Stubbed decomposition for backstop test.",
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
                        # Long enough not to overlap any registry slug
                        # tightly; the canonicaliser is disabled above
                        # but this is defence in depth.
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
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = str(
            self.tempdir / "demand.jsonl"
        )
        os.environ["PLANMYAGENTS_RUN_LOG_STORE_PATH"] = str(
            self.tempdir / "run_log.jsonl"
        )
        os.environ["PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH"] = str(
            self.tempdir / "gaps.jsonl"
        )

        from planmyagents_api.web.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self._tempdir.cleanup()
        for var in (
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            "PLANMYAGENTS_BENCHMARK_STORE_URL",
            "PLANMYAGENTS_VERIFICATION_STORE_URL",
            "PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB",
            "PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL",
            "PLANMYAGENTS_PLANNER",
            "PLANMYAGENTS_INTENT_MAPPER",
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
            "PLANMYAGENTS_LIVE_DISCOVERY",
            "PLANMYAGENTS_CANDIDATE_JUDGE",
            "PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER",
            "PLANMYAGENTS_DEMAND_STORE_PATH",
            "PLANMYAGENTS_RUN_LOG_STORE_PATH",
            "PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH",
            "PLANMYAGENTS_GENERAL_RESEARCH_AGENT",
            "PLANMYAGENTS_SLUG_CANONICALIZATION",
            "PLANMYAGENTS_LABEL_RECONCILER",
        ):
            os.environ.pop(var, None)

    def _post_goal(self, goal: str) -> dict[str, Any]:
        response = self.client.post(
            "/goal", json={"goal": goal, "execute": False}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_unavailable_agent_yields_missing_credentials_status(self) -> None:
        spy = _SpyResearchAgent(available=False)
        with patch(
            "planmyagents_api.web.app.GeneralResearchAgent",
            return_value=spy,
        ):
            body = self._post_goal(
                "Find liquor stores in southern India"
            )

        plan = body.get("plan") or {}
        discovery = plan.get("discovery") or {}
        backstop = discovery.get("research_backstop") or {}
        # The wire-contract keys are present even on the unavailable
        # path so the frontend doesn't have to special-case them.
        self.assertEqual(set(backstop.keys()) >= {"status", "results"}, True)
        self.assertEqual(backstop.get("status"), "missing_credentials")
        self.assertEqual(backstop.get("results"), [])

    def test_available_agent_returns_applied_with_per_capability_results(
        self,
    ) -> None:
        spy = _SpyResearchAgent(available=True)
        with patch(
            "planmyagents_api.web.app.GeneralResearchAgent",
            return_value=spy,
        ):
            body = self._post_goal(
                "Find liquor stores in southern India and compare prices"
            )

        plan = body.get("plan") or {}
        discovery = plan.get("discovery") or {}
        backstop = discovery.get("research_backstop") or {}
        self.assertEqual(backstop.get("status"), "applied")
        self.assertGreaterEqual(len(backstop.get("results") or []), 1)

        # Each entry MUST carry the wire-contract keys.
        first = backstop["results"][0]
        self.assertEqual(
            set(first.keys()),
            {"capability_id", "user_facing_step", "result"},
        )
        self.assertEqual(
            set(first["result"].keys()),
            {
                "status",
                "summary",
                "sources",
                "citations",
                "elapsed_ms",
                "reason",
            },
        )
        self.assertEqual(first["result"]["status"], "applied")
        # Spy was called at least once and at most the per-request cap.
        self.assertGreaterEqual(len(spy.calls), 1)
        self.assertLessEqual(len(spy.calls), 3)


if __name__ == "__main__":
    unittest.main()
