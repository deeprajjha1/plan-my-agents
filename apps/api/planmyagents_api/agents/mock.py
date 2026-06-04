"""Mock providers used by tests and local benchmark development."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse


@dataclass(frozen=True)
class MockEmailVerifier:
    """Deterministic email-verification provider.

    The provider intentionally has simple, inspectable behavior so tests can
    validate scoring and reporting without external API keys.
    """

    provider_id: str = "mock-email-verifier"
    capabilities: list[str] = None  # type: ignore[assignment]
    default_latency_ms: int = 25
    unit_cost_usd: float = 0.001
    dishonest: bool = False

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["email_verification"])

    async def estimate_cost(self, request: ProviderRequest) -> float:
        _ensure_capability(self.capabilities, request.capability)
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return True

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        await asyncio.sleep(0)

        email = str(request.inputs.get("email", "")).lower().strip()
        local_part, _, domain = email.partition("@")
        syntactically_valid = bool(local_part) and bool(domain) and "." in domain

        if self.dishonest:
            result = "deliverable"
            score = 90
        elif not syntactically_valid:
            result = "undeliverable"
            score = 0
        elif domain.endswith("invalid.example") or email.startswith("missing@"):
            result = "undeliverable"
            score = 5
        elif "catchall" in email or email.endswith("@catchall.example"):
            result = "risky"
            score = 55
        elif "unknown" in email:
            result = "unknown"
            score = 40
        elif "@" in email and "." in email.rsplit("@", 1)[-1]:
            result = "deliverable"
            score = 95
        else:
            result = "undeliverable"
            score = 0

        return ProviderResponse(
            succeeded=True,
            output={
                "email": email,
                "result": result,
                "score": score,
                "status": "ok",
            },
            cost_usd=self.unit_cost_usd,
            latency_ms=self.default_latency_ms,
            raw_response={"mock": True},
        )


@dataclass(frozen=True)
class MockContactEnricher:
    """Deterministic contact-enrichment provider for tests."""

    provider_id: str = "mock-contact-enricher"
    capabilities: list[str] = None  # type: ignore[assignment]
    default_latency_ms: int = 50
    unit_cost_usd: float = 0.01

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["contact_enrichment"])

    async def estimate_cost(self, request: ProviderRequest) -> float:
        _ensure_capability(self.capabilities, request.capability)
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return True

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        await asyncio.sleep(0)

        first = str(request.inputs.get("first_name", "")).strip()
        last = str(request.inputs.get("last_name", "")).strip()
        domain = str(request.inputs.get("company_domain", "")).lower().strip()

        if "nonexistent" in domain or first.lower().startswith("bartholomew"):
            output = {"status": "not_found", "found": False, "confidence": 0.99}
        else:
            slug = f"{first}.{last}".strip(".").replace(" ", ".").lower()
            output = {
                "status": "ok",
                "found": True,
                "first_name": first,
                "last_name": last,
                "title": request.inputs.get("expected_title", "CTO"),
                "email": f"{slug}@{domain}",
                "linkedin_url": f"https://www.linkedin.com/in/{slug}",
                "confidence": 0.86,
            }

        return ProviderResponse(
            succeeded=True,
            output=output,
            cost_usd=self.unit_cost_usd,
            latency_ms=self.default_latency_ms,
            raw_response={"mock": True},
        )


@dataclass(frozen=True)
class MockStripePaymentAuthorization:
    """Deterministic Stripe-shaped ``payment_authorization`` provider.

    Mirrors the shape of :class:`StripePaymentAuthorization` (intent_id,
    amount_minor, currency, status, test_mode, receipt_url) so the same
    benchmark cases that grade the real wrapper grade this mock too.
    Behaviour is keyed off ``payment_method`` to match Stripe's
    documented test-card matrix:

      * ``pm_card_visa`` (and unspecified)        -> ``succeeded``
      * ``pm_card_chargeDeclined``                 -> ``requires_payment_method``
      * ``pm_card_chargeDeclinedInsufficientFunds`` -> ``requires_payment_method``
      * ``pm_card_authenticationRequired``         -> ``requires_action``
      * ``pm_card_visa_chargeDeclinedExpiredCard`` -> ``requires_payment_method``
      * any other id                               -> ``requires_payment_method``

    Used by the CI benchmark to lock the wrapper's *normalisation*
    contract independently of network access. Real Stripe behaviour
    drift is caught by the live-mode benchmark run, gated behind a
    ``sk_test_...`` key.
    """

    provider_id: str = "mock-stripe-payments"
    capabilities: list[str] = None  # type: ignore[assignment]
    default_latency_ms: int = 80
    unit_cost_usd: float = 0.32
    dishonest: bool = False

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["payment_authorization"])

    async def estimate_cost(self, request: ProviderRequest) -> float:
        _ensure_capability(self.capabilities, request.capability)
        amount = float(request.inputs.get("amount") or 0.0)
        currency = str(request.inputs.get("currency") or "usd").lower()
        if amount > 0 and currency in {"usd", "eur", "gbp", "cad", "aud", "inr"}:
            return round(amount * 0.029 + 0.30, 4)
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return True

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        await asyncio.sleep(0)

        amount = float(request.inputs.get("amount") or 0.0)
        currency = str(request.inputs.get("currency") or "usd").lower()
        amount_minor = (
            int(round(amount)) if currency == "jpy" else int(round(amount * 100))
        )
        payment_method = str(request.inputs.get("payment_method") or "pm_card_visa")

        if self.dishonest:
            # Useful for "bad provider scores worse than good provider"
            # regression tests.
            status = "succeeded"
        elif payment_method == "pm_card_authenticationRequired":
            status = "requires_action"
        elif payment_method.startswith("pm_card_chargeDeclined") or "Declined" in payment_method:
            status = "requires_payment_method"
        elif payment_method == "pm_card_visa" or payment_method == "":
            status = "succeeded"
        else:
            status = "requires_payment_method"

        output = {
            "intent_id": f"pi_3MOCK{abs(hash((payment_method, amount_minor, currency))) % (10 ** 16):016d}",
            "amount_minor": amount_minor,
            "currency": currency,
            "status": status,
            "test_mode": True,
            "receipt_url": (
                f"https://stripe.com/receipts/mock/{payment_method}"
                if status == "succeeded"
                else None
            ),
        }
        # Cost recorded as actual fee only on success — matches the
        # real adapter's behaviour.
        cost = round(amount * 0.029 + 0.30, 4) if status == "succeeded" else 0.0
        # `succeeded=True` reflects wrapper success (we got an
        # authoritative answer from "Stripe"); business success
        # is carried by output.status. See StripePaymentAuthorization
        # for the same split.
        return ProviderResponse(
            succeeded=True,
            output=output,
            cost_usd=cost,
            latency_ms=self.default_latency_ms,
            error=None,
            raw_response={"mock": True, "status": status},
        )


@dataclass(frozen=True)
class MockRazorpayPaymentAuthorization:
    """Deterministic Razorpay-shaped ``payment_authorization`` provider.

    Mirrors :class:`RazorpayPaymentAuthorization` (order_id,
    amount_minor, currency, status, test_mode, receipt). Behaviour
    is keyed off ``simulate``:

      * unspecified or ``"created"`` -> ``status="created"`` (the
        wrapper-level success state for Order Create)
      * ``"failed"``                  -> error response (wrapper
        succeeded=False)
      * ``"attempted"``               -> ``status="attempted"``
      * ``"paid"``                    -> ``status="paid"``

    Razorpay does NOT expose a card-level test matrix at the Order
    Create step (test cards apply at the Checkout payment step,
    which is browser-driven). So our mock keys off an explicit
    ``simulate`` input rather than payment_method.
    """

    provider_id: str = "mock-razorpay-payments"
    capabilities: list[str] = None  # type: ignore[assignment]
    default_latency_ms: int = 60
    unit_cost_usd: float = 0.0
    dishonest: bool = False

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["payment_authorization"])

    async def estimate_cost(self, request: ProviderRequest) -> float:
        _ensure_capability(self.capabilities, request.capability)
        amount = float(request.inputs.get("amount") or 0.0)
        currency = str(request.inputs.get("currency") or "inr").lower()
        if amount > 0 and currency == "inr":
            return round(amount * 0.0236 / 83.0, 4)
        if amount > 0:
            return round(amount * 0.03, 4)
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return True

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        await asyncio.sleep(0)

        amount = float(request.inputs.get("amount") or 0.0)
        currency = str(request.inputs.get("currency") or "INR").upper()
        amount_minor = int(round(amount * 100))
        simulate = str(request.inputs.get("simulate") or "created").lower()

        if self.dishonest:
            simulate = "paid"  # Always claim full success; benchmark catches this.

        if simulate == "failed":
            # Wrapper failure: mirrors Razorpay's error envelope,
            # used to test the wrapper's error path scoring.
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=self.default_latency_ms,
                error="razorpay_error[BAD_REQUEST_ERROR]: simulated failure",
                raw_response={"mock": True, "error": True},
            )

        status = simulate if simulate in {"created", "attempted", "paid"} else "created"
        order_id = (
            f"order_MOCK{abs(hash((status, amount_minor, currency))) % (10 ** 14):014d}"
        )
        receipt_value = str(request.inputs.get("receipt") or "")[:40] or None
        output = {
            "order_id": order_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "status": status,
            "test_mode": True,
            "receipt": receipt_value,
        }
        return ProviderResponse(
            succeeded=True,
            output=output,
            cost_usd=0.0,
            latency_ms=self.default_latency_ms,
            error=None,
            raw_response={"mock": True, "status": status},
        )


@dataclass(frozen=True)
class MockResendEmailSender:
    """Deterministic Resend-shaped ``email_send`` provider.

    Mirrors :class:`ResendEmailSender` (email_id, from, to,
    subject, status, test_mode). Status keys off the recipient:

      * ``delivered@resend.dev`` and any other resend.dev test
        address -> ``status="queued"`` (Resend's authoritative
        accept-for-delivery state)
      * ``bad@invalid.example`` and similar synthetically-broken
        addresses -> wrapper-success=False (transport error)
      * any other recipient (real-shape) -> ``status="queued"``
        if real_domains_allowed semantics permit (mocked permissive)
    """

    provider_id: str = "mock-resend-emails"
    capabilities: list[str] = None  # type: ignore[assignment]
    default_latency_ms: int = 90
    unit_cost_usd: float = 0.0004
    dishonest: bool = False

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["email_send"])

    async def estimate_cost(self, request: ProviderRequest) -> float:
        _ensure_capability(self.capabilities, request.capability)
        recipients = request.inputs.get("to") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        return round(self.unit_cost_usd * max(len(recipients), 1), 6)

    async def health_check(self) -> bool:
        return True

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        await asyncio.sleep(0)

        recipients = request.inputs.get("to") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        recipients = [r for r in recipients if r]

        subject = str(request.inputs.get("subject") or "").strip()
        from_address = str(request.inputs.get("from") or "onboarding@resend.dev")

        if not recipients or not subject:
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=self.default_latency_ms,
                error="missing recipients or subject",
                raw_response={"mock": True},
            )

        # Synthetic invalid recipient -> wrapper returns failure.
        if any("@invalid.example" in r for r in recipients):
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=self.default_latency_ms,
                error="resend_error[validation_error]: invalid recipient",
                raw_response={"mock": True},
            )

        if self.dishonest:
            # Always claim a fake id even on degenerate input.
            email_id = "mock-fake-id-not-from-resend"
        else:
            seed = abs(hash((from_address, tuple(recipients), subject)))
            email_id = f"{seed % (10 ** 8):08x}-mock-resend"

        is_test = (
            from_address.endswith("@resend.dev")
            and all(r.endswith("@resend.dev") for r in recipients)
        )
        output = {
            "email_id": email_id,
            "from": from_address,
            "to": recipients,
            "subject": subject,
            "status": "queued",
            "test_mode": is_test,
        }
        cost = round(self.unit_cost_usd * len(recipients), 6)
        return ProviderResponse(
            succeeded=True,
            output=output,
            cost_usd=cost,
            latency_ms=self.default_latency_ms,
            error=None,
            raw_response={"mock": True},
        )


@dataclass(frozen=True)
class MockFirecrawlScraper:
    """Deterministic Firecrawl-shaped ``web_scraping`` provider.

    Mirrors :class:`FirecrawlScraper` (url, title, markdown,
    char_count, status="scraped", test_mode). Behaviour keys
    off the URL host:

      * ``example.com`` -> short stable markdown (~150 chars)
        matching what real Firecrawl returns for the RFC-2606
        reserved domain.
      * ``firecrawl.dev`` and content-rich URLs -> longer
        markdown (~1500 chars).
      * any URL with ``/empty`` -> succeeds with empty markdown
        (the "page exists but has no content" path).
      * any URL with ``/error`` or ``/blocked`` -> wrapper
        succeeded=False (transport failure shape).
    """

    provider_id: str = "mock-firecrawl"
    capabilities: list[str] = None  # type: ignore[assignment]
    default_latency_ms: int = 250
    unit_cost_usd: float = 0.002
    dishonest: bool = False

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["web_scraping"])

    async def estimate_cost(self, request: ProviderRequest) -> float:
        _ensure_capability(self.capabilities, request.capability)
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return True

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        await asyncio.sleep(0)

        url = str(request.inputs.get("url") or "").strip()
        if not url:
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=self.default_latency_ms,
                error="url_required",
                raw_response={"mock": True},
            )

        if "/error" in url or "/blocked" in url:
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=self.default_latency_ms,
                error="firecrawl_error: target unreachable",
                raw_response={"mock": True},
            )

        # /empty wins over example.com so a URL like
        # https://example.com/empty correctly returns empty
        # markdown (the adversarial honesty case).
        if "/empty" in url:
            markdown = ""
            link_count = 0
            title = "Empty page"
        elif "example.com" in url or "example.org" in url or "example.net" in url:
            markdown = (
                "Example Domain. This domain is for use in illustrative examples "
                "in documents. You may use this domain in literature without prior "
                "coordination or asking for permission."
            )
            link_count = 1
            title = "Example Domain"
        else:
            markdown = (
                "Sample content for benchmark. " * 60
            ).strip()
            link_count = 5
            title = "Sample Page"

        # Dishonest mock: claim 1000+ chars for any URL — including
        # empty pages and error URLs. Caught by the empty-page
        # case which expects char_count==0.
        if self.dishonest:
            markdown = ("hallucinated content " * 60).strip()
            link_count = 99
            title = "Fake Title"

        # Share the same RFC-2606 / RFC-6761 detection as the real
        # wrapper, but via the firewall-clean utility module so this
        # routing-side file never has to import from
        # ``benchmark.baselines.*`` (audited by
        # ``scripts/audit_baseline_firewall.py``).
        from planmyagents_api.test_domains import is_reserved_test_url

        is_test = is_reserved_test_url(url)
        preview = markdown[:500] + ("…" if len(markdown) > 500 else "")
        output = {
            "url": url,
            "title": title,
            "description": "",
            "markdown": markdown,
            "markdown_preview": preview,
            "char_count": len(markdown),
            "html_present": True,
            "link_count": link_count,
            "status": "scraped",
            "test_mode": is_test,
        }
        return ProviderResponse(
            succeeded=True,
            output=output,
            cost_usd=self.unit_cost_usd,
            latency_ms=self.default_latency_ms,
            error=None,
            raw_response={"mock": True},
        )


@dataclass(frozen=True)
class MockShippoQuoteFetcher:
    """Deterministic Shippo-shaped ``shipping_quote`` provider.

    Mirrors :class:`ShippoQuoteFetcher` (shipment_id, rates,
    cheapest_rate, status="quoted", test_mode). Returns three
    canned rates (USPS, UPS, FedEx) for a valid request, with
    deterministic prices so benchmark grading is stable.
    """

    provider_id: str = "mock-shippo-shipping"
    capabilities: list[str] = None  # type: ignore[assignment]
    default_latency_ms: int = 350
    unit_cost_usd: float = 0.0
    dishonest: bool = False

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["shipping_quote"])

    async def estimate_cost(self, request: ProviderRequest) -> float:  # noqa: ARG002
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return True

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        await asyncio.sleep(0)

        address_from = request.inputs.get("address_from") or {}
        address_to = request.inputs.get("address_to") or {}
        parcel = request.inputs.get("parcel") or {}

        if not (address_from.get("country") and address_to.get("country")):
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=self.default_latency_ms,
                error="missing address country",
                raw_response={"mock": True},
            )

        try:
            weight = float(parcel.get("weight") or 0)
        except (TypeError, ValueError):
            weight = 0.0
        if weight <= 0:
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=self.default_latency_ms,
                error="parcel weight must be > 0",
                raw_response={"mock": True},
            )

        if self.dishonest:
            # Always return zero rates with a hallucinated cheapest
            # rate — caught by tests asserting cheapest > 0.
            output = {
                "shipment_id": "shp_dishonest",
                "origin_country": str(address_from.get("country")),
                "destination_country": str(address_to.get("country")),
                "rate_count": 0,
                "rates": [],
                "cheapest_rate": {
                    "rate_id": "fake",
                    "carrier": "DishonestCarrier",
                    "service": "Hallucinated",
                    "amount": 999.0,
                    "currency": "USD",
                    "days_estimated": 0,
                },
                "status": "quoted",
                "test_mode": True,
            }
            return ProviderResponse(
                succeeded=True,
                output=output,
                cost_usd=0.0,
                latency_ms=self.default_latency_ms,
                error=None,
                raw_response={"mock": True},
            )

        # Deterministic rates derived from weight so different
        # parcels produce different prices (catches wrappers that
        # ignore the parcel input).
        base = max(weight * 1.5, 5.0)
        rates = [
            {
                "rate_id": f"rate_usps_{int(base * 100)}",
                "carrier": "USPS",
                "service": "Priority Mail",
                "amount": round(base + 2.0, 2),
                "currency": "USD",
                "days_estimated": 3,
            },
            {
                "rate_id": f"rate_ups_{int(base * 100)}",
                "carrier": "UPS",
                "service": "Ground",
                "amount": round(base + 4.5, 2),
                "currency": "USD",
                "days_estimated": 2,
            },
            {
                "rate_id": f"rate_fedex_{int(base * 100)}",
                "carrier": "FedEx",
                "service": "Express Saver",
                "amount": round(base + 7.0, 2),
                "currency": "USD",
                "days_estimated": 2,
            },
        ]
        cheapest = min(rates, key=lambda r: r["amount"])
        output = {
            "shipment_id": f"shp_mock_{int(base * 100)}",
            "origin_country": str(address_from.get("country")),
            "destination_country": str(address_to.get("country")),
            "rate_count": len(rates),
            "rates": rates,
            "cheapest_rate": cheapest,
            "status": "quoted",
            "test_mode": True,
        }
        return ProviderResponse(
            succeeded=True,
            output=output,
            cost_usd=0.0,
            latency_ms=self.default_latency_ms,
            error=None,
            raw_response={"mock": True},
        )


@dataclass(frozen=True)
class MockEbayBrowseProvider:
    """Deterministic eBay-shaped ``price_comparison`` provider.

    Mirrors :class:`EbayBrowseProvider`. Returns 5 deterministic
    listings keyed off the query, with prices spread across a
    range so min/avg/max are meaningful. A query containing
    ``"nothing-matches"`` returns zero results (the honest
    no_results path).
    """

    provider_id: str = "mock-ebay-browse"
    capabilities: list[str] = None  # type: ignore[assignment]
    default_latency_ms: int = 280
    unit_cost_usd: float = 0.0
    dishonest: bool = False

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["price_comparison"])

    async def estimate_cost(self, request: ProviderRequest) -> float:  # noqa: ARG002
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return True

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        await asyncio.sleep(0)

        query = str(request.inputs.get("query") or "").strip()
        if not query:
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=self.default_latency_ms,
                error="query_required",
                raw_response={"mock": True},
            )

        # Honest no-results path: a wrapper that hallucinates
        # listings here would fail the no_results case.
        if "nothing-matches" in query.lower() and not self.dishonest:
            output = {
                "query": query,
                "result_count": 0,
                "items": [],
                "min_price": None,
                "max_price": None,
                "average_price": 0.0,
                "status": "no_results",
                "test_mode": True,
            }
            return ProviderResponse(
                succeeded=True,
                output=output,
                cost_usd=0.0,
                latency_ms=self.default_latency_ms,
                error=None,
                raw_response={"mock": True},
            )

        seed = abs(hash(query)) % 1000 + 10
        items = []
        for i in range(5):
            price = round(seed + (i * 7.5), 2)
            items.append({
                "item_id": f"v1|mock-{seed}-{i}|0",
                "title": f"{query} — Listing {i + 1}",
                "price": price,
                "currency": "USD",
                "condition": "NEW" if i % 2 == 0 else "USED",
                "url": f"https://sandbox.ebay.com/itm/mock-{seed}-{i}",
                "seller": f"mock_seller_{i}",
                "image_url": "",
                "shipping_cost": round(i * 2.0, 2),
            })
        priced = [i for i in items if i["price"] > 0]
        avg_price = round(sum(i["price"] for i in priced) / len(priced), 2)
        min_item = min(priced, key=lambda i: i["price"])
        max_item = max(priced, key=lambda i: i["price"])

        if self.dishonest:
            # Hallucinate an unrealistic high min price so cap
            # math and downstream comparisons mislead. Caught by
            # tests asserting min < max.
            min_item = max_item

        output = {
            "query": query,
            "result_count": len(items),
            "items": items,
            "min_price": min_item,
            "max_price": max_item,
            "average_price": avg_price,
            "status": "found",
            "test_mode": True,
        }
        return ProviderResponse(
            succeeded=True,
            output=output,
            cost_usd=0.0,
            latency_ms=self.default_latency_ms,
            error=None,
            raw_response={"mock": True},
        )


def _ensure_capability(capabilities: list[str], requested: str) -> None:
    if requested not in capabilities:
        raise ValueError(f"provider does not support capability: {requested}")
