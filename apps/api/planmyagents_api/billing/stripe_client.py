"""Thin Stripe client — Checkout creation + webhook signature verification.

No third-party SDK. Stdlib ``urllib`` + ``hmac`` + ``json`` only.

Why hand-rolled
---------------

The official ``stripe`` package is well-maintained but ships a few hundred
generated REST methods we will never need. Our footprint is two
operations:

1. ``POST /v1/checkout/sessions`` (create a hosted checkout URL).
2. Validate the ``Stripe-Signature`` header on incoming webhooks.

Both are ~60 lines of stdlib code, with the bonus that we avoid pinning
to a fast-moving SDK release line.

Stripe webhook signature format (v1)
------------------------------------

    Stripe-Signature: t=<unix_seconds>,v1=<hex_hmac>,v1=<hex_hmac_old>

The signed payload is ``f"{t}.{raw_body}"`` HMAC-SHA256'd with the
``whsec_…`` shared secret. There can be multiple ``v1`` entries during
secret rotation; we accept the request when any one matches.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StripeError(Exception):
    """Base — every billing failure path."""


class StripeApiError(StripeError):
    """HTTP failure from the Stripe REST API."""


class StripeSignatureError(StripeError):
    """The incoming webhook failed signature verification."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STRIPE_SECRET_KEY_ENV = "STRIPE_SECRET_KEY"
STRIPE_WEBHOOK_SECRET_ENV = "STRIPE_WEBHOOK_SECRET"
STRIPE_PRO_PRICE_ID_ENV = "STRIPE_PRO_PRICE_ID"
STRIPE_SUCCESS_URL_ENV = "STRIPE_CHECKOUT_SUCCESS_URL"
STRIPE_CANCEL_URL_ENV = "STRIPE_CHECKOUT_CANCEL_URL"
STRIPE_LIVE_OPT_IN_ENV = "STRIPE_LIVE_MODE_OPT_IN"
STRIPE_API_BASE_ENV = "STRIPE_API_BASE"

DEFAULT_API_BASE = "https://api.stripe.com"
DEFAULT_REPLAY_TOLERANCE_SECONDS = 300  # Stripe's own default


@dataclass(frozen=True)
class StripeConfig:
    secret_key: str
    webhook_secret: str
    pro_price_id: str
    success_url: str
    cancel_url: str
    api_base: str = DEFAULT_API_BASE
    live_mode_opt_in: bool = False
    replay_tolerance_seconds: int = DEFAULT_REPLAY_TOLERANCE_SECONDS

    def is_active(self) -> bool:
        return bool(self.secret_key) and bool(self.pro_price_id)

    def is_webhook_active(self) -> bool:
        return bool(self.webhook_secret)

    def is_test_mode(self) -> bool:
        return self.secret_key.startswith("sk_test_")


def stripe_config_from_env(env: dict[str, str] | None = None) -> StripeConfig:
    source = env if env is not None else os.environ
    secret = (source.get(STRIPE_SECRET_KEY_ENV) or "").strip()
    webhook = (source.get(STRIPE_WEBHOOK_SECRET_ENV) or "").strip()
    price = (source.get(STRIPE_PRO_PRICE_ID_ENV) or "").strip()
    success = (source.get(STRIPE_SUCCESS_URL_ENV) or "").strip()
    cancel = (source.get(STRIPE_CANCEL_URL_ENV) or "").strip()
    api_base = (source.get(STRIPE_API_BASE_ENV) or DEFAULT_API_BASE).rstrip("/")
    live_opt_in = (source.get(STRIPE_LIVE_OPT_IN_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

    config = StripeConfig(
        secret_key=secret,
        webhook_secret=webhook,
        pro_price_id=price,
        success_url=success or "http://localhost:3000/account?upgrade=success",
        cancel_url=cancel or "http://localhost:3000/account?upgrade=cancelled",
        api_base=api_base,
        live_mode_opt_in=live_opt_in,
    )

    # Defence-in-depth: if a live key is set but live opt-in isn't, refuse
    # at config-load time so we can never accidentally charge real cards
    # from a dev shell.
    if secret.startswith("sk_live_") and not live_opt_in:
        raise RuntimeError(
            "STRIPE_SECRET_KEY is a LIVE key but STRIPE_LIVE_MODE_OPT_IN is not set. "
            "Refusing to start — see docs/operations.md §billing-live-mode."
        )
    return config


# ---------------------------------------------------------------------------
# Checkout session creation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckoutSession:
    session_id: str
    url: str
    raw: dict[str, Any] = field(default_factory=dict)


def create_checkout_session(
    *,
    config: StripeConfig,
    user_id: str,
    customer_email: str,
    http_post=None,
) -> CheckoutSession:
    """Create a Stripe Checkout session for the Pro plan.

    The session is one-shot — we pass ``client_reference_id=user_id`` so
    the webhook handler can map back to our marketplace_store user row
    without any extra Stripe Customer lookup.

    Args:
        http_post: dependency-injection seam used by tests. Production
            uses :func:`_default_http_post`, which talks to ``api.stripe.com``
            over stdlib ``urllib``.
    """
    if not config.is_active():
        raise StripeApiError(
            "Stripe is not configured (missing STRIPE_SECRET_KEY or STRIPE_PRO_PRICE_ID)"
        )
    if not user_id:
        raise StripeApiError("user_id is required")
    if not customer_email:
        raise StripeApiError("customer_email is required")

    body = [
        ("mode", "subscription"),
        ("line_items[0][price]", config.pro_price_id),
        ("line_items[0][quantity]", "1"),
        ("success_url", config.success_url),
        ("cancel_url", config.cancel_url),
        ("client_reference_id", user_id),
        ("customer_email", customer_email),
        # Mark the session with our user id in metadata too — belt-and-
        # braces if Stripe ever drops client_reference_id from a webhook
        # payload variant.
        ("metadata[planmyagents_user_id]", user_id),
    ]

    poster = http_post or _default_http_post
    raw = poster(
        url=f"{config.api_base}/v1/checkout/sessions",
        secret_key=config.secret_key,
        body=urllib_parse.urlencode(body).encode("utf-8"),
    )
    session_id = str(raw.get("id") or "")
    url = str(raw.get("url") or "")
    if not session_id or not url:
        raise StripeApiError(
            f"Stripe Checkout session response missing id/url: {raw!r}"
        )
    return CheckoutSession(session_id=session_id, url=url, raw=raw)


def _default_http_post(*, url: str, secret_key: str, body: bytes) -> dict[str, Any]:
    req = urllib_request.Request(  # noqa: S310 — Stripe API is always https://
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            # Stripe-Version is optional but pinning to a known-good API
            # version protects us from a quiet schema change on their side.
            "Stripe-Version": "2024-04-10",
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:  # noqa: S310
            payload = resp.read().decode("utf-8")
    except HTTPError as exc:  # 4xx / 5xx
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:  # pragma: no cover
            err_body = ""
        raise StripeApiError(
            f"Stripe API HTTPError {exc.code}: {err_body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise StripeApiError(f"Stripe API URLError: {exc.reason}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise StripeApiError(f"Stripe API returned non-JSON: {payload!r}") from exc


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookEvent:
    event_id: str
    event_type: str
    data_object: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


def verify_webhook_signature(
    *,
    payload: bytes,
    signature_header: str | None,
    secret: str,
    now: int | None = None,
    tolerance_seconds: int = DEFAULT_REPLAY_TOLERANCE_SECONDS,
) -> None:
    """Raise :class:`StripeSignatureError` unless the request is authentic.

    Spec: https://stripe.com/docs/webhooks/signatures
    """
    if not secret:
        raise StripeSignatureError("webhook secret is not configured")
    if not signature_header:
        raise StripeSignatureError("missing Stripe-Signature header")
    parts = [p.strip() for p in signature_header.split(",") if p.strip()]
    timestamp: str | None = None
    candidate_sigs: list[str] = []
    for entry in parts:
        if "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        if key == "t":
            timestamp = value
        elif key == "v1":
            candidate_sigs.append(value)
    if not timestamp or not candidate_sigs:
        raise StripeSignatureError(
            "Stripe-Signature header missing t= or v1= entries"
        )
    try:
        ts_int = int(timestamp)
    except ValueError as exc:
        raise StripeSignatureError(f"malformed t={timestamp!r}") from exc

    now_int = int(now if now is not None else time.time())
    if abs(now_int - ts_int) > tolerance_seconds:
        raise StripeSignatureError(
            f"timestamp {ts_int} is outside tolerance ±{tolerance_seconds}s"
        )

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(
        secret.encode("utf-8"), signed_payload, hashlib.sha256
    ).hexdigest()
    for candidate in candidate_sigs:
        if hmac.compare_digest(expected, candidate):
            return
    raise StripeSignatureError("no v1 signature matched the computed HMAC")


def parse_webhook_event(payload: bytes) -> WebhookEvent:
    """Parse the verified payload into a :class:`WebhookEvent`."""
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StripeSignatureError(f"webhook payload is not valid JSON: {exc}") from exc
    event_id = str(raw.get("id") or "")
    event_type = str(raw.get("type") or "")
    data = raw.get("data") or {}
    data_object = data.get("object") if isinstance(data, dict) else {}
    if not isinstance(data_object, dict):
        data_object = {}
    if not event_id or not event_type:
        raise StripeSignatureError(
            f"webhook payload missing id/type: {raw!r}"
        )
    return WebhookEvent(
        event_id=event_id,
        event_type=event_type,
        data_object=data_object,
        raw=raw,
    )
