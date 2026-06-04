"use client";

import type {
  DemandSummaryEntry,
  HumanFallbackPayload,
} from "@/lib/api";

/**
 * Surface for the LLM-suggested manual workarounds the backend
 * attaches to a refused /goal response.
 *
 * Render contract:
 *
 *   - status="applied" with one or more validated alternatives →
 *     show the card with each suggestion (brand name, one-line "why",
 *     URL link).
 *   - status="empty" → show a softer "we tried but couldn't think of
 *     any reliable manual workarounds" panel so the user understands
 *     the system isn't broken, just out of ideas.
 *   - status="unavailable" → silent, the engineer drawer surfaces the
 *     reason; no need to alarm the user when the suggester LLM tier
 *     was simply unreachable.
 *   - status="skipped_specialist_results" → silent, agents *do* exist
 *     for this goal and are rendered elsewhere on the page.
 *   - status="skipped_no_capabilities" / "disabled" → silent.
 *
 * Demand summary is shown as a small footer chip when at least one
 * missing capability has prior demand. Helps the user feel "I'm not
 * alone in wanting this" without dominating the card.
 */
export function HumanAlternatives({
  payload,
  demandSummary,
}: {
  payload: HumanFallbackPayload | null | undefined;
  demandSummary: DemandSummaryEntry[] | null | undefined;
}) {
  if (!payload) return null;
  if (
    payload.status === "skipped_specialist_results" ||
    payload.status === "skipped_no_capabilities" ||
    payload.status === "disabled" ||
    payload.status === "unavailable"
  ) {
    return null;
  }

  const summary = (demandSummary ?? []).filter(
    (entry) => entry.request_count > 0,
  );
  const totalRequests = summary.reduce(
    (acc, entry) => acc + entry.request_count,
    0,
  );
  const distinctRequesters = summary.reduce(
    (acc, entry) => acc + entry.distinct_requester_count,
    0,
  );

  return (
    <section className="surface p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-ink-900">
            No agent can do this yet — try one of these manually
          </h3>
          <p className="mt-1 text-xs text-ink-600">
            We couldn&rsquo;t find an AI agent that solves this goal. Until
            one exists, here are public sites you can use by hand. The
            demand for this capability is logged so we can flag it when an
            agent does ship.
          </p>
        </div>
        <span className="tag-warn">Manual workaround</span>
      </header>

      {payload.status === "applied" && payload.alternatives.length > 0 && (
        <ul className="mt-4 grid gap-3 sm:grid-cols-2">
          {payload.alternatives.map((alt, index) => (
            <li
              key={`${alt.url}-${index}`}
              className="rounded-xl border border-ink-200 bg-white p-3 transition hover:border-accent-300"
            >
              <a
                href={alt.url}
                target="_blank"
                rel="noopener noreferrer"
                className="block"
              >
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-semibold text-ink-900">
                    {alt.name}
                  </span>
                  <span className="text-[10px] uppercase tracking-wide text-accent-600">
                    Open ↗
                  </span>
                </div>
                <p className="mt-1 line-clamp-3 text-xs text-ink-700">
                  {alt.why}
                </p>
                <p className="mt-2 truncate font-mono text-[10px] text-ink-400">
                  {alt.url}
                </p>
              </a>
            </li>
          ))}
        </ul>
      )}

      {payload.status === "empty" && (
        <div className="mt-4 rounded-lg border border-ink-200 bg-ink-50 p-3 text-sm text-ink-700">
          We tried to suggest a manual workaround but couldn&rsquo;t find a
          reliable public service. The capability is logged so when an
          agent ships we&rsquo;ll surface it the next time you ask.
        </div>
      )}

      {summary.length > 0 && (
        <footer className="mt-4 flex flex-wrap items-center gap-2 border-t border-ink-100 pt-3 text-xs text-ink-600">
          <span className="font-semibold text-ink-700">
            {distinctRequesters || totalRequests} other request
            {(distinctRequesters || totalRequests) === 1 ? "" : "s"} for this
          </span>
          <span className="text-ink-400">·</span>
          <span>
            Tracked across {summary.length} capabilit
            {summary.length === 1 ? "y" : "ies"}; ranked on the{" "}
            <a
              href="/open-mcp-opportunities"
              className="text-accent-600 underline-offset-2 hover:underline"
            >
              Open MCP Opportunities
            </a>{" "}
            board.
          </span>
        </footer>
      )}
    </section>
  );
}
