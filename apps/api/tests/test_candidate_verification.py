from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.normalizer import normalize_candidate
from planmyagents_api.discovery.verification import candidate_with_verification, verify_candidate


class CandidateVerificationTest(unittest.TestCase):
    def test_verifies_a2a_capability_from_agent_card(self) -> None:
        candidate = normalize_candidate(
            {
                "id": "calendar-agent",
                "display_name": "Calendar Agent",
                "vendor": "Calendar Agent",
                "provider_type": "a2a_agent",
                "evidence_url": "https://example.com/agent-card.json",
                "capabilities": [{"id": "calendar_access", "confidence": 0.8}],
            },
            source="test",
        )

        result = verify_candidate(
            candidate,
            fetch_text=lambda _url, _timeout: json.dumps(
                {"name": "Calendar Agent", "skills": [{"name": "calendar_access"}]}
            ),
        )
        updated = candidate_with_verification(candidate, result)

        self.assertEqual("capability_verified", result.status)
        self.assertEqual(["calendar_access"], result.verified_capabilities)
        self.assertEqual("capability_verified", updated.verification_status)

    def test_known_provider_does_not_imply_capability_verification(self) -> None:
        candidate = normalize_candidate(
            {
                "id": "calendar-agent",
                "display_name": "Calendar Agent",
                "vendor": "Calendar Agent",
                "provider_type": "a2a_agent",
                "evidence_url": "https://example.com/agent-card.json",
                "capabilities": [{"id": "calendar_access", "confidence": 0.8}],
            },
            source="test",
        )

        result = verify_candidate(
            candidate,
            fetch_text=lambda _url, _timeout: json.dumps(
                {"name": "Calendar Agent", "skills": [{"name": "email_verification"}]}
            ),
        )

        self.assertEqual("known_provider", result.status)
        self.assertIn("capability_evidence_missing", result.blockers)

    def test_vendor_name_match_promotes_to_known_provider(self) -> None:
        # Regression: previously a slug-style id like "razorpay-payments"
        # against a vendor homepage that only ever says "razorpay" was
        # downgraded to unverified, which made the Recent Verifications
        # panel for major first-party providers stay empty.
        candidate = normalize_candidate(
            {
                "id": "razorpay-payments",
                "display_name": "razorpay-payments",
                "vendor": "Razorpay",
                "vendor_url": "https://razorpay.com",
                "evidence_url": "https://razorpay.com",
                "provider_type": "ai_agent",
                "capabilities": [{"id": "payment_authorization", "confidence": 0.7}],
            },
            source="test",
        )

        result = verify_candidate(
            candidate,
            fetch_text=lambda _url, _timeout: (
                "<html><title>Razorpay — Payment Gateway</title>"
                "<body>Razorpay is the best payment solution.</body></html>"
            ),
        )

        self.assertEqual("known_provider", result.status)
        self.assertEqual([], result.verified_capabilities)
        self.assertIn("capability_evidence_missing", result.blockers)

    def test_id_token_match_works_for_first_party_slugs(self) -> None:
        # Same logic, but with the slug-prefix path (no vendor field).
        candidate = normalize_candidate(
            {
                "id": "stripe-payments",
                "display_name": "stripe-payments",
                "vendor": "",
                "vendor_url": "https://stripe.com",
                "evidence_url": "https://stripe.com",
                "provider_type": "ai_agent",
                "capabilities": [{"id": "payment_authorization", "confidence": 0.7}],
            },
            source="test",
        )

        result = verify_candidate(
            candidate,
            fetch_text=lambda _url, _timeout: "<html><body>Stripe is online payment infra.</body></html>",
        )

        self.assertEqual("known_provider", result.status)

    def test_missing_evidence_stays_unverified(self) -> None:
        candidate = normalize_candidate(
            {
                "id": "unknown-agent",
                "display_name": "Unknown Agent",
                "vendor": "Unknown Agent",
                "provider_type": "ai_agent",
                "capabilities": [{"id": "semantic_search", "confidence": 0.8}],
            },
            source="test",
        )

        result = verify_candidate(candidate, fetch_text=lambda _url, _timeout: "")

        self.assertEqual("unverified", result.status)
        self.assertIn("evidence_url_missing", result.blockers)


if __name__ == "__main__":
    unittest.main()
