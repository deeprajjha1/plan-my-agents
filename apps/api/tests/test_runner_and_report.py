from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.mock import MockEmailVerifier
from planmyagents_api.benchmark.report import render_markdown_report, summarize_runs
from planmyagents_api.benchmark.runner import read_runs_json, run_sync, write_runs_json


class RunnerAndReportTest(unittest.TestCase):
    def test_runner_executes_email_benchmark_cases(self) -> None:
        runs = run_sync(
            provider=MockEmailVerifier(),
            capability="email_verification",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
            limit=5,
        )

        self.assertEqual(len(runs), 5)
        self.assertEqual(runs[0].provider_id, "mock-email-verifier")
        self.assertEqual(runs[0].capability, "email_verification")
        self.assertGreaterEqual(runs[0].score.quality_score, 0.0)

    def test_bad_provider_scores_lower_than_good_provider(self) -> None:
        good_runs = run_sync(
            provider=MockEmailVerifier(),
            capability="email_verification",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        bad_runs = run_sync(
            provider=MockEmailVerifier(provider_id="mock-email-bad", dishonest=True),
            capability="email_verification",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )

        good_summary = summarize_runs(good_runs)
        bad_summary = summarize_runs(bad_runs)

        self.assertGreater(good_summary["average_quality"], bad_summary["average_quality"])
        self.assertGreater(good_summary["success_rate"], bad_summary["success_rate"])

    def test_write_and_read_runs_json(self) -> None:
        runs = run_sync(
            provider=MockEmailVerifier(),
            capability="email_verification",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
            limit=2,
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "runs.json"
            write_runs_json(runs, output_path)
            payload = read_runs_json(output_path)

        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["provider_id"], "mock-email-verifier")

    def test_markdown_report_contains_summary_and_results_table(self) -> None:
        runs = run_sync(
            provider=MockEmailVerifier(),
            capability="email_verification",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
            limit=3,
        )

        report = render_markdown_report(runs)

        self.assertIn("# PlanMyAgents Benchmark Report", report)
        self.assertIn("Provider:", report)
        self.assertIn("| Case | Difficulty | Quality |", report)
        self.assertIn("email-0001-valid-saas-work-email", report)


if __name__ == "__main__":
    unittest.main()
