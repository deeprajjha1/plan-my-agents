from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.models import (
    ProviderResponse,
    ScoreResult,
    TestCase,
)
from planmyagents_api.benchmark.scoring import score_response
from planmyagents_api.eval.decomposition import (
    DECIDE_TO_CALL,
    INTEGRATE_RESULT,
    SCORING_METHOD_EXACT,
    decompose,
)


def _case(expected: dict) -> TestCase:
    return TestCase(
        id="c1",
        capability="web_scraping",
        difficulty="easy",
        inputs={"url": "https://example.com"},
        expected=expected,
    )


class DecompositionTest(unittest.TestCase):
    def test_success_yields_all_steps_passing(self) -> None:
        case = _case({"status": {"accept": ["ok"]}})
        response = ProviderResponse(
            succeeded=True,
            output={"status": "ok", "tool_name": "scrape"},
            cost_usd=0.0,
            latency_ms=10,
        )
        score = score_response(case, response)
        result = decompose(test_case=case, response=response, score=score)
        steps = {s.step: s.score for s in result.steps}
        self.assertEqual(steps[DECIDE_TO_CALL], 1.0)
        self.assertEqual(steps[INTEGRATE_RESULT], score.quality_score)
        self.assertEqual(result.scoring_method, SCORING_METHOD_EXACT)

    def test_gated_response_fails_decide_step(self) -> None:
        case = _case({"status": {"accept": ["ok"]}})
        response = ProviderResponse(
            succeeded=False,
            output=None,
            cost_usd=0.0,
            latency_ms=0,
            error="Generic protocol adapter execution is disabled.",
            raw_response={"execution_gated": True},
        )
        score = score_response(case, response)
        result = decompose(test_case=case, response=response, score=score)
        steps = {s.step: s.score for s in result.steps}
        self.assertEqual(steps[DECIDE_TO_CALL], 0.0)
        self.assertEqual(steps[INTEGRATE_RESULT], 0.0)

    def test_transport_error_locates_failure_at_integrate(self) -> None:
        case = _case({"status": {"accept": ["ok"]}})
        response = ProviderResponse(
            succeeded=False,
            output=None,
            cost_usd=0.0,
            latency_ms=12,
            error="transport: connection refused",
        )
        score = score_response(case, response)
        result = decompose(test_case=case, response=response, score=score)
        steps = {s.step: s.score for s in result.steps}
        # The call was attempted (decide=1) but integration failed.
        self.assertEqual(steps[DECIDE_TO_CALL], 1.0)
        self.assertEqual(steps[INTEGRATE_RESULT], 0.0)

    def test_exact_match_composite_unchanged_and_in_range(self) -> None:
        # The decomposition is additive — exact-match scoring is byte-for-byte
        # the same as before, and composite stays in [0,1].
        case = _case({"status": {"accept": ["ok"]}})
        response = ProviderResponse(
            succeeded=True, output={"status": "ok"}, cost_usd=0.0, latency_ms=5
        )
        score = score_response(case, response)
        self.assertEqual(score.quality_score, 1.0)
        self.assertTrue(0.0 <= score.quality_score <= 1.0)

    def test_rubric_case_without_judge_fails_closed(self) -> None:
        case = _case({"rubric_version": "rubric:web_scraping:v1"})
        response = ProviderResponse(
            succeeded=True, output={"text": "hello"}, cost_usd=0.0, latency_ms=5
        )
        score = score_response(case, response)  # no judge supplied
        self.assertFalse(score.succeeded)
        self.assertEqual(score.reason, "rubric_case_requires_judge_but_none_supplied")

    def test_rubric_case_delegates_to_judge(self) -> None:
        class _FakeJudge:
            def score_test_case(self, test_case, response):
                return ScoreResult(
                    quality_score=0.8, succeeded=True, field_scores=[], reason="judge"
                )

        case = _case({"rubric_version": "rubric:web_scraping:v1"})
        response = ProviderResponse(
            succeeded=True, output={"text": "hello"}, cost_usd=0.0, latency_ms=5
        )
        score = score_response(case, response, judge=_FakeJudge())
        self.assertEqual(score.quality_score, 0.8)
        self.assertEqual(score.reason, "judge")


if __name__ == "__main__":
    unittest.main()
