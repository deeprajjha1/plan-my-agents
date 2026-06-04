from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.router import ProviderRouter
from planmyagents_api.benchmark.models import ProviderRequest
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import normalize_candidate
from planmyagents_api.discovery.store import JsonDiscoveryStore
from planmyagents_api.registry.promotions import is_promotion_ready, merge_promoted_candidates


class RegistryPromotionsTest(unittest.TestCase):
    def test_merges_only_promotion_ready_candidates(self) -> None:
        base = _base_registry()
        promoted = _candidate("db-currency-agent", promotion_ready=True)
        raw = _candidate("raw-currency-agent", promotion_ready=False)

        merged = merge_promoted_candidates(base, [raw, promoted])

        provider_ids = {agent["id"] for agent in merged["agents"]}
        self.assertIn("db-currency-agent", provider_ids)
        self.assertNotIn("raw-currency-agent", provider_ids)
        self.assertIn("currency_conversion", merged["capabilities"])

    def test_generic_protocol_candidate_is_blocked_in_production_routing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "agents.json"
            registry_path.write_text(json.dumps(_base_registry(), indent=2) + "\n")
            store_path = Path(directory) / "discovery-store.json"
            JsonDiscoveryStore(store_path).save([_candidate("db-currency-agent", promotion_ready=True)])

            with patch.dict(
                os.environ,
                {
                    "PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB": "true",
                    "PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL": str(store_path),
                },
                clear=True,
            ):
                decision = ProviderRouter(registry_path=registry_path).route("currency_conversion")

        self.assertFalse(decision.routable)
        self.assertIn("Dev/test/local providers are disabled", decision.reason)
        self.assertTrue(decision.candidates[0]["dev_provider_blocked"])

    def test_generic_protocol_candidate_can_be_loaded_only_in_dev_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "agents.json"
            registry_path.write_text(json.dumps(_base_registry(), indent=2) + "\n")
            store_path = Path(directory) / "discovery-store.json"
            JsonDiscoveryStore(store_path).save([_candidate("db-currency-agent", promotion_ready=True)])

            with patch.dict(
                os.environ,
                {
                    "PLANMYAGENTS_ALLOW_DEV_PROVIDERS": "true",
                    "PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB": "true",
                    "PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL": str(store_path),
                },
                clear=True,
            ):
                router = ProviderRouter(registry_path=registry_path)
                decision = router.route("currency_conversion")
                provider = router.provider_for(str(decision.provider_id))

        self.assertTrue(decision.routable)
        self.assertEqual("db-currency-agent", decision.provider_id)
        self.assertEqual("db-currency-agent", provider.provider_id)

    def test_generic_protocol_adapter_execution_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "agents.json"
            registry_path.write_text(json.dumps(_base_registry(), indent=2) + "\n")
            store_path = Path(directory) / "discovery-store.json"
            JsonDiscoveryStore(store_path).save([_candidate("db-currency-agent", promotion_ready=True)])

            with patch.dict(
                os.environ,
                {
                    "PLANMYAGENTS_ALLOW_DEV_PROVIDERS": "true",
                    "PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB": "true",
                    "PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL": str(store_path),
                },
                clear=True,
            ):
                router = ProviderRouter(registry_path=registry_path)
                provider = router.provider_for("db-currency-agent")
                response = asyncio.run(
                    provider.execute(
                        ProviderRequest(
                            capability="currency_conversion",
                            inputs={"from": "USD", "to": "INR", "amount": 1},
                            idempotency_key="test",
                        )
                    )
                )

        self.assertFalse(response.succeeded)
        self.assertIn("Generic protocol adapter execution is disabled", str(response.error))

    def test_generic_a2a_adapter_returns_structured_refusal_in_sandbox(self) -> None:
        """Gap 4: with execution enabled the GenericA2AAdapter no longer
        returns a synthesised "metadata_only" success. A2A invocation
        isn't implemented in the generic adapter (the spec is still in
        active drafting), so the adapter returns a structured refusal
        explaining what's missing — never a fake-success that the
        workflow executor would happily charge against the cost cap.
        Hand-written A2A adapters per vendor remain the supported path.
        """

        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "agents.json"
            registry_path.write_text(json.dumps(_base_registry(), indent=2) + "\n")
            store_path = Path(directory) / "discovery-store.json"
            JsonDiscoveryStore(store_path).save([_candidate("db-currency-agent", promotion_ready=True)])

            with patch.dict(
                os.environ,
                {
                    "PLANMYAGENTS_ALLOW_DEV_PROVIDERS": "true",
                    "PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION": "true",
                    "PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB": "true",
                    "PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL": str(store_path),
                },
                clear=True,
            ):
                router = ProviderRouter(registry_path=registry_path)
                provider = router.provider_for("db-currency-agent")
                response = asyncio.run(
                    provider.execute(
                        ProviderRequest(
                            capability="currency_conversion",
                            inputs={"from": "USD", "to": "INR", "amount": 1},
                            idempotency_key="test",
                        )
                    )
                )

        self.assertFalse(response.succeeded)
        self.assertIn("A2A skill invocation is not implemented", str(response.error))
        # Refusal envelope carries the structured tag set the UI / cost
        # ledger reads to distinguish honest refusals from real errors.
        self.assertTrue(response.raw_response.get("refused"))
        self.assertEqual("a2a", response.raw_response.get("protocol"))
        self.assertEqual(0.0, response.cost_usd)

    def test_raw_db_candidates_are_not_loaded_as_routable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "agents.json"
            registry_path.write_text(json.dumps(_base_registry(), indent=2) + "\n")
            store_path = Path(directory) / "discovery-store.json"
            JsonDiscoveryStore(store_path).save([_candidate("raw-currency-agent", promotion_ready=False)])

            with patch.dict(
                os.environ,
                {
                    "PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB": "true",
                    "PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL": str(store_path),
                },
                clear=True,
            ):
                decision = ProviderRouter(registry_path=registry_path).route("currency_conversion")

        self.assertFalse(decision.routable)
        self.assertIn("No provider in the registry", decision.reason)


def _candidate(candidate_id: str, *, promotion_ready: bool) -> DiscoveryCandidate:
    candidate = normalize_candidate(
        {
            "id": candidate_id,
            "display_name": candidate_id,
            "vendor": "Currency Agent",
            "vendor_url": "https://currency.example",
            "provider_type": "a2a_agent",
            "verification_status": "capability_verified" if promotion_ready else "unverified",
            "evidence_url": "https://currency.example/agent-card.json",
            "capabilities": [{"id": "currency_conversion", "confidence": 0.95}],
        },
        source="test",
    )
    if not promotion_ready:
        return candidate
    promoted = DiscoveryCandidate(
        **{
            **candidate.__dict__,
            "lifecycle_status": "configured",
            "route_status": "ready_for_promotion",
            "will_fail": False,
            "will_fail_reasons": [],
            "adapter_module": "",
            "benchmark_status": "passed",
        }
    )
    self_check = is_promotion_ready(promoted)
    if not self_check:
        raise AssertionError("test fixture should be promotion-ready")
    return promoted


def _base_registry() -> dict:
    return {
        "version": "0.1.0",
        "updated_at": "2026-05-06",
        "capabilities": ["email_verification"],
        "agents": [
            {
                "id": "hunter",
                "display_name": "Hunter",
                "vendor": "Hunter.io",
                "vendor_url": "https://hunter.io",
                "capabilities": [
                    {
                        "id": "email_verification",
                        "tier": "primary",
                        "endpoint": "GET /v2/email-verifier",
                        "unit_cost_usd": 0.005,
                        "pricing_model": "per_request",
                    }
                ],
                "api_base_url": "https://api.hunter.io/v2",
                "auth": {"type": "query_param", "env_var": "HUNTER_API_KEY"},
                "is_active": True,
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
