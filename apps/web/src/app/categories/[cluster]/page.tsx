import Link from "next/link";
import { notFound } from "next/navigation";

import { fetchCategory } from "@/lib/api";
import {
  benchmarkStatusTagClass,
  benchmarkStatusLabel,
  routableTagClass,
  routableTagLabel,
  verificationStatusLabel,
  verificationStatusTagClass,
  verificationStatusTooltip,
} from "@/lib/tags";

type Params = Promise<{ cluster: string }>;

export default async function CategoryDetailPage({
  params,
}: {
  params: Params;
}) {
  const { cluster } = await params;
  let detail;
  try {
    detail = await fetchCategory(cluster);
  } catch (error) {
    if ((error as { status?: number }).status === 404) {
      notFound();
    }
    throw error;
  }

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/categories"
          className="text-xs text-ink-400 hover:text-ink-900"
        >
          &larr; All categories
        </Link>
        <h1 className="mt-2 text-2xl font-semibold text-ink-900">
          {detail.display_name}
        </h1>
        <p className="mt-1 text-sm text-ink-600">
          {detail.totals.candidates} candidates &middot;{" "}
          {detail.totals.known_listed} known/listed &middot;{" "}
          {detail.totals.benchmark_passed} benchmark-passed &middot;{" "}
          {detail.totals.routable_today} runnable today
        </p>
        <div className="mt-2 flex flex-wrap gap-1">
          {detail.capability_ids.map((capability) => (
            <Link
              key={capability}
              href={`/leaderboards/${encodeURIComponent(capability)}`}
              className="tag-neutral hover:bg-ink-200"
              title={`Open leaderboard for ${capability}`}
            >
              {capability} &rarr;
            </Link>
          ))}
        </div>
      </div>

      <div className="grid gap-3">
        {detail.candidates.map((candidate) => (
          <Link
            key={candidate.provider_id}
            href={`/agents/${candidate.provider_id}`}
            className="surface group flex items-start gap-4 p-5 transition hover:border-ink-400"
          >
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <div className="text-base font-semibold text-ink-900 group-hover:text-accent-600">
                  {candidate.display_name}
                </div>
                <span className="tag-neutral">{candidate.provider_type}</span>
              </div>
              <div className="mt-1 text-xs text-ink-400">
                {candidate.provider_id}
              </div>
              <div className="mt-3 flex flex-wrap gap-1">
                {candidate.capabilities.map((capability) => (
                  <span key={capability} className="tag-info">
                    {capability}
                  </span>
                ))}
              </div>
              {candidate.will_fail_reasons.length > 0 && (
                <ul className="mt-3 list-disc pl-5 text-xs text-ink-600">
                  {candidate.will_fail_reasons.slice(0, 3).map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
            </div>
            <div className="flex w-44 flex-col items-end gap-1 text-xs">
              <span
                className={verificationStatusTagClass(candidate.verification_status)}
                title={verificationStatusTooltip(candidate.verification_status)}
              >
                {verificationStatusLabel(candidate.verification_status)}
              </span>
              <span
                className={benchmarkStatusTagClass(candidate.benchmark_status)}
              >
                Bench: {benchmarkStatusLabel(candidate.benchmark_status)}
              </span>
              <span className={routableTagClass(candidate.will_fail)}>
                {routableTagLabel(candidate.will_fail)}
              </span>
              {candidate.required_env_vars.length > 0 && (
                <span className="tag-warn">
                  Needs: {candidate.required_env_vars.join(", ")}
                </span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
