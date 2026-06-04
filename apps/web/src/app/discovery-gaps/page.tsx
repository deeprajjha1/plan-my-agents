/**
 * Discovery gaps — public 'we tried and surfaced nothing routable' surface.
 *
 * Companion to /open-mcp-opportunities:
 *
 *   /open-mcp-opportunities surfaces "an OpenAPI spec exists but no MCP /
 *   A2A / AI agent wraps it" — i.e. capabilities where the API supply
 *   exists but the agent supply is missing.
 *
 *   /discovery-gaps (this page) surfaces "we ran the full scout +
 *   judge pass for this capability and surfaced ZERO routable agents",
 *   regardless of whether an underlying API exists. The leaderboard sort
 *   key is `zero_yield_count` desc so the top of the list is the
 *   capabilities the world keeps asking for and we keep failing to
 *   deliver — exactly the signal a new vendor wants to see before they
 *   decide what to ship.
 *
 * The /goal refusal flow links here from the planning-refusal card
 * (see PlanningRefusalCard), so this route MUST exist or users hit a
 * 404 the moment a request fails. Audit-flagged regression: the route
 * was missing for several weeks while the backend endpoint and the
 * inbound link were already in place.
 */

import Link from "next/link";

import { fetchDiscoveryGaps, type DiscoveryGapCapability } from "@/lib/api";
import { RecentVerificationsPanel } from "@/components/RecentVerificationsPanel";
import { formatLabel } from "@/lib/format";

const formatTimestamp = (raw: string): string => {
  if (!raw) return "—";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toISOString().replace("T", " ").slice(0, 16) + " UTC";
};

export default async function DiscoveryGapsPage() {
  let data;
  try {
    // The page is the public leaderboard; pull a generous window so
    // the long tail is also visible. The /discovery-gaps endpoint
    // caps at 200 server-side.
    data = await fetchDiscoveryGaps(200);
  } catch (error) {
    return (
      <div className="surface p-8">
        <h1 className="text-xl font-semibold text-ink-900">
          Discovery gaps are not reachable
        </h1>
        <p className="mt-2 text-sm text-ink-600">
          Start the FastAPI app and refresh this page:
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-ink-900 px-4 py-3 text-xs text-ink-50">
          make api
        </pre>
        <p className="mt-3 text-xs text-ink-400">
          Error: {(error as { detail?: string }).detail ?? String(error)}
        </p>
      </div>
    );
  }

  const rows = data.capabilities;
  const hasRows = rows.length > 0;
  const totalObservations = rows.reduce(
    (sum: number, row: DiscoveryGapCapability) => sum + row.observation_count,
    0,
  );
  const totalZeroYield = rows.reduce(
    (sum: number, row: DiscoveryGapCapability) => sum + row.zero_yield_count,
    0,
  );

  return (
    <div className="space-y-8">
      <section>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-ink-900">
              Discovery gaps
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-ink-600">
              Capabilities that real <code>/goal</code> requests have
              asked for but the discovery + judge pipeline could not find
              a routable agent for. Each row is a per-capability rollup
              of post-discovery outcomes — sorted with the brutally-honest{" "}
              <strong>zero-yield first</strong> ordering.
            </p>
            <p className="mt-2 max-w-3xl text-xs text-ink-400">
              Companion to{" "}
              <Link
                href="/open-mcp-opportunities"
                className="text-accent-600 hover:underline"
              >
                Open MCP opportunities
              </Link>
              : that page lists capabilities where an underlying OpenAPI
              spec exists but nobody has wrapped it; this page lists
              capabilities where we couldn&apos;t even surface a vendor
              candidate. PlanMyAgents does not ship wrappers; this is a
              public list of demand the agent ecosystem isn&apos;t yet
              meeting.
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <Stat label="Capabilities" value={data.total_capabilities} />
            <Stat label="Observations" value={totalObservations} />
            <Stat label="Zero-yield" value={totalZeroYield} />
          </div>
        </div>
      </section>

      {!hasRows && (
        <div className="surface p-6 text-sm text-ink-600">
          <p>No discovery gaps recorded yet. That can mean either:</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            <li>
              No <code>/goal</code> request has hit a planning refusal yet
              — the gap log only fills when a real request asks for a
              capability the discovery + judge pass cannot satisfy.
            </li>
            <li>
              Recording is disabled — check{" "}
              <code>PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED</code>.
            </li>
          </ul>
        </div>
      )}

      <div className="overflow-x-auto rounded-md border border-ink-200">
        <table className="min-w-full divide-y divide-ink-200 text-sm">
          <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-400">
            <tr>
              <th className="px-3 py-2 text-left">Capability</th>
              <th className="px-3 py-2 text-right">Zero-yield</th>
              <th className="px-3 py-2 text-right">Observations</th>
              <th className="px-3 py-2 text-right">Distinct goals</th>
              <th className="px-3 py-2 text-left">Last observed</th>
              <th className="px-3 py-2 text-left">Recent goals</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {rows.map((row: DiscoveryGapCapability) => (
              <tr key={row.capability_id} className="bg-white">
                <td className="px-3 py-2 align-top">
                  <div className="font-medium text-ink-900">
                    {formatLabel(row.capability_id)}
                  </div>
                  <code className="text-xs text-ink-400">
                    {row.capability_id}
                  </code>
                </td>
                <td className="px-3 py-2 text-right align-top tabular-nums font-semibold text-ink-900">
                  {row.zero_yield_count}
                </td>
                <td className="px-3 py-2 text-right align-top tabular-nums text-ink-700">
                  {row.observation_count}
                </td>
                <td className="px-3 py-2 text-right align-top tabular-nums text-ink-700">
                  {row.distinct_goal_count}
                </td>
                <td className="px-3 py-2 align-top text-xs text-ink-500">
                  {formatTimestamp(row.last_observed_at)}
                </td>
                <td className="px-3 py-2 align-top text-xs italic text-ink-500">
                  {row.sample_goals.length > 0 ? (
                    <ul className="space-y-1">
                      {row.sample_goals.slice(0, 3).map((goal: string) => (
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

      <RecentVerificationsPanel
        limit={5}
        contextLine="Latest discovered providers we re-verified — proves the trust ladder keeps moving."
      />

      <section className="surface-muted space-y-2 p-5 text-xs text-ink-600">
        <h2 className="text-sm font-semibold text-ink-900">Methodology</h2>
        <p>
          <strong>Observation</strong>: every <code>/goal</code> request
          that decomposes into a capability the planner cannot satisfy
          increments one observation per missing capability. Goal text is
          truncated and PII-stripped before storage.
        </p>
        <p>
          <strong>Zero-yield</strong>: a subset of observations where the
          downstream scout + judge pass surfaced zero accepted candidates
          for the capability. A high zero-yield count is the strongest
          signal that the agent supply for a capability is missing
          entirely (not just unwrapped). Sort key for this leaderboard.
        </p>
        <p>
          <strong>Distinct goals</strong>: number of distinct goal hashes
          that asked for the capability, so a single repeat-asker
          can&apos;t inflate the rankings.
        </p>
        <p>
          Raw API: <code>GET /discovery-gaps?limit=200</code> — same data
          as this page, JSON shape.
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
