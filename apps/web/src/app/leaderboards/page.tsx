import Link from "next/link";

import {
  fetchDiscoveryGaps,
  fetchLeaderboards,
  type DiscoveryGapsResponse,
} from "@/lib/api";
import { credibilityLabel, credibilityTagClass } from "@/lib/tags";
import { formatLabel } from "@/lib/format";

export default async function LeaderboardsIndexPage() {
  let data;
  let discoveryGaps: DiscoveryGapsResponse | null = null;
  try {
    data = await fetchLeaderboards();
  } catch (error) {
    return (
      <div className="surface p-8">
        <h1 className="text-xl font-semibold text-ink-900">
          Leaderboards are not reachable
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

  // Discovery gap signal renders independently. If the endpoint or
  // its underlying store is unreachable we silently degrade to no
  // tile rather than failing the whole leaderboards page — the gap
  // surface is auxiliary, not load-bearing.
  try {
    discoveryGaps = await fetchDiscoveryGaps(6);
  } catch {
    discoveryGaps = null;
  }

  const groupedByCluster = new Map<
    string,
    {
      cluster_id: string;
      cluster_display_name: string;
      capabilities: typeof data.capabilities;
    }
  >();
  for (const entry of data.capabilities) {
    const existing = groupedByCluster.get(entry.cluster_id);
    if (existing) {
      existing.capabilities.push(entry);
    } else {
      groupedByCluster.set(entry.cluster_id, {
        cluster_id: entry.cluster_id,
        cluster_display_name: entry.cluster_display_name,
        capabilities: [entry],
      });
    }
  }
  const clusters = Array.from(groupedByCluster.values()).sort((a, b) =>
    a.cluster_display_name.localeCompare(b.cluster_display_name),
  );

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-semibold text-ink-900">
          Agent leaderboards by capability
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-ink-600">
          One ranked table per capability. Providers are scored on real benchmark
          runs against the PlanMyAgents case suite. Untested providers appear at
          the bottom of each leaderboard with a benchmark status of{" "}
          <span className="tag-neutral">not tested</span> &mdash; we never
          silently drop them.
        </p>
        <p className="mt-3 text-xs text-ink-400">
          Rows tagged <span className="tag-neutral">synthetic</span> are scored
          against mock adapters and should not be treated as real-world
          performance. Run <code>make benchmark-schedule</code> to add real
          adapter runs.
        </p>
      </section>

      <DiscoveryGapsTile gaps={discoveryGaps} />

      {clusters.length === 0 && (
        <div className="surface p-6 text-sm text-ink-600">
          No capabilities discovered yet. Run{" "}
          <code>make discovery-upkeep</code> to populate the discovery store.
        </div>
      )}

      <div className="space-y-8" id="leaderboards-by-cluster">
        {clusters.map((cluster) => (
          <section key={cluster.cluster_id} className="space-y-3">
            <div className="flex items-end justify-between">
              <div>
                <h2 className="text-lg font-semibold text-ink-900">
                  {cluster.cluster_display_name}
                </h2>
                <p className="text-xs text-ink-400">
                  {cluster.capabilities.length}{" "}
                  {cluster.capabilities.length === 1
                    ? "leaderboard"
                    : "leaderboards"}{" "}
                  &middot; cluster <code>{cluster.cluster_id}</code>
                </p>
              </div>
              <Link
                href={`/categories/${cluster.cluster_id}`}
                className="text-xs text-ink-400 hover:text-ink-900"
              >
                Browse cluster &rarr;
              </Link>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {cluster.capabilities.map((entry) => (
                <Link
                  key={entry.capability}
                  href={`/leaderboards/${encodeURIComponent(entry.capability)}`}
                  className="surface group p-4 transition hover:border-ink-400"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="text-sm font-semibold text-ink-900 group-hover:text-accent-600">
                        {formatLabel(entry.capability)}
                      </div>
                      <div className="mt-1 text-xs text-ink-400">
                        <code>{entry.capability}</code>
                      </div>
                      <div className="mt-2">
                        <span
                          className={credibilityTagClass(entry.credibility.status)}
                          title={
                            entry.credibility.reasons.join(" ") ||
                            "Credibility status"
                          }
                        >
                          {credibilityLabel(entry.credibility.status)}
                        </span>
                      </div>
                    </div>
                    <div className="text-right text-xl font-semibold tabular-nums text-ink-900">
                      {entry.provider_count}
                    </div>
                  </div>
                  <dl className="mt-3 grid grid-cols-3 gap-2 text-xs text-ink-600">
                    <div className="surface-muted px-3 py-2">
                      <dt className="text-ink-400">Bench-passed</dt>
                      <dd className="text-base font-semibold text-ink-800">
                        {entry.benchmark_passed}
                      </dd>
                    </div>
                    <div className="surface-muted px-3 py-2">
                      <dt className="text-ink-400">Runnable</dt>
                      <dd className="text-base font-semibold text-ink-800">
                        {entry.routable_today}
                      </dd>
                    </div>
                    <div className="surface-muted px-3 py-2">
                      <dt className="text-ink-400">Real runs</dt>
                      <dd className="text-base font-semibold text-ink-800">
                        {entry.has_real_adapter_runs ? "Yes" : "No"}
                      </dd>
                    </div>
                  </dl>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function DiscoveryGapsTile({
  gaps,
}: {
  gaps: DiscoveryGapsResponse | null;
}) {
  // Brutally-honest "the world hasn't built this yet" tile. Renders
  // the top capabilities ranked by how often a /goal request landed
  // on them with zero routable agents accepted by the judge. Empty
  // state is intentional and clearly worded — better than hiding the
  // tile and confusing operators about whether the feature is wired.
  if (!gaps) {
    return (
      <section className="surface space-y-2 border-amber-300 bg-amber-50/40 p-5">
        <h2 className="text-sm font-semibold text-amber-900">
          Capabilities the world hasn&apos;t built yet
        </h2>
        <p className="text-xs text-amber-800">
          Discovery gap signal endpoint not reachable. Restart{" "}
          <code>make api</code> after the latest migration.
        </p>
      </section>
    );
  }

  const top = gaps.capabilities.filter((row) => row.zero_yield_count > 0);

  if (top.length === 0) {
    return (
      <section className="surface space-y-2 p-5">
        <h2 className="text-sm font-semibold text-ink-900">
          Capabilities the world hasn&apos;t built yet
        </h2>
        <p className="text-xs text-ink-500">
          No zero-yield capabilities recorded yet. Each <code>/goal</code>{" "}
          request that surfaces zero routable agents for a missing
          capability adds one row to the gap log; come back after a few
          live runs.
        </p>
      </section>
    );
  }

  return (
    <section className="surface space-y-3 p-5">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">
            Capabilities the world hasn&apos;t built yet
          </h2>
          <p className="mt-1 text-xs text-ink-500">
            Top capabilities ranked by zero-yield <code>/goal</code>{" "}
            requests. Each row is a capability where users keep asking
            but our discovery + judge pass surfaces no routable agent.
          </p>
        </div>
        <a
          href="/discovery-gaps"
          className="text-xs text-ink-400 hover:text-ink-900"
        >
          View raw API &rarr;
        </a>
      </div>
      <ul className="grid gap-2 md:grid-cols-2">
        {top.map((row) => (
          <li
            key={row.capability_id}
            className="surface-muted space-y-1 px-3 py-2"
          >
            <div className="flex items-baseline justify-between gap-3">
              <code className="text-sm font-semibold text-ink-900">
                {row.capability_id}
              </code>
              <span className="text-base font-semibold tabular-nums text-amber-700">
                {row.zero_yield_count}
                <span className="ml-1 text-xs font-normal text-ink-400">
                  zero-yield
                </span>
              </span>
            </div>
            <div className="text-[11px] text-ink-400">
              {row.distinct_goal_count} distinct goal
              {row.distinct_goal_count === 1 ? "" : "s"} &middot;{" "}
              {row.observation_count} total observation
              {row.observation_count === 1 ? "" : "s"}
            </div>
            {row.sample_goals.length > 0 && (
              <div
                className="text-[11px] italic text-ink-500"
                title={row.sample_goals.join(" \u2022 ")}
              >
                &ldquo;{row.sample_goals[0]}&rdquo;
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
