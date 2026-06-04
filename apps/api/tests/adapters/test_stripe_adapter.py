"""Tests for the Stripe ``payment_authorization`` adapter."""

from __future__ import annotations

import os
import unittest
from typing import Any

from planmyagents_api.benchmark.baselines.stripe import (
    StripeConfigurationError,
    StripePaymentAuthorization,
)
from planmyagents_api.benchmark.models import ProviderRequest


def _request(
    *,
    amount: float = 20.0,
    currency: str = "usd",
    payment_method: str | None = None,
    description: str | None = None,
    customer_id: str | None = None,
    idempotency_key: str = "wf:1:k",
) -> ProviderRequest:
    inputs: dict[str, Any] = {"amount": amount, "currency": currency}
    if payment_method is not None:
        inputs["payment_method"] = payment_method
    if description is not None:
        inputs["description"] = description
    if customer_id is not None:
        inputs["customer_id"] = customer_id
    return ProviderRequest(
        capability="payment_authorization",
        inputs=inputs,
        idempotency_key=idempotency_key,
    )


def _success_payload(
    *, amount_minor: int = 2000, currency: str = "usd",
    receipt_url: str = "https://stripe.com/r/test_receipt",
) -> dict[str, Any]:
    return {
        "id": "pi_3Rt000000000000000",
        "object": "payment_intent",
        "amount": amount_minor,
        "currency": currency,
        "status": "succeeded",
        "client_secret": "pi_3Rt000000000000000_secret_xyz",
        "latest_charge": {
            "id": "ch_3Rt000000000000000",
            "receipt_url": receipt_url,
            "paid": True,
        },
    }


class _RecordingTransport:
    """Test transport that records the call args and returns canned JSON."""

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


class StripeConfigurationGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._preserve = {
            k: os.environ.get(k)
            for k in ("STRIPE_SECRET_KEY", "STRIPE_API_KEY", "STRIPE_ALLOW_LIVE_MODE")
        }
        for k in self._preserve:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._preserve.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    async def test_missing_key_raises_configuration_error(self) -> None:
        adapter = StripePaymentAuthorization()
        with self.assertRaises(StripeConfigurationError):
            await adapter.execute(_request())

    async def test_test_key_runs_without_live_opt_in(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = StripePaymentAuthorization(
            api_key="sk_test_abc", transport=transport
        )
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertEqual(len(transport.calls), 1)
        # Test mode flag must propagate to the normalised output.
        self.assertTrue(response.output["test_mode"])

    async def test_live_key_refused_without_opt_in(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = StripePaymentAuthorization(
            api_key="sk_live_xyz", transport=transport
        )
        with self.assertRaises(StripeConfigurationError):
            await adapter.execute(_request())
        # Critically: the transport was NEVER called, so no money moves.
        self.assertEqual(len(transport.calls), 0)

    async def test_live_key_allowed_with_explicit_env_opt_in(self) -> None:
        os.environ["STRIPE_ALLOW_LIVE_MODE"] = "true"
        transport = _RecordingTransport(_success_payload())
        adapter = StripePaymentAuthorization(
            api_key="sk_live_xyz", transport=transport
        )
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        # And test_mode is correctly false here.
        self.assertFalse(response.output["test_mode"])

    async def test_unknown_key_shape_blocked_without_opt_in(self) -> None:
        # Restricted keys (rk_) and any other shape can't confirm
        # payments anyway, but we treat them as live by default.
        transport = _RecordingTransport(_success_payload())
        adapter = StripePaymentAuthorization(
            api_key="rk_live_unknown_shape", transport=transport
        )
        with self.assertRaises(StripeConfigurationError):
            await adapter.execute(_request())


# ---------------------------------------------------------------------------
# Request shape / Stripe API contract
# ---------------------------------------------------------------------------


class StripeExecuteRequestShapeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transport = _RecordingTransport(_success_payload())
        self.adapter = StripePaymentAuthorization(
            api_key="sk_test_abc", transport=self.transport
        )

    async def test_amount_is_converted_to_minor_units(self) -> None:
        await self.adapter.execute(_request(amount=20.0, currency="usd"))
        body = self.transport.calls[0][1].decode()
        self.assertIn("amount=2000", body)
        self.assertIn("currency=usd", body)

    async def test_jpy_amount_uses_zero_decimal(self) -> None:
        # JPY is a zero-decimal currency — major equals minor.
        await self.adapter.execute(_request(amount=2500, currency="jpy"))
        body = self.transport.calls[0][1].decode()
        self.assertIn("amount=2500", body)
        self.assertIn("currency=jpy", body)

    async def test_default_payment_method_is_test_card(self) -> None:
        await self.adapter.execute(_request())
        body = self.transport.calls[0][1].decode()
        self.assertIn("payment_method=pm_card_visa", body)
        # Sync resolution: redirects disabled.
        self.assertIn("automatic_payment_methods%5Ballow_redirects%5D=never", body)

    async def test_explicit_payment_method_is_passed_through(self) -> None:
        await self.adapter.execute(_request(payment_method="pm_card_chargeDeclined"))
        body = self.transport.calls[0][1].decode()
        self.assertIn("payment_method=pm_card_chargeDeclined", body)

    async def test_description_and_customer_added_when_provided(self) -> None:
        await self.adapter.execute(
            _request(description="test order #1", customer_id="cus_AbCdEf"),
        )
        body = self.transport.calls[0][1].decode()
        self.assertIn("description=test+order+%231", body)
        self.assertIn("customer=cus_AbCdEf", body)

    async def test_idempotency_key_propagates_to_header(self) -> None:
        await self.adapter.execute(_request(idempotency_key="wf-7-attempt-1"))
        headers = self.transport.calls[0][2]
        self.assertEqual(headers.get("Idempotency-Key"), "wf-7-attempt-1")
        self.assertTrue(headers.get("Authorization", "").startswith("Bearer sk_test_"))

    async def test_zero_amount_refused_locally(self) -> None:
        response = await self.adapter.execute(_request(amount=0))
        self.assertFalse(response.succeeded)
        self.assertIn("amount_required", response.error or "")
        # Local refusal: HTTP transport must NOT have been called.
        self.assertEqual(len(self.transport.calls), 0)


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


class StripeResponseNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_path_normalizes_payment_intent(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = StripePaymentAuthorization(
            api_key="sk_test_abc", transport=transport
        )
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertEqual(response.output["intent_id"], "pi_3Rt000000000000000")
        self.assertEqual(response.output["amount_minor"], 2000)
        self.assertEqual(response.output["currency"], "usd")
        self.assertEqual(response.output["status"], "succeeded")
        self.assertTrue(response.output["test_mode"])
        self.assertEqual(response.output["receipt_url"], "https://stripe.com/r/test_receipt")
        # Cost recorded matches the documented Stripe fee on $20: 0.029*20+0.30 = $0.88.
        self.assertAlmostEqual(response.cost_usd, 0.88, places=2)

    async def test_requires_action_status_carries_through_as_wrapper_success(
        self,
    ) -> None:
        # Contract: ``succeeded`` reflects wrapper success ("did we
        # get an authoritative answer from Stripe?"), not business
        # success ("did the money move?"). A 3DS challenge is an
        # authoritative *partial* answer that the wrapper must surface
        # via ``output.status`` rather than burying it under
        # ``succeeded=False``. Workflow scorer
        # (workflows/scoring._score_payment_authorization) grades
        # business success by inspecting output.status separately.
        payload = _success_payload()
        payload["status"] = "requires_action"
        transport = _RecordingTransport(payload)
        adapter = StripePaymentAuthorization(
            api_key="sk_test_abc", transport=transport
        )
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertIsNone(response.error)
        self.assertEqual(response.output["status"], "requires_action")
        # Cost recorded as zero: only fired payments charge a fee
        # (succeeded=True at the wrapper level does NOT mean money
        # moved at the Stripe level).
        self.assertEqual(response.cost_usd, 0.0)

    async def test_stripe_error_response_normalised(self) -> None:
        error_payload = {
            "error": {
                "type": "invalid_request_error",
                "code": "amount_too_small",
                "message": "Amount must be at least $0.50 USD.",
            }
        }
        transport = _RecordingTransport(error_payload)
        adapter = StripePaymentAuthorization(
            api_key="sk_test_abc", transport=transport
        )
        response = await adapter.execute(_request(amount=0.10))
        self.assertFalse(response.succeeded)
        self.assertIn("amount_too_small", response.error or "")

    async def test_transport_exception_surfaced_as_failed_response(self) -> None:
        def raising_transport(url, body, headers, timeout):  # noqa: ARG001
            raise ConnectionError("network down")

        adapter = StripePaymentAuthorization(
            api_key="sk_test_abc", transport=raising_transport
        )
        response = await adapter.execute(_request())
        self.assertFalse(response.succeeded)
        self.assertIn("ConnectionError", response.error or "")


# ---------------------------------------------------------------------------
# Cost projection
# ---------------------------------------------------------------------------


class StripeCostEstimateTests(unittest.IsolatedAsyncioTestCase):
    async def test_estimate_uses_documented_fee(self) -> None:
        adapter = StripePaymentAuthorization(api_key="sk_test_abc")
        cost = await adapter.estimate_cost(_request(amount=100.0, currency="usd"))
        # 2.9% * $100 + $0.30 = $3.20.
        self.assertAlmostEqual(cost, 3.20, places=2)

    async def test_estimate_zero_for_unknown_currency(self) -> None:
        # Falls back to unit_cost_usd (0.0 by default) when we don't
        # know the FX rate to USD.
        adapter = StripePaymentAuthorization(api_key="sk_test_abc")
        cost = await adapter.estimate_cost(_request(amount=100.0, currency="xyz"))
        self.assertEqual(cost, 0.0)


if __name__ == "__main__":
    unittest.main()
