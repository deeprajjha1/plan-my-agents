"use client";

import type { IndexFreshness } from "./response-types";

/**
 * Expands the index-freshness header pill into a three-column
 * provenance grid: which scout/catalog produced each candidate, when
 * we first added it, and when we last refreshed it.
 *
 * Pre-launch this lived in `goal/page.tsx`. Lifted out because the
 * page was 1.3 kLOC and growing — extraction matches the same
 * boundaries the user would expect from the surrounding components
 * (CostPreview, LiveDiscoveryPanel, ...).
 */
export function IndexProvenanceDetail({
  freshness,
}: {
  freshness: IndexFreshness;
}) {
  const sources = freshness.sources ?? [];
  const firstSeen = freshness.first_seen_days ?? [];
  const lastSeen = freshness.last_seen_days ?? [];
  return (
    <section>
      <header className="mb-2">
        <h3 className="text-sm font-semibold text-ink-900">
          Where the {freshness.total ?? "?"} indexed candidates came from
        </h3>
        <p className="text-xs text-ink-500">
          {freshness.total_agentic ?? 0} agents ·{" "}
          {freshness.total_apis_without_agents ?? 0} APIs without an agent.
          Each row shows the scout / catalog that produced it.
        </p>
      </header>
      <div className="grid gap-4 md:grid-cols-3">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
            By source
          </div>
          <ul className="mt-1 space-y-0.5 text-xs text-ink-700">
            {sources.map((row) => (
              <li
                key={row.source}
                className="flex items-baseline justify-between gap-2"
              >
                <code className="truncate">{row.source}</code>
                <span className="tabular-nums text-ink-500">{row.count}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
            First seen (when added to index)
          </div>
          <ul className="mt-1 space-y-0.5 text-xs text-ink-700">
            {firstSeen.map((row) => (
              <li
                key={row.day}
                className="flex items-baseline justify-between gap-2"
              >
                <span className="tabular-nums">{row.day}</span>
                <span className="tabular-nums text-ink-500">+{row.count}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
            Last seen (last scout refresh)
          </div>
          <ul className="mt-1 space-y-0.5 text-xs text-ink-700">
            {lastSeen.map((row) => (
              <li
                key={row.day}
                className="flex items-baseline justify-between gap-2"
              >
                <span className="tabular-nums">{row.day}</span>
                <span className="tabular-nums text-ink-500">{row.count}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
