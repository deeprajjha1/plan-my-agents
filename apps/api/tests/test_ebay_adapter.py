"""Tests for the eBay Browse ``price_comparison`` adapter."""

from __future__ import annotations

import os
import unittest
from typing import Any

from planmyagents_api.benchmark.baselines.ebay import (
    EbayBrowseProvider,
    EbayConfigurationError,
)
from planmyagents_api.benchmark.models import ProviderRequest


def _request(
    *,
    query: str = "wireless headphones",
    extras: dict[str, Any] | None = None,
    idempotency_key: str = "wf:1:k",
) -> ProviderRequest:
    inputs: dict[str, Any] = {"query": query}
    if extras:
        inputs.update(extras)
    return ProviderRequest(
        capability="price_comparison",
        inputs=inputs,
        idempotency_key=idempotency_key,
    )


def _success_payload(items: int = 3) -> dict[str, Any]:
    summaries = []
    for i in range(items):
        summaries.append({
            "itemId": f"v1|{1000 + i}|0",
            "title": f"Listing {i}",
            "price": {"value": f"{20 + i * 5:.2f}", "currency": "USD"},
            "condition": "NEW" if i % 2 == 0 else "USED",
            "itemWebUrl": f"https://sandbox.ebay.com/itm/{1000 + i}",
            "seller": {"username": f"seller_{i}"},
            "image": {"imageUrl": f"https://i.ebayimg.com/{1000 + i}.jpg"},
            "shippingOptions": [
                {"shippingCost": {"value": f"{i * 1.5:.2f}", "currency": "USD"}}
            ],
        })
    return {"itemSummaries": summaries, "total": items}


def _empty_payload() -> dict[str, Any]:
    return {"itemSummaries": [], "total": 0}


class _RecordingTransport:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> dict[str, Any]:
        self.calls.append((url, headers, timeout))
        return self.payload


# ---------------------------------------------------------------------------
# Configuration / safety gates
# ---------------------------------------------------------------------------


class EbayConfigurationGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._preserve = {
            k: os.environ.get(k)
            for k in ("EBAY_OAUTH_TOKEN", "EBAY_ALLOW_PRODUCTION", "EBAY_MARKETPLACE_ID")
        }
        for k in self._preserve:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._preserve.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    async def test_missing_token_raises_configuration_error(self) -> None:
        adapter = EbayBrowseProvider()
        with self.assertRaises(EbayConfigurationError):
            await adapter.execute(_request())

    async def test_with_token_uses_sandbox_by_default(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = EbayBrowseProvider(oauth_token="v^1.1.test", transport=transport)
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertTrue(response.output["test_mode"])
        # URL targets sandbox.
        self.assertIn("api.sandbox.ebay.com", transport.calls[0][0])

    async def test_production_endpoint_refused_without_opt_in(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = EbayBrowseProvider(
            oauth_token="v^1.1.test",
            api_base_url="https://api.ebay.com/buy/browse/v1",
            transport=transport,
        )
        with self.assertRaises(EbayConfigurationError):
            await adapter.execute(_request())
        self.assertEqual(len(transport.calls), 0)

    async def test_production_endpoint_allowed_with_opt_in(self) -> None:
        os.environ["EBAY_ALLOW_PRODUCTION"] = "true"
        transport = _RecordingTransport(_success_payload())
        adapter = EbayBrowseProvider(
            oauth_token="v^1.1.test",
            api_base_url="https://api.ebay.com/buy/browse/v1",
            transport=transport,
        )
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertFalse(response.output["test_mode"])

    async def test_marketplace_header_overrideable(self) -> None:
        os.environ["EBAY_MARKETPLACE_ID"] = "EBAY_GB"
        transport = _RecordingTransport(_success_payload())
        adapter = EbayBrowseProvider(oauth_token="v^1.1.test", transport=transport)
        await adapter.execute(_request())
        headers = transport.calls[0][1]
        self.assertEqual(headers.get("X-EBAY-C-MARKETPLACE-ID"), "EBAY_GB")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class EbayValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transport = _RecordingTransport(_success_payload())
        self.adapter = EbayBrowseProvider(
            oauth_token="v^1.1.test", transport=self.transport
        )

    async def test_empty_query_refused(self) -> None:
        response = await self.adapter.execute(_request(query=""))
        self.assertFalse(response.succeeded)
        self.assertIn("query_required", response.error or "")
        self.assertEqual(len(self.transport.calls), 0)

    async def test_query_too_long_refused(self) -> None:
        response = await self.adapter.execute(_request(query="x" * 400))
        self.assertFalse(response.succeeded)
        self.assertIn("query_too_long", response.error or "")

    async def test_limit_clamped_to_max(self) -> None:
        # limit=999 -> clamps to 50.
        await self.adapter.execute(_request(extras={"limit": 999}))
        url = self.transport.calls[0][0]
        self.assertIn("limit=50", url)


# ---------------------------------------------------------------------------
# Request shape (eBay's quirky filter syntax)
# ---------------------------------------------------------------------------


class EbayRequestShapeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transport = _RecordingTransport(_success_payload())
        self.adapter = EbayBrowseProvider(
            oauth_token="v^1.1.test", transport=self.transport
        )

    async def test_query_propagates(self) -> None:
        await self.adapter.execute(_request(query="bluetooth speaker"))
        url = self.transport.calls[0][0]
        self.assertIn("q=bluetooth+speaker", url)

    async def test_price_filter_uses_ebay_syntax(self) -> None:
        await self.adapter.execute(
            _request(extras={"min_price": 20, "max_price": 100, "currency": "USD"})
        )
        url = self.transport.calls[0][0]
        # eBay's price filter is a quirky bracketed range.
        self.assertIn("price%3A%5B20..100%5D", url)
        self.assertIn("priceCurrency%3AUSD", url)

    async def test_condition_filter_uses_brace_syntax(self) -> None:
        await self.adapter.execute(_request(extras={"condition": "new"}))
        url = self.transport.calls[0][0]
        # conditions:{NEW} (uppercased by the wrapper).
        self.assertIn("conditions%3A%7BNEW%7D", url)

    async def test_authorization_header_set(self) -> None:
        await self.adapter.execute(_request())
        headers = self.transport.calls[0][1]
        self.assertEqual(headers.get("Authorization"), "Bearer v^1.1.test")


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


class EbayResponseNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_normalises_three_listings_with_min_max_avg(self) -> None:
        transport = _RecordingTransport(_success_payload(items=3))
        adapter = EbayBrowseProvider(oauth_token="v^1.1.test", transport=transport)
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertEqual(response.output["status"], "found")
        self.assertEqual(response.output["result_count"], 3)
        self.assertEqual(response.output["min_price"]["price"], 20.0)
        self.assertEqual(response.output["max_price"]["price"], 30.0)
        self.assertAlmostEqual(response.output["average_price"], 25.0, places=2)

    async def test_empty_results_returns_no_results_status(self) -> None:
        transport = _RecordingTransport(_empty_payload())
        adapter = EbayBrowseProvider(oauth_token="v^1.1.test", transport=transport)
        response = await adapter.execute(_request(query="impossible-query"))
        self.assertTrue(response.succeeded)
        self.assertEqual(response.output["status"], "no_results")
        self.assertEqual(response.output["result_count"], 0)
        self.assertIsNone(response.output["min_price"])

    async def test_ebay_error_response_normalised(self) -> None:
        transport = _RecordingTransport(
            {"errors": [{"errorId": 1001, "message": "Invalid access token"}]}
        )
        adapter = EbayBrowseProvider(oauth_token="v^1.1.test", transport=transport)
        response = await adapter.execute(_request())
        self.assertFalse(response.succeeded)
        self.assertIn("Invalid access token", response.error or "")

    async def test_transport_exception_surfaces_as_failed_response(self) -> None:
        def raising_transport(url, headers, timeout):  # noqa: ARG001
            raise ConnectionError("eBay unreachable")

        adapter = EbayBrowseProvider(
            oauth_token="v^1.1.test", transport=raising_transport
        )
        response = await adapter.execute(_request())
        self.assertFalse(response.succeeded)
        self.assertIn("ConnectionError", response.error or "")


if __name__ == "__main__":
    unittest.main()
