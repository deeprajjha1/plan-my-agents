/**
 * Typed client for the PlanMyAgents FastAPI backend.
 *
 * All fetches go through Next.js's `fetch` with `cache: "no-store"` so the UI
 * always reflects the live state of the discovery + benchmark stores during
 * dev. We can switch to ISR per-route once the page set stabilises.
 */

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

export const apiBaseUrl = (): string => {
  return (
    process.env.PLANMYAGENTS_API_BASE_URL ??
    process.env.NEXT_PUBLIC_PLANMYAGENTS_API_BASE_URL ??
    DEFAULT_API_BASE_URL
  );
};

export type CategoryTotals = {
  candidates: number;
  known_listed: number;
  benchmark_passed: number;
  routable_today: number;
};

export type CategorySummary = {
  cluster_id: string;
  display_name: string;
  capability_ids: string[];
  totals: CategoryTotals;
  provider_type_breakdown: Record<string, number>;
  top_candidate_ids: string[];
};

export type CategoriesResponse = {
  store: string;
  backend: string;
  embedder: string | null;
  total_candidates: number;
  categories: CategorySummary[];
};

export type CandidateCard = {
  provider_id: string;
  display_name: string;
  provider_type: string;
  capabilities: string[];
  verification_status: string;
  benchmark_status: string;
  will_fail: boolean;
  will_fail_reasons: string[];
  required_env_vars: string[];
  promotion_readiness: {
    ready_for_promotion?: boolean;
    blockers?: string[];
    required_steps?: string[];
  };
  evidence_url?: string;
  freshness?: { status: string; days_since_last_seen: number };
  docs_available?: boolean;
  auth_method?: string;
  tool_count?: number;
  skill_count?: number;
  [key: string]: unknown;
};

export type CandidateUsageExample = {
  title: string;
  language: string;
  snippet: string;
};

export type CandidateDocs = {
  setup_url: string;
  auth_method: string;
  auth_scopes: string[];
  install_steps: string[];
  usage_examples: CandidateUsageExample[];
  pricing_url: string;
  status_page_url: string;
  rate_limit_requests_per_minute: number;
  rate_limit_monthly_quota: number;
};

export type CandidateTool = {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  examples: string[];
};

export type CategoryDetail = {
  cluster_id: string;
  display_name: string;
  capability_ids: string[];
  totals: CategoryTotals;
  candidates: CandidateCard[];
};

export type Ranking = {
  provider_id: string;
  capability: string;
  sample_size: number;
  success_rate: number;
  avg_quality_score: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  avg_cost_usd: number;
  composite_score: number;
  benchmark_status: string;
  source: string;
  rank: number;
  last_run_at: string;
};

export type VerificationRecord = {
  provider_id: string;
  status: string;
  evidence_url: string;
  verified_capabilities: string[];
  blockers: string[];
  notes: string[];
  created_at: string;
};

export type AgentDetail = {
  candidate: Record<string, unknown>;
  promotion_readiness: {
    ready_for_promotion?: boolean;
    blockers?: string[];
    required_steps?: string[];
  };
  rankings: Ranking[];
  verification_history: VerificationRecord[];
  recent_runs: Array<Record<string, unknown>>;
};

export type SearchResponse = {
  query: string;
  store: string;
  backend: string;
  embedder: string;
  capability: string | null;
  provider_type: string | null;
  results: CandidateCard[];
};

export type ApiError = {
  status: number;
  detail: string;
  // Structured detail when the backend returned an object (e.g. 503
  // PlanningUnavailableError carries `{error, message, llm_quality,
  // remediation}` so the UI can render a remediation list instead of a
  // generic toast). `null` for plain string-detail errors.
  detailObject?: Record<string, unknown> | null;
};

const get = async <T>(path: string): Promise<T> => {
  const response = await fetch(`${apiBaseUrl()}${path}`, { cache: "no-store" });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body === "object" && body && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch {
      // swallow JSON parse errors
    }
    const error: ApiError = { status: response.status, detail };
    throw error;
  }
  return (await response.json()) as T;
};

export const fetchCategories = (): Promise<CategoriesResponse> =>
  get("/discovery/categories");

/**
 * Live counts of the five evidence tables (sprint-pitch-align Phase 4).
 *
 * Backs the homepage Live-Evidence strip and the deck's "evidence-first"
 * claim. Server-side caches for 60s by default so even render-on-every-
 * request strips don't pressure Postgres.
 */
export type EvidenceHealth = {
  benchmark_runs_24h: number;
  verification_records_7d: number;
  discovery_run_events_24h: number;
  capability_demand_events_24h: number;
  discovery_gap_events_24h: number;
  route_status_routable_count: number;
  benchmark_runs_total: number;
  verification_records_total: number;
  checked_at: string;
  // Added 2026-05-19. Optional in the type for backwards compat with
  // any cached response shape pinned before the field landed; the live
  // API always returns them. `db_reachable: false` means the counts
  // above are not authoritative — the underlying query failed. Display
  // a distinct "DB unreachable" state in that case so the user can
  // distinguish "empty DB" from "dead DB" (cf. /health/evidence
  // docstring; the 2026-05-19 audit motivated this split).
  db_reachable?: boolean;
  db_error?: string | null;
};

export const fetchEvidenceHealth = (): Promise<EvidenceHealth> =>
  get("/health/evidence");

/**
 * Latest N rows of verification_records summarised for the UI.
 * Backs the "Recent verifications" sections on /discovery-gaps and
 * /open-mcp-opportunities.
 */
export type RecentVerificationRecord = {
  provider_id: string;
  status: string;
  evidence_url: string;
  verified_capabilities: string[];
  blockers: string[];
  created_at: string;
};

export type RecentVerificationsResponse = {
  records: RecentVerificationRecord[];
  checked_at: string;
};

export const fetchRecentVerifications = (
  limit = 5,
): Promise<RecentVerificationsResponse> =>
  get(`/evidence/recent-verifications?limit=${limit}`);

export const fetchCategory = (clusterId: string): Promise<CategoryDetail> =>
  get(`/discovery/categories/${encodeURIComponent(clusterId)}`);

export const fetchAgent = (providerId: string): Promise<AgentDetail> =>
  get(`/discovery/agents/${encodeURIComponent(providerId)}`);

export type LeaderboardEntry = {
  rank: number;
  provider_id: string;
  display_name: string;
  provider_type: string;
  composite_score: number;
  success_rate: number;
  avg_quality_score: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  avg_cost_usd: number;
  sample_size: number;
  benchmark_status: string;
  source: string;
  last_run_at: string;
  verification_status: string;
  routable_today: boolean;
  will_fail_reasons: string[];
  evidence_url: string;
};

export type CredibilityStatus =
  | "publishable"
  | "developing"
  | "smoke_test"
  | "synthetic_only";

export type Credibility = {
  status: CredibilityStatus;
  real_provider_count: number;
  total_provider_count: number;
  max_real_sample_size: number;
  last_real_run_at: string;
  reasons: string[];
  unblockers: string[];
};

export type LeaderboardResponse = {
  capability: string;
  cluster_id: string;
  cluster_display_name: string;
  total_providers: number;
  benchmark_passed: number;
  routable_today: number;
  entries: LeaderboardEntry[];
  credibility: Credibility;
};

export type LeaderboardIndexEntry = {
  capability: string;
  cluster_id: string;
  cluster_display_name: string;
  provider_count: number;
  benchmark_passed: number;
  routable_today: number;
  has_real_adapter_runs: boolean;
  credibility: Credibility;
};

export type LeaderboardIndexResponse = {
  capabilities: LeaderboardIndexEntry[];
};

export const fetchLeaderboards = (): Promise<LeaderboardIndexResponse> =>
  get("/leaderboards");

export const fetchLeaderboard = (capability: string): Promise<LeaderboardResponse> =>
  get(`/leaderboards/${encodeURIComponent(capability)}`);

// ===========================================================================
// Eval methodology (/eval/methodology) — backs the public /trust page.
//
// Read-only projection of the Eval_Framework: the cheapest-first tier
// ladder, the per-protocol invocation maturity, the ranking-source
// taxonomy, and the LIVE per-capability credibility histogram. Every
// value is derived from backend code that already governs behaviour, so
// the page can never claim a tier/protocol/source the framework does not
// implement.
// ===========================================================================

export type EvalTierInfo = {
  id: string;
  rank: number;
  label: string;
  description: string;
  credibility_band: CredibilityStatus | string;
};

export type EvalProtocolInfo = {
  protocol: string;
  maturity: "executable" | "refusal_only" | "planned" | string;
  can_invoke: boolean;
  note: string;
};

export type EvalSourceInfo = {
  source: string;
  is_real: boolean;
  description: string;
};

export type CredibilityDistributionEntry = {
  status: CredibilityStatus | string;
  count: number;
};

export type EvalMethodologyResponse = {
  tiers: EvalTierInfo[];
  protocols: EvalProtocolInfo[];
  sources: EvalSourceInfo[];
  scoring_methods: string[];
  real_eval_sources: string[];
  credibility_distribution: CredibilityDistributionEntry[];
  total_capabilities: number;
  real_run_capabilities: number;
  spec_reference: string;
};

export const fetchEvalMethodology = (): Promise<EvalMethodologyResponse> =>
  get("/eval/methodology");

export type ApiWithoutAgent = {
  provider_id: string;
  display_name: string;
  vendor: string;
  openapi_url: string;
  capabilities: string[];
  source: string;
};

export type OpenMcpOpportunityCapability = {
  capability_id: string;
  api_supply_count: number;
  demand_request_count: number;
  distinct_requester_count: number;
  score: number;
  sample_demand_goals: string[];
  apis: ApiWithoutAgent[];
};

export type OpenMcpOpportunitiesResponse = {
  total_opportunities: number;
  total_apis_without_agents: number;
  methodology: string;
  capabilities: OpenMcpOpportunityCapability[];
  filter?: { capability: string };
};

export const fetchOpenMcpOpportunities = (): Promise<OpenMcpOpportunitiesResponse> =>
  get("/open-mcp-opportunities?limit=200");

export type DiscoveryGapCapability = {
  capability_id: string;
  observation_count: number;
  distinct_goal_count: number;
  zero_yield_count: number;
  last_observed_at: string;
  sample_goals: string[];
};

export type DiscoveryGapsResponse = {
  status: string;
  total_capabilities: number;
  filter: { capability: string } | null;
  capabilities: DiscoveryGapCapability[];
};

export const fetchDiscoveryGaps = (
  limit: number = 6,
): Promise<DiscoveryGapsResponse> =>
  get(`/discovery-gaps?limit=${limit}`);

/**
 * Sprint-6 / C — public demand-signal APIs.
 *
 * Flat, stable contract for vendor-facing / partner-portal consumption.
 * Both endpoints are rate-limited per-IP (default 60 RPM) — the public
 * /demand page calls them once per render which is well inside the
 * budget.
 */
export type DemandTopCapabilityEntry = {
  capability_id: string;
  request_count: number;
  distinct_requester_count: number;
  last_requested_at: string;
  routable_today: boolean;
  routable_provider_ids: string[];
  sample_goals: string[];
};

export type DemandTopCapabilitiesResponse = {
  window_days: number;
  total: number;
  generated_at: string;
  entries: DemandTopCapabilityEntry[];
};

export const fetchDemandTopCapabilities = (
  limit: number = 25,
  windowDays: number = 30,
): Promise<DemandTopCapabilitiesResponse> =>
  get(`/demand/top-capabilities?limit=${limit}&window_days=${windowDays}`);

export type DemandGapEntry = {
  capability_id: string;
  request_count: number;
  discovery_attempts: number;
  zero_yield_count: number;
  sample_goals: string[];
};

export type DemandGapsResponse = {
  window_days: number;
  total: number;
  generated_at: string;
  entries: DemandGapEntry[];
};

export const fetchDemandGaps = (
  limit: number = 25,
  windowDays: number = 30,
  minZeroYield: number = 0,
): Promise<DemandGapsResponse> =>
  get(
    `/demand/gaps?limit=${limit}&window_days=${windowDays}&min_zero_yield=${minZeroYield}`,
  );

export const search = (params: {
  q: string;
  capability?: string;
  provider_type?: string;
  limit?: number;
}): Promise<SearchResponse> => {
  const query = new URLSearchParams({ q: params.q });
  if (params.capability) query.set("capability", params.capability);
  if (params.provider_type) query.set("provider_type", params.provider_type);
  if (params.limit) query.set("limit", String(params.limit));
  return get(`/discovery/search?${query.toString()}`);
};

export type GoalRequest = {
  goal: string;
  execute?: boolean;
};

export type GoalSubTask = {
  capability: string;
  description?: string;
  inputs?: Record<string, unknown>;
};

/**
 * One sub-task emitted by the LLM goal decomposer.
 *
 * Replaces the old slug-only "missing_capabilities" view as the
 * primary thing the UI shows the user. Each field maps to a
 * specific downstream consumer:
 *
 * - `user_facing_step` is what we show in "Why this is blocked"
 *   ("Search the web for OEM fleet programs in India") instead of
 *   the opaque slug ("cross_border_commerce").
 * - `description` is the longer one-sentence narration used on
 *   expansion.
 * - `search_query` is what the scouts ran against the discovery
 *   sources for this sub-task (capability-shaped, not goal-specific).
 * - `acceptance_criteria` is what the candidate judge filtered
 *   discovered candidates against.
 * - `suggested_capability_id` is the snake_case label used for
 *   cross-request aggregation (leaderboards, demand events).
 */
export type DecomposedSubTask = {
  description: string;
  user_facing_step: string;
  search_query: string;
  acceptance_criteria: string;
  suggested_capability_id: string;
};

export type GoalPlanPayload = {
  status: string;
  summary?: string;
  sub_tasks?: GoalSubTask[];
  refusal_reasons?: string[];
  missing_capabilities?: string[];
  planner?: Record<string, unknown> & {
    decomposed_sub_tasks?: DecomposedSubTask[];
    decomposer?: {
      status: string;
      intent_summary?: string;
      confidence?: number;
      catalog_reused_capabilities?: string[];
      new_capabilities?: string[];
      reason?: string;
    };
  };
  discovery?: {
    status: string;
    missing_capabilities: string[];
    decomposed_sub_tasks?: DecomposedSubTask[];
    searched_capabilities: string[];
    total_candidates: number;
    candidates: CandidateCard[];
    will_fail_reasons?: string[];
    /**
     * LLM-suggested public websites or services the user could use to
     * accomplish the goal manually. Surfaced when the system has no
     * specialist agent for the missing capabilities. The status field
     * disambiguates "we tried but the LLM was down" (`unavailable`)
     * from "we have agents so we didn't try" (`skipped_specialist_results`)
     * from "the LLM ran but produced nothing useful" (`empty`). The UI
     * should render the alternatives list only when status === `applied`.
     */
    human_alternatives?: HumanFallbackPayload;
    /**
     * Per-missing-capability demand snapshot. The UI uses this to show
     * "47 other users have asked for this" alongside each missing
     * capability so the gap feels like a real opportunity rather than
     * a one-off failure.
     */
    demand?: {
      events_recorded?: number;
      open_mcp_opportunities_link?: string;
      per_capability_summary?: DemandSummaryEntry[];
    };
    /**
     * Slice 2: horizontal research-agent backstop. Per-capability
     * search-+-synthesis output produced when no specialist agent
     * exists for the user's goal. The UI must render this with
     * explicit "general research, not a specialist" framing so users
     * don't mistake it for an authoritative agent verdict.
     */
    research_backstop?: ResearchBackstopPayload;
  } | null;
  gap_report?: Record<string, unknown>;
  /**
   * Sprint 4 T1-A: stable identifier for the cached plan so the user
   * can download the recipe via /recipe/export?goal_id=... at any time
   * within the cache TTL (default 7 days). Computed deterministically
   * from goal_text + plan summary by the API. Absent if the recipe
   * cache write failed (non-fatal).
   */
  goal_id?: string;
  /**
   * Sprint 4 T1-A: download URL per supported recipe format. The
   * frontend renders these as "Download for Claude Desktop / n8n /
   * Cursor / Markdown / CLI" buttons. The label is human-readable;
   * the format id is what the /recipe/export endpoint understands.
   */
  recipe_coverage?: RecipeCoverage;
  recipes?: RecipeDownload[];
  [key: string]: unknown;
};

export type RecipeCoverage = {
  step_count: number;
  recommended_step_count: number;
  exportable_step_count: number;
  gap_count: number;
  status: "empty" | "gap_only" | "partial" | "complete" | string;
};

export type RecipeDownload = {
  format: string;
  label: string;
  download_url: string;
  coverage?: RecipeCoverage;
};

/**
 * One LLM-suggested manual workaround. The UI renders this as a card
 * with the brand `name` as the title, the `why` as a one-line
 * rationale, and the `url` as the click-target.
 */
export type HumanAlternative = {
  url: string;
  name: string;
  why: string;
};

/**
 * Wire shape for the human-fallback suggester's response. `status` is
 * the only field the UI must branch on; alternatives is empty unless
 * status === "applied". `reason` is populated only on `unavailable`
 * and is intended for engineer-drawer debug display, not user copy.
 */
export type HumanFallbackPayload = {
  status:
    | "applied"
    | "empty"
    | "skipped_no_capabilities"
    | "skipped_specialist_results"
    | "unavailable"
    | "disabled";
  alternatives: HumanAlternative[];
  reason?: string;
};

/**
 * Per-capability aggregated demand row, sourced from the
 * `capability_demand_events` store. The UI renders the request_count
 * as the headline number ("47 requests") and the
 * distinct_requester_count as the secondary chip ("12 distinct
 * users"). Sample goals are shown as a hover tooltip so the rendered
 * list stays compact.
 */
export type DemandSummaryEntry = {
  capability_id: string;
  request_count: number;
  distinct_requester_count: number;
  last_requested_at: string;
  sample_goals: string[];
  has_local_match_count: number;
  has_apis_without_agents_count: number;
};

/**
 * One source the research agent considered for its synthesis. The UI
 * renders these as cited links beneath the summary; only sources
 * present in the `citations` array are styled as "cited", everything
 * else is rendered as "additional context" so the user can see the
 * full search context without confusing what was actually used.
 */
export type ResearchSource = {
  url: string;
  title: string;
  snippet: string;
};

/**
 * Result of one research-agent invocation for a single missing
 * capability. `status` discriminates between the LLM/search outcomes
 * defined in `general_research_agent.ResearchResult`.
 */
export type ResearchResultEntry = {
  status:
    | "applied"
    | "empty_search"
    | "missing_query"
    | "unavailable"
    | "disabled"
    | "missing_credentials";
  summary: string;
  sources: ResearchSource[];
  citations: string[];
  elapsed_ms: number;
  reason?: string;
};

/**
 * Top-level research-backstop block. `status` is the only field the
 * UI must branch on for show/hide decisions:
 *   - "applied" → at least one capability got a usable answer; render
 *     the card with all entries.
 *   - "no_results" → backstop ran for every capability but produced
 *     nothing useful; render a softer panel.
 *   - everything else (skipped/disabled/missing_credentials) → silent.
 */
export type ResearchBackstopPayload = {
  status:
    | "applied"
    | "no_results"
    | "skipped_specialist_results"
    | "disabled"
    | "missing_credentials"
    | "missing_query"
    | "unavailable";
  capability_id: string;
  results: Array<{
    capability_id: string;
    user_facing_step: string;
    result: ResearchResultEntry;
  }>;
  skipped_capabilities: string[];
  reason?: string;
};

export type GoalResponse = {
  ok: boolean;
  plan: GoalPlanPayload;
  executed: boolean;
  answer: string | null;
  summary: string | null;
  execution?: Record<string, unknown> | null;
};

export type GoalExplainRequest = {
  goal: string;
};

export type GoalExplainResponse = {
  ok: boolean;
  goal: string;
  model: string;
  thinking: string;
  content: string;
  // Best-effort JSON-parsed plan from the model's `content` output.
  // Null when parsing failed (the trace itself is the load-bearing
  // field for this endpoint, so we never block the response on plan
  // parsing).
  plan: Record<string, unknown> | null;
  duration_ms: number;
  // False for non-thinking models (qwen2.5, llama3.x). The UI uses
  // this to render "this model doesn't expose its reasoning" instead
  // of an empty "Reasoning trace" panel that looks broken.
  model_supports_thinking: boolean;
};

export const submitGoalExplain = async (
  request: GoalExplainRequest,
): Promise<GoalExplainResponse> => {
  const response = await fetch(`${apiBaseUrl()}/goal/explain`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    let detail = response.statusText;
    let detailObject: Record<string, unknown> | null = null;
    try {
      const body = await response.json();
      if (typeof body === "object" && body && "detail" in body) {
        const raw = (body as { detail: unknown }).detail;
        if (typeof raw === "string") {
          detail = raw;
        } else if (raw && typeof raw === "object") {
          detailObject = raw as Record<string, unknown>;
          const message = (raw as { message?: unknown; error?: unknown })
            .message;
          if (typeof message === "string" && message) {
            detail = message;
          }
        }
      }
    } catch {
      // swallow
    }
    const error: ApiError = {
      status: response.status,
      detail,
      detailObject,
    };
    throw error;
  }
  return (await response.json()) as GoalExplainResponse;
};

// ===========================================================================
// Pro tier (T1-B): saved recipes + account + billing
// ===========================================================================

export type AccountMe =
  | { authenticated: false }
  | {
      authenticated: true;
      user_id: string;
      clerk_user_id: string;
      email: string;
      plan: "free" | "pro" | "enterprise";
    };

export type SavedRecipe = {
  recipe_id: string;
  workspace_id: string;
  user_id: string;
  goal: string;
  format: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
  recipe_coverage?: RecipeCoverage | null;
  download_url: string;
};

export type SavedRecipesList = {
  total: number;
  recipes: SavedRecipe[];
};

export type CheckoutSession = {
  session_id: string;
  url: string;
  mode: "test" | "live";
};

/**
 * Run an authenticated fetch against the FastAPI backend.
 *
 * `token` is a Clerk session JWT (caller is expected to obtain it via
 * useAuth().getToken() and pass it in). The token is sent as a Bearer
 * credential so the backend's `current_user` dependency can resolve
 * the marketplace_store user row.
 *
 * Returns either the parsed JSON body or throws an ApiError with the
 * status code + structured detail so the UI can branch on
 * `401` (sign in) vs `402` (upgrade) vs `503` (billing not yet
 * configured) without inspecting strings.
 */
const authedFetch = async <T>(
  path: string,
  init: RequestInit & { token: string | null },
): Promise<T> => {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (init.token) {
    headers.set("Authorization", `Bearer ${init.token}`);
  }
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = response.statusText;
    let detailObject: Record<string, unknown> | null = null;
    try {
      const body = await response.json();
      if (typeof body === "object" && body && "detail" in body) {
        const raw = (body as { detail: unknown }).detail;
        if (typeof raw === "string") {
          detail = raw;
        } else if (raw && typeof raw === "object") {
          detailObject = raw as Record<string, unknown>;
          const message = (raw as { message?: unknown }).message;
          if (typeof message === "string") detail = message;
        }
      }
    } catch {
      // swallow JSON parse errors
    }
    throw { status: response.status, detail, detailObject } as ApiError;
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
};

export const fetchAccountMe = (token: string | null): Promise<AccountMe> =>
  authedFetch("/account/me", { method: "GET", token });

export const listSavedRecipes = (
  token: string | null,
): Promise<SavedRecipesList> =>
  authedFetch("/recipes", { method: "GET", token });

export const saveRecipe = (
  token: string | null,
  body: { goal_id: string; format: string; notes?: string },
): Promise<SavedRecipe> =>
  authedFetch("/recipes", {
    method: "POST",
    body: JSON.stringify(body),
    token,
  });

export const deleteSavedRecipe = (
  token: string | null,
  recipeId: string,
): Promise<void> =>
  authedFetch(`/recipes/${encodeURIComponent(recipeId)}`, {
    method: "DELETE",
    token,
  });

export const createBillingCheckoutSession = (
  token: string | null,
): Promise<CheckoutSession> =>
  authedFetch("/billing/checkout", { method: "POST", token });

// ===========================================================================
// /goal submission (existing — kept below the new exports for clarity)
// ===========================================================================

export const submitGoal = async (request: GoalRequest): Promise<GoalResponse> => {
  const response = await fetch(`${apiBaseUrl()}/goal`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    let detail = response.statusText;
    let detailObject: Record<string, unknown> | null = null;
    try {
      const body = await response.json();
      if (typeof body === "object" && body && "detail" in body) {
        const raw = (body as { detail: unknown }).detail;
        if (typeof raw === "string") {
          detail = raw;
        } else if (raw && typeof raw === "object") {
          detailObject = raw as Record<string, unknown>;
          // Surface the human-readable message as `detail` for any
          // caller that still treats it as a string. Falls back to
          // JSON-stringifying so we never lose information.
          const message = (raw as { message?: unknown; error?: unknown }).message;
          const errorTag = (raw as { error?: unknown }).error;
          if (typeof message === "string" && message) {
            detail = message;
          } else if (typeof errorTag === "string" && errorTag) {
            detail = errorTag;
          } else {
            detail = JSON.stringify(raw);
          }
        }
      }
    } catch {
      // swallow
    }
    const error: ApiError = {
      status: response.status,
      detail,
      detailObject,
    };
    throw error;
  }
  return (await response.json()) as GoalResponse;
};
