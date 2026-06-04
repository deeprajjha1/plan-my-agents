"""Tests for the per-capability count helper used by the candidate
judge to feed exact ``judge_evaluated`` and ``judge_accepted`` numbers
to the discovery-gap recorder.

Why a dedicated test file: the helper is small but its semantic is
the contract that lets the discovery-gap leaderboard show exact
counts instead of the request-level approximation we shipped earlier.
A regression that off-by-ones the count would corrupt the tile
silently, so we lock the behaviour down here.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.web.app import _per_capability_counts  # noqa: E402


@dataclass
class _FakeCapability:
    """Minimal stand-in for ``CandidateCapability`` — the helper only
    reads ``.id``, so we don't pull the full model class into a unit
    test that would otherwise run in microseconds."""

    id: str


@dataclass
class _FakeCandidate:
    """Minimal stand-in for ``DiscoveryCandidate``. The helper reads
    only ``.capabilities[*].id``."""

    capabilities: list[_FakeCapability] = field(default_factory=list)


class PerCapabilityCountsTests(unittest.TestCase):
    def test_empty_inputs_return_zero_for_each_requested_capability(self) -> None:
        # Zero-fill contract: the recorder relies on every requested
        # capability appearing in the returned dict so it never has to
        # branch on missing keys.
        self.assertEqual(
            _per_capability_counts([], ["a", "b"]),
            {"a": 0, "b": 0},
        )

    def test_single_capability_candidate_counts_once(self) -> None:
        candidates = [_FakeCandidate(capabilities=[_FakeCapability(id="a")])]
        self.assertEqual(
            _per_capability_counts(candidates, ["a", "b"]),
            {"a": 1, "b": 0},
        )

    def test_multi_capability_candidate_counts_in_every_matching_bucket(
        self,
    ) -> None:
        # A candidate claiming two capabilities must increment BOTH
        # buckets — that's the right semantic for "how many candidates
        # claim to support capability X". The recorder uses these
        # counts independently per capability; double-counting across
        # capabilities is intentional.
        candidates = [
            _FakeCandidate(
                capabilities=[
                    _FakeCapability(id="a"),
                    _FakeCapability(id="b"),
                ]
            )
        ]
        self.assertEqual(
            _per_capability_counts(candidates, ["a", "b"]),
            {"a": 1, "b": 1},
        )

    def test_unrequested_capabilities_are_ignored(self) -> None:
        # The judge's per-capability map must only include requested
        # capabilities — the gap recorder iterates them and would log
        # spurious rows for unrequested ones.
        candidates = [
            _FakeCandidate(
                capabilities=[
                    _FakeCapability(id="a"),
                    _FakeCapability(id="x"),  # not requested
                ]
            )
        ]
        self.assertEqual(
            _per_capability_counts(candidates, ["a"]),
            {"a": 1},
        )
        self.assertNotIn("x", _per_capability_counts(candidates, ["a"]))

    def test_multiple_candidates_aggregate_per_capability(self) -> None:
        candidates = [
            _FakeCandidate(capabilities=[_FakeCapability(id="a")]),
            _FakeCandidate(capabilities=[_FakeCapability(id="a")]),
            _FakeCandidate(capabilities=[_FakeCapability(id="b")]),
        ]
        self.assertEqual(
            _per_capability_counts(candidates, ["a", "b"]),
            {"a": 2, "b": 1},
        )

    def test_candidate_with_no_capabilities_contributes_zero(self) -> None:
        # Defensive: a malformed candidate row (legitimate enough to
        # have reached the judge but with empty capabilities) must
        # not crash and must not be counted.
        candidates = [_FakeCandidate(capabilities=[])]
        self.assertEqual(
            _per_capability_counts(candidates, ["a"]),
            {"a": 0},
        )

    def test_duplicate_capability_ids_on_one_candidate_count_once(self) -> None:
        # Hardening: even if upstream data accidentally has the same
        # capability id listed twice on one candidate, we still count
        # the candidate once for that capability. Otherwise the
        # leaderboard tile would inflate.
        candidates = [
            _FakeCandidate(
                capabilities=[
                    _FakeCapability(id="a"),
                    _FakeCapability(id="a"),
                ]
            )
        ]
        self.assertEqual(_per_capability_counts(candidates, ["a"]), {"a": 1})


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
