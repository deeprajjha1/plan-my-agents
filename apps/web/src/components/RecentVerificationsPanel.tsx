"use client";

/**
 * RecentVerificationsPanel — proves the verifier is still running.
 *
 * sprint-pitch-align Phase 4 / P4-3. Lives on /discovery-gaps and
 * /open-mcp-opportunities and renders the most-recent N rows of
 * ``verification_records``. The pages are public proof-of-gap surfaces;
 * showing fresh evidence-pipeline activity beside them stops them
 * reading like a stale dump.
 *
 * Server-side endpoint is /evidence/recent-verifications (added in the
 * same sprint). Read-only and resilient — falls back to a quiet
 * "nothing recent" line on any failure so the gap surfaces stay
 * useful even if Postgres is unreachable.
 */

import { useEffect, useState, type ReactElement } from "react";

import {
  type RecentVerificationsResponse,
  fetchRecentVerifications,
} from "@/lib/api";

const statusTone = (status: string): string => {
  switch (status) {
    case "capability_verified":
      return "bg-emerald-50 text-emerald-800 border-emerald-200";
    case "known_provider":
      return "bg-sky-50 text-sky-800 border-sky-200";
    case "registered_in_directory":
      return "bg-indigo-50 text-indigo-800 border-indigo-200";
    case "unverified":
      return "bg-amber-50 text-amber-800 border-amber-200";
    default:
      return "bg-ink-50 text-ink-700 border-ink-200";
  }
};

const formatTimestamp = (iso: string): string => {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
};

type Props = {
  limit?: number;
  /** Why this panel is here on the page, in one sentence. */
  contextLine?: string;
};

export function RecentVerificationsPanel({
  limit = 5,
  contextLine,
}: Props): ReactElement {
  const [data, setData] = useState<RecentVerificationsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchRecentVerifications(limit)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err) => {
        if (cancelled) return;
        const message =
          typeof err === "object" && err && "detail" in err
            ? String((err as { detail: unknown }).detail)
            : String(err);
        setError(message);
      });
    return () => {
      cancelled = true;
    };
  }, [limit]);

  if (error) {
    return (
      <section
        data-testid="recent-verifications-panel"
        data-state="error"
        className="surface-muted p-5 text-xs text-ink-600"
      >
        <h2 className="text-sm font-semibold text-ink-900">
          Recent verifications
        </h2>
        <p className="mt-1">
          Verifier feed unavailable: {error}. Gap data above is still
          authoritative.
        </p>
      </section>
    );
  }

  if (!data) {
    return (
      <section
        data-testid="recent-verifications-panel"
        data-state="loading"
        className="surface-muted p-5"
      >
        <h2 className="text-sm font-semibold text-ink-900">
          Recent verifications
        </h2>
        <div className="mt-3 h-20 animate-pulse rounded bg-white/60" />
      </section>
    );
  }

  return (
    <section
      data-testid="recent-verifications-panel"
      data-state="ok"
      className="surface-muted space-y-3 p-5"
    >
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">
            Recent verifications
          </h2>
          {contextLine && (
            <p className="text-xs text-ink-500">{contextLine}</p>
          )}
        </div>
        <div className="text-[11px] text-ink-400">
          checked {formatTimestamp(data.checked_at)}
        </div>
      </header>
      {data.records.length === 0 ? (
        <p className="text-xs text-ink-500">
          No verification records yet. Populate via{" "}
          <code className="rounded bg-ink-100 px-1.5 py-0.5">
            make verify-top-candidates
          </code>
          .
        </p>
      ) : (
        <ul className="space-y-2 text-sm">
          {data.records.map((record, index) => (
            <li
              key={`${record.provider_id}-${record.created_at}-${index}`}
              className="rounded-md border border-ink-200 bg-white px-3 py-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs text-ink-800">
                  {record.provider_id}
                </span>
                <span
                  className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${statusTone(record.status)}`}
                >
                  {record.status}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-ink-500">
                <span>{formatTimestamp(record.created_at)}</span>
                {record.verified_capabilities.length > 0 && (
                  <span>
                    Verified:{" "}
                    <span className="font-mono">
                      {record.verified_capabilities.slice(0, 3).join(", ")}
                    </span>
                  </span>
                )}
                {record.blockers.length > 0 && (
                  <span>
                    Blocker:{" "}
                    <span className="text-amber-700">
                      {record.blockers[0]}
                    </span>
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
