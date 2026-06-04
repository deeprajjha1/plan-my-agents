"""Tests for the Stripe billing client + webhook handler + endpoints.

We never talk to Stripe in tests:

* :func:`create_checkout_session` is exercised against an injected
  ``http_post`` stub that captures the form payload.
* :func:`verify_webhook_signature` is tested by signing a payload with
  the same secret we hand to the verifier.
* The end-to-end webhook endpoint is exercised via FastAPI's TestClient
  with a generated signed payload — same path Stripe production
  webhooks travel.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.auth.clerk import AuthMode, ClerkConfig
from planmyagents_api.auth.deps import (
    configure_auth_dependencies,
    reset_auth_dependencies,
)
from planmyagents_api.billing.handlers import handle_webhook_event
from planmyagents_api.billing.stripe_client import (
    StripeApiError,
    StripeConfig,
    StripeSignatureError,
    WebhookEvent,
    create_checkout_session,
    parse_webhook_event,
    stripe_config_from_env,
    verify_webhook_signature,
)
from planmyagents_api.marketplace_store import InMemoryMarketplaceStore
from planmyagents_api.web import app as web_app

WEBHOOK_SECRET = "whsec_test_secret_value"


def _make_config(*, webhook: bool = True) -> StripeConfig:
    return StripeConfig(
        secret_key="sk_test_abc",
        webhook_secret=WEBHOOK_SECRET if webhook else "",
        pro_price_id="price_test_123",
        success_url="http://localhost:3000/account?ok=1",
        cancel_url="http://localhost:3000/account?cancel=1",
    )


def _sign_payload(payload: bytes, *, secret: str = WEBHOOK_SECRET) -> tuple[str, int]:
    ts = int(time.time())
    signed = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}", ts


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class StripeConfigFromEnvTests(unittest.TestCase):
    def test_disabled_without_secret(self) -> None:
        config = stripe_config_from_env(env={})
        self.assertFalse(config.is_active())
        self.assertFalse(config.is_webhook_active())

    def test_live_key_without_opt_in_refused_at_boot(self) -> None:
        with self.assertRaises(RuntimeError):
            stripe_config_from_env(
                env={
                    "STRIPE_SECRET_KEY": "sk_live_abc",
                    "STRIPE_PRO_PRICE_ID": "price_x",
                }
            )

    def test_live_mode_opt_in_accepts_live_key(self) -> None:
        config = stripe_config_from_env(
            env={
                "STRIPE_SECRET_KEY": "sk_live_abc",
                "STRIPE_PRO_PRICE_ID": "price_x",
                "STRIPE_LIVE_MODE_OPT_IN": "true",
            }
        )
        self.assertTrue(config.is_active())
        self.assertFalse(config.is_test_mode())
        self.assertTrue(config.live_mode_opt_in)

    def test_test_key_picked_up(self) -> None:
        config = stripe_config_from_env(
            env={
                "STRIPE_SECRET_KEY": "sk_test_abc",
                "STRIPE_PRO_PRICE_ID": "price_x",
                "STRIPE_WEBHOOK_SECRET": "whsec_x",
            }
        )
        self.assertTrue(config.is_active())
        self.assertTrue(config.is_test_mode())
        self.assertTrue(config.is_webhook_active())


# ---------------------------------------------------------------------------
# Checkout creation
# ---------------------------------------------------------------------------


class CreateCheckoutSessionTests(unittest.TestCase):
    def test_inactive_config_refuses(self) -> None:
        config = StripeConfig(
            secret_key="",
            webhook_secret="",
            pro_price_id="",
            success_url="x",
            cancel_url="y",
        )
        with self.assertRaises(StripeApiError):
            create_checkout_session(
                config=config,
                user_id="u",
                customer_email="a@b.com",
                http_post=lambda **kwargs: {},
            )

    def test_happy_path_returns_session_url(self) -> None:
        captured: dict[str, Any] = {}

        def fake_post(*, url: str, secret_key: str, body: bytes) -> dict[str, Any]:
            captured["url"] = url
            captured["secret_key"] = secret_key
            captured["body"] = body.decode("utf-8")
            return {
                "id": "cs_test_123",
                "url": "https://checkout.stripe.com/c/pay/cs_test_123",
            }

        config = _make_config()
        session = create_checkout_session(
            config=config,
            user_id="user_abc",
            customer_email="alice@example.com",
            http_post=fake_post,
        )
        self.assertEqual(session.session_id, "cs_test_123")
        self.assertIn("checkout.stripe.com", session.url)
        # Form-encoded body contains the expected line item + reference id.
        self.assertIn("line_items%5B0%5D%5Bprice%5D=price_test_123", captured["body"])
        self.assertIn("client_reference_id=user_abc", captured["body"])
        self.assertIn("customer_email=alice%40example.com", captured["body"])
        self.assertIn("metadata%5Bplanmyagents_user_id%5D=user_abc", captured["body"])

    def test_response_missing_url_raises(self) -> None:
        config = _make_config()
        with self.assertRaises(StripeApiError):
            create_checkout_session(
                config=config,
                user_id="u",
                customer_email="a@b.com",
                http_post=lambda **kwargs: {"id": "cs_test", "url": ""},
            )


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------


class VerifyWebhookSignatureTests(unittest.TestCase):
    def test_missing_secret_refuses(self) -> None:
        with self.assertRaises(StripeSignatureError):
            verify_webhook_signature(
                payload=b"{}",
                signature_header="t=1,v1=deadbeef",
                secret="",
            )

    def test_missing_header_refuses(self) -> None:
        with self.assertRaises(StripeSignatureError):
            verify_webhook_signature(
                payload=b"{}",
                signature_header=None,
                secret=WEBHOOK_SECRET,
            )

    def test_valid_signature_accepted(self) -> None:
        payload = b'{"id": "evt_1", "type": "checkout.session.completed"}'
        header, ts = _sign_payload(payload)
        verify_webhook_signature(
            payload=payload,
            signature_header=header,
            secret=WEBHOOK_SECRET,
            now=ts,
        )

    def test_replay_outside_window_refused(self) -> None:
        payload = b'{"id": "evt_old"}'
        header, ts = _sign_payload(payload)
        with self.assertRaises(StripeSignatureError):
            verify_webhook_signature(
                payload=payload,
                signature_header=header,
                secret=WEBHOOK_SECRET,
                now=ts + 10_000,
                tolerance_seconds=300,
            )

    def test_tampered_payload_refused(self) -> None:
        payload = b'{"id": "evt_1"}'
        header, ts = _sign_payload(payload)
        tampered = b'{"id": "evt_pwned"}'
        with self.assertRaises(StripeSignatureError):
            verify_webhook_signature(
                payload=tampered,
                signature_header=header,
                secret=WEBHOOK_SECRET,
                now=ts,
            )

    def test_wrong_secret_refused(self) -> None:
        payload = b'{"id": "evt_1"}'
        header, ts = _sign_payload(payload)
        with self.assertRaises(StripeSignatureError):
            verify_webhook_signature(
                payload=payload,
                signature_header=header,
                secret="whsec_a_different_secret",
                now=ts,
            )

    def test_multiple_v1_entries_one_matches(self) -> None:
        """Stripe rotates webhook secrets — multiple v1= entries are allowed."""
        payload = b'{"id": "evt_1"}'
        ts = int(time.time())
        signed = f"{ts}.".encode() + payload
        valid = hmac.new(
            WEBHOOK_SECRET.encode("utf-8"), signed, hashlib.sha256
        ).hexdigest()
        header = f"t={ts},v1=00deadbeef,v1={valid}"
        verify_webhook_signature(
            payload=payload,
            signature_header=header,
            secret=WEBHOOK_SECRET,
            now=ts,
        )


class ParseWebhookEventTests(unittest.TestCase):
    def test_parses_well_formed_event(self) -> None:
        payload = json.dumps(
            {
                "id": "evt_test",
                "type": "checkout.session.completed",
                "data": {"object": {"client_reference_id": "user_1"}},
            }
        ).encode("utf-8")
        event = parse_webhook_event(payload)
        self.assertEqual(event.event_id, "evt_test")
        self.assertEqual(event.event_type, "checkout.session.completed")
        self.assertEqual(event.data_object["client_reference_id"], "user_1")

    def test_non_json_payload_raises(self) -> None:
        with self.assertRaises(StripeSignatureError):
            parse_webhook_event(b"not json")

    def test_missing_event_type_raises(self) -> None:
        with self.assertRaises(StripeSignatureError):
            parse_webhook_event(b'{"id": "evt_1"}')


# ---------------------------------------------------------------------------
# Event handler
# ---------------------------------------------------------------------------


class HandleWebhookEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryMarketplaceStore()
        self.user = self.store.upsert_user(
            clerk_user_id="user_clerk", email="alice@example.com"
        )

    def _event(self, *, event_type: str, data_object: dict[str, Any]) -> WebhookEvent:
        return WebhookEvent(
            event_id=f"evt_{event_type}",
            event_type=event_type,
            data_object=data_object,
            raw={},
        )

    def test_checkout_completed_marks_user_pro(self) -> None:
        event = self._event(
            event_type="checkout.session.completed",
            data_object={
                "client_reference_id": self.user.user_id,
                "payment_status": "paid",
            },
        )
        outcome = handle_webhook_event(event=event, store=self.store)
        self.assertEqual(outcome["outcome"], "upgraded")
        refreshed = self.store.user_for_clerk_id("user_clerk")
        assert refreshed is not None
        self.assertEqual(refreshed.plan, "pro")

    def test_checkout_completed_unpaid_skipped(self) -> None:
        event = self._event(
            event_type="checkout.session.completed",
            data_object={
                "client_reference_id": self.user.user_id,
                "payment_status": "unpaid",
            },
        )
        outcome = handle_webhook_event(event=event, store=self.store)
        self.assertEqual(outcome["outcome"], "skipped")
        refreshed = self.store.user_for_clerk_id("user_clerk")
        assert refreshed is not None
        self.assertEqual(refreshed.plan, "free")

    def test_subscription_deleted_downgrades(self) -> None:
        self.store.set_plan(user_id=self.user.user_id, plan="pro")
        event = self._event(
            event_type="customer.subscription.deleted",
            data_object={"metadata": {"planmyagents_user_id": self.user.user_id}},
        )
        outcome = handle_webhook_event(event=event, store=self.store)
        self.assertEqual(outcome["outcome"], "downgraded")
        refreshed = self.store.user_for_clerk_id("user_clerk")
        assert refreshed is not None
        self.assertEqual(refreshed.plan, "free")

    def test_subscription_updated_active_upgrades(self) -> None:
        event = self._event(
            event_type="customer.subscription.updated",
            data_object={
                "metadata": {"planmyagents_user_id": self.user.user_id},
                "status": "active",
            },
        )
        outcome = handle_webhook_event(event=event, store=self.store)
        self.assertEqual(outcome["outcome"], "upgraded")

    def test_subscription_updated_past_due_downgrades(self) -> None:
        self.store.set_plan(user_id=self.user.user_id, plan="pro")
        event = self._event(
            event_type="customer.subscription.updated",
            data_object={
                "metadata": {"planmyagents_user_id": self.user.user_id},
                "status": "past_due",
            },
        )
        outcome = handle_webhook_event(event=event, store=self.store)
        self.assertEqual(outcome["outcome"], "downgraded")
        self.assertEqual(outcome["stripe_status"], "past_due")

    def test_unknown_event_type_ignored(self) -> None:
        event = self._event(
            event_type="invoice.payment_succeeded",
            data_object={},
        )
        outcome = handle_webhook_event(event=event, store=self.store)
        self.assertEqual(outcome["outcome"], "ignored")

    def test_event_without_user_id_skipped(self) -> None:
        event = self._event(
            event_type="checkout.session.completed",
            data_object={"payment_status": "paid"},
        )
        outcome = handle_webhook_event(event=event, store=self.store)
        self.assertEqual(outcome["outcome"], "skipped")
        self.assertEqual(outcome["reason"], "missing_user_id")

    def test_replay_is_idempotent(self) -> None:
        event = self._event(
            event_type="checkout.session.completed",
            data_object={
                "client_reference_id": self.user.user_id,
                "payment_status": "paid",
            },
        )
        handle_webhook_event(event=event, store=self.store)
        # Stripe will retry — replay must not flip the user back.
        handle_webhook_event(event=event, store=self.store)
        refreshed = self.store.user_for_clerk_id("user_clerk")
        assert refreshed is not None
        self.assertEqual(refreshed.plan, "pro")


# ---------------------------------------------------------------------------
# /billing/checkout + /stripe/webhook endpoints
# ---------------------------------------------------------------------------


class BillingEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryMarketplaceStore()
        web_app.set_marketplace_store_for_tests(self.store)
        configure_auth_dependencies(
            config=ClerkConfig(mode=AuthMode.DEV), store=self.store
        )

    def tearDown(self) -> None:
        web_app.set_marketplace_store_for_tests(None)
        web_app.set_stripe_config_for_tests(None)
        reset_auth_dependencies()

    def _dev_headers(self) -> dict[str, str]:
        return {
            "X-Dev-Clerk-User-Id": "user_1",
            "X-Dev-Clerk-User-Email": "a@b.com",
        }

    def test_checkout_503_when_stripe_not_configured(self) -> None:
        web_app.set_stripe_config_for_tests(
            StripeConfig(
                secret_key="",
                webhook_secret="",
                pro_price_id="",
                success_url="x",
                cancel_url="y",
            )
        )
        client = TestClient(web_app.create_app())
        resp = client.post("/billing/checkout", headers=self._dev_headers())
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"]["code"], "billing_not_configured")

    def test_checkout_requires_auth(self) -> None:
        web_app.set_stripe_config_for_tests(_make_config())
        client = TestClient(web_app.create_app())
        resp = client.post("/billing/checkout")
        self.assertEqual(resp.status_code, 401)

    def test_webhook_503_when_secret_missing(self) -> None:
        web_app.set_stripe_config_for_tests(_make_config(webhook=False))
        client = TestClient(web_app.create_app())
        resp = client.post("/stripe/webhook", content=b"{}")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"]["code"], "webhook_not_configured")

    def test_webhook_rejects_unsigned_payload(self) -> None:
        web_app.set_stripe_config_for_tests(_make_config())
        client = TestClient(web_app.create_app())
        resp = client.post(
            "/stripe/webhook", content=b'{"id":"evt_1","type":"x"}'
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["code"], "invalid_signature")

    def test_webhook_happy_path_upgrades_user(self) -> None:
        web_app.set_stripe_config_for_tests(_make_config())
        # Pre-create the user with the in-memory store so we have their id.
        user = self.store.upsert_user(clerk_user_id="user_1", email="a@b.com")
        client = TestClient(web_app.create_app())
        payload = json.dumps(
            {
                "id": "evt_checkout",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "client_reference_id": user.user_id,
                        "payment_status": "paid",
                    }
                },
            }
        ).encode("utf-8")
        header, _ = _sign_payload(payload)
        resp = client.post(
            "/stripe/webhook",
            content=payload,
            headers={"Stripe-Signature": header, "Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200, msg=resp.text)
        self.assertEqual(resp.json()["outcome"], "upgraded")
        refreshed = self.store.user_for_clerk_id("user_1")
        assert refreshed is not None
        self.assertEqual(refreshed.plan, "pro")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
