import Link from "next/link";
import { notFound } from "next/navigation";

import {
  fetchAgent,
  type CandidateDocs,
  type CandidateTool,
} from "@/lib/api";
import {
  benchmarkStatusLabel,
  benchmarkStatusTagClass,
  routableTagClass,
  routableTagLabel,
  verificationStatusLabel,
  verificationStatusTagClass,
  verificationStatusTooltip,
} from "@/lib/tags";

type Params = Promise<{ id: string }>;

type Capability = { id: string; confidence: number; notes?: string };

type Observation = {
  source_id: string;
  observed_at: string;
  evidence_url: string;
  notes: string;
};

type CandidateView = {
  id: string;
  display_name: string;
  vendor: string;
  vendor_url: string;
  provider_type: string;
  capabilities: Capability[];
  verification_status: string;
  benchmark_status: string;
  will_fail: boolean;
  will_fail_reasons: string[];
  required_env_vars: string[];
  evidence_url: string;
  discovery: { source: string; first_seen_at: string; last_seen_at: string };
  docs?: CandidateDocs;
  tools?: CandidateTool[];
  skills?: CandidateTool[];
  openapi_url?: string;
  observations?: Observation[];
  confirmation_count?: number;
  source_ids?: string[];
};

const formatNumber = (value: number, fractionDigits = 2): string => {
  if (Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
};

const formatPercent = (value: number): string => `${(value * 100).toFixed(1)}%`;

const docsAreEmpty = (docs?: CandidateDocs): boolean => {
  if (!docs) return true;
  return (
    !docs.setup_url &&
    !docs.auth_method &&
    docs.install_steps.length === 0 &&
    docs.usage_examples.length === 0 &&
    docs.auth_scopes.length === 0
  );
};

const ProviderTypeBadge = ({ providerType }: { providerType: string }) => {
  // Same convention as the goal-page candidate row: green for runnable
  // agent types, amber for "API only — not an agent". Avoids the
  // confusion where a discovered API page reads as "Discovered Agent"
  // just because the surface is /agents/{id}.
  const meta: { label: string; cls: string } = ((): {
    label: string;
    cls: string;
  } => {
    switch (providerType) {
      case "mcp_server":
        return {
          label: "MCP server",
          cls: "border-emerald-300 bg-emerald-50 text-emerald-800",
        };
      case "a2a_agent":
        return {
          label: "A2A agent",
          cls: "border-sky-300 bg-sky-50 text-sky-800",
        };
      case "ai_agent":
        return {
          label: "AI agent",
          cls: "border-emerald-300 bg-emerald-50 text-emerald-800",
        };
      case "api_provider":
        return {
          label: "API (not an agent)",
          cls: "border-amber-300 bg-amber-50 text-amber-800",
        };
      case "payment_provider":
        return {
          label: "Payment API (not an agent)",
          cls: "border-amber-300 bg-amber-50 text-amber-800",
        };
      default:
        return {
          label: providerType || "unknown",
          cls: "border-ink-200 bg-ink-50 text-ink-700",
        };
    }
  })();
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wide ${meta.cls}`}
    >
      {meta.label}
    </span>
  );
};

const AuthMethodBadge = ({ method }: { method: string }) => {
  if (!method) return <span className="tag-neutral">unknown auth</span>;
  const palette: Record<string, string> = {
    api_key: "tag-success",
    bearer_token: "tag-success",
    oauth2: "tag-success",
    none: "tag-neutral",
    mcp_stdio: "tag-warn",
    mcp_http: "tag-success",
  };
  const className = palette[method] ?? "tag-neutral";
  return <span className={className}>{method}</span>;
};

export default async function AgentDetailPage({ params }: { params: Params }) {
  const { id } = await params;
  let detail;
  try {
    detail = await fetchAgent(id);
  } catch (error) {
    if ((error as { status?: number }).status === 404) {
      notFound();
    }
    throw error;
  }

  const candidate = detail.candidate as unknown as CandidateView;
  const docs = candidate.docs;
  const tools = candidate.tools ?? [];
  const skills = candidate.skills ?? [];
  const observations = candidate.observations ?? [];
  const confirmationCount = candidate.confirmation_count ?? observations.length;
  const showSetup = !docsAreEmpty(docs);

  return (
    <div className="space-y-6">
      <div>
        <Link href="/categories" className="text-xs text-ink-400 hover:text-ink-900">
          &larr; All categories
        </Link>
        <div className="mt-2 flex items-baseline justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold text-ink-900">
                {candidate.display_name}
              </h1>
              <ProviderTypeBadge providerType={candidate.provider_type} />
            </div>
            <div className="text-xs text-ink-400">{candidate.id}</div>
          </div>
          <div className="flex flex-wrap gap-1">
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
            {showSetup && <AuthMethodBadge method={docs?.auth_method ?? ""} />}
            {tools.length > 0 && (
              <span className="tag-neutral">{tools.length} tools</span>
            )}
            {skills.length > 0 && (
              <span className="tag-neutral">{skills.length} skills</span>
            )}
            {confirmationCount > 1 && (
              <span className="tag-success">
                Confirmed by {confirmationCount} sources
              </span>
            )}
          </div>
        </div>
      </div>

      <section className="surface p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          Vendor
        </h2>
        <div className="mt-2 grid gap-2 text-sm md:grid-cols-2">
          <div>
            <div className="text-ink-400">Vendor</div>
            <div className="text-ink-800">{candidate.vendor || "—"}</div>
          </div>
          <div>
            <div className="text-ink-400">Provider type</div>
            <div className="text-ink-800">{candidate.provider_type}</div>
          </div>
          <div className="md:col-span-2">
            <div className="text-ink-400">Vendor URL</div>
            <a
              href={candidate.vendor_url || candidate.evidence_url}
              target="_blank"
              rel="noreferrer"
              className="text-accent-600 underline"
            >
              {candidate.vendor_url || candidate.evidence_url || "—"}
            </a>
          </div>
        </div>
      </section>

      {observations.length > 0 && (
        <ObservationsSection observations={observations} />
      )}

      {showSetup && docs && <SetupSection docs={docs} candidateName={candidate.display_name} />}

      {tools.length > 0 && (
        <ToolsSection
          title="Tools"
          subtitle={
            candidate.provider_type === "mcp_server"
              ? "Returned by tools/list (MCP) or curated catalog"
              : "Operations resolved from the OpenAPI spec or catalog"
          }
          tools={tools}
        />
      )}

      {skills.length > 0 && (
        <ToolsSection
          title="Skills"
          subtitle="Declared in the A2A AgentCard"
          tools={skills}
        />
      )}

      <section className="surface p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          Promotion readiness
        </h2>
        <div className="mt-2 text-sm">
          {detail.promotion_readiness.ready_for_promotion ? (
            <span className="tag-success">Ready for promotion</span>
          ) : (
            <span className="tag-warn">Not yet ready for promotion</span>
          )}
        </div>
        {detail.promotion_readiness.blockers &&
          detail.promotion_readiness.blockers.length > 0 && (
            <div className="mt-3">
              <div className="text-xs uppercase tracking-wide text-ink-400">
                Blockers
              </div>
              <ul className="mt-1 list-disc pl-5 text-sm text-ink-600">
                {detail.promotion_readiness.blockers.map((blocker) => (
                  <li key={blocker}>{blocker}</li>
                ))}
              </ul>
            </div>
          )}
        {detail.promotion_readiness.required_steps &&
          detail.promotion_readiness.required_steps.length > 0 && (
            <div className="mt-3">
              <div className="text-xs uppercase tracking-wide text-ink-400">
                Required steps
              </div>
              <ul className="mt-1 list-disc pl-5 text-sm text-ink-600">
                {detail.promotion_readiness.required_steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ul>
            </div>
          )}
      </section>

      <section className="surface p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          Capabilities
        </h2>
        <div className="mt-2 grid gap-2">
          {candidate.capabilities.map((capability) => (
            <div
              key={capability.id}
              className="flex items-center justify-between rounded-lg border border-ink-200 px-3 py-2"
            >
              <div>
                <div className="font-mono text-sm text-ink-800">
                  {capability.id}
                </div>
                {capability.notes && (
                  <div className="text-xs text-ink-400">{capability.notes}</div>
                )}
              </div>
              <span className="tag-neutral">
                conf: {formatNumber(capability.confidence)}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="surface p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          Benchmark rankings
        </h2>
        {detail.rankings.length === 0 ? (
          <p className="mt-2 text-sm text-ink-600">
            No benchmark rankings yet. Run{" "}
            <code>make benchmark-schedule</code> to populate this section.
          </p>
        ) : (
          <table className="mt-3 w-full text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-ink-400">
                <th className="py-2 pr-4">Capability</th>
                <th className="py-2 pr-4">Sample</th>
                <th className="py-2 pr-4">Success</th>
                <th className="py-2 pr-4">Quality</th>
                <th className="py-2 pr-4">p50/p95 ms</th>
                <th className="py-2 pr-4">Avg cost</th>
                <th className="py-2 pr-4">Composite</th>
                <th className="py-2 pr-4">Source</th>
              </tr>
            </thead>
            <tbody>
              {detail.rankings.map((ranking) => (
                <tr
                  key={`${ranking.capability}-${ranking.last_run_at}`}
                  className="border-t border-ink-200"
                >
                  <td className="py-2 pr-4 font-mono text-xs">
                    {ranking.capability}
                  </td>
                  <td className="py-2 pr-4">{ranking.sample_size}</td>
                  <td className="py-2 pr-4">
                    {formatPercent(ranking.success_rate)}
                  </td>
                  <td className="py-2 pr-4">
                    {formatNumber(ranking.avg_quality_score)}
                  </td>
                  <td className="py-2 pr-4">
                    {ranking.p50_latency_ms} / {ranking.p95_latency_ms}
                  </td>
                  <td className="py-2 pr-4">
                    ${formatNumber(ranking.avg_cost_usd, 4)}
                  </td>
                  <td className="py-2 pr-4">
                    {formatNumber(ranking.composite_score)}
                  </td>
                  <td className="py-2 pr-4">
                    <span
                      className={
                        ranking.source === "real_adapter"
                          ? "tag-success"
                          : "tag-neutral"
                      }
                    >
                      {ranking.source}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="surface p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          Verification history
        </h2>
        {detail.verification_history.length === 0 ? (
          <p className="mt-2 text-sm text-ink-600">
            No verification checks recorded yet. Run{" "}
            <code>make candidate-verify-smoke</code> to record one.
          </p>
        ) : (
          <ul className="mt-3 space-y-2 text-sm">
            {detail.verification_history.map((record) => (
              <li
                key={`${record.created_at}-${record.status}`}
                className="rounded-lg border border-ink-200 px-3 py-2"
              >
                <div className="flex items-center justify-between">
                  <span
                    className={verificationStatusTagClass(record.status)}
                    title={verificationStatusTooltip(record.status)}
                  >
                    {verificationStatusLabel(record.status)}
                  </span>
                  <span className="text-xs text-ink-400">
                    {record.created_at}
                  </span>
                </div>
                {record.verified_capabilities.length > 0 && (
                  <div className="mt-2 text-xs text-ink-600">
                    Verified capabilities:{" "}
                    {record.verified_capabilities.join(", ")}
                  </div>
                )}
                {record.blockers.length > 0 && (
                  <div className="mt-1 text-xs text-ink-600">
                    Blockers: {record.blockers.join(", ")}
                  </div>
                )}
                {record.notes.length > 0 && (
                  <div className="mt-1 text-xs text-ink-400">
                    Notes: {record.notes.join("; ")}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="surface p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
          Recent benchmark runs
        </h2>
        {detail.recent_runs.length === 0 ? (
          <p className="mt-2 text-sm text-ink-600">
            No benchmark runs recorded yet for this provider.
          </p>
        ) : (
          <div className="mt-3 max-h-80 overflow-y-auto rounded-lg border border-ink-200">
            <table className="w-full text-left text-xs">
              <thead className="bg-ink-50 text-ink-400">
                <tr>
                  <th className="px-3 py-2">Test case</th>
                  <th className="px-3 py-2">Capability</th>
                  <th className="px-3 py-2">OK?</th>
                  <th className="px-3 py-2">Quality</th>
                  <th className="px-3 py-2">Latency</th>
                  <th className="px-3 py-2">Cost</th>
                </tr>
              </thead>
              <tbody>
                {detail.recent_runs.map((row, idx) => {
                  const score = (row as { score?: { quality_score?: number; succeeded?: boolean } })
                    .score;
                  const response = (row as {
                    response?: { latency_ms?: number; cost_usd?: number };
                  }).response;
                  return (
                    <tr
                      key={`${row.test_case_id ?? idx}-${idx}`}
                      className="border-t border-ink-200"
                    >
                      <td className="px-3 py-2 font-mono">
                        {String(row.test_case_id ?? "—")}
                      </td>
                      <td className="px-3 py-2 font-mono">
                        {String(row.capability ?? "—")}
                      </td>
                      <td className="px-3 py-2">
                        {score?.succeeded ? "✓" : "✗"}
                      </td>
                      <td className="px-3 py-2">
                        {formatNumber(score?.quality_score ?? 0)}
                      </td>
                      <td className="px-3 py-2">
                        {response?.latency_ms ?? "—"}
                      </td>
                      <td className="px-3 py-2">
                        ${formatNumber(response?.cost_usd ?? 0, 4)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

const ObservationsSection = ({ observations }: { observations: Observation[] }) => (
  <section className="surface p-5">
    <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
      Discovery sources
    </h2>
    <div className="text-xs text-ink-400">
      Each row is one source independently surfacing this provider.
    </div>
    <ul className="mt-3 space-y-2 text-sm">
      {observations.map((obs, idx) => (
        <li
          key={`${obs.source_id}-${obs.evidence_url || idx}`}
          className="flex flex-col gap-1 rounded-lg border border-ink-200 px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
        >
          <div>
            <div className="font-mono text-xs text-ink-800">{obs.source_id}</div>
            {obs.evidence_url && (
              <a
                href={obs.evidence_url}
                target="_blank"
                rel="noreferrer"
                className="break-all text-xs text-accent-600 underline"
              >
                {obs.evidence_url}
              </a>
            )}
            {obs.notes && (
              <div className="mt-1 text-xs text-ink-500">{obs.notes}</div>
            )}
          </div>
          {obs.observed_at && (
            <span className="font-mono text-xs text-ink-400">
              {obs.observed_at}
            </span>
          )}
        </li>
      ))}
    </ul>
  </section>
);

const SetupSection = ({
  docs,
  candidateName,
}: {
  docs: CandidateDocs;
  candidateName: string;
}) => (
  <section className="surface p-5">
    <div className="flex items-center justify-between">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
        Setup
      </h2>
      <AuthMethodBadge method={docs.auth_method} />
    </div>

    <div className="mt-3 grid gap-3 text-sm md:grid-cols-2">
      {docs.setup_url && (
        <div>
          <div className="text-ink-400">Documentation</div>
          <a
            href={docs.setup_url}
            target="_blank"
            rel="noreferrer"
            className="text-accent-600 underline"
          >
            {docs.setup_url}
          </a>
        </div>
      )}
      {docs.pricing_url && (
        <div>
          <div className="text-ink-400">Pricing</div>
          <a
            href={docs.pricing_url}
            target="_blank"
            rel="noreferrer"
            className="text-accent-600 underline"
          >
            {docs.pricing_url}
          </a>
        </div>
      )}
      {docs.status_page_url && (
        <div>
          <div className="text-ink-400">Status</div>
          <a
            href={docs.status_page_url}
            target="_blank"
            rel="noreferrer"
            className="text-accent-600 underline"
          >
            {docs.status_page_url}
          </a>
        </div>
      )}
      {docs.auth_scopes.length > 0 && (
        <div>
          <div className="text-ink-400">Scopes</div>
          <div className="flex flex-wrap gap-1">
            {docs.auth_scopes.map((scope) => (
              <span key={scope} className="tag-neutral font-mono text-xs">
                {scope}
              </span>
            ))}
          </div>
        </div>
      )}
      {(docs.rate_limit_requests_per_minute > 0 ||
        docs.rate_limit_monthly_quota > 0) && (
        <div>
          <div className="text-ink-400">Rate limits</div>
          <div className="text-ink-800">
            {docs.rate_limit_requests_per_minute > 0 &&
              `${docs.rate_limit_requests_per_minute} req/min`}
            {docs.rate_limit_requests_per_minute > 0 &&
              docs.rate_limit_monthly_quota > 0 &&
              " · "}
            {docs.rate_limit_monthly_quota > 0 &&
              `${docs.rate_limit_monthly_quota.toLocaleString()} req/mo`}
          </div>
        </div>
      )}
    </div>

    {docs.install_steps.length > 0 && (
      <div className="mt-4">
        <div className="text-xs uppercase tracking-wide text-ink-400">
          Connect {candidateName}
        </div>
        <ol className="mt-1 list-decimal space-y-1 pl-5 text-sm text-ink-700">
          {docs.install_steps.map((step, idx) => (
            <li key={`${idx}-${step.slice(0, 24)}`}>{step}</li>
          ))}
        </ol>
      </div>
    )}

    {docs.usage_examples.length > 0 && (
      <div className="mt-4 space-y-3">
        <div className="text-xs uppercase tracking-wide text-ink-400">
          Usage examples
        </div>
        {docs.usage_examples.map((example, idx) => (
          <div
            key={`${idx}-${example.title}`}
            className="rounded-lg border border-ink-200"
          >
            <div className="flex items-center justify-between border-b border-ink-200 px-3 py-2 text-xs">
              <span className="font-medium text-ink-700">{example.title || "Example"}</span>
              <span className="font-mono text-ink-400">{example.language}</span>
            </div>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words p-3 text-xs text-ink-800">
              <code>{example.snippet}</code>
            </pre>
          </div>
        ))}
      </div>
    )}
  </section>
);

const ToolsSection = ({
  title,
  subtitle,
  tools,
}: {
  title: string;
  subtitle: string;
  tools: CandidateTool[];
}) => (
  <section className="surface p-5">
    <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-400">
      {title}
    </h2>
    <div className="text-xs text-ink-400">{subtitle}</div>
    <div className="mt-3 grid gap-2">
      {tools.map((tool) => {
        const params = Object.keys(
          (tool.input_schema?.properties as Record<string, unknown> | undefined) ?? {},
        );
        return (
          <details
            key={tool.name}
            className="rounded-lg border border-ink-200 px-3 py-2 text-sm"
          >
            <summary className="flex cursor-pointer items-center justify-between gap-2">
              <div>
                <div className="font-mono text-sm text-ink-800">{tool.name}</div>
                {tool.description && (
                  <div className="mt-0.5 text-xs text-ink-500">
                    {tool.description}
                  </div>
                )}
              </div>
              {params.length > 0 && (
                <span className="tag-neutral text-xs">{params.length} params</span>
              )}
            </summary>
            {(params.length > 0 || tool.examples.length > 0) && (
              <div className="mt-3 space-y-2 border-t border-ink-200 pt-3">
                {params.length > 0 && (
                  <div>
                    <div className="text-xs uppercase tracking-wide text-ink-400">
                      Inputs
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {params.map((name) => (
                        <span
                          key={name}
                          className="tag-neutral font-mono text-xs"
                        >
                          {name}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {tool.examples.length > 0 && (
                  <div>
                    <div className="text-xs uppercase tracking-wide text-ink-400">
                      Examples
                    </div>
                    <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-ink-600">
                      {tool.examples.map((example, idx) => (
                        <li key={`${idx}-${example.slice(0, 24)}`}>{example}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </details>
        );
      })}
    </div>
  </section>
);
