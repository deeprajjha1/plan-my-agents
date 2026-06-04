"use client";

import type {
  IndexFreshness,
  LiveDiscoveryStatus,
  LiveResearchStatus,
} from "./response-types";

/**
 * Compact header strip showing where this response's candidates came
 * from: the static index, the tier-1 free scouts run this request,
 * the tier-1 token-required scouts (GitHub PAT), and the tier-2 paid
 * SaaS scouts (Brave/Tavily/Exa).
 *
 * GitHub PAT is free, so it belongs in its own "free, requires
 * zero-cost token" bucket rather than being lumped with the paid
 * vendors. Backends old enough not to emit `tiers` fall back to
 * treating the whole missing list as paid; conservative.
 */
export function IndexProvenancePill({
  liveResearch,
  liveDiscovery,
  freshness,
}: {
  liveResearch: LiveResearchStatus | null;
  liveDiscovery: LiveDiscoveryStatus | null;
  freshness: IndexFreshness | null;
}) {
  const totalIndexed = freshness?.total ?? freshness?.total_agentic ?? null;
  const lastRefresh = freshness?.latest_refresh_day ?? null;
  const scoutsRan = liveDiscovery?.scouts_dispatched ?? 0;
  const tier1Status =
    scoutsRan > 0 ? `${scoutsRan} scouts ran` : "no scouts dispatched";

  const freeWithToken = liveResearch?.tiers?.free_with_token;
  const paidSearch = liveResearch?.tiers?.paid_search;
  const freeWithTokenMissing = freeWithToken?.missing ?? [];
  const freeWithTokenConfigured = freeWithToken?.configured ?? [];
  const paidSearchMissing =
    paidSearch?.missing ?? liveResearch?.missing_keys ?? [];

  const freeWithTokenStatus =
    freeWithToken === undefined
      ? null
      : freeWithTokenConfigured.length > 0
        ? `${freeWithTokenConfigured.length} configured`
        : `${freeWithTokenMissing.length} unset (free PAT)`;
  const paidStatus =
    paidSearchMissing.length > 0
      ? `${paidSearchMissing.length} unset`
      : "configured";

  return (
    <div className="grid gap-2 rounded-lg border border-ink-200 bg-ink-50 px-3 py-2 text-xs text-ink-700 sm:grid-cols-4">
      <div>
        <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
          Index
        </div>
        <div className="mt-0.5">{totalIndexed ?? "?"} candidates</div>
        <div className="text-ink-500">
          {lastRefresh ? `last refreshed ${lastRefresh}` : "freshness unknown"}
        </div>
      </div>
      <div>
        <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
          Tier-1 free scouts (this request)
        </div>
        <div className="mt-0.5 text-emerald-700">{tier1Status}</div>
        <div className="text-ink-500">
          MCP registry · APIs.guru · HN · vendor RSS
        </div>
      </div>
      {freeWithToken && (
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
            Tier-1 (free, requires token)
          </div>
          <div
            className={`mt-0.5 ${
              freeWithTokenConfigured.length > 0
                ? "text-emerald-700"
                : "text-amber-700"
            }`}
          >
            {freeWithTokenStatus}
          </div>
          <div className="text-ink-500">
            GitHub code search · GitHub recently-pushed
          </div>
        </div>
      )}
      <div>
        <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
          Tier-2 paid third-party
        </div>
        <div
          className={`mt-0.5 ${paidSearchMissing.length > 0 ? "text-amber-700" : "text-emerald-700"}`}
        >
          {paidStatus}
        </div>
        <div className="text-ink-500">Brave · Tavily · Exa</div>
      </div>
    </div>
  );
}
