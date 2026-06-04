"use client";

/**
 * LiveEvidenceStrip — homepage "evidence is alive" strip
 *
 * sprint-pitch-align Phase 4 / P4-1. Hits `/health/evidence` (which is
 * server-side TTL-cached for 60s) and renders a four-tile strip with
 * the live counts of:
 *
 *   1. Benchmark runs (total + last-24h delta)
 *   2. Verification records (total + last-7d delta)
 *   3. Discovery scout runs in the last 24h
 *   4. Capability demand events in the last 24h
 *
 * Renders even when every count is zero or the API is unreachable —
 * either case is information, not an error. A subtle "checked at"
 * timestamp lives at the bottom so a DD reviewer can tell at a glance
 * that the data is fresh.
 *
 * Designed to be the visible proof of the deck claim "evidence-first
 * — every claim on the homepage is backed by a row in Postgres". When
 * tiles say "0", that is itself an honest signal we have to fix
 * (which is exactly what sprint-pitch-align Phase 3 cron jobs solve).
 */

import { useEffect, useState, type ReactElement } from "react";

import { type EvidenceHealth, fetchEvidenceHealth } from "@/lib/api";

type FetchState =
  | { status: "loading" }
  | { status: "ok"; data: EvidenceHealth }
  | { status: "error"; message: string };

const formatNumber = (value: number): string =>
  Intl.NumberFormat("en-US", { notation: "compact" }).format(value);

const formatTimestamp = (iso: string): string => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
};

type TileProps = {
  label: string;
  value: string;
  delta: string;
  /** Optional href so a curious DD reviewer can click through. */
  href?: string;
};

function Tile({ label, value, delta, href }: TileProps): ReactElement {
  const inner = (
    <div className="rounded-lg border border-ink-200 bg-white p-4 transition hover:border-ink-400">
      <div className="text-xs uppercase tracking-wide text-ink-400">
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold text-ink-900">{value}</div>
      <div className="mt-1 text-xs text-ink-500">{delta}</div>
    </div>
  );
  if (href) {
    return (
      <a href={href} className="block focus:outline-none">
        {inner}
      </a>
    );
  }
  return inner;
}

export function LiveEvidenceStrip(): ReactElement {
  const [state, setState] = useState<FetchState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchEvidenceHealth()
      .then((data) => {
        if (!cancelled) setState({ status: "ok", data });
      })
      .catch((err) => {
        if (cancelled) return;
        const message =
          typeof err === "object" && err && "detail" in err
            ? String((err as { detail: unknown }).detail)
            : String(err);
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") {
    return (
      <section
        data-testid="live-evidence-strip"
        data-state="loading"
        className="rounded-xl border border-ink-200 bg-ink-50 p-5"
      >
        <header className="mb-3 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-ink-900">Live evidence</h2>
            <p className="text-xs text-ink-500">
              Real counts of benchmark runs, verifications, and scout
              activity in the public Postgres store.
            </p>
          </div>
        </header>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="h-24 animate-pulse rounded-lg border border-ink-200 bg-white/60"
            />
          ))}
        </div>
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section
        data-testid="live-evidence-strip"
        data-state="error"
        className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
      >
        <div className="font-semibold">Live evidence unavailable</div>
        <div className="mt-1 text-xs">
          /health/evidence did not respond: {state.message}. The homepage
          stays renderable; tables of record are still authoritative.
        </div>
      </section>
    );
  }

  const data = state.data;
  // When /health/evidence reports the DB is unreachable, every count
  // below is a fall-back zero — NOT an authoritative reading. Render
  // a distinct state so operators don't confuse "system alive but
  // empty" with "system down". Pre-2026-05-19 the strip happily
  // rendered all-zeros with no such signal, which cost the operator
  // ~15 minutes diagnosing whether the DB had been cleaned up.
  const dbReachable = data.db_reachable !== false;
  if (!dbReachable) {
    return (
      <section
        data-testid="live-evidence-strip"
        data-state="db-unreachable"
        className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900"
      >
        <div className="font-semibold">Live evidence — database unreachable</div>
        <div className="mt-1 text-xs">
          /health/evidence answered, but the underlying counts query
          failed: {data.db_error ?? "no error detail"}. The counts above
          would all read zero; that does not mean the data was wiped.
          Start the Postgres container (e.g. <code>make infra-up</code>)
          and refresh.
        </div>
        <div className="mt-2 text-[11px] text-rose-700">
          checked {formatTimestamp(data.checked_at)}
        </div>
      </section>
    );
  }
  return (
    <section
      data-testid="live-evidence-strip"
      data-state="ok"
      className="rounded-xl border border-ink-200 bg-ink-50 p-5"
    >
      <header className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">Live evidence</h2>
          <p className="text-xs text-ink-500">
            Every count below comes from a Postgres row, not a slide.
          </p>
        </div>
        <div className="text-[11px] text-ink-400">
          checked {formatTimestamp(data.checked_at)}
        </div>
      </header>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile
          label="Benchmark runs"
          value={formatNumber(data.benchmark_runs_total)}
          delta={`${formatNumber(data.benchmark_runs_24h)} in last 24h`}
          href="/leaderboards"
        />
        <Tile
          label="Verifications"
          value={formatNumber(data.verification_records_total)}
          delta={`${formatNumber(data.verification_records_7d)} in last 7d`}
          href="/discovery-gaps"
        />
        <Tile
          label="Scout runs · 24h"
          value={formatNumber(data.discovery_run_events_24h)}
          delta={`${formatNumber(data.route_status_routable_count)} cell${data.route_status_routable_count === 1 ? "" : "s"} routable (30d)`}
          href="/leaderboards"
        />
        <Tile
          label="Demand events · 24h"
          value={formatNumber(data.capability_demand_events_24h)}
          delta={`${formatNumber(data.discovery_gap_events_24h)} gap events 24h`}
          href="/open-mcp-opportunities"
        />
      </div>
    </section>
  );
}
