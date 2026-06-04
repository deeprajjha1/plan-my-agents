from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.models import (
    BenchmarkRun,
    FieldScore,
    ProviderResponse,
    ScoreResult,
)
from planmyagents_api.benchmark.rankings import (
    benchmark_status_from_ranking,
    compute_rankings,
)


def _run(
    *,
    provider: str,
    capability: str,
    case_id: str,
    succeeded: bool,
    quality: float,
    latency_ms: int,
    cost_usd: float,
) -> BenchmarkRun:
    return BenchmarkRun(
        test_case_id=case_id,
        provider_id=provider,
        capability=capability,
        difficulty="easy",
        response=ProviderResponse(
            succeeded=succeeded,
            output={"ok": succeeded},
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            error=None,
        ),
        score=ScoreResult(
            quality_score=quality,
            succeeded=succeeded,
            field_scores=[FieldScore(field="ok", weight=1.0, score=quality, reason="")],
            reason="",
        ),
    )


class RankingComputationTest(unittest.TestCase):
    def test_groups_runs_by_provider_and_capability(self) -> None:
        runs = [
            _run(
                provider="alpha",
                capability="email_verification",
                case_id="a1",
                succeeded=True,
                quality=1.0,
                latency_ms=100,
                cost_usd=0.001,
            ),
            _run(
                provider="alpha",
                capability="email_verification",
                case_id="a2",
                succeeded=True,
                quality=0.9,
                latency_ms=200,
                cost_usd=0.001,
            ),
            _run(
                provider="beta",
                capability="email_verification",
                case_id="b1",
                succeeded=False,
                quality=0.2,
                latency_ms=300,
                cost_usd=0.002,
            ),
        ]
        rankings = compute_rankings(runs)
        self.assertEqual(len(rankings), 2)
        alpha = next(r for r in rankings if r.provider_id == "alpha")
        beta = next(r for r in rankings if r.provider_id == "beta")

        self.assertEqual(alpha.sample_size, 2)
        self.assertAlmostEqual(alpha.success_rate, 1.0)
        self.assertAlmostEqual(alpha.avg_quality_score, 0.95, places=2)
        self.assertEqual(alpha.p50_latency_ms, 100)
        self.assertEqual(alpha.p95_latency_ms, 200)

        self.assertEqual(beta.sample_size, 1)
        self.assertAlmostEqual(beta.success_rate, 0.0)
        self.assertEqual(beta.p50_latency_ms, 300)

        # Alpha must outrank beta on composite score.
        self.assertGreater(alpha.composite_score, beta.composite_score)
        self.assertEqual(alpha.rank, 1)
        self.assertEqual(beta.rank, 2)

    def test_benchmark_status_passes_only_when_threshold_met(self) -> None:
        passing_runs = [
            _run(
                provider="alpha",
                capability="contact_enrichment",
                case_id=f"c{idx}",
                succeeded=True,
                quality=0.9,
                latency_ms=80,
                cost_usd=0.01,
            )
            for idx in range(3)
        ]
        rankings = compute_rankings(passing_runs)
        self.assertEqual(len(rankings), 1)
        self.assertEqual(benchmark_status_from_ranking(rankings[0]), "passed")
        self.assertEqual(rankings[0].benchmark_status, "passed")

        mixed_runs = passing_runs + [
            _run(
                provider="alpha",
                capability="contact_enrichment",
                case_id="cf",
                succeeded=False,
                quality=0.0,
                latency_ms=120,
                cost_usd=0.01,
            )
        ]
        rankings = compute_rankings(mixed_runs)
        self.assertEqual(rankings[0].benchmark_status, "failed")

    def test_empty_runs_returns_empty_rankings(self) -> None:
        self.assertEqual(compute_rankings([]), [])


if __name__ == "__main__":
    unittest.main()
