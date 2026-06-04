"""Unit tests for the Open MCP Opportunities report builder."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.apis_without_agents_store import ApiWithoutAgentRecord
from planmyagents_api.discovery.demand_store import DemandSummary
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.discovery.open_mcp_opportunities import compute_open_mcp_opportunities


def _api(provider_id: str, capability: str, vendor: str = "vendor") -> ApiWithoutAgentRecord:
    return ApiWithoutAgentRecord(
        dedupe_key=provider_id,
        provider_id=provider_id,
        display_name=provider_id.replace("-", " ").title(),
        vendor=vendor,
        vendor_url=f"https://{vendor}.example",
        provider_type="api_provider",
        openapi_url=f"https://{vendor}.example/openapi.json",
        capabilities=(capability,),
        source_id="apis_guru",
        first_seen_at="2026-05-01",
        last_seen_at="2026-05-01",
    )


def _agent(provider_id: str, capability: str) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=provider_id,
        display_name=provider_id,
        vendor="acme",
        vendor_url="https://acme.example",
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id=capability, confidence=0.9)],
        verification_status="capability_verified",
        evidence_url="https://acme.example/agent.json",
        source="curated",
    )


class OpenMcpOpportunitiesTests(unittest.TestCase):
    def test_capability_with_apis_and_no_agent_is_an_opportunity(self) -> None:
        report = compute_open_mcp_opportunities(
            agentic_candidates=[],
            api_records=[
                _api("stripe-payments", "payment_authorization", "stripe"),
                _api("plaid-payments", "payment_authorization", "plaid"),
            ],
            demand_summaries=[],
        )
        self.assertEqual(len(report.capabilities), 1)
        self.assertEqual(report.total_apis_without_agents, 2)
        capability = report.capabilities[0]
        self.assertEqual(capability.capability_id, "payment_authorization")
        self.assertEqual(capability.api_supply_count, 2)
        self.assertEqual(capability.demand_request_count, 0)
        provider_ids = sorted(api.provider_id for api in capability.apis)
        self.assertEqual(provider_ids, ["plaid-payments", "stripe-payments"])

    def test_capability_with_existing_agent_is_filtered_out(self) -> None:
        report = compute_open_mcp_opportunities(
            agentic_candidates=[_agent("alpha-mcp", "payment_authorization")],
            api_records=[_api("stripe-payments", "payment_authorization", "stripe")],
            demand_summaries=[],
        )
        capability_ids = [c.capability_id for c in report.capabilities]
        self.assertNotIn("payment_authorization", capability_ids)
        self.assertEqual(report.total_opportunities, 0)

    def test_superseded_api_is_filtered_out(self) -> None:
        record = ApiWithoutAgentRecord(
            dedupe_key="stripe-payments",
            provider_id="stripe-payments",
            display_name="Stripe Payments",
            vendor="stripe",
            vendor_url="https://stripe.example",
            provider_type="api_provider",
            openapi_url="https://stripe.example/openapi.json",
            capabilities=("payment_authorization",),
            source_id="apis_guru",
            first_seen_at="2026-05-01",
            last_seen_at="2026-05-01",
            superseded_by_provider_id="acme-stripe-mcp",
        )
        report = compute_open_mcp_opportunities(
            agentic_candidates=[],
            api_records=[record],
            demand_summaries=[],
        )
        self.assertEqual(report.total_opportunities, 0)
        self.assertEqual(report.capabilities, [])

    def test_capability_with_demand_only_is_included_with_zero_supply(self) -> None:
        report = compute_open_mcp_opportunities(
            agentic_candidates=[],
            api_records=[],
            demand_summaries=[
                DemandSummary(
                    capability_id="real_estate_dubai",
                    request_count=5,
                    distinct_requester_count=4,
                    last_requested_at="2026-05-01T00:00:00+00:00",
                    sample_goals=["Buy a house in Dubai with USDC"],
                )
            ],
        )
        self.assertEqual(len(report.capabilities), 1)
        capability = report.capabilities[0]
        self.assertEqual(capability.capability_id, "real_estate_dubai")
        self.assertEqual(capability.api_supply_count, 0)
        self.assertEqual(capability.demand_request_count, 5)
        self.assertEqual(capability.apis, [])
        self.assertEqual(capability.sample_demand_goals, ["Buy a house in Dubai with USDC"])

    def test_ranking_combines_demand_and_supply(self) -> None:
        report = compute_open_mcp_opportunities(
            agentic_candidates=[],
            api_records=[
                _api("api-a-1", "alpha", "a1"),
                _api("api-a-2", "alpha", "a2"),
                _api("api-b-1", "beta", "b1"),
            ],
            demand_summaries=[
                DemandSummary(
                    capability_id="beta",
                    request_count=10,
                    distinct_requester_count=8,
                    last_requested_at="2026-05-01T00:00:00+00:00",
                    sample_goals=["beta example"],
                ),
                DemandSummary(
                    capability_id="alpha",
                    request_count=1,
                    distinct_requester_count=1,
                    last_requested_at="2026-05-01T00:00:00+00:00",
                    sample_goals=["alpha example"],
                ),
            ],
        )
        ordered_ids = [c.capability_id for c in report.capabilities]
        self.assertEqual(ordered_ids, ["beta", "alpha"])
        self.assertEqual(report.capabilities[0].score, 11.0)  # 10 demand + 1 supply
        self.assertEqual(report.capabilities[1].score, 3.0)   # 1 demand + 2 supply

    def test_empty_inputs_produce_empty_report(self) -> None:
        report = compute_open_mcp_opportunities(
            agentic_candidates=[], api_records=[], demand_summaries=[]
        )
        self.assertEqual(report.total_opportunities, 0)
        self.assertEqual(report.total_apis_without_agents, 0)
        self.assertEqual(report.capabilities, [])

    def test_methodology_text_is_set(self) -> None:
        report = compute_open_mcp_opportunities(
            agentic_candidates=[], api_records=[], demand_summaries=[]
        )
        self.assertIn("at least one vendor", report.methodology)

    def test_methodology_describes_the_or_inclusion_rule(self) -> None:
        """Methodology copy must match the code's actual filter.

        Regression for the 2026-05-19 audit finding: the methodology
        text claimed `(a) at least one OpenAPI spec AND (b) zero
        agents`, but `compute_open_mcp_opportunities` actually
        includes capabilities with demand-only (no OpenAPI spec) as
        the "deepest gap" path. Top-3 rows on a live install were all
        api_supply_count=0 demand rows, directly contradicting the
        stated AND-rule — exactly the gap a vendor would call out
        on the methodology pop-out.

        This test pins three substrings the corrected copy carries
        so a future edit can't silently revert to the misleading
        AND-formulation:
        """

        report = compute_open_mcp_opportunities(
            agentic_candidates=[], api_records=[], demand_summaries=[]
        )
        methodology = report.methodology
        # OR semantic must be explicit.
        self.assertIn("OR", methodology)
        # The demand-only branch (the surprise that drove the audit)
        # must be named.
        self.assertIn("demand", methodology.lower())
        # The "no API spec → still surfaced" branch must be named —
        # this is what makes (b) load-bearing copy.
        self.assertIn("api_supply_count=0", methodology)

    def test_apis_capped_per_capability(self) -> None:
        many_apis = [_api(f"api-{i}", "search", f"v{i}") for i in range(20)]
        report = compute_open_mcp_opportunities(
            agentic_candidates=[],
            api_records=many_apis,
            demand_summaries=[],
            max_apis_per_capability=5,
        )
        self.assertEqual(len(report.capabilities), 1)
        self.assertEqual(report.capabilities[0].api_supply_count, 20)
        self.assertEqual(len(report.capabilities[0].apis), 5)

    def test_to_json_shape(self) -> None:
        report = compute_open_mcp_opportunities(
            agentic_candidates=[],
            api_records=[_api("stripe-payments", "payment_authorization", "stripe")],
            demand_summaries=[],
        )
        payload = report.to_json()
        self.assertIn("capabilities", payload)
        self.assertIn("methodology", payload)
        self.assertEqual(payload["total_opportunities"], 1)
        self.assertIn("capability_id", payload["capabilities"][0])
        self.assertIn("apis", payload["capabilities"][0])
        api_payload = payload["capabilities"][0]["apis"][0]
        self.assertEqual(
            sorted(api_payload.keys()),
            ["capabilities", "display_name", "openapi_url", "provider_id", "source", "vendor"],
        )


if __name__ == "__main__":
    unittest.main()
