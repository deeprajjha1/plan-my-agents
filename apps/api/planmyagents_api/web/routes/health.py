"""Health and evidence-summary routes.

Extracted from ``planmyagents_api.web.app`` on 2026-05-19. These
endpoints are the smallest, lowest-coupling cluster in the API and
were the first to migrate as a sanity check on the router pattern
(``planmyagents_api.web.routes.__init__`` has the broader rationale).

The endpoints here share three properties that make them ideal
first-movers:

* They only read configuration and module-level caches; none of them
  invoke the planner, the LLM tiers, or any of the heavier discovery
  pipelines.
* They are public-by-default (no auth dependency) and called by the
  homepage Live-Evidence strip on every render, so the 60-second
  TTL cache that backs ``/health/evidence`` materially matters.
* Their tests already isolated cleanly in
  ``apps/api/tests/test_web_app.py`` (``test_health_returns_store_locations``)
  so the migration could be validated in isolation.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from planmyagents_api._config import (
    benchmark_store_url,
    discovery_store_url,
    verification_store_url,
)
from planmyagents_api.discovery.embeddings import embedder_from_env
from planmyagents_api.web.models import (
    EvidenceHealthResponse,
    HealthResponse,
    RecentVerificationsResponse,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        discovery_store=discovery_store_url(),
        benchmark_store=benchmark_store_url(),
        verification_store=verification_store_url(),
        embedding_model=embedder_from_env().name,
    )


@router.get(
    "/health/evidence",
    response_model=EvidenceHealthResponse,
    tags=["health"],
)
def health_evidence() -> EvidenceHealthResponse:
    """Live counts of evidence tables that back the pitch deck claims.

    Cached for ``PLANMYAGENTS_HEALTH_EVIDENCE_CACHE_S`` seconds
    (default 60) so the homepage Live-Evidence strip can hit this
    on every page render without pressuring the DB.
    """

    # Import lazily so this router module can be imported before
    # `web.app` finishes evaluating (avoids a circular import while
    # the bulk of the helpers still live in ``app.py``). Once the
    # cache helper migrates to its own module, this lazy import
    # collapses into a top-level one.
    from planmyagents_api.web.app import _compute_evidence_health_cached

    return _compute_evidence_health_cached()


@router.get(
    "/evidence/recent-verifications",
    response_model=RecentVerificationsResponse,
    tags=["evidence"],
)
def recent_verifications(
    limit: int = Query(default=5, ge=1, le=50),
) -> RecentVerificationsResponse:
    """Latest N verification_records, summarised for the UI.

    Backs the "Recent verifications" section on /discovery-gaps and
    /open-mcp-opportunities. Reads directly from Postgres when
    configured (otherwise returns an empty list — there is no JSONL
    backend for verification_records).
    """

    from planmyagents_api.web.app import _recent_verifications

    return _recent_verifications(limit)
