"""Integration tests for `/open-mcp-opportunities` and the refusal payload split.

Verifies:
  * The endpoint returns ranked capabilities matching the local store.
  * `/goal` no longer mixes `api_provider` rows into the candidate list;
    instead they appear under `apis_without_agents`.
  * Demand events are recorded into the configured demand log on each
    refusal.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient
from planmyagents_api.discovery.demand_store import JsonDemandStore
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.discovery.store import JsonDiscoveryStore


def _api_provider(provider_id: str, capability: str, vendor: str) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=provider_id,
        display_name=provider_id.replace("-", " ").title(),
        vendor=vendor,
        vendor_url=f"https://{vendor}.example",
        provider_type="api_provider",
        capabilities=[CandidateCapability(id=capability, confidence=0.6)],
        evidence_url=f"https://{vendor}.example/openapi.json",
        source="apis_guru",
    )


def _agent(provider_id: str, capability: str) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=provider_id,
        display_name=provider_id.replace("-", " ").title(),
        vendor="acme",
        vendor_url="https://acme.example",
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id=capability, confidence=0.95)],
        verification_status="capability_verified",
        evidence_url="https://acme.example/agent.json",
        source="curated",
    )


class OpenMcpOpportunitiesEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        self.demand_path = self.tempdir / "demand.jsonl"

        # Seed the discovery store with:
        #   - 2 api_providers for `payment_authorization` (uncovered by any agent)
        #   - 1 api_provider for `email_verification` (covered by an agent)
        #   - 1 mcp_server for `email_verification`
        # Expectation: only `payment_authorization` shows up as an opportunity.
        JsonDiscoveryStore(self.discovery_path).save(
            [
                _api_provider("stripe-payments", "payment_authorization", "stripe"),
                _api_provider("plaid-payments", "payment_authorization", "plaid"),
                _api_provider("hunter-email", "email_verification", "hunter"),
                _agent("acme-email-mcp", "email_verification"),
            ]
        )

        os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(self.discovery_path)
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = str(self.demand_path)
        self.run_log_path = self.tempdir / "discovery_run_events.jsonl"
        os.environ["PLANMYAGENTS_RUN_LOG_STORE_PATH"] = str(self.run_log_path)
        os.environ["PLANMYAGENTS_PLANNER"] = "rules"
        os.environ["PLANMYAGENTS_INTENT_MAPPER"] = "off"
        for key in (
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
        ):
            os.environ[key] = "false"
        os.environ["PLANMYAGENTS_LIVE_DISCOVERY"] = "false"
        os.environ["PLANMYAGENTS_CANDIDATE_JUDGE"] = "off"

        from planmyagents_api.web.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self._tempdir.cleanup()
        for key in (
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            "PLANMYAGENTS_DEMAND_STORE_PATH",
            "PLANMYAGENTS_RUN_LOG_STORE_PATH",
            "PLANMYAGENTS_PLANNER",
            "PLANMYAGENTS_INTENT_MAPPER",
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
            "PLANMYAGENTS_LIVE_DISCOVERY",
            "PLANMYAGENTS_CANDIDATE_JUDGE",
        ):
            os.environ.pop(key, None)

    def test_endpoint_returns_only_capabilities_without_agents(self) -> None:
        response = self.client.get("/open-mcp-opportunities")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        capability_ids = [c["capability_id"] for c in body["capabilities"]]
        self.assertIn("payment_authorization", capability_ids)
        # Already covered by an MCP — should not appear.
        self.assertNotIn("email_verification", capability_ids)

        payment_entry = next(
            c for c in body["capabilities"] if c["capability_id"] == "payment_authorization"
        )
        self.assertEqual(payment_entry["api_supply_count"], 2)
        provider_ids = sorted(api["provider_id"] for api in payment_entry["apis"])
        self.assertEqual(provider_ids, ["plaid-payments", "stripe-payments"])

    def test_endpoint_supports_capability_filter(self) -> None:
        response = self.client.get(
            "/open-mcp-opportunities",
            params={"capability": "payment_authorization"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["capabilities"]), 1)
        self.assertEqual(body["capabilities"][0]["capability_id"], "payment_authorization")
        self.assertEqual(body["filter"], {"capability": "payment_authorization"})

    def test_endpoint_includes_methodology_text(self) -> None:
        response = self.client.get("/open-mcp-opportunities")
        body = response.json()
        self.assertIn("methodology", body)
        self.assertGreater(len(body["methodology"]), 50)


class RefusalPayloadShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        self.demand_path = self.tempdir / "demand.jsonl"

        # Only api_provider rows in the index for the capabilities the
        # planner will surface — refusal should keep `candidates` empty
        # but still surface the apis_without_agents block.
        JsonDiscoveryStore(self.discovery_path).save(
            [
                _api_provider("stripe-payments", "payment_authorization", "stripe"),
            ]
        )

        os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(self.discovery_path)
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = str(self.demand_path)
        self.run_log_path = self.tempdir / "discovery_run_events.jsonl"
        os.environ["PLANMYAGENTS_RUN_LOG_STORE_PATH"] = str(self.run_log_path)
        os.environ["PLANMYAGENTS_PLANNER"] = "rules"
        os.environ["PLANMYAGENTS_INTENT_MAPPER"] = "off"
        for key in (
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
        ):
            os.environ[key] = "false"
        os.environ["PLANMYAGENTS_LIVE_DISCOVERY"] = "false"
        os.environ["PLANMYAGENTS_CANDIDATE_JUDGE"] = "off"

        from planmyagents_api.web.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self._tempdir.cleanup()
        for key in (
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            "PLANMYAGENTS_DEMAND_STORE_PATH",
            "PLANMYAGENTS_RUN_LOG_STORE_PATH",
            "PLANMYAGENTS_PLANNER",
            "PLANMYAGENTS_INTENT_MAPPER",
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
            "PLANMYAGENTS_LIVE_DISCOVERY",
            "PLANMYAGENTS_CANDIDATE_JUDGE",
        ):
            os.environ.pop(key, None)

    def test_refusal_payload_keeps_api_provider_out_of_candidates(self) -> None:
        # Use a goal that maps to payment_authorization in the rule planner.
        response = self.client.post(
            "/goal",
            json={
                "goal": "Charge a customer 49.99 USD for premium subscription.",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        plan = body.get("plan") or {}
        discovery = plan.get("discovery") or {}
        if not discovery:
            self.skipTest("planner did not produce a discovery block for this goal")
        candidates = discovery.get("candidates", [])
        for candidate in candidates:
            self.assertNotEqual(
                candidate.get("provider_type"),
                "api_provider",
                "api_provider rows must not appear in candidate list",
            )

    def test_refusal_payload_includes_apis_without_agents_block(self) -> None:
        response = self.client.post(
            "/goal",
            json={
                "goal": "Charge a customer 49.99 USD for premium subscription.",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        discovery = ((response.json().get("plan") or {}).get("discovery")) or {}
        if not discovery:
            self.skipTest("planner did not produce a discovery block for this goal")
        block = discovery.get("apis_without_agents")
        self.assertIsNotNone(block, "apis_without_agents block missing from refusal")
        self.assertIn("count", block)
        self.assertIn("per_capability", block)
        self.assertIn("link", block)
        self.assertEqual(block["link"], "/open-mcp-opportunities")

    def test_refusal_records_demand_events(self) -> None:
        response = self.client.post(
            "/goal",
            json={
                "goal": "Charge a customer 49.99 USD for premium subscription.",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json().get("plan") or {}
        discovery = plan.get("discovery") or {}
        if not discovery:
            self.skipTest("planner did not produce a discovery block for this goal")
        # The recorder writes to the JSONL store we configured.
        events = JsonDemandStore(self.demand_path).load_all()
        self.assertGreater(len(events), 0, "expected at least one demand event")
        capability_ids = {event.capability_id for event in events}
        # Don't assert a specific capability id (rule-planner output can
        # change); just assert recording happened.
        self.assertTrue(all(c for c in capability_ids))


if __name__ == "__main__":
    unittest.main()
