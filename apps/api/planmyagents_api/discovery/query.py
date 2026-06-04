"""Query expansion for discovery search.

This module produces an ``ExpandedDiscoveryQuery`` from a goal text plus
the capabilities the planner explicitly requested. The expanded form is
what discovery sources, the SQLite index, and the discovery service
ranking layer all consume.

History note (deliberately preserved for context):
    Earlier versions of this file maintained two hardcoded dictionaries
    — ``CAPABILITY_SYNONYMS`` (English keywords mapped to capability
    slugs) and ``COMMON_NORMALIZATIONS`` (a tiny typo/spelling table
    biased toward Indian/US English). Both are gone. The synonym dict
    over-inferred capabilities (any query containing "find" routed to
    `semantic_search`; any query containing "book" routed to
    `payment_authorization`), and the normalization dict was a thin
    geographic spell-checker that didn't generalise. They've been
    replaced by ``CapabilityIndex.infer_from_text`` which does the same
    job via embedding similarity against the live registry — no
    English keyword lists, no domain bias, scales to whatever the
    registry grows to without code edits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from planmyagents_api.discovery.capability_index import get_default_capability_index


@dataclass(frozen=True)
class ExpandedDiscoveryQuery:
    """Normalized discovery query used by sources and ranking.

    Field stability matters: every discovery source and the SQLite
    indexer reads these attributes by name. Renames here cascade to
    7+ files. Add new fields freely; deprecate by leaving the field in
    place and emptying it.
    """

    capabilities: set[str]
    task_description: str
    normalized_description: str
    terms: set[str]
    inferred_capabilities: set[str]
    # ``normalizations`` used to hold typo→correction pairs from the
    # deleted ``COMMON_NORMALIZATIONS`` table. Now always empty —
    # preserved as a field so consumers (service.py response payload,
    # index.py ranking display) keep working without an interface
    # change. Will be removed in a follow-up once those consumers are
    # confirmed quiet about it.
    normalizations: dict[str, str]

    @property
    def all_capabilities(self) -> set[str]:
        return set(self.capabilities) | set(self.inferred_capabilities)


def expand_discovery_query(
    *, capabilities: set[str], task_description: str
) -> ExpandedDiscoveryQuery:
    """Expand capability and text terms without trusting them for execution.

    Inferred capabilities come from
    ``CapabilityIndex.infer_from_text``. With the default
    ``DeterministicHashEmbedder`` this conservatively returns few or no
    matches; set ``PLANMYAGENTS_EMBEDDING_MODEL`` to enable a real
    semantic embedder (e.g. Ollama ``nomic-embed-text``) for richer
    inferences. Either way, the returned set only ever contains slugs
    that actually exist in the live registry — no phantom capabilities.
    """

    normalized = task_description.lower()
    terms = _terms(normalized)

    inferred: set[str] = set()
    if task_description:
        # The capability index is registry-aware: it only proposes
        # registry-known slugs. That's the right contract for "inferred
        # capabilities", which downstream code treats as routable.
        index = get_default_capability_index()
        inferred = index.infer_from_text(task_description)

    # Always include the explicit capability ids in the term bag so
    # substring-overlap matchers (the SQLite index, source-side relevance
    # ranking) treat them as keywords. We do NOT expand them with English
    # synonyms anymore — that was the mechanism by which the deleted
    # CAPABILITY_SYNONYMS table was injecting bias into every source.
    for capability in capabilities:
        if capability:
            terms.add(capability)

    return ExpandedDiscoveryQuery(
        capabilities={item for item in capabilities if item},
        task_description=task_description,
        normalized_description=normalized,
        terms=terms,
        inferred_capabilities=inferred,
        normalizations={},
    )


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", value.lower()) if len(term) > 2}
