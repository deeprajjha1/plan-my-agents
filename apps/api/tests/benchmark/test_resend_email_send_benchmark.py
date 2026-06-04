"""Benchmark-runner integration test for the Resend email_send wrapper."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.mock import MockResendEmailSender
from planmyagents_api.benchmark.report import summarize_runs
from planmyagents_api.benchmark.runner import run_sync


class ResendEmailSendBenchmarkTests(unittest.TestCase):
    def test_honest_mock_runs_all_five_cases(self) -> None:
        runs = run_sync(
            provider=MockResendEmailSender(),
            capability="email_send",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        self.assertEqual(len(runs), 5)
        self.assertTrue(all(run.provider_id == "mock-resend-emails" for run in runs))

    def test_honest_mock_passes_all_five_cases(self) -> None:
        runs = run_sync(
            provider=MockResendEmailSender(),
            capability="email_send",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        for run in runs:
            self.assertGreaterEqual(
                run.score.quality_score,
                0.9,
                msg=f"honest case {run.test_case_id} expected >= 0.9, got "
                    f"{run.score.quality_score}",
            )

    def test_dishonest_mock_loses_on_id_format(self) -> None:
        """The dishonest mock returns ``email_id="mock-fake-id-not-from-resend"``,
        which fails the UUID-shape format_check on case 0005 and
        scores lower in aggregate.
        """

        good = run_sync(
            provider=MockResendEmailSender(),
            capability="email_send",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        bad = run_sync(
            provider=MockResendEmailSender(
                provider_id="mock-resend-bad", dishonest=True
            ),
            capability="email_send",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        good_summary = summarize_runs(good)
        bad_summary = summarize_runs(bad)
        self.assertGreater(
            good_summary["average_quality"], bad_summary["average_quality"]
        )


if __name__ == "__main__":
    unittest.main()
