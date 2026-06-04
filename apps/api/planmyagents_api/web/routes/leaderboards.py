"""Leaderboard + benchmark-runs routes.

Extracted from ``planmyagents_api.web.app`` on 2026-05-19. The
three endpoints here share a tight dependency on the benchmark
store and the credibility classifier — splitting them across two
files would force the credibility classifier import into both,
without any payoff.

Endpoints
---------
* ``GET /leaderboards``                  — capability index
* ``GET /leaderboards/{capability}``     — per-capability board
* ``GET /benchmark/runs``                — raw runs (debug / API)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from planmyagents_api._config import benchmark_store_url, discovery_store_url
from planmyagents_api.benchmark.credibility import classify
from planmyagents_api.benchmark.store import benchmark_store_for_path
from planmyagents_api.discovery.categories import category_for_capability
from planmyagents_api.discovery.store import discovery_store_for_path
from planmyagents_api.web.models import (
    BenchmarkRunsResponse,
    LeaderboardIndexResponse,
    LeaderboardResponse,
)

router = APIRouter()


@router.get(
    "/leaderboards",
    response_model=LeaderboardIndexResponse,
    tags=["leaderboard"],
)
def leaderboards_index() -> LeaderboardIndexResponse:
    from planmyagents_api.web.app import _leaderboard_index_entries

    candidates = discovery_store_for_path(discovery_store_url()).load()
    rankings = list(benchmark_store_for_path(benchmark_store_url()).load_rankings())
    entries = _leaderboard_index_entries(candidates=candidates, rankings=rankings)
    return LeaderboardIndexResponse(capabilities=entries)


@router.get(
    "/leaderboards/{capability}",
    response_model=LeaderboardResponse,
    tags=["leaderboard"],
)
def leaderboard_for_capability(capability: str) -> LeaderboardResponse:
    from planmyagents_api.web.app import (
        _candidate_supports,
        _credibility_model,
        _leaderboard_entries,
    )

    capability_id = capability.strip()
    if not capability_id:
        raise HTTPException(status_code=400, detail="capability is required")

    candidates = discovery_store_for_path(discovery_store_url()).load()
    candidates_supporting = [
        candidate
        for candidate in candidates
        if _candidate_supports(candidate, capability_id)
    ]
    if not candidates_supporting:
        raise HTTPException(
            status_code=404,
            detail=f"No discovery candidates declare capability `{capability_id}`.",
        )

    rankings = [
        row
        for row in benchmark_store_for_path(benchmark_store_url()).load_rankings()
        if str(row.get("capability") or "") == capability_id
    ]
    cluster_id, cluster_display = category_for_capability(capability_id)
    entries = _leaderboard_entries(
        candidates=candidates_supporting,
        rankings=rankings,
    )
    verdict = classify(
        capability=capability_id,
        candidates=candidates_supporting,
        rankings=rankings,
    )
    return LeaderboardResponse(
        capability=capability_id,
        cluster_id=cluster_id,
        cluster_display_name=cluster_display,
        total_providers=len(candidates_supporting),
        benchmark_passed=sum(
            1 for entry in entries if entry.benchmark_status == "passed"
        ),
        routable_today=sum(1 for entry in entries if entry.routable_today),
        entries=entries,
        credibility=_credibility_model(verdict),
    )


@router.get(
    "/benchmark/runs",
    response_model=BenchmarkRunsResponse,
    tags=["benchmark"],
)
def benchmark_runs(
    provider_id: str | None = Query(default=None),
    capability: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> BenchmarkRunsResponse:
    all_runs = benchmark_store_for_path(benchmark_store_url()).load_json()
    if provider_id:
        all_runs = [row for row in all_runs if row.get("provider_id") == provider_id]
    if capability:
        all_runs = [row for row in all_runs if row.get("capability") == capability]
    return BenchmarkRunsResponse(
        provider_id=provider_id,
        capability=capability,
        runs=all_runs[-limit:],
    )
