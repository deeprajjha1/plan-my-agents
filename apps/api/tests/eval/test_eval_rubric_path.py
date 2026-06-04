"""Worked example: judge-scored evaluation of an open-ended capability.

Proves the rubric + Eval_Judge path runs end-to-end against a discovered MCP
agent for `return_policy_analysis` ("analyse seller return policies") — the
exact judgment-heavy capability I earlier (wrongly) implied was unscoreable.

Uses the curated rubric-shaped reference cases in
`packages/benchmarks/return_policy_analysis/` and a deterministic fake judge
(no real LLM needed in CI). The real EvalJudge is unit-tested separately in
test_eval_judge.py; here we prove the framework wiring produces a real
`judge`-sourced run that the credibility classifier counts.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.protocol import ENABLE_PROTOCOL_EXECUTION_ENV
from planmyagents_api.benchmark.credibility import _is_real_run
from planmyagents_api.benchmark.models import ScoreResult
from planmyagents_api.cost.cost_cap import CostCapPolicy
from planmyagents_api.discovery.models import (
    CandidateCapability,
    CandidateTool,
    DiscoveryCandidate,
)
from planmyagents_api.eval.framework import build_default_framework
from planmyagents_api.eval.ground_truth import GroundTruthKind, GroundTruthManager
from planmyagents_api.eval.models import EvalRunMode, EvalTier

from tests.fixtures.mcp_stub_server import McpStubServer

CAP = "return_policy_analysis"
BENCH_DIR = ROOT / "packages" / "benchmarks"


class _FakeJudge:
    """Deterministic stand-in for EvalJudge.

    Scores an analysis by how many rubric criteria keywords the output text
    covers — enough to prove the framework routes rubric cases to a judge and
    persists a real `judge`-sourced run, without a live LLM in CI.
    """

    def score_test_case(self, test_case, response):
        text = str((response.output or {}).get("text", "")).lower()
        criteria = test_case.expected.get("rubric_criteria") or []
        # Crude keyword coverage: a criterion is "met" if a salient token from
        # it appears in the output. Deterministic and good enough for wiring.
        hits = 0
        for c in criteria:
            tokens = [t for t in str(c).lower().replace(".", "").split() if len(t) > 4]
            if any(tok in text for tok in tokens):
                hits += 1
        score = hits / len(criteria) if criteria else 0.0
        return ScoreResult(
            quality_score=round(score, 4),
            succeeded=score > 0,
            field_scores=[],
            reason="judge",
        )


def _scrape_tool_response(args):
    # The stub MCP "agent" returns a faithful analysis of the policy_text it
    # was given — simulating a competent return-policy analyzer.
    return {"text": str(args.get("policy_text", "")), "status": "ok"}


def _candidate(base_url: str) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id="policycheck-mcp",
        display_name="PolicyCheck (MCP)",
        vendor="PolicyCheck",
        vendor_url=base_url,
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id=CAP, confidence=0.95)],
        verification_status="registered_in_directory",
        tools=[CandidateTool(name="analyze_return_policy", input_schema={})],
    )


class RubricGroundTruthTest(unittest.TestCase):
    def test_rubric_shaped_yaml_resolves_as_rubric_with_cases(self) -> None:
        gt = GroundTruthManager(benchmarks_dir=BENCH_DIR).resolve(CAP)
        self.assertEqual(gt.kind, GroundTruthKind.RUBRIC)
        self.assertTrue(gt.cases, "rubric cases are the reference inputs")
        self.assertIsNotNone(gt.rubric)
        self.assertTrue(gt.rubric.criteria)

    def test_registered_rubric_without_cases_is_non_eval_able(self) -> None:
        # A rubric describes HOW to score; with no reference cases there is
        # nothing to invoke the agent with → honestly non-eval-able.
        from planmyagents_api.eval.ground_truth import Rubric

        mgr = GroundTruthManager(
            benchmarks_dir=BENCH_DIR / "does_not_exist",
            rubrics={"x_cap": Rubric(capability="x_cap", criteria=["c1"])},
        )
        gt = mgr.resolve("x_cap")
        self.assertEqual(gt.kind, GroundTruthKind.NONE)
        self.assertIn("no reference cases", gt.reason_if_none)


class JudgeScoredEndToEndTest(unittest.TestCase):
    def test_open_ended_capability_scores_via_judge(self) -> None:
        with McpStubServer(
            {"analyze_return_policy": _scrape_tool_response}
        ) as base_url, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False
        ):
            framework = build_default_framework(
                benchmarks_dir=BENCH_DIR,
                cost_cap=CostCapPolicy(
                    enabled=True, per_goal_usd=100.0, daily_usd=100.0, ledger=None
                ),
                judge=_FakeJudge(),
            )
            result = framework.evaluate_cell(
                candidate=_candidate(base_url),
                capability=CAP,
                run_mode=EvalRunMode.SANDBOX,
            )

            self.assertTrue(result.eval_able, result.reason)
            # The decisive proof: a real, judge-sourced run for an open-ended
            # capability — not verification-only, not synthetic.
            self.assertEqual(result.source, "judge")
            self.assertTrue(result.is_real_run)
            self.assertIsNotNone(result.quality_score)
            self.assertEqual(result.sample_size, 3)  # 3 reference cases
            self.assertTrue(_is_real_run(result.ranking.to_json()))
            # Provenance records the judge basis honestly.
            self.assertEqual(result.provenance.scoring_method, "rubric_judge")
            self.assertEqual(result.provenance.ground_truth_kind, "rubric")
            self.assertTrue(result.provenance.rubric_version)

    def test_judge_unavailable_degrades_to_verification_only(self) -> None:
        # Same capability, but NO judge wired → honestly non-eval-able, never
        # a fabricated score.
        with McpStubServer(
            {"analyze_return_policy": _scrape_tool_response}
        ) as base_url, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False
        ):
            framework = build_default_framework(benchmarks_dir=BENCH_DIR, judge=None)
            result = framework.evaluate_cell(
                candidate=_candidate(base_url),
                capability=CAP,
                run_mode=EvalRunMode.SANDBOX,
            )
            self.assertFalse(result.eval_able)
            self.assertIsNone(result.quality_score)
            self.assertEqual(result.tier_reached, EvalTier.FUNCTIONAL_SMOKE)
            self.assertIn("judge_unavailable", result.reason)


if __name__ == "__main__":
    unittest.main()
