"""Shippo shipping_quote benchmark baseline.

BENCHMARK-ONLY. Per the 16-May-2026 product spec this adapter is
NOT reachable from the customer ``/goal`` execution path. It exists
to give the benchmark runner a known-good shipping_quote baseline
against which discovered MCP / A2A / OpenAPI shipping providers
can be scored. See ``benchmark/baselines/__init__.py`` for the
firewall rules.

Sprint 3a (S3a-4): fifth commodity-API wrapper.

Why Shippo:
    * Open signup globally with instant test API tokens
      (prefixed ``shippo_test_``).
    * Test-mode API uses synthetic carrier accounts so a benchmark
      run never books a real shipment or charges a real label.
    * One POST returns a list of rates from multiple carriers
      (USPS, UPS, FedEx, DHL...) — the wrapper picks the cheapest
      and surfaces the full list for downstream comparison.
    * Already in the curated registry under
      ``shippo-shipping``.

Wrapper safety model:
    Shipping has lower abuse vectors than payments (worst case:
    you generate spam labels), but the wrapper still enforces:

    1. Test mode by default — refuses ``shippo_live_`` tokens
       without ``SHIPPO_ALLOW_LIVE_MODE=true``.
    2. Pre-call refusal on missing address country code (a
       common bug-class is sending [object Object] addresses).
    3. Refusal on parcels with zero/negative dimensions or
       weight (Shippo would reject anyway; we save the round-trip).

The wrapper models the *quote* step (POST /shipments/ with
``async=false`` returns rates synchronously). Buying a label is
a separate POST /transactions/ — out of scope for v1.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse

ShippoTransport = Callable[[str, bytes, dict[str, str], float], dict[str, Any]]


class ShippoConfigurationError(RuntimeError):
    """Raised when Shippo is invoked without an API token, or
    when a live token is presented without explicit opt-in."""


_DEFAULT_API_BASE = "https://api.goshippo.com"
_LIVE_MODE_OPT_IN_ENV = "SHIPPO_ALLOW_LIVE_MODE"
# Per-quote cost: Shippo's documented rate-lookup is free even on
# the paid tier; charges happen only when buying a label. Use $0
# for the cap projection so quote-only flows don't count against
# the goal budget.
_PER_QUOTE_USD = 0.0
_REQUIRED_ADDRESS_FIELDS = ("country",)


@dataclass(frozen=True)
class ShippoQuoteFetcher:
    """Shippo ``shipping_quote`` adapter (POST /shipments/)."""

    api_token: str | None = None
    transport: ShippoTransport | None = None
    timeout_seconds: float = 30.0
    api_base_url: str = _DEFAULT_API_BASE
    provider_id: str = "shippo-shipping"
    capabilities: list[str] = None  # type: ignore[assignment]
    unit_cost_usd: float = _PER_QUOTE_USD

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["shipping_quote"])
        if self.api_token is None:
            object.__setattr__(self, "api_token", os.getenv("SHIPPO_API_TOKEN"))

    # ---- ProviderAdapter contract -----------------------------------

    async def estimate_cost(self, request: ProviderRequest) -> float:  # noqa: ARG002
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return bool(self.api_token)

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not self.api_token:
            raise ShippoConfigurationError(
                "SHIPPO_API_TOKEN is required for provider `shippo-shipping`."
            )
        if not _live_mode_allowed(self.api_token):
            raise ShippoConfigurationError(
                "Refusing to call Shippo in LIVE mode without explicit "
                f"{_LIVE_MODE_OPT_IN_ENV}=true opt-in. Use a shippo_test_... "
                "token for Sprint 3a wrapper validation."
            )

        address_from = request.inputs.get("address_from")
        address_to = request.inputs.get("address_to")
        parcel = request.inputs.get("parcel")

        from_refusal = _validate_address(address_from, label="address_from")
        if from_refusal is not None:
            return from_refusal
        to_refusal = _validate_address(address_to, label="address_to")
        if to_refusal is not None:
            return to_refusal
        parcel_refusal = _validate_parcel(parcel)
        if parcel_refusal is not None:
            return parcel_refusal

        # Type-narrowing: validators returned None, so all three
        # inputs are present and dict-shaped.
        assert isinstance(address_from, dict)
        assert isinstance(address_to, dict)
        assert isinstance(parcel, dict)

        body_payload: dict[str, Any] = {
            "address_from": _normalize_address(address_from),
            "address_to": _normalize_address(address_to),
            "parcels": [_normalize_parcel(parcel)],
            # Synchronous mode means rates come back in the response
            # body (no polling). Required for a single-call wrapper.
            "async": False,
        }
        carriers = request.inputs.get("carriers")
        if isinstance(carriers, list) and carriers:
            body_payload["carrier_accounts"] = carriers

        body = json.dumps(body_payload).encode("utf-8")
        endpoint = f"{self.api_base_url}/shipments/"
        headers = _shippo_headers(
            self.api_token, idempotency_key=request.idempotency_key
        )

        start = time.perf_counter()
        try:
            payload = (self.transport or _default_transport)(
                endpoint, body, headers, self.timeout_seconds
            )
            latency_ms = int((time.perf_counter() - start) * 1000)

            # Shippo error envelope: ``detail`` field, no
            # ``object_id``. We treat any response without a
            # shipment id as a wrapper-level failure.
            if not payload.get("object_id") and (
                payload.get("detail") or payload.get("error") or payload.get("messages")
            ):
                return ProviderResponse(
                    succeeded=False,
                    output=None,
                    cost_usd=0.0,
                    latency_ms=latency_ms,
                    error=_shippo_error_string(payload),
                    raw_response=payload,
                )

            normalized = _normalize_shipment(
                payload, test_mode=_is_test_token(self.api_token or "")
            )
            return ProviderResponse(
                succeeded=True,
                output=normalized,
                cost_usd=0.0,  # Quote step is free.
                latency_ms=latency_ms,
                error=None,
                raw_response=payload,
            )
        except ShippoConfigurationError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalise transport failures
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


def _shippo_headers(api_token: str, *, idempotency_key: str) -> dict[str, str]:
    headers = {
        "Authorization": f"ShippoToken {api_token}",
        "Content-Type": "application/json",
        "User-Agent": "PlanMyAgentsBenchmark/0.1 shippo-adapter",
    }
    if idempotency_key:
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
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001 - error.read can fail
            body_text = ""
        try:
            return json.loads(body_text) if body_text else {"detail": str(exc)}
        except json.JSONDecodeError:
            return {"detail": str(exc), "raw": body_text}
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("Shippo response was not a JSON object")
    return payload


def _normalize_address(addr: dict[str, Any]) -> dict[str, str]:
    """Coerce all address values to strings (Shippo's contract)."""

    out: dict[str, str] = {}
    for key in (
        "name", "company", "street1", "street2", "street3",
        "city", "state", "zip", "country", "phone", "email",
    ):
        value = addr.get(key)
        if value is not None and value != "":
            out[key] = str(value)
    return out


def _normalize_parcel(parcel: dict[str, Any]) -> dict[str, str]:
    return {
        "length": str(parcel.get("length", "5")),
        "width": str(parcel.get("width", "5")),
        "height": str(parcel.get("height", "5")),
        "distance_unit": str(parcel.get("distance_unit", "in")),
        "weight": str(parcel.get("weight", "1")),
        "mass_unit": str(parcel.get("mass_unit", "lb")),
    }


def _normalize_shipment(
    payload: dict[str, Any], *, test_mode: bool
) -> dict[str, Any]:
    rates = payload.get("rates") or []
    if not isinstance(rates, list):
        rates = []
    normalised_rates = []
    for rate in rates:
        if not isinstance(rate, dict):
            continue
        normalised_rates.append({
            "rate_id": str(rate.get("object_id") or ""),
            "carrier": str(rate.get("provider") or ""),
            "service": str(rate.get("servicelevel", {}).get("name") or rate.get("servicelevel_name") or ""),
            "amount": float(rate.get("amount") or 0.0),
            "currency": str(rate.get("currency") or "USD"),
            "days_estimated": _safe_int(rate.get("estimated_days")),
        })

    cheapest = min(
        (r for r in normalised_rates if r["amount"] > 0),
        key=lambda r: r["amount"],
        default=None,
    )

    address_from = payload.get("address_from") or {}
    address_to = payload.get("address_to") or {}
    return {
        "shipment_id": str(payload.get("object_id") or ""),
        "origin_country": str(address_from.get("country") or ""),
        "destination_country": str(address_to.get("country") or ""),
        "rate_count": len(normalised_rates),
        "rates": normalised_rates,
        "cheapest_rate": cheapest,
        "status": "quoted",
        "test_mode": test_mode,
    }


def _shippo_error_string(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    if isinstance(detail, str):
        return f"shippo_error: {detail}"
    error = payload.get("error")
    if isinstance(error, str):
        return f"shippo_error: {error}"
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            return f"shippo_error: {first.get('text') or first.get('source') or 'quote_failed'}"
    return "shippo_error: quote_failed"


def _validate_address(addr: Any, *, label: str) -> ProviderResponse | None:
    if not isinstance(addr, dict):
        return _refusal(error=f"{label}_required: must be an object with at least a country code")
    for field in _REQUIRED_ADDRESS_FIELDS:
        if not str(addr.get(field) or "").strip():
            return _refusal(error=f"{label}_missing_field:{field}")
    return None


def _validate_parcel(parcel: Any) -> ProviderResponse | None:
    if not isinstance(parcel, dict):
        return _refusal(
            error="parcel_required: must be an object with length/width/height/weight"
        )
    for field in ("length", "width", "height", "weight"):
        try:
            value = float(parcel.get(field) or 0)
        except (TypeError, ValueError):
            return _refusal(error=f"parcel_field_invalid:{field}")
        if value <= 0:
            return _refusal(error=f"parcel_field_must_be_positive:{field}")
    return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_test_token(token: str) -> bool:
    return bool(token) and token.startswith("shippo_test_")


def _live_mode_allowed(token: str | None) -> bool:
    if not token:
        return False
    if _is_test_token(token):
        return True
    if not token.startswith("shippo_live_"):
        return os.getenv(_LIVE_MODE_OPT_IN_ENV, "").lower() in {"1", "true", "yes"}
    return os.getenv(_LIVE_MODE_OPT_IN_ENV, "").lower() in {"1", "true", "yes"}


def _refusal(*, error: str) -> ProviderResponse:
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
            f"provider `shippo-shipping` does not support capability: {requested}"
        )
