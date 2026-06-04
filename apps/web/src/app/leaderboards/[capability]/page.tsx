import Link from "next/link";
import { notFound } from "next/navigation";

import { fetchLeaderboard } from "@/lib/api";
import {
  benchmarkStatusLabel,
  benchmarkStatusTagClass,
  credibilityLabel,
  credibilityTagClass,
  routableTagClass,
  routableTagLabel,
  verificationStatusLabel,
  verificationStatusTagClass,
  verificationStatusTooltip,
} from "@/lib/tags";
import { formatLabel } from "@/lib/format";

type Params = Promise<{ capability: string }>;

const formatNumber = (value: number, fractionDigits = 2): string =>
  Number.isFinite(value) ? value.toFixed(fractionDigits) : "—";

const formatPercent = (value: number): string =>
  Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";

const formatLatency = (ms: number): string => {
  if (!Number.isFinite(ms) || ms <= 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
};

const formatCost = (usd: number): string => {
  if (!Number.isFinite(usd) || usd <= 0) return "$0.00";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(3)}`;
};

const formatTimestamp = (value: string): string => {
  if (!value) return "—";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  return new Date(parsed).toLocaleString();
};

export default async function LeaderboardCapabilityPage({
  params,
}: {
  params: Params;
}) {
  const { capability } = await params;
  let board;
  try {
    board = await fetchLeaderboard(capability);
  } catch (error) {
    if ((error as { status?: number }).status === 404) {
      notFound();
    }
    throw error;
  }

  const tested = board.entries.filter((entry) => entry.sample_size > 0);
  const untested = board.entries.filter((entry) => entry.sample_size === 0);
  const credibility = board.credibility;

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/leaderboards"
          className="text-xs text-ink-400 hover:text-ink-900"
        >
          &larr; All leaderboards
        </Link>
        <h1 className="mt-2 text-2xl font-semibold text-ink-900">
          {formatLabel(board.capability)}
        </h1>
        <p className="mt-1 text-sm text-ink-600">
          Cluster:{" "}
          <Link
            href={`/categories/${board.cluster_id}`}
            className="text-accent-600 hover:underline"
          >
            {board.cluster_display_name}
          </Link>{" "}
          &middot; capability id <code>{board.capability}</code>
        </p>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="tag-neutral">
            {board.total_providers} providers declare this capability
          </span>
          <span className="tag-success">
            {board.benchmark_passed} benchmark-passed
          </span>
          <span className="tag-info">{board.routable_today} runnable today</span>
          <span
            className={credibilityTagClass(credibility.status)}
            title="See banner below for the full credibility verdict"
          >
            Credibility: {credibilityLabel(credibility.status)}
          </span>
        </div>
      </div>

      {credibility.status !== "publishable" && (
        <section
          className={
            credibility.status === "developing"
              ? "rounded-lg border border-accent-200 bg-accent-50 p-4 text-sm text-ink-800"
              : "rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-ink-800"
          }
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-ink-900">
                Credibility status: {credibilityLabel(credibility.status)}
              </div>
              <p className="mt-1 max-w-3xl text-xs text-ink-600">
                We do not consider this leaderboard ready to publish externally
                yet. Treat the numbers below as an internal smoke test, not a
                vendor comparison. The list under <em>Unblockers</em> is what
                would change that.
              </p>
            </div>
            <span className={credibilityTagClass(credibility.status)}>
              {credibility.real_provider_count} of{" "}
              {credibility.total_provider_count} on real adapters
            </span>
          </div>
          {credibility.reasons.length > 0 && (
            <div className="mt-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                Why
              </div>
              <ul className="mt-1 list-disc pl-5 text-xs text-ink-700">
                {credibility.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
          {credibility.unblockers.length > 0 && (
            <div className="mt-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                Unblockers
              </div>
              <ul className="mt-1 list-disc pl-5 text-xs text-ink-700">
                {credibility.unblockers.map((unblocker) => (
                  <li key={unblocker}>{unblocker}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {tested.length > 0 ? (
        <section className="surface overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-500">
              <tr>
                <th className="px-4 py-3 text-left">Rank</th>
                <th className="px-4 py-3 text-left">Provider</th>
                <th className="px-4 py-3 text-right">Composite</th>
                <th className="px-4 py-3 text-right">Success</th>
                <th className="px-4 py-3 text-right">Quality</th>
                <th className="px-4 py-3 text-right">p50</th>
                <th className="px-4 py-3 text-right">p95</th>
                <th className="px-4 py-3 text-right">Avg cost</th>
                <th className="px-4 py-3 text-right">Samples</th>
                <th className="px-4 py-3 text-left">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {tested.map((entry) => (
                <tr key={entry.provider_id} className="hover:bg-ink-50">
                  <td className="px-4 py-3 align-top text-ink-400 tabular-nums">
                    #{entry.rank}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <Link
                      href={`/agents/${entry.provider_id}`}
                      className="text-sm font-semibold text-ink-900 hover:text-accent-600"
                    >
                      {entry.display_name}
                    </Link>
                    <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-ink-400">
                      <code>{entry.provider_id}</code>
                      <span className="tag-neutral">{entry.provider_type}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      <span
                        className={verificationStatusTagClass(
                          entry.verification_status,
                        )}
                        title={verificationStatusTooltip(entry.verification_status)}
                      >
                        {verificationStatusLabel(entry.verification_status)}
                      </span>
                      <span className={routableTagClass(!entry.routable_today)}>
                        {routableTagLabel(!entry.routable_today)}
                      </span>
                      {entry.source === "synthetic" && (
                        <span className="tag-neutral">synthetic</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right align-top tabular-nums text-ink-800">
                    {formatNumber(entry.composite_score, 3)}
                  </td>
                  <td className="px-4 py-3 text-right align-top tabular-nums text-ink-800">
                    {formatPercent(entry.success_rate)}
                  </td>
                  <td className="px-4 py-3 text-right align-top tabular-nums text-ink-800">
                    {formatNumber(entry.avg_quality_score, 2)}
                  </td>
                  <td className="px-4 py-3 text-right align-top tabular-nums text-ink-700">
                    {formatLatency(entry.p50_latency_ms)}
                  </td>
                  <td className="px-4 py-3 text-right align-top tabular-nums text-ink-700">
                    {formatLatency(entry.p95_latency_ms)}
                  </td>
                  <td className="px-4 py-3 text-right align-top tabular-nums text-ink-700">
                    {formatCost(entry.avg_cost_usd)}
                  </td>
                  <td className="px-4 py-3 text-right align-top tabular-nums text-ink-500">
                    {entry.sample_size}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <span
                      className={benchmarkStatusTagClass(entry.benchmark_status)}
                    >
                      {benchmarkStatusLabel(entry.benchmark_status)}
                    </span>
                    <div className="mt-1 text-xs text-ink-400">
                      {formatTimestamp(entry.last_run_at)}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : (
        <div className="surface p-6 text-sm text-ink-600">
          No benchmark runs yet for this capability. Run{" "}
          <code>make benchmark-schedule</code> to populate rankings.
        </div>
      )}

      {untested.length > 0 && (
        <section className="space-y-3">
          <div>
            <h2 className="text-sm font-semibold text-ink-900">
              Discovered but not yet benchmarked
            </h2>
            <p className="text-xs text-ink-400">
              These providers declare <code>{board.capability}</code> but have
              not been measured. They are listed here so the leaderboard does
              not silently hide unmeasured options.
            </p>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            {untested.map((entry) => (
              <Link
                key={entry.provider_id}
                href={`/agents/${entry.provider_id}`}
                className="surface flex items-start justify-between gap-3 p-3 transition hover:border-ink-400"
              >
                <div>
                  <div className="text-sm font-semibold text-ink-900">
                    {entry.display_name}
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-xs text-ink-400">
                    <code>{entry.provider_id}</code>
                    <span className="tag-neutral">{entry.provider_type}</span>
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1 text-xs">
                  <span
                    className={verificationStatusTagClass(
                      entry.verification_status,
                    )}
                    title={verificationStatusTooltip(entry.verification_status)}
                  >
                    {verificationStatusLabel(entry.verification_status)}
                  </span>
                  <span className="tag-neutral">not measured</span>
                </div>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
