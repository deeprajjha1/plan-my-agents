import type { DiscoveryCandidate } from "./MissingCapabilities";
import { hasProviderEvidence } from "@/lib/tags";

type Counts = {
  total: number;
  known: number;
  benchmarked: number;
  adapterReady: number;
  routable: number;
};

const summarise = (candidates: DiscoveryCandidate[]): Counts => {
  let known = 0;
  let benchmarked = 0;
  let adapterReady = 0;
  let routable = 0;
  for (const candidate of candidates) {
    if (hasProviderEvidence(candidate.verification_status)) known += 1;
    if (candidate.benchmark_status === "passed") benchmarked += 1;
    const adapterModule = String(
      (candidate as { adapter_module?: string }).adapter_module ?? "",
    );
    if (adapterModule && !adapterModule.startsWith("planmyagents_api.agents.protocol:"))
      adapterReady += 1;
    if (!(candidate.will_fail ?? true)) routable += 1;
  }
  return {
    total: candidates.length,
    known,
    benchmarked,
    adapterReady,
    routable,
  };
};

const stepClass = (
  base: "active" | "done" | "blocked" | "neutral",
): string => {
  switch (base) {
    case "active":
      return "step-chip step-chip-active";
    case "done":
      return "step-chip step-chip-done";
    case "blocked":
      return "step-chip step-chip-blocked";
    default:
      return "step-chip";
  }
};

export function DiscoveryProgressStrip({
  candidates,
}: {
  candidates: DiscoveryCandidate[];
}) {
  const counts = summarise(candidates);
  if (counts.total === 0) {
    return (
      <div className="surface px-5 py-4 text-sm text-ink-500">
        No discovery candidates surfaced for this goal.
      </div>
    );
  }

  const steps: Array<{
    label: string;
    value: number;
    base: "active" | "done" | "blocked" | "neutral";
    detail: string;
  }> = [
    {
      label: "Discovered",
      value: counts.total,
      base: "active",
      detail: "candidates surfaced",
    },
    {
      label: "Known/listed",
      value: counts.known,
      base: counts.known > 0 ? "done" : "blocked",
      detail: counts.known > 0 ? "source signal found" : "needs source signal",
    },
    {
      label: "Benchmarked",
      value: counts.benchmarked,
      base: counts.benchmarked > 0 ? "done" : "blocked",
      detail:
        counts.benchmarked > 0 ? "passed real cases" : "no benchmark runs yet",
    },
    {
      label: "Adapter ready",
      value: counts.adapterReady,
      base: counts.adapterReady > 0 ? "done" : "blocked",
      detail:
        counts.adapterReady > 0
          ? "production adapter wired"
          : "needs production adapter",
    },
    {
      label: "Runnable today",
      value: counts.routable,
      base: counts.routable > 0 ? "done" : "blocked",
      detail:
        counts.routable > 0
          ? "can run real tasks now"
          : "blocked from production routing",
    },
  ];

  return (
    <div className="flex flex-wrap gap-2">
      {steps.map((step) => (
        <div key={step.label} className={stepClass(step.base)}>
          <div className="text-xs uppercase tracking-wide text-ink-500">
            {step.label}
          </div>
          <div className="text-2xl font-semibold text-ink-900">{step.value}</div>
          <div className="text-[11px] text-ink-500">{step.detail}</div>
        </div>
      ))}
    </div>
  );
}
