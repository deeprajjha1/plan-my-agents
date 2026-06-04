"use client";

import { useState } from "react";

type SubResult = {
  capability?: unknown;
  description?: unknown;
  provider_id?: unknown;
  succeeded?: unknown;
  refusal_reason?: unknown;
  score?: { quality_score?: number };
  response?: { latency_ms?: number; cost_usd?: number; output?: unknown };
};

export function ExecutionSteps({
  execution,
}: {
  execution: Record<string, unknown>;
}) {
  const subResults = (execution.sub_task_results as SubResult[]) ?? [];
  const records = (execution.records as Array<Record<string, unknown>>) ?? [];
  const cost = Number(execution.total_cost_usd ?? 0);
  const confidence = Number(execution.average_confidence ?? 0);

  if (subResults.length === 0 && records.length === 0) return null;

  return (
    <section className="surface p-5">
      <header className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-ink-900">Steps</h3>
          <p className="text-xs text-ink-500">
            One row per sub-task. The provider, latency, cost, and quality
            score are the same numbers we use to refresh rankings.
          </p>
        </div>
        <div className="text-right text-xs text-ink-400">
          <div>
            confidence{" "}
            <span className="font-semibold text-ink-700">
              {confidence.toFixed(2)}
            </span>
          </div>
          <div>
            cost <span className="font-semibold text-ink-700">${cost.toFixed(4)}</span>
          </div>
        </div>
      </header>

      <ol className="mt-4 space-y-2">
        {subResults.map((result, index) => (
          <SubTaskRow key={index} ordinal={index + 1} result={result} />
        ))}
      </ol>

      {records.length > 0 && (
        <details className="mt-4 text-xs text-ink-500">
          <summary className="cursor-pointer text-sm font-medium text-ink-700">
            Stitched output ({records.length} record
            {records.length === 1 ? "" : "s"})
          </summary>
          <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-ink-50 p-3 text-[11px] leading-snug text-ink-700">
            {JSON.stringify(records, null, 2)}
          </pre>
        </details>
      )}
    </section>
  );
}

function SubTaskRow({
  ordinal,
  result,
}: {
  ordinal: number;
  result: SubResult;
}) {
  const [open, setOpen] = useState(false);
  const succeeded = Boolean(result.succeeded ?? false);
  const quality = result.score?.quality_score;
  const latency = result.response?.latency_ms;
  const cost = result.response?.cost_usd;
  return (
    <li className="rounded-lg border border-ink-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full items-start justify-between gap-3 px-3 py-2 text-left transition hover:bg-ink-50"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-400">#{ordinal}</span>
            {/* Each field below is typed `unknown` to match the
                backend's permissive Pydantic shapes. Pre-fix
                (2026-05-20 sweep), `String(unknown)` would render
                `[object Object]` for non-string truthy values and an
                empty `<code></code>` for missing provider_ids. Guard
                with explicit string checks and a typographic fallback. */}
            <span className="tag-info">
              {typeof result.capability === "string" && result.capability
                ? result.capability
                : "—"}
            </span>
            {typeof result.description === "string" && result.description && (
              <span className="truncate text-sm text-ink-700">
                {result.description}
              </span>
            )}
          </div>
          <div className="mt-1 text-xs text-ink-500">
            via{" "}
            <code className="rounded bg-ink-100 px-1.5 py-0.5">
              {typeof result.provider_id === "string" && result.provider_id
                ? result.provider_id
                : "—"}
            </code>
            {quality !== undefined && (
              <span className="ml-2">quality {Number(quality).toFixed(2)}</span>
            )}
            {latency !== undefined && (
              <span className="ml-2">{Number(latency)}ms</span>
            )}
            {cost !== undefined && (
              <span className="ml-2">${Number(cost).toFixed(4)}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={succeeded ? "tag-success" : "tag-warn"}>
            {succeeded ? "ok" : "refused"}
          </span>
          <span className="text-ink-400">{open ? "−" : "+"}</span>
        </div>
      </button>

      {open && (
        <div className="space-y-2 border-t border-ink-100 px-3 py-3 text-xs">
          {Boolean(result.refusal_reason) && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-amber-800">
              <span className="font-semibold">Refusal:</span>{" "}
              {String(result.refusal_reason)}
            </div>
          )}
          {result.response?.output !== undefined && (
            <pre className="max-h-48 overflow-auto rounded-md bg-ink-50 p-2 text-[11px] text-ink-700">
              {JSON.stringify(result.response.output, null, 2)}
            </pre>
          )}
        </div>
      )}
    </li>
  );
}
