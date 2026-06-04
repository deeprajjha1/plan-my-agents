from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.protocol import GenericMcpAdapter
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.eval.protocols import (
    ProtocolInvokerRegistry,
    ProtocolMaturity,
    default_registry,
)


def _candidate(provider_type: str, *, openapi_url: str = "") -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=f"{provider_type}-1",
        display_name="Cand",
        vendor="Vendor",
        vendor_url="https://example.com",
        provider_type=provider_type,
        capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
        openapi_url=openapi_url,
    )


class ProtocolRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = default_registry()

    def test_mcp_is_executable(self) -> None:
        self.assertEqual(self.registry.maturity("mcp"), ProtocolMaturity.EXECUTABLE)
        invoker = self.registry.for_candidate(_candidate("mcp_server"))
        self.assertIsNotNone(invoker)
        adapter = invoker.build_adapter(_candidate("mcp_server"))
        self.assertIsInstance(adapter, GenericMcpAdapter)

    def test_a2a_is_refusal_only(self) -> None:
        self.assertEqual(self.registry.maturity("a2a"), ProtocolMaturity.REFUSAL_ONLY)
        invoker = self.registry.for_candidate(_candidate("a2a_agent"))
        self.assertEqual(invoker.maturity, ProtocolMaturity.REFUSAL_ONLY)
        refusal = invoker.refusal(_candidate("a2a_agent"), "web_scraping")
        self.assertFalse(refusal.succeeded)
        self.assertTrue(refusal.raw_response.get("refused"))

    def test_acp_and_anp_are_planned(self) -> None:
        self.assertEqual(self.registry.maturity("acp"), ProtocolMaturity.PLANNED)
        self.assertEqual(self.registry.maturity("anp"), ProtocolMaturity.PLANNED)

    def test_non_executable_build_adapter_raises(self) -> None:
        invoker = self.registry.for_candidate(_candidate("a2a_agent"))
        with self.assertRaises(NotImplementedError):
            invoker.build_adapter(_candidate("a2a_agent"))

    def test_ai_agent_with_openapi_url_upgrades_to_openapi(self) -> None:
        cand = _candidate("ai_agent", openapi_url="https://example.com/openapi.json")
        self.assertEqual(self.registry.protocol_for_candidate(cand), "openapi")
        self.assertEqual(self.registry.for_candidate(cand).maturity, ProtocolMaturity.EXECUTABLE)

    def test_register_new_invoker_without_touching_coordinator(self) -> None:
        # Demonstrate the extension surface: adding a protocol is a one-liner.
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _FakeInvoker:
            protocol: str = "x402"
            maturity: ProtocolMaturity = ProtocolMaturity.PLANNED

            def build_adapter(self, candidate):  # pragma: no cover - not reached
                raise NotImplementedError

            def refusal(self, candidate, capability):  # pragma: no cover
                raise NotImplementedError

        registry = ProtocolInvokerRegistry()
        registry.register(_FakeInvoker())
        self.assertEqual(registry.maturity("x402"), ProtocolMaturity.PLANNED)

    def test_unknown_protocol_defaults_planned(self) -> None:
        registry = ProtocolInvokerRegistry()
        self.assertEqual(registry.maturity("nonsense"), ProtocolMaturity.PLANNED)


if __name__ == "__main__":
    unittest.main()
