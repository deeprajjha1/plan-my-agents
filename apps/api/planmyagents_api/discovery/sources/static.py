"""Static discovery source used as the first production-safe connector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate

STATIC_DISCOVERY_CATALOG: list[dict[str, Any]] = [
    {
        "id": "duffel",
        "display_name": "Duffel",
        "vendor": "Duffel",
        "vendor_url": "https://duffel.com",
        "provider_type": "api_provider",
        "capabilities": [
            {
                "id": "travel_search",
                "confidence": 0.85,
                "notes": "Flight offer search API candidate.",
            },
            {
                "id": "fare_comparison",
                "confidence": 0.8,
                "notes": "Compare returned flight offers.",
            },
            {
                "id": "booking_execution",
                "confidence": 0.8,
                "notes": "Order creation flow candidate.",
            },
        ],
        "required_env_vars": ["DUFFEL_API_TOKEN"],
    },
    {
        "id": "amadeus",
        "display_name": "Amadeus for Developers",
        "vendor": "Amadeus",
        "vendor_url": "https://developers.amadeus.com",
        "provider_type": "api_provider",
        "capabilities": [
            {
                "id": "travel_search",
                "confidence": 0.9,
                "notes": "Flight Offers Search API candidate.",
            },
            {
                "id": "fare_comparison",
                "confidence": 0.85,
                "notes": "Fare and offer comparison candidate.",
            },
        ],
        "required_env_vars": ["AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET"],
    },
    {
        "id": "stripe-payments",
        "display_name": "Stripe Payments",
        "vendor": "Stripe",
        "vendor_url": "https://stripe.com",
        "provider_type": "payment_provider",
        "capabilities": [
            {
                "id": "payment_authorization",
                "confidence": 0.95,
                "notes": "PaymentIntent authorization flow.",
            }
        ],
        "required_env_vars": ["STRIPE_SECRET_KEY"],
    },
    {
        "id": "easyship",
        "display_name": "Easyship",
        "vendor": "Easyship",
        "vendor_url": "https://www.easyship.com",
        "provider_type": "api_provider",
        "capabilities": [
            {
                "id": "shipping_quote",
                "confidence": 0.75,
                "notes": "Cross-carrier shipping quote candidate.",
            },
            {
                "id": "cross_border_commerce",
                "confidence": 0.65,
                "notes": "Duties/taxes and courier options candidate.",
            },
        ],
        "required_env_vars": ["EASYSHIP_API_KEY"],
    },
]


@dataclass(frozen=True)
class StaticDiscoverySource:
    """Curated source connector.

    This is intentionally small and deterministic. It exercises the same source
    interface that MCP/A2A/web connectors will use later.
    """

    raw_candidates: list[dict[str, Any]] | None = None
    source_id: str = "static_seed"
    goal_hash: str = ""

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        candidates: list[DiscoveryCandidate] = []
        raw_candidates = (
            STATIC_DISCOVERY_CATALOG if self.raw_candidates is None else self.raw_candidates
        )
        for raw in raw_candidates:
            raw_capabilities = {
                str(item.get("id"))
                for item in raw.get("capabilities", [])
                if isinstance(item, dict)
            }
            if capabilities and raw_capabilities.isdisjoint(capabilities):
                continue
            try:
                candidates.append(
                    normalize_candidate(
                        raw,
                        source=self.source_id,
                        requested_capabilities=sorted(capabilities),
                        goal_hash=self.goal_hash,
                    )
                )
            except CandidateNormalizationError:
                continue
        return candidates
