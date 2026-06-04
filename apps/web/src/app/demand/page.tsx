/**
 * Public demand-signal page (sprint-6 / C).
 *
 * The vendor-outreach hook: drops a sharable URL on top of the
 * `/demand/top-capabilities` and `/demand/gaps` APIs so the founder can
 * point an Apollo / Apify / Resend RevOps contact at one link that
 * answers "what would my team build that would route?".
 *
 * Both panels render flat tables; the data layer is the same one the
 * homepage strip and the discovery-gaps page already consume, so all
 * three surfaces agree on the routable-count semantics by construction.
 *
 * The third panel is a "Take action" footer that:
 *   - links to /goal so a vendor can stress-test our refusal flow
 *   - links to /open-mcp-opportunities + /discovery-gaps for richer
 *     drill-downs
 *   - documents the raw JSON endpoints so the contact's eng team can
 *     consume the data directly
 */

import Link from "next/link";

import {
  fetchDemandGaps,
  fetchDemandTopCapabilities,
  type DemandGapEntry,
  type DemandTopCapabilityEntry,
} from "@/lib/api";
import { formatLabel } from "@/lib/format";

const formatTimestamp = (raw: string): string => {
  if (!raw) return "—";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toISOString().replace("T", " ").slice(0, 16) + " UTC";
};

export default async function PublicDemandPage() {
  let top: Awaited<ReturnType<typeof fetchDemandTopCapabilities>> | null = null;
  let gaps: Awaited<ReturnType<typeof fetchDemandGaps>> | null = null;
  let loadError: string | null = null;
  try {
    [top, gaps] = await Promise.all([
      fetchDemandTopCapabilities(20, 30),
      fetchDemandGaps(20, 30),
    ]);
  } catch (error) {
    loadError =
      (error as { detail?: string }).detail ?? String(error) ?? "fetch failed";
  }

  if (loadError) {
    return (
      <div className="surface p-8">
        <h1 className="text-xl font-semibold text-ink-900">
          Demand signal not reachable
        </h1>
        <p className="mt-2 text-sm text-ink-600">
          The public demand APIs return rate-limited live counts from
          PlanMyAgents&apos; demand + gap stores. Start the FastAPI app and
          refresh:
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-ink-900 px-4 py-3 text-xs text-ink-50">
          make api
        </pre>
        <p className="mt-3 text-xs text-ink-400">Error: {loadError}</p>
      </div>
    );
  }

  const topRows = top?.entries ?? [];
  const gapRows = gaps?.entries ?? [];
  const totalRequests = topRows.reduce(
    (sum, row) => sum + row.request_count,
    0,
  );
  const routableTop = topRows.filter((row) => row.routable_today).length;

  return (
    <div
      className="space-y-8"
      data-testid="public-demand-page"
      data-state="ok"
    >
      <section>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-ink-900">
              Demand signal — what the world is asking for
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-ink-600">
              PlanMyAgents records every <code>/goal</code> request and
              every refusal. This page surfaces the two questions a
              vendor or design partner actually asks before they decide
              what to build:
            </p>
            <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-ink-700">
              <li>
                <strong>Top capabilities</strong> — what people request
                most, in the last 30 days.
              </li>
              <li>
                <strong>Routable gaps</strong> — capabilities people ask
                for that today have no routable agent OR for which our
                discovery + judge pass returned zero acceptable
                candidates.
              </li>
            </ol>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <Stat label="Capabilities" value={top?.total ?? 0} />
            <Stat label="Requests (30d)" value={totalRequests} />
            <Stat label="Routable today" value={routableTop} />
          </div>
        </div>
      </section>

      <section data-testid="demand-top-capabilities-panel">
        <header className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold text-ink-900">
            Top capabilities by request count (30d)
          </h2>
          <code className="text-xs text-ink-400">
            GET /demand/top-capabilities
          </code>
        </header>
        {topRows.length === 0 ? (
          <p className="surface mt-3 p-4 text-sm text-ink-500">
            No demand events recorded in the lookback window yet.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto rounded-md border border-ink-200">
            <table className="min-w-full divide-y divide-ink-200 text-sm">
              <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-400">
                <tr>
                  <th className="px-3 py-2 text-left">Capability</th>
                  <th className="px-3 py-2 text-right">Requests</th>
                  <th className="px-3 py-2 text-right">Distinct askers</th>
                  <th className="px-3 py-2 text-left">Routable today</th>
                  <th className="px-3 py-2 text-left">Last asked</th>
                  <th className="px-3 py-2 text-left">Recent goals</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {topRows.map((row: DemandTopCapabilityEntry) => (
                  <tr
                    key={row.capability_id}
                    className="bg-white"
                    data-testid={`demand-top-row-${row.capability_id}`}
                  >
                    <td className="px-3 py-2 align-top">
                      <div className="font-medium text-ink-900">
                        {formatLabel(row.capability_id)}
                      </div>
                      <code className="text-xs text-ink-400">
                        {row.capability_id}
                      </code>
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums font-semibold text-ink-900">
                      {row.request_count}
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums text-ink-700">
                      {row.distinct_requester_count}
                    </td>
                    <td className="px-3 py-2 align-top text-xs">
                      {row.routable_today ? (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-emerald-700">
                          Yes
                          {row.routable_provider_ids.length > 0 && (
                            <>
                              {" "}
                              <span className="text-ink-500">
                                ({row.routable_provider_ids.join(", ")})
                              </span>
                            </>
                          )}
                        </span>
                      ) : (
                        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-700">
                          No
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 align-top text-xs text-ink-500">
                      {formatTimestamp(row.last_requested_at)}
                    </td>
                    <td className="px-3 py-2 align-top text-xs italic text-ink-500">
                      {row.sample_goals.length > 0 ? (
                        <ul className="space-y-1">
                          {row.sample_goals.slice(0, 2).map((goal: string) => (
                            <li key={goal}>&ldquo;{goal}&rdquo;</li>
                          ))}
                        </ul>
                      ) : (
                        <span className="text-ink-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section data-testid="demand-gaps-panel">
        <header className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold text-ink-900">
            Routable gaps — capabilities with demand and no supply
          </h2>
          <code className="text-xs text-ink-400">GET /demand/gaps</code>
        </header>
        <p className="mt-1 max-w-3xl text-xs text-ink-500">
          Sorted by zero-yield count desc (the brutally-honest &ldquo;we
          tried discovery N times and surfaced nothing&rdquo; signal),
          then request count desc.
        </p>
        {gapRows.length === 0 ? (
          <p className="surface mt-3 p-4 text-sm text-ink-500">
            No demand-with-supply-gap capabilities in the lookback
            window. Either we cover everything people are asking for
            today (unlikely), or no demand has been recorded yet.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto rounded-md border border-ink-200">
            <table className="min-w-full divide-y divide-ink-200 text-sm">
              <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-400">
                <tr>
                  <th className="px-3 py-2 text-left">Capability</th>
                  <th className="px-3 py-2 text-right">Requests</th>
                  <th className="px-3 py-2 text-right">Discovery attempts</th>
                  <th className="px-3 py-2 text-right">Zero-yield</th>
                  <th className="px-3 py-2 text-left">Recent goals</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {gapRows.map((row: DemandGapEntry) => (
                  <tr
                    key={row.capability_id}
                    className="bg-white"
                    data-testid={`demand-gap-row-${row.capability_id}`}
                  >
                    <td className="px-3 py-2 align-top">
                      <div className="font-medium text-ink-900">
                        {formatLabel(row.capability_id)}
                      </div>
                      <code className="text-xs text-ink-400">
                        {row.capability_id}
                      </code>
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums font-semibold text-ink-900">
                      {row.request_count}
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums text-ink-700">
                      {row.discovery_attempts}
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums text-ink-700">
                      {row.zero_yield_count}
                    </td>
                    <td className="px-3 py-2 align-top text-xs italic text-ink-500">
                      {row.sample_goals.length > 0 ? (
                        <ul className="space-y-1">
                          {row.sample_goals.slice(0, 2).map((goal: string) => (
                            <li key={goal}>&ldquo;{goal}&rdquo;</li>
                          ))}
                        </ul>
                      ) : (
                        <span className="text-ink-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="surface-muted space-y-3 p-5 text-sm text-ink-700">
        <h2 className="text-base font-semibold text-ink-900">
          Take action
        </h2>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>
            <strong>Vendors:</strong> any row above with
            &ldquo;Routable today: No&rdquo; and a non-zero request
            count is a build target. If you wrap one of these as MCP /
            A2A / OpenAPI, file a PR against{" "}
            <code>packages/registry/agents.json</code> and the next
            benchmark cron picks you up.
          </li>
          <li>
            <strong>Design partners:</strong> stress-test the refusal
            flow on{" "}
            <Link href="/goal" className="text-accent-600 hover:underline">
              /goal
            </Link>
            . Every refusal lands here within minutes.
          </li>
          <li>
            <strong>Richer drill-downs:</strong>{" "}
            <Link
              href="/discovery-gaps"
              className="text-accent-600 hover:underline"
            >
              /discovery-gaps
            </Link>{" "}
            (per-capability outcome rollup) and{" "}
            <Link
              href="/open-mcp-opportunities"
              className="text-accent-600 hover:underline"
            >
              /open-mcp-opportunities
            </Link>{" "}
            (capabilities where an OpenAPI spec exists but no MCP wraps
            it).
          </li>
        </ul>
        <p className="text-xs text-ink-500">
          Raw APIs: <code>GET /demand/top-capabilities</code> +{" "}
          <code>GET /demand/gaps</code>. Both rate-limited to 60 RPM
          per IP by default — set{" "}
          <code>PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN</code> to tune.
          For an exportable CSV that joins demand, gaps, and the
          current routable-today set per capability:{" "}
          <code>make demand-snapshot</code> writes{" "}
          <code>data/demand_snapshot.csv</code>.
        </p>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="surface-muted px-4 py-2 text-right">
      <div className="text-ink-400">{label}</div>
      <div className="text-xl font-semibold tabular-nums text-ink-900">
        {value}
      </div>
    </div>
  );
}
