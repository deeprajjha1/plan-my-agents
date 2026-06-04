"use client";

import type { LiveResearchStatus } from "./response-types";

/**
 * Per-source breakdown of the live web research run for this
 * request: which scouts were queried, which were skipped, and which
 * env vars need to be set to enable the skipped ones.
 *
 * When the backend supplies tier metadata we render two clearly-
 * labelled groups so the user knows which keys cost $0 (GitHub PAT)
 * vs. paid SaaS (Brave/Tavily/Exa). Older backends without `tiers`
 * fall back to a flat list — they will still render correctly, just
 * without the tier split.
 */
export function LiveResearchDetail({
  status,
}: {
  status: LiveResearchStatus;
}) {
  return (
    <section>
      <header className="mb-2">
        <h3 className="text-sm font-semibold text-ink-900">
          Live web research
        </h3>
        <p className="text-xs text-ink-500">{status.message}</p>
      </header>

      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-ink-500">
            Queried
          </div>
          <ul className="mt-1 list-disc pl-5 text-xs text-ink-700">
            {status.queried_sources.length === 0 ? (
              <li className="list-none text-ink-400">none</li>
            ) : (
              status.queried_sources.map((source) => (
                <li key={source}>
                  <code>{source}</code>
                </li>
              ))
            )}
          </ul>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-ink-500">
            Skipped
          </div>
          <ul className="mt-1 list-disc pl-5 text-xs text-ink-700">
            {status.skipped_sources.length === 0 ? (
              <li className="list-none text-ink-400">none</li>
            ) : (
              status.skipped_sources.map((source) => (
                <li key={source}>
                  <code>{source}</code>
                </li>
              ))
            )}
          </ul>
        </div>
      </div>

      {status.missing_keys.length > 0 && (
        <div className="mt-3 space-y-3">
          {status.tiers ? (
            <>
              {status.tiers.free_with_token &&
                status.tiers.free_with_token.missing.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">
                      Free, requires token (set these first)
                    </div>
                    <ul className="mt-1 flex flex-wrap gap-2 text-xs">
                      {status.tiers.free_with_token.missing.map((key) => (
                        <li
                          key={key.env_var}
                          className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-ink-700"
                        >
                          <code>{key.env_var}</code>
                          <span className="ml-2 text-ink-500">{key.label}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              {status.tiers.paid_search &&
                status.tiers.paid_search.missing.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">
                      Paid third-party search (free tiers exist)
                    </div>
                    <ul className="mt-1 flex flex-wrap gap-2 text-xs">
                      {status.tiers.paid_search.missing.map((key) => (
                        <li
                          key={key.env_var}
                          className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-ink-700"
                        >
                          <code>{key.env_var}</code>
                          <span className="ml-2 text-ink-500">{key.label}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
            </>
          ) : (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                Set these env vars to enable live research
              </div>
              <ul className="mt-1 flex flex-wrap gap-2 text-xs">
                {status.missing_keys.map((key) => (
                  <li
                    key={key.env_var}
                    className="rounded border border-ink-200 bg-white px-2 py-1 text-ink-700"
                  >
                    <code>{key.env_var}</code>
                    <span className="ml-2 text-ink-500">{key.label}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {status.next_step_command && (
            <p className="text-xs text-ink-600">
              Then run <code>{status.next_step_command}</code> and retry.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
