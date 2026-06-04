"""Unit tests for ``_best_verification_status`` in
``planmyagents_api.discovery.dedupe``.

Why this file exists
--------------------
The dedupe step merges two duplicate ``DiscoveryCandidate`` rows
into one. When the rows disagree on ``verification_status``, the
merge must keep the higher-trust tier — otherwise a freshly
upgraded row (e.g. Smithery → ``registered_in_directory``) gets
silently demoted by an older ``unverified`` row that's still in
the local store.

Pre-Fix-3 the priority map only knew three statuses
(``capability_verified`` / ``known_provider`` / ``unverified``).
``registered_in_directory`` (Smithery, MCP Marketplace, official
MCP registry) and ``community_listed`` (Moltbook) defaulted to
priority 0 via ``priority.get(..., 0)``, which made them lose
ties against ``unverified`` rows. This file pins the full ladder
so a future addition of a new tier is forced to update the map
or the test breaks.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.dedupe import _best_verification_status


class VerificationPriorityLadder(unittest.TestCase):
    """Pin the strict order of the verification ladder.

    The order matters because it's referenced by both the dedupe
    merge AND the gap-report qualification gate; if they diverge a
    candidate could be "qualified enough to surface" but "not high-
    enough trust to win a merge" — confusing for the operator and
    silently corrupting the local store.
    """

    LADDER = [
        "capability_verified",
        "registered_in_directory",
        "known_provider",
        "community_listed",
        "unverified_example",
        "unverified",
    ]

    def test_each_status_beats_every_lower_one(self) -> None:
        """For every (higher, lower) pair, picking either order
        should yield ``higher``. Pinning both directions catches
        an off-by-one in the comparison operator."""

        for higher_index, higher in enumerate(self.LADDER):
            for lower in self.LADDER[higher_index + 1 :]:
                with self.subTest(higher=higher, lower=lower):
                    self.assertEqual(
                        _best_verification_status(higher, lower),
                        higher,
                        f"{higher} should beat {lower}",
                    )
                    self.assertEqual(
                        _best_verification_status(lower, higher),
                        higher,
                        f"{higher} should beat {lower} regardless of arg order",
                    )


class FixThreeRegressionPins(unittest.TestCase):
    """The exact merges that fail pre-Fix-3 and must succeed now."""

    def test_smithery_upgrade_overrides_stale_unverified(self) -> None:
        """The bedrock failure mode: a Smithery scout re-runs and
        emits a row with ``registered_in_directory``, but the local
        store already has the same dedupe key tagged as
        ``unverified`` (from before Fix 3). Pre-Fix-3 the dedupe
        kept the stale ``unverified`` row; post-Fix-3 the upgrade
        wins."""

        result = _best_verification_status(
            existing="unverified", incoming="registered_in_directory"
        )
        self.assertEqual(result, "registered_in_directory")

    def test_marketplace_upgrade_overrides_stale_unverified(self) -> None:
        """Same shape as Smithery — Marketplace scout pushes the
        upgrade, dedupe must preserve it."""

        result = _best_verification_status(
            existing="unverified", incoming="registered_in_directory"
        )
        self.assertEqual(result, "registered_in_directory")

    def test_capability_verified_beats_registered_in_directory(self) -> None:
        """The post-goal MCP probe upgrades ``registered_in_directory``
        to ``capability_verified`` after a successful tools/list. The
        upgrade must persist on next dedupe pass."""

        result = _best_verification_status(
            existing="registered_in_directory",
            incoming="capability_verified",
        )
        self.assertEqual(result, "capability_verified")

    def test_community_listed_beats_unverified_but_loses_to_known_provider(
        self,
    ) -> None:
        """Moltbook's ``community_listed`` is a real signal — at
        least one corroborating artifact link — so it beats raw
        ``unverified``. But it's still below ``known_provider``
        because we haven't independently confirmed reachability."""

        self.assertEqual(
            _best_verification_status("unverified", "community_listed"),
            "community_listed",
        )
        self.assertEqual(
            _best_verification_status("community_listed", "known_provider"),
            "known_provider",
        )

    def test_unknown_status_is_treated_as_lowest_priority(self) -> None:
        """Defensive: a status the map doesn't know about (legacy
        store row, future tier rolled out without updating the
        map) should never beat a known status. Treats unknowns as
        priority 0 so they lose to anything we recognise."""

        self.assertEqual(
            _best_verification_status("unknown_status", "registered_in_directory"),
            "registered_in_directory",
        )
        self.assertEqual(
            _best_verification_status("registered_in_directory", "unknown_status"),
            "registered_in_directory",
        )


if __name__ == "__main__":
    unittest.main()
