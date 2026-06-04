"""Capability-cluster categorisation for the discovery browser.

Categories are derived deterministically from capability ids using the
prefix-as-cluster convention: ``<prefix>_<rest>`` becomes cluster
``<prefix>``. So ``payment_authorization`` and ``payment_routing`` both
land in the ``payment`` cluster; ``email_verification`` lands in
``email``; ``company_data_lookup`` lands in ``company``. This is a
purely structural rule, no domain knowledge.

History note (deliberately preserved):
    Earlier versions of this file maintained an
    ``EXPLICIT_CATEGORY_BY_CAPABILITY`` override map that hand-grouped
    11 capability slugs into 5 vertical clusters (lead_intelligence,
    payments, travel, knowledge_search, data_extraction). That map was
    domain-biased (only the verticals the team had prior context for
    got hand-grouped) and didn't scale: every new capability category
    required a code edit. The prefix rule already handled most cases
    correctly; the explicit map was relied on for *aesthetic* grouping
    (e.g. mapping all three payment-prefixed slugs to "Payments" works
    fine without an explicit map). Removed in favour of the prefix
    rule alone — if a future capability cluster needs a friendlier
    display name, name the slugs consistently and the prefix rule will
    pick it up automatically.

A category card is intentionally *not* a popularity score. It surfaces
honest, queryable signals: total candidates, candidates with a known/listed
source signal, benchmark-passed candidates, and routable-today candidates.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.registry.promotions import is_promotion_ready


@dataclass(frozen=True)
class CategorySummary:
    """Aggregate signals shown on the category index card."""

    cluster_id: str
    display_name: str
    capability_ids: list[str]
    total_candidates: int
    known_listed_candidates: int
    benchmark_passed_candidates: int
    routable_today_candidates: int
    provider_type_breakdown: dict[str, int] = field(default_factory=dict)
    top_candidate_ids: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "display_name": self.display_name,
            "capability_ids": list(self.capability_ids),
            "totals": {
                "candidates": self.total_candidates,
                "known_listed": self.known_listed_candidates,
                "benchmark_passed": self.benchmark_passed_candidates,
                "routable_today": self.routable_today_candidates,
            },
            "provider_type_breakdown": dict(self.provider_type_breakdown),
            "top_candidate_ids": list(self.top_candidate_ids),
        }


def category_for_capability(capability_id: str) -> tuple[str, str]:
    """Return (cluster_id, display_name) for a capability id.

    The cluster id is the slug's leading underscore-delimited token.
    Empty / single-token slugs become their own cluster. Display name
    is the cluster id title-cased with underscores replaced by spaces.
    """

    parts = capability_id.split("_")
    cluster_id = parts[0] if len(parts) > 1 else capability_id
    display = cluster_id.replace("_", " ").title()
    return cluster_id, display


def cluster_candidates(
    candidates: list[DiscoveryCandidate],
) -> dict[str, CategorySummary]:
    """Group candidates by capability cluster and compute summary stats."""

    by_cluster: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        clusters_for_candidate: set[tuple[str, str]] = set()
        for capability in candidate.capabilities:
            clusters_for_candidate.add(category_for_capability(capability.id))

        for cluster_id, display_name in clusters_for_candidate:
            cluster = by_cluster.setdefault(
                cluster_id,
                {
                    "display_name": display_name,
                    "capability_ids": set(),
                    "candidates": [],
                },
            )
            for capability in candidate.capabilities:
                if category_for_capability(capability.id)[0] == cluster_id:
                    cluster["capability_ids"].add(capability.id)
            cluster["candidates"].append(candidate)

    summaries: dict[str, CategorySummary] = {}
    for cluster_id, payload in by_cluster.items():
        cluster_candidates_list: list[DiscoveryCandidate] = payload["candidates"]
        provider_types = Counter(
            candidate.provider_type for candidate in cluster_candidates_list
        )
        known_listed = sum(
            1
            for candidate in cluster_candidates_list
            if _has_provider_evidence(candidate.verification_status)
        )
        passed = sum(
            1
            for candidate in cluster_candidates_list
            if candidate.benchmark_status == "passed"
        )
        routable = sum(1 for candidate in cluster_candidates_list if is_promotion_ready(candidate))
        # Deterministic top-3: prefer runnable + benchmark-passed, then
        # known/listed source signal, then provider-type priority, then
        # alphabetical.
        ranked = sorted(
            cluster_candidates_list,
            key=lambda candidate: (
                _candidate_rank_key(candidate),
                candidate.id,
            ),
        )
        summaries[cluster_id] = CategorySummary(
            cluster_id=cluster_id,
            display_name=payload["display_name"],
            capability_ids=sorted(payload["capability_ids"]),
            total_candidates=len(cluster_candidates_list),
            known_listed_candidates=known_listed,
            benchmark_passed_candidates=passed,
            routable_today_candidates=routable,
            provider_type_breakdown=dict(provider_types),
            top_candidate_ids=[candidate.id for candidate in ranked[:3]],
        )
    return summaries


def candidates_in_cluster(
    candidates: list[DiscoveryCandidate], cluster_id: str
) -> list[DiscoveryCandidate]:
    selected: list[DiscoveryCandidate] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        if candidate.id in seen_ids:
            continue
        for capability in candidate.capabilities:
            if category_for_capability(capability.id)[0] == cluster_id:
                selected.append(candidate)
                seen_ids.add(candidate.id)
                break
    return sorted(
        selected,
        key=lambda candidate: (_candidate_rank_key(candidate), candidate.id),
    )


def _candidate_rank_key(candidate: DiscoveryCandidate) -> tuple[int, int, int, int, int]:
    """Sort key used for category top-3 + cluster listing.

    Order:
    1. Routable today (best first)
    2. Benchmark passed
    3. Known/listed source signal
    4. Higher confirmation count (more sources agree → higher credibility)
    5. Provider type priority (lower = better)
    """

    routable = 0 if is_promotion_ready(candidate) else 1
    passed = 0 if candidate.benchmark_status == "passed" else 1
    verified = 0 if _has_provider_evidence(candidate.verification_status) else 1
    # Negate so higher confirmation count sorts earlier under stable ascending order.
    confirmation_rank = -candidate.confirmation_count
    return routable, passed, verified, confirmation_rank, candidate.discovery_priority


def _has_provider_evidence(status: str) -> bool:
    return status not in {"", "unverified", "unverified_example"}
