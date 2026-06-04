from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.credibility import (  # noqa: E402
    CredibilityThresholds,
    classify,
    classify_index,
)
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate  # noqa: E402

NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
RECENT = (NOW - timedelta(days=2)).isoformat()
STALE = (NOW - timedelta(days=120)).isoformat()


def _candidate(provider_id: str, capability: str = "email_verification") -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=provider_id,
        display_name=provider_id,
        vendor=provider_id,
        vendor_url=f"https://{provider_id}.example",
        provider_type="api_provider",
        capabilities=[CandidateCapability(id=capability, confidence=0.9)],
    )


def _ranking(
    provider_id: str,
    capability: str = "email_verification",
    *,
    source: str = "real_adapter",
    sample_size: int = 50,
    last_run_at: str = RECENT,
    benchmark_status: str = "passed",
) -> dict[str, Any]:
    return {
        "provider_id": provider_id,
        "capability": capability,
        "source": source,
        "sample_size": sample_size,
        "last_run_at": last_run_at,
        "benchmark_status": benchmark_status,
        "composite_score": 0.7,
        "success_rate": 0.9,
    }


class CredibilityClassifierTest(unittest.TestCase):
    def test_synthetic_only_when_no_real_runs(self) -> None:
        verdict = classify(
            capability="email_verification",
            candidates=[_candidate("alpha"), _candidate("beta")],
            rankings=[
                _ranking("alpha", source="synthetic"),
                _ranking("beta", source="mock"),
            ],
            now=NOW,
        )
        self.assertEqual(verdict.status, "synthetic_only")
        self.assertEqual(verdict.real_provider_count, 0)
        self.assertGreater(len(verdict.unblockers), 0)

    def test_smoke_test_when_real_runs_below_provider_threshold(self) -> None:
        verdict = classify(
            capability="email_verification",
            candidates=[_candidate("alpha"), _candidate("beta")],
            rankings=[_ranking("alpha")],
            now=NOW,
        )
        self.assertEqual(verdict.status, "smoke_test")
        self.assertTrue(verdict.is_smoke_test)
        self.assertFalse(verdict.is_publishable)
        self.assertIn("at least 3", " ".join(verdict.reasons))

    def test_smoke_test_when_sample_size_below_threshold(self) -> None:
        verdict = classify(
            capability="email_verification",
            candidates=[
                _candidate("alpha"),
                _candidate("beta"),
                _candidate("gamma"),
            ],
            rankings=[
                _ranking("alpha", sample_size=5),
                _ranking("beta", sample_size=10),
                _ranking("gamma", sample_size=12),
            ],
            now=NOW,
        )
        self.assertEqual(verdict.status, "smoke_test")
        self.assertEqual(verdict.real_provider_count, 3)
        self.assertEqual(verdict.max_real_sample_size, 12)

    def test_developing_when_only_freshness_is_missing(self) -> None:
        verdict = classify(
            capability="email_verification",
            candidates=[
                _candidate("alpha"),
                _candidate("beta"),
                _candidate("gamma"),
            ],
            rankings=[
                _ranking("alpha", last_run_at=STALE),
                _ranking("beta", last_run_at=STALE),
                _ranking("gamma", last_run_at=STALE),
            ],
            now=NOW,
        )
        self.assertEqual(verdict.status, "developing")
        self.assertFalse(verdict.is_publishable)

    def test_publishable_when_all_thresholds_met(self) -> None:
        verdict = classify(
            capability="email_verification",
            candidates=[
                _candidate("alpha"),
                _candidate("beta"),
                _candidate("gamma"),
            ],
            rankings=[
                _ranking("alpha"),
                _ranking("beta"),
                _ranking("gamma"),
            ],
            now=NOW,
        )
        self.assertEqual(verdict.status, "publishable")
        self.assertTrue(verdict.is_publishable)
        self.assertEqual(verdict.unblockers, [])

    def test_thresholds_overridable(self) -> None:
        thresholds = CredibilityThresholds(
            min_real_providers=2, min_sample_size=10, max_run_age_days=365
        )
        verdict = classify(
            capability="email_verification",
            candidates=[_candidate("alpha"), _candidate("beta")],
            rankings=[
                _ranking("alpha", sample_size=12, last_run_at=STALE),
                _ranking("beta", sample_size=15, last_run_at=STALE),
            ],
            thresholds=thresholds,
            now=NOW,
        )
        self.assertEqual(verdict.status, "publishable")

    def test_zero_sample_size_real_source_does_not_count_as_real(self) -> None:
        # Defensive: a row tagged real_adapter with 0 samples is not credible.
        verdict = classify(
            capability="email_verification",
            candidates=[_candidate("alpha")],
            rankings=[_ranking("alpha", sample_size=0)],
            now=NOW,
        )
        self.assertEqual(verdict.status, "synthetic_only")

    def test_classify_index_returns_one_verdict_per_capability(self) -> None:
        candidates_by_capability = {
            "email_verification": [_candidate("alpha", "email_verification")],
            "web_scraping": [_candidate("beta", "web_scraping")],
        }
        verdicts = classify_index(
            candidates_by_capability=candidates_by_capability,
            rankings=[_ranking("alpha", source="synthetic")],
            now=NOW,
        )
        self.assertEqual(set(verdicts.keys()), {"email_verification", "web_scraping"})
        self.assertEqual(verdicts["email_verification"].status, "synthetic_only")
        self.assertEqual(verdicts["web_scraping"].status, "synthetic_only")


if __name__ == "__main__":
    unittest.main()
