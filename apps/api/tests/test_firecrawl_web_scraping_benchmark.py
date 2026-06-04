"""Benchmark-runner integration test for the Firecrawl web_scraping wrapper."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.mock import MockFirecrawlScraper
from planmyagents_api.benchmark.runner import run_sync


class FirecrawlWebScrapingBenchmarkTests(unittest.TestCase):
    def test_honest_mock_runs_all_five_cases(self) -> None:
        runs = run_sync(
            provider=MockFirecrawlScraper(),
            capability="web_scraping",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        self.assertEqual(len(runs), 5)
        self.assertTrue(all(run.provider_id == "mock-firecrawl" for run in runs))

    def test_honest_mock_passes_all_five_cases(self) -> None:
        runs = run_sync(
            provider=MockFirecrawlScraper(),
            capability="web_scraping",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        for run in runs:
            self.assertGreaterEqual(
                run.score.quality_score,
                0.9,
                msg=f"honest case {run.test_case_id} expected >= 0.9, got "
                    f"{run.score.quality_score}",
            )

    def test_dishonest_mock_loses_on_empty_page_case(self) -> None:
        """The dishonest mock hallucinates content for the
        ``/empty`` URL, which the adversarial case 0005 grades
        with ``char_count: equals: 0``. The honest mock returns
        empty content (matches), the dishonest mock returns
        hallucinated content (fails).
        """

        good = run_sync(
            provider=MockFirecrawlScraper(),
            capability="web_scraping",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        bad = run_sync(
            provider=MockFirecrawlScraper(
                provider_id="mock-firecrawl-bad", dishonest=True
            ),
            capability="web_scraping",
            benchmarks_dir=ROOT / "packages" / "benchmarks",
        )
        good_by_id = {r.test_case_id: r for r in good}
        bad_by_id = {r.test_case_id: r for r in bad}
        self.assertGreater(
            good_by_id["web-scrape-0005-adversarial-empty-page-honesty"].score.quality_score,
            bad_by_id["web-scrape-0005-adversarial-empty-page-honesty"].score.quality_score,
        )


if __name__ == "__main__":
    unittest.main()
