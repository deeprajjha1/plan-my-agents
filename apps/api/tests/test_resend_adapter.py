"""Tests for the Resend ``email_send`` adapter."""

from __future__ import annotations

import os
import unittest
from typing import Any

from planmyagents_api.benchmark.baselines.resend import (
    ResendConfigurationError,
    ResendEmailSender,
)
from planmyagents_api.benchmark.models import ProviderRequest


def _request(
    *,
    to: str | list[str] = "delivered@resend.dev",
    subject: str = "Hi",
    text: str | None = "Hello",
    html: str | None = None,
    from_address: str | None = None,
    extras: dict[str, Any] | None = None,
    idempotency_key: str = "wf:1:k",
) -> ProviderRequest:
    inputs: dict[str, Any] = {"to": to, "subject": subject}
    if text is not None:
        inputs["text"] = text
    if html is not None:
        inputs["html"] = html
    if from_address is not None:
        inputs["from"] = from_address
    if extras:
        inputs.update(extras)
    return ProviderRequest(
        capability="email_send",
        inputs=inputs,
        idempotency_key=idempotency_key,
    )


def _success_payload(email_id: str = "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794") -> dict[str, Any]:
    return {"id": email_id}


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


class ResendConfigurationGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._preserve = {
            k: os.environ.get(k)
            for k in ("RESEND_API_KEY", "RESEND_ALLOW_REAL_DOMAINS")
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
        adapter = ResendEmailSender()
        with self.assertRaises(ResendConfigurationError):
            await adapter.execute(_request())

    async def test_test_safe_sender_default_runs(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = ResendEmailSender(api_key="re_test_abc", transport=transport)
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertTrue(response.output["test_mode"])

    async def test_real_domain_sender_refused_without_opt_in(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = ResendEmailSender(api_key="re_test_abc", transport=transport)
        response = await adapter.execute(
            _request(from_address="founder@my-real-co.com")
        )
        self.assertFalse(response.succeeded)
        self.assertIn("real_domain_refused", response.error or "")
        # Critical: the transport was NEVER called.
        self.assertEqual(len(transport.calls), 0)

    async def test_real_domain_sender_allowed_with_explicit_opt_in(self) -> None:
        os.environ["RESEND_ALLOW_REAL_DOMAINS"] = "true"
        transport = _RecordingTransport(_success_payload())
        adapter = ResendEmailSender(api_key="re_test_abc", transport=transport)
        response = await adapter.execute(
            _request(from_address="founder@my-real-co.com")
        )
        self.assertTrue(response.succeeded)
        # Sender is real, so test_mode is false even though the
        # to-address is on resend.dev.
        self.assertFalse(response.output["test_mode"])


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


class ResendExecuteRequestShapeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transport = _RecordingTransport(_success_payload())
        self.adapter = ResendEmailSender(
            api_key="re_test_abc", transport=self.transport
        )

    async def test_required_fields_validated_locally(self) -> None:
        # Missing recipient.
        response = await self.adapter.execute(_request(to=""))
        self.assertFalse(response.succeeded)
        self.assertIn("recipient_required", response.error or "")
        self.assertEqual(len(self.transport.calls), 0)
        # Missing subject.
        response = await self.adapter.execute(_request(subject=""))
        self.assertFalse(response.succeeded)
        self.assertIn("subject_required", response.error or "")
        # Missing both html AND text.
        response = await self.adapter.execute(_request(text=None, html=None))
        self.assertFalse(response.succeeded)
        self.assertIn("body_required", response.error or "")

    async def test_recipient_string_or_list_accepted(self) -> None:
        await self.adapter.execute(_request(to="delivered@resend.dev"))
        body1 = self.transport.calls[0][1].decode()
        self.assertIn('"to": ["delivered@resend.dev"]', body1)

        self.transport.calls.clear()
        await self.adapter.execute(_request(to=["a@resend.dev", "b@resend.dev"]))
        body2 = self.transport.calls[0][1].decode()
        self.assertIn('"to": ["a@resend.dev", "b@resend.dev"]', body2)

    async def test_optional_fields_propagate(self) -> None:
        await self.adapter.execute(
            _request(
                extras={
                    "reply_to": "reply@resend.dev",
                    "cc": ["cc@resend.dev"],
                    "bcc": ["bcc@resend.dev"],
                    "tags": [{"name": "campaign", "value": "bench"}],
                }
            )
        )
        body = self.transport.calls[0][1].decode()
        self.assertIn('"reply_to": "reply@resend.dev"', body)
        self.assertIn('"cc": ["cc@resend.dev"]', body)
        self.assertIn('"bcc": ["bcc@resend.dev"]', body)
        self.assertIn('"tags":', body)

    async def test_idempotency_key_propagates_to_header(self) -> None:
        await self.adapter.execute(_request(idempotency_key="wf-3-attempt-1"))
        headers = self.transport.calls[0][2]
        self.assertEqual(headers.get("Idempotency-Key"), "wf-3-attempt-1")
        self.assertEqual(headers.get("Authorization"), "Bearer re_test_abc")


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


class ResendResponseNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_path_normalises_email_response(self) -> None:
        transport = _RecordingTransport(_success_payload("aaa-bbb-ccc"))
        adapter = ResendEmailSender(api_key="re_test_abc", transport=transport)
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertEqual(response.output["email_id"], "aaa-bbb-ccc")
        self.assertEqual(response.output["from"], "onboarding@resend.dev")
        self.assertEqual(response.output["to"], ["delivered@resend.dev"])
        self.assertEqual(response.output["status"], "queued")
        self.assertTrue(response.output["test_mode"])

    async def test_resend_error_response_normalised(self) -> None:
        transport = _RecordingTransport(
            {"name": "validation_error", "message": "from is required"}
        )
        adapter = ResendEmailSender(api_key="re_test_abc", transport=transport)
        response = await adapter.execute(_request())
        self.assertFalse(response.succeeded)
        self.assertIn("validation_error", response.error or "")

    async def test_transport_exception_surfaces_as_failed_response(self) -> None:
        def raising_transport(url, body, headers, timeout):  # noqa: ARG001
            raise TimeoutError("upstream timeout")

        adapter = ResendEmailSender(api_key="re_test_abc", transport=raising_transport)
        response = await adapter.execute(_request())
        self.assertFalse(response.succeeded)
        self.assertIn("TimeoutError", response.error or "")


# ---------------------------------------------------------------------------
# Cost projection
# ---------------------------------------------------------------------------


class ResendCostEstimateTests(unittest.IsolatedAsyncioTestCase):
    async def test_estimate_per_recipient_uses_paid_tier_rate(self) -> None:
        adapter = ResendEmailSender(api_key="re_test_abc")
        cost_one = await adapter.estimate_cost(_request(to="a@resend.dev"))
        cost_three = await adapter.estimate_cost(
            _request(to=["a@resend.dev", "b@resend.dev", "c@resend.dev"])
        )
        self.assertAlmostEqual(cost_one, 0.0004, places=6)
        self.assertAlmostEqual(cost_three, 0.0012, places=6)


if __name__ == "__main__":
    unittest.main()
