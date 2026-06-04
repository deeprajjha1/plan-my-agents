"use client";

import type { LiveDiscoveryStatus } from "./response-types";

/**
 * Visual lookup for scout-status colour coding. Each `status` string
 * comes straight from the backend's `LiveDiscoveryScoutSummary`
 * (see `apps/api/planmyagents_api/discovery/live_discovery.py`).
 * Unknown statuses default to a neutral ink tone in the cell below.
 */
const SCOUT_STATUS_TAG: Record<string, string> = {
  ok: "border-emerald-300 bg-emerald-50 text-emerald-800",
  timeout: "border-amber-300 bg-amber-50 text-amber-800",
  error: "border-rose-300 bg-rose-50 text-rose-800",
  skipped: "border-ink-200 bg-ink-50 text-ink-700",
};

/**
 * Detail panel for the request-time discovery sweep.
 *
 * Skipped (returns null) when the backend short-circuits via
 * `skipped_no_missing_capabilities` — nothing to show in that case.
 * On `error` we surface the backend message instead of the panel so
 * the failure is visible at the same prominence as a successful run.
 */
export function LiveDiscoveryPanel({
  status,
}: {
  status: LiveDiscoveryStatus;
}) {
  if (status.status === "skipped_no_missing_capabilities") {
    return null;
  }
  if (status.status === "error") {
    return (
      <section className="rounded-lg border border-rose-300 bg-rose-50 p-4 text-xs text-ink-800">
        <div className="text-sm font-semibold text-ink-900">
          Request-time discovery failed
        </div>
        <p className="mt-1">{status.error}</p>
      </section>
    );
  }

  const candidates = status.newly_discovered_candidates ?? [];
  const perCapability = status.per_capability ?? [];
  const freshCount = status.candidates_freshly_discovered ?? 0;
  const dispatchedCount = status.scouts_dispatched ?? 0;
  const persistedCount = status.candidates_persisted ?? 0;

  return (
    <section className="surface space-y-4 p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold text-ink-900">
            Just searched the live web for this request
          </h3>
          <p className="text-xs text-ink-500">
            We dispatched {dispatchedCount} scout fleet
            {dispatchedCount === 1 ? "" : "s"} (one per missing capability,
            capped at 3) and persisted {persistedCount} new candidate
            {persistedCount === 1 ? "" : "s"} into the index. {freshCount} of
            them are flagged as fresh for this request.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 font-semibold text-emerald-800">
            {freshCount} freshly discovered
          </span>
          <span className="rounded border border-ink-200 bg-ink-50 px-2 py-1 text-ink-700">
            {dispatchedCount} dispatch{dispatchedCount === 1 ? "" : "es"}
          </span>
        </div>
      </header>

      {perCapability.length > 0 && (
        <div className="space-y-3">
          {perCapability.map((cap) => (
            <details
              key={cap.capability}
              className="rounded border border-ink-200 bg-white"
              open={cap.dispatch.merged_candidate_count > 0}
            >
              <summary className="cursor-pointer list-none px-3 py-2 text-xs">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="font-semibold text-ink-900">
                    <code>{cap.capability}</code>
                  </span>
                  <span className="text-ink-500">
                    {cap.dispatch.merged_candidate_count} candidate
                    {cap.dispatch.merged_candidate_count === 1 ? "" : "s"} ·{" "}
                    {cap.dispatch.total_elapsed_ms} ms ·{" "}
                    {cap.query_expansion.used_llm
                      ? "LLM-expanded queries"
                      : `rules fallback (${cap.query_expansion.fallback_reason ?? "n/a"})`}
                  </span>
                </div>
              </summary>
              <div className="border-t border-ink-200 px-3 py-2">
                <div className="grid gap-1 text-xs">
                  {cap.dispatch.scouts.map((s) => (
                    <div
                      key={s.scout_id}
                      className="flex flex-wrap items-baseline gap-2"
                    >
                      <span
                        className={`rounded border px-2 py-0.5 text-[11px] font-medium ${
                          SCOUT_STATUS_TAG[s.status] ??
                          "border-ink-200 bg-ink-50 text-ink-700"
                        }`}
                      >
                        {s.status}
                      </span>
                      <code className="text-ink-700">{s.scout_id}</code>
                      <span className="text-ink-500">
                        {s.elapsed_ms} ms · {s.candidate_count} cand
                      </span>
                      {s.error && (
                        <span className="text-rose-700">{s.error}</span>
                      )}
                      {s.skipped_reason && (
                        <span className="text-ink-500">
                          {s.skipped_reason}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </details>
          ))}
        </div>
      )}

      {candidates.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
            Discovered candidates
          </div>
          <ul className="grid gap-2">
            {candidates.slice(0, 12).map((c) => (
              <li
                key={c.provider_id}
                className="flex flex-wrap items-baseline justify-between gap-2 rounded border border-ink-200 bg-white px-3 py-2 text-xs"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-semibold text-ink-900">
                      {c.display_name || c.provider_id}
                    </span>
                    {c.freshly_discovered_at_request && (
                      <span className="rounded border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-800">
                        FRESH
                      </span>
                    )}
                    <span className="text-ink-500">
                      via <code>{c.source}</code>
                    </span>
                  </div>
                  <div className="mt-0.5 text-ink-500">
                    {c.vendor || "unknown vendor"} · {c.provider_type}
                    {c.evidence_url && (
                      <>
                        {" · "}
                        <a
                          href={c.evidence_url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-accent-700 underline"
                        >
                          evidence
                        </a>
                      </>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
          {candidates.length > 12 && (
            <p className="mt-2 text-xs text-ink-500">
              {candidates.length - 12} more not shown — open the response JSON
              below for the full list.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
