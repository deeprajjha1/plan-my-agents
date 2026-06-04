"""Benchmark-runner integration test for the Razorpay
payment_authorization wrapper.

The runner loads ALL payment_authorization cases (both Stripe-
shaped and Razorpay-shaped) when filtered by capability, so this
test filters down to just the ``razorpay-`` prefix to grade the
Razorpay-specific contract independently. Each mock produces the
canonical authoritative answer for its provider; the dishonest
mock claims ``status=paid`` for everything (including the
adversarial zero-amount case) and must lose.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.mock import MockRazorpayPaymentAuthorization
from planmyagents_api.benchmark.runner import run_sync


def _razorpay_only(runs):
    return [run for run in runs if run.test_case_id.startswith("razorpay-")]


class RazorpayPaymentAuthorizationBenchmarkTests(unittest.TestCase):
    def test_honest_mock_scores_well_on_razorpay_cases(self) -> None:
        runs = run_sync(
            provider=MockRazorpayPaymentAuthorization(),
            capability="payment_authorization",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        razorpay_runs = _razorpay_only(runs)
        # The Razorpay YAML defines exactly five cases; even if the
        # Stripe cases are also loaded under the same capability,
        # filtering by id prefix isolates the contract under test.
        self.assertEqual(len(razorpay_runs), 5)

        by_id = {run.test_case_id: run for run in razorpay_runs}
        # All five cases should pass for the honest mock — they're
        # all constructed against Razorpay's authoritative
        # ``status="created"`` Order Create outcome.
        for case_id, run in by_id.items():
            self.assertGreaterEqual(
                run.score.quality_score,
                0.9,
                msg=f"honest case {case_id} expected >= 0.9, got "
                    f"{run.score.quality_score}",
            )

    def test_dishonest_mock_loses_on_status_field(self) -> None:
        """The dishonest mock claims ``status=paid`` for every input.

        Each Razorpay benchmark case demands ``status="created"``
        (the Order Create wrapper-level success state). The
        dishonest mock fails the status check on every case,
        losing the 0.5 / 0.6 / 0.7 weight allocated to status —
        on aggregate, far below the honest mock.
        """

        good = run_sync(
            provider=MockRazorpayPaymentAuthorization(),
            capability="payment_authorization",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        bad = run_sync(
            provider=MockRazorpayPaymentAuthorization(
                provider_id="mock-razorpay-bad", dishonest=True
            ),
            capability="payment_authorization",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        good_by_id = {r.test_case_id: r for r in _razorpay_only(good)}
        bad_by_id = {r.test_case_id: r for r in _razorpay_only(bad)}

        for case_id in (
            "razorpay-0001-basic-inr-order",
            "razorpay-0002-razorpay-minimum-amount",
            "razorpay-0003-usd-international-order",
            "razorpay-0004-receipt-truncation",
            "razorpay-0005-adversarial-status-not-paid",
        ):
            self.assertGreater(
                good_by_id[case_id].score.quality_score,
                bad_by_id[case_id].score.quality_score,
                msg=f"honest must outscore dishonest on {case_id}: "
                    f"honest={good_by_id[case_id].score.quality_score}, "
                    f"dishonest={bad_by_id[case_id].score.quality_score}",
            )


if __name__ == "__main__":
    unittest.main()
