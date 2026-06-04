"""FastAPI app exposing discovery, benchmark, and goal endpoints.

Run locally:

    PYTHONPATH=apps/api .venv/bin/uvicorn planmyagents_api.web.app:app --reload --port 8000

The app reads its persistence locations from environment variables so it works
identically against SQLite/JSON locally and against Postgres in production:

* ``PLANMYAGENTS_DISCOVERY_STORE_URL``
* ``PLANMYAGENTS_BENCHMARK_STORE_URL``
* ``PLANMYAGENTS_VERIFICATION_STORE_URL``
* ``PLANMYAGENTS_EMBEDDING_MODEL`` (optional)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from planmyagents_api._config import (
    benchmark_store_url as _benchmark_store_url,
)
from planmyagents_api._config import (
    discovery_store_url as _discovery_store_url,
)
from planmyagents_api._config import (
    log_store_defaults_in_use,
)
from planmyagents_api._config import (
    verification_store_url as _verification_store_url,
)
from planmyagents_api._env import load_dotenv_once
from planmyagents_api._logging import setup_logging
from planmyagents_api.agents.router import ProviderRouter
from planmyagents_api.benchmark.credibility import classify_index
from planmyagents_api.benchmark.store import benchmark_store_for_path
from planmyagents_api.billing import (
    StripeConfig,
    stripe_config_from_env,
)
from planmyagents_api.discovery.candidate_judge import (
    CandidateJudge,
    is_judge_enabled,
)
from planmyagents_api.discovery.categories import (
    category_for_capability,
)
from planmyagents_api.discovery.constants import AGENTIC_PROVIDER_TYPES
from planmyagents_api.discovery.demand_recorder import (
    load_demand_summary,
    record_refusal_demand,
)
from planmyagents_api.discovery.discovery_gaps_recorder import (
    record_discovery_gap,
)
from planmyagents_api.discovery.gaps import build_gap_report
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import (
    CandidateNormalizationError,
    normalize_candidate,
)
from planmyagents_api.discovery.post_goal_refresh import kick_off_post_goal_refresh
from planmyagents_api.discovery.query_expansion import expand_for_scouts
from planmyagents_api.discovery.scouts import ScoutDispatcher, default_scouts
from planmyagents_api.discovery.service import (
    search_candidates,
)
from planmyagents_api.discovery.store import discovery_store_for_path
from planmyagents_api.llm.escalating_client import (
    NoLlmTierAvailableError,
    build_default_escalating_client,
)
from planmyagents_api.marketplace_store import (
    MarketplaceStore,
    marketplace_store_from_env,
)
from planmyagents_api.planner.cost_estimator import estimate_plan_cost
from planmyagents_api.planner.goal import GoalPlan
from planmyagents_api.planner.human_fallback_suggester import (
    HumanFallbackSuggestion,
    suggest_human_alternatives,
    suggester_enabled,
)
from planmyagents_api.planner.local_qwen import (
    LocalQwenPlannerError,
    OllamaQwenClient,
    build_planner_messages,
)
from planmyagents_api.planner.recipe_export import (
    RECIPE_FORMATS,
    build_recipe_context,
    render_recipe,
)
from planmyagents_api.planner.recipe_export.goal_cache import (
    compute_goal_id,
    goal_cache_from_env,
)
from planmyagents_api.registry.promotions import is_promotion_ready
from planmyagents_api.research.general_research_agent import (
    GENERAL_RESEARCH_CAPABILITY_ID,
    GeneralResearchAgent,
    ResearchResult,
)
from planmyagents_api.web.models import (
    CredibilityModel,
    EvidenceHealthResponse,
    GoalExplainResponse,
    GoalRequest,
    GoalResponse,
    LeaderboardEntry,
    LeaderboardIndexEntry,
    RecentVerificationRecord,
    RecentVerificationsResponse,
    SavedRecipeResponse,
)
from planmyagents_api.web.planning import (
    PlanningUnavailableError,
    plan_goal_with_pre_plan_discovery,
)
from planmyagents_api.web.rate_limiter import (
    public_demand_rate_limiter,
)
from planmyagents_api.workflows.executor import WorkflowExecutor

# Load `.env` immediately after imports so uvicorn workers, dev reloads, and
# direct Python launches all see the same configuration before any function
# in this module reads `os.getenv(...)`. No-op if the file is absent; never
# overrides shell-exported values.
load_dotenv_once()
# Wire stdout + rotating-file logging the moment we know our env vars
# are populated. Without this the `logger.info(...)` calls scattered
# through discovery/, planner/, and judge/ would be silently
# discarded — that's the bug that made `make api` look frozen during
# a 5-minute /goal request.
setup_logging()
logger = logging.getLogger("planmyagents_api.web.app")

ROOT = Path(__file__).resolve().parents[4]

# `_discovery_store_url`, `_benchmark_store_url`, `_verification_store_url`
# are imported from `planmyagents_api._config` above. They were inlined here
# until 2026-05-19, when the in-tree audit found that this file defaulted
# them to local SQLite/JSON paths while `web/planning.py` defaulted the
# same env vars to a Postgres DSN — a real split-brain when the env vars
# were unset. The new module is the single source of truth; the matching
# guard test in `tests/test_store_url_defaults.py` keeps any future file
# from re-introducing its own default.
log_store_defaults_in_use(logger)


# ---------------------------------------------------------------------------
# /health/evidence (sprint-pitch-align P3-3)
# ---------------------------------------------------------------------------
#
# Lightweight in-process TTL cache so the homepage Live-Evidence strip can
# call this on every render without a DB round-trip each time. Default 60s.
# Cleared on every process restart (the strip just re-fetches).

_EVIDENCE_HEALTH_CACHE: tuple[float, EvidenceHealthResponse] | None = None


def _evidence_health_cache_seconds() -> float:
    raw = os.getenv("PLANMYAGENTS_HEALTH_EVIDENCE_CACHE_S", "60")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 60.0


def _evidence_dsn() -> str:
    # All five evidence tables live in the same Postgres instance today; if
    # we ever shard them, this is the one place to teach it.
    return _benchmark_store_url()


def _compute_evidence_health() -> EvidenceHealthResponse:
    """Direct Postgres reads — six small COUNT(*) queries.

    Falls back to all-zeros (not an HTTP error) when the DSN does not
    resolve to Postgres (dev shells using SQLite/JSON stores) so the
    homepage strip stays renderable in every environment. The
    ``db_reachable`` flag (added 2026-05-19) lets consumers
    distinguish "system alive but empty" from "DB down / DSN wrong"
    — the latter previously surfaced as all-zeros with no signal that
    something was actually wrong.
    """
    from datetime import UTC, datetime

    dsn = _evidence_dsn()
    now_iso = datetime.now(UTC).isoformat()
    if not dsn.startswith(("postgresql://", "postgres://")):
        # Dev shells using SQLite/JSON stores: this is a config choice,
        # not a failure. Report db_reachable=True so the UI does not
        # show an alarming "DB down" banner for an intentional setup.
        return EvidenceHealthResponse(
            benchmark_runs_24h=0,
            verification_records_7d=0,
            discovery_run_events_24h=0,
            capability_demand_events_24h=0,
            discovery_gap_events_24h=0,
            route_status_routable_count=0,
            benchmark_runs_total=0,
            verification_records_total=0,
            checked_at=now_iso,
            db_reachable=True,
            db_error=None,
        )

    db_reachable = True
    db_error: str | None = None
    row = (0, 0, 0, 0, 0, 0, 0, 0)
    try:
        import psycopg

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # NOTE on "routable" semantics: a (provider, capability)
                # pair is considered routable today when we have at least
                # one successful benchmark run for it in the last 30 days.
                # That matches the deck's claim — "routes through tested
                # and runnable providers when one exists" — much better
                # than counting `discovery_candidates.route_status`, which
                # only flips once a candidate has been through the full
                # promotion pipeline (a separate, much rarer event).
                cur.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM benchmark_runs
                       WHERE created_at > now() - interval '24 hours'),
                      (SELECT COUNT(*) FROM verification_records
                       WHERE created_at > now() - interval '7 days'),
                      (SELECT COUNT(*) FROM discovery_run_events
                       WHERE started_at > now() - interval '24 hours'),
                      (SELECT COUNT(*) FROM capability_demand_events
                       WHERE requested_at > now() - interval '24 hours'),
                      (SELECT COUNT(*) FROM discovery_gap_events
                       WHERE observed_at > now() - interval '24 hours'),
                      (SELECT COUNT(DISTINCT (provider_id, capability))
                       FROM benchmark_runs
                       WHERE succeeded = true
                         AND created_at > now() - interval '30 days'),
                      (SELECT COUNT(*) FROM benchmark_runs),
                      (SELECT COUNT(*) FROM verification_records)
                    """
                )
                row = cur.fetchone() or row
    except Exception as exc:  # noqa: BLE001 — health endpoint must never raise
        # The contract is: never raise (homepage must always render),
        # but always tell the truth. Pre-2026-05-19 we lost the second
        # half of that contract — the UI couldn't tell empty-DB from
        # dead-DB. The flag + error string fix that asymmetry without
        # changing the never-raise guarantee.
        logger = logging.getLogger(__name__)
        logger.warning("evidence-health query failed: %s", exc)
        db_reachable = False
        # Truncate to keep the field UI-renderable; the full message
        # is in the server log line above.
        db_error = str(exc).splitlines()[0][:240] if str(exc) else type(exc).__name__

    return EvidenceHealthResponse(
        benchmark_runs_24h=int(row[0] or 0),
        verification_records_7d=int(row[1] or 0),
        discovery_run_events_24h=int(row[2] or 0),
        capability_demand_events_24h=int(row[3] or 0),
        discovery_gap_events_24h=int(row[4] or 0),
        route_status_routable_count=int(row[5] or 0),
        benchmark_runs_total=int(row[6] or 0),
        verification_records_total=int(row[7] or 0),
        checked_at=now_iso,
        db_reachable=db_reachable,
        db_error=db_error,
    )


def _compute_evidence_health_cached() -> EvidenceHealthResponse:
    global _EVIDENCE_HEALTH_CACHE
    cache_seconds = _evidence_health_cache_seconds()
    now = time.monotonic()
    if cache_seconds > 0 and _EVIDENCE_HEALTH_CACHE is not None:
        cached_at, cached_value = _EVIDENCE_HEALTH_CACHE
        if now - cached_at < cache_seconds:
            return cached_value
    fresh = _compute_evidence_health()
    _EVIDENCE_HEALTH_CACHE = (now, fresh)
    return fresh


def _recent_verifications(limit: int) -> RecentVerificationsResponse:
    """Direct Postgres read for the latest N verification_records.

    Returns an empty list (not an error) when the DSN is not Postgres
    or the query fails, so the page stays renderable.
    """
    from datetime import UTC, datetime

    now_iso = datetime.now(UTC).isoformat()
    dsn = _verification_store_url()
    if not dsn.startswith(("postgresql://", "postgres://")):
        return RecentVerificationsResponse(records=[], checked_at=now_iso)

    try:
        import psycopg

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT provider_id, status, evidence_url,
                           verified_capabilities, blockers, created_at
                    FROM verification_records
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — read-side resilience
        logger = logging.getLogger(__name__)
        logger.warning("recent_verifications query failed: %s", exc)
        return RecentVerificationsResponse(records=[], checked_at=now_iso)

    records = []
    for provider_id, status_text, evidence_url, capabilities, blockers, created_at in rows:
        # ``blockers`` is JSONB so psycopg returns a Python list already;
        # tolerate strings for the rare legacy row.
        if isinstance(blockers, str):
            try:
                import json as _json

                blockers_list = list(_json.loads(blockers))
            except (ValueError, TypeError):
                blockers_list = []
        else:
            blockers_list = list(blockers or [])
        records.append(
            RecentVerificationRecord(
                provider_id=str(provider_id),
                status=str(status_text),
                evidence_url=str(evidence_url or ""),
                verified_capabilities=[str(c) for c in (capabilities or [])],
                blockers=[str(b) for b in blockers_list],
                created_at=(
                    created_at.isoformat()
                    if hasattr(created_at, "isoformat")
                    else str(created_at)
                ),
            )
        )
    return RecentVerificationsResponse(records=records, checked_at=now_iso)


# Process-singleton goal cache. We build lazily so tests can monkey-
# patch the env before the first /goal request lands. The cache
# backend is selected by PLANMYAGENTS_GOAL_CACHE_PATH per
# planner.recipe_export.goal_cache.goal_cache_from_env.
_GOAL_CACHE = None


def _goal_cache():
    global _GOAL_CACHE
    if _GOAL_CACHE is None:
        _GOAL_CACHE = goal_cache_from_env()
    return _GOAL_CACHE


def _routable_providers_by_capability() -> dict[str, list[str]]:
    """Per-capability list of provider_ids with at least one
    succeeded benchmark run in the last 30 days. Matches the
    homepage strip's routable-count semantics so the public APIs
    and the homepage agree on which capabilities count as
    currently routable.

    Used by :mod:`planmyagents_api.web.routes.demand`. Lives here
    (and not in a dedicated helpers module) because every other
    consumer of ``_evidence_dsn()`` and the connection-failure
    fallback pattern already lives in this file. If a second
    router ever needs Postgres reads it's time to lift this and
    ``_evidence_dsn`` into ``_evidence_helpers.py``.
    """

    dsn = _evidence_dsn()
    if not dsn.startswith(("postgresql://", "postgres://")):
        return {}
    out: dict[str, list[str]] = {}
    try:
        import psycopg

        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT capability, provider_id
                FROM benchmark_runs
                WHERE succeeded = true
                  AND created_at > now() - interval '30 days'
                GROUP BY capability, provider_id
                ORDER BY capability, provider_id
                """
            )
            for cap, pid in cur.fetchall():
                out.setdefault(str(cap), []).append(str(pid))
    except Exception as exc:  # noqa: BLE001 — read-side resilience
        logger.warning("routable-providers query failed: %s", exc)
        return {}
    return out


# Marketplace store singleton — backs the Pro-tier users + saved_recipes
# tables (T1-B-1). Lazy-built so tests can swap the in-memory backend in
# before the first /recipes request via `set_marketplace_store_for_tests`.
_MARKETPLACE_STORE: MarketplaceStore | None = None


def _marketplace_store() -> MarketplaceStore:
    global _MARKETPLACE_STORE
    if _MARKETPLACE_STORE is None:
        _MARKETPLACE_STORE = marketplace_store_from_env()
    return _MARKETPLACE_STORE


def set_marketplace_store_for_tests(store: MarketplaceStore | None) -> None:
    """Test-only override. Production paths use the env-driven factory."""
    global _MARKETPLACE_STORE
    _MARKETPLACE_STORE = store


# Stripe config singleton — lazy-built so we never crash at boot just
# because billing isn't configured (placeholder-safe per T1-B-5).
_STRIPE_CONFIG: StripeConfig | None = None


def _stripe_config() -> StripeConfig:
    global _STRIPE_CONFIG
    if _STRIPE_CONFIG is None:
        _STRIPE_CONFIG = stripe_config_from_env()
    return _STRIPE_CONFIG


def set_stripe_config_for_tests(config: StripeConfig | None) -> None:
    global _STRIPE_CONFIG
    _STRIPE_CONFIG = config


def _recipe_download_url(*, goal_id: str, format: str) -> str:
    return f"/recipe/export?goal_id={goal_id}&format={format}"


def _saved_recipe_to_response(recipe) -> SavedRecipeResponse:
    fmt_value = recipe.format
    goal_id = (
        recipe.recipe_json.get("goal_id")
        if isinstance(recipe.recipe_json, dict)
        else None
    ) or ""
    recipe_coverage = (
        recipe.recipe_json.get("recipe_coverage")
        if isinstance(recipe.recipe_json, dict)
        else None
    )
    return SavedRecipeResponse(
        recipe_id=recipe.recipe_id,
        workspace_id=recipe.workspace_id,
        user_id=recipe.user_id,
        goal=recipe.goal,
        format=fmt_value,
        notes=recipe.notes,
        created_at=recipe.created_at,
        updated_at=recipe.updated_at,
        recipe_coverage=recipe_coverage if isinstance(recipe_coverage, dict) else None,
        download_url=_recipe_download_url(goal_id=goal_id, format=fmt_value),
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="PlanMyAgents API",
        version="0.1.0",
        summary="Discovery, benchmark, and goal-routing endpoints for the agent trust layer.",
    )
    cors_allow_origins = [
        origin.strip()
        for origin in os.getenv(
            "PLANMYAGENTS_CORS_ALLOW_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # /health, /health/evidence, /evidence/recent-verifications now
    # live in `planmyagents_api.web.routes.health`. The 2026-05-19
    # split-into-routers refactor moves endpoints out of this
    # 3,500-line file one logical cluster at a time; the health
    # cluster was the first because it has the lowest coupling.
    from planmyagents_api.web.routes import demand as _demand_routes
    from planmyagents_api.web.routes import discovery as _discovery_routes
    from planmyagents_api.web.routes import health as _health_routes
    from planmyagents_api.web.routes import leaderboards as _leaderboards_routes

    # Per-app rate limiter for the public demand APIs. Attached to
    # `app.state` so each `TestClient(create_app())` gets its own
    # bucket dict — see `routes/demand.py` for the rationale.
    app.state.demand_rate_limiter = public_demand_rate_limiter()

    app.include_router(_health_routes.router)
    app.include_router(_discovery_routes.router)
    app.include_router(_leaderboards_routes.router)
    app.include_router(_demand_routes.router)

    # /discovery/{categories, agents, search, index-freshness} moved
    # to `planmyagents_api.web.routes.discovery` in the 2026-05-19
    # routers refactor — see the router registration above.

    # /leaderboards{,/{capability}} + /benchmark/runs moved to
    # `planmyagents_api.web.routes.leaderboards`. /open-mcp-opportunities
    # moved to `planmyagents_api.web.routes.discovery`. See the router
    # registration above for the rest of the 2026-05-19 split-up.

    # /discovery-gaps + /capability-labels moved to
    # `planmyagents_api.web.routes.discovery`. /demand/top-capabilities
    # + /demand/gaps moved to `planmyagents_api.web.routes.demand` (and
    # the per-app token-bucket rate limiter lives in
    # `app.state.demand_rate_limiter`, attached above).

    @app.post(
        "/goal",
        response_model=GoalResponse,
        tags=["goal"],
    )
    def goal(request: GoalRequest) -> GoalResponse:
        # Per-stage timing for the /goal pipeline. We log structured
        # progress at every stage boundary because the route otherwise
        # appears frozen during a 5-minute LLM call — silence is the
        # exact failure mode this instrumentation prevents. Operators
        # can `tail -f .planmyagents_runs/api.log` to watch a stuck request
        # in real time.
        request_started = time.monotonic()
        goal_preview = request.goal[:80].replace("\n", " ")
        logger.info(
            "/goal received: goal=%r execute=%s",
            goal_preview + ("…" if len(request.goal) > 80 else ""),
            request.execute,
        )

        router = ProviderRouter()
        planner_started = time.monotonic()
        logger.info("/goal planner: starting")
        try:
            # plan_goal_with_pre_plan_discovery wraps plan_goal_smart with
            # the Gap-3 re-sequencing: when the first-pass plan is
            # unsupported, scouts are dispatched against the decomposer's
            # per-sub-task search_query so the discovery store is warm
            # BEFORE the route handler does its own refusal-path discovery.
            # Hot-path executable plans are returned unchanged. The wrapper
            # is hard-gated by PLANMYAGENTS_PRE_PLAN_DISCOVERY (default
            # OFF) so a feature toggle exists for operators who want to
            # trade ~5–35s of latency on first refusal for cache warmth on
            # the next request and a richer in-response gap explanation.
            plan, metadata = plan_goal_with_pre_plan_discovery(
                request.goal, router=router
            )
        except PlanningUnavailableError as exc:
            # Honest refusal path: every LLM tier is down (or selected
            # mode is misconfigured). Surface a structured 503 so the
            # UI can render a clear "no LLM tier available" card
            # instead of silently degrading to substring rules. This is
            # the correctness contract the user explicitly chose ("when
            # both LLMs unreachable -> refuse the goal honestly").
            logger.warning(
                "/goal planner: refused after %dms (no LLM tier): %s",
                int((time.monotonic() - planner_started) * 1000),
                exc.metadata.reason,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "planning_unavailable",
                    "message": exc.metadata.reason,
                    "llm_quality": {"planner": exc.metadata.to_json()},
                    "remediation": _planning_remediation(exc.metadata),
                },
            ) from exc
        planner_elapsed_ms = int((time.monotonic() - planner_started) * 1000)
        # Surface the most useful planner decisions: how many sub-tasks
        # came out, how many capabilities are missing, and which tier
        # produced the plan. These three numbers usually tell you
        # whether the rest of the pipeline is going to be cheap or
        # expensive (lots of missing capabilities = lots of scouts).
        plan_status = getattr(plan, "status", "?")
        sub_task_count = len(getattr(plan, "sub_tasks", []) or [])
        missing_count = len(getattr(plan, "missing_capabilities", []) or [])
        tier_used = (
            (metadata.get("llm_quality") or {}).get("planner", {}).get("tier_used", "?")
            if isinstance(metadata, dict)
            else "?"
        )
        logger.info(
            "/goal planner: complete in %dms status=%s tier=%s sub_tasks=%d missing=%d",
            planner_elapsed_ms,
            plan_status,
            tier_used,
            sub_task_count,
            missing_count,
        )

        plan_payload: dict[str, Any] = {**plan.to_json(), "planner": metadata}

        # Cost preview is informational and only meaningful when there are
        # sub-tasks to estimate against. Costs are computed from the cheapest
        # *credible* (non-synthetic) ranking row per capability so mock
        # adapter prices never inflate or deflate the preview.
        if plan.sub_tasks:
            try:
                rankings = list(
                    benchmark_store_for_path(_benchmark_store_url()).load_rankings()
                )
                plan_payload["cost_estimate"] = estimate_plan_cost(
                    plan=plan, rankings=rankings
                )
            except Exception as exc:  # noqa: BLE001 — preview must never block planning
                plan_payload["cost_estimate"] = {
                    "error": "cost preview unavailable",
                    "detail": str(exc),
                }

        if not request.execute or not plan.executable:
            discovery_started = time.monotonic()
            logger.info(
                "/goal discovery: starting for %d missing capabilities", missing_count
            )
            decomposed_sub_tasks = _extract_decomposed_sub_tasks(metadata)
            discovery = _refusal_discovery(
                request.goal,
                plan,
                decomposed_sub_tasks=decomposed_sub_tasks,
                pre_plan_summary=metadata.get("pre_plan_discovery")
                if isinstance(metadata, dict)
                else None,
            )
            _log_discovery_summary(discovery, discovery_started)
            if discovery is not None:
                plan_payload["discovery"] = discovery
            plan_payload["gap_report"] = build_gap_report(plan=plan, discovery=discovery)
            _attach_recipe_export_metadata(
                plan_payload, goal_text=request.goal
            )
            total_ms = int((time.monotonic() - request_started) * 1000)
            logger.info("/goal response ready in %dms (refused, plan-only)", total_ms)
            # Background discovery refresh — fire-and-forget, debounced.
            # See post_goal_refresh.kick_off_post_goal_refresh for the
            # contract; this call returns immediately so it cannot
            # affect /goal latency. Kicking off on the refused-plan
            # path matters as much as on the executed path: a refusal
            # is exactly the case where a user is most likely to want
            # the dashboards to refresh and surface alternatives.
            kick_off_post_goal_refresh(store_url=_discovery_store_url())
            return GoalResponse(
                ok=True,
                plan=plan_payload,
                executed=False,
                answer=None,
                summary=plan.summary,
            )

        execution_started = time.monotonic()
        logger.info("/goal execution: starting (%d sub-tasks)", sub_task_count)
        execution = asyncio.run(
            WorkflowExecutor(router=router).execute(plan, workflow_id="api-goal")
        )
        execution_payload = execution.to_json()
        logger.info(
            "/goal execution: complete in %dms executed=%s",
            int((time.monotonic() - execution_started) * 1000),
            execution.executed,
        )
        if not execution.executed:
            blocked = _blocked_capabilities(plan, execution_payload)
            discovery_started = time.monotonic()
            logger.info(
                "/goal discovery: starting for %d blocked capabilities", len(blocked)
            )
            decomposed_sub_tasks = _extract_decomposed_sub_tasks(metadata)
            discovery = _refusal_discovery(
                request.goal,
                plan,
                blocked_capabilities=blocked,
                decomposed_sub_tasks=decomposed_sub_tasks,
                pre_plan_summary=metadata.get("pre_plan_discovery")
                if isinstance(metadata, dict)
                else None,
            )
            _log_discovery_summary(discovery, discovery_started)
            if discovery is not None:
                plan_payload["discovery"] = discovery
            plan_payload["gap_report"] = build_gap_report(plan=plan, discovery=discovery)
            unsupported_payload = {
                **plan_payload,
                "status": "unsupported",
                "summary": execution.summary,
                "refusal_reasons": execution.refusal_reasons,
            }
            _attach_recipe_export_metadata(unsupported_payload, goal_text=request.goal)
            total_ms = int((time.monotonic() - request_started) * 1000)
            logger.info("/goal response ready in %dms (refused, post-execute)", total_ms)
            kick_off_post_goal_refresh(store_url=_discovery_store_url())
            return GoalResponse(
                ok=True,
                plan=unsupported_payload,
                executed=False,
                execution=execution_payload,
                summary=execution.summary,
            )

        _attach_recipe_export_metadata(plan_payload, goal_text=request.goal)
        total_ms = int((time.monotonic() - request_started) * 1000)
        logger.info("/goal response ready in %dms (executed)", total_ms)
        kick_off_post_goal_refresh(store_url=_discovery_store_url())
        return GoalResponse(
            ok=True,
            plan=plan_payload,
            executed=True,
            answer=_answer_from_execution(execution_payload),
            execution=execution_payload,
            summary=_summary_text(execution_payload),
        )

    @app.post(
        "/goal/explain",
        response_model=GoalExplainResponse,
        tags=["goal"],
    )
    def goal_explain(request: GoalRequest) -> GoalExplainResponse:
        """Re-run the planner with chain-of-thought ("thinking") output
        ENABLED for this single request, so the UI can show *why* the
        model produced the plan it did.

        Why this is a separate endpoint from ``/goal``:

        * Thinking mode adds 60-180s per call on local hardware
          (the model generates a multi-thousand-token reasoning trace
          before the JSON answer). Forcing every ``/goal`` call to pay
          that cost would make the product feel broken.
        * 99% of users never need the trace — they just want the plan.
          The UI gates this endpoint behind an explicit "Why?" button
          so the latency is opt-in.
        * Non-thinking models (qwen2.5, llama3.x) silently ignore the
          ``think`` flag; we surface ``model_supports_thinking=False``
          rather than render a misleading empty panel.

        Scope: this re-runs ONLY the planner LLM call, not the full
        ``/goal`` pipeline (no discovery, no candidate judge, no
        execution). The intent mapper / judge each have their own
        reasoning that surfaces elsewhere — keeping this endpoint
        focused on planner reasoning means the trace is concise and
        the latency stays bounded to a single LLM call.
        """

        explain_started = time.monotonic()
        explain_preview = request.goal[:80].replace("\n", " ")
        logger.info(
            "/goal/explain received: goal=%r",
            explain_preview + ("…" if len(request.goal) > 80 else ""),
        )

        router = ProviderRouter()
        # Explicitly bump the per-call timeout for the explain path.
        # The default OllamaQwenClient timeout (120s, governed by
        # PLANMYAGENTS_QWEN_TIMEOUT_SECONDS) is sized for the fast
        # `think: false` /goal hot path. Thinking mode adds 60-180s on
        # local hardware, which routinely blows the 120s budget for
        # planner-shaped prompts. We size this endpoint at 5 minutes
        # so the user's "Why?" click resolves cleanly instead of
        # surfacing a misleading timeout 503. Users who want to fail
        # faster can still set PLANMYAGENTS_QWEN_EXPLAIN_TIMEOUT_SECONDS.
        explain_timeout = float(
            os.getenv("PLANMYAGENTS_QWEN_EXPLAIN_TIMEOUT_SECONDS", "300")
        )
        client = OllamaQwenClient(timeout_seconds=explain_timeout)
        try:
            messages = build_planner_messages(request.goal, router.registry)
            result = client.complete_with_thinking(messages)
            logger.info(
                "/goal/explain complete in %dms model=%s thinking_chars=%d supports_thinking=%s",
                int((time.monotonic() - explain_started) * 1000),
                result.model,
                len(result.thinking or ""),
                result.model_supports_thinking,
            )
        except LocalQwenPlannerError as exc:
            logger.warning(
                "/goal/explain refused after %dms: %s",
                int((time.monotonic() - explain_started) * 1000),
                exc,
            )
            # Mirror the /goal route's contract: when the local LLM is
            # unavailable, refuse honestly with 503 instead of a 500.
            # We don't escalate to Groq here because the whole point
            # is "show me what the LOCAL model thought" — escalating
            # would defeat the purpose.
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "explain_unavailable",
                    "message": str(exc),
                    "model": client.model,
                    "remediation": [
                        f"Run `ollama serve` and `ollama pull {client.model}`.",
                        "Verify thinking mode is supported by the model "
                        f"(`ollama show {client.model} --modelfile` should "
                        "list a Qwen3.x family).",
                        "Raise PLANMYAGENTS_QWEN_TIMEOUT_SECONDS if the "
                        "request is timing out — thinking adds 60-180s "
                        "on local hardware.",
                    ],
                },
            ) from exc

        # Best-effort: parse the JSON the model produced into a plan
        # dict so the UI can render it next to the trace. If parsing
        # fails, return None — the trace itself is the load-bearing
        # field for this endpoint, not the parsed plan.
        plan_dict: dict[str, Any] | None = None
        try:
            parsed = _safe_json_loads(result.content)
            if isinstance(parsed, dict):
                plan_dict = parsed
        except Exception:  # noqa: BLE001 — explain endpoint never blocks
            plan_dict = None

        return GoalExplainResponse(
            ok=True,
            goal=request.goal,
            model=result.model,
            thinking=result.thinking,
            content=result.content,
            plan=plan_dict,
            duration_ms=result.duration_ms,
            model_supports_thinking=result.model_supports_thinking,
        )

    @app.get("/recipe/export", tags=["recipe"])
    def recipe_export(
        goal_id: str = Query(..., description="goal_id returned by /goal"),
        workflow_option: int = Query(
            0, ge=0, description="Reserved; today there is one workflow per goal."
        ),
        format: str = Query(
            "markdown",
            description=f"One of: {', '.join(sorted(RECIPE_FORMATS))}",
        ),
    ) -> Response:
        """Render the cached plan as a downloadable recipe.

        See ``planmyagents_api.planner.recipe_export`` for the
        renderer contract and the list of supported formats. The
        endpoint is intentionally stateless on the request side —
        caller passes ``goal_id``, server looks up the cached plan
        and renders. No auth required for T1-A; T1-B layers Clerk in
        for saved-recipe history.
        """
        if format == "n8n_yaml":
            raise HTTPException(
                status_code=410,
                detail={
                    "error": "format_deprecated",
                    "message": (
                        "n8n exports are now JSON; use format=n8n_json."
                    ),
                    "replacement_format": "n8n_json",
                },
            )
        if format not in RECIPE_FORMATS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_format",
                    "message": f"Unknown recipe format: {format!r}",
                    "supported_formats": sorted(RECIPE_FORMATS),
                },
            )
        cached = _goal_cache().load(goal_id)
        if cached is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "goal_not_found",
                    "message": (
                        f"goal_id `{goal_id}` is not in cache (expired, "
                        "evicted, or never seen). Re-submit the goal via "
                        "/goal to refresh it."
                    ),
                },
            )
        try:
            context = build_recipe_context(
                goal_id=cached.goal_id,
                goal_text=cached.goal_text,
                plan_payload=cached.plan_payload,
                workflow_option_index=workflow_option,
            )
            rendered = render_recipe(context, format)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "render_failed", "message": str(exc)},
            ) from exc
        except Exception as exc:  # noqa: BLE001 — renderer must never 500 silently
            logger.exception("/recipe/export render failed for %s/%s", goal_id, format)
            raise HTTPException(
                status_code=500,
                detail={"error": "render_failed", "message": str(exc)},
            ) from exc
        filename = f"planmyagents-{goal_id}{rendered.filename_suffix}"
        coverage = context.coverage
        return Response(
            content=rendered.body,
            media_type=rendered.content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "private, max-age=300",
                "X-PlanMyAgents-Goal-Id": goal_id,
                "X-PlanMyAgents-Format": format,
                "X-PlanMyAgents-Recipe-Status": coverage.status,
                "X-PlanMyAgents-Step-Count": str(coverage.step_count),
                "X-PlanMyAgents-Recommended-Step-Count": str(
                    coverage.recommended_step_count
                ),
                "X-PlanMyAgents-Exportable-Step-Count": str(
                    coverage.exportable_step_count
                ),
                "X-PlanMyAgents-Gap-Count": str(coverage.gap_count),
            },
        )

    # /recipes CRUD now lives in `planmyagents_api.web.routes.recipes`.
    # /account/me + /billing/* now live in `planmyagents_api.web.routes.billing`.
    # See the 2026-05-19 routers refactor in `web/routes/__init__.py`.
    from planmyagents_api.web.routes import billing as _billing_routes
    from planmyagents_api.web.routes import recipes as _recipes_routes

    app.include_router(_recipes_routes.router)
    app.include_router(_billing_routes.router)

    return app


def _safe_json_loads(raw: str) -> Any:
    """Tolerant JSON parse for explain-endpoint responses.

    Wraps :func:`json.loads` to never raise on the explain happy path
    — the trace itself is more valuable than a strictly-parsed plan
    dict, and the planner already validates JSON elsewhere. Returns
    ``None`` for unparseable input.
    """

    import json as _json

    try:
        return _json.loads(raw)
    except (ValueError, TypeError):
        return None


def _log_discovery_summary(
    discovery: dict[str, Any] | None, started_at: float
) -> None:
    """Emit a one-line summary of how the discovery + judge sub-stage
    behaved for a /goal request. Pulled out into a helper because both
    the pre-execute refusal branch and the post-execute fail branch
    need to log identically — and silence here was specifically what
    made the screenshot in
    https://… look "stuck for 5 minutes with no
    information"."""

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    if discovery is None:
        logger.info("/goal discovery: complete in %dms (no payload)", elapsed_ms)
        return
    total = discovery.get("total_candidates")
    judge = discovery.get("candidate_judge") or {}
    judge_status = judge.get("status", "?")
    accepted = judge.get("accepted", "—")
    rejected = judge.get("rejected", "—")
    live = discovery.get("live_discovery") or {}
    scouts_dispatched = live.get("scouts_dispatched", 0)
    candidates_found = live.get("candidates_found_total", 0)
    logger.info(
        "/goal discovery: complete in %dms total_candidates=%s judge=%s accepted=%s "
        "rejected=%s live_scouts_dispatched=%s live_candidates_found=%s",
        elapsed_ms,
        total,
        judge_status,
        accepted,
        rejected,
        scouts_dispatched,
        candidates_found,
    )


def _planning_remediation(metadata: Any) -> list[str]:
    """Build human-readable remediation steps for a 503 refusal payload.

    The shape of ``metadata`` is :class:`PlanningUnavailableMetadata`
    but we type it loosely to avoid an import cycle in this hot path.
    """

    steps: list[str] = []
    primary_label = getattr(metadata, "primary_label", "")
    primary_attempted = getattr(metadata, "primary_attempted", False)
    primary_error = getattr(metadata, "primary_error", "") or ""
    fallback_label = getattr(metadata, "fallback_label", "")
    mode = getattr(metadata, "mode", "")
    if mode == "groq" and "GROQ_API_KEY" in (
        getattr(metadata, "reason", "") or ""
    ):
        steps.append(
            "Set GROQ_API_KEY in .env (free tier: https://console.groq.com)."
        )
    if primary_attempted and "ConnectionRefused" in primary_error:
        steps.append(
            f"Local model '{primary_label}' is unreachable. Start Ollama "
            f"(`ollama serve`) and ensure the model is pulled."
        )
    if fallback_label and not getattr(metadata, "fallback_attempted", False):
        steps.append(
            f"Set GROQ_API_KEY to enable the '{fallback_label}' fallback tier."
        )
    if not steps:
        steps.append(
            "Restore at least one LLM tier (local Ollama or hosted Groq) "
            "and retry."
        )
    return steps


def _blocked_capabilities(plan: GoalPlan, execution: dict[str, Any]) -> set[str]:
    capabilities = {
        str(result.get("capability"))
        for result in execution.get("sub_task_results", [])
        if result.get("capability")
    }
    if capabilities:
        return capabilities
    return {task.capability for task in plan.sub_tasks} | set(
        plan.missing_capabilities
    )


def _refusal_discovery(
    goal: str,
    plan: GoalPlan,
    *,
    blocked_capabilities: set[str] | None = None,
    decomposed_sub_tasks: list[dict[str, Any]] | None = None,
    pre_plan_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run discovery whenever a goal is refused so the UI has a path forward.

    Today this only queries the **local discovery store**; live web/GitHub/MCP
    research is opt-in via separate scripts and is *not* triggered by /goal.
    The returned payload makes that explicit via ``live_research`` so the UI
    can show a clear "we did not call live search" banner instead of leaving
    the user to wonder.

    ``decomposed_sub_tasks`` carries the rich per-sub-task objects emitted
    by :func:`planmyagents_api.planner.goal_decomposer.decompose_goal`. When
    provided, each sub-task's LLM-written ``search_query`` and
    ``acceptance_criteria`` are threaded into the scout dispatcher and
    candidate judge respectively, replacing the old slug-only contract.
    The shape mirrors :class:`DecomposedSubTask.to_json` so it round-trips
    cleanly into the response payload for the UI to render.

    ``pre_plan_summary`` is the dict returned by
    :func:`~planmyagents_api.discovery.pre_plan_discovery.run_pre_plan_discovery`
    when the Gap-3 wrapper already dispatched scouts before the planner
    refused. When the wrapper status is ``"ran"`` we skip the local
    ``_live_discovery`` re-dispatch (the persistent store is already
    warm AND the upstream sources hate being hammered twice on the same
    request) and surface the pre-plan summary as the live-discovery
    payload so the response shape stays stable for the UI.
    """

    capabilities = blocked_capabilities or (
        {task.capability for task in plan.sub_tasks}
        | set(plan.missing_capabilities)
    )
    capabilities = {item for item in capabilities if item}
    if not capabilities:
        return None

    sub_tasks_by_capability = _index_sub_tasks_by_capability(decomposed_sub_tasks)

    payload = search_candidates(
        capabilities=capabilities,
        task_description=goal,
        store_path=_discovery_store_url(),
        limit=50,
        persist=False,
        load_store=True,
        include_stale=True,
    )

    # Path A separation. The discovery_candidates table now only holds
    # agentic rows (mcp_server / a2a_agent / ai_agent) — the postgres
    # CHECK constraint enforces it and the storage facade routes on
    # save. But `payload["results"]` can still contain api_provider
    # rows that came in via `index.ingest_sources(...)` (e.g.
    # StaticDiscoverySource), so we filter defensively here. The
    # "APIs without agents" block is built from the parallel
    # apis_without_agents store + any non-agentic rows that flowed
    # through the index this request.
    raw_results = list(payload.get("results", []))
    agentic_results = [
        r for r in raw_results
        if str(r.get("provider_type") or "") in AGENTIC_PROVIDER_TYPES
    ]
    inline_api_results = [
        r for r in raw_results
        if str(r.get("provider_type") or "") in {"api_provider", "payment_provider"}
    ]
    sorted_capabilities = sorted(capabilities)

    # LLM-driven retrieval-time relevance judge. Replaces blind trust in
    # substring-derived capability tags. Without this filter, a school
    # project tagged ``fare_comparison`` because its description happens
    # to contain "compare" would surface as a candidate for "buy
    # cheapest single malt". The user explicitly chose
    # "every_request_no_persist" semantics: judge runs per request,
    # verdicts are not persisted to the DB, so the same candidate may be
    # accepted for one goal and rejected for another.
    judge_metadata: dict[str, Any]
    agentic_results, judge_metadata = _apply_candidate_judge(
        goal=goal,
        required_capabilities=sorted_capabilities,
        agentic_results=agentic_results,
        store_url=_discovery_store_url(),
        acceptance_criteria_by_capability=_extract_field_by_capability(
            sub_tasks_by_capability, field="acceptance_criteria"
        ),
    )

    agentic_by_capability = _group_by_capability(agentic_results, sorted_capabilities)

    store = discovery_store_for_path(_discovery_store_url())
    apis_records = store.load_apis_without_agents()
    apis_without_agents_records, apis_by_capability = (
        _filter_api_records_for_capabilities(
            apis_records, sorted_capabilities
        )
    )
    # Merge in any inline api_provider rows that came from per-request
    # source ingestion (e.g. StaticDiscoverySource) but aren't in the
    # persistent store yet. Convert them to ApiWithoutAgentRecord shape
    # so the block is uniform.
    if inline_api_results:
        existing_ids = {r.provider_id for r in apis_without_agents_records}
        for result in inline_api_results:
            provider_id = str(result.get("provider_id") or "")
            if not provider_id or provider_id in existing_ids:
                continue
            inline_record = _api_record_from_result_payload(result)
            apis_without_agents_records.append(inline_record)
            for capability in inline_record.capabilities:
                if capability in apis_by_capability:
                    apis_by_capability[capability].append(inline_record)
            existing_ids.add(provider_id)
    apis_without_agents_block = _build_apis_without_agents_block(
        apis_without_agents_records, apis_by_capability
    )

    # Always-on demand recording. The user explicitly asked for the
    # "what doesn't exist?" signal to be persistent — without this, the
    # Open MCP Opportunities ranking has no demand input and the gap
    # claim is just rhetoric.
    demand_recorded = record_refusal_demand(
        goal=goal,
        missing_capabilities=sorted_capabilities,
        candidates_by_capability=agentic_by_capability,
        apis_without_agents_by_capability=apis_by_capability,
        requester=None,  # No request context here yet; future: pass session hash
    )

    # Live request-time discovery via the scout fleet. Default-on, opt out
    # by setting PLANMYAGENTS_LIVE_DISCOVERY=false (tests do this so they
    # don't make real HTTP calls). Failures here must never block /goal.
    #
    # The Gap-3 wrapper may already have dispatched scouts before the
    # planner refused. When that's the case we surface the pre-plan
    # summary as the live-discovery payload (with a "source": "pre_plan"
    # marker) and skip a redundant dispatch — the same scouts hitting
    # the same sources twice in a single request hurts upstream rate
    # limits without producing new results, since pre-plan already
    # save_merge'd whatever it found.
    live_discovery_payload: dict[str, Any] | None = None
    pre_plan_already_dispatched = bool(
        pre_plan_summary
        and pre_plan_summary.get("status") == "ran"
        and pre_plan_summary.get("capabilities_dispatched")
    )
    if pre_plan_already_dispatched:
        live_discovery_payload = {
            "status": "skipped_pre_plan_dispatch_already_ran",
            "source": "pre_plan",
            "pre_plan_summary": pre_plan_summary,
        }
    elif _live_discovery_enabled():
        existing_provider_ids = {
            str(result.get("provider_id"))
            for result in payload.get("results", [])
            if result.get("provider_id")
        }
        try:
            live_discovery_payload = _live_discovery(
                goal=goal,
                missing_capabilities=sorted_capabilities,
                existing_provider_ids=existing_provider_ids,
                search_queries_by_capability=_extract_field_by_capability(
                    sub_tasks_by_capability, field="search_query"
                ),
            )
        except Exception as exc:  # noqa: BLE001 — must never block /goal
            live_discovery_payload = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }

    # Always-on discovery gap recording. This captures the *outcome*
    # for each missing capability (post-judge accepted count + scout
    # dispatch shape) so the leaderboard can surface "capabilities
    # the world hasn't built yet" as a structured tile rather than
    # buried log noise. We record after live_discovery so the gap row
    # reflects what actually happened (scouts ran, judge filtered),
    # not just what the planner asked for. Recording is best-effort
    # via record_discovery_gap (exceptions are swallowed there).
    discovery_gaps_recorded = _record_discovery_gap_for_request(
        goal=goal,
        missing_capabilities=sorted_capabilities,
        agentic_by_capability=agentic_by_capability,
        live_discovery_payload=live_discovery_payload,
        judge_metadata=judge_metadata,
    )

    # Human fallback suggestions. Run only when the system genuinely has
    # no specialist agent for the user — i.e. zero accepted agentic
    # results across every missing capability. When agents *do* exist we
    # surface those instead; cluttering the UI with "or you could do
    # this manually here" alongside a real agent list is worse than
    # showing only one of the two. The suggester is best-effort: any
    # LLM failure degrades to ``status="unavailable"`` and the rest of
    # the refusal payload is returned unchanged.
    human_alternatives_payload = _build_human_alternatives_payload(
        goal=goal,
        missing_capabilities=sorted_capabilities,
        decomposed_sub_tasks=decomposed_sub_tasks,
        has_specialist_results=bool(agentic_results),
    )

    # Aggregated demand-per-missing-capability snapshot. The recorder
    # above wrote *this* request's demand event; the loader here
    # returns the running totals (request_count, distinct_requester_count,
    # sample_goals) across every request the system has ever served.
    # The UI uses this to render "47 other users have asked for an
    # agent like this" alongside the human-fallback card so the user
    # sees both their workaround AND the broader signal that the gap
    # is real.
    demand_per_capability = _load_demand_per_missing_capability(sorted_capabilities)

    # Slice 2: horizontal research-agent backstop. When no specialist
    # agent fits the goal, we can still produce a useful answer by
    # running a real web search and asking the LLM to synthesise the
    # result. This is a *last resort* — quality is by definition lower
    # than a domain specialist would offer, but it's dramatically
    # better than refusing the request outright.
    #
    # Gating mirrors the suggester: only invoke when (a) we genuinely
    # have no specialist results, (b) the agent module is enabled and
    # has credentials, (c) we're under the per-request capability cap.
    # Failure paths return structured "unavailable" / "missing_credentials"
    # rather than crashing the refusal payload.
    research_backstop_payload = _build_research_backstop_payload(
        goal=goal,
        missing_capabilities=sorted_capabilities,
        decomposed_sub_tasks=decomposed_sub_tasks,
        has_specialist_results=bool(agentic_results),
    )

    return {
        "status": "discovery_ran",
        "persisted_to_registry": False,
        "persisted_to_store": _discovery_store_url(),
        "missing_capabilities": sorted_capabilities,
        # Mirror of the planner-level decomposition so the discovery
        # block in the UI can render the rich per-sub-task narrative
        # ("Search the web for OEM fleet programs in India") inline
        # with the candidates it surfaced for that sub-task.
        "decomposed_sub_tasks": list(decomposed_sub_tasks or []),
        "searched_capabilities": payload["searched_capabilities"],
        "normalizations": payload["normalizations"],
        "will_fail": True,
        "will_fail_reasons": [
            "Discovered candidates are not routable until reviewed, adapted, "
            "benchmarked, and configured.",
            "The runtime refuses execution rather than calling unverified results.",
        ],
        # Headline count is now agent-only. The total includes api_provider
        # rows for engineering transparency but the user-facing UI should
        # show `total_agentic_candidates`.
        "total_candidates": payload["total_candidates"],
        "total_agentic_candidates": len(agentic_results),
        "candidates": agentic_results,
        "apis_without_agents": apis_without_agents_block,
        "demand": {
            "events_recorded": demand_recorded,
            "open_mcp_opportunities_link": "/open-mcp-opportunities",
            # Per-missing-capability aggregate ("how many other users have
            # asked for this?"), computed AFTER record_refusal_demand so
            # the totals include this request. Empty list when the demand
            # store is unreadable; UI must treat absence as "0 prior
            # requests, you're the first" rather than rendering a
            # spinner.
            "per_capability_summary": demand_per_capability,
        },
        "discovery_gaps": {
            "events_recorded": discovery_gaps_recorded,
            "leaderboard_link": "/discovery-gaps",
        },
        # LLM-suggested manual workarounds for when no specialist agent
        # fits the user's goal. Surface for the UI as a "Try doing this
        # manually here" card on the refusal screen. See
        # planmyagents_api.planner.human_fallback_suggester for the
        # full design rationale; the short story is: when the system
        # honestly cannot satisfy the user, give them somewhere to go.
        "human_alternatives": human_alternatives_payload,
        # Slice 2: horizontal research-agent backstop. Per-capability
        # search-+-synthesis output produced by the first-party
        # GeneralResearchAgent when no specialist exists. The UI
        # renders this with explicit "general research, not a
        # specialist" framing so users don't mistake the answer for
        # an authoritative agent verdict. See
        # planmyagents_api.research.general_research_agent for the
        # full design rationale.
        "research_backstop": research_backstop_payload,
        # Provenance for the indexed pool — answers "WHEN and WHERE did
        # these N candidates come from?" without forcing the user to
        # crack open SQL. Computed lazily so a quiet /goal call doesn't
        # pay for a histogram pass.
        "index_freshness": _index_freshness(),
        "live_research": _live_research_status(),
        "live_discovery": live_discovery_payload,
        # LLM provenance for the retrieval-time relevance judge. The
        # frontend renders this as a green/amber/red pill so the user
        # always knows which tier filtered their results and whether
        # any junk slipped through because the judge couldn't run.
        "candidate_judge": judge_metadata,
    }


def _extract_decomposed_sub_tasks(
    metadata: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Pull the decomposer's sub-task list out of planner metadata.

    Returns ``[]`` when the decomposer didn't run (executable plan,
    decomposer disabled, or LLM tier unavailable). Callers should
    treat an empty list as "no rich sub-tasks; fall back to the
    legacy slug-only behaviour" — every downstream consumer must
    keep working without the rich shape.
    """

    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("decomposed_sub_tasks")
    if not isinstance(raw, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, dict):
            cleaned.append(entry)
    return cleaned


def _index_sub_tasks_by_capability(
    sub_tasks: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Index sub-tasks by their ``suggested_capability_id``.

    When two sub-tasks declare the same capability id (e.g. two
    web_search calls with different queries), the FIRST one wins —
    that mirrors declaration order, which the decomposer guarantees
    is execution order, so the first occurrence is the one the user
    sees in the workflow narrative.
    """

    out: dict[str, dict[str, Any]] = {}
    for entry in sub_tasks or []:
        if not isinstance(entry, dict):
            continue
        capability_id = str(entry.get("suggested_capability_id") or "").strip()
        if not capability_id:
            continue
        out.setdefault(capability_id, entry)
    return out


def _extract_field_by_capability(
    sub_tasks_by_capability: dict[str, dict[str, Any]],
    *,
    field: str,
) -> dict[str, str]:
    """Project one string field out of an indexed sub-task map.

    Returns a stable ``{capability_id: value}`` dict suitable for
    passing to scouts (``search_query``) or the judge
    (``acceptance_criteria``). Empty values are dropped so the
    downstream caller can treat "key absent" and "value empty" as
    the same condition.
    """

    out: dict[str, str] = {}
    for capability_id, sub_task in sub_tasks_by_capability.items():
        value = sub_task.get(field)
        if isinstance(value, str) and value.strip():
            out[capability_id] = value.strip()
    return out


def _blocked_candidate_judge_summary(
    *,
    status: str,
    rejected: int,
    warning: str,
    **extra: Any,
) -> dict[str, Any]:
    """Metadata for cases where candidates could not be safely judged."""

    return {
        "status": status,
        "accepted": 0,
        "rejected": rejected,
        "blocked_unjudged": rejected,
        "untracked_passthrough": 0,
        "fail_closed": True,
        "warning": warning,
        **extra,
    }


def _apply_candidate_judge(
    *,
    goal: str,
    required_capabilities: list[str],
    agentic_results: list[dict[str, Any]],
    store_url: str,
    acceptance_criteria_by_capability: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the LLM relevance judge over ``agentic_results``.

    Live scouts can return inline candidates before the discovery store
    has persisted them. Those rows must still be judged before they can
    become recommendations; otherwise the noisiest, freshest candidates
    bypass the relevance safety net.
    """

    if not agentic_results:
        return agentic_results, {
            "status": "skipped_no_results",
            "accepted": 0,
            "rejected": 0,
        }
    if not is_judge_enabled():
        return [], _blocked_candidate_judge_summary(
            status="disabled",
            rejected=len(agentic_results),
            warning=(
                "PLANMYAGENTS_CANDIDATE_JUDGE=off — results are not "
                "safe to recommend. Candidate recommendations are blocked "
                "until the judge is enabled."
            ),
        )

    # Reload the matching DiscoveryCandidates from the store so the
    # judge sees the rich object (docs, observations, tools) rather
    # than the public-summary projection.
    try:
        from planmyagents_api.discovery.store import discovery_store_for_path

        store = discovery_store_for_path(store_url)
        all_candidates = store.load()
    except Exception as exc:  # noqa: BLE001 - judge must never crash /goal
        return [], _blocked_candidate_judge_summary(
            status="store_unavailable",
            rejected=len(agentic_results),
            warning=(
                "Could not load the discovery store for candidate judgment. "
                f"Candidate recommendations are blocked: {exc}"
            ),
        )

    candidates_by_id = {candidate.id: candidate for candidate in all_candidates}
    candidates_to_judge: list[DiscoveryCandidate] = []
    untracked: list[DiscoveryCandidate] = []
    unjudgeable: list[dict[str, Any]] = []
    seen_candidate_ids: set[str] = set()
    for result_payload in agentic_results:
        provider_id = _provider_id_from_result(result_payload)
        if not provider_id or provider_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(provider_id)
        candidate = candidates_by_id.get(provider_id)
        if candidate is None:
            try:
                candidate = _candidate_from_agentic_result(
                    result_payload,
                    requested_capabilities=required_capabilities,
                )
            except CandidateNormalizationError:
                unjudgeable.append(result_payload)
                continue
            untracked.append(candidate)
        candidates_to_judge.append(candidate)

    if not candidates_to_judge:
        return [], {
            "status": "blocked_no_judgeable_candidates",
            "accepted": 0,
            "rejected": len(agentic_results),
            "blocked_untracked": len(agentic_results),
            "untracked_passthrough": 0,
            "warning": (
                "Candidate judge had no normalizable candidates to evaluate; "
                "blocking recommendation results."
            ),
        }

    try:
        client = build_default_escalating_client()
        judge = CandidateJudge(chat_client=client)
        result = judge.judge(
            goal=goal,
            required_capabilities=required_capabilities,
            candidates=candidates_to_judge,
            acceptance_criteria_by_capability=acceptance_criteria_by_capability or {},
        )
    except NoLlmTierAvailableError as exc:
        return [], _blocked_candidate_judge_summary(
            status="unavailable",
            rejected=len(agentic_results),
            warning=(
                "Candidate judge could not run because no LLM tier is "
                "reachable. Candidate recommendations are blocked until "
                "judgment can run."
            ),
            error=str(exc),
            primary_label=exc.metadata.primary_label,
            fallback_label=exc.metadata.fallback_label,
        )

    accepted_ids = {c.id for c in result.accepted}
    filtered = [
        r for r in agentic_results
        if _provider_id_from_result(r) in accepted_ids
    ]
    untracked_ids = {candidate.id for candidate in untracked}
    blocked_untracked = len(untracked_ids - accepted_ids) + len(unjudgeable)
    summary = result.to_summary()
    summary["status"] = "applied"
    summary["untracked_judged"] = len(untracked)
    summary["untracked_accepted"] = len(untracked_ids & accepted_ids)
    summary["blocked_untracked"] = blocked_untracked
    summary["untracked_passthrough"] = 0
    summary["unjudgeable_blocked"] = len(unjudgeable)
    # Per-capability counts so the discovery-gaps recorder can store
    # exact ``judge_evaluated`` and ``judge_accepted`` numbers for
    # each missing capability — not the request-level approximation
    # we used to fall back to. A candidate that claims multiple
    # capabilities counts in every matching bucket; that is the
    # correct semantic for "how many candidates did we evaluate that
    # claimed to support capability X?".
    summary["evaluated_by_capability"] = _per_capability_counts(
        candidates_to_judge, required_capabilities
    )
    summary["accepted_by_capability"] = _per_capability_counts(
        result.accepted, required_capabilities
    )
    return filtered, summary


def _provider_id_from_result(result: dict[str, Any]) -> str:
    return str(result.get("provider_id") or result.get("id") or "").strip()


def _candidate_from_agentic_result(
    result: dict[str, Any],
    *,
    requested_capabilities: list[str],
) -> DiscoveryCandidate:
    """Rehydrate a public-summary/live-scout result for CandidateJudge.

    The discovery store has richer ``DiscoveryCandidate`` rows, but live
    request-time scouts may return a public-summary-shaped dict before
    persistence catches up. Build the minimum normalized candidate so the
    judge sees the same provider instead of letting it pass through.
    """

    provider_id = _provider_id_from_result(result)
    source_ids = result.get("source_ids") if isinstance(result.get("source_ids"), list) else []
    source = str(result.get("source") or (source_ids[0] if source_ids else "") or "inline_request")
    evidence_url = str(result.get("evidence_url") or result.get("vendor_url") or "").strip()
    observations = result.get("observations")
    if not isinstance(observations, list):
        observations = [{"source_id": source, "evidence_url": evidence_url}]
    raw = {
        "id": provider_id,
        "display_name": result.get("display_name") or provider_id,
        "vendor": result.get("vendor") or result.get("display_name") or provider_id,
        "vendor_url": result.get("vendor_url") or evidence_url,
        "provider_type": result.get("provider_type") or "api_provider",
        "capabilities": result.get("capabilities") or [],
        "required_env_vars": result.get("required_env_vars") or [],
        "compatible_provider_ids": result.get("compatible_provider_ids") or [],
        "verification_status": result.get("verification_status") or "unverified",
        "evidence_url": evidence_url,
        "will_fail_reasons": result.get("will_fail_reasons") or [],
        "docs": result.get("docs") if isinstance(result.get("docs"), dict) else {},
        "tools": result.get("tools") or [],
        "skills": result.get("skills") or [],
        "openapi_url": result.get("openapi_url") or "",
        "observations": observations,
        "metadata": result.get("metadata") if isinstance(result.get("metadata"), dict) else {},
    }
    candidate = normalize_candidate(
        raw,
        source=source,
        requested_capabilities=requested_capabilities,
    )
    return DiscoveryCandidate(
        **{
            **candidate.__dict__,
            "lifecycle_status": str(result.get("lifecycle_status") or candidate.lifecycle_status),
            "route_status": str(result.get("route_status") or candidate.route_status),
            "will_fail": bool(result.get("will_fail", candidate.will_fail)),
            "will_fail_reasons": [
                str(item)
                for item in (result.get("will_fail_reasons") or candidate.will_fail_reasons)
                if str(item).strip()
            ],
            "adapter_module": str(result.get("adapter_module") or candidate.adapter_module),
            "benchmark_status": str(result.get("benchmark_status") or candidate.benchmark_status),
        }
    )


def _per_capability_counts(
    candidates: list[Any], required_capabilities: list[str]
) -> dict[str, int]:
    """Count how many candidates in ``candidates`` claim each capability
    in ``required_capabilities``.

    Used by the candidate-judge metadata to give the discovery-gap
    recorder exact per-capability ``judge_evaluated`` and
    ``judge_accepted`` numbers, replacing the previous request-level
    approximation. Returns a stable dict with one key per requested
    capability (zero-filled), so the recorder never has to worry about
    missing keys.
    """

    counts = dict.fromkeys(required_capabilities, 0)
    for candidate in candidates:
        capability_ids = {
            getattr(cap, "id", "") for cap in getattr(candidate, "capabilities", [])
        }
        for capability in required_capabilities:
            if capability in capability_ids:
                counts[capability] += 1
    return counts


def _group_by_capability(
    results: list[dict[str, Any]], capability_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Bucket a list of candidate payloads under each capability id they
    claim. A candidate that supports multiple capabilities appears in
    every matching bucket — we want each capability's view to be self-
    contained for the demand log and the public Open MCP Opportunities
    page."""

    capability_set = {c for c in capability_ids if c}
    buckets: dict[str, list[dict[str, Any]]] = {c: [] for c in capability_set}
    for result in results:
        caps = result.get("capabilities") or []
        if not isinstance(caps, list):
            continue
        for capability in caps:
            key = str(capability)
            if key in capability_set:
                buckets[key].append(result)
    return buckets


def _api_record_from_result_payload(payload: dict[str, Any]):
    """Build an in-memory `ApiWithoutAgentRecord` from a search-result
    payload. Used when an inline `api_provider` row arrives from
    `StaticDiscoverySource` (or similar per-request source) and isn't
    in the persistent store yet — we still want it to surface in the
    `apis_without_agents` block of the refusal response."""

    from planmyagents_api.discovery.apis_without_agents_store import ApiWithoutAgentRecord

    capabilities = payload.get("capabilities") or []
    if not isinstance(capabilities, list):
        capabilities = []
    return ApiWithoutAgentRecord(
        dedupe_key=str(payload.get("provider_id") or ""),
        provider_id=str(payload.get("provider_id") or ""),
        display_name=str(payload.get("display_name") or ""),
        vendor=str(payload.get("vendor") or ""),
        vendor_url=str(payload.get("vendor_url") or ""),
        provider_type=str(payload.get("provider_type") or "api_provider"),
        openapi_url=str(payload.get("evidence_url") or ""),
        capabilities=tuple(str(c) for c in capabilities),
        source_id=str(payload.get("source") or ""),
        first_seen_at=str(payload.get("first_seen_at") or ""),
        last_seen_at=str(payload.get("last_seen_at") or ""),
    )


def _filter_api_records_for_capabilities(
    records: list[Any],
    capability_ids: list[str],
) -> tuple[list[Any], dict[str, list[Any]]]:
    """Return only the API records that touch one of `capability_ids`,
    plus a per-capability bucketing of those records. Drops records
    that have already been superseded (an agent now wraps the API).
    """

    capability_set = {c for c in capability_ids if c}
    filtered: list[Any] = []
    by_capability: dict[str, list[Any]] = {c: [] for c in capability_set}
    for record in records:
        if getattr(record, "superseded_by_provider_id", ""):
            continue
        record_caps = set(record.capabilities)
        intersect = record_caps & capability_set
        if not intersect:
            continue
        filtered.append(record)
        for capability_id in intersect:
            by_capability[capability_id].append(record)
    return filtered, by_capability


def _build_apis_without_agents_block(
    records: list[Any],
    by_capability: dict[str, list[Any]],
) -> dict[str, Any]:
    """Compose the `apis_without_agents` summary that the refusal payload
    surfaces in place of inline `api_provider` rows.

    Shape: a count, a per-capability breakdown of the top-N vendors, and
    a link the UI can render as "X vendors have an OpenAPI spec for
    these capabilities but nobody has shipped an MCP yet — see Open MCP
    Opportunities."
    """

    per_capability: list[dict[str, Any]] = []
    for capability_id, items in by_capability.items():
        if not items:
            continue
        per_capability.append(
            {
                "capability": capability_id,
                "count": len(items),
                "examples": [_compact_api_row(item) for item in items[:5]],
            }
        )
    per_capability.sort(key=lambda entry: (-entry["count"], entry["capability"]))

    return {
        "count": len(records),
        "per_capability": per_capability,
        "link": "/open-mcp-opportunities",
        "explainer": (
            "These vendors expose an OpenAPI spec but no MCP/A2A wrapper. "
            "They aren't agent-ready, so they're not listed as candidates. "
            "They're an opportunity for the community to ship an MCP."
        ),
    }


def _compact_api_row(record: Any) -> dict[str, Any]:
    """Trim an `ApiWithoutAgentRecord` to the four fields the public
    Open MCP Opportunities page actually renders. Keeps the refusal
    response small."""

    return {
        "provider_id": record.provider_id,
        "display_name": record.display_name,
        "vendor": record.vendor,
        "openapi_url": record.openapi_url,
        "capabilities": list(record.capabilities),
    }


def _record_discovery_gap_for_request(
    *,
    goal: str,
    missing_capabilities: list[str],
    agentic_by_capability: dict[str, list[dict[str, Any]]],
    judge_metadata: dict[str, Any],
    live_discovery_payload: dict[str, Any] | None,
) -> int:
    """Record one ``DiscoveryGapEvent`` per missing capability.

    Bridges the request-side state (already computed for the response
    payload) to the gap recorder's per-capability shape.

    Three counts per capability:

    1. ``judge_accepted`` — exact, derived from the judge's
       ``accepted_by_capability`` map when the judge ran successfully.
       Falls back to ``len(agentic_by_capability[cap])`` (post-judge
       survivors grouped by tag) when the judge map is missing —
       e.g. judge was disabled or unavailable for this request.
    2. ``judge_evaluated`` — exact, derived from the judge's
       ``evaluated_by_capability`` map. Counts how many candidates
       claiming this capability were actually inspected by the LLM
       judge. Falls back to ``judge_accepted`` when the map is
       missing, which under-reports but is honest about what we
       know.
    3. ``scouts_dispatched`` / ``scouts_returned_zero`` — exact when
       live discovery ran (read from ``live_discovery_payload.per_capability``);
       both zero when live discovery was disabled or skipped.

    Returns the count of events written. Never raises — gap recording
    is a best-effort side-effect, exactly like demand recording.
    """

    if not missing_capabilities:
        return 0

    accepted_map = judge_metadata.get("accepted_by_capability") or {}
    evaluated_map = judge_metadata.get("evaluated_by_capability") or {}

    judge_accepted_by_capability: dict[str, int] = {}
    judge_evaluated_by_capability: dict[str, int] = {}
    for cap in missing_capabilities:
        accepted = (
            int(accepted_map[cap])
            if cap in accepted_map
            else len(agentic_by_capability.get(cap, []) or [])
        )
        evaluated = int(evaluated_map[cap]) if cap in evaluated_map else accepted
        judge_accepted_by_capability[cap] = accepted
        judge_evaluated_by_capability[cap] = evaluated

    scout_dispatch_by_capability: dict[str, dict[str, int]] = {}
    if live_discovery_payload and isinstance(live_discovery_payload, dict):
        for entry in live_discovery_payload.get("per_capability", []) or []:
            capability = str(entry.get("capability") or "")
            if not capability:
                continue
            scouts = (entry.get("dispatch") or {}).get("scouts") or []
            dispatched = sum(
                1
                for scout in scouts
                if str(scout.get("status") or "") != "skipped"
            )
            returned_zero = sum(
                1
                for scout in scouts
                if str(scout.get("status") or "") == "ok"
                and int(scout.get("candidate_count") or 0) == 0
            )
            scout_dispatch_by_capability[capability] = {
                "dispatched": dispatched,
                "returned_zero": returned_zero,
            }

    return record_discovery_gap(
        goal=goal,
        missing_capabilities=missing_capabilities,
        judge_accepted_by_capability=judge_accepted_by_capability,
        judge_evaluated_by_capability=judge_evaluated_by_capability,
        scout_dispatch_by_capability=scout_dispatch_by_capability,
    )


def _build_human_alternatives_payload(
    *,
    goal: str,
    missing_capabilities: list[str],
    decomposed_sub_tasks: list[dict[str, Any]] | None,
    has_specialist_results: bool,
) -> dict[str, Any]:
    """Run the LLM-driven human-fallback suggester for a refused goal.

    Gating:

    * If ``has_specialist_results`` is True the system *does* have at
      least one routable agent for the user. The MissingCapabilities
      block on the UI shows those agents; appending "or you could go
      to these websites instead" alongside them would clutter the
      surface and undercut the agent recommendation. Skip the
      suggester in that case.
    * If the env-var ``PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER`` is off
      we short-circuit before the LLM is even constructed — useful
      for tests and for operators measuring suggester latency
      independently.

    Defensive: any unexpected exception inside the suggester is
    swallowed here as well as inside the suggester itself, because
    the refusal payload must always be returned even when an
    opportunistic UX optimisation breaks.
    """

    if has_specialist_results:
        return HumanFallbackSuggestion(
            status="skipped_specialist_results"
        ).to_json()
    if not suggester_enabled():
        return HumanFallbackSuggestion(status="disabled").to_json()
    try:
        suggestion = suggest_human_alternatives(
            goal=goal,
            missing_capabilities=missing_capabilities,
            decomposed_sub_tasks=decomposed_sub_tasks,
        )
    except Exception as exc:  # noqa: BLE001 — must never block /goal
        logger.warning(
            "human_fallback_suggester unexpected failure: %s: %s",
            type(exc).__name__,
            exc,
        )
        return HumanFallbackSuggestion(
            status="unavailable",
            reason=f"{type(exc).__name__}: {exc}",
        ).to_json()
    return suggestion.to_json()


def _load_demand_per_missing_capability(
    missing_capabilities: list[str],
) -> list[dict[str, Any]]:
    """Project the global demand summary down to *this* request's
    missing capabilities.

    The aggregate ``load_demand_summary()`` returns a list ordered by
    total demand (most-requested first); we filter it to the slugs
    we care about so the UI can render "47 requests, 12 distinct
    users" right next to the matching capability row without doing
    a second client-side join.

    Returns ``[]`` on any read failure — the demand store is JSON or
    Postgres-backed and either can transiently fail. Empty is the
    correct degraded state because a missing-capability with zero
    other requests genuinely produces an empty entry too; the UI
    treats absence and zero the same way.
    """

    if not missing_capabilities:
        return []
    requested = {cap.strip().lower() for cap in missing_capabilities if cap}
    try:
        summaries = load_demand_summary(sample_goals_per_capability=3)
    except Exception as exc:  # noqa: BLE001 — best-effort read
        logger.warning(
            "demand summary load failed: %s: %s",
            type(exc).__name__,
            exc,
        )
        return []

    out: list[dict[str, Any]] = []
    for summary in summaries:
        if summary.capability_id.lower() in requested:
            out.append(summary.to_json())
    return out


# Hard cap on how many capabilities we research per request. Each
# call costs one Brave search + one LLM synthesis, so 3 caps the
# wall time at roughly the same budget as live discovery (which has
# the same per-request cap by design). Capabilities beyond the cap
# are skipped on this request and would fire on a follow-up.
_MAX_RESEARCH_BACKSTOP_CAPABILITIES = 3


def _build_research_backstop_payload(
    *,
    goal: str,
    missing_capabilities: list[str],
    decomposed_sub_tasks: list[dict[str, Any]] | None,
    has_specialist_results: bool,
) -> dict[str, Any]:
    """Run the GeneralResearchAgent for each missing capability.

    Returns a structured block:

    .. code-block:: json

        {
          "status": "applied" | "skipped_specialist_results"
                  | "missing_credentials" | "disabled",
          "capability_id": "general_web_research",
          "results": [
            {
              "capability_id": "...",
              "user_facing_step": "...",
              "result": { ... ResearchResult.to_json ... }
            }
          ],
          "skipped_capabilities": ["..."]  # over the per-request cap
        }

    The wrapper exists because the UI wants both the per-capability
    detail AND a single top-level status it can branch on. The
    research agent itself is unaware of the multi-capability fan-out;
    we orchestrate that here so the agent stays a small, testable
    primitive.
    """

    if has_specialist_results:
        return {
            "status": "skipped_specialist_results",
            "capability_id": GENERAL_RESEARCH_CAPABILITY_ID,
            "results": [],
            "skipped_capabilities": [],
        }

    agent = GeneralResearchAgent()
    if not agent.available():
        # The agent itself returns ``"disabled"`` for env-off and
        # ``"missing_credentials"`` for missing API key. We probe
        # both via a single ``available()`` call here and surface a
        # single "not deployed" status so the UI doesn't have to
        # branch on internals.
        # NOTE: probe one path with the agent so the user gets the
        # specific reason (key vs flag) for engineering debugging,
        # without paying a real network/LLM call.
        sentinel = agent.research(sub_task_description="probe")
        return {
            "status": sentinel.status,
            "capability_id": GENERAL_RESEARCH_CAPABILITY_ID,
            "results": [],
            "skipped_capabilities": [],
            "reason": sentinel.reason,
        }

    sub_task_index = _index_sub_tasks_by_capability(decomposed_sub_tasks)

    capabilities_to_research = list(missing_capabilities)[
        : _MAX_RESEARCH_BACKSTOP_CAPABILITIES
    ]
    skipped = list(missing_capabilities)[_MAX_RESEARCH_BACKSTOP_CAPABILITIES:]

    results: list[dict[str, Any]] = []
    for capability_id in capabilities_to_research:
        sub_task = sub_task_index.get(capability_id) or {}
        # Prefer the decomposer's human-readable step text over the
        # bare slug for the search query — slugs like
        # ``liquor_store_locator`` produce noticeably worse Brave
        # hits than ``find liquor stores in southern India``.
        query = (
            (sub_task.get("user_facing_step") or "").strip()
            or (sub_task.get("description") or "").strip()
            or capability_id
        )
        try:
            result = agent.research(sub_task_description=query, goal=goal)
        except Exception as exc:  # noqa: BLE001 — must never block /goal
            logger.warning(
                "research_backstop unexpected failure for %s: %s: %s",
                capability_id,
                type(exc).__name__,
                exc,
            )
            result = ResearchResult(
                status="unavailable",
                reason=f"{type(exc).__name__}: {exc}",
            )
        results.append(
            {
                "capability_id": capability_id,
                "user_facing_step": query,
                "result": result.to_json(),
            }
        )

    overall_status = (
        "applied"
        if any(entry["result"]["status"] == "applied" for entry in results)
        else "no_results"
    )
    return {
        "status": overall_status,
        "capability_id": GENERAL_RESEARCH_CAPABILITY_ID,
        "results": results,
        "skipped_capabilities": skipped,
    }


def _live_discovery_enabled() -> bool:
    """Live request-time scouts are default-ON. Tests / offline setups
    flip `PLANMYAGENTS_LIVE_DISCOVERY=false`."""
    return (os.getenv("PLANMYAGENTS_LIVE_DISCOVERY", "true") or "").strip().lower() not in {
        "false",
        "0",
        "off",
        "no",
    }


# Hard cap on how many missing capabilities we run scouts for in a single
# /goal request. Each capability costs up to ~10s wall (parallel scouts),
# so we cap at 3 to keep p99 below ~30s. Capabilities beyond the cap are
# left for the next request after the user iterates.
_MAX_LIVE_DISCOVERY_CAPABILITIES = 3


def _live_discovery(
    *,
    goal: str,
    missing_capabilities: list[str],
    existing_provider_ids: set[str],
    search_queries_by_capability: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Dispatch scouts for up to N missing capabilities, persist new
    candidates, and return a UI-ready summary.

    The "freshly_discovered" flag on each candidate is True iff the
    candidate's provider_id was absent from `existing_provider_ids` at
    the time the dispatch started — i.e., this scout fleet is what
    surfaced it for this request.

    ``search_queries_by_capability`` is the per-capability search
    query the goal decomposer wrote for this request (one per
    sub-task). When present, that query is fed into the per-scout
    query expander as the *task text* — replacing the raw goal text.
    The decomposer's queries are capability-shaped (e.g. ``"send
    bulk email API"``) instead of goal-specific (``"email Bengaluru
    car dealers"``), so the scouts maximise recall at the capability
    level and let the downstream judge filter back against the goal.
    Falls back to the raw goal text when no override is provided
    for a given capability.
    """

    if not missing_capabilities:
        return {
            "status": "skipped_no_missing_capabilities",
            "scouts_dispatched": 0,
            "newly_discovered_candidates": [],
            "per_capability": [],
        }

    selected = missing_capabilities[:_MAX_LIVE_DISCOVERY_CAPABILITIES]
    dispatcher = ScoutDispatcher(default_scouts())

    # Try the planner LLM (Qwen → Groq → none). If both unavailable the
    # rule-based fallback inside expand_for_scouts kicks in transparently.
    chat_client = _maybe_chat_client_for_query_expansion()
    overrides = search_queries_by_capability or {}

    # Parallelise per-capability dispatch. Before 2026-05-20 this
    # was a sequential ``for capability in selected:`` loop — total
    # wall-clock was therefore ``sum(per_dispatch_times)``. The
    # underlying ``ScoutDispatcher.dispatch`` already runs scouts in
    # parallel WITHIN a single capability, and is safe to call
    # concurrently across capabilities (it creates a fresh
    # ``ThreadPoolExecutor`` per call and never mutates shared
    # state). With ``_MAX_LIVE_DISCOVERY_CAPABILITIES = 3`` and the
    # global per-dispatch budget capped at 28 s, parallelising the
    # outer loop drops 3 × 18-22 s dispatches from a 54-66 s
    # sequential total down to ~22 s. That win is what brings the
    # /goal request back under the Playwright suite's 240 s budget
    # after the scout fleet grew from 14 → 17 on 2026-05-20.
    #
    # Ordering is preserved by indexing per-capability outputs back
    # into a list of the same shape as ``selected``; the merge of
    # discovered candidates uses the same ``seen_ids`` dedupe gate
    # as the old sequential code, just applied after all dispatches
    # complete instead of during.
    from concurrent.futures import ThreadPoolExecutor

    def _expand_and_dispatch(capability: str) -> tuple[str, Any, Any]:
        scout_task_text = overrides.get(capability, goal)
        expansion = expand_for_scouts(
            capability=capability,
            task_text=scout_task_text,
            chat_client=chat_client,
        )
        result = dispatcher.dispatch(
            capability=capability,
            task_description=scout_task_text,
            per_scout_query_overrides=expansion.per_scout_queries,
        )
        return scout_task_text, expansion, result

    # max_workers=len(selected) is safe because _MAX_LIVE_DISCOVERY_CAPABILITIES
    # caps it at 3 — well below any sensible thread-pool limit.
    per_capability_outputs: list[tuple[str, Any, Any]] = [None] * len(selected)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=max(len(selected), 1)) as outer_pool:
        future_to_index = {
            outer_pool.submit(_expand_and_dispatch, capability): idx
            for idx, capability in enumerate(selected)
        }
        for future in future_to_index:
            idx = future_to_index[future]
            per_capability_outputs[idx] = future.result()

    per_capability_summaries: list[dict[str, Any]] = []
    new_candidates: list[DiscoveryCandidate] = []
    seen_ids: set[str] = set()
    for capability, (scout_task_text, expansion, result) in zip(
        selected, per_capability_outputs
    ):
        for candidate in result.merged_candidates:
            if candidate.id in seen_ids:
                continue
            seen_ids.add(candidate.id)
            new_candidates.append(candidate)

        per_capability_summaries.append(
            {
                "capability": capability,
                # Provenance: did this scout dispatch use the
                # decomposer's LLM-written query, or the raw goal
                # text? Surfaced verbatim so the UI can show "scouts
                # ran with: 'send bulk email API' (decomposer)".
                "scout_task_text": scout_task_text,
                "scout_task_text_source": (
                    "goal_decomposer" if capability in overrides else "raw_goal"
                ),
                "query_expansion": {
                    "used_llm": expansion.used_llm,
                    "fallback_reason": expansion.fallback_reason,
                    "per_scout_queries": expansion.per_scout_queries,
                },
                "dispatch": result.to_summary(),
            }
        )

    # Persist any candidate not already in the index. We call `save_merge`
    # (not plain `.save`) because the JSON / SQLite backends do
    # DELETE-then-INSERT — passing only `new_candidates` would wipe the
    # existing local store. `save_merge` loads, merges by dedupe_key,
    # then saves the union. Postgres was already safe (ON CONFLICT
    # UPDATE) but the merge call works there too.
    persisted_count = 0
    if new_candidates:
        try:
            discovery_store_for_path(_discovery_store_url()).save_merge(
                new_candidates
            )
            persisted_count = len(new_candidates)
        except Exception as exc:  # noqa: BLE001 — persistence failure is non-fatal
            return {
                "status": "ran_persistence_failed",
                "error": f"{type(exc).__name__}: {exc}",
                "scouts_dispatched": len(selected),
                "newly_discovered_candidates": [
                    _serialize_live_candidate(c, freshly_discovered=True)
                    for c in new_candidates
                ],
                "per_capability": per_capability_summaries,
            }

    serialized_candidates = [
        _serialize_live_candidate(
            candidate,
            freshly_discovered=candidate.id not in existing_provider_ids,
        )
        for candidate in new_candidates
    ]
    return {
        "status": "ran",
        "scouts_dispatched": len(selected),
        "candidates_found_total": len(new_candidates),
        "candidates_freshly_discovered": sum(
            1 for c in new_candidates if c.id not in existing_provider_ids
        ),
        "candidates_persisted": persisted_count,
        "newly_discovered_candidates": serialized_candidates,
        "per_capability": per_capability_summaries,
    }


def _serialize_live_candidate(
    candidate: DiscoveryCandidate, *, freshly_discovered: bool
) -> dict[str, Any]:
    """UI-ready shape mirroring (a subset of) the existing search result
    JSON. The `freshly_discovered_at_request` flag lets the frontend
    show a 'just discovered' pill."""
    return {
        "provider_id": candidate.id,
        "display_name": candidate.display_name,
        "vendor": candidate.vendor,
        "vendor_url": candidate.vendor_url,
        "provider_type": candidate.provider_type,
        "source": candidate.source,
        "evidence_url": candidate.evidence_url,
        "capabilities": [
            {"id": c.id, "confidence": c.confidence, "notes": c.notes}
            for c in candidate.capabilities
        ],
        "metadata": dict(candidate.metadata),
        "will_fail": candidate.will_fail,
        "will_fail_reasons": sorted(set(candidate.will_fail_reasons)),
        "benchmark_status": candidate.benchmark_status,
        "route_status": candidate.route_status,
        "freshly_discovered_at_request": freshly_discovered,
    }


def _maybe_chat_client_for_query_expansion() -> Any:
    """Best-effort chat client for the LLM query expander.

    Returns the escalating client (Qwen primary, Groq fallback) so the
    expander gets automatic escalation when the local tier hiccups.
    Returns ``None`` only if construction itself fails — the underlying
    query expander still has a deterministic fallback for that case.
    """
    try:
        from planmyagents_api.llm.escalating_client import build_default_escalating_client

        return build_default_escalating_client()
    except Exception:  # noqa: BLE001
        return None


# Env vars the live research connectors look for, grouped by tier so the UI
# can render "free with token" separately from "paid third-party search".
#
# - FREE_TOKEN keys gate first-party scouts whose token costs $0 (GitHub
#   PAT, no scopes, 5,000 req/hr). They are *the* highest-leverage things
#   for a user to set first because the marginal cost is zero.
#
# - PAID_SEARCH keys gate third-party general web search vendors used as
#   long-tail fallbacks. All have free tiers but the underlying service is
#   a paid SaaS — strategically we want to *outgrow* this tier (see
#   .env "TIER 2" comment).
#
# Aggregating both into a single legacy `LIVE_RESEARCH_KEYS` is preserved
# for any callers that still want a flat list, but new callers should use
# the structured `LIVE_RESEARCH_KEY_TIERS` so the GitHub-as-Tier-2
# misfeature can't sneak back.
LIVE_RESEARCH_KEY_TIERS: dict[str, list[tuple[str, str]]] = {
    "free_with_token": [
        ("GITHUB_TOKEN", "GitHub code search (free PAT, 5000 req/hr)"),
    ],
    "paid_search": [
        ("BRAVE_SEARCH_API_KEY", "Brave web search (free tier exists)"),
        ("TAVILY_API_KEY", "Tavily web search (free tier exists)"),
        ("EXA_API_KEY", "Exa neural search"),
    ],
}

LIVE_RESEARCH_KEYS: list[tuple[str, str]] = [
    *LIVE_RESEARCH_KEY_TIERS["free_with_token"],
    *LIVE_RESEARCH_KEY_TIERS["paid_search"],
]


def _index_freshness() -> dict[str, Any]:
    """Provenance breakdown of the indexed candidate pool.

    Returns total counts, first-seen / last-seen day buckets, and a
    per-source histogram. The UI uses this to answer "where did these
    N candidates come from, and when?" without the user needing to
    run SQL or dig into the JSON.

    Reads from both the agentic discovery_candidates table and the
    apis_without_agents table so the total matches what the user
    sees on `/categories` and `/open-mcp-opportunities` summed.
    Failures are swallowed — freshness is informational, never blocks
    /goal.
    """

    from collections import Counter

    try:
        store = discovery_store_for_path(_discovery_store_url())
        agentic = store.load()
        api_records = store.load_apis_without_agents()
    except Exception as exc:  # noqa: BLE001 — freshness must never block /goal
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    by_source: Counter[str] = Counter()
    first_seen_days: Counter[str] = Counter()
    last_seen_days: Counter[str] = Counter()

    for c in agentic:
        by_source[c.source or "unknown"] += 1
        if c.first_seen_at:
            first_seen_days[c.first_seen_at[:10]] += 1
        if c.last_seen_at:
            last_seen_days[c.last_seen_at[:10]] += 1
    for r in api_records:
        by_source[r.source_id or "unknown"] += 1
        if getattr(r, "first_seen_at", ""):
            first_seen_days[r.first_seen_at[:10]] += 1
        if getattr(r, "last_seen_at", ""):
            last_seen_days[r.last_seen_at[:10]] += 1

    sources = [
        {"source": src, "count": n}
        for src, n in sorted(by_source.items(), key=lambda kv: -kv[1])
    ]
    first_seen = [
        {"day": day, "count": n} for day, n in sorted(first_seen_days.items())
    ]
    last_seen = [
        {"day": day, "count": n} for day, n in sorted(last_seen_days.items())
    ]
    latest_refresh = max(last_seen_days) if last_seen_days else None

    return {
        "ok": True,
        "total_agentic": len(agentic),
        "total_apis_without_agents": len(api_records),
        "total": len(agentic) + len(api_records),
        "latest_refresh_day": latest_refresh,
        "sources": sources,
        "first_seen_days": first_seen,
        "last_seen_days": last_seen,
    }


def _live_research_status() -> dict[str, Any]:
    """Tell the UI exactly what was (and was not) searched on this request.

    Today the `/goal` route never triggers live web/GitHub/MCP-registry/A2A
    research — it only queries the local discovery store. This helper makes
    that fact a first-class, machine-readable field on the response so the
    Goal page can render an honest banner instead of burying the truth in
    `gap_report.capability_gaps[*].blockers[1]`.
    """

    configured = [
        {"env_var": env_var, "label": label}
        for env_var, label in LIVE_RESEARCH_KEYS
        if os.getenv(env_var)
    ]
    missing = [
        {"env_var": env_var, "label": label}
        for env_var, label in LIVE_RESEARCH_KEYS
        if not os.getenv(env_var)
    ]

    # Same lists, but split by tier so the UI can render "free with token"
    # separately from "paid third-party search" — GitHub PAT is free and
    # belongs in the first bucket, not lumped with Brave/Tavily/Exa.
    def _tier_buckets(env_vars: list[tuple[str, str]]) -> dict[str, list[dict[str, str]]]:
        return {
            "configured": [
                {"env_var": ev, "label": label}
                for ev, label in env_vars
                if os.getenv(ev)
            ],
            "missing": [
                {"env_var": ev, "label": label}
                for ev, label in env_vars
                if not os.getenv(ev)
            ],
        }

    tiers = {
        tier_name: _tier_buckets(env_vars)
        for tier_name, env_vars in LIVE_RESEARCH_KEY_TIERS.items()
    }

    if configured:
        status = "not_run_per_request_disabled"
        message = (
            "Live web/GitHub research keys are configured, but the /goal route "
            "does not trigger live search per request. To refresh the index "
            "with new live findings, run `make discovery-refresh` (or "
            "`scripts/run_discovery_research.py` directly)."
        )
    else:
        status = "not_run_no_keys"
        message = (
            "No live web/GitHub research keys are configured, so only the "
            "local discovery store was queried for this request. Configure "
            "at least one of the env vars below to enable live research, "
            "then run `make discovery-refresh` to grow the index."
        )

    return {
        "status": status,
        "ran": False,
        "queried_sources": ["local_discovery_store"],
        "skipped_sources": [
            "brave_web_search",
            "tavily_web_search",
            "github_code_search",
            "mcp_registry_search",
            "a2a_card_search",
            "openapi_spec_search",
            "vendor_doc_search",
            "agent_marketplace_search",
        ],
        "configured_keys": configured,
        "missing_keys": missing,
        "tiers": tiers,
        "message": message,
        "next_step_command": "make discovery-refresh",
    }


def _answer_from_execution(execution: dict[str, Any]) -> str | None:
    records = execution.get("records") or []
    if not records:
        sub_results = execution.get("sub_task_results") or []
        if not sub_results:
            return None
        succeeded = sum(1 for item in sub_results if item.get("succeeded"))
        return (
            f"Executed {succeeded}/{len(sub_results)} sub-tasks "
            f"with average confidence "
            f"{float(execution.get('average_confidence', 0.0)):.2f}."
        )
    summary_parts: list[str] = []
    for record in records[:5]:
        capability = record.get("capability") or "result"
        data = record.get("data")
        if isinstance(data, dict):
            highlight = data.get("status") or data.get("result") or data.get("answer")
            if highlight:
                summary_parts.append(f"{capability}: {highlight}")
                continue
        summary_parts.append(capability)
    return "; ".join(summary_parts) if summary_parts else None


def _summary_text(execution: dict[str, Any]) -> str:
    sub_results = execution.get("sub_task_results") or []
    succeeded = sum(1 for item in sub_results if item.get("succeeded"))
    total = len(sub_results)
    confidence = float(execution.get("average_confidence", 0.0))
    cost = float(execution.get("total_cost_usd", 0.0))
    return (
        f"Executed {succeeded}/{total} sub-tasks. "
        f"Average confidence {confidence:.2f}. Total cost ${cost:.4f}."
    )


def _attach_recipe_export_metadata(
    plan_payload: dict[str, Any], *, goal_text: str
) -> None:
    """Compute goal_id, persist plan to the goal cache, and attach
    download URLs for every supported recipe format to plan_payload.

    Side-effects only. The plan_payload dict is mutated in place so
    the caller can rely on its existing reference being enriched.
    Failures here are non-fatal — recipe export is a convenience
    surface, not a correctness gate; we log the failure and return
    so the /goal response still ships.
    """
    try:
        goal_id = compute_goal_id(goal_text, plan_payload)
        context = build_recipe_context(
            goal_id=goal_id,
            goal_text=goal_text,
            plan_payload=plan_payload,
        )
        plan_payload["goal_id"] = goal_id
        plan_payload["recipe_coverage"] = context.coverage.to_dict()
        _goal_cache().save(
            goal_id=goal_id, goal_text=goal_text, plan_payload=plan_payload
        )
        plan_payload["recipes"] = [
            {
                "format": fmt,
                "label": label,
                "coverage": context.coverage.to_dict(),
                "download_url": (
                    f"/recipe/export?goal_id={goal_id}&workflow_option=0&format={fmt}"
                ),
            }
            for fmt, label in RECIPE_FORMATS.items()
        ]
    except Exception as exc:  # noqa: BLE001 — never break /goal because the cache slipped
        logger.warning(
            "/goal recipe export metadata not attached: %s",
            exc,
            exc_info=True,
        )


def _candidate_supports(candidate: DiscoveryCandidate, capability_id: str) -> bool:
    return any(cap.id == capability_id for cap in candidate.capabilities)


def _leaderboard_entries(
    *,
    candidates: list[DiscoveryCandidate],
    rankings: list[dict[str, Any]],
) -> list[LeaderboardEntry]:
    """Join discovery candidates with benchmark rankings for a single capability.

    Every candidate that *declares* the capability gets a row, even if it has
    never been benchmarked. That keeps the leaderboard honest: untested
    providers show up with sample_size=0 and a benchmark_status that signals
    "we have not measured this yet" rather than being silently dropped.
    """

    rankings_by_provider = {
        str(row.get("provider_id") or ""): row for row in rankings
    }

    entries: list[LeaderboardEntry] = []
    for candidate in candidates:
        ranking = rankings_by_provider.get(candidate.id, {})
        entries.append(
            LeaderboardEntry(
                rank=0,
                provider_id=candidate.id,
                display_name=candidate.display_name,
                provider_type=candidate.provider_type,
                composite_score=float(ranking.get("composite_score") or 0.0),
                success_rate=float(ranking.get("success_rate") or 0.0),
                avg_quality_score=float(ranking.get("avg_quality_score") or 0.0),
                p50_latency_ms=int(ranking.get("p50_latency_ms") or 0),
                p95_latency_ms=int(ranking.get("p95_latency_ms") or 0),
                avg_cost_usd=float(ranking.get("avg_cost_usd") or 0.0),
                sample_size=int(ranking.get("sample_size") or 0),
                benchmark_status=str(
                    ranking.get("benchmark_status")
                    or candidate.benchmark_status
                    or "not_started"
                ),
                source=str(ranking.get("source") or "synthetic"),
                last_run_at=str(ranking.get("last_run_at") or ""),
                verification_status=candidate.verification_status,
                routable_today=is_promotion_ready(candidate),
                will_fail_reasons=sorted(set(candidate.will_fail_reasons)),
                evidence_url=candidate.evidence_url,
            )
        )

    entries.sort(
        key=lambda entry: (
            0 if entry.sample_size > 0 else 1,
            -entry.composite_score,
            -entry.success_rate,
            entry.p95_latency_ms,
            entry.avg_cost_usd,
            entry.provider_id,
        )
    )
    for index, entry in enumerate(entries, start=1):
        entry.rank = index
    return entries


def _credibility_model(verdict) -> CredibilityModel:
    """Adapt a CredibilityVerdict to its Pydantic boundary model.

    The verdict carries the capability id; the boundary model does not because
    callers already key entries by capability.
    """

    payload = verdict.to_json()
    payload.pop("capability", None)
    return CredibilityModel(**payload)


def _leaderboard_index_entries(
    *,
    candidates: list[DiscoveryCandidate],
    rankings: list[dict[str, Any]],
) -> list[LeaderboardIndexEntry]:
    capability_to_candidates: dict[str, list[DiscoveryCandidate]] = {}
    for candidate in candidates:
        for capability in candidate.capabilities:
            capability_to_candidates.setdefault(capability.id, []).append(candidate)

    rankings_by_capability: dict[str, list[dict[str, Any]]] = {}
    for row in rankings:
        capability = str(row.get("capability") or "")
        if capability:
            rankings_by_capability.setdefault(capability, []).append(row)

    verdicts = classify_index(
        candidates_by_capability=capability_to_candidates,
        rankings=rankings,
    )

    # Iterate the UNION of capabilities surfaced by either source. If we
    # only iterate `capability_to_candidates` we silently drop any
    # capability that has succeeded benchmark runs but no row in
    # discovery_candidates (the symptom that hid Razorpay's
    # payment_authorization from the leaderboard on 2026-05-19 even
    # though `benchmark_runs` had 5 succeeded real-adapter rows for it).
    # That asymmetry is exactly the kind of "deck claims X, data shows
    # less than X" gap a vendor would flag on first review.
    all_capability_ids = sorted(
        capability_to_candidates.keys() | rankings_by_capability.keys()
    )

    entries: list[LeaderboardIndexEntry] = []
    for capability_id in all_capability_ids:
        candidates_for_cap = capability_to_candidates.get(capability_id, [])
        cluster_id, cluster_display = category_for_capability(capability_id)
        capability_rankings = rankings_by_capability.get(capability_id, [])
        bench_passed = sum(
            1
            for row in capability_rankings
            if str(row.get("benchmark_status") or "") == "passed"
        )
        routable_today = sum(
            1 for candidate in candidates_for_cap if is_promotion_ready(candidate)
        )
        has_real_runs = any(
            str(row.get("source") or "") not in {"", "synthetic"}
            for row in capability_rankings
        )
        # Provider count: distinct provider_ids across both surfaces.
        # Pure-ranking entries (tested-but-not-indexed) still get a
        # non-zero provider_count this way, which is what the operator
        # needs to see to know "we have evidence, the index hasn't
        # caught up yet" (vs. "no providers at all").
        provider_ids_from_candidates = {c.id for c in candidates_for_cap}
        provider_ids_from_rankings = {
            str(row.get("provider_id") or "")
            for row in capability_rankings
            if row.get("provider_id")
        }
        distinct_provider_count = len(
            provider_ids_from_candidates | provider_ids_from_rankings
        )
        verdict = verdicts.get(capability_id)
        if verdict is None and capability_rankings:
            # `classify_index` only verdicts capabilities that have at
            # least one declared candidate. For capabilities surfaced
            # purely via rankings (tested-but-not-indexed providers),
            # call the per-capability classifier directly so the
            # credibility pill still reflects the real-run signal
            # instead of falling back to "synthetic_only".
            from planmyagents_api.benchmark.credibility import classify as _classify

            verdict = _classify(
                capability=capability_id,
                candidates=[],
                rankings=capability_rankings,
            )
        credibility = (
            _credibility_model(verdict)
            if verdict is not None
            else CredibilityModel(
                status="synthetic_only",
                real_provider_count=0,
                total_provider_count=distinct_provider_count,
                max_real_sample_size=0,
                last_real_run_at="",
                reasons=["No verdict computed."],
                unblockers=[],
            )
        )
        entries.append(
            LeaderboardIndexEntry(
                capability=capability_id,
                cluster_id=cluster_id,
                cluster_display_name=cluster_display,
                provider_count=distinct_provider_count,
                benchmark_passed=bench_passed,
                routable_today=routable_today,
                has_real_adapter_runs=has_real_runs,
                credibility=credibility,
            )
        )
    # Publishable / developing first so the index visibly rewards credibility,
    # not just routability count.
    status_rank = {
        "publishable": 0,
        "developing": 1,
        "smoke_test": 2,
        "synthetic_only": 3,
    }
    entries.sort(
        key=lambda entry: (
            status_rank.get(entry.credibility.status, 9),
            -entry.routable_today,
            -entry.benchmark_passed,
            -entry.provider_count,
            entry.capability,
        )
    )
    return entries


app = create_app()
