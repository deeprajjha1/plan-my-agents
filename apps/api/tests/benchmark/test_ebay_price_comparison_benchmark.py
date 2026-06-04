"""Benchmark-runner integration test for the eBay price_comparison wrapper."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.mock import MockEbayBrowseProvider
from planmyagents_api.benchmark.runner import run_sync


class EbayPriceComparisonBenchmarkTests(unittest.TestCase):
    def test_honest_mock_runs_all_five_cases(self) -> None:
        runs = run_sync(
            provider=MockEbayBrowseProvider(),
            capability="price_comparison",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        self.assertEqual(len(runs), 5)
        self.assertTrue(all(run.provider_id == "mock-ebay-browse" for run in runs))

    def test_honest_mock_passes_all_five_cases(self) -> None:
        runs = run_sync(
            provider=MockEbayBrowseProvider(),
            capability="price_comparison",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        for run in runs:
            self.assertGreaterEqual(
                run.score.quality_score,
                0.9,
                msg=f"honest case {run.test_case_id} expected >= 0.9, got "
                    f"{run.score.quality_score}",
            )

    def test_dishonest_mock_loses_on_no_results_case(self) -> None:
        """The dishonest mock hallucinates listings even for the
        no-results query. Case 0005 expects status="no_results"
        and result_count=0; dishonest fails both.
        """

        good = run_sync(
            provider=MockEbayBrowseProvider(),
            capability="price_comparison",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        bad = run_sync(
            provider=MockEbayBrowseProvider(
                provider_id="mock-ebay-bad", dishonest=True
            ),
            capability="price_comparison",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        good_by_id = {r.test_case_id: r for r in good}
        bad_by_id = {r.test_case_id: r for r in bad}
        self.assertGreater(
            good_by_id["price-comparison-0005-adversarial-no-results-honesty"].score.quality_score,
            bad_by_id["price-comparison-0005-adversarial-no-results-honesty"].score.quality_score,
        )


if __name__ == "__main__":
    unittest.main()
