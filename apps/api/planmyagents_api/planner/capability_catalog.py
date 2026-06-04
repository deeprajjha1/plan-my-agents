"""Capability inference from discovered candidate metadata.

This keeps deterministic planning from becoming a pile of
domain-specific if-statements. Source catalogs advertise candidates and
capabilities; the planner infers missing capabilities from that catalog
by matching user-goal terms against tokens derived from each capability's
candidates (display_name, vendor, capability id, capability notes). The
alias map is therefore *derived from the live registry* rather than a
hardcoded synonym dictionary — domain-agnostic by construction.

History note (deliberately preserved):
    Earlier versions of this module also carried three hardcoded English
    keyword sets (``BOOKING_TERMS = {"book", "reserve"}``,
    ``ORDER_TERMS = {"buy", "purchase", "order", "checkout"}``,
    ``TRANSACTION_TERMS = BOOKING_TERMS | ORDER_TERMS``) plus a
    ``COMMON_NORMALIZATIONS`` typo table biased toward Indian/US English
    spellings. They biased the inference toward retail/travel verbs and
    duplicated the deleted ``discovery/query.py:CAPABILITY_SYNONYMS``
    table. Removed as part of the same domain-symmetry pass — this
    module now relies entirely on candidate-derived aliases. If your
    user goal contains intent that the registry can't express via any
    capability id or candidate metadata, the catalog correctly returns
    nothing and the goal becomes a discovery target, which is the
    honest answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from planmyagents_api.discovery.models import DiscoveryCandidate

STOPWORDS = {
    "agent",
    "agents",
    "api",
    "a2a",
    "mcp",
    "server",
    "service",
    "services",
    "provider",
    "providers",
    "tool",
    "tools",
    "com",
    "www",
    "https",
    "http",
}


@dataclass(frozen=True)
class CapabilityCatalog:
    """Searchable alias map derived from discovery candidates."""

    aliases_by_capability: dict[str, set[str]] = field(default_factory=dict)

    @property
    def capability_ids(self) -> set[str]:
        return set(self.aliases_by_capability)

    @property
    def aliases_by_term(self) -> dict[str, set[str]]:
        aliases: dict[str, set[str]] = {}
        for capability, terms in self.aliases_by_capability.items():
            for term in terms:
                aliases.setdefault(term, set()).add(capability)
        return aliases


def build_capability_catalog(candidates: list[DiscoveryCandidate]) -> CapabilityCatalog:
    """Build a capability catalog from candidate names, capability IDs, and notes."""

    aliases_by_capability: dict[str, set[str]] = {}
    for candidate in candidates:
        candidate_terms = _terms(
            " ".join(
                [
                    candidate.id,
                    candidate.display_name,
                    candidate.vendor,
                    candidate.vendor_url,
                    candidate.provider_type,
                ]
            )
        )
        for capability in candidate.capabilities:
            terms = aliases_by_capability.setdefault(capability.id, set())
            terms.update(_terms(capability.id))
            terms.update(_terms(capability.notes))
            if _allows_candidate_text_aliases(capability.id):
                terms.update(candidate_terms)
    return CapabilityCatalog(aliases_by_capability=aliases_by_capability)


def infer_capabilities_from_catalog(goal: str, catalog: CapabilityCatalog) -> list[str]:
    """Infer missing capabilities from user terms and the discovered catalog.

    Pure alias-overlap scoring against the candidate-derived alias map.
    No hardcoded English keyword fallbacks (``BOOKING_TERMS``,
    ``ORDER_TERMS``, etc.) — those biased the system toward retail/
    travel verbs and were duplicative with the candidate-derived
    aliases anyway: if the registry has a ``booking_execution``
    capability whose candidates mention "book" / "reserve" in their
    metadata, the alias path picks them up naturally.
    """

    terms = _terms(goal.lower())
    aliases_by_term = catalog.aliases_by_term
    scores: dict[str, int] = {capability: 0 for capability in catalog.capability_ids}

    for term in terms:
        for capability in aliases_by_term.get(term, set()):
            scores[capability] += 1

    inferred = {capability for capability, score in scores.items() if score > 0}
    return sorted(inferred)


def _terms(value: str) -> set[str]:
    terms: set[str] = set()
    for raw in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", value.lower()):
        for part in raw.replace("_", "-").split("-"):
            term = _stem(part)
            if len(term) > 2 and term not in STOPWORDS:
                terms.add(term)
    return terms


def _stem(value: str) -> str:
    if value.endswith("ing") and len(value) > 5:
        return value[:-3]
    if value.endswith("ies") and len(value) > 5:
        return f"{value[:-3]}y"
    if value.endswith("s") and len(value) > 4:
        return value[:-1]
    return value


def _allows_candidate_text_aliases(capability_id: str) -> bool:
    return capability_id.endswith(
        (
            "_authorization",
            "_automation",
            "_comparison",
            "_execution",
            "_extraction",
            "_lookup",
            "_retrieval",
            "_scraping",
            "_search",
        )
    )
