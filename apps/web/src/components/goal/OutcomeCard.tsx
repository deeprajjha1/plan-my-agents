import type { GoalResponse, GoalSubTask } from "@/lib/api";

type Mode =
  | "executed"
  | "executable_not_run"
  | "partial_not_run"
  | "outline_only"
  | "refused"
  | "error";

const titleFor = (mode: Mode): string => {
  switch (mode) {
    case "executed":
      return "Done";
    case "executable_not_run":
      return "Plan ready, not executed";
    case "partial_not_run":
      return "Plan partially ready";
    case "outline_only":
      return "Plan outline only";
    case "refused":
      return "Cannot complete this goal yet";
    case "error":
      return "Request failed";
  }
};

const subtitleFor = (mode: Mode, response: GoalResponse | null): string => {
  if (!response) return "";
  const plan = response.plan ?? {};
  const subTasks = (plan.sub_tasks as Array<unknown> | undefined) ?? [];
  const missing = (plan.missing_capabilities as string[] | undefined) ?? [];
  const coverage = plan.recipe_coverage;
  switch (mode) {
    case "executed":
      return (
        response.summary ??
        `Executed ${subTasks.length} sub-task${subTasks.length === 1 ? "" : "s"}.`
      );
    case "executable_not_run":
      return `${subTasks.length} sub-task${subTasks.length === 1 ? "" : "s"} ready. Tick "Execute" and re-submit to run.`;
    case "partial_not_run":
      return `${coverage?.exportable_step_count ?? 0}/${
        coverage?.step_count ?? subTasks.length
      } sub-task${
        (coverage?.step_count ?? subTasks.length) === 1 ? "" : "s"
      } exportable. The remaining steps are gaps, not runnable agents.`;
    case "outline_only":
      return `The planner produced ${
        coverage?.step_count ?? subTasks.length
      } sub-task${
        (coverage?.step_count ?? subTasks.length) === 1 ? "" : "s"
      }, but none are routable/exportable yet. Use the Markdown runbook as a diagnostic plan; no agent will run this today.`;
    case "refused":
      return `${missing.length || subTasks.length || 0} capabilit${
        (missing.length || subTasks.length || 0) === 1 ? "y" : "ies"
      } needed; none are routable in the current registry.`;
    case "error":
      return "";
  }
};

const colourFor = (mode: Mode): string => {
  switch (mode) {
    case "executed":
      return "border-green-200 bg-green-50";
    case "executable_not_run":
      return "border-accent-300 bg-accent-50";
    case "partial_not_run":
      return "border-amber-200 bg-amber-50";
    case "outline_only":
      return "border-amber-200 bg-amber-50";
    case "refused":
      return "border-amber-200 bg-amber-50";
    case "error":
      return "border-red-200 bg-red-50";
  }
};

const tagFor = (mode: Mode): { label: string; cls: string } => {
  switch (mode) {
    case "executed":
      return { label: "Executed", cls: "tag-success" };
    case "executable_not_run":
      return { label: "Executable", cls: "tag-info" };
    case "partial_not_run":
      return { label: "Partial", cls: "tag-warn" };
    case "outline_only":
      return { label: "No executable agents", cls: "tag-warn" };
    case "refused":
      return { label: "Refused", cls: "tag-warn" };
    case "error":
      return { label: "Error", cls: "tag-danger" };
  }
};

export const outcomeMode = (response: GoalResponse | null): Mode => {
  if (!response) return "error";
  if (response.executed) return "executed";
  const plan = response.plan ?? {};
  const coverage = plan.recipe_coverage;
  if (coverage) {
    if (coverage.status === "gap_only" || coverage.exportable_step_count === 0) {
      return "outline_only";
    }
    if (coverage.status === "partial" || coverage.gap_count > 0) {
      return "partial_not_run";
    }
  }
  if (plan.status === "executable") return "executable_not_run";
  return "refused";
};

/**
 * Pull the human-friendly headline out of a sub-task. The decomposer
 * stores its short label in `inputs.user_facing_step` ("Find bus
 * schedules") and falls back to the longer `description` when the
 * planner doesn't emit a step label. We prefer the short label
 * because it's what the user *asked* the planner to give us; the
 * description is the LLM's longer narration and reads as filler in a
 * compact list.
 */
const stepLabelFor = (subTask: GoalSubTask): string => {
  const inputs = subTask.inputs ?? {};
  const userFacingStep =
    typeof inputs.user_facing_step === "string"
      ? inputs.user_facing_step.trim()
      : "";
  if (userFacingStep) return userFacingStep;
  if (subTask.description) return subTask.description.trim();
  return subTask.capability || "(unnamed sub-task)";
};

const detailLineFor = (subTask: GoalSubTask): string | null => {
  // Show the longer description as a secondary line ONLY when it
  // adds information beyond the short headline — otherwise it's
  // redundant noise.
  const inputs = subTask.inputs ?? {};
  const userFacingStep =
    typeof inputs.user_facing_step === "string"
      ? inputs.user_facing_step.trim()
      : "";
  if (
    userFacingStep &&
    subTask.description &&
    subTask.description.trim() !== userFacingStep
  ) {
    return subTask.description.trim();
  }
  return null;
};

function SubTasksList({ subTasks }: { subTasks: GoalSubTask[] }) {
  // Renders the ordered list of sub-tasks the planner decomposed the
  // goal into. Shown on every outcome (executed / executable / outline-
  // only / refused) — the user always wants to know "what did the
  // planner think I asked for?" before they trust or override the
  // routing decision. Empty state is suppressed at the call site.
  //
  // `data-testid` lets Playwright / RTL assert the list renders
  // regardless of outcome mode (see
  // apps/web/tests/e2e/subtasks-list-modes.spec.ts).
  return (
    <div
      data-testid="goal-sub-tasks-list"
      className="mt-4 rounded-lg border border-ink-200 bg-white p-3"
    >
      <div className="text-xs uppercase tracking-wide text-ink-400">
        Sub-tasks the planner produced
      </div>
      <ol className="mt-2 space-y-2 text-sm text-ink-800">
        {subTasks.map((subTask, index) => {
          const headline = stepLabelFor(subTask);
          const detail = detailLineFor(subTask);
          return (
            <li
              key={`${subTask.capability || "sub-task"}-${index}`}
              className="flex gap-3"
              data-testid="goal-sub-task-row"
            >
              <span className="mt-0.5 inline-flex h-5 w-5 flex-none items-center justify-center rounded-full bg-ink-100 text-[11px] font-semibold tabular-nums text-ink-600">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-medium text-ink-900">{headline}</div>
                {detail && (
                  <div className="mt-0.5 text-xs text-ink-600">{detail}</div>
                )}
                {subTask.capability && (
                  <div className="mt-1 text-[11px] text-ink-400">
                    capability <code>{subTask.capability}</code>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function OutcomeCard({
  response,
  errorDetail,
  onRetryExecute,
}: {
  response: GoalResponse | null;
  errorDetail: string | null;
  onRetryExecute?: () => void;
}) {
  const mode: Mode = errorDetail ? "error" : outcomeMode(response);
  const title = titleFor(mode);
  const subtitle = errorDetail ?? subtitleFor(mode, response);
  const tag = tagFor(mode);
  const showRetryExecute =
    mode === "executable_not_run" && Boolean(onRetryExecute);
  const answer = response?.answer ?? null;
  const subTasks = (response?.plan?.sub_tasks ?? []) as GoalSubTask[];

  return (
    <section className={`rounded-2xl border p-5 ${colourFor(mode)}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-ink-500">
            Outcome
          </div>
          <h2 className="mt-1 text-2xl font-semibold text-ink-900">{title}</h2>
          {subtitle && (
            <p className="mt-1 text-sm text-ink-700">{subtitle}</p>
          )}
        </div>
        <span className={tag.cls}>{tag.label}</span>
      </div>

      {answer && (
        <div className="mt-4 rounded-lg border border-ink-200 bg-white p-3 text-sm text-ink-800">
          <div className="text-xs uppercase tracking-wide text-ink-400">
            Answer
          </div>
          <div className="mt-1 whitespace-pre-wrap">{answer}</div>
        </div>
      )}

      {/* Always render the decomposition — even when the outcome is
          "executed" the user benefits from seeing what the planner
          thought their goal meant, so they can challenge the framing
          if it's wrong. Hidden only when the API returned no
          sub-tasks at all (typical of upstream errors / 503s). */}
      {subTasks.length > 0 && <SubTasksList subTasks={subTasks} />}

      {showRetryExecute && (
        <button
          type="button"
          onClick={onRetryExecute}
          className="mt-4 inline-flex items-center gap-2 rounded-lg bg-ink-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-ink-800"
        >
          Run this plan now
        </button>
      )}
    </section>
  );
}
