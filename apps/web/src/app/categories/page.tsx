import Link from "next/link";

import { fetchCategories } from "@/lib/api";
import { formatLabel } from "@/lib/format";

export default async function CategoriesIndexPage() {
  let data;
  try {
    data = await fetchCategories();
  } catch (error) {
    return (
      <div className="surface p-8">
        <h1 className="text-xl font-semibold text-ink-900">
          The discovery API is not reachable
        </h1>
        <p className="mt-2 text-sm text-ink-600">
          Start the FastAPI app and refresh this page:
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-ink-900 px-4 py-3 text-xs text-ink-50">
          make api
        </pre>
        <p className="mt-3 text-xs text-ink-400">
          Default base URL is <code>http://127.0.0.1:8000</code>. Override with{" "}
          <code>PLANMYAGENTS_API_BASE_URL</code>.
        </p>
        <p className="mt-3 text-xs text-ink-400">
          Error: {(error as { detail?: string }).detail ?? String(error)}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <section>
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-wide text-ink-400">
              Browse · Categories
            </p>
            <h1 className="mt-1 text-2xl font-semibold text-ink-900">
              Agent categories
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-ink-600">
              {data.total_candidates} discovered candidates across{" "}
              {data.categories.length} capability clusters. Each card shows
              honest counts: how many candidates we have, how many have a
              known/listed provider signal, how many passed benchmarks, and how
              many are runnable in production today. Use this surface to
              understand <em>why</em> a goal succeeded or was refused on the
              home page.
            </p>
            <p className="mt-1 text-xs text-ink-400">
              Backed by <code>{data.store}</code> &middot; embedder{" "}
              <code>{data.embedder}</code>
            </p>
          </div>
          <Link
            href="/leaderboards"
            className="surface-muted shrink-0 px-3 py-2 text-xs font-semibold text-ink-700 hover:border-ink-400 hover:text-ink-900"
          >
            View leaderboards &rarr;
          </Link>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        {data.categories.map((category) => (
          <Link
            key={category.cluster_id}
            href={`/categories/${category.cluster_id}`}
            className="surface group p-5 transition hover:border-ink-400"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-base font-semibold text-ink-900 group-hover:text-accent-600">
                  {category.display_name}
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {category.capability_ids.map((capability) => (
                    <span key={capability} className="tag-neutral">
                      {capability}
                    </span>
                  ))}
                </div>
              </div>
              <div className="text-right text-2xl font-semibold tabular-nums text-ink-900">
                {category.totals.candidates}
              </div>
            </div>

            <dl className="mt-4 grid grid-cols-3 gap-2 text-xs text-ink-600">
              <div className="surface-muted px-3 py-2">
                <dt className="text-ink-400">Known/listed</dt>
                <dd className="text-base font-semibold text-ink-800">
                  {category.totals.known_listed}
                </dd>
              </div>
              <div className="surface-muted px-3 py-2">
                <dt className="text-ink-400">Bench-passed</dt>
                <dd className="text-base font-semibold text-ink-800">
                  {category.totals.benchmark_passed}
                </dd>
              </div>
              <div className="surface-muted px-3 py-2">
                <dt className="text-ink-400">Routable</dt>
                <dd className="text-base font-semibold text-ink-800">
                  {category.totals.routable_today}
                </dd>
              </div>
            </dl>

            {category.top_candidate_ids.length > 0 && (
              <div className="mt-4 text-xs text-ink-400">
                Top candidates:{" "}
                <span className="text-ink-600">
                  {category.top_candidate_ids.join(", ")}
                </span>
              </div>
            )}
            {Object.keys(category.provider_type_breakdown).length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {Object.entries(category.provider_type_breakdown).map(
                  ([type, count]) => (
                    <span key={type} className="tag-info">
                      {formatLabel(type)} &middot; {count}
                    </span>
                  ),
                )}
              </div>
            )}
          </Link>
        ))}
      </section>
    </div>
  );
}
