"""Tests for the Shippo ``shipping_quote`` adapter."""

from __future__ import annotations

import os
import unittest
from typing import Any

from planmyagents_api.benchmark.baselines.shippo import (
    ShippoConfigurationError,
    ShippoQuoteFetcher,
)
from planmyagents_api.benchmark.models import ProviderRequest


def _us_address(**overrides) -> dict[str, Any]:
    return {
        "name": "Test",
        "street1": "731 Market St",
        "city": "San Francisco",
        "state": "CA",
        "zip": "94103",
        "country": "US",
        **overrides,
    }


def _parcel(**overrides) -> dict[str, Any]:
    return {
        "length": 10,
        "width": 8,
        "height": 4,
        "weight": 2,
        "distance_unit": "in",
        "mass_unit": "lb",
        **overrides,
    }


def _request(
    *,
    address_from: dict | None = None,
    address_to: dict | None = None,
    parcel: dict | None = None,
    extras: dict | None = None,
    idempotency_key: str = "wf:1:k",
) -> ProviderRequest:
    inputs: dict[str, Any] = {
        "address_from": address_from if address_from is not None else _us_address(),
        "address_to": address_to if address_to is not None else _us_address(zip="10118", city="New York", state="NY"),
        "parcel": parcel if parcel is not None else _parcel(),
    }
    if extras:
        inputs.update(extras)
    return ProviderRequest(
        capability="shipping_quote",
        inputs=inputs,
        idempotency_key=idempotency_key,
    )


def _success_payload() -> dict[str, Any]:
    return {
        "object_id": "shp_test_1234567890",
        "object_state": "VALID",
        "status": "SUCCESS",
        "address_from": {"country": "US"},
        "address_to": {"country": "US"},
        "rates": [
            {
                "object_id": "rate_usps_1",
                "provider": "USPS",
                "servicelevel": {"name": "Priority Mail"},
                "amount": "8.50",
                "currency": "USD",
                "estimated_days": 3,
            },
            {
                "object_id": "rate_ups_1",
                "provider": "UPS",
                "servicelevel": {"name": "Ground"},
                "amount": "10.20",
                "currency": "USD",
                "estimated_days": 2,
            },
            {
                "object_id": "rate_fedex_1",
                "provider": "FedEx",
                "servicelevel": {"name": "Express Saver"},
                "amount": "13.75",
                "currency": "USD",
                "estimated_days": 2,
            },
        ],
    }


class _RecordingTransport:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, bytes, dict[str, str], float]] = []

    def __call__(
        self, url: str, body: bytes, headers: dict[str, str], timeout: float
    ) -> dict[str, Any]:
        self.calls.append((url, body, headers, timeout))
        return self.payload


# ---------------------------------------------------------------------------
# Configuration / safety gates
# ---------------------------------------------------------------------------


class ShippoConfigurationGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._preserve = {
            k: os.environ.get(k)
            for k in ("SHIPPO_API_TOKEN", "SHIPPO_ALLOW_LIVE_MODE")
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
        adapter = ShippoQuoteFetcher()
        with self.assertRaises(ShippoConfigurationError):
            await adapter.execute(_request())

    async def test_test_token_runs(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = ShippoQuoteFetcher(api_token="shippo_test_abc", transport=transport)
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertTrue(response.output["test_mode"])

    async def test_live_token_refused_without_opt_in(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = ShippoQuoteFetcher(api_token="shippo_live_xyz", transport=transport)
        with self.assertRaises(ShippoConfigurationError):
            await adapter.execute(_request())
        self.assertEqual(len(transport.calls), 0)

    async def test_live_token_allowed_with_explicit_opt_in(self) -> None:
        os.environ["SHIPPO_ALLOW_LIVE_MODE"] = "true"
        transport = _RecordingTransport(_success_payload())
        adapter = ShippoQuoteFetcher(api_token="shippo_live_xyz", transport=transport)
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertFalse(response.output["test_mode"])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ShippoValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transport = _RecordingTransport(_success_payload())
        self.adapter = ShippoQuoteFetcher(
            api_token="shippo_test_abc", transport=self.transport
        )

    async def test_missing_address_country_refused(self) -> None:
        bad_address = {"street1": "123 Main", "city": "X"}
        response = await self.adapter.execute(_request(address_from=bad_address))
        self.assertFalse(response.succeeded)
        self.assertIn("address_from_missing_field:country", response.error or "")
        self.assertEqual(len(self.transport.calls), 0)

    async def test_zero_weight_parcel_refused(self) -> None:
        response = await self.adapter.execute(_request(parcel=_parcel(weight=0)))
        self.assertFalse(response.succeeded)
        self.assertIn("parcel_field_must_be_positive:weight", response.error or "")

    async def test_invalid_parcel_dim_refused(self) -> None:
        response = await self.adapter.execute(_request(parcel=_parcel(length=-1)))
        self.assertFalse(response.succeeded)
        self.assertIn("length", response.error or "")

    async def test_non_dict_address_refused(self) -> None:
        response = await self.adapter.execute(_request(address_to="just a string"))
        self.assertFalse(response.succeeded)
        self.assertIn("address_to_required", response.error or "")


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


class ShippoRequestShapeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transport = _RecordingTransport(_success_payload())
        self.adapter = ShippoQuoteFetcher(
            api_token="shippo_test_abc", transport=self.transport
        )

    async def test_async_false_is_set(self) -> None:
        await self.adapter.execute(_request())
        body = self.transport.calls[0][1].decode()
        self.assertIn('"async": false', body)

    async def test_addresses_and_parcel_round_trip(self) -> None:
        await self.adapter.execute(
            _request(
                address_from=_us_address(country="US", zip="94103"),
                address_to=_us_address(
                    country="IN",
                    state="KA",
                    city="Bengaluru",
                    zip="560001",
                    street1="100 Brigade Rd",
                ),
                parcel=_parcel(weight=3, length=12),
            )
        )
        body = self.transport.calls[0][1].decode()
        self.assertIn('"country": "US"', body)
        self.assertIn('"country": "IN"', body)
        self.assertIn('"weight": "3"', body)
        self.assertIn('"length": "12"', body)
        # Strings — Shippo's contract.

    async def test_authorization_uses_shippo_token_scheme(self) -> None:
        await self.adapter.execute(_request())
        headers = self.transport.calls[0][2]
        self.assertEqual(headers.get("Authorization"), "ShippoToken shippo_test_abc")
        self.assertEqual(headers.get("Idempotency-Key"), "wf:1:k")


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


class ShippoResponseNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_normalises_three_rates_with_cheapest(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = ShippoQuoteFetcher(api_token="shippo_test_abc", transport=transport)
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertEqual(response.output["status"], "quoted")
        self.assertEqual(response.output["rate_count"], 3)
        self.assertTrue(response.output["test_mode"])
        cheapest = response.output["cheapest_rate"]
        self.assertEqual(cheapest["carrier"], "USPS")
        self.assertEqual(cheapest["amount"], 8.50)

    async def test_shippo_error_response_normalised(self) -> None:
        transport = _RecordingTransport(
            {"detail": "Authentication credentials were not provided."}
        )
        adapter = ShippoQuoteFetcher(api_token="shippo_test_abc", transport=transport)
        response = await adapter.execute(_request())
        self.assertFalse(response.succeeded)
        self.assertIn("Authentication", response.error or "")

    async def test_transport_exception_surfaces_as_failed_response(self) -> None:
        def raising_transport(url, body, headers, timeout):  # noqa: ARG001
            raise TimeoutError("rate fetch timed out")

        adapter = ShippoQuoteFetcher(
            api_token="shippo_test_abc", transport=raising_transport
        )
        response = await adapter.execute(_request())
        self.assertFalse(response.succeeded)
        self.assertIn("TimeoutError", response.error or "")


if __name__ == "__main__":
    unittest.main()
