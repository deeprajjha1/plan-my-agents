/**
 * Sprint-6 / A — internal demand cockpit.
 *
 * Combines four signals on one page so the founder can prep for a
 * design-partner call in under five minutes:
 *   1. Live evidence-health counters (same numbers as the homepage strip)
 *   2. Top capabilities by request (last 30d)
 *   3. Routable gaps (capabilities with demand AND a supply gap)
 *   4. Recent verifications + recent benchmark runs (proves the cron is alive)
 *
 * This page deliberately does NOT call any `/admin/*` or auth-gated
 * endpoint — it consumes the same public reads as the rest of the site.
 * That keeps it stable across auth/Clerk churn and lets a partner
 * shoulder-surf without surprise. When we add real admin actions
 * (force-rerun a benchmark, demote a candidate) they live behind
 * `/account` and the auth flow, not here.
 */

import Link from "next/link";

import {
  fetchDemandGaps,
  fetchDemandTopCapabilities,
  fetchEvidenceHealth,
  fetchRecentVerifications,
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

export default async function InternalDemandCockpitPage() {
  let health: Awaited<ReturnType<typeof fetchEvidenceHealth>> | null = null;
  let top: Awaited<ReturnType<typeof fetchDemandTopCapabilities>> | null = null;
  let gaps: Awaited<ReturnType<typeof fetchDemandGaps>> | null = null;
  let verifications: Awaited<
    ReturnType<typeof fetchRecentVerifications>
  > | null = null;
  let loadError: string | null = null;

  try {
    [health, top, gaps, verifications] = await Promise.all([
      fetchEvidenceHealth(),
      fetchDemandTopCapabilities(50, 30),
      fetchDemandGaps(50, 30),
      fetchRecentVerifications(10),
    ]);
  } catch (error) {
    loadError =
      (error as { detail?: string }).detail ?? String(error) ?? "fetch failed";
  }

  if (loadError) {
    return (
      <div className="surface p-8">
        <h1 className="text-xl font-semibold text-ink-900">
          Internal cockpit not reachable
        </h1>
        <p className="mt-2 text-sm text-ink-600">
          Start the FastAPI app and refresh.
        </p>
        <p className="mt-3 text-xs text-ink-400">Error: {loadError}</p>
      </div>
    );
  }

  const topRows = top?.entries ?? [];
  const gapRows = gaps?.entries ?? [];
  const verificationRows = verifications?.records ?? [];

  return (
    <div
      className="space-y-8"
      data-testid="dev-demand-cockpit"
      data-state="ok"
    >
      <section className="surface-muted p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-xl font-semibold text-ink-900">
            Demand cockpit
          </h1>
          <p className="text-xs text-ink-500">
            Internal page — same data the public surfaces consume, all
            in one view. No auth gate.
          </p>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4 lg:grid-cols-7">
          <Stat
            label="Benchmark runs (24h)"
            value={health?.benchmark_runs_24h ?? 0}
          />
          <Stat
            label="Routable cells (30d)"
            value={health?.route_status_routable_count ?? 0}
            highlight
          />
          <Stat
            label="Verifications (7d)"
            value={health?.verification_records_7d ?? 0}
          />
          <Stat
            label="Discovery runs (24h)"
            value={health?.discovery_run_events_24h ?? 0}
          />
          <Stat
            label="Demand events (24h)"
            value={health?.capability_demand_events_24h ?? 0}
            highlight
          />
          <Stat
            label="Gap events (24h)"
            value={health?.discovery_gap_events_24h ?? 0}
          />
          <Stat
            label="Benchmark runs (all-time)"
            value={health?.benchmark_runs_total ?? 0}
          />
        </div>
        <p className="mt-3 text-xs text-ink-500">
          Checked at{" "}
          <code>{formatTimestamp(health?.checked_at ?? "")}</code> · live
          source: <code>GET /health/evidence</code>
        </p>
      </section>

      <section>
        <header className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold text-ink-900">
            Top capabilities by request count (30d)
          </h2>
          <a
            href="/demand"
            className="text-xs text-accent-600 hover:underline"
          >
            Public surface ↗
          </a>
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
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {topRows.map((row: DemandTopCapabilityEntry) => (
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
                      {row.request_count}
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums text-ink-700">
                      {row.distinct_requester_count}
                    </td>
                    <td className="px-3 py-2 align-top text-xs">
                      {row.routable_today ? (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-emerald-700">
                          {row.routable_provider_ids.join(", ") || "Yes"}
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
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <header className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold text-ink-900">
            Routable gaps (zero-yield desc, then demand desc)
          </h2>
          <Link
            href="/discovery-gaps"
            className="text-xs text-accent-600 hover:underline"
          >
            Public drill-down ↗
          </Link>
        </header>
        {gapRows.length === 0 ? (
          <p className="surface mt-3 p-4 text-sm text-ink-500">
            No demand-with-supply-gap capabilities in the window.
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
                  <th className="px-3 py-2 text-left">Sample goal</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {gapRows.map((row: DemandGapEntry) => (
                  <tr key={row.capability_id} className="bg-white">
                    <td className="px-3 py-2 align-top">
                      <div className="font-medium text-ink-900">
                        {formatLabel(row.capability_id)}
                      </div>
                      <code className="text-xs text-ink-400">
                        {row.capability_id}
                      </code>
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums text-ink-700">
                      {row.request_count}
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums text-ink-700">
                      {row.discovery_attempts}
                    </td>
                    <td className="px-3 py-2 text-right align-top tabular-nums font-semibold text-ink-900">
                      {row.zero_yield_count}
                    </td>
                    <td className="px-3 py-2 align-top text-xs italic text-ink-500">
                      {row.sample_goals.length > 0
                        ? `"${row.sample_goals[0]}"`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <header className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold text-ink-900">
            Recent verifications
          </h2>
          <code className="text-xs text-ink-400">
            GET /evidence/recent-verifications
          </code>
        </header>
        {verificationRows.length === 0 ? (
          <p className="surface mt-3 p-4 text-sm text-ink-500">
            No verification records yet — run{" "}
            <code>make verify-top-candidates</code> or wait for the
            verify cron.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto rounded-md border border-ink-200">
            <table className="min-w-full divide-y divide-ink-200 text-sm">
              <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-400">
                <tr>
                    <th className="px-3 py-2 text-left">Provider</th>
                    <th className="px-3 py-2 text-left">Status</th>
                    <th className="px-3 py-2 text-left">Blockers</th>
                    <th className="px-3 py-2 text-left">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {verificationRows.map((row, idx) => (
                  <tr key={`${row.provider_id}-${idx}`} className="bg-white">
                    <td className="px-3 py-2 align-top">
                      <code className="text-xs text-ink-700">
                        {row.provider_id}
                      </code>
                    </td>
                    <td className="px-3 py-2 align-top text-xs">
                      <span
                        className={
                          row.status === "capability_verified"
                            ? "rounded-full bg-emerald-100 px-2 py-0.5 text-emerald-700"
                            : row.status === "known_provider"
                              ? "rounded-full bg-sky-100 px-2 py-0.5 text-sky-700"
                              : "rounded-full bg-ink-100 px-2 py-0.5 text-ink-700"
                        }
                      >
                        {row.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 align-top text-xs text-ink-600">
                      {row.blockers.length > 0 ? row.blockers.join(", ") : "—"}
                    </td>
                    <td className="px-3 py-2 align-top text-xs text-ink-500">
                      {formatTimestamp(row.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="surface-muted p-5 text-xs text-ink-600">
        <h2 className="text-sm font-semibold text-ink-900">
          Operator playbook
        </h2>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            <strong>Export for a partner call:</strong>{" "}
            <code>make demand-snapshot</code> writes a stable CSV to{" "}
            <code>data/demand_snapshot.csv</code>. Override the window
            with <code>LOOKBACK_DAYS=7</code> or the output with{" "}
            <code>OUTPUT=…</code>.
          </li>
          <li>
            <strong>Refresh routable cells:</strong>{" "}
            <code>make benchmark-cron</code> re-runs every benchmark-
            gated capability;{" "}
            <code>make backfill-phase5-cells</code> lands the Resend +
            Firecrawl response-fixture rows that flip the cells from
            &ldquo;wired&rdquo; to &ldquo;routable&rdquo;.
          </li>
          <li>
            <strong>Add a new design partner:</strong> walk them through{" "}
            <code>docs/design-partner-onboarding.md</code>; the
            checklist closes the loop on{" "}
            <Link
              href="/goal"
              className="text-accent-600 hover:underline"
            >
              /goal
            </Link>{" "}
            → refusal → demand event → cockpit row.
          </li>
          <li>
            <strong>Public version of this page:</strong>{" "}
            <a
              href="/demand"
              className="text-accent-600 hover:underline"
            >
              /demand
            </a>{" "}
            (rate-limited; safe to share with vendors).
          </li>
        </ul>
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  highlight = false,
}: {
  label: string;
  value: number;
  highlight?: boolean;
}) {
  return (
    <div
      className={
        highlight
          ? "rounded-md border border-accent-200 bg-accent-50 px-4 py-2 text-right"
          : "rounded-md border border-ink-200 bg-white px-4 py-2 text-right"
      }
    >
      <div className="text-ink-400">{label}</div>
      <div className="text-xl font-semibold tabular-nums text-ink-900">
        {value}
      </div>
    </div>
  );
}
