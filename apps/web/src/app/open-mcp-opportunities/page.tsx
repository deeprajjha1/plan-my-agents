/**
 * Open MCP opportunities — public 'gap as signal' surface.
 *
 * For every capability that no MCP / A2A / AI-agent in our index covers,
 * we list whichever of the following the backend found:
 *   - vendors that DO expose an OpenAPI spec for it (`api_provider`
 *     rows from APIs.guru and similar sources — wrappable today), AND
 *   - the demand signal (how many real /goal requests asked for it).
 *
 * Capabilities with demand but NO matching OpenAPI spec are deliberately
 * included with `api_supply_count=0` — those are the deepest gaps and
 * carry the strongest "build this" signal even though no wrapper-ready
 * API exists yet. The page calls them out with a "Deepest gap" callout
 * instead of the API table.
 *
 * The page is intentionally honest about the methodology and never
 * implies PlanMyAgents will ship the wrappers — it's a public list of
 * opportunities, not a roadmap.
 */

import {
  fetchOpenMcpOpportunities,
  type OpenMcpOpportunityCapability,
} from "@/lib/api";
import { RecentVerificationsPanel } from "@/components/RecentVerificationsPanel";
import { formatLabel } from "@/lib/format";

const hostFromUrl = (raw: string): string => {
  if (!raw) return "";
  try {
    return new URL(raw).host;
  } catch {
    return raw;
  }
};

export default async function OpenMcpOpportunitiesPage() {
  let data;
  try {
    data = await fetchOpenMcpOpportunities();
  } catch (error) {
    return (
      <div className="surface p-8">
        <h1 className="text-xl font-semibold text-ink-900">
          Open MCP opportunities are not reachable
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

  const opportunities = data.capabilities;
  const hasOpportunities = opportunities.length > 0;
  const totalAcrossAllCapabilities = opportunities.reduce(
    (sum: number, capability: OpenMcpOpportunityCapability) =>
      sum + capability.api_supply_count,
    0,
  );
  const totalDemand = opportunities.reduce(
    (sum: number, capability: OpenMcpOpportunityCapability) =>
      sum + capability.demand_request_count,
    0,
  );

  return (
    <div className="space-y-8">
      <section>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-ink-900">
              Open MCP opportunities
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-ink-600">
              Capabilities where{" "}
              <strong>nobody has shipped an MCP server, A2A agent, or AI agent</strong>{" "}
              yet — either because the wrappable APIs exist and no one has
              wrapped them, or because real users have asked for the
              capability and no vendor has built it at all.
            </p>
            <p className="mt-2 max-w-3xl text-xs text-ink-400">
              These are concrete integration gaps any developer or vendor
              could close. Rows tagged <strong>Deepest gap</strong> have
              demand but no OpenAPI spec in our index — the strongest
              build-this signal. PlanMyAgents does not ship wrappers;
              this page is a public list of opportunities, ranked by
              combined demand and supply.
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div className="surface-muted px-4 py-2 text-right">
              <div className="text-ink-400">Capabilities</div>
              <div className="text-xl font-semibold tabular-nums text-ink-900">
                {opportunities.length}
              </div>
            </div>
            <div className="surface-muted px-4 py-2 text-right">
              <div className="text-ink-400">APIs without agents</div>
              <div className="text-xl font-semibold tabular-nums text-ink-900">
                {totalAcrossAllCapabilities}
              </div>
            </div>
            <div className="surface-muted px-4 py-2 text-right">
              <div className="text-ink-400">Demand events</div>
              <div className="text-xl font-semibold tabular-nums text-ink-900">
                {totalDemand}
              </div>
            </div>
          </div>
        </div>
      </section>

      {!hasOpportunities && (
        <div className="surface p-6 text-sm text-ink-600">
          <p>No open opportunities right now. That can mean either:</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            <li>The discovery store has no <code>api_provider</code> rows yet — run <code>make discovery-refresh</code>.</li>
            <li>Every capability our APIs cover already has at least one MCP / A2A / AI-agent in the index.</li>
          </ul>
        </div>
      )}

      <div className="space-y-5">
        {opportunities.map((capability: OpenMcpOpportunityCapability) => (
          <section
            key={capability.capability_id}
            className="surface space-y-3 p-5"
          >
            <header className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-ink-900">
                  {formatLabel(capability.capability_id)}
                </h2>
                <div className="mt-1 text-xs text-ink-400">
                  <code>{capability.capability_id}</code>
                </div>
                {capability.sample_demand_goals.length > 0 && (
                  <ul className="mt-2 max-w-2xl space-y-1 text-xs italic text-ink-500">
                    {capability.sample_demand_goals.map((goal: string) => (
                      <li key={goal}>&ldquo;{goal}&rdquo;</li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="grid grid-cols-3 gap-2 text-xs">
                <Stat label="APIs" value={capability.api_supply_count} />
                <Stat
                  label="Requests"
                  value={capability.demand_request_count}
                />
                <Stat
                  label="Score"
                  value={Math.round(capability.score * 10) / 10}
                />
              </div>
            </header>

            {capability.apis.length > 0 ? (
              <div className="overflow-x-auto rounded-md border border-ink-200">
                <table className="min-w-full divide-y divide-ink-200 text-sm">
                  <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-400">
                    <tr>
                      <th className="px-3 py-2 text-left">Vendor</th>
                      <th className="px-3 py-2 text-left">Display name</th>
                      <th className="px-3 py-2 text-left">OpenAPI spec</th>
                      <th className="px-3 py-2 text-left">Other capabilities</th>
                      <th className="px-3 py-2 text-left">Discovered via</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-ink-100">
                    {capability.apis.map((api) => (
                      <tr key={api.provider_id} className="bg-white">
                        <td className="px-3 py-2 align-top font-medium text-ink-900">
                          {api.vendor || "—"}
                        </td>
                        <td className="px-3 py-2 align-top text-ink-700">
                          {api.display_name}
                        </td>
                        <td className="px-3 py-2 align-top">
                          {api.openapi_url ? (
                            <a
                              href={api.openapi_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-accent-600 hover:underline"
                            >
                              {hostFromUrl(api.openapi_url)}
                            </a>
                          ) : (
                            <span className="text-ink-400">—</span>
                          )}
                        </td>
                        <td className="px-3 py-2 align-top text-xs text-ink-500">
                          {api.capabilities
                            .filter((c) => c !== capability.capability_id)
                            .slice(0, 3)
                            .join(", ") || "—"}
                        </td>
                        <td className="px-3 py-2 align-top text-xs text-ink-500">
                          {api.source}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-ink-300 bg-ink-50 px-4 py-3 text-sm text-ink-600">
                <strong>Deepest gap:</strong> users have asked for this
                capability but no vendor has even an OpenAPI spec for it
                in our index.
              </div>
            )}
          </section>
        ))}
      </div>

      <RecentVerificationsPanel
        limit={5}
        contextLine="Latest discovered providers we re-verified — proves the trust ladder keeps moving."
      />

      <section className="surface-muted space-y-2 p-5 text-xs text-ink-600">
        <h2 className="text-sm font-semibold text-ink-900">Methodology</h2>
        <p>{data.methodology}</p>
        <p>
          Demand counts come from real <code>/goal</code> requests where the
          planner detected this capability as missing. Goal text is
          truncated and PII-stripped before storage. Requester identifiers
          (when present) are hashed.
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
