"""eBay Browse API price_comparison benchmark baseline.

BENCHMARK-ONLY. Per the 16-May-2026 product spec this adapter is
NOT reachable from the customer ``/goal`` execution path. It exists
to give the benchmark runner a known-good price_comparison baseline
against which discovered MCP / A2A / OpenAPI marketplace providers
can be scored. See ``benchmark/baselines/__init__.py`` for the
firewall rules.

Sprint 3a (S3a-4): fifth and final commodity-API wrapper.

Why eBay Browse:
    * Open developer signup at developer.ebay.com — instant
      access to the Sandbox environment without business
      verification.
    * The Browse API returns multiple competing listings for a
      query, which is exactly the price_comparison shape: many
      sellers, many prices, surface min/avg/max.
    * Bearer-token authenticated REST GET — single-call wrapper.
    * Already in the curated registry under ``ebay-buy-api``.

Wrapper safety model:
    Read-only API (no buy / bid / payment side-effects), so the
    risk surface is limited. The wrapper still:

    1. Defaults to the SANDBOX endpoint and refuses production
       endpoints unless ``EBAY_ALLOW_PRODUCTION=true`` is set
       — protects against accidental production-rate API spend
       on shared dev keys.
    2. Validates query length (must be non-empty, <= 350 chars
       — eBay's documented cap).
    3. Caps ``limit`` at 50 (eBay's documented page max) so a
       caller can't accidentally request 10,000 items.

The wrapper takes a pre-fetched OAuth Application Access Token
via ``EBAY_OAUTH_TOKEN``. Token fetching (the
client_credentials grant) is intentionally out of scope —
eBay tokens are 2-hour-lived and the operator refreshes them
out-of-band. Adding the token-fetch flow here would double the
wrapper's complexity without buying anything for the benchmark.
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

EbayTransport = Callable[[str, dict[str, str], float], dict[str, Any]]


class EbayConfigurationError(RuntimeError):
    """Raised when eBay is invoked without an OAuth token, or
    when the production endpoint is targeted without explicit
    opt-in."""


_DEFAULT_API_BASE_SANDBOX = "https://api.sandbox.ebay.com/buy/browse/v1"
_DEFAULT_API_BASE_PRODUCTION = "https://api.ebay.com/buy/browse/v1"
_PRODUCTION_OPT_IN_ENV = "EBAY_ALLOW_PRODUCTION"
_PER_CALL_USD = 0.0  # eBay Browse is free under their developer terms.
_MAX_QUERY_LENGTH = 350
_MAX_LIMIT = 50


@dataclass(frozen=True)
class EbayBrowseProvider:
    """eBay Browse ``price_comparison`` adapter (GET /item_summary/search)."""

    oauth_token: str | None = None
    transport: EbayTransport | None = None
    timeout_seconds: float = 30.0
    api_base_url: str | None = None  # None -> sandbox by default.
    provider_id: str = "ebay-browse"
    capabilities: list[str] = None  # type: ignore[assignment]
    unit_cost_usd: float = _PER_CALL_USD

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["price_comparison"])
        if self.oauth_token is None:
            object.__setattr__(self, "oauth_token", os.getenv("EBAY_OAUTH_TOKEN"))
        if self.api_base_url is None:
            # Default to sandbox; the operator opts into production
            # endpoint by either passing it explicitly or setting
            # the env var below to enable production-shaped routing.
            base_url = (
                _DEFAULT_API_BASE_PRODUCTION
                if _production_allowed()
                else _DEFAULT_API_BASE_SANDBOX
            )
            object.__setattr__(self, "api_base_url", base_url)

    # ---- ProviderAdapter contract -----------------------------------

    async def estimate_cost(self, request: ProviderRequest) -> float:  # noqa: ARG002
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return bool(self.oauth_token)

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not self.oauth_token:
            raise EbayConfigurationError(
                "EBAY_OAUTH_TOKEN is required for provider `ebay-browse`. "
                "Fetch one via the eBay developer portal (Get an Application "
                "Access Token, client_credentials grant)."
            )
        if self.api_base_url == _DEFAULT_API_BASE_PRODUCTION and not _production_allowed():
            raise EbayConfigurationError(
                "Refusing to call eBay PRODUCTION endpoint without explicit "
                f"{_PRODUCTION_OPT_IN_ENV}=true opt-in. Default sandbox URL "
                "is safe for benchmarking."
            )

        query = str(request.inputs.get("query") or "").strip()
        if not query:
            return _refusal(error="query_required: provide a non-empty `query` string")
        if len(query) > _MAX_QUERY_LENGTH:
            return _refusal(
                error=f"query_too_long: max {_MAX_QUERY_LENGTH} chars, got {len(query)}"
            )

        try:
            limit = int(request.inputs.get("limit") or 5)
        except (TypeError, ValueError):
            return _refusal(error="limit_not_numeric: must be an integer 1..50")
        limit = max(1, min(limit, _MAX_LIMIT))

        # Build the eBay-flavoured filter string. eBay uses an
        # idiosyncratic "filter" param with comma-separated key:value
        # entries (price:[20..100],conditions:{NEW}).
        filters: list[str] = []
        min_price = request.inputs.get("min_price")
        max_price = request.inputs.get("max_price")
        if min_price is not None or max_price is not None:
            min_str = "" if min_price is None else str(min_price)
            max_str = "" if max_price is None else str(max_price)
            currency = str(request.inputs.get("currency") or "USD").upper()
            filters.append(f"price:[{min_str}..{max_str}]")
            filters.append(f"priceCurrency:{currency}")
        if request.inputs.get("condition"):
            condition = str(request.inputs["condition"]).upper()
            filters.append(f"conditions:{{{condition}}}")

        params: dict[str, str] = {"q": query, "limit": str(limit)}
        if filters:
            params["filter"] = ",".join(filters)
        if request.inputs.get("category_ids"):
            params["category_ids"] = str(request.inputs["category_ids"])
        if request.inputs.get("sort"):
            params["sort"] = str(request.inputs["sort"])

        url = f"{self.api_base_url}/item_summary/search?{urllib.parse.urlencode(params)}"
        headers = _ebay_headers(self.oauth_token)

        start = time.perf_counter()
        try:
            payload = (self.transport or _default_transport)(
                url, headers, self.timeout_seconds
            )
            latency_ms = int((time.perf_counter() - start) * 1000)

            # eBay error envelope: ``errors`` array with code + message.
            # A successful empty-result page returns total=0 with no
            # ``errors`` key, which is NOT a failure — it's a wrapper
            # success with status="no_results".
            errors = payload.get("errors")
            if errors and isinstance(errors, list):
                return ProviderResponse(
                    succeeded=False,
                    output=None,
                    cost_usd=0.0,
                    latency_ms=latency_ms,
                    error=_ebay_error_string(errors),
                    raw_response=payload,
                )

            normalized = _normalize_search(
                payload,
                query=query,
                test_mode=self.api_base_url == _DEFAULT_API_BASE_SANDBOX,
            )
            return ProviderResponse(
                succeeded=True,
                output=normalized,
                cost_usd=0.0,
                latency_ms=latency_ms,
                error=None,
                raw_response=payload,
            )
        except EbayConfigurationError:
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


def _ebay_headers(oauth_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {oauth_token}",
        "Content-Type": "application/json",
        # eBay strongly recommends specifying a marketplace context;
        # EBAY_US is the broadest test-data surface in sandbox. The
        # operator can override by setting EBAY_MARKETPLACE_ID.
        "X-EBAY-C-MARKETPLACE-ID": os.getenv(
            "EBAY_MARKETPLACE_ID", "EBAY_US"
        ),
        "User-Agent": "PlanMyAgentsBenchmark/0.1 ebay-adapter",
    }


def _default_transport(
    url: str, headers: dict[str, str], timeout_seconds: float
) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
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
            return (
                json.loads(body_text) if body_text
                else {"errors": [{"message": str(exc)}]}
            )
        except json.JSONDecodeError:
            return {"errors": [{"message": str(exc), "raw": body_text}]}
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("eBay response was not a JSON object")
    return payload


def _normalize_search(
    payload: dict[str, Any], *, query: str, test_mode: bool
) -> dict[str, Any]:
    items = payload.get("itemSummaries") or []
    if not isinstance(items, list):
        items = []
    normalised_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        price_obj = item.get("price") or {}
        try:
            price_value = float(price_obj.get("value") or 0.0)
        except (TypeError, ValueError):
            price_value = 0.0
        currency = str(price_obj.get("currency") or "USD")
        seller_obj = item.get("seller") or {}
        image_obj = item.get("image") or {}
        normalised_items.append({
            "item_id": str(item.get("itemId") or ""),
            "title": str(item.get("title") or ""),
            "price": price_value,
            "currency": currency,
            "condition": str(item.get("condition") or ""),
            "url": str(item.get("itemWebUrl") or ""),
            "seller": str(seller_obj.get("username") or ""),
            "image_url": str(image_obj.get("imageUrl") or ""),
            "shipping_cost": _shipping_cost(item),
        })

    priced_items = [i for i in normalised_items if i["price"] > 0]
    if priced_items:
        min_item = min(priced_items, key=lambda i: i["price"])
        max_item = max(priced_items, key=lambda i: i["price"])
        avg_price = round(
            sum(i["price"] for i in priced_items) / len(priced_items), 2
        )
        status = "found"
    else:
        min_item = None
        max_item = None
        avg_price = 0.0
        status = "no_results"

    return {
        "query": query,
        "result_count": len(normalised_items),
        "items": normalised_items,
        "min_price": min_item,
        "max_price": max_item,
        "average_price": avg_price,
        "status": status,
        "test_mode": test_mode,
    }


def _shipping_cost(item: dict[str, Any]) -> float:
    """Extract the cheapest shipping cost from an eBay item.

    eBay returns a list of ``shippingOptions``; we pick the
    minimum so the total landed price is comparable across
    listings. Returns 0.0 when shipping data is absent (which
    eBay typically reports as "free" or seller-funded).
    """

    options = item.get("shippingOptions") or []
    if not isinstance(options, list):
        return 0.0
    costs: list[float] = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        cost_obj = opt.get("shippingCost") or {}
        try:
            cost = float(cost_obj.get("value") or 0.0)
        except (TypeError, ValueError):
            continue
        costs.append(cost)
    return min(costs) if costs else 0.0


def _ebay_error_string(errors: list[Any]) -> str:
    if not errors:
        return "ebay_error: search_failed"
    first = errors[0]
    if isinstance(first, dict):
        message = first.get("message") or first.get("longMessage") or ""
        code = first.get("errorId") or first.get("code") or ""
        if code and message:
            return f"ebay_error[{code}]: {message}"
        if message:
            return f"ebay_error: {message}"
    return "ebay_error: search_failed"


def _production_allowed() -> bool:
    return os.getenv(_PRODUCTION_OPT_IN_ENV, "").lower() in {"1", "true", "yes"}


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
            f"provider `ebay-browse` does not support capability: {requested}"
        )
