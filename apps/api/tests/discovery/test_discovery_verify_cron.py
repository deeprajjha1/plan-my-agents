"""Unit tests for the discovery-verify cron logic (sprint-pitch-align P3-2).

The cron has three pieces of meaningful logic worth unit-testing without
touching Postgres or live HTTP:

* `_is_due`        — was the candidate last verified longer ago than its
                     tier's cadence?
* `_is_stale_for_demotion` — has the recovery window (2 * cadence) elapsed?
* `_demote_if_stale` — combines the above with the tier ladder to drop
                       chronically-failing candidates one tier.

These tests avoid the Postgres dependency so they run on every commit.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts" / "discovery"))

from run_discovery_verify_cron import (  # noqa: E402
    TIER_DEMOTION,
    TIER_INTERVAL_DAYS,
    _demote_if_stale,
    _is_due,
    _is_stale_for_demotion,
)


@dataclass
class _FakeCandidate:
    id: str
    dedupe_key: str
    verification_status: str
    evidence_url: str = ""
    vendor_url: str = ""
    # The cron's demotion path round-trips through `c.__dict__`, so we need
    # to mimic the real DiscoveryCandidate field surface enough for that to
    # not blow up.
    capabilities: list = field(default_factory=list)


class _FakeDiscoveryStore:
    """Stand-in for `PostgresDiscoveryStore` so the cron logic is testable
    without a live database."""

    def __init__(self, candidates: list[_FakeCandidate]) -> None:
        self._candidates = list(candidates)
        self.saved_payloads: list[list[_FakeCandidate]] = []

    def load(self) -> list[_FakeCandidate]:
        return list(self._candidates)

    def save(self, candidates: list[_FakeCandidate]) -> None:
        self._candidates = list(candidates)
        self.saved_payloads.append(list(candidates))


class IsDueTest(unittest.TestCase):
    def test_returns_true_when_never_verified(self) -> None:
        now = datetime(2026, 5, 18, tzinfo=UTC)
        self.assertTrue(_is_due(None, "known_provider", now=now))
        self.assertTrue(_is_due("", "known_provider", now=now))

    def test_returns_false_when_within_cadence(self) -> None:
        now = datetime(2026, 5, 18, tzinfo=UTC)
        recent = (now - timedelta(days=1)).isoformat()
        self.assertFalse(_is_due(recent, "known_provider", now=now))

    def test_returns_true_when_past_cadence(self) -> None:
        now = datetime(2026, 5, 18, tzinfo=UTC)
        stale = (now - timedelta(days=TIER_INTERVAL_DAYS["known_provider"] + 1)).isoformat()
        self.assertTrue(_is_due(stale, "known_provider", now=now))

    def test_unparseable_timestamps_treated_as_due(self) -> None:
        now = datetime(2026, 5, 18, tzinfo=UTC)
        self.assertTrue(_is_due("not-a-timestamp", "known_provider", now=now))


class IsStaleForDemotionTest(unittest.TestCase):
    def test_only_stale_after_double_cadence(self) -> None:
        now = datetime(2026, 5, 18, tzinfo=UTC)
        cadence = TIER_INTERVAL_DAYS["known_provider"]
        almost = (now - timedelta(days=cadence + 1)).isoformat()
        well_past = (now - timedelta(days=2 * cadence + 1)).isoformat()

        self.assertFalse(_is_stale_for_demotion(almost, "known_provider", now=now))
        self.assertTrue(_is_stale_for_demotion(well_past, "known_provider", now=now))


class DemoteIfStaleTest(unittest.TestCase):
    def test_demotes_known_provider_to_registered_when_stale_and_fresh_fails(self) -> None:
        now = datetime(2026, 5, 18, tzinfo=UTC)
        candidate = _FakeCandidate(
            id="x",
            dedupe_key="x-dedupe",
            verification_status="known_provider",
        )
        store = _FakeDiscoveryStore([candidate])
        well_past = (now - timedelta(days=2 * TIER_INTERVAL_DAYS["known_provider"] + 1)).isoformat()

        demoted = _demote_if_stale(
            store,
            candidate,
            fresh_status="unverified",
            last_good_at=well_past,
            now=now,
        )

        self.assertEqual(demoted, TIER_DEMOTION["known_provider"])
        self.assertEqual(store.saved_payloads[-1][0].verification_status, demoted)

    def test_does_not_demote_when_fresh_verification_succeeds(self) -> None:
        now = datetime(2026, 5, 18, tzinfo=UTC)
        candidate = _FakeCandidate(
            id="x",
            dedupe_key="x-dedupe",
            verification_status="known_provider",
        )
        store = _FakeDiscoveryStore([candidate])
        well_past = (now - timedelta(days=2 * TIER_INTERVAL_DAYS["known_provider"] + 1)).isoformat()

        demoted = _demote_if_stale(
            store,
            candidate,
            fresh_status="known_provider",
            last_good_at=well_past,
            now=now,
        )

        self.assertIsNone(demoted)
        self.assertEqual(store.saved_payloads, [])

    def test_does_not_demote_within_recovery_window(self) -> None:
        now = datetime(2026, 5, 18, tzinfo=UTC)
        candidate = _FakeCandidate(
            id="x",
            dedupe_key="x-dedupe",
            verification_status="known_provider",
        )
        store = _FakeDiscoveryStore([candidate])
        within_window = (now - timedelta(days=TIER_INTERVAL_DAYS["known_provider"] + 2)).isoformat()

        demoted = _demote_if_stale(
            store,
            candidate,
            fresh_status="unverified",
            last_good_at=within_window,
            now=now,
        )

        self.assertIsNone(demoted)


if __name__ == "__main__":
    unittest.main()
