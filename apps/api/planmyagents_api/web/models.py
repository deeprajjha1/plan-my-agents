"""Pydantic models for the FastAPI boundary.

Kept intentionally permissive (most fields are str/dict to mirror the existing
JSON payloads) so we do not duplicate the rich domain dataclasses. The point of
these models is to *document* the API shape and reject malformed inputs, not to
re-encode the entire domain.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Saved recipes (T1-B-3)
# ---------------------------------------------------------------------------


class SaveRecipeRequest(BaseModel):
    """POST /recipes payload.

    The client only needs to pass the goal_id of a plan they have
    already received from /goal — the server fetches the cached plan
    payload, validates the format, and persists. We intentionally do
    NOT accept the plan payload directly: that path would let a
    tampered client persist a recipe that points at agents we never
    recommended.
    """

    goal_id: str = Field(min_length=1, max_length=64)
    format: str = Field(
        min_length=1,
        description="One of the keys returned by GET /recipe/formats.",
    )
    notes: str | None = Field(default=None, max_length=2000)


class SavedRecipeResponse(BaseModel):
    recipe_id: str
    workspace_id: str
    user_id: str
    goal: str
    format: str
    notes: str | None = None
    created_at: str
    updated_at: str
    recipe_coverage: dict[str, object] | None = None
    download_url: str = Field(
        description="Pre-built /recipe/export URL for one-click download.",
    )


class SavedRecipesListResponse(BaseModel):
    total: int
    recipes: list[SavedRecipeResponse]


class CategoryTotals(BaseModel):
    candidates: int
    known_listed: int
    benchmark_passed: int
    routable_today: int


class CategorySummaryResponse(BaseModel):
    cluster_id: str
    display_name: str
    capability_ids: list[str]
    totals: CategoryTotals
    provider_type_breakdown: dict[str, int]
    top_candidate_ids: list[str]


class CategoriesResponse(BaseModel):
    store: str
    backend: str
    embedder: str | None = None
    total_candidates: int
    categories: list[CategorySummaryResponse]


class CandidateCardModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider_id: str
    display_name: str
    provider_type: str
    capabilities: list[str]
    verification_status: str
    benchmark_status: str
    will_fail: bool
    will_fail_reasons: list[str] = Field(default_factory=list)
    required_env_vars: list[str] = Field(default_factory=list)
    promotion_readiness: dict[str, Any] = Field(default_factory=dict)


class CategoryDetailResponse(BaseModel):
    cluster_id: str
    display_name: str
    capability_ids: list[str]
    totals: CategoryTotals
    candidates: list[CandidateCardModel]


class AgentDetailResponse(BaseModel):
    candidate: dict[str, Any]
    promotion_readiness: dict[str, Any]
    rankings: list[dict[str, Any]]
    verification_history: list[dict[str, Any]]
    recent_runs: list[dict[str, Any]]


class BenchmarkRunsResponse(BaseModel):
    provider_id: str | None = None
    capability: str | None = None
    runs: list[dict[str, Any]]


class GoalRequest(BaseModel):
    goal: str = Field(min_length=1)
    execute: bool = False


class GoalResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    plan: dict[str, Any]
    executed: bool
    answer: str | None = None
    summary: str | None = None
    execution: dict[str, Any] | None = None


class GoalExplainResponse(BaseModel):
    """Reasoning-trace response for the on-demand "Why did the
    planner pick this?" UI flow. Returns the model's chain-of-thought
    next to the JSON answer it produced, so the user can see *why*
    the planner landed on a particular plan without paying the +60-
    180s thinking-mode latency on every ``/goal`` call.

    ``model_supports_thinking`` is False for non-thinking models
    (qwen2.5, llama3.x). In that case ``thinking`` will be empty and
    the UI should surface that fact rather than showing a misleading
    empty panel."""

    model_config = ConfigDict(extra="allow")

    ok: bool
    goal: str
    model: str
    thinking: str
    content: str
    plan: dict[str, Any] | None
    duration_ms: int
    model_supports_thinking: bool


class HealthResponse(BaseModel):
    status: str
    discovery_store: str
    benchmark_store: str
    verification_store: str
    embedding_model: str


class RecentVerificationRecord(BaseModel):
    """One row of the ``verification_records`` table summarised for the UI.

    Used by ``/evidence/recent-verifications`` (sprint-pitch-align P4-3)
    to back the "Recent verifications" section on ``/discovery-gaps``
    and ``/open-mcp-opportunities`` so a visitor can confirm we keep
    re-verifying, not just collected a one-time snapshot.
    """

    provider_id: str
    status: str
    evidence_url: str
    verified_capabilities: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    created_at: str


class RecentVerificationsResponse(BaseModel):
    """Wrapper for the recent verifications list + check timestamp."""

    records: list[RecentVerificationRecord] = Field(default_factory=list)
    checked_at: str


class EvidenceHealthResponse(BaseModel):
    """Live counts of the five evidence tables that back the deck.

    Used by the homepage Live-Evidence strip and the CI guard so a DD
    reviewer can confirm in ~30 seconds that the evidence pipeline is
    actually producing rows, not just designed to.

    Fields:
    * ``benchmark_runs_24h`` — number of rows in ``benchmark_runs``
      within the last 24h (deck claim "first live cell shipped").
    * ``verification_records_7d`` — number of rows in
      ``verification_records`` within the last 7 days (deck claim
      "5-tier evidence ladder").
    * ``discovery_run_events_24h`` — number of rows in
      ``discovery_run_events`` within the last 24h (deck claim "14
      scouts").
    * ``capability_demand_events_24h`` — number of rows in
      ``capability_demand_events`` within the last 24h (refusal →
      demand signal moat).
    * ``discovery_gap_events_24h`` — number of rows in
      ``discovery_gap_events`` within the last 24h (the "Open MCP
      Opportunities" data source).
    * ``route_status_routable_count`` — number of (provider, capability)
      pairs with at least one successful ``benchmark_runs`` row in the
      last 30 days. This is what the deck means by "routes through
      tested and runnable providers when one exists" — a capability
      we'd actually route /goal at today because we benchmarked the
      adapter passing.
    * ``benchmark_runs_total`` / ``verification_records_total`` —
      cumulative counts the homepage can show alongside the windowed
      ones to convey "this is alive" vs "this is mature".
    * ``checked_at`` — ISO8601 timestamp of when these counts were
      computed (helps the UI distinguish stale renders).
    * ``db_reachable`` — true when the underlying COUNT queries
      actually executed against Postgres. False when the DSN was a
      non-Postgres path (dev shells) OR the connection / query
      raised. **Without this flag, the API returned all-zeros
      indistinguishable from "DB up but empty", which on
      2026-05-19 cost ~15 minutes of operator time diagnosing
      "did the DB get cleaned up?" when Docker had just stopped.**
      Surface this on every consumer that renders counts so the
      "system alive but empty" vs "system down" failure modes are
      visibly distinct.
    * ``db_error`` — populated with the connection / query failure
      message when ``db_reachable`` is false. ``None`` otherwise.
      Truncated to a short string suitable for inline UI display;
      the full stack lives in the API server logs.
    """

    benchmark_runs_24h: int
    verification_records_7d: int
    discovery_run_events_24h: int
    capability_demand_events_24h: int
    discovery_gap_events_24h: int
    route_status_routable_count: int
    benchmark_runs_total: int
    verification_records_total: int
    checked_at: str
    # Defaults preserve API-shape backwards compatibility for any
    # consumer that omits these fields when *building* the model
    # (e.g. older test fixtures). The API always emits them.
    db_reachable: bool = True
    db_error: str | None = None


class DemandTopCapabilityEntry(BaseModel):
    """One row of the public ``GET /demand/top-capabilities`` response.

    The contract is intentionally flat (no nested capability arrays) and
    stable: vendor outreach emails and dashboard adapters can hyperlink
    individual entries by ``capability_id``. New columns are appended to
    this model over time, never inserted in the middle.
    """

    capability_id: str
    request_count: int
    distinct_requester_count: int
    last_requested_at: str
    routable_today: bool
    routable_provider_ids: list[str] = Field(default_factory=list)
    sample_goals: list[str] = Field(default_factory=list)


class DemandTopCapabilitiesResponse(BaseModel):
    """Wrapper for the demand-top-capabilities feed."""

    window_days: int
    total: int
    generated_at: str
    entries: list[DemandTopCapabilityEntry] = Field(default_factory=list)


class DemandGapEntry(BaseModel):
    """One row of ``GET /demand/gaps`` — capabilities with demand AND
    a routable supply gap. A capability is in this feed when:

    * ``request_count > 0`` in the lookback window, AND
    * ``routable_today=false`` (no succeeded benchmark_runs row in 30d),
      OR ``zero_yield_count > 0`` (we tried to discover and surfaced
      nothing the judge would accept).
    """

    capability_id: str
    request_count: int
    discovery_attempts: int
    zero_yield_count: int
    sample_goals: list[str] = Field(default_factory=list)


class DemandGapsResponse(BaseModel):
    """Wrapper for the demand-gaps feed."""

    window_days: int
    total: int
    generated_at: str
    entries: list[DemandGapEntry] = Field(default_factory=list)


class CredibilityModel(BaseModel):
    """Per-capability classification of how trustworthy the leaderboard is.

    See ``planmyagents_api.benchmark.credibility`` for the underlying rules. The UI
    uses ``status`` to show a colour pill and ``reasons`` / ``unblockers`` to
    explain to a buyer (or to ourselves) what is missing before this page
    becomes publishable.
    """

    status: str
    real_provider_count: int
    total_provider_count: int
    max_real_sample_size: int
    last_real_run_at: str
    reasons: list[str] = Field(default_factory=list)
    unblockers: list[str] = Field(default_factory=list)


class LeaderboardEntry(BaseModel):
    """One ranked provider on a per-capability leaderboard."""

    rank: int
    provider_id: str
    display_name: str
    provider_type: str
    composite_score: float
    success_rate: float
    avg_quality_score: float
    p50_latency_ms: int
    p95_latency_ms: int
    avg_cost_usd: float
    sample_size: int
    benchmark_status: str
    source: str
    last_run_at: str
    verification_status: str
    routable_today: bool
    will_fail_reasons: list[str] = Field(default_factory=list)
    evidence_url: str = ""


class LeaderboardResponse(BaseModel):
    capability: str
    cluster_id: str
    cluster_display_name: str
    total_providers: int
    benchmark_passed: int
    routable_today: int
    entries: list[LeaderboardEntry]
    credibility: CredibilityModel


class LeaderboardIndexEntry(BaseModel):
    capability: str
    cluster_id: str
    cluster_display_name: str
    provider_count: int
    benchmark_passed: int
    routable_today: int
    has_real_adapter_runs: bool
    credibility: CredibilityModel


class LeaderboardIndexResponse(BaseModel):
    capabilities: list[LeaderboardIndexEntry]
