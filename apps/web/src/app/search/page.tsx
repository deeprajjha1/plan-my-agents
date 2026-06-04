import Link from "next/link";

import { search } from "@/lib/api";
import {
  benchmarkStatusLabel,
  benchmarkStatusTagClass,
  routableTagClass,
  routableTagLabel,
  verificationStatusLabel,
  verificationStatusTagClass,
  verificationStatusTooltip,
} from "@/lib/tags";

type SearchParams = Promise<{
  q?: string;
  capability?: string;
  provider_type?: string;
}>;

export default async function SearchPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const params = await searchParams;
  const query = params.q?.trim() ?? "";
  const capability = params.capability?.trim() || undefined;
  const providerType = params.provider_type?.trim() || undefined;

  let results;
  let searchError: string | null = null;
  if (query) {
    try {
      results = await search({
        q: query,
        capability,
        provider_type: providerType,
        limit: 30,
      });
    } catch (error) {
      // Pre-fix (2026-05-20 sweep): we wrote the error string into a
      // synthetic `results.error` field that nothing in the JSX ever
      // read, so a failed /search rendered as a clean "0 results"
      // panel — a silent error. Now we surface it explicitly above the
      // results table.
      searchError =
        (error as { detail?: string } | null)?.detail ?? String(error);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-ink-900">Search agents</h1>
        <p className="mt-1 text-sm text-ink-600">
          Embedding + keyword search across the discovery store. Filter by
          capability or provider type to narrow.
        </p>
      </div>

      <form
        action="/search"
        className="surface flex flex-wrap items-end gap-3 p-4"
      >
        <label className="flex-1">
          <span className="text-xs uppercase tracking-wide text-ink-400">
            Query
          </span>
          <input
            type="text"
            name="q"
            defaultValue={query}
            placeholder="e.g. crypto payments stablecoin"
            className="mt-1 w-full rounded-lg border border-ink-200 px-3 py-2 text-sm focus:border-accent-500 focus:outline-none"
            required
          />
        </label>
        <label>
          <span className="text-xs uppercase tracking-wide text-ink-400">
            Capability
          </span>
          <input
            type="text"
            name="capability"
            defaultValue={capability ?? ""}
            placeholder="optional"
            className="mt-1 w-48 rounded-lg border border-ink-200 px-3 py-2 text-sm focus:border-accent-500 focus:outline-none"
          />
        </label>
        <label>
          <span className="text-xs uppercase tracking-wide text-ink-400">
            Provider type
          </span>
          <input
            type="text"
            name="provider_type"
            defaultValue={providerType ?? ""}
            placeholder="optional"
            className="mt-1 w-40 rounded-lg border border-ink-200 px-3 py-2 text-sm focus:border-accent-500 focus:outline-none"
          />
        </label>
        <button
          type="submit"
          className="h-10 rounded-lg bg-ink-900 px-4 text-sm font-medium text-white hover:bg-ink-800"
        >
          Search
        </button>
      </form>

      {searchError && (
        <div
          role="alert"
          className="surface border-rose-300 bg-rose-50 p-4 text-sm text-rose-700"
        >
          <p className="font-medium">Search failed</p>
          <p className="mt-1 text-xs text-rose-600">{searchError}</p>
          <p className="mt-2 text-xs text-rose-600">
            The API may be down; try <code>make api</code> or refresh in a
            moment. If this keeps happening, check{" "}
            <code>/health/evidence</code> on the API host.
          </p>
        </div>
      )}

      {!results && !searchError && (
        <p className="text-sm text-ink-400">
          Enter a query above to search the discovery store.
        </p>
      )}

      {results && (
        <>
          <div className="text-xs text-ink-400">
            backend: <code>{results.backend}</code> &middot; embedder:{" "}
            <code>{results.embedder}</code> &middot; results:{" "}
            {results.results.length}
          </div>
          <div className="grid gap-3">
            {results.results.map((candidate) => (
              <Link
                key={candidate.provider_id}
                href={`/agents/${candidate.provider_id}`}
                className="surface group flex items-start gap-4 p-4 transition hover:border-ink-400"
              >
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <div className="text-sm font-semibold text-ink-900 group-hover:text-accent-600">
                      {candidate.display_name}
                    </div>
                    <span className="tag-neutral">
                      {candidate.provider_type}
                    </span>
                  </div>
                  <div className="text-xs text-ink-400">
                    {candidate.provider_id}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {candidate.capabilities.map((capabilityId) => (
                      <span key={capabilityId} className="tag-info">
                        {capabilityId}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex w-44 flex-col items-end gap-1 text-xs">
                  <span
                    className={verificationStatusTagClass(
                      candidate.verification_status,
                    )}
                    title={verificationStatusTooltip(candidate.verification_status)}
                  >
                    {verificationStatusLabel(candidate.verification_status)}
                  </span>
                  <span
                    className={benchmarkStatusTagClass(
                      candidate.benchmark_status,
                    )}
                  >
                    Bench: {benchmarkStatusLabel(candidate.benchmark_status)}
                  </span>
                  <span className={routableTagClass(candidate.will_fail)}>
                    {routableTagLabel(candidate.will_fail)}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
