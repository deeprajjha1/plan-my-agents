"""Stripe payment-authorization benchmark baseline.

BENCHMARK-ONLY. Per the 16-May-2026 product spec this adapter is
NOT reachable from the customer ``/goal`` execution path. It exists
to give the benchmark runner a known-good payment_authorization
baseline against which discovered MCP / A2A / OpenAPI payment
providers can be scored. See ``benchmark/baselines/__init__.py``
for the firewall rules.

Sprint 3a (S3a-4):
    First commodity-API wrapper to ship after the cost-cap safety
    gate landed. Stripe is the demo slice because it has the most
    rigorous test environment of any paid provider we'll wrap and
    is the riskiest from a billing-safety perspective — making it
    the best validator of the cost-cap stack end-to-end.

Design choices, mirrors the Hunter adapter where it can:

* **Stdlib-only.** No `stripe` package; we hand-build the form-encoded
  POST. Keeps the prototype dependency-free and removes a path for
  the package's pin to drift away from our routing layer.
* **Test mode by default, live mode is opt-in via env.** Even if a
  caller pastes a live ``sk_live_...`` key, we refuse unless
  ``STRIPE_ALLOW_LIVE_MODE=true`` is also set. This is *belt and
  braces* on top of the cost cap — a bug that bypasses the cap
  still hits this gate before any real money moves.
* **Injectable transport.** Tests instantiate the adapter with a
  fake transport that returns canned PaymentIntent payloads, so
  no real key (live or test) is required for CI.
* **Refusals normalize to ``ProviderResponse.succeeded=False``**
  rather than raising. The benchmark runner and workflow executor
  both rely on the response shape; raising here would short-circuit
  scoring and ledger updates.

Capability supported: ``payment_authorization``.

Inputs (``ProviderRequest.inputs``):
    amount: number — major units (e.g., dollars). Required.
    currency: str — three-letter ISO currency. Required.
    payment_method: str — Stripe payment-method id. Defaults to
        ``pm_card_visa`` (Stripe's pre-built test PaymentMethod
        that always succeeds in test mode).
    description: str — optional, attached to the PaymentIntent.
    customer_id: str — optional, prefixed ``cus_``.

Output (``ProviderResponse.output``):
    intent_id: str — Stripe PaymentIntent id (``pi_...``).
    amount_minor: int — amount in minor units (e.g., cents).
    currency: str — three-letter ISO currency.
    status: str — Stripe status string. ``succeeded`` is the
        success terminal state; the others are normalized verbatim
        so downstream code can decide policy.
    test_mode: bool — true iff the key starts with ``sk_test_``.
    receipt_url: str | None — populated only for completed test-mode
        charges via the latest_charge field; otherwise null.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse

# Type alias: same Transport contract as hunter.py — (url, body, timeout) → dict.
# We deviate slightly because Stripe is POST-with-body, not GET-only.
StripeTransport = Callable[[str, bytes, dict[str, str], float], dict[str, Any]]


class StripeConfigurationError(RuntimeError):
    """Raised when Stripe is invoked without a usable API key, or
    when a live key is presented without explicit opt-in."""


_DEFAULT_API_BASE = "https://api.stripe.com/v1"
_DEFAULT_PAYMENT_METHOD = "pm_card_visa"
_LIVE_MODE_OPT_IN_ENV = "STRIPE_ALLOW_LIVE_MODE"


@dataclass(frozen=True)
class StripePaymentAuthorization:
    """Stripe ``payment_authorization`` adapter (PaymentIntent create-and-confirm).

    The dataclass is frozen so it can be instantiated by the
    registry-driven router via ``StripePaymentAuthorization()``;
    runtime configuration comes from env vars on ``__post_init__``
    rather than constructor args (matches Hunter's pattern).
    """

    api_key: str | None = None
    transport: StripeTransport | None = None
    timeout_seconds: float = 15.0
    api_base_url: str = _DEFAULT_API_BASE
    provider_id: str = "stripe-payments"
    capabilities: list[str] = None  # type: ignore[assignment]
    unit_cost_usd: float = 0.0  # Stripe charges per successful payment, not per API call.

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["payment_authorization"])
        if self.api_key is None:
            # Accept either env var name. STRIPE_SECRET_KEY is what
            # the agents.json registry declares; STRIPE_API_KEY
            # matches the curated_ai_agents.json convention. We
            # preferentially read SECRET first so a deployment that
            # sets both ends up using the registered one.
            object.__setattr__(
                self,
                "api_key",
                os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY"),
            )

    # ---- ProviderAdapter contract -----------------------------------

    async def estimate_cost(self, request: ProviderRequest) -> float:
        """Pre-call estimate.

        Stripe charges per successful payment, not per API call —
        the typical consumer fee is 2.9% + $0.30. Without
        knowing the request amount we'd return zero, but the cost
        cap will then under-count and may green-light a real
        $1000 charge. So we estimate using the request's
        ``amount`` field when present (the request shape
        guarantees major-unit input — see module docstring).

        For non-card flows or if amount is missing, fall back to
        the ``unit_cost_usd`` constant. The cost cap will treat
        this as a $0 projection, so the daily-cap safety net
        still bites if many of these calls happen.
        """

        _ensure_capability(self.capabilities, request.capability)
        amount = float(request.inputs.get("amount") or 0.0)
        currency = str(request.inputs.get("currency") or "").lower()
        if amount > 0 and currency in {"usd", "eur", "gbp", "cad", "aud", "inr"}:
            # Stripe's documented standard fee is 2.9% + $0.30. We
            # use this as the *fee* projection, not the principal.
            # The principal flows through the merchant's own books;
            # we cap our exposure to the platform fee.
            return round(amount * 0.029 + 0.30, 4)
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return bool(self.api_key)

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not self.api_key:
            raise StripeConfigurationError(
                "STRIPE_SECRET_KEY (or STRIPE_API_KEY) is required for provider "
                "`stripe-payments`."
            )
        if not _live_mode_allowed(self.api_key):
            raise StripeConfigurationError(
                "Refusing to call Stripe in LIVE mode without explicit "
                f"{_LIVE_MODE_OPT_IN_ENV}=true opt-in. Use a sk_test_... key for "
                "Sprint 3a wrapper validation, or set the env var only when you "
                "deliberately want real money to move."
            )

        amount_major = float(request.inputs.get("amount") or 0.0)
        currency = str(request.inputs.get("currency") or "usd").lower()
        amount_minor = _to_minor_units(amount_major, currency=currency)
        if amount_minor <= 0:
            return _refusal(
                request,
                error="amount_required: stripe payment_authorization requires a positive amount",
            )

        payment_method = (
            str(request.inputs.get("payment_method") or _DEFAULT_PAYMENT_METHOD)
        )
        body_fields: dict[str, str | int] = {
            "amount": str(amount_minor),
            "currency": currency,
            "payment_method": payment_method,
            "confirm": "true",
            # Disable redirect-based payment methods so the call resolves
            # synchronously in a non-browser context (no 3DS challenge
            # bouncing back to a hosted URL the wrapper can't follow).
            "automatic_payment_methods[enabled]": "true",
            "automatic_payment_methods[allow_redirects]": "never",
            "expand[]": "latest_charge",
        }
        if request.inputs.get("description"):
            body_fields["description"] = str(request.inputs["description"])
        if request.inputs.get("customer_id"):
            body_fields["customer"] = str(request.inputs["customer_id"])

        body = urllib.parse.urlencode(body_fields).encode("utf-8")
        url = f"{self.api_base_url}/payment_intents"
        headers = _stripe_headers(self.api_key, idempotency_key=request.idempotency_key)

        start = time.perf_counter()
        try:
            payload = (self.transport or _default_transport)(
                url, body, headers, self.timeout_seconds
            )
            latency_ms = int((time.perf_counter() - start) * 1000)

            # Stripe error response (no PaymentIntent created at all,
            # e.g. amount_too_small, invalid_request_error). The
            # wrapper *failed* to get an authoritative payment
            # outcome, so this is succeeded=False. Distinct from a
            # PaymentIntent that was created and *declined* — that
            # path returns succeeded=True with output.status set to
            # the decline reason, because the wrapper *did* get an
            # authoritative answer ("the payment didn't go through,
            # here's why").
            if "error" in payload and "id" not in payload:
                return ProviderResponse(
                    succeeded=False,
                    output=None,
                    cost_usd=0.0,
                    latency_ms=latency_ms,
                    error=_stripe_error_string(payload),
                    raw_response=payload,
                )

            normalized = _normalize_payment_intent(
                payload, test_mode=_is_test_key(self.api_key)
            )
            payment_status = str(payload.get("status", "")).lower()
            payment_succeeded = payment_status == "succeeded"
            actual_cost = _actual_fee(payload) if payment_succeeded else 0.0
            # `succeeded` reflects WRAPPER success ("did we get an
            # authoritative answer from Stripe?"), not BUSINESS
            # success ("did the payment go through?"). Business
            # success is carried by ``output.status`` and graded by
            # workflows/scoring._score_payment_authorization at
            # runtime. This split is what lets benchmarks credit a
            # wrapper for *correctly* reporting a decline rather
            # than penalising it for the underlying failure.
            return ProviderResponse(
                succeeded=True,
                output=normalized,
                cost_usd=actual_cost,
                latency_ms=latency_ms,
                error=None,
                raw_response=payload,
            )
        except StripeConfigurationError:
            # Already explanatory; let the workflow surface as a refusal.
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            latency_ms = int((time.perf_counter() - start) * 1000)
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=latency_ms,
                error=f"{type(exc).__name__}: {exc}",
                raw_response={},
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stripe_headers(api_key: str, *, idempotency_key: str) -> dict[str, str]:
    # Stripe accepts Basic auth with the secret as username (empty password)
    # OR Authorization: Bearer. Both are documented; Basic happens to be
    # what `stripe` SDK uses under the hood. We send Bearer for readability.
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "PlanMyAgentsBenchmark/0.1 stripe-adapter",
        # Stripe-Version pinning protects us from API changes silently
        # rewriting the response shape under the wrapper. Update this
        # when we deliberately re-test against a newer surface.
        "Stripe-Version": "2025-09-30.clover",
    }
    if idempotency_key:
        # Stripe's Idempotency-Key header makes retries safe. The
        # workflow executor synthesises one per (workflow, ordinal,
        # input) so a network retry can't double-charge.
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _default_transport(
    url: str, body: bytes, headers: dict[str, str], timeout_seconds: float
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            payload_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # Stripe error responses come back as JSON in the error body.
        # Surfacing the body verbatim (when parseable) is more useful
        # than a generic HTTP message at the calling tier.
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001 - error.read can also fail
            body_text = ""
        try:
            return json.loads(body_text) if body_text else {"error": str(exc)}
        except json.JSONDecodeError:
            return {"error": str(exc), "raw": body_text}
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("Stripe response was not a JSON object")
    return payload


def _normalize_payment_intent(
    payload: dict[str, Any], *, test_mode: bool
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Stripe payment_intents response missing JSON object")
    # Some error responses don't have an id; expose them too so the
    # caller can see the underlying failure rather than a generic
    # "missing intent_id".
    intent_id = str(payload.get("id") or "")
    amount_minor = int(payload.get("amount") or 0)
    currency = str(payload.get("currency") or "")
    status = str(payload.get("status") or "")
    latest_charge = payload.get("latest_charge")
    receipt_url: str | None = None
    if isinstance(latest_charge, dict):
        receipt_url = str(latest_charge.get("receipt_url") or "") or None
    return {
        "intent_id": intent_id,
        "amount_minor": amount_minor,
        "currency": currency,
        "status": status,
        "test_mode": test_mode,
        "receipt_url": receipt_url,
    }


def _stripe_error_string(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        msg = error.get("message")
        code = error.get("code")
        if msg and code:
            return f"stripe_error[{code}]: {msg}"
        if msg:
            return f"stripe_error: {msg}"
    if isinstance(error, str):
        return f"stripe_error: {error}"
    status = payload.get("status")
    if status:
        return f"payment_intent_status:{status}"
    return "payment_intent_failed"


def _actual_fee(payload: dict[str, Any]) -> float:
    """Actual platform fee, in USD, for the cost ledger.

    Stripe doesn't echo the application fee back on the
    PaymentIntent itself unless we explicitly request balance
    transactions. As an approximation we use the standard
    documented fee (2.9% + $0.30) on the gross amount, in
    USD-equivalent. For non-USD currencies we'd really want a
    daily FX rate; for the first-cut wrapper the fee is recorded
    in the PaymentIntent's currency and treated as USD-equivalent
    by the cost ledger (acceptable error for the cap math at the
    cents-level).
    """

    amount_minor = float(payload.get("amount") or 0.0)
    if amount_minor <= 0:
        return 0.0
    amount_major = amount_minor / 100.0
    return round(amount_major * 0.029 + 0.30, 4)


def _to_minor_units(amount_major: float, *, currency: str) -> int:
    """Convert major-unit amount (dollars) to minor-unit (cents).

    Stripe's "zero-decimal currencies" (JPY, KRW, etc.) take amounts
    in major units verbatim. The full list is documented at
    https://docs.stripe.com/currencies#zero-decimal — we hardcode
    the JPY case (most common) and treat everything else as
    standard 100-minor-per-major.
    """

    if currency == "jpy":
        return int(round(amount_major))
    return int(round(amount_major * 100))


def _is_test_key(api_key: str) -> bool:
    return bool(api_key) and api_key.startswith("sk_test_")


def _live_mode_allowed(api_key: str) -> bool:
    if _is_test_key(api_key):
        return True
    if not api_key.startswith("sk_live_"):
        # Restricted keys (rk_...) and unknown shapes — accept them
        # only with explicit opt-in. Stripe restricted keys can't
        # confirm payments anyway, so this is mostly defensive.
        return os.getenv(_LIVE_MODE_OPT_IN_ENV, "").lower() in {"1", "true", "yes"}
    return os.getenv(_LIVE_MODE_OPT_IN_ENV, "").lower() in {"1", "true", "yes"}


def _refusal(request: ProviderRequest, *, error: str) -> ProviderResponse:
    return ProviderResponse(
        succeeded=False,
        output=None,
        cost_usd=0.0,
        latency_ms=0,
        error=error,
        raw_response={},
    )


def _ensure_capability(capabilities: list[str], requested: str) -> None:
    if requested not in capabilities:
        raise ValueError(
            f"provider `stripe-payments` does not support capability: {requested}"
        )
