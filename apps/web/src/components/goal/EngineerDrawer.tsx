"use client";

import type { GoalResponse } from "@/lib/api";

export function EngineerDrawer({
  response,
}: {
  response: GoalResponse;
}) {
  const planner = (response.plan?.planner as Record<string, unknown>) ?? {};
  // The intent_mapper field has been replaced by the goal decomposer
  // (see planmyagents_api.planner.goal_decomposer). Surface its provenance
  // here so engineers can see which sub-tasks the LLM produced and
  // whether any were freshly coined vs reused from the catalog.
  const decomposer = planner.decomposer as Record<string, unknown> | undefined;
  const decomposedSubTasks = (planner.decomposed_sub_tasks ?? []) as Array<
    Record<string, unknown>
  >;
  return (
    <details className="surface p-4 text-xs text-ink-500">
      <summary className="cursor-pointer text-sm font-medium text-ink-700">
        Engineer details
      </summary>
      <dl className="mt-3 grid grid-cols-1 gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
        <Field label="Planner mode">{String(planner.mode ?? "unknown")}</Field>
        {Boolean(planner.model) && (
          <Field label="Model">{String(planner.model)}</Field>
        )}
        {decomposer && (
          <Field label="Goal decomposer">
            {String(decomposer.status ?? "unknown")}
            {Boolean(decomposer.confidence) &&
              ` (confidence ${Number(decomposer.confidence).toFixed(2)})`}
          </Field>
        )}
        {decomposedSubTasks.length > 0 && (
          <Field label="Sub-tasks">{decomposedSubTasks.length}</Field>
        )}
        <Field label="Plan status">{String(response.plan?.status ?? "?")}</Field>
        <Field label="Executed">{response.executed ? "true" : "false"}</Field>
      </dl>
      <pre className="mt-3 max-h-96 overflow-auto rounded-lg bg-ink-50 p-3 text-[11px] leading-snug text-ink-700">
        {JSON.stringify(response, null, 2)}
      </pre>
    </details>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt className="font-medium uppercase tracking-wide text-ink-400">
        {label}
      </dt>
      <dd className="text-ink-700">{children}</dd>
    </div>
  );
}
