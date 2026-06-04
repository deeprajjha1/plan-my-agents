"use client";

import type { CostEstimate } from "./response-types";

/**
 * Renders the planner's cost preview block.
 *
 * Pricing in this preview is sourced from real benchmark adapters
 * only — synthetic/mock prices are excluded so the dollar figure
 * cannot be inflated by fixture data. When a sub-task has no real
 * adapter pricing it shows "no real-adapter pricing" instead of
 * silently dropping to $0.
 */
export function CostPreview({ estimate }: { estimate: CostEstimate }) {
  if (estimate.error) {
    return (
      <section className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-xs text-ink-700">
        Cost preview unavailable: {estimate.error}
      </section>
    );
  }
  const total = estimate.total_estimated_usd ?? 0;
  const covered = estimate.covered_sub_task_count ?? 0;
  const totalCount = estimate.total_sub_task_count ?? 0;
  const partial = covered < totalCount;
  return (
    <section className="surface p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink-900">
          Estimated cost{partial ? " (partial)" : ""}
        </h3>
        <span className="text-lg font-semibold tabular-nums text-ink-900">
          ${total.toFixed(4)}
        </span>
      </header>
      <p className="mt-1 text-xs text-ink-500">
        Based on the cheapest provider with real benchmark pricing per
        sub-task. Synthetic / mock prices are excluded.
      </p>
      {estimate.per_sub_task && estimate.per_sub_task.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-ink-700">
          {estimate.per_sub_task.map((row, idx) => (
            <li
              key={`${row.capability}-${idx}`}
              className="flex flex-wrap items-baseline justify-between gap-2"
            >
              <span>
                <code>{row.capability}</code>
                {row.provider_id && (
                  <span className="ml-2 text-ink-500">
                    via {row.provider_id}
                  </span>
                )}
              </span>
              <span className="tabular-nums text-ink-700">
                {row.avg_cost_usd === null
                  ? "no real-adapter pricing"
                  : `$${row.avg_cost_usd.toFixed(4)}`}
              </span>
            </li>
          ))}
        </ul>
      )}
      {estimate.credibility_notes && estimate.credibility_notes.length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-xs text-amber-700">
          {estimate.credibility_notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
