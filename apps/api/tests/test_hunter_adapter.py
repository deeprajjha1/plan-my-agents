from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.baselines.hunter import (
    HunterConfigurationError,
    HunterEmailVerifier,
)
from planmyagents_api.benchmark.models import ProviderRequest


class HunterAdapterTest(unittest.TestCase):
    def test_normalizes_successful_hunter_response(self) -> None:
        def fake_transport(url: str, timeout_seconds: float) -> dict:
            self.assertIn("email=jane%40acme.com", url)
            self.assertEqual(timeout_seconds, 10.0)
            return {
                "data": {
                    "email": "Jane@Acme.com",
                    "result": "deliverable",
                    "score": 95,
                    "regexp": True,
                    "gibberish": False,
                    "disposable": False,
                    "webmail": False,
                    "mx_records": True,
                    "smtp_server": True,
                    "smtp_check": True,
                    "accept_all": False,
                }
            }

        provider = HunterEmailVerifier(api_key="test-key", transport=fake_transport)
        response = asyncio.run(
            provider.execute(
                ProviderRequest(
                    capability="email_verification",
                    inputs={"email": "jane@acme.com"},
                    idempotency_key="test",
                )
            )
        )

        self.assertTrue(response.succeeded)
        self.assertEqual(response.output["email"], "jane@acme.com")
        self.assertEqual(response.output["result"], "deliverable")
        self.assertEqual(response.output["score"], 95)
        self.assertTrue(response.output["checks"]["mx_records"])

    def test_requires_api_key(self) -> None:
        provider = HunterEmailVerifier(api_key="", transport=lambda _url, _timeout: {})

        with self.assertRaises(HunterConfigurationError):
            asyncio.run(
                provider.execute(
                    ProviderRequest(
                        capability="email_verification",
                        inputs={"email": "jane@acme.com"},
                        idempotency_key="test",
                    )
                )
            )

    def test_converts_transport_error_to_failed_provider_response(self) -> None:
        def failing_transport(_url: str, _timeout_seconds: float) -> dict:
            raise RuntimeError("boom")

        provider = HunterEmailVerifier(api_key="test-key", transport=failing_transport)
        response = asyncio.run(
            provider.execute(
                ProviderRequest(
                    capability="email_verification",
                    inputs={"email": "jane@acme.com"},
                    idempotency_key="test",
                )
            )
        )

        self.assertFalse(response.succeeded)
        self.assertIn("boom", response.error)
        self.assertEqual(response.cost_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
