"""Razorpay payment-authorization benchmark baseline.

BENCHMARK-ONLY. Per the 16-May-2026 product spec this adapter is
NOT reachable from the customer ``/goal`` execution path. It exists
to give the benchmark runner a known-good payment_authorization
baseline against which discovered MCP / A2A / OpenAPI payment
providers can be scored. See ``benchmark/baselines/__init__.py``
for the firewall rules.

Sprint 3a (S3a-4): second commodity-API wrapper, the Indian-market
counterpart to the Stripe slice.

Why ship Razorpay alongside Stripe:
    * Stripe India onboarding is invite-only, so an Indian-domiciled
      operator cannot validate the Stripe slice without waiting on
      an invite or claiming residence elsewhere. Razorpay's signup
      is open in India and issues test keys without KYC, so the
      end-to-end demo path lights up immediately for the Indian
      market.
    * Razorpay is the natural production processor for the user's
      actual customers, so wrapper time invested here is not
      throwaway.

Design choices, mirrors the Stripe adapter where possible:

* **Stdlib-only.** No `razorpay` package; we hand-build the JSON
  POST. Keeps the prototype dependency-free.
* **Test mode by default, live mode is opt-in via env.** Even if a
  caller pastes a live ``rzp_live_...`` key, we refuse unless
  ``RAZORPAY_ALLOW_LIVE_MODE=true`` is also set. Same belt-and-
  braces gate as Stripe.
* **Injectable transport.** Tests instantiate the adapter with a
  fake transport that returns canned Order payloads, so no real
  key is required for CI.
* **Normalised output is provider-native** (``status="created"``
  for a freshly authorised order). The wider workflow scorer
  recognises both Stripe-style (``succeeded``) and Razorpay-style
  (``created`` / ``attempted`` / ``paid``) statuses.

The wrapper models *Order Creation* — the server-side authorization
step in Razorpay's standard flow:

    Backend:  POST /v1/orders  →  order_id
    Frontend: razorpay-checkout collects card → payment_id + signature
    Backend:  signature verification → ``paid``

Order Creation is deterministic in test mode and does not require
a frontend, so it's the right benchmarkable step. Capture / payment
verification are out of scope for the v1 wrapper (would require a
browser to drive checkout).
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse

# (url, body, headers, timeout) -> response_dict
RazorpayTransport = Callable[[str, bytes, dict[str, str], float], dict[str, Any]]


class RazorpayConfigurationError(RuntimeError):
    """Raised when Razorpay is invoked without usable credentials,
    or when live keys are presented without explicit opt-in."""


_DEFAULT_API_BASE = "https://api.razorpay.com/v1"
_LIVE_MODE_OPT_IN_ENV = "RAZORPAY_ALLOW_LIVE_MODE"

# Razorpay's documented currencies as of 2026 — the wrapper treats
# all of them as standard 100-minor-per-major except where Razorpay
# explicitly differs. Razorpay does NOT support zero-decimal currencies
# the way Stripe does (no JPY support today), so the math is uniform.
_SUPPORTED_CURRENCIES_FOR_FEE_ESTIMATE = {
    "inr", "usd", "eur", "gbp", "sgd", "aud", "cad", "aed",
}


@dataclass(frozen=True)
class RazorpayPaymentAuthorization:
    """Razorpay ``payment_authorization`` adapter (Order Create)."""

    key_id: str | None = None
    key_secret: str | None = None
    transport: RazorpayTransport | None = None
    timeout_seconds: float = 15.0
    api_base_url: str = _DEFAULT_API_BASE
    provider_id: str = "razorpay-payments"
    capabilities: list[str] = None  # type: ignore[assignment]
    unit_cost_usd: float = 0.0  # Per-call fee is amount-derived; see estimate_cost.

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["payment_authorization"])
        if self.key_id is None:
            object.__setattr__(self, "key_id", os.getenv("RAZORPAY_KEY_ID"))
        if self.key_secret is None:
            object.__setattr__(self, "key_secret", os.getenv("RAZORPAY_KEY_SECRET"))

    # ---- ProviderAdapter contract -----------------------------------

    async def estimate_cost(self, request: ProviderRequest) -> float:
        """Pre-call estimate.

        Razorpay's documented domestic pricing is 2% on INR cards
        plus 18% GST on the fee (effective ~2.36%). For
        non-INR currencies the published international rate is 3%.
        We use the higher international rate as the projection so
        the cost cap is conservative — under-estimating fees would
        let the cap miss real spend.
        """

        _ensure_capability(self.capabilities, request.capability)
        amount = float(request.inputs.get("amount") or 0.0)
        currency = str(request.inputs.get("currency") or "inr").lower()
        if amount <= 0:
            return self.unit_cost_usd
        if currency == "inr":
            # 2% + 18% GST on the fee = 0.02 * 1.18 = 0.0236.
            inr_fee = round(amount * 0.0236, 4)
            # Convert INR fee to USD-equivalent for the ledger. We
            # use a conservative ~1 USD = 83 INR rate; this is
            # low-precision but the ledger only needs ballpark
            # accuracy for the cap math (real fees come back on
            # the response when settlement happens).
            return round(inr_fee / 83.0, 4)
        if currency in _SUPPORTED_CURRENCIES_FOR_FEE_ESTIMATE:
            return round(amount * 0.03, 4)
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return bool(self.key_id and self.key_secret)

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not (self.key_id and self.key_secret):
            raise RazorpayConfigurationError(
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required for "
                "provider `razorpay-payments`."
            )
        if not _live_mode_allowed(self.key_id):
            raise RazorpayConfigurationError(
                "Refusing to call Razorpay in LIVE mode without explicit "
                f"{_LIVE_MODE_OPT_IN_ENV}=true opt-in. Use a rzp_test_... "
                "key for Sprint 3a wrapper validation, or set the env var "
                "only when you deliberately want real money to move."
            )

        amount_major = float(request.inputs.get("amount") or 0.0)
        currency = str(request.inputs.get("currency") or "INR").upper()
        amount_minor = _to_minor_units(amount_major)
        if amount_minor <= 0:
            return _refusal(
                request,
                error="amount_required: razorpay payment_authorization requires "
                      "a positive amount",
            )

        body_payload: dict[str, Any] = {
            "amount": amount_minor,
            "currency": currency,
        }
        if request.inputs.get("receipt"):
            # Razorpay rejects receipts longer than 40 characters with
            # BAD_REQUEST_ERROR. We truncate explicit receipts the same
            # way we truncate the idempotency-key fallback below — the
            # alternative (passing the full string and letting Razorpay
            # reject the call) makes the wrapper's error surface
            # depend on receipt-length validation logic that lives
            # outside our codebase. Truncating defensively is safe
            # because the receipt is a correlation id, not an integrity
            # value: collisions in the truncated tail would just look
            # like duplicate receipts in the dashboard, not lost data.
            body_payload["receipt"] = str(request.inputs["receipt"])[:40]
        else:
            # Razorpay's receipt field is optional but useful as a
            # human-readable correlation id in the dashboard. Use the
            # workflow's idempotency key (truncated to Razorpay's
            # 40-char limit) so an operator browsing the dashboard
            # can trace back to the workflow.
            body_payload["receipt"] = (request.idempotency_key or "")[:40]
        if request.inputs.get("notes") and isinstance(request.inputs["notes"], dict):
            body_payload["notes"] = request.inputs["notes"]
        # Razorpay supports `payment_capture` flag; default behaviour
        # auto-captures on successful payment, which is what almost
        # everyone wants. We don't expose it as a wrapper input until
        # we have a use case to differentiate.

        body = json.dumps(body_payload).encode("utf-8")
        url = f"{self.api_base_url}/orders"
        headers = _razorpay_headers(
            self.key_id, self.key_secret, idempotency_key=request.idempotency_key
        )

        start = time.perf_counter()
        try:
            payload = (self.transport or _default_transport)(
                url, body, headers, self.timeout_seconds
            )
            latency_ms = int((time.perf_counter() - start) * 1000)

            # Razorpay error responses carry an ``error`` object with
            # ``code`` and ``description``. They also tend to NOT carry
            # an ``id`` field. Distinguishing here keeps the
            # wrapper-success vs business-success contract aligned with
            # the Stripe adapter: succeeded=False ONLY when the
            # wrapper failed to get an authoritative answer.
            if "error" in payload and "id" not in payload:
                return ProviderResponse(
                    succeeded=False,
                    output=None,
                    cost_usd=0.0,
                    latency_ms=latency_ms,
                    error=_razorpay_error_string(payload),
                    raw_response=payload,
                )

            normalized = _normalize_order(
                payload, test_mode=_is_test_key(self.key_id or "")
            )
            # Order creation does NOT charge any fee in Razorpay —
            # the fee only applies once a payment is captured against
            # the order (which happens via the customer-facing
            # checkout flow, out of scope for this wrapper). Cost is
            # therefore zero at this stage; the cost cap relied on
            # the estimate at gate-time.
            return ProviderResponse(
                succeeded=True,
                output=normalized,
                cost_usd=0.0,
                latency_ms=latency_ms,
                error=None,
                raw_response=payload,
            )
        except RazorpayConfigurationError:
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


def _razorpay_headers(
    key_id: str, key_secret: str, *, idempotency_key: str
) -> dict[str, str]:
    # Razorpay uses HTTP Basic auth with key_id as username and
    # key_secret as password. Manual base64 encode keeps the wrapper
    # stdlib-only.
    credential = f"{key_id}:{key_secret}".encode()
    encoded = base64.b64encode(credential).decode("ascii")
    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "User-Agent": "PlanMyAgentsBenchmark/0.1 razorpay-adapter",
    }
    # Razorpay supports an X-Razorpay-Account header for partner
    # accounts and an X-Idempotency-Key header for safe retries.
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    return headers


def _default_transport(
    url: str, body: bytes, headers: dict[str, str], timeout_seconds: float
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            payload_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001 - error.read can fail
            body_text = ""
        try:
            return json.loads(body_text) if body_text else {"error": {"description": str(exc)}}
        except json.JSONDecodeError:
            return {"error": {"description": str(exc), "raw": body_text}}
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("Razorpay response was not a JSON object")
    return payload


def _normalize_order(payload: dict[str, Any], *, test_mode: bool) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Razorpay /orders response missing JSON object")
    return {
        "order_id": str(payload.get("id") or ""),
        "amount_minor": int(payload.get("amount") or 0),
        "currency": str(payload.get("currency") or ""),
        "status": str(payload.get("status") or ""),
        "test_mode": test_mode,
        # Razorpay echoes the receipt verbatim — useful for dashboard
        # cross-reference at incident time. Surface it on the
        # normalised output.
        "receipt": str(payload.get("receipt") or "") or None,
    }


def _razorpay_error_string(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        description = error.get("description")
        code = error.get("code")
        if description and code:
            return f"razorpay_error[{code}]: {description}"
        if description:
            return f"razorpay_error: {description}"
    return "razorpay_order_failed"


def _to_minor_units(amount_major: float) -> int:
    """Convert major units to minor units.

    Razorpay does not support zero-decimal currencies (no JPY
    today), so the math is uniformly *100. INR is paise (1 INR =
    100 paise); USD is cents (1 USD = 100 cents).
    """

    return int(round(amount_major * 100))


def _is_test_key(key_id: str) -> bool:
    return bool(key_id) and key_id.startswith("rzp_test_")


def _live_mode_allowed(key_id: str | None) -> bool:
    if not key_id:
        return False
    if _is_test_key(key_id):
        return True
    if not key_id.startswith("rzp_live_"):
        # Unknown shape — refuse without explicit opt-in.
        return os.getenv(_LIVE_MODE_OPT_IN_ENV, "").lower() in {"1", "true", "yes"}
    return os.getenv(_LIVE_MODE_OPT_IN_ENV, "").lower() in {"1", "true", "yes"}


def _refusal(request: ProviderRequest, *, error: str) -> ProviderResponse:  # noqa: ARG001
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
            f"provider `razorpay-payments` does not support capability: {requested}"
        )
