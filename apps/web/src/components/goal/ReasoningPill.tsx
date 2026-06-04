"use client";

import type {
  CandidateJudgeMetadata,
  PlannerLlmQuality,
  ReasoningInfo,
  ReasoningTone,
} from "./response-types";

/**
 * Two-stage "where did this answer come from" provenance pill.
 *
 * Pre-2026-05-19 the planner + judge tier metadata was buried inside
 * the JSON download — invisible to anyone who didn't dig. This pill
 * forces the question front-and-centre: which model planned the
 * answer, which model filtered the candidates, and were either of
 * them degraded? The failure mode users need to see most clearly is
 * "planner ran on local Qwen, judge couldn't run at all" — staying
 * silent about that would defeat the entire point.
 */
export function ReasoningPill({
  planner,
  judge,
}: {
  planner: PlannerLlmQuality | null;
  judge: CandidateJudgeMetadata | null;
}) {
  const plannerInfo = describePlannerTier(planner?.planner);
  const judgeInfo = describeJudgeTier(judge);
  const overallTone = combineTone([plannerInfo.tone, judgeInfo.tone]);
  const containerClass =
    overallTone === "danger"
      ? "border-rose-200 bg-rose-50 text-rose-900"
      : overallTone === "warn"
        ? "border-amber-200 bg-amber-50 text-amber-900"
        : "border-emerald-200 bg-emerald-50 text-emerald-900";

  return (
    <div
      className={`grid gap-2 rounded-lg border px-3 py-2 text-xs sm:grid-cols-2 ${containerClass}`}
      role="status"
      aria-label="LLM reasoning provenance for this response"
    >
      <ReasoningStage label="Planner" info={plannerInfo} />
      <ReasoningStage label="Candidate filter" info={judgeInfo} />
    </div>
  );
}

function ReasoningStage({
  label,
  info,
}: {
  label: string;
  info: ReasoningInfo;
}) {
  const dotClass =
    info.tone === "ok"
      ? "bg-emerald-500"
      : info.tone === "warn"
        ? "bg-amber-500"
        : "bg-rose-500";
  return (
    <div>
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide opacity-80">
        <span className={`inline-block h-1.5 w-1.5 rounded-full ${dotClass}`} />
        {label}
      </div>
      <div className="mt-0.5 font-medium">{info.headline}</div>
      {info.detail ? (
        <div className="text-[11px] opacity-80">{info.detail}</div>
      ) : null}
    </div>
  );
}

function describePlannerTier(
  planner: PlannerLlmQuality["planner"] | undefined,
): ReasoningInfo {
  if (!planner) {
    return {
      tone: "warn",
      headline: "Reasoning provenance missing",
      detail: "Older response payload — no planner tier metadata.",
    };
  }
  if (planner.warning) {
    // Rules-mode debug fallback. Always loud — the user must know
    // their answer came from regex, not an LLM.
    return {
      tone: "danger",
      headline: "Substring rules planner (no LLM)",
      detail: planner.warning,
    };
  }
  const mode = planner.mode || "escalating";
  if (planner.tier_used === "fallback") {
    return {
      tone: "warn",
      headline: `Reasoning by ${planner.fallback_label || "fallback"} (escalated)`,
      detail: `Primary ${planner.primary_label || "tier"} failed: ${planner.primary_error || "unknown"}`,
    };
  }
  if (planner.tier_used === "primary") {
    return {
      tone: "ok",
      headline: `Reasoning by ${planner.primary_label || "primary"}`,
      detail: mode === "escalating" ? "No fallback needed." : `mode=${mode}`,
    };
  }
  // tier_used="none" or unknown — every escalating-mode path that
  // gets here means the planner refused; a 503 response would
  // normally have intercepted, so this branch indicates the response
  // shape is partial. Surface it loudly rather than pretending
  // everything is fine.
  return {
    tone: "danger",
    headline: "Planner reasoning unavailable",
    detail:
      planner.fallback_error ||
      planner.primary_error ||
      "no tier produced an answer",
  };
}

function describeJudgeTier(judge: CandidateJudgeMetadata | null): ReasoningInfo {
  if (!judge) {
    return {
      tone: "warn",
      headline: "Candidate filter not run",
      detail: "No discovery for this request, or older API payload.",
    };
  }
  if (judge.status === "skipped_no_results") {
    return {
      tone: "ok",
      headline: "No candidates to filter",
    };
  }
  if (judge.status === "disabled") {
    return {
      tone: "warn",
      headline: "Candidate filter disabled",
      detail:
        judge.warning ||
        "PLANMYAGENTS_CANDIDATE_JUDGE=off — irrelevant candidates may appear.",
    };
  }
  if (judge.status === "unavailable" || judge.status === "store_unavailable") {
    return {
      tone: "danger",
      headline: "Candidate filter unavailable",
      detail:
        judge.warning ||
        judge.error ||
        "Filter could not run — candidates below may be irrelevant.",
    };
  }
  // status === "applied"
  const accepted = judge.accepted ?? 0;
  const rejected = judge.rejected ?? 0;
  const tier =
    judge.tier_used === "fallback"
      ? judge.fallback_label || "fallback"
      : judge.primary_label || judge.tier_used || "primary";
  const tone: ReasoningTone =
    judge.tier_used === "fallback" ? "warn" : "ok";
  return {
    tone,
    headline: `Filter by ${tier}: ${accepted} kept, ${rejected} dropped`,
    detail: judge.sample_rejection_reason
      ? `e.g. dropped — ${judge.sample_rejection_reason}`
      : undefined,
  };
}

function combineTone(tones: ReasoningTone[]): ReasoningTone {
  if (tones.includes("danger")) return "danger";
  if (tones.includes("warn")) return "warn";
  return "ok";
}
