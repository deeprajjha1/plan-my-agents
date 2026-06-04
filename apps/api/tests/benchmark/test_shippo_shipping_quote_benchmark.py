"""Benchmark-runner integration test for the Shippo shipping_quote wrapper."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.mock import MockShippoQuoteFetcher
from planmyagents_api.benchmark.runner import run_sync


class ShippoShippingQuoteBenchmarkTests(unittest.TestCase):
    def test_honest_mock_runs_all_five_cases(self) -> None:
        runs = run_sync(
            provider=MockShippoQuoteFetcher(),
            capability="shipping_quote",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        self.assertEqual(len(runs), 5)
        self.assertTrue(all(run.provider_id == "mock-shippo-shipping" for run in runs))

    def test_honest_mock_passes_all_five_cases(self) -> None:
        runs = run_sync(
            provider=MockShippoQuoteFetcher(),
            capability="shipping_quote",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        for run in runs:
            self.assertGreaterEqual(
                run.score.quality_score,
                0.9,
                msg=f"honest case {run.test_case_id} expected >= 0.9, got "
                    f"{run.score.quality_score}",
            )

    def test_dishonest_mock_loses_on_rate_count(self) -> None:
        """The dishonest mock returns 0 rates with a hallucinated
        cheapest_rate. Case 0005 demands rate_count >= 2; the
        dishonest mock fails because rate_count=0.
        """

        good = run_sync(
            provider=MockShippoQuoteFetcher(),
            capability="shipping_quote",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        bad = run_sync(
            provider=MockShippoQuoteFetcher(
                provider_id="mock-shippo-bad", dishonest=True
            ),
            capability="shipping_quote",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        good_by_id = {r.test_case_id: r for r in good}
        bad_by_id = {r.test_case_id: r for r in bad}
        self.assertGreater(
            good_by_id["shipping-quote-0005-adversarial-rate-presence"].score.quality_score,
            bad_by_id["shipping-quote-0005-adversarial-rate-presence"].score.quality_score,
        )


if __name__ == "__main__":
    unittest.main()
