"""Tests for the Razorpay ``payment_authorization`` adapter."""

from __future__ import annotations

import base64
import os
import unittest
from typing import Any

from planmyagents_api.benchmark.baselines.razorpay import (
    RazorpayConfigurationError,
    RazorpayPaymentAuthorization,
)
from planmyagents_api.benchmark.models import ProviderRequest


def _request(
    *,
    amount: float = 500.0,
    currency: str = "INR",
    receipt: str | None = None,
    notes: dict[str, Any] | None = None,
    idempotency_key: str = "wf:1:k",
) -> ProviderRequest:
    inputs: dict[str, Any] = {"amount": amount, "currency": currency}
    if receipt is not None:
        inputs["receipt"] = receipt
    if notes is not None:
        inputs["notes"] = notes
    return ProviderRequest(
        capability="payment_authorization",
        inputs=inputs,
        idempotency_key=idempotency_key,
    )


def _success_payload(
    *, amount_minor: int = 50000, currency: str = "INR", receipt: str = "rcpt_1"
) -> dict[str, Any]:
    return {
        "id": "order_OVN0123456789ab",
        "entity": "order",
        "amount": amount_minor,
        "amount_paid": 0,
        "amount_due": amount_minor,
        "currency": currency,
        "receipt": receipt,
        "status": "created",
        "attempts": 0,
        "notes": [],
        "created_at": 1737000000,
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


class RazorpayConfigurationGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._preserve = {
            k: os.environ.get(k)
            for k in (
                "RAZORPAY_KEY_ID",
                "RAZORPAY_KEY_SECRET",
                "RAZORPAY_ALLOW_LIVE_MODE",
            )
        }
        for k in self._preserve:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._preserve.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    async def test_missing_keys_raise_configuration_error(self) -> None:
        adapter = RazorpayPaymentAuthorization()
        with self.assertRaises(RazorpayConfigurationError):
            await adapter.execute(_request())

    async def test_missing_secret_raises_configuration_error(self) -> None:
        adapter = RazorpayPaymentAuthorization(key_id="rzp_test_abc", key_secret=None)
        with self.assertRaises(RazorpayConfigurationError):
            await adapter.execute(_request())

    async def test_test_keys_run_without_live_opt_in(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = RazorpayPaymentAuthorization(
            key_id="rzp_test_abc", key_secret="secret", transport=transport
        )
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertEqual(len(transport.calls), 1)
        self.assertTrue(response.output["test_mode"])

    async def test_live_keys_refused_without_opt_in(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = RazorpayPaymentAuthorization(
            key_id="rzp_live_xyz", key_secret="secret", transport=transport
        )
        with self.assertRaises(RazorpayConfigurationError):
            await adapter.execute(_request())
        self.assertEqual(len(transport.calls), 0)

    async def test_live_keys_allowed_with_explicit_env_opt_in(self) -> None:
        os.environ["RAZORPAY_ALLOW_LIVE_MODE"] = "true"
        transport = _RecordingTransport(_success_payload())
        adapter = RazorpayPaymentAuthorization(
            key_id="rzp_live_xyz", key_secret="secret", transport=transport
        )
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertFalse(response.output["test_mode"])

    async def test_unknown_key_shape_blocked_without_opt_in(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = RazorpayPaymentAuthorization(
            key_id="rk_unknown_shape", key_secret="secret", transport=transport
        )
        with self.assertRaises(RazorpayConfigurationError):
            await adapter.execute(_request())


# ---------------------------------------------------------------------------
# Request shape / Razorpay API contract
# ---------------------------------------------------------------------------


class RazorpayExecuteRequestShapeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transport = _RecordingTransport(_success_payload())
        self.adapter = RazorpayPaymentAuthorization(
            key_id="rzp_test_abc", key_secret="my_secret", transport=self.transport
        )

    async def test_amount_is_converted_to_minor_units(self) -> None:
        await self.adapter.execute(_request(amount=500.0, currency="INR"))
        body = self.transport.calls[0][1].decode()
        self.assertIn('"amount": 50000', body)
        self.assertIn('"currency": "INR"', body)

    async def test_currency_is_uppercased(self) -> None:
        # Razorpay's contract requires uppercase ISO codes.
        await self.adapter.execute(_request(amount=20.0, currency="usd"))
        body = self.transport.calls[0][1].decode()
        self.assertIn('"currency": "USD"', body)

    async def test_receipt_propagates_when_provided(self) -> None:
        await self.adapter.execute(_request(receipt="my-cart-7"))
        body = self.transport.calls[0][1].decode()
        self.assertIn('"receipt": "my-cart-7"', body)

    async def test_idempotency_key_used_as_default_receipt(self) -> None:
        # When the caller doesn't supply a receipt, the wrapper
        # uses the idempotency key (truncated to Razorpay's 40 char
        # limit) so an operator can trace dashboard entries back to
        # the workflow.
        await self.adapter.execute(_request(idempotency_key="wf-7-attempt-1"))
        body = self.transport.calls[0][1].decode()
        self.assertIn('"receipt": "wf-7-attempt-1"', body)

    async def test_long_receipt_truncated_to_forty_chars(self) -> None:
        long_receipt = "x" * 80
        await self.adapter.execute(
            _request(idempotency_key=long_receipt, receipt=None)
        )
        body = self.transport.calls[0][1].decode()
        # The default-receipt path truncates to 40.
        self.assertIn('"receipt": "' + ("x" * 40) + '"', body)

    async def test_explicit_receipt_also_truncated_to_forty_chars(self) -> None:
        # Audit regression: previously the explicit receipt path did
        # ``str(request.inputs["receipt"])`` with no length cap, so a
        # caller passing a 60-char receipt got a Razorpay
        # BAD_REQUEST_ERROR at the API instead of the wrapper
        # silently complying with the documented 40-char limit. The
        # default-receipt path already truncated correctly; this path
        # now mirrors that behaviour.
        long_receipt = "y" * 60
        await self.adapter.execute(_request(receipt=long_receipt))
        body = self.transport.calls[0][1].decode()
        self.assertIn('"receipt": "' + ("y" * 40) + '"', body)
        self.assertNotIn('"receipt": "' + ("y" * 41), body)

    async def test_notes_propagate_when_provided(self) -> None:
        await self.adapter.execute(_request(notes={"workflow_id": "wf-7"}))
        body = self.transport.calls[0][1].decode()
        self.assertIn('"notes":', body)
        self.assertIn('"workflow_id": "wf-7"', body)

    async def test_zero_amount_refused_locally(self) -> None:
        response = await self.adapter.execute(_request(amount=0))
        self.assertFalse(response.succeeded)
        self.assertIn("amount_required", response.error or "")
        # Local refusal — transport NOT called.
        self.assertEqual(len(self.transport.calls), 0)

    async def test_basic_auth_header_uses_key_pair(self) -> None:
        await self.adapter.execute(_request())
        headers = self.transport.calls[0][2]
        auth = headers.get("Authorization", "")
        self.assertTrue(auth.startswith("Basic "))
        decoded = base64.b64decode(auth.removeprefix("Basic ")).decode()
        self.assertEqual(decoded, "rzp_test_abc:my_secret")

    async def test_idempotency_key_propagates_to_header(self) -> None:
        await self.adapter.execute(_request(idempotency_key="wf-9-attempt-2"))
        headers = self.transport.calls[0][2]
        self.assertEqual(headers.get("X-Idempotency-Key"), "wf-9-attempt-2")


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


class RazorpayResponseNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_path_normalises_order(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = RazorpayPaymentAuthorization(
            key_id="rzp_test_abc", key_secret="s", transport=transport
        )
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertEqual(response.output["order_id"], "order_OVN0123456789ab")
        self.assertEqual(response.output["amount_minor"], 50000)
        self.assertEqual(response.output["currency"], "INR")
        self.assertEqual(response.output["status"], "created")
        self.assertTrue(response.output["test_mode"])
        # Order create does NOT charge a fee — that comes at
        # capture-time. Wrapper records cost_usd=0.
        self.assertEqual(response.cost_usd, 0.0)

    async def test_razorpay_error_response_normalised(self) -> None:
        error_payload = {
            "error": {
                "code": "BAD_REQUEST_ERROR",
                "description": "amount must be at least 100 paise (i.e. ₹1.00)",
            }
        }
        transport = _RecordingTransport(error_payload)
        adapter = RazorpayPaymentAuthorization(
            key_id="rzp_test_abc", key_secret="s", transport=transport
        )
        # The transport returns the canned Razorpay error envelope
        # for *any* request — we just need the wrapper's local
        # validation to pass so the transport actually gets called.
        # ₹100 is well above the wrapper's local zero-floor.
        response = await adapter.execute(_request(amount=100.0))
        self.assertFalse(response.succeeded)
        self.assertIn("BAD_REQUEST_ERROR", response.error or "")

    async def test_transport_exception_surfaces_as_failed_response(self) -> None:
        def raising_transport(url, body, headers, timeout):  # noqa: ARG001
            raise ConnectionError("network down")

        adapter = RazorpayPaymentAuthorization(
            key_id="rzp_test_abc", key_secret="s", transport=raising_transport
        )
        response = await adapter.execute(_request())
        self.assertFalse(response.succeeded)
        self.assertIn("ConnectionError", response.error or "")


# ---------------------------------------------------------------------------
# Cost projection
# ---------------------------------------------------------------------------


class RazorpayCostEstimateTests(unittest.IsolatedAsyncioTestCase):
    async def test_inr_estimate_uses_documented_fee(self) -> None:
        # ₹500 × 2.36% = ₹11.80; converted at ~83 INR/USD ≈ $0.14.
        adapter = RazorpayPaymentAuthorization(
            key_id="rzp_test_abc", key_secret="s"
        )
        cost = await adapter.estimate_cost(_request(amount=500.0, currency="INR"))
        self.assertAlmostEqual(cost, 0.14, places=2)

    async def test_usd_estimate_uses_international_rate(self) -> None:
        # $25 × 3% = $0.75.
        adapter = RazorpayPaymentAuthorization(
            key_id="rzp_test_abc", key_secret="s"
        )
        cost = await adapter.estimate_cost(_request(amount=25.0, currency="USD"))
        self.assertAlmostEqual(cost, 0.75, places=2)

    async def test_estimate_zero_for_unsupported_currency(self) -> None:
        adapter = RazorpayPaymentAuthorization(
            key_id="rzp_test_abc", key_secret="s"
        )
        cost = await adapter.estimate_cost(_request(amount=100.0, currency="XYZ"))
        self.assertEqual(cost, 0.0)


if __name__ == "__main__":
    unittest.main()
