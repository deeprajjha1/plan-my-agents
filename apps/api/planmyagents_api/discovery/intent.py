"""Reusable intent signals for discovery ranking.

This is not a scenario router. It extracts broad task intent labels so ranking can
prefer candidates whose protocol/capability shape fits the request and demote
obvious mismatches.
"""

from __future__ import annotations

from dataclasses import dataclass

from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.query import ExpandedDiscoveryQuery

INTENT_TERMS: dict[str, set[str]] = {
    "public_web_research": {
        "company",
        "competitor",
        "customer",
        "domain",
        "firmographic",
        "funding",
        "market",
        "marketing",
        "research",
        "revenue",
        "seo",
        "website",
    },
    "internal_data": {
        "database",
        "db",
        "internal",
        "postgres",
        "postgresql",
        "sql",
        "sqlite",
        "warehouse",
    },
    "scheduling": {"appointment", "calendar", "reschedule", "schedule", "slot"},
    "healthcare": {"clinic", "doctor", "health", "healthcare", "hospital", "patient"},
    "travel": {"airfare", "flight", "flights", "hotel", "lodging", "ticket", "travel"},
    "contact_data": {"contact", "contacts", "cto", "email", "linkedin", "person"},
    "commerce": {"checkout", "payment", "quote", "shipping", "purchase", "refund"},
}

CANDIDATE_FIT_TERMS: dict[str, set[str]] = {
    "public_web_research": {
        "browser_automation",
        "company_data_lookup",
        "document_retrieval",
        "entity_extraction",
        "semantic_search",
        "web_fetch",
        "web_interaction",
        "web_scraping",
        "web_search",
    },
    "internal_data": {
        "database",
        "database_query",
        "postgres",
        "postgresql",
        "sql",
        "sqlite",
        "structured_data_lookup",
    },
    "scheduling": {
        "appointment_rescheduling",
        "booking_execution",
        "calendar_access",
        "calendar_management",
        "schedule_management",
    },
    "healthcare": {
        "healthcare_portal_access",
        "patient_record_access",
        "provider_directory_lookup",
    },
    "travel": {
        "booking_execution",
        "fare_comparison",
        "lodging_comparison",
        "lodging_search",
        "travel_search",
    },
    "contact_data": {
        "company_data_lookup",
        "contact_enrichment",
        "email_verification",
    },
    "commerce": {
        "cross_border_commerce",
        "payment_authorization",
        "shipping_quote",
    },
}


@dataclass(frozen=True)
class IntentFit:
    excluded: bool
    score_delta: float
    reasons: list[str]


def score_intent_fit(*, candidate: DiscoveryCandidate, query: ExpandedDiscoveryQuery) -> IntentFit:
    """Score broad intent fit without adding prompt-specific routing."""

    query_intents = _query_intents(query.terms)
    candidate_intents = _candidate_intents(candidate)
    if (
        "internal_data" in candidate_intents
        and "public_web_research" in query_intents
        and "internal_data" not in query_intents
    ):
        return IntentFit(
            excluded=True,
            score_delta=0.0,
            reasons=["excluded:intent_mismatch_internal_database_for_public_web_goal"],
        )

    overlap = sorted(query_intents & candidate_intents)
    if overlap:
        return IntentFit(
            excluded=False,
            score_delta=min(0.18, 0.08 * len(overlap)),
            reasons=[f"intent_fit:{','.join(overlap)}"],
        )

    if query_intents and candidate_intents:
        return IntentFit(
            excluded=False,
            score_delta=-0.05,
            reasons=["intent_fit:weak"],
        )

    return IntentFit(excluded=False, score_delta=0.0, reasons=[])


def _query_intents(terms: set[str]) -> set[str]:
    return {intent for intent, intent_terms in INTENT_TERMS.items() if terms & intent_terms}


def _candidate_intents(candidate: DiscoveryCandidate) -> set[str]:
    terms = {
        candidate.id.lower(),
        candidate.display_name.lower(),
        candidate.vendor.lower(),
        candidate.provider_type.lower(),
        *{capability.id.lower() for capability in candidate.capabilities},
    }
    return {
        intent
        for intent, candidate_terms in CANDIDATE_FIT_TERMS.items()
        if terms & candidate_terms
    }
