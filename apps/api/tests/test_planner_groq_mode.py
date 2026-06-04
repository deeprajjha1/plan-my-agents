"""End-to-end test that PLANMYAGENTS_PLANNER=groq routes through GroqChatClient."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.router import ProviderRouter
from planmyagents_api.discovery.normalizer import normalize_candidate
from planmyagents_api.planner.capability_catalog import build_capability_catalog


class _StubResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


class PlannerGroqModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_env: dict[str, str | None] = {}
        env_overrides = (
            ("PLANMYAGENTS_PLANNER", "groq"),
            ("GROQ_API_KEY", "stub-key"),
            ("GROQ_MODEL", "llama-3.3-70b-versatile"),
            # The goal decomposer (and the legacy intent_mapper before
            # it) make additional LLM calls on unsupported plans. This
            # test is specifically asserting the *constrained planner*
            # routes through GroqChatClient — disabling the decomposer
            # keeps the assertion ``mock.assert_called_once()`` valid
            # without making the test depend on decomposer behaviour
            # that has its own dedicated coverage in
            # ``test_goal_decomposer.py``.
            ("PLANMYAGENTS_GOAL_DECOMPOSER", "off"),
            # Suppress all default live-network discovery sources so the
            # planner's `_build_default_capability_catalog` doesn't make
            # real HTTP calls during tests. Each of these is opt-out via
            # the env toggle convention.
            ("PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY", "false"),
            ("PLANMYAGENTS_DISCOVERY_APIS_GURU", "false"),
            ("PLANMYAGENTS_DISCOVERY_HACKER_NEWS", "false"),
            ("PLANMYAGENTS_DISCOVERY_VENDOR_RSS", "false"),
            ("PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED", "false"),
        )
        for key, value in env_overrides:
            self._previous_env[key] = os.environ.get(key)
            os.environ[key] = value

    def tearDown(self) -> None:
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_groq_mode_uses_groq_client_and_returns_unsupported_plan(self) -> None:
        from planmyagents_api.web.planning import plan_goal_smart

        groq_completion = json.dumps(
            {
                "status": "unsupported",
                "summary": "Need cross-border real estate + crypto stack.",
                "missing_capabilities": [
                    "property_search",
                    "kyc_aml_check",
                    "crypto_onramp",
                    "stablecoin_transfer",
                    "escrow_management",
                    "title_verification",
                    "currency_conversion",
                    "registration_filing",
                    "tax_advisory",
                    "residency_advisory",
                ],
                "refusal_reasons": [
                    "PlanMyAgents has no routable providers for these capabilities yet."
                ],
                "sub_tasks": [],
            }
        )

        catalog_candidate = normalize_candidate(
            {
                "id": "demo",
                "display_name": "Demo",
                "vendor": "Demo",
                "vendor_url": "https://demo.example",
                "provider_type": "mcp_server",
                "capabilities": ["semantic_search"],
            },
            source="test",
        )
        catalog = build_capability_catalog([catalog_candidate])

        groq_response = json.dumps(
            {"choices": [{"message": {"content": groq_completion}}]}
        ).encode("utf-8")

        with patch("planmyagents_api.planner.groq_client.request.urlopen") as mock:
            mock.return_value = _StubResponse(groq_response)
            plan, metadata = plan_goal_smart(
                "Help me buy a house in Dubai using crypto",
                router=ProviderRouter(),
                capability_catalog=catalog,
            )

        self.assertEqual(plan.status, "unsupported")
        self.assertGreaterEqual(len(plan.missing_capabilities), 8)
        self.assertEqual(metadata["mode"], "groq")
        self.assertEqual(metadata["model"], "llama-3.3-70b-versatile")
        mock.assert_called_once()
        sent_request = mock.call_args[0][0]
        self.assertTrue(sent_request.full_url.endswith("/chat/completions"))
        sent_payload = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(sent_payload["model"], "llama-3.3-70b-versatile")

    def test_groq_mode_refuses_when_api_key_missing(self) -> None:
        # Behavior change: PLANMYAGENTS_PLANNER=groq with no key used to
        # silently fall through to the deterministic substring rules
        # planner ("rules_fallback"). That silent-degradation path is
        # what produced confidently-wrong matches like the school-project
        # MCP server appearing under "fare_comparison". The new contract
        # is to refuse honestly via PlanningUnavailableError so the route
        # handler can return a 503 the user can act on.
        os.environ.pop("GROQ_API_KEY", None)

        from planmyagents_api.web.planning import PlanningUnavailableError, plan_goal_smart

        with self.assertRaises(PlanningUnavailableError) as ctx:
            plan_goal_smart(
                "verify if jane@acme.com is a valid email",
                router=ProviderRouter(),
            )

        meta = ctx.exception.metadata
        self.assertEqual(meta.mode, "groq")
        self.assertIn("GROQ_API_KEY", meta.reason)
        self.assertFalse(meta.fallback_attempted)
        self.assertEqual(meta.fallback_error, "missing_api_key")


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
