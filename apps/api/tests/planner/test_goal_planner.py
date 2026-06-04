from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.normalizer import normalize_candidate
from planmyagents_api.planner.capability_catalog import build_capability_catalog
from planmyagents_api.planner.goal import plan_goal


class GoalPlannerTest(unittest.TestCase):
    def test_plans_email_verification_when_emails_are_supplied(self) -> None:
        plan = plan_goal("Please verify jane@acme.com and bob@example.com")

        self.assertTrue(plan.executable)
        self.assertEqual(plan.status, "executable")
        self.assertEqual(len(plan.sub_tasks), 2)
        self.assertEqual(plan.sub_tasks[0].capability, "email_verification")

    def test_salesforce_email_verification_does_not_trigger_sfo_travel(self) -> None:
        plan = plan_goal(
            "verify if deepraj.jha@salesforce.com is valid email id",
            supported_capabilities={"email_verification"},
        )

        self.assertTrue(plan.executable)
        self.assertEqual(["email_verification"], [task.capability for task in plan.sub_tasks])
        self.assertEqual({"email": "deepraj.jha@salesforce.com"}, plan.sub_tasks[0].inputs)

    def test_refuses_contact_discovery_without_implemented_capability(self) -> None:
        catalog = _catalog(
            [
                (
                    "semantic-search-agent",
                    [
                        {
                            "id": "semantic_search",
                            "notes": "Find companies, prospects, and SaaS account lists.",
                        }
                    ],
                ),
                (
                    "contact-enrichment-agent",
                    [
                        {
                            "id": "contact_enrichment",
                            "notes": "Find CTO contacts and work email addresses.",
                        }
                    ],
                ),
            ]
        )
        plan = plan_goal(
            "Find CTO emails for 100 EU SaaS companies",
            capability_catalog=catalog,
        )

        self.assertFalse(plan.executable)
        self.assertIn("contact_enrichment", plan.missing_capabilities)
        self.assertIn("semantic_search", plan.missing_capabilities)

    def test_refuses_regulated_or_physical_world_goal(self) -> None:
        catalog = _catalog(
            [
                (
                    "legal-shipping-agent",
                    [
                        {"id": "legal_verification", "notes": "Check law violation risk."},
                        {"id": "cross_border_commerce", "notes": "Handle customs and import duty."},
                        {"id": "order_execution", "notes": "Ship physical goods and execute orders."},
                        {"id": "shipping_quote", "notes": "Quote international shipping."},
                    ],
                )
            ]
        )
        plan = plan_goal(
            "Find the cheapest PS5 globally and ship it to India without law violation",
            capability_catalog=catalog,
        )

        self.assertFalse(plan.executable)
        self.assertIn("legal_verification", plan.missing_capabilities)
        self.assertIn("cross_border_commerce", plan.missing_capabilities)
        self.assertIn("order_execution", plan.missing_capabilities)

    def test_refuses_travel_booking_with_domain_capabilities(self) -> None:
        catalog = _catalog(
            [
                (
                    "travel-booking-agent",
                    [
                        {"id": "travel_search", "notes": "Find flights and airline tickets."},
                        {"id": "fare_comparison", "notes": "Compare cheapest fares and prices."},
                        {"id": "booking_execution", "notes": "Book and reserve travel."},
                        {"id": "payment_authorization", "notes": "Authorize payment for booking."},
                    ],
                )
            ]
        )
        plan = plan_goal(
            "I want to book my flight from Bengaluru to San Francisco on 20th June 2026, suggest cheapest options",
            capability_catalog=catalog,
        )

        self.assertFalse(plan.executable)
        self.assertIn("travel_search", plan.missing_capabilities)
        self.assertIn("fare_comparison", plan.missing_capabilities)
        self.assertIn("booking_execution", plan.missing_capabilities)
        self.assertIn("payment_authorization", plan.missing_capabilities)

    def test_refuses_hotel_booking_with_lodging_capabilities(self) -> None:
        catalog = build_capability_catalog(
            [
                normalize_candidate(
                    {
                        "id": "test-lodging-booking-agent",
                        "display_name": "Test Lodging Booking Agent",
                        "vendor": "Test A2A Directory",
                        "vendor_url": "https://example.com",
                        "provider_type": "a2a_agent",
                        "capabilities": [
                            {"id": "lodging_search", "notes": "Find hotel stays."},
                            {"id": "lodging_comparison", "notes": "Compare lodging ratings."},
                            {"id": "booking_execution", "notes": "Reserve a room."},
                        ],
                    },
                    source="test",
                ),
                normalize_candidate(
                    {
                        "id": "payment-agent",
                        "display_name": "Payment Agent",
                        "vendor": "Payment Agent",
                        "vendor_url": "https://example.com/pay",
                        "provider_type": "a2a_agent",
                        "capabilities": [{"id": "payment_authorization"}],
                    },
                    source="test",
                ),
            ]
        )
        plan = plan_goal(
            "book me the decent hotel in san fransisco on 21st may, 2026",
            capability_catalog=catalog,
        )

        self.assertFalse(plan.executable)
        self.assertIn("lodging_search", plan.missing_capabilities)
        self.assertIn("lodging_comparison", plan.missing_capabilities)
        self.assertIn("booking_execution", plan.missing_capabilities)
        # NOTE: ``payment_authorization`` is no longer auto-attached to
        # any goal containing booking/order vocabulary. The deleted
        # BOOKING_TERMS heuristic in ``capability_catalog.py`` was a
        # vertical assumption ("book always implies pay") that didn't
        # generalise — for example, "book a meeting room" or "book a
        # tutoring session" don't necessarily need payment. If a goal
        # legitimately needs payment, the planner should say so
        # explicitly and the catalog will surface payment_authorization
        # via the candidate's own alias bag.
        self.assertNotIn("payment_authorization", plan.missing_capabilities)

    def test_refuses_empty_goal(self) -> None:
        plan = plan_goal("")

        self.assertFalse(plan.executable)
        self.assertIn("No goal was provided", plan.summary)

    def test_refuses_email_goal_when_intent_is_unclear(self) -> None:
        plan = plan_goal("Send a message to jane@acme.com")

        self.assertFalse(plan.executable)
        self.assertEqual(plan.missing_capabilities, [])


def _catalog(items):
    return build_capability_catalog(
        [
            normalize_candidate(
                {
                    "id": candidate_id,
                    "display_name": candidate_id.replace("-", " ").title(),
                    "vendor": "Test Directory",
                    "vendor_url": "https://example.com",
                    "provider_type": "a2a_agent",
                    "capabilities": capabilities,
                },
                source="test",
            )
            for candidate_id, capabilities in items
        ]
    )


if __name__ == "__main__":
    unittest.main()
