"""Tests for source-observation tracking on discovery candidates."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.dedupe import merge_candidates
from planmyagents_api.discovery.models import (
    CandidateCapability,
    CandidateObservation,
    DiscoveryCandidate,
)
from planmyagents_api.discovery.normalizer import (
    candidate_from_registry,
    normalize_candidate,
)


def _candidate(*, source: str, evidence_url: str = "") -> DiscoveryCandidate:
    return normalize_candidate(
        {
            "id": "shared",
            "display_name": "Shared",
            "vendor": "Shared",
            "vendor_url": "https://shared.example",
            "provider_type": "mcp_server",
            "capabilities": ["q"],
            "evidence_url": evidence_url or "https://shared.example/about",
        },
        source=source,
    )


class CandidateObservationTests(unittest.TestCase):
    def test_normaliser_seeds_one_observation_per_call(self) -> None:
        candidate = _candidate(source="curated_mcp")
        self.assertEqual(len(candidate.observations), 1)
        self.assertEqual(candidate.observations[0].source_id, "curated_mcp")
        self.assertEqual(candidate.confirmation_count, 1)
        self.assertEqual(candidate.source_ids, ["curated_mcp"])

    def test_merging_two_sources_unions_observations(self) -> None:
        a = _candidate(source="curated_mcp", evidence_url="https://shared.example/a")
        b = _candidate(source="github_research", evidence_url="https://shared.example/b")

        merged = merge_candidates(a, b)

        self.assertEqual(merged.confirmation_count, 2)
        self.assertEqual(merged.source_ids, ["curated_mcp", "github_research"])
        self.assertEqual(len(merged.observations), 2)

    def test_merging_same_source_same_url_dedupes_observation(self) -> None:
        a = _candidate(source="curated_mcp", evidence_url="https://shared.example/x")
        b = _candidate(source="curated_mcp", evidence_url="https://shared.example/x")

        merged = merge_candidates(a, b)
        self.assertEqual(len(merged.observations), 1)
        self.assertEqual(merged.confirmation_count, 1)

    def test_merging_three_sources_keeps_all_observations(self) -> None:
        a = _candidate(source="curated_mcp")
        b = _candidate(source="github_research", evidence_url="https://shared.example/b")
        c = _candidate(source="brave_web_search", evidence_url="https://shared.example/c")

        merged = merge_candidates(merge_candidates(a, b), c)
        self.assertEqual(merged.confirmation_count, 3)
        self.assertEqual(
            merged.source_ids,
            ["brave_web_search", "curated_mcp", "github_research"],
        )

    def test_to_registry_json_roundtrips_observations(self) -> None:
        a = _candidate(source="curated_mcp")
        b = _candidate(source="github_research", evidence_url="https://shared.example/b")
        merged = merge_candidates(a, b)

        restored = candidate_from_registry(merged.to_registry_json())
        self.assertEqual(restored.confirmation_count, 2)
        self.assertEqual(
            sorted(obs.source_id for obs in restored.observations),
            ["curated_mcp", "github_research"],
        )

    def test_public_summary_exposes_confirmation_count(self) -> None:
        candidate = DiscoveryCandidate(
            id="x",
            display_name="X",
            vendor="X",
            vendor_url="https://x.example",
            provider_type="mcp_server",
            capabilities=[CandidateCapability(id="search", confidence=0.5)],
            observations=[
                CandidateObservation(source_id="a", observed_at="2026-05-01"),
                CandidateObservation(source_id="b", observed_at="2026-05-02"),
            ],
        )
        summary = candidate.to_public_summary()
        self.assertEqual(summary["confirmation_count"], 2)
        self.assertEqual(summary["source_ids"], ["a", "b"])


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
