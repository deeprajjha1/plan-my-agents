"use client";

import type { PlannerLlmQuality } from "./response-types";

/**
 * Renders the structured 503 PlanningUnavailableError payload from
 * the `/goal` endpoint. The user explicitly chose "refuse honestly"
 * semantics when both LLM tiers are down — this card is what makes
 * that refusal actionable: it tells the user *which* tier failed and
 * *what to do about it* (set `GROQ_API_KEY`, start Ollama, etc.)
 * instead of leaving them with a generic toast.
 *
 * Only renders when `detail.error === "planning_unavailable"`. Any
 * other error.detail shape returns `null` so the page can fall back
 * to whatever generic error card it would otherwise use.
 */
export function PlanningRefusalCard({
  detail,
}: {
  detail: Record<string, unknown>;
}) {
  const errorTag = typeof detail.error === "string" ? detail.error : "";
  if (errorTag !== "planning_unavailable") {
    return null;
  }
  const message = typeof detail.message === "string" ? detail.message : "";
  const remediation = Array.isArray(detail.remediation)
    ? (detail.remediation.filter((s) => typeof s === "string") as string[])
    : [];
  const llmQuality =
    (detail.llm_quality as PlannerLlmQuality | undefined) ?? null;
  const planner = llmQuality?.planner;
  const primaryStatus = planner
    ? planner.primary_attempted
      ? `attempted, ${planner.primary_error || "unknown error"}`
      : "not attempted"
    : "unknown";
  const fallbackStatus = planner
    ? planner.fallback_attempted
      ? `attempted, ${planner.fallback_error || "unknown error"}`
      : planner.fallback_error
        ? `not attempted (${planner.fallback_error})`
        : "not configured"
    : "unknown";

  return (
    <section className="rounded-2xl border border-rose-200 bg-rose-50 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-rose-700">
            Reasoning unavailable
          </div>
          <h2 className="mt-1 text-2xl font-semibold text-rose-900">
            No LLM tier could plan this goal
          </h2>
          {message && (
            <p className="mt-1 text-sm text-rose-800">{message}</p>
          )}
        </div>
        <span className="rounded-full border border-rose-300 bg-white px-2.5 py-0.5 text-xs font-medium text-rose-800">
          503 · refused honestly
        </span>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-rose-200 bg-white p-3 text-xs text-rose-900">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-rose-700">
            Primary tier ({planner?.primary_label || "—"})
          </div>
          <div className="mt-0.5">{primaryStatus}</div>
        </div>
        <div className="rounded-lg border border-rose-200 bg-white p-3 text-xs text-rose-900">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-rose-700">
            Fallback tier ({planner?.fallback_label || "—"})
          </div>
          <div className="mt-0.5">{fallbackStatus}</div>
        </div>
      </div>

      {remediation.length > 0 && (
        <div className="mt-4 rounded-lg border border-rose-200 bg-white p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-rose-700">
            How to fix
          </div>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-rose-900">
            {remediation.map((step, idx) => (
              <li key={idx}>{step}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-4 text-xs italic text-rose-700">
        PlanMyAgents will not silently fall back to substring rules.
        Restore an LLM tier and retry — refusing here prevents
        confidently-wrong results.
      </p>
    </section>
  );
}
