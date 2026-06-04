/**
 * /trust — the public window onto the Eval_Framework.
 *
 * Why this page exists
 * --------------------
 * The eval framework is the asset the business plan calls the moat, yet
 * until now it had zero UI: it lived entirely in CLI / cron jobs and its
 * output only showed up indirectly as leaderboard numbers. A buyer (or a
 * DD reviewer) had no way to answer "how does a number on a leaderboard
 * come to exist, and why are most cells not publishable yet?".
 *
 * This page answers exactly that, and does it honestly: the credibility
 * histogram at the top is LIVE (same index the /leaderboards page reads),
 * so when every cell is `synthetic_only` the page says so in red rather
 * than implying a trust layer that does not exist yet. Per AGENTS.md hard
 * rule #1, no surface may claim more than the index data warrants — this
 * page is built to make the gap legible, not to hide it.
 *
 * Everything rendered here is derived from backend code that already
 * governs real behaviour (the tier ladder, the protocol-maturity
 * registry, the source taxonomy, the credibility classifier). The page
 * cannot advertise a tier/protocol/source the framework does not
 * implement.
 */

import Link from "next/link";

import { fetchEvalMethodology } from "@/lib/api";
import {
  credibilityLabel,
  credibilityTagClass,
  protocolMaturityLabel,
  protocolMaturityTagClass,
} from "@/lib/tags";
import { formatLabel } from "@/lib/format";

export const metadata = {
  title: "Trust & evaluation methodology · PlanMyAgents",
};

export default async function TrustPage() {
  let data;
  try {
    data = await fetchEvalMethodology();
  } catch (error) {
    return (
      <div className="surface p-8">
        <h1 className="text-xl font-semibold text-ink-900">
          The eval methodology API is not reachable
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

  const totalCaps = data.total_capabilities;
  const realCaps = data.real_run_capabilities;
  const publishable =
    data.credibility_distribution.find((row) => row.status === "publishable")
      ?.count ?? 0;
  const syntheticOnly =
    data.credibility_distribution.find(
      (row) => row.status === "synthetic_only",
    )?.count ?? 0;
  const allSynthetic = totalCaps > 0 && syntheticOnly === totalCaps;

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <p className="text-xs uppercase tracking-wide text-ink-400">
          Trust layer · How we evaluate agents
        </p>
        <h1 className="text-3xl font-semibold tracking-tight text-ink-900">
          How a leaderboard number comes to exist
        </h1>
        <p className="max-w-3xl text-sm text-ink-600">
          PlanMyAgents discovers agents across protocols, then puts each one
          through a cheapest-first evaluation ladder. A capability only earns
          a publishable leaderboard once real invocations against curated
          ground truth back it up. This page is the honest accounting of
          where the index stands today &mdash; including the cells we cannot
          yet stand behind.
        </p>
      </header>

      {/* LIVE honesty banner. When every cell is synthetic_only we say so in
          red — this is the same data /leaderboards renders, not a slide. */}
      <section
        className={
          allSynthetic
            ? "rounded-2xl border border-red-300 bg-red-50 p-5"
            : publishable > 0
              ? "rounded-2xl border border-green-200 bg-green-50 p-5"
              : "rounded-2xl border border-amber-300 bg-amber-50 p-5"
        }
      >
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <h2 className="text-sm font-semibold text-ink-900">
              Live index state
            </h2>
            <p className="mt-1 text-sm text-ink-700">
              {allSynthetic ? (
                <>
                  Every one of the <strong>{totalCaps}</strong> capability
                  cells in the index is currently{" "}
                  <span className={credibilityTagClass("synthetic_only")}>
                    {credibilityLabel("synthetic_only")}
                  </span>{" "}
                  &mdash; scored against mock adapters, not real vendors. We
                  show this rather than hide it. The numbers on{" "}
                  <Link
                    href="/leaderboards"
                    className="text-accent-600 hover:underline"
                  >
                    the leaderboards
                  </Link>{" "}
                  test our scoring code, not the agents, until real adapters
                  land.
                </>
              ) : (
                <>
                  <strong>{realCaps}</strong> of <strong>{totalCaps}</strong>{" "}
                  capability cells have at least one real-adapter run.{" "}
                  <strong>{publishable}</strong> meet the publishable bar. The
                  rest are still climbing the ladder below.
                </>
              )}
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <Stat label="Capabilities" value={totalCaps} />
            <Stat label="With real runs" value={realCaps} />
            <Stat label="Publishable" value={publishable} />
          </div>
        </div>

        {data.credibility_distribution.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {data.credibility_distribution.map((row) => (
              <span
                key={row.status}
                className={credibilityTagClass(row.status)}
                title={`${row.count} capability cell(s) at "${row.status}"`}
              >
                {credibilityLabel(row.status)} &middot; {row.count}
              </span>
            ))}
          </div>
        )}
      </section>

      {/* The tier ladder. */}
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-ink-900">
            The evaluation ladder
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-ink-600">
            Cheapest-first. We climb only as far as the evidence allows; a
            failed rung stops the ladder so we never fabricate a higher claim.
            Each rung maps onto a credibility band the leaderboards enforce.
          </p>
        </div>
        <ol className="space-y-3">
          {data.tiers.map((tier, idx) => (
            <li key={tier.id} className="surface flex gap-4 p-5">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink-900 text-sm font-semibold text-white">
                {idx + 1}
              </div>
              <div className="flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-base font-semibold text-ink-900">
                    {tier.label}
                  </h3>
                  <span className={credibilityTagClass(tier.credibility_band)}>
                    {credibilityLabel(tier.credibility_band)}
                  </span>
                  <code className="text-xs text-ink-400">{tier.id}</code>
                </div>
                <p className="mt-1 text-sm text-ink-600">{tier.description}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* Protocol maturity. */}
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-ink-900">
            What we can actually invoke
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-ink-600">
            &ldquo;Apply to every agent broadly&rdquo; only works if we are
            honest about which protocols have a stable invocation surface
            today. Protocols marked{" "}
            <span className={protocolMaturityTagClass("refusal_only")}>
              {protocolMaturityLabel("refusal_only")}
            </span>{" "}
            or{" "}
            <span className={protocolMaturityTagClass("planned")}>
              {protocolMaturityLabel("planned")}
            </span>{" "}
            get a verification-only result &mdash; never a fabricated quality
            score. Adding a protocol later is registering one invoker; the
            scoring path does not change.
          </p>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          {data.protocols.map((protocol) => (
            <div key={protocol.protocol} className="surface p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-semibold uppercase tracking-wide text-ink-900">
                  {protocol.protocol}
                </div>
                <span className={protocolMaturityTagClass(protocol.maturity)}>
                  {protocolMaturityLabel(protocol.maturity)}
                </span>
              </div>
              <p className="mt-2 text-xs text-ink-600">{protocol.note}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Source taxonomy. */}
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-ink-900">
            Which results count as real
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-ink-600">
            Every ranking carries a <code>source</code>. Only the{" "}
            {data.real_eval_sources
              .map((s) => formatLabel(s))
              .join(" and ")}{" "}
            sources count as real runs toward a credible leaderboard;
            everything else is structurally treated as non-real by the
            credibility classifier, so honesty does not depend on remembering
            to label things at each call site.
          </p>
        </div>
        <div className="surface overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-500">
              <tr>
                <th className="px-4 py-3 text-left">Source</th>
                <th className="px-4 py-3 text-left">Counts as real?</th>
                <th className="px-4 py-3 text-left">Meaning</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {data.sources.map((src) => (
                <tr key={src.source} className="align-top">
                  <td className="px-4 py-3">
                    <code className="text-xs text-ink-800">{src.source}</code>
                  </td>
                  <td className="px-4 py-3">
                    {src.is_real ? (
                      <span className="tag-success">Real run</span>
                    ) : (
                      <span className="tag-neutral">Not real</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-ink-600">
                    {src.description}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Scoring methods + spec pointer. */}
      <section className="surface-muted space-y-3 p-5 text-xs text-ink-600">
        <h2 className="text-sm font-semibold text-ink-900">
          Scoring methods &amp; standards alignment
        </h2>
        <div className="flex flex-wrap gap-2">
          {data.scoring_methods.map((method) => (
            <span key={method} className="tag-info">
              {formatLabel(method)}
            </span>
          ))}
        </div>
        <p>
          Tool/agent calls are scored with a four-step decomposition the
          eval field has converged on &mdash; decide-to-call,
          select-operation, build-arguments, integrate-result &mdash; so a
          failure can be located at its step without changing the composite
          0&ndash;1 quality scale the credibility classifier consumes.
          Open-ended outputs use a versioned LLM-judge rubric.
        </p>
        <p>
          Full requirements, design, and the 13 correctness properties live
          in the spec at <code>{data.spec_reference}</code>. Submit any agent
          with a published card on the{" "}
          <Link
            href="/submit-agent"
            className="text-accent-600 hover:underline"
          >
            agent submission page
          </Link>
          .
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
