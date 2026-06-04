from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.eval.models import (
    NON_REAL_EVAL_SOURCES,
    REAL_EVAL_SOURCES,
    EvalProvenance,
    EvalResult,
    EvalRunMode,
    EvalTier,
    tier_rank,
)


def _provenance() -> EvalProvenance:
    return EvalProvenance(
        eval_tier=EvalTier.STATIC_VERIFICATION.value,
        run_mode=EvalRunMode.DRY_RUN.value,
        safety_class="read_only",
        provider_type="mcp_server",
    )


class EvalModelsTest(unittest.TestCase):
    def test_verification_only_result_has_absent_quality(self) -> None:
        result = EvalResult(
            provider_id="p1",
            capability="web_scraping",
            provider_type="mcp_server",
            tier_reached=EvalTier.STATIC_VERIFICATION,
            run_mode=EvalRunMode.DRY_RUN,
            safety_class="read_only",
            source="verification_only",
            eval_able=False,
            provenance=_provenance(),
        )
        self.assertIsNone(result.quality_score)
        self.assertFalse(result.is_real_run)

    def test_scored_result_round_trips(self) -> None:
        result = EvalResult(
            provider_id="p1",
            capability="web_scraping",
            provider_type="mcp_server",
            tier_reached=EvalTier.SCORED_BENCHMARK,
            run_mode=EvalRunMode.SANDBOX,
            safety_class="read_only",
            source="exact_match",
            eval_able=True,
            quality_score=0.9,
            sample_size=30,
            provenance=_provenance(),
        )
        payload = result.to_json()
        self.assertEqual(payload["quality_score"], 0.9)
        self.assertEqual(payload["source"], "exact_match")
        self.assertTrue(payload["is_real_run"])
        self.assertEqual(payload["tier_reached"], "scored_benchmark")
        self.assertEqual(payload["provenance"]["provider_type"], "mcp_server")

    def test_score_on_non_real_source_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EvalResult(
                provider_id="p1",
                capability="web_scraping",
                provider_type="mcp_server",
                tier_reached=EvalTier.SCORED_BENCHMARK,
                run_mode=EvalRunMode.SANDBOX,
                safety_class="read_only",
                source="gated",          # non-real
                eval_able=True,
                quality_score=0.9,
                sample_size=30,
            )

    def test_static_verification_with_score_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EvalResult(
                provider_id="p1",
                capability="web_scraping",
                provider_type="mcp_server",
                tier_reached=EvalTier.STATIC_VERIFICATION,
                run_mode=EvalRunMode.DRY_RUN,
                safety_class="read_only",
                source="exact_match",
                eval_able=True,
                quality_score=0.5,
                sample_size=1,
            )

    def test_source_taxonomy_is_disjoint(self) -> None:
        self.assertEqual(REAL_EVAL_SOURCES & NON_REAL_EVAL_SOURCES, frozenset())

    def test_tier_rank_is_cheapest_first(self) -> None:
        self.assertLess(
            tier_rank(EvalTier.STATIC_VERIFICATION),
            tier_rank(EvalTier.FUNCTIONAL_SMOKE),
        )
        self.assertLess(
            tier_rank(EvalTier.FUNCTIONAL_SMOKE),
            tier_rank(EvalTier.SCORED_BENCHMARK),
        )
        self.assertLess(
            tier_rank(EvalTier.SCORED_BENCHMARK),
            tier_rank(EvalTier.CONTINUOUS_REEVAL),
        )


if __name__ == "__main__":
    unittest.main()
