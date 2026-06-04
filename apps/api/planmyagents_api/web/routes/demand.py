"""Public demand-signal routes.

Extracted from ``planmyagents_api.web.app`` on 2026-05-19. Two
endpoints with a deliberately small, stable, vendor-facing contract.

Endpoints
---------
* ``GET /demand/top-capabilities`` — top-N by request count in window
* ``GET /demand/gaps``             — demand AND routable-supply gap

Rate-limiter ownership
----------------------
Each ``FastAPI`` instance owns its own :class:`TokenBucketRateLimiter`
via ``app.state.demand_rate_limiter`` (attached in
``create_app()``). The handlers below pull the limiter off
``request.app.state`` rather than a module-level singleton so the
test suite's per-test ``TestClient(create_app())`` calls don't share
bucket state — a previous integration test's rate-limit-exhaustion
would otherwise bleed into the next test's first request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status

from planmyagents_api.discovery.demand_recorder import load_demand_summary
from planmyagents_api.discovery.discovery_gaps_recorder import (
    load_discovery_gaps_summary,
)
from planmyagents_api.web.models import (
    DemandGapEntry,
    DemandGapsResponse,
    DemandTopCapabilitiesResponse,
    DemandTopCapabilityEntry,
)
from planmyagents_api.web.rate_limiter import RateLimitExceeded

router = APIRouter()


def _check_demand_rate_limit(request: Request) -> None:
    """Enforce the per-IP token bucket for the public demand APIs.

    The limiter lives on ``app.state.demand_rate_limiter``; see the
    module docstring for the per-app-instance rationale. A 429 with
    a ``Retry-After`` header (whole seconds, rounded up) is returned
    when the bucket runs dry.
    """

    limiter = request.app.state.demand_rate_limiter
    key = (
        request.client.host
        if request.client and request.client.host
        else "anonymous"
    )
    try:
        limiter.acquire(key)
    except RateLimitExceeded as exc:
        retry_after = max(1, int(exc.retry_after_seconds + 0.999))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Rate limit exceeded on public demand API. "
                f"Retry in {retry_after}s."
            ),
            headers={"Retry-After": str(retry_after)},
        ) from exc


def _in_window(value: str, cutoff: datetime) -> bool:
    if not value:
        return False
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts >= cutoff


@router.get(
    "/demand/top-capabilities",
    response_model=DemandTopCapabilitiesResponse,
    tags=["demand"],
)
def demand_top_capabilities(
    request: Request,
    window_days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="Lookback window in days (default 30).",
    ),
    limit: int = Query(default=25, ge=1, le=200),
) -> DemandTopCapabilitiesResponse:
    """Top-N capabilities by request count in the lookback window.

    Public, vendor-facing. Stable flat contract: callers can rely
    on the column set growing (right side) but never being
    re-ordered or renamed. Capability ids that have no demand
    events in the window are omitted (the response is the answer
    to "what did people ask for"; zero-demand capabilities are
    in ``/capability-labels`` instead).

    Rate-limited per-IP via
    ``PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN`` (default 60).
    """

    from planmyagents_api.web.app import _routable_providers_by_capability

    _check_demand_rate_limit(request)

    summaries = load_demand_summary(sample_goals_per_capability=3)
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    filtered = [s for s in summaries if _in_window(s.last_requested_at, cutoff)]
    routable = _routable_providers_by_capability()
    entries = [
        DemandTopCapabilityEntry(
            capability_id=s.capability_id,
            request_count=s.request_count,
            distinct_requester_count=s.distinct_requester_count,
            last_requested_at=s.last_requested_at,
            routable_today=bool(routable.get(s.capability_id)),
            routable_provider_ids=list(routable.get(s.capability_id, [])),
            sample_goals=list(s.sample_goals),
        )
        for s in filtered[:limit]
    ]
    return DemandTopCapabilitiesResponse(
        window_days=window_days,
        total=len(filtered),
        generated_at=datetime.now(UTC).isoformat(),
        entries=entries,
    )


@router.get(
    "/demand/gaps",
    response_model=DemandGapsResponse,
    tags=["demand"],
)
def demand_gaps_feed(
    request: Request,
    window_days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="Lookback window in days (default 30).",
    ),
    limit: int = Query(default=25, ge=1, le=200),
    min_zero_yield: int = Query(
        default=0,
        ge=0,
        description=(
            "Drop capabilities whose zero_yield_count is below this. "
            "0 keeps everything that has demand AND is not "
            "routable today; >=1 returns only capabilities where we "
            "demonstrably tried discovery and surfaced nothing."
        ),
    ),
) -> DemandGapsResponse:
    """Capabilities with demand AND a routable supply gap.

    A capability is in this feed when ``request_count > 0`` in the
    lookback window AND either (a) ``routable_today=false`` (no
    succeeded benchmark_runs row in 30d), OR (b) ``zero_yield_count
    > 0`` (the discovery scout-judge pipeline tried and surfaced
    nothing). Sorted by ``zero_yield_count`` desc, then
    ``request_count`` desc.

    Rate-limited per-IP via
    ``PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN`` (default 60).
    """

    from planmyagents_api.web.app import _routable_providers_by_capability

    _check_demand_rate_limit(request)

    demand_summaries = load_demand_summary(sample_goals_per_capability=3)
    gap_summaries = load_discovery_gaps_summary(sample_goals_per_capability=3)
    gap_by_cap = {g.capability_id: g for g in gap_summaries}
    routable = _routable_providers_by_capability()

    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    entries: list[DemandGapEntry] = []
    for demand_summary in demand_summaries:
        if not _in_window(demand_summary.last_requested_at, cutoff):
            continue
        cap_id = demand_summary.capability_id
        gap = gap_by_cap.get(cap_id)
        zero_yield = int(getattr(gap, "zero_yield_count", 0) or 0)
        discovery_attempts = int(getattr(gap, "observation_count", 0) or 0)
        is_routable = bool(routable.get(cap_id))
        if is_routable and zero_yield == 0:
            continue
        if zero_yield < min_zero_yield:
            continue
        entries.append(
            DemandGapEntry(
                capability_id=cap_id,
                request_count=demand_summary.request_count,
                discovery_attempts=discovery_attempts,
                zero_yield_count=zero_yield,
                sample_goals=list(demand_summary.sample_goals),
            )
        )

    entries.sort(
        key=lambda e: (-e.zero_yield_count, -e.request_count, e.capability_id)
    )
    return DemandGapsResponse(
        window_days=window_days,
        total=len(entries),
        generated_at=datetime.now(UTC).isoformat(),
        entries=entries[:limit],
    )
