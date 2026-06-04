from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.planner.cost_estimator import estimate_plan_cost  # noqa: E402
from planmyagents_api.planner.goal import GoalPlan, PlannedSubTask  # noqa: E402


def _plan(*subtasks: tuple[str, str]) -> GoalPlan:
    return GoalPlan(
        status="executable",
        summary="test plan",
        sub_tasks=[
            PlannedSubTask(capability=cap, description=desc, inputs={})
            for cap, desc in subtasks
        ],
    )


def _ranking(
    *,
    capability: str,
    provider_id: str,
    avg_cost_usd: float,
    sample_size: int = 50,
    source: str = "real_adapter",
    composite_score: float = 0.7,
) -> dict[str, Any]:
    return {
        "capability": capability,
        "provider_id": provider_id,
        "avg_cost_usd": avg_cost_usd,
        "sample_size": sample_size,
        "source": source,
        "composite_score": composite_score,
    }


class CostEstimatorTest(unittest.TestCase):
    def test_picks_cheapest_credible_provider_per_capability(self) -> None:
        plan = _plan(("email_verification", "Verify x"))
        rankings = [
            _ranking(
                capability="email_verification",
                provider_id="hunter",
                avg_cost_usd=0.005,
            ),
            _ranking(
                capability="email_verification",
                provider_id="zerobounce",
                avg_cost_usd=0.020,
            ),
        ]
        result = estimate_plan_cost(plan=plan, rankings=rankings)
        self.assertEqual(result["per_sub_task"][0]["provider_id"], "hunter")
        self.assertAlmostEqual(result["total_estimated_usd"], 0.005)
        self.assertEqual(result["covered_sub_task_count"], 1)
        self.assertEqual(result["missing_capabilities"], [])

    def test_ignores_synthetic_rankings_for_pricing(self) -> None:
        plan = _plan(("email_verification", "Verify x"))
        rankings = [
            _ranking(
                capability="email_verification",
                provider_id="mock-cheap",
                avg_cost_usd=0.0001,
                source="synthetic",
            ),
            _ranking(
                capability="email_verification",
                provider_id="hunter",
                avg_cost_usd=0.005,
                source="real_adapter",
            ),
        ]
        result = estimate_plan_cost(plan=plan, rankings=rankings)
        self.assertEqual(result["per_sub_task"][0]["provider_id"], "hunter")

    def test_marks_capabilities_without_real_pricing_as_missing(self) -> None:
        plan = _plan(
            ("email_verification", "Verify x"),
            ("web_scraping", "Scrape y"),
        )
        rankings = [
            _ranking(
                capability="email_verification",
                provider_id="hunter",
                avg_cost_usd=0.005,
            ),
            _ranking(
                capability="web_scraping",
                provider_id="mock-firecrawl",
                avg_cost_usd=0.001,
                source="synthetic",
            ),
        ]
        result = estimate_plan_cost(plan=plan, rankings=rankings)
        self.assertEqual(result["missing_capabilities"], ["web_scraping"])
        self.assertIsNone(result["per_sub_task"][1]["provider_id"])
        self.assertIsNone(result["per_sub_task"][1]["avg_cost_usd"])
        self.assertEqual(result["covered_sub_task_count"], 1)
        self.assertTrue(result["credibility_notes"])

    def test_returns_zero_total_when_no_credible_pricing_anywhere(self) -> None:
        plan = _plan(("email_verification", "Verify x"))
        rankings = [
            _ranking(
                capability="email_verification",
                provider_id="mock",
                avg_cost_usd=0.5,
                source="synthetic",
            ),
        ]
        result = estimate_plan_cost(plan=plan, rankings=rankings)
        self.assertEqual(result["total_estimated_usd"], 0.0)
        self.assertEqual(result["covered_sub_task_count"], 0)
        self.assertIn(
            "No sub-task has real-adapter pricing data",
            " ".join(result["credibility_notes"]),
        )

    def test_zero_sample_size_real_source_is_not_credible(self) -> None:
        plan = _plan(("email_verification", "Verify x"))
        rankings = [
            _ranking(
                capability="email_verification",
                provider_id="declared_only",
                avg_cost_usd=0.001,
                sample_size=0,
                source="real_adapter",
            ),
        ]
        result = estimate_plan_cost(plan=plan, rankings=rankings)
        self.assertEqual(result["missing_capabilities"], ["email_verification"])

    def test_empty_plan_returns_empty_estimate(self) -> None:
        plan = GoalPlan(
            status="unsupported",
            summary="nothing to do",
            refusal_reasons=["no understood capabilities"],
            missing_capabilities=["unknown"],
        )
        result = estimate_plan_cost(plan=plan, rankings=[])
        self.assertEqual(result["total_estimated_usd"], 0.0)
        self.assertEqual(result["per_sub_task"], [])
        self.assertEqual(result["missing_capabilities"], [])


if __name__ == "__main__":
    unittest.main()
