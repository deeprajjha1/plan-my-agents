from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.planner.goal import GoalPlan
from planmyagents_api.registry.discovery import enrich_registry_from_unsupported_plan
from planmyagents_api.registry.loader import load_registry

# Live network discovery sources are wired default-on in service.py
# (registry.modelcontextprotocol.io and api.apis.guru). Disable them here
# so this suite tests the deterministic curated-catalog behavior without
# letting hundreds of network-sourced candidates push the curated ones
# (e.g., `stripe-payments`) out of the assertion windows.
_DISABLED_LIVE_NETWORK_SOURCES = {
    "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY": "false",
    "PLANMYAGENTS_DISCOVERY_APIS_GURU": "false",
    # The three event-driven sources also hit live endpoints (HN Firebase,
    # GitHub search, vendor RSS feeds). Same reasoning: keep this suite
    # deterministic and offline.
    "PLANMYAGENTS_DISCOVERY_HACKER_NEWS": "false",
    "PLANMYAGENTS_DISCOVERY_VENDOR_RSS": "false",
    "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED": "false",
}


class ProviderDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_env: dict[str, str | None] = {}
        for key, value in _DISABLED_LIVE_NETWORK_SOURCES.items():
            self._previous_env[key] = os.environ.get(key)
            os.environ[key] = value

    def tearDown(self) -> None:
        for key, previous in self._previous_env.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous

    def test_enriches_registry_with_non_routable_candidates(self) -> None:
        registry_path = self._temp_registry()
        plan = GoalPlan(
            status="unsupported",
            summary="Travel booking is unavailable.",
            missing_capabilities=["travel_search", "fare_comparison", "payment_authorization"],
        )

        discovery = enrich_registry_from_unsupported_plan(
            goal="Book a flight from Bengaluru to San Francisco",
            plan=plan,
            registry_path=registry_path,
            persist_registry=True,
        )

        self.assertIsNotNone(discovery)
        self.assertTrue(discovery["will_fail"])
        self.assertIn("duffel", discovery["added_candidates"])
        self.assertIn("amadeus", discovery["added_candidates"])
        # As of Sprint 3a-4 ``stripe-payments`` was promoted from
        # discovered_agents to the routable agents array (with an
        # executable adapter and a benchmark gate), so the static
        # discovery source's stripe entry must be a no-op for the
        # promoted id — adding it again would shadow the promoted
        # entry. The enricher correctly skips already-known agents.
        self.assertNotIn("stripe-payments", discovery["added_candidates"])

        enriched = load_registry(registry_path)
        # Sanity: the promoted agent must already be in the routable
        # ``agents`` array (this is the contract that the static
        # source relies on for its dedup check).
        agent_ids = {agent["id"] for agent in enriched["agents"]}
        self.assertIn("stripe-payments", agent_ids)
        candidates = {agent["id"]: agent for agent in enriched["discovered_agents"]}
        self.assertTrue(candidates["duffel"]["will_fail"])
        self.assertEqual("api_provider", candidates["duffel"]["provider_type"])
        self.assertGreater(candidates["duffel"]["discovery_priority"], 1)
        self.assertIn("DUFFEL_API_TOKEN", candidates["duffel"]["required_env_vars"])
        self.assertEqual("not_started", candidates["duffel"]["benchmark_status"])
        self.assertIn(
            "API key is required before execution.", candidates["duffel"]["will_fail_reasons"]
        )

    def test_discovery_is_idempotent_for_repeated_failures(self) -> None:
        registry_path = self._temp_registry()
        plan = GoalPlan(
            status="unsupported",
            summary="Travel booking is unavailable.",
            missing_capabilities=["travel_search"],
        )

        enrich_registry_from_unsupported_plan(
            goal="Book a flight", plan=plan, registry_path=registry_path, persist_registry=True
        )
        enrich_registry_from_unsupported_plan(
            goal="Book another flight",
            plan=plan,
            registry_path=registry_path,
            persist_registry=True,
        )

        enriched = load_registry(registry_path)
        candidate_ids = [agent["id"] for agent in enriched["discovered_agents"]]
        self.assertEqual(len(candidate_ids), len(set(candidate_ids)))

    def test_can_search_without_mutating_registry(self) -> None:
        registry_path = self._temp_registry()
        before = registry_path.read_text()
        plan = GoalPlan(
            status="unsupported",
            summary="Travel booking is unavailable.",
            missing_capabilities=["travel_search", "fare_comparison"],
        )

        discovery = enrich_registry_from_unsupported_plan(
            goal="Book a flight",
            plan=plan,
            registry_path=registry_path,
            persist_registry=False,
        )

        self.assertIsNotNone(discovery)
        self.assertFalse(discovery["persisted_to_registry"])
        self.assertEqual(before, registry_path.read_text())

    def test_ignores_internal_runtime_capabilities(self) -> None:
        registry_path = self._temp_registry()
        plan = GoalPlan(
            status="unsupported",
            summary="Dependency-aware execution is unavailable.",
            missing_capabilities=["dependency_resolution"],
        )

        discovery = enrich_registry_from_unsupported_plan(
            goal="Find companies then enrich contacts",
            plan=plan,
            registry_path=registry_path,
        )

        self.assertIsNone(discovery)

    def _temp_registry(self) -> Path:
        base = load_registry(ROOT / "packages" / "registry" / "agents.json")
        base.pop("discovered_agents", None)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        registry_path = Path(directory.name) / "agents.json"
        registry_path.write_text(json.dumps(base, indent=2) + "\n")
        return registry_path


if __name__ == "__main__":
    unittest.main()
