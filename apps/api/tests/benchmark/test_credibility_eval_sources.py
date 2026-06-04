from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.credibility import _is_real_run, classify
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.eval.models import NON_REAL_EVAL_SOURCES


def _candidate(provider_id: str) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=provider_id,
        display_name=provider_id,
        vendor=provider_id,
        vendor_url="https://example.com",
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
    )


class CredibilityEvalSourcesTest(unittest.TestCase):
    def test_new_eval_sources_are_non_real(self) -> None:
        for source in NON_REAL_EVAL_SOURCES:
            row = {"source": source, "sample_size": 50}
            self.assertFalse(_is_real_run(row), f"{source} should be non-real")

    def test_exact_match_and_judge_are_real(self) -> None:
        self.assertTrue(_is_real_run({"source": "exact_match", "sample_size": 1}))
        self.assertTrue(_is_real_run({"source": "judge", "sample_size": 1}))

    def test_zero_sample_real_source_is_not_real(self) -> None:
        self.assertFalse(_is_real_run({"source": "exact_match", "sample_size": 0}))

    def test_existing_synthetic_still_non_real(self) -> None:
        for source in ("synthetic", "mock", "fixture", "stub"):
            self.assertFalse(_is_real_run({"source": source, "sample_size": 99}))

    def test_verification_only_keeps_cell_synthetic_only(self) -> None:
        # A cell whose only ranking is a verification-only eval row must stay
        # synthetic_only — the no-overclaim / monotonicity property.
        verdict = classify(
            capability="web_scraping",
            candidates=[_candidate("p1")],
            rankings=[
                {
                    "provider_id": "p1",
                    "capability": "web_scraping",
                    "source": "verification_only",
                    "sample_size": 5,
                    "last_run_at": datetime.now(UTC).isoformat(),
                }
            ],
        )
        self.assertEqual(verdict.status, "synthetic_only")
        self.assertEqual(verdict.real_provider_count, 0)

    def test_real_eval_rows_can_reach_smoke_test(self) -> None:
        # One real exact_match provider with a small sample → smoke_test
        # (real data, but below the 3-provider / 30-sample publishable bar).
        verdict = classify(
            capability="web_scraping",
            candidates=[_candidate("p1")],
            rankings=[
                {
                    "provider_id": "p1",
                    "capability": "web_scraping",
                    "source": "exact_match",
                    "sample_size": 5,
                    "last_run_at": datetime.now(UTC).isoformat(),
                }
            ],
        )
        self.assertEqual(verdict.status, "smoke_test")
        self.assertEqual(verdict.real_provider_count, 1)


if __name__ == "__main__":
    unittest.main()
