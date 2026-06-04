"use client";

import type { DiscoveryCandidate } from "@/components/goal/MissingCapabilities";
import { DiscoveryProgressStrip } from "@/components/goal/DiscoveryProgressStrip";

import { CostPreview } from "./CostPreview";
import { IndexProvenanceDetail } from "./IndexProvenanceDetail";
import { LiveDiscoveryPanel } from "./LiveDiscoveryPanel";
import { LiveResearchDetail } from "./LiveResearchDetail";
import type {
  CostEstimate,
  IndexFreshness,
  LiveDiscoveryStatus,
  LiveResearchStatus,
} from "./response-types";

/**
 * Collapsible "everything else" panel: index provenance, cost
 * estimate, live discovery scouts, live web research gaps, and
 * promotion progress for each surfaced candidate.
 *
 * Kept collapsed by default — the response card above already shows
 * the headline answer and the page becomes unfriendly tall when
 * every detail is open. The Live-research gap row only renders when
 * research was skipped *and* live discovery didn't dispatch any
 * scouts; if scouts ran, the LiveDiscoveryPanel section already
 * covers the "what did we look at" question.
 */
export function DetailsAccordion({
  costEstimate,
  liveDiscovery,
  liveResearch,
  freshness,
  candidates,
}: {
  costEstimate: CostEstimate | null;
  liveDiscovery: LiveDiscoveryStatus | null;
  liveResearch: LiveResearchStatus | null;
  freshness: IndexFreshness | null;
  candidates: DiscoveryCandidate[];
}) {
  const liveScoutsDispatched = (liveDiscovery?.scouts_dispatched ?? 0) > 0;
  const showLiveResearchGap = Boolean(
    liveResearch && !liveResearch.ran && !liveScoutsDispatched,
  );

  return (
    <details className="surface p-0">
      <summary className="cursor-pointer list-none px-5 py-3 text-sm font-semibold text-ink-700 hover:text-ink-900">
        <span className="mr-2 text-ink-400">▸</span>
        Details (provenance, cost, scouts, promotion progress)
      </summary>
      <div className="space-y-4 border-t border-ink-100 px-5 py-4">
        {freshness && freshness.ok && (
          <IndexProvenanceDetail freshness={freshness} />
        )}
        {costEstimate && <CostPreview estimate={costEstimate} />}
        {liveDiscovery &&
          liveDiscovery.status !== "skipped_no_missing_capabilities" && (
            <LiveDiscoveryPanel status={liveDiscovery} />
          )}
        {showLiveResearchGap && liveResearch && (
          <LiveResearchDetail status={liveResearch} />
        )}
        {candidates.length > 0 && (
          <section>
            <header className="mb-2">
              <h3 className="text-sm font-semibold text-ink-900">
                Promotion progress
              </h3>
              <p className="text-xs text-ink-500">
                How far the surfaced candidates are from being routable.
              </p>
            </header>
            <DiscoveryProgressStrip candidates={candidates} />
          </section>
        )}
      </div>
    </details>
  );
}
