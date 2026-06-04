"""Discovery + capability-labels routes.

Extracted from ``planmyagents_api.web.app`` on 2026-05-19. This is
the largest router by endpoint count (eight endpoints, originally
~330 LOC inline). They share a single dependency surface: the
discovery store, the benchmark store, and the verification store
— all three driven by the centralised env vars in
:mod:`planmyagents_api._config`.

Endpoints
---------
* ``GET /discovery/categories``                — cluster summaries
* ``GET /discovery/categories/{cluster_id}``    — per-cluster detail
* ``GET /discovery/agents/{provider_id}``       — per-agent detail
* ``GET /discovery/search``                     — semantic search
* ``GET /discovery/index-freshness``            — provenance breakdown
* ``GET /open-mcp-opportunities``               — APIs without MCPs
* ``GET /discovery-gaps``                       — zero-yield gaps
* ``GET /capability-labels``                    — labels coined by decomposer

Why ``capability-labels`` lives here
------------------------------------
It sits on the boundary between discovery (the labels come from
discovery candidates) and planning (the labels are coined by the
planner's decomposer). We pick discovery because the public-facing
purpose is "the world's view of which capabilities exist" — same
mental model as the other endpoints in this file. If the planner
ever needs to mutate these from a different surface we'll split.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from planmyagents_api._config import (
    benchmark_store_url,
    discovery_store_url,
    verification_store_url,
)
from planmyagents_api.benchmark.store import benchmark_store_for_path
from planmyagents_api.discovery.categories import (
    candidates_in_cluster,
    cluster_candidates,
)
from planmyagents_api.discovery.demand_recorder import load_demand_summary
from planmyagents_api.discovery.discovery_gaps_recorder import (
    load_discovery_gaps_summary,
)
from planmyagents_api.discovery.embeddings import embedder_from_env
from planmyagents_api.discovery.open_mcp_opportunities import (
    compute_open_mcp_opportunities,
)
from planmyagents_api.discovery.readiness import promotion_readiness
from planmyagents_api.discovery.service import search_candidates_with_embeddings
from planmyagents_api.discovery.store import discovery_store_for_path
from planmyagents_api.discovery.verification_store import verification_store_for_path
from planmyagents_api.planner.capability_label_recorder import (
    labels_to_payload,
    load_persisted_labels,
)
from planmyagents_api.web.models import (
    AgentDetailResponse,
    CandidateCardModel,
    CategoriesResponse,
    CategoryDetailResponse,
    CategorySummaryResponse,
)

router = APIRouter()


@router.get(
    "/discovery/categories",
    response_model=CategoriesResponse,
    tags=["discovery"],
)
def discovery_categories() -> CategoriesResponse:
    store_url = discovery_store_url()
    candidates = discovery_store_for_path(store_url).load()
    clusters = cluster_candidates(candidates)
    ordered = sorted(
        clusters.values(),
        key=lambda summary: (
            -summary.routable_today_candidates,
            -summary.benchmark_passed_candidates,
            -summary.known_listed_candidates,
            -summary.total_candidates,
            summary.cluster_id,
        ),
    )
    return CategoriesResponse(
        store=store_url,
        backend="postgres"
        if store_url.startswith(("postgresql://", "postgres://"))
        else "local",
        embedder=embedder_from_env().name,
        total_candidates=len(candidates),
        categories=[
            CategorySummaryResponse(**summary.to_json()) for summary in ordered
        ],
    )


@router.get(
    "/discovery/categories/{cluster_id}",
    response_model=CategoryDetailResponse,
    tags=["discovery"],
)
def discovery_category_detail(cluster_id: str) -> CategoryDetailResponse:
    store_url = discovery_store_url()
    candidates = discovery_store_for_path(store_url).load()
    clusters = cluster_candidates(candidates)
    summary = clusters.get(cluster_id)
    if summary is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown category cluster: {cluster_id}"
        )
    cluster_members = candidates_in_cluster(candidates, cluster_id)
    cards: list[CandidateCardModel] = []
    for candidate in cluster_members:
        payload = candidate.to_public_summary()
        payload["promotion_readiness"] = promotion_readiness(candidate)
        cards.append(CandidateCardModel(**payload))
    return CategoryDetailResponse(
        cluster_id=summary.cluster_id,
        display_name=summary.display_name,
        capability_ids=summary.capability_ids,
        totals=summary.to_json()["totals"],
        candidates=cards,
    )


@router.get(
    "/discovery/agents/{provider_id}",
    response_model=AgentDetailResponse,
    tags=["discovery"],
)
def discovery_agent_detail(provider_id: str) -> AgentDetailResponse:
    store_url = discovery_store_url()
    candidates = discovery_store_for_path(store_url).load()
    candidate = next(
        (item for item in candidates if item.id == provider_id), None
    )
    if candidate is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown discovery candidate: {provider_id}"
        )

    benchmark_store = benchmark_store_for_path(benchmark_store_url())
    rankings_payload = list(benchmark_store.load_rankings())
    rankings_for_provider = [
        row for row in rankings_payload if row.get("provider_id") == provider_id
    ]
    recent_runs = [
        row
        for row in benchmark_store.load_json()
        if row.get("provider_id") == provider_id
    ][-50:]
    verification_store = verification_store_for_path(verification_store_url())
    history = verification_store.history_for(provider_id)

    candidate_payload = candidate.to_registry_json()
    candidate_payload["promotion_readiness"] = promotion_readiness(candidate)
    return AgentDetailResponse(
        candidate=candidate_payload,
        promotion_readiness=promotion_readiness(candidate),
        rankings=rankings_for_provider,
        verification_history=history,
        recent_runs=recent_runs,
    )


@router.get("/discovery/search", tags=["discovery"])
def discovery_search(
    q: str = Query(min_length=1),
    capability: str | None = Query(default=None),
    provider_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return search_candidates_with_embeddings(
        store_url=discovery_store_url(),
        query=q,
        capability=capability,
        provider_type=provider_type,
        limit=limit,
    )


@router.get("/discovery/index-freshness", tags=["discovery"])
def discovery_index_freshness() -> dict[str, Any]:
    """Provenance + recency breakdown of the indexed candidate pool.

    Answers "where did the N candidates come from?" and "when were
    they last refreshed?" without making the caller round-trip
    through /goal. Read-only; never mutates the store.
    """

    from planmyagents_api.web.app import _index_freshness

    return _index_freshness()


@router.get("/open-mcp-opportunities", tags=["discovery"])
def open_mcp_opportunities(
    capability: str | None = Query(
        default=None,
        description="Filter to a single capability id.",
    ),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Capabilities where APIs exist but no MCP/A2A/AI-agent does.

    This is the public 'gap signal' surface: developers and vendors
    can browse it to find APIs they could productively wrap as an
    MCP, ranked by combined demand + supply signal.
    """

    store = discovery_store_for_path(discovery_store_url())
    report = compute_open_mcp_opportunities(
        agentic_candidates=store.load(),
        api_records=store.load_apis_without_agents(),
        demand_summaries=load_demand_summary(),
    )
    payload = report.to_json()
    if capability:
        target = capability.strip()
        payload["capabilities"] = [
            entry
            for entry in payload["capabilities"]
            if entry["capability_id"] == target
        ]
        payload["filter"] = {"capability": target}
    payload["capabilities"] = payload["capabilities"][:limit]
    return payload


@router.get("/discovery-gaps", tags=["discovery"])
def discovery_gaps(
    capability: str | None = Query(
        default=None,
        description="Filter to a single capability id.",
    ),
    limit: int = Query(default=20, ge=1, le=200),
    sample_goals_per_capability: int = Query(default=3, ge=0, le=10),
) -> dict[str, Any]:
    """Capabilities the world keeps asking for but nobody has shipped
    a routable agent for yet.

    Each row is a per-capability rollup of post-discovery outcomes:
    scouts dispatched, scouts that returned zero, candidates the
    judge evaluated, candidates the judge accepted. The leaderboard
    sort key is ``zero_yield_count`` desc, so the top of the list
    is the brutally-honest "we tried N times and surfaced no
    routable agent" capabilities — exactly the signal a vendor
    looking for an MCP idea wants. Companion to
    ``/open-mcp-opportunities``: that page surfaces "API exists
    but no MCP wraps it"; this surfaces "we couldn't even find an
    API yet".
    """

    summaries = load_discovery_gaps_summary(
        sample_goals_per_capability=sample_goals_per_capability
    )
    if capability:
        target = capability.strip()
        summaries = [
            summary
            for summary in summaries
            if summary.capability_id == target
        ]
    rows = [summary.to_json() for summary in summaries[:limit]]
    return {
        "status": "ok",
        "total_capabilities": len(summaries),
        "filter": {"capability": capability} if capability else None,
        "capabilities": rows,
    }


@router.get("/capability-labels", tags=["discovery"])
def capability_labels(
    sort: str = Query(
        default="last_used_at",
        description=(
            "Order key. One of 'last_used_at' (default; recently active "
            "labels first), 'usage_count' (most-reused first), or 'id' "
            "(alphabetical, stable for diffing)."
        ),
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    min_usage_count: int = Query(default=1, ge=1),
) -> dict[str, Any]:
    """Persisted capability labels coined by the goal decomposer.

    Each row is a snake_case capability id that the decomposer
    invented in some past request and the label reconciler
    decided was genuinely new (i.e. not semantically equivalent
    to anything already in the catalog at the time). Subsequent
    requests' decompositions see these ids as catalog hints, so
    the same coined label can be reused — that's the vocabulary
    growth the leaderboards rely on for cross-request demand
    aggregation.

    ``usage_count`` is incremented every time the reconciler
    UPSERTs the label (i.e. every time a future request coins a
    slug we then matched/persisted as the same id). A label that
    sits at ``usage_count == 1`` for weeks is a candidate for
    manual review — the decomposer coined it once and it never
    recurred, which usually means it was either too goal-specific
    or duplicates something the reconciler couldn't see.
    """

    sort_key = (sort or "").strip().lower()
    if sort_key not in {"last_used_at", "usage_count", "id"}:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_sort",
                "message": f"sort must be one of last_used_at|usage_count|id, got {sort!r}",
            },
        )

    labels = load_persisted_labels()
    filtered = [
        label for label in labels if label.usage_count >= min_usage_count
    ]
    if sort_key == "last_used_at":
        filtered.sort(key=lambda lbl: lbl.last_used_at, reverse=True)
    elif sort_key == "usage_count":
        # Stable secondary sort on id keeps the response order
        # deterministic across requests with identical usage_count.
        filtered.sort(key=lambda lbl: (-lbl.usage_count, lbl.id))
    else:
        filtered.sort(key=lambda lbl: lbl.id)

    return {
        "status": "ok",
        "total": len(filtered),
        "sort": sort_key,
        "min_usage_count": min_usage_count,
        "labels": labels_to_payload(filtered[:limit]),
    }
