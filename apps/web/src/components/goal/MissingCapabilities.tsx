"use client";

import Link from "next/link";
import { useState } from "react";

import type { DecomposedSubTask } from "@/lib/api";
import {
  benchmarkStatusLabel,
  benchmarkStatusTagClass,
  hasProviderEvidence,
  routableTagClass,
  routableTagLabel,
  verificationStatusLabel,
  verificationStatusTagClass,
  verificationStatusTooltip,
} from "@/lib/tags";

export type DiscoveryCandidate = {
  provider_id?: string;
  id?: string;
  display_name?: string;
  provider_type?: string;
  verification_status?: string;
  benchmark_status?: string;
  will_fail?: boolean;
  capabilities?: Array<string | { id?: string }>;
  [key: string]: unknown;
};

type GroupedCapability = {
  capability: string;
  qualified: DiscoveryCandidate[];
  rejected: DiscoveryCandidate[];
  /**
   * The LLM-written sub-task this capability came from, when the
   * goal decomposer ran for this request. When present, the UI
   * shows the human-readable narrative ("Search the web for OEM
   * fleet programs in India") as the row title and demotes the
   * opaque slug to a secondary chip. When absent (decomposer
   * disabled, executable plan, or LLM tier unavailable), the row
   * falls back to the slug-only view it used to show.
   */
  subTask?: DecomposedSubTask;
};

const candidateCapabilityIds = (candidate: DiscoveryCandidate): string[] => {
  const list = candidate.capabilities ?? [];
  const ids: string[] = [];
  for (const item of list) {
    if (typeof item === "string") {
      ids.push(item);
    } else if (item && typeof item === "object" && typeof item.id === "string") {
      ids.push(item.id);
    }
  }
  return ids;
};

const isQualifiedAgenticType = (providerType: string | undefined): boolean => {
  return (
    providerType === "mcp_server" ||
    providerType === "a2a_agent" ||
    providerType === "ai_agent"
  );
};

export const groupCandidatesByCapability = (
  missingCapabilities: string[],
  candidates: DiscoveryCandidate[],
  decomposedSubTasks?: DecomposedSubTask[],
): GroupedCapability[] => {
  // Index sub-tasks by suggested_capability_id so we can attach the
  // LLM-written narrative to its matching capability row. First
  // occurrence wins to mirror the decomposer's execution-order
  // contract.
  const subTaskIndex = new Map<string, DecomposedSubTask>();
  for (const subTask of decomposedSubTasks ?? []) {
    if (
      subTask &&
      typeof subTask.suggested_capability_id === "string" &&
      !subTaskIndex.has(subTask.suggested_capability_id)
    ) {
      subTaskIndex.set(subTask.suggested_capability_id, subTask);
    }
  }
  return missingCapabilities.map((capability) => {
    const matching = candidates.filter((candidate) =>
      candidateCapabilityIds(candidate).includes(capability),
    );
    const qualified = matching.filter((candidate) =>
      isQualifiedAgenticType(candidate.provider_type),
    );
    const rejected = matching.filter(
      (candidate) => !isQualifiedAgenticType(candidate.provider_type),
    );
    return {
      capability,
      qualified,
      rejected,
      subTask: subTaskIndex.get(capability),
    };
  });
};

export function MissingCapabilities({
  groups,
}: {
  groups: GroupedCapability[];
}) {
  if (groups.length === 0) return null;

  return (
    <section className="surface p-5">
      <header className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-ink-900">
            Why this is blocked
          </h3>
          <p className="text-xs text-ink-500">
            One row per missing capability. Click to see candidates and the
            reason each one is not routable yet.
          </p>
        </div>
        <span className="tag-warn">{groups.length} blocker{groups.length === 1 ? "" : "s"}</span>
      </header>

      <ul className="mt-4 divide-y divide-ink-100 rounded-xl border border-ink-200">
        {groups.map((group) => (
          <CapabilityRow key={group.capability} group={group} />
        ))}
      </ul>
    </section>
  );
}

function CapabilityRow({ group }: { group: GroupedCapability }) {
  const [open, setOpen] = useState(false);
  const knownSourceCount = group.qualified.filter((candidate) =>
    hasProviderEvidence(candidate.verification_status),
  ).length;
  // Prefer the LLM-written human-readable step when the decomposer
  // ran. Falls back to the slug-only legacy view for pre-decomposer
  // executable plans or when the decomposer was unavailable.
  const headline = group.subTask?.user_facing_step ?? group.capability;
  const description = group.subTask?.description;

  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-ink-50"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-ink-900">
                {headline}
              </div>
              {group.subTask && (
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="tag-info text-[10px]">
                    {group.capability}
                  </span>
                  <span className="text-xs text-ink-500">
                    {group.qualified.length} candidate
                    {group.qualified.length === 1 ? "" : "s"} ·{" "}
                    {knownSourceCount} known/listed
                  </span>
                </div>
              )}
              {!group.subTask && (
                <span className="mt-1 text-xs text-ink-500">
                  {group.qualified.length} candidate
                  {group.qualified.length === 1 ? "" : "s"} ·{" "}
                  {knownSourceCount} known/listed
                </span>
              )}
            </div>
          </div>
          <div className="mt-1 text-sm text-ink-700">
            {group.qualified.length === 0
              ? "No qualified MCP, A2A, or AI-agent candidates found yet."
              : "Candidates exist, but none are tested by PlanMyAgents, benchmark-passed, and adapter-ready."}
          </div>
        </div>
        <span className="text-ink-400">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-ink-100 bg-ink-50 px-4 py-4">
          {group.subTask && (
            <div className="rounded-lg border border-ink-200 bg-white p-3 text-xs">
              {description && (
                <p className="text-sm text-ink-700">{description}</p>
              )}
              <dl className="mt-2 grid gap-1 text-ink-600 sm:grid-cols-2">
                <div>
                  <dt className="font-semibold uppercase tracking-wide text-ink-400">
                    Search query (scouts)
                  </dt>
                  <dd className="mt-0.5 font-mono text-ink-700">
                    {group.subTask.search_query}
                  </dd>
                </div>
                <div>
                  <dt className="font-semibold uppercase tracking-wide text-ink-400">
                    Acceptance (judge)
                  </dt>
                  <dd className="mt-0.5 text-ink-700">
                    {group.subTask.acceptance_criteria}
                  </dd>
                </div>
              </dl>
            </div>
          )}
          {group.qualified.length > 0 && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                Top candidates
              </div>
              <ul className="mt-2 space-y-2">
                {group.qualified.slice(0, 5).map((candidate, index) => (
                  <CandidateRow key={index} candidate={candidate} />
                ))}
              </ul>
            </div>
          )}
          {group.rejected.length > 0 && (
            <RejectedToggle rejected={group.rejected} />
          )}
        </div>
      )}
    </li>
  );
}

function CandidateRow({ candidate }: { candidate: DiscoveryCandidate }) {
  const providerId = String(candidate.provider_id ?? candidate.id ?? "");
  const displayName = String(candidate.display_name ?? providerId);
  const verification = String(candidate.verification_status ?? "unverified");
  const benchmark = String(candidate.benchmark_status ?? "not_started");
  const willFail = Boolean(candidate.will_fail ?? true);
  const providerType = String(candidate.provider_type ?? "");
  return (
    <li className="flex items-center justify-between gap-3 rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm">
      <Link
        href={`/agents/${providerId}`}
        className="min-w-0 flex-1 font-medium text-ink-900 hover:text-accent-600"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate">{displayName}</span>
          <ProviderTypeBadge providerType={providerType} />
        </div>
      </Link>
      <div className="flex items-center gap-1 text-[11px]">
        <span
          className={verificationStatusTagClass(verification)}
          title={verificationStatusTooltip(verification)}
        >
          {verificationStatusLabel(verification)}
        </span>
        <span className={benchmarkStatusTagClass(benchmark)}>
          {benchmarkStatusLabel(benchmark)}
        </span>
        <span className={routableTagClass(willFail)}>
          {routableTagLabel(willFail)}
        </span>
      </div>
    </li>
  );
}

function ProviderTypeBadge({ providerType }: { providerType: string }) {
  // Make the AGENT / MCP / A2A vs API distinction visually obvious so a
  // user can never confuse a discovered REST API for a runnable agent.
  const meta = ((): { label: string; cls: string } => {
    switch (providerType) {
      case "mcp_server":
        return {
          label: "MCP",
          cls: "border-emerald-300 bg-emerald-50 text-emerald-800",
        };
      case "a2a_agent":
        return {
          label: "A2A",
          cls: "border-sky-300 bg-sky-50 text-sky-800",
        };
      case "ai_agent":
        return {
          label: "AGENT",
          cls: "border-emerald-300 bg-emerald-50 text-emerald-800",
        };
      case "api_provider":
        return {
          label: "API (not an agent)",
          cls: "border-amber-300 bg-amber-50 text-amber-800",
        };
      case "payment_provider":
        return {
          label: "PAYMENT API",
          cls: "border-amber-300 bg-amber-50 text-amber-800",
        };
      default:
        return { label: providerType || "unknown", cls: "tag-info" };
    }
  })();
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${meta.cls}`}
    >
      {meta.label}
    </span>
  );
}

function RejectedToggle({ rejected }: { rejected: DiscoveryCandidate[] }) {
  const [show, setShow] = useState(false);
  return (
    <div>
      <button
        type="button"
        onClick={() => setShow((prev) => !prev)}
        className="text-xs font-medium text-ink-600 underline-offset-2 hover:underline"
      >
        {show ? "Hide" : "Show"} {rejected.length} non-agentic fallback
        {rejected.length === 1 ? "" : "s"} (APIs, payment providers)
      </button>
      {show && (
        <ul className="mt-2 space-y-1 text-xs text-ink-600">
          {rejected.map((candidate, index) => (
            <li key={index}>
              <span className="font-medium text-ink-800">
                {String(candidate.display_name ?? candidate.id ?? "")}
              </span>{" "}
              <span className="text-ink-400">
                ({String(candidate.provider_type ?? "")})
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
