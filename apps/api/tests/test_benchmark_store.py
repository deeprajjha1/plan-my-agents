from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.models import BenchmarkRun, FieldScore, ProviderResponse, ScoreResult
from planmyagents_api.benchmark.store import JsonBenchmarkStore
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import normalize_candidate
from planmyagents_api.registry.promotions import apply_benchmark_statuses


class BenchmarkStoreTest(unittest.TestCase):
    def test_json_benchmark_store_derives_latest_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonBenchmarkStore(Path(directory) / "runs.json")
            store.save([_run(score=0.95, succeeded=True)])

            statuses = store.latest_statuses()

        self.assertEqual("passed", statuses[("candidate-agent", "semantic_search")])

    def test_benchmark_statuses_apply_to_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "runs.json"
            JsonBenchmarkStore(store_path).save([_run(score=0.95, succeeded=True)])
            candidate = _candidate()

            updated = apply_benchmark_statuses([candidate], str(store_path))

        self.assertEqual("passed", updated[0].benchmark_status)


def _candidate() -> DiscoveryCandidate:
    return normalize_candidate(
        {
            "id": "candidate-agent",
            "display_name": "Candidate Agent",
            "vendor": "Candidate Agent",
            "vendor_url": "https://candidate.example",
            "provider_type": "a2a_agent",
            "verification_status": "capability_verified",
            "capabilities": [{"id": "semantic_search", "confidence": 0.9}],
        },
        source="test",
    )


def _run(*, score: float, succeeded: bool) -> BenchmarkRun:
    return BenchmarkRun(
        test_case_id="case-1",
        provider_id="candidate-agent",
        capability="semantic_search",
        difficulty="easy",
        response=ProviderResponse(
            succeeded=succeeded,
            output={"status": "ok"},
            cost_usd=0.0,
            latency_ms=10,
        ),
        score=ScoreResult(
            quality_score=score,
            succeeded=succeeded,
            field_scores=[
                FieldScore(field="status", weight=1.0, score=score, reason="test")
            ],
            reason="test",
        ),
    )


if __name__ == "__main__":
    unittest.main()
