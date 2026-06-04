"""Benchmark-runner integration test for the payment_authorization
suite.

Mirrors `tests/test_runner_and_report.py` but for the Stripe wrapper.
The honest mock implements Stripe's documented test-card behaviour;
the dishonest mock claims success for every call. The honest mock
must outscore the dishonest one — that's the contract that protects
us from regressing the wrapper's normalisation logic.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.mock import MockStripePaymentAuthorization
from planmyagents_api.benchmark.report import summarize_runs
from planmyagents_api.benchmark.runner import run_sync


def _stripe_only(runs):
    """The runner loads ALL payment_authorization YAML cases when
    filtered by capability. This isolates the ones whose ids start
    with the ``payment-`` prefix (Stripe vocabulary), distinct from
    the ``razorpay-`` prefix used by the Razorpay suite.
    """

    return [run for run in runs if run.test_case_id.startswith("payment-")]


class PaymentAuthorizationBenchmarkTests(unittest.TestCase):
    def test_honest_mock_runs_all_five_stripe_cases(self) -> None:
        runs = run_sync(
            provider=MockStripePaymentAuthorization(),
            capability="payment_authorization",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        stripe_runs = _stripe_only(runs)
        self.assertEqual(len(stripe_runs), 5)
        self.assertTrue(
            all(run.provider_id == "mock-stripe-payments" for run in stripe_runs)
        )
        capabilities = {run.capability for run in stripe_runs}
        self.assertEqual(capabilities, {"payment_authorization"})

    def test_honest_mock_passes_all_five_stripe_cases(self) -> None:
        runs = run_sync(
            provider=MockStripePaymentAuthorization(),
            capability="payment_authorization",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        stripe_runs = _stripe_only(runs)
        by_id = {run.test_case_id: run for run in stripe_runs}

        # The contract is that the honest wrapper grades 1.0 on
        # every case, including the decline + SCA cases. That's
        # because ``succeeded`` reflects wrapper success (we got an
        # authoritative answer); business success is the
        # ``output.status`` field, which the YAML cases match
        # against an ``accept`` list. A wrapper that correctly
        # reports requires_payment_method is HONEST, not failed.
        for case_id, run in by_id.items():
            self.assertGreaterEqual(
                run.score.quality_score,
                0.9,
                msg=f"honest case {case_id} expected >= 0.9, got {run.score.quality_score}",
            )

    def test_dishonest_mock_loses_overall_versus_honest_mock(self) -> None:
        """Bad-provider canary.

        The dishonest mock claims ``status=succeeded`` for every
        request. On the success cases (0001, 0002, 0003) it grades
        the same as the honest mock. On the decline (0004) and
        SCA (0005) cases its claimed status (``succeeded``) does
        NOT match the case's expected status accept list
        (``requires_payment_method`` / ``requires_action``), so it
        scores low on those cases. The honest mock matches both.
        Net: the honest mock wins on aggregate quality. This is
        the contract that protects the wrapper from regressing
        into "always claim success" behaviour — exactly the most
        dangerous payment-wrapper bug class.
        """

        good = _stripe_only(run_sync(
            provider=MockStripePaymentAuthorization(),
            capability="payment_authorization",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        ))
        bad = _stripe_only(run_sync(
            provider=MockStripePaymentAuthorization(
                provider_id="mock-stripe-bad", dishonest=True
            ),
            capability="payment_authorization",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        ))

        good_summary = summarize_runs(good)
        bad_summary = summarize_runs(bad)

        self.assertGreater(
            good_summary["average_quality"],
            bad_summary["average_quality"],
            msg="honest wrapper must outscore the dishonest one — it correctly "
            "matches the decline+SCA expected statuses while the dishonest "
            "wrapper claims success across the board.",
        )


if __name__ == "__main__":
    unittest.main()
