"use client";

import { useState } from "react";

import type { ResearchBackstopPayload } from "@/lib/api";

/**
 * Surface for the first-party general research agent's output.
 *
 * Render contract:
 *   - status="applied" → card with one accordion per capability that
 *     produced a usable answer. Each entry shows the synthesised
 *     summary, the cited sources (linked, with snippets), and the
 *     full search context behind a toggle.
 *   - status="no_results" → softer panel telling the user the
 *     backstop ran but found nothing useful.
 *   - everything else (skipped/disabled/missing_credentials/
 *     unavailable) → silent. The other refusal cards already cover
 *     those cases; we don't want to add noise telling users that
 *     yet another optional component is unavailable.
 *
 * Visual framing is deliberately distinct from the specialist
 * "MissingCapabilities" block: a blue-tinted advisory border rather
 * than the warning-amber the refusal blockers use, plus an "Advisory
 * answer" tag that makes the lower-quality status obvious. This is a
 * load-bearing part of the design — users must NOT mistake a
 * research-agent answer for an authoritative specialist verdict.
 */
export function ResearchBackstop({
  payload,
}: {
  payload: ResearchBackstopPayload | null | undefined;
}) {
  if (!payload) return null;
  if (
    payload.status !== "applied" &&
    payload.status !== "no_results"
  ) {
    return null;
  }

  const usefulEntries = payload.results.filter(
    (entry) => entry.result.status === "applied" && entry.result.summary,
  );

  return (
    <section className="rounded-2xl border border-blue-200 bg-blue-50/50 p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-ink-900">
            Best-effort answer from the general research agent
          </h3>
          <p className="mt-1 text-xs text-ink-700">
            No specialist agent fits this goal yet. We ran a web search
            and asked an LLM to synthesise an answer from public sources.
            Treat this as an advisory starting point, not a verified
            specialist verdict.
          </p>
        </div>
        <span className="inline-flex items-center rounded border border-blue-300 bg-blue-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-800">
          Advisory answer
        </span>
      </header>

      {payload.status === "no_results" && (
        <div className="mt-4 rounded-lg border border-blue-200 bg-white p-3 text-sm text-ink-700">
          The research agent ran but couldn&rsquo;t find usable public
          sources for this goal. The capability is logged so when a
          specialist agent ships we&rsquo;ll surface it the next time
          you ask.
        </div>
      )}

      {payload.status === "applied" && usefulEntries.length === 0 && (
        <div className="mt-4 rounded-lg border border-blue-200 bg-white p-3 text-sm text-ink-700">
          The research agent partially ran but didn&rsquo;t produce a
          usable summary. See the engineer drawer below for details.
        </div>
      )}

      {payload.status === "applied" && usefulEntries.length > 0 && (
        <ul className="mt-4 space-y-3">
          {usefulEntries.map((entry, index) => (
            <ResearchEntry key={`${entry.capability_id}-${index}`} entry={entry} />
          ))}
        </ul>
      )}

      {payload.skipped_capabilities.length > 0 && (
        <p className="mt-3 text-xs text-ink-500">
          {payload.skipped_capabilities.length} additional capabilit
          {payload.skipped_capabilities.length === 1 ? "y" : "ies"} weren&rsquo;t
          researched on this request to keep latency bounded. Re-run the
          query to see them.
        </p>
      )}
    </section>
  );
}

function ResearchEntry({
  entry,
}: {
  entry: ResearchBackstopPayload["results"][number];
}) {
  const [showAllSources, setShowAllSources] = useState(false);
  const result = entry.result;
  const citedUrls = new Set(result.citations);
  const cited = result.sources.filter((s) => citedUrls.has(s.url));
  const uncited = result.sources.filter((s) => !citedUrls.has(s.url));

  return (
    <li className="rounded-xl border border-blue-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-ink-900">
            {entry.user_facing_step || entry.capability_id}
          </div>
          <div className="mt-1 text-[10px] uppercase tracking-wide text-ink-500">
            Capability: {entry.capability_id}
          </div>
        </div>
        {result.elapsed_ms > 0 && (
          <span className="text-[10px] text-ink-400">
            synthesised in {result.elapsed_ms}ms
          </span>
        )}
      </div>

      <p className="mt-3 whitespace-pre-wrap text-sm text-ink-800">
        {result.summary}
      </p>

      {cited.length > 0 && (
        <div className="mt-4">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
            Cited sources
          </div>
          <ul className="mt-2 space-y-2">
            {cited.map((source, index) => (
              <SourceRow
                key={`${source.url}-${index}`}
                source={source}
                cited
              />
            ))}
          </ul>
        </div>
      )}

      {uncited.length > 0 && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setShowAllSources((prev) => !prev)}
            className="text-xs font-medium text-ink-600 underline-offset-2 hover:underline"
          >
            {showAllSources ? "Hide" : "Show"} {uncited.length} additional
            search result{uncited.length === 1 ? "" : "s"} (not cited)
          </button>
          {showAllSources && (
            <ul className="mt-2 space-y-2">
              {uncited.map((source, index) => (
                <SourceRow
                  key={`${source.url}-${index}`}
                  source={source}
                  cited={false}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

function SourceRow({
  source,
  cited,
}: {
  source: { url: string; title: string; snippet: string };
  cited: boolean;
}) {
  return (
    <li
      className={
        cited
          ? "rounded-lg border border-blue-200 bg-blue-50/50 p-2"
          : "rounded-lg border border-ink-200 bg-ink-50 p-2"
      }
    >
      <a
        href={source.url}
        target="_blank"
        rel="noopener noreferrer"
        className="block"
      >
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-ink-900">
            {source.title || source.url}
          </span>
          <span className="text-[10px] uppercase tracking-wide text-accent-600">
            ↗
          </span>
        </div>
        {source.snippet && (
          <p className="mt-1 line-clamp-2 text-xs text-ink-700">
            {source.snippet}
          </p>
        )}
        <p className="mt-1 truncate font-mono text-[10px] text-ink-400">
          {source.url}
        </p>
      </a>
    </li>
  );
}
