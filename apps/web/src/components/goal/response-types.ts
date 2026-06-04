/**
 * Shared types for the /goal response panel components.
 *
 * Until 2026-05-19 these lived inline inside `app/goal/page.tsx` —
 * one 1,370-line file with fourteen inline subcomponents. The
 * pre-launch audit identified the page as the second-highest-ROI
 * refactor; the extraction broke down by component, with this file
 * acting as the single source of truth for every shape the response
 * panel consumes.
 *
 * Most of these mirror Pydantic models on the backend
 * (`apps/api/planmyagents_api/web/models.py`) and helper records on
 * the planner / discovery side. The fields are documented near their
 * server-side counterparts; this file is intentionally pure-type so
 * tree-shaking can drop it from any client bundle that doesn't render
 * the response panel.
 */

export type CostEstimateSubTask = {
  capability: string;
  provider_id: string | null;
  avg_cost_usd: number | null;
  sample_size: number;
  source: string | null;
  basis: string;
};

export type CostEstimate = {
  total_estimated_usd?: number;
  covered_sub_task_count?: number;
  total_sub_task_count?: number;
  per_sub_task?: CostEstimateSubTask[];
  missing_capabilities?: string[];
  credibility_notes?: string[];
  error?: string;
};

export type LiveResearchKey = { env_var: string; label: string };

export type LiveResearchTierBucket = {
  configured: LiveResearchKey[];
  missing: LiveResearchKey[];
};

export type LiveResearchStatus = {
  status: "not_run_no_keys" | "not_run_per_request_disabled" | "ran" | string;
  ran: boolean;
  queried_sources: string[];
  skipped_sources: string[];
  configured_keys: LiveResearchKey[];
  missing_keys: LiveResearchKey[];
  // Optional — only present on backends new enough to split GitHub
  // (free PAT) from Brave/Tavily/Exa (paid SaaS).
  tiers?: {
    free_with_token?: LiveResearchTierBucket;
    paid_search?: LiveResearchTierBucket;
  };
  message: string;
  next_step_command?: string;
};

export type LiveDiscoveryScoutSummary = {
  scout_id: string;
  status: "ok" | "timeout" | "error" | "skipped" | string;
  candidate_count: number;
  elapsed_ms: number;
  skipped_reason?: string | null;
  error?: string | null;
};

export type LiveDiscoveryCandidate = {
  provider_id: string;
  display_name: string;
  vendor: string;
  vendor_url?: string | null;
  provider_type?: string;
  source: string;
  evidence_url?: string | null;
  capabilities?: Array<{ id: string; confidence?: number }>;
  freshly_discovered_at_request: boolean;
};

export type LiveDiscoveryPerCapability = {
  capability: string;
  query_expansion: {
    used_llm: boolean;
    fallback_reason?: string | null;
    per_scout_queries?: Record<string, string>;
  };
  dispatch: {
    sub_task_id?: string | null;
    capability?: string | null;
    total_elapsed_ms: number;
    merged_candidate_count: number;
    scouts: LiveDiscoveryScoutSummary[];
  };
};

export type LiveDiscoveryStatus = {
  status:
    | "ran"
    | "ran_persistence_failed"
    | "skipped_no_missing_capabilities"
    | "error"
    | string;
  scouts_dispatched?: number;
  candidates_found_total?: number;
  candidates_freshly_discovered?: number;
  candidates_persisted?: number;
  newly_discovered_candidates?: LiveDiscoveryCandidate[];
  per_capability?: LiveDiscoveryPerCapability[];
  error?: string;
};

export type IndexFreshness = {
  ok: boolean;
  total?: number;
  total_agentic?: number;
  total_apis_without_agents?: number;
  latest_refresh_day?: string | null;
  sources?: Array<{ source: string; count: number }>;
  first_seen_days?: Array<{ day: string; count: number }>;
  last_seen_days?: Array<{ day: string; count: number }>;
  error?: string;
};

/**
 * Mirrors `EscalationMetadata.to_json()` from the backend
 * (`apps/api/planmyagents_api/llm/escalating_client.py`). The
 * `planner.llm_quality.planner` field on /goal responses uses this
 * shape; the same shape appears inside 503 PlanningUnavailableError
 * detail payloads as `llm_quality.planner`.
 */
export type PlannerLlmQuality = {
  planner?: {
    tier_used?: "primary" | "fallback" | "none" | string;
    mode?: string;
    primary_label?: string;
    fallback_label?: string;
    primary_attempted?: boolean;
    primary_error?: string | null;
    quality_check_triggered?: boolean;
    fallback_attempted?: boolean;
    fallback_error?: string | null;
    warning?: string;
  };
};

/**
 * Mirrors `JudgeResult.to_summary()` from the backend
 * (`apps/api/planmyagents_api/discovery/candidate_judge.py`).
 */
export type CandidateJudgeMetadata = {
  status?:
    | "applied"
    | "disabled"
    | "skipped_no_results"
    | "store_unavailable"
    | "unavailable"
    | string;
  tier_used?: "primary" | "fallback" | "none" | string;
  primary_label?: string;
  fallback_label?: string;
  accepted?: number;
  rejected?: number;
  warning?: string;
  error?: string;
  sample_rejection_reason?: string;
};

export type ReasoningTone = "ok" | "warn" | "danger";

export type ReasoningInfo = {
  tone: ReasoningTone;
  headline: string;
  detail?: string;
};
