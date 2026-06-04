"""Unit tests for the Fix-3 verification-gate broadening.

Why this file exists
--------------------
Before Fix 3, ``_is_qualified_candidate`` in
``planmyagents_api.discovery.gaps`` required
``verification_status == "capability_verified"``. That single-tier
gate was responsible for the user-visible "no agents discovered"
failure on the gift goal:

* Curated agents (Stripe, Shopify, Razorpay, Google Places, …) ship
  with ``verification_status="known_provider"`` — they exist and
  are reachable, but we haven't programmatically run their tools.
  All of them were rejected.

* Live-discovered MCP servers (Perplexity MCP @ match_score 0.93,
  Linkup MCP @ 0.93, Walmart MCP, etc.) come back as
  ``known_provider`` after the scout fetches their card. The
  post-goal probe stage upgrades them to ``capability_verified``
  asynchronously, but the request that triggered the discovery
  never benefits from that upgrade. All of them were rejected.

Fix 3 broadens the gate to accept the trust ladder above
``unverified``: ``capability_verified``, ``registered_in_directory``,
``known_provider``. ``community_listed`` and ``unverified`` remain
gated because they are noisier signals and require operator
verification before being surfaced as routable candidates.

Test strategy
-------------
We exercise ``build_gap_report`` directly with hand-built candidate
dicts so each test pins the gate's behaviour for a single
verification status and provider type combination. No live registry,
no LLM judge — just the gate logic.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.gaps import (
    QUALIFIED_VERIFICATION_STATUSES,
    _is_qualified_candidate,
    build_gap_report,
)
from planmyagents_api.planner.goal import GoalPlan


def _candidate(
    *,
    provider_id: str,
    provider_type: str,
    verification_status: str,
    capabilities: list[str],
    match_score: float = 0.9,
) -> dict:
    """Hand-build a discovery candidate with the bare minimum fields
    ``build_gap_report`` and ``_is_qualified_candidate`` look at.

    Keeping the shape minimal makes failures easy to pinpoint —
    ``provider_type`` and ``verification_status`` are the two fields
    the gate looks at, everything else is just enough to round-trip
    through the gap-report builder."""

    return {
        "provider_id": provider_id,
        "display_name": provider_id,
        "provider_type": provider_type,
        "verification_status": verification_status,
        "capabilities": list(capabilities),
        "match_score": match_score,
        "will_fail": False,
        "will_fail_reasons": [],
        "required_env_vars": [],
        "benchmark_status": "not_started",
    }


def _refusal_plan(missing: list[str]) -> GoalPlan:
    return GoalPlan(
        status="unsupported",
        summary="test",
        missing_capabilities=missing,
    )


class QualifiedVerificationStatusSet(unittest.TestCase):
    """Pin the set of statuses the gate considers qualified.

    The set is referenced by both ``_is_qualified_candidate`` and
    the ``rejection_reason`` text — making it a frozen module-level
    constant lets the test pin both at once and gives the UI a stable
    list to render in trust-tier badges.
    """

    def test_qualified_set_contains_top_three_tiers(self) -> None:
        self.assertEqual(
            QUALIFIED_VERIFICATION_STATUSES,
            frozenset(
                {
                    "capability_verified",
                    "registered_in_directory",
                    "known_provider",
                }
            ),
        )

    def test_unverified_and_community_listed_are_excluded(self) -> None:
        self.assertNotIn("unverified", QUALIFIED_VERIFICATION_STATUSES)
        self.assertNotIn("community_listed", QUALIFIED_VERIFICATION_STATUSES)


class GateAcceptsBroadenedTrustLadder(unittest.TestCase):
    """Each qualified status, paired with an agentic provider type,
    must pass the gate. Pin all 9 combinations so a future tightening
    can't silently re-introduce the gift-goal failure mode.
    """

    AGENTIC_TYPES = ["mcp_server", "a2a_agent", "ai_agent"]
    QUALIFIED_STATUSES = sorted(QUALIFIED_VERIFICATION_STATUSES)

    def test_every_agentic_type_with_qualified_status_passes_gate(self) -> None:
        for provider_type in self.AGENTIC_TYPES:
            for verification_status in self.QUALIFIED_STATUSES:
                with self.subTest(
                    provider_type=provider_type,
                    verification_status=verification_status,
                ):
                    candidate = _candidate(
                        provider_id=f"{provider_type}-{verification_status}",
                        provider_type=provider_type,
                        verification_status=verification_status,
                        capabilities=["web_search"],
                    )
                    self.assertTrue(
                        _is_qualified_candidate(candidate),
                        f"{provider_type}/{verification_status} should pass gate",
                    )


class GateRejectsLowerTrustLadder(unittest.TestCase):
    """``community_listed`` and ``unverified`` remain gated. Pin so
    a future "let everything through" change doesn't accidentally
    turn the discovery-store into noise.
    """

    def test_community_listed_does_not_pass_gate(self) -> None:
        candidate = _candidate(
            provider_id="moltbook-self-listed-agent",
            provider_type="ai_agent",
            verification_status="community_listed",
            capabilities=["web_search"],
        )
        self.assertFalse(_is_qualified_candidate(candidate))

    def test_unverified_does_not_pass_gate(self) -> None:
        candidate = _candidate(
            provider_id="anonymous-mcp",
            provider_type="mcp_server",
            verification_status="unverified",
            capabilities=["web_search"],
        )
        self.assertFalse(_is_qualified_candidate(candidate))

    def test_missing_verification_status_does_not_pass_gate(self) -> None:
        """Defensive: if a candidate has no ``verification_status``
        field at all (legacy rows), the gate falls back to
        ``"unverified"`` semantics and rejects it."""

        candidate = {
            "provider_id": "legacy-row",
            "display_name": "Legacy Row",
            "provider_type": "mcp_server",
            "capabilities": ["web_search"],
            "match_score": 0.9,
        }
        self.assertFalse(_is_qualified_candidate(candidate))

    def test_non_agentic_provider_type_does_not_pass_gate(self) -> None:
        """Even with the highest verification status, an
        ``api_provider`` or ``payment_provider`` is not a "qualified
        agentic candidate" — that's the Path A separation the rest
        of the discovery pipeline depends on."""

        for provider_type in ["api_provider", "payment_provider"]:
            with self.subTest(provider_type=provider_type):
                candidate = _candidate(
                    provider_id=f"{provider_type}-example",
                    provider_type=provider_type,
                    verification_status="capability_verified",
                    capabilities=["web_search"],
                )
                self.assertFalse(_is_qualified_candidate(candidate))


class GiftGoalRegression(unittest.TestCase):
    """The exact failure mode the user reported: high-quality MCP
    servers and curated AI agents discovered for the gift goal must
    now flow through to the gap report's ``best_candidate`` instead
    of being dumped into ``rejected_candidates`` with a
    "no verified public evidence" boilerplate.

    This is the integration-level pin that complements the unit
    pins above — it would have caught the regression that originally
    prompted Fix 3.
    """

    def test_perplexity_mcp_qualifies_for_web_search(self) -> None:
        """Perplexity MCP shipped from Smithery as
        ``known_provider``. Pre-Fix-3 the gate rejected it; post-
        Fix-3 it must show up as the best candidate for
        ``web_search``."""

        plan = _refusal_plan(["web_search"])
        discovery = {
            "candidates": [
                _candidate(
                    provider_id="ai-perplexity-mcp-server",
                    provider_type="mcp_server",
                    verification_status="known_provider",
                    capabilities=["web_search"],
                    match_score=0.93,
                ),
            ]
        }
        report = build_gap_report(plan=plan, discovery=discovery)
        web_gap = next(
            g for g in report["capability_gaps"] if g["capability"] == "web_search"
        )
        self.assertNotEqual(
            web_gap["status"], "no_qualified_candidate_found",
            f"Perplexity MCP should qualify for web_search; got: {web_gap['status']}",
        )
        self.assertIsNotNone(web_gap["best_candidate"])
        self.assertEqual(
            web_gap["best_candidate"]["provider_id"], "ai-perplexity-mcp-server"
        )

    def test_curated_known_provider_agent_qualifies_for_store_locator(
        self,
    ) -> None:
        """The curated registry ships Google Places as
        ``known_provider``. It is the canonical
        ``store_locator`` provider; pre-Fix-3 the gate rejected it
        for not being ``capability_verified``."""

        plan = _refusal_plan(["store_locator"])
        discovery = {
            "candidates": [
                _candidate(
                    provider_id="google-places",
                    provider_type="ai_agent",
                    verification_status="known_provider",
                    capabilities=["store_locator"],
                    match_score=0.88,
                ),
            ]
        }
        report = build_gap_report(plan=plan, discovery=discovery)
        gap = next(
            g for g in report["capability_gaps"] if g["capability"] == "store_locator"
        )
        self.assertIsNotNone(gap["best_candidate"])
        self.assertEqual(gap["best_candidate"]["provider_id"], "google-places")

    def test_rejection_reason_names_status_and_eligible_set(self) -> None:
        """When the gate DOES reject (community_listed /
        unverified / non-agentic), the rejection reason must name
        the actual status and the eligible set so the operator
        knows what would unblock the candidate."""

        plan = _refusal_plan(["web_search"])
        discovery = {
            "candidates": [
                _candidate(
                    provider_id="moltbook-self-listed",
                    provider_type="ai_agent",
                    verification_status="community_listed",
                    capabilities=["web_search"],
                    match_score=0.9,
                ),
            ]
        }
        report = build_gap_report(plan=plan, discovery=discovery)
        web_gap = next(
            g for g in report["capability_gaps"] if g["capability"] == "web_search"
        )
        self.assertEqual(
            web_gap["status"], "no_qualified_candidate_found"
        )
        rejected = web_gap["rejected_candidates"]
        self.assertEqual(len(rejected), 1)
        reason = rejected[0]["rejection_reason"]
        self.assertIn("community_listed", reason)
        for status in QUALIFIED_VERIFICATION_STATUSES:
            self.assertIn(status, reason)


if __name__ == "__main__":
    unittest.main()
