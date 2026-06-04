"use client";

import type {
  DecomposedSubTask,
  DemandSummaryEntry,
  GoalResponse,
  HumanFallbackPayload,
  ResearchBackstopPayload,
} from "@/lib/api";
import { EngineerDrawer } from "@/components/goal/EngineerDrawer";
import { ExecutionSteps } from "@/components/goal/ExecutionSteps";
import { ExplainPanel } from "@/components/goal/ExplainPanel";
import { HumanAlternatives } from "@/components/goal/HumanAlternatives";
import {
  type DiscoveryCandidate,
  MissingCapabilities,
  groupCandidatesByCapability,
} from "@/components/goal/MissingCapabilities";
import { OutcomeCard, outcomeMode } from "@/components/goal/OutcomeCard";
import { RecipeDownloadButtons } from "@/components/goal/RecipeDownloadButtons";
import { ResearchBackstop } from "@/components/goal/ResearchBackstop";
import { SaveRecipeButton } from "@/components/goal/SaveRecipeButton";

import { DetailsAccordion } from "./DetailsAccordion";
import { DownloadResponseButton } from "./DownloadResponseButton";
import { IndexProvenancePill } from "./IndexProvenancePill";
import { ReasoningPill } from "./ReasoningPill";
import type {
  CandidateJudgeMetadata,
  CostEstimate,
  IndexFreshness,
  LiveDiscoveryStatus,
  LiveResearchStatus,
  PlannerLlmQuality,
} from "./response-types";

/**
 * Top-level orchestrator for /goal response rendering.
 *
 * `goal/page.tsx` owns the form, submission lifecycle, and error
 * detail; this component owns the rendering of whatever the form
 * produced. The split was deliberate — keeping form-state vs.
 * response-state on opposite sides of the boundary lets the page
 * itself stay ~180 lines and lets the panel be re-mounted (e.g. from
 * /recipes preview) without dragging the form along.
 *
 * `submittedGoal` is the *exact* text the backend planned for — kept
 * separate from the editable textarea so the ExplainPanel always
 * re-runs the prompt that produced the displayed response, even if
 * the user has since edited the textbox to compose a follow-up.
 */
export function ResponseLayout({
  response,
  errorDetail,
  onRetryExecute,
  submittedGoal,
}: {
  response: GoalResponse | null;
  errorDetail: string | null;
  onRetryExecute: () => void;
  submittedGoal: string;
}) {
  const mode = response ? outcomeMode(response) : "error";
  const plan: Record<string, unknown> = response?.plan
    ? (response.plan as unknown as Record<string, unknown>)
    : {};
  const discovery = plan.discovery as
    | {
        candidates?: DiscoveryCandidate[];
        missing_capabilities?: string[];
        decomposed_sub_tasks?: DecomposedSubTask[];
      }
    | null
    | undefined;
  const candidates = discovery?.candidates ?? [];
  const missingCapabilities = (plan.missing_capabilities ?? []) as string[];
  // The decomposer's sub-task list is mirrored under both
  // `plan.planner.decomposed_sub_tasks` (planning provenance) and
  // `plan.discovery.decomposed_sub_tasks` (rendering convenience).
  // Prefer the discovery copy because it travels with the candidate
  // list this page is about to render. Fall back to planner so a
  // partial response (no discovery block) still gets the rich UI.
  const planner = (plan.planner ?? {}) as {
    decomposed_sub_tasks?: DecomposedSubTask[];
  };
  const decomposedSubTasks =
    discovery?.decomposed_sub_tasks ?? planner.decomposed_sub_tasks ?? [];
  const groups = groupCandidatesByCapability(
    missingCapabilities,
    candidates,
    decomposedSubTasks,
  );
  const costEstimate = (plan.cost_estimate ?? null) as CostEstimate | null;
  const liveResearch =
    ((plan.discovery as Record<string, unknown> | undefined)
      ?.live_research as LiveResearchStatus | undefined) ?? null;
  const liveDiscovery =
    ((plan.discovery as Record<string, unknown> | undefined)
      ?.live_discovery as LiveDiscoveryStatus | undefined) ?? null;
  // Slice 1 (human fallback UX): LLM-suggested manual workarounds the
  // backend attaches to the discovery block when no specialist agent
  // fits the goal. Null when the discovery block is absent (executable
  // plan path); the component itself short-circuits on null/skipped.
  const humanAlternatives =
    ((plan.discovery as Record<string, unknown> | undefined)
      ?.human_alternatives as HumanFallbackPayload | undefined) ?? null;
  // Per-missing-capability demand snapshot lives under
  // `plan.discovery.demand.per_capability_summary`. Keeps the
  // HumanAlternatives card self-contained — it can render the
  // "47 other users have asked for this" footer without any further
  // server roundtrip or client-side aggregation.
  const demandPerCapability =
    (
      (plan.discovery as Record<string, unknown> | undefined)?.demand as
        | { per_capability_summary?: DemandSummaryEntry[] }
        | undefined
    )?.per_capability_summary ?? null;
  // Slice 2 (research-agent backstop): per-capability search-+-LLM
  // synthesis output. Null when discovery is absent (executable plan
  // path); the component itself short-circuits on null and on non-
  // user-visible statuses (skipped/disabled/missing_credentials).
  const researchBackstop =
    ((plan.discovery as Record<string, unknown> | undefined)
      ?.research_backstop as ResearchBackstopPayload | undefined) ?? null;
  const indexFreshness =
    ((plan.discovery as Record<string, unknown> | undefined)
      ?.index_freshness as IndexFreshness | undefined) ?? null;
  // LLM provenance: which tier produced the plan, and which tier
  // filtered the candidates. Surfaced as a status pill so the user
  // always knows whether they got an actual LLM answer or a degraded
  // fallback.
  const plannerLlmQuality =
    ((plan.planner as Record<string, unknown> | undefined)
      ?.llm_quality as PlannerLlmQuality | undefined) ?? null;
  const candidateJudge =
    ((plan.discovery as Record<string, unknown> | undefined)
      ?.candidate_judge as CandidateJudgeMetadata | undefined) ?? null;

  const hasDetails = Boolean(
    response &&
      (costEstimate ||
        (liveDiscovery &&
          liveDiscovery.status !== "skipped_no_missing_capabilities") ||
        (liveResearch && !liveResearch.ran) ||
        candidates.length > 0),
  );

  return (
    <div className="space-y-6">
      <OutcomeCard
        response={response}
        errorDetail={errorDetail}
        onRetryExecute={onRetryExecute}
      />

      {response?.plan?.goal_id && response?.plan?.recipes && (
        <>
          <RecipeDownloadButtons
            goalId={response.plan.goal_id}
            recipes={response.plan.recipes}
          />
          <SaveRecipeButton
            goalId={response.plan.goal_id}
            recipes={response.plan.recipes}
          />
        </>
      )}

      {response && (plannerLlmQuality || candidateJudge) && (
        <ReasoningPill
          planner={plannerLlmQuality}
          judge={candidateJudge}
        />
      )}

      {response && submittedGoal && <ExplainPanel goal={submittedGoal} />}

      {response && (liveResearch || liveDiscovery || indexFreshness) && (
        <IndexProvenancePill
          liveResearch={liveResearch}
          liveDiscovery={liveDiscovery}
          freshness={indexFreshness}
        />
      )}

      {response && mode === "executed" && response.execution && (
        <ExecutionSteps
          execution={response.execution as Record<string, unknown>}
        />
      )}

      {response && mode === "refused" && groups.length > 0 && (
        <MissingCapabilities groups={groups} />
      )}

      {response && mode === "refused" && (
        <ResearchBackstop payload={researchBackstop} />
      )}

      {response && mode === "refused" && (
        <HumanAlternatives
          payload={humanAlternatives}
          demandSummary={demandPerCapability}
        />
      )}

      {hasDetails && response && (
        <DetailsAccordion
          costEstimate={costEstimate}
          liveDiscovery={liveDiscovery}
          liveResearch={liveResearch}
          freshness={indexFreshness}
          candidates={candidates}
        />
      )}

      {response && (
        <div className="flex flex-wrap items-center gap-3">
          <DownloadResponseButton response={response} />
          <EngineerDrawer response={response} />
        </div>
      )}
    </div>
  );
}
