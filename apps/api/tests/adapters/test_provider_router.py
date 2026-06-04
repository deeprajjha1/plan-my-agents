"""Provider router tests.

After the 16-May-2026 pivot, hand-written vendor adapters are
benchmark baselines and never user-routable. These tests therefore
exercise the routing surface through (a) the new
``adapter_factories=`` injection point and (b) synthetic
non-baseline fixtures rather than through ``hunter``-style real
wrappers. The firewall assertions (a benchmark baseline is never
routable, never returned by ``provider_for``) live near the bottom.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.router import (  # noqa: E402
    ADAPTER_FACTORIES,
    ProviderRouter,
)
from planmyagents_api.benchmark.models import (  # noqa: E402
    ProviderRequest,
    ProviderResponse,
)

# ---------------------------------------------------------------------------
# Synthetic non-baseline fixture used by every routing test below.
# ---------------------------------------------------------------------------


class _StubProductionAdapter:
    """Synthetic non-baseline adapter — stands in for a generic-protocol
    or mock adapter that the registry might point to."""

    provider_id = "stub-prod"
    capabilities = ["email_verification"]

    async def estimate_cost(self, request: ProviderRequest) -> float:
        return 0.0

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            succeeded=True, output={}, cost_usd=0.0, latency_ms=1, raw_response={}
        )

    async def health_check(self) -> bool:
        return True


def _stub_factory() -> _StubProductionAdapter:
    return _StubProductionAdapter()


def _stub_agent(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "stub-prod",
        "display_name": "Stub Production Provider",
        "vendor": "Stub Vendor",
        "vendor_url": "https://stub.example",
        "runtime_mode": "production",
        "capabilities": [
            {
                "id": "email_verification",
                "tier": "primary",
                "endpoint": "POST /v1/verify",
                "unit_cost_usd": 0.001,
                "pricing_model": "per_request",
            }
        ],
        "api_base_url": "https://stub.example/v1",
        "auth": {"type": "header", "env_var": "STUB_PROD_API_KEY"},
        "is_active": True,
    }
    base.update(overrides)
    return base


def _write_registry(directory: Path, *agents: dict[str, Any]) -> Path:
    path = directory / "agents.json"
    path.write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "updated_at": "2026-05-16",
                "capabilities": ["email_verification"],
                "agents": list(agents),
            }
        )
    )
    return path


# ---------------------------------------------------------------------------
# Routing surface — exercised through the synthetic non-baseline agent.
# ---------------------------------------------------------------------------


class ProviderRouterTest(unittest.TestCase):
    def test_routes_from_registry_when_executable_provider_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = _write_registry(Path(directory), _stub_agent())
            with patch.dict(
                os.environ, {"STUB_PROD_API_KEY": "test-key"}, clear=True
            ):
                router = ProviderRouter(
                    registry_path=registry_path,
                    adapter_factories={"stub-prod": _stub_factory},
                )
                decision = router.route("email_verification")

        self.assertTrue(decision.routable)
        self.assertEqual(decision.provider_id, "stub-prod")
        self.assertGreaterEqual(len(decision.candidates), 1)
        self.assertTrue(
            any(
                candidate["provider_id"] == "stub-prod" and candidate["configured"]
                for candidate in decision.candidates
            )
        )

    def test_refuses_when_registry_has_capability_but_no_provider_is_configured(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = _write_registry(Path(directory), _stub_agent())
            with patch.dict(os.environ, {}, clear=True):
                decision = ProviderRouter(registry_path=registry_path).route(
                    "email_verification"
                )

        self.assertFalse(decision.routable)
        self.assertIsNone(decision.provider_id)
        self.assertIn("none are configured", decision.reason)
        self.assertTrue(
            any(
                candidate["required_env_var"] == "STUB_PROD_API_KEY"
                for candidate in decision.candidates
            )
        )

    def test_refuses_configured_provider_without_executable_adapter(self) -> None:
        # Real-registry test: clearbit is configured by env but has no
        # adapter_module wired up, so router refuses it as not executable.
        with patch.dict(os.environ, {"CLEARBIT_API_KEY": "test-key"}, clear=True):
            decision = ProviderRouter().route("company_data_lookup")

        self.assertFalse(decision.routable)
        self.assertIsNone(decision.provider_id)
        self.assertIn("no executable adapter", decision.reason)
        self.assertTrue(
            any(
                candidate["provider_id"] == "clearbit"
                and candidate["configured"]
                and not candidate["has_executable_adapter"]
                for candidate in decision.candidates
            )
        )

    def test_blocks_dev_only_provider_in_production_routing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = _write_registry(
                Path(directory), _stub_agent(runtime_mode="fixture")
            )
            with patch.dict(
                os.environ, {"STUB_PROD_API_KEY": "test-key"}, clear=True
            ):
                router = ProviderRouter(
                    registry_path=registry_path,
                    adapter_factories={"stub-prod": _stub_factory},
                )
                decision = router.route("email_verification")

        self.assertFalse(decision.routable)
        self.assertIn("Dev/test/local providers are disabled", decision.reason)
        self.assertTrue(decision.candidates[0]["dev_provider_blocked"])

    def test_allows_dev_only_provider_when_explicitly_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = _write_registry(
                Path(directory), _stub_agent(runtime_mode="fixture")
            )
            with patch.dict(
                os.environ,
                {
                    "STUB_PROD_API_KEY": "test-key",
                    "PLANMYAGENTS_ALLOW_DEV_PROVIDERS": "true",
                },
                clear=True,
            ):
                router = ProviderRouter(
                    registry_path=registry_path,
                    adapter_factories={"stub-prod": _stub_factory},
                )
                decision = router.route("email_verification")

        self.assertTrue(decision.routable)
        self.assertEqual("stub-prod", decision.provider_id)

    def test_refuses_discovered_capability_as_will_fail(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            decision = ProviderRouter().route("travel_search")

        self.assertFalse(decision.routable)
        self.assertGreaterEqual(len(decision.candidates), 1)
        self.assertIn("discovered provider candidate", decision.reason)
        self.assertTrue(all(candidate["will_fail"] for candidate in decision.candidates))

    def test_refuses_unknown_capability_without_candidates(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            decision = ProviderRouter().route("mars_colony_construction")

        self.assertFalse(decision.routable)
        self.assertEqual(decision.candidates, [])
        self.assertIn("No provider in the registry", decision.reason)

    def test_provider_for_enforces_configuration_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = _write_registry(Path(directory), _stub_agent())
            with patch.dict(os.environ, {}, clear=True):
                router = ProviderRouter(
                    registry_path=registry_path,
                    adapter_factories={"stub-prod": _stub_factory},
                )
                with self.assertRaises(PermissionError) as exc:
                    router.provider_for("stub-prod")

        self.assertIn("missing configuration", str(exc.exception))

    def test_provider_for_enforces_dev_provider_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = _write_registry(
                Path(directory), _stub_agent(runtime_mode="fixture")
            )
            with patch.dict(
                os.environ, {"STUB_PROD_API_KEY": "test-key"}, clear=True
            ):
                router = ProviderRouter(
                    registry_path=registry_path,
                    adapter_factories={"stub-prod": _stub_factory},
                )
                with self.assertRaises(PermissionError) as exc:
                    router.provider_for("stub-prod")

        self.assertIn("disabled for production", str(exc.exception))


# ---------------------------------------------------------------------------
# Firewall: benchmark baselines never appear in routing decisions.
# ---------------------------------------------------------------------------


class BenchmarkBaselineFirewallTest(unittest.TestCase):
    """Hand-written benchmark baselines are NEVER reachable through routing.

    These tests are the most important in this file because they are
    the test-level expression of architectural principle P11 in
    ``docs/ARCHITECTURE.md``. If any of them fail, the firewall has
    been weakened — escalate to the founder before merging.
    """

    def test_global_adapter_factories_dict_is_empty(self) -> None:
        # ADAPTER_FACTORIES must remain empty in production; tests
        # inject through the constructor kwarg.
        self.assertEqual(
            ADAPTER_FACTORIES,
            {},
            "ADAPTER_FACTORIES must stay empty — baselines belong in benchmark/baselines/",
        )

    def test_baseline_provider_is_never_routable_even_when_configured(self) -> None:
        # Hunter is marked is_benchmark_baseline=true in the real
        # agents.json. Setting HUNTER_API_KEY must NOT make it routable.
        with patch.dict(os.environ, {"HUNTER_API_KEY": "test-key"}, clear=True):
            decision = ProviderRouter().route("email_verification")

        self.assertFalse(
            decision.routable,
            "Baseline 'hunter' must never be routable from /goal, "
            "even when configured — firewall violation.",
        )
        baseline_in_candidates = [
            candidate
            for candidate in decision.candidates
            if candidate["provider_id"] == "hunter"
        ]
        self.assertEqual(
            baseline_in_candidates,
            [],
            "Baseline 'hunter' must not appear in candidates at all "
            "— it is filtered out before summaries are built.",
        )

    def test_provider_for_refuses_benchmark_baseline(self) -> None:
        with patch.dict(os.environ, {"HUNTER_API_KEY": "test-key"}, clear=True):
            router = ProviderRouter()
            with self.assertRaises(PermissionError) as exc:
                router.provider_for("hunter")

        self.assertIn("benchmark-only baseline", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
