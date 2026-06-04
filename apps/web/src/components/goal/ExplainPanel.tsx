"use client";

import { useState } from "react";

import { type GoalExplainResponse, submitGoalExplain } from "@/lib/api";

/**
 * Renders the on-demand "Why did the planner pick this?" affordance:
 * a single button that, when clicked, re-runs the planner with
 * chain-of-thought ("thinking") output enabled and shows the model's
 * reasoning trace next to the JSON answer it produced.
 *
 * Why this is gated behind a button instead of always-on:
 *   - Thinking mode adds 60-180s per call on local Apple Silicon
 *     (the 27B Qwen3.x model emits a multi-thousand-token reasoning
 *     trace before the JSON answer).
 *   - 99% of users never need the trace — they just want the plan.
 *   - Letting users opt-in keeps the /goal hot path fast while
 *     making the "why" inspectable when a plan looks suspicious.
 *
 * The panel also surfaces ``model_supports_thinking=false`` for
 * older / non-thinking models (qwen2.5, llama3.x) so the user knows
 * an empty trace is "model can't do this", not "API broke".
 */
export function ExplainPanel({ goal }: { goal: string }) {
  const [status, setStatus] = useState<"idle" | "loading" | "ok" | "error">(
    "idle",
  );
  const [explanation, setExplanation] = useState<GoalExplainResponse | null>(
    null,
  );
  const [error, setError] = useState<{ message: string; remediation: string[] } | null>(
    null,
  );
  const [open, setOpen] = useState(false);

  const handleClick = async () => {
    if (status === "loading") return;
    setStatus("loading");
    setError(null);
    setExplanation(null);
    setOpen(true);
    try {
      const data = await submitGoalExplain({ goal });
      setExplanation(data);
      setStatus("ok");
    } catch (err) {
      const apiErr = err as {
        detail?: string;
        detailObject?: Record<string, unknown> | null;
      };
      const detailObject = apiErr.detailObject ?? null;
      const remediation = Array.isArray(detailObject?.remediation)
        ? ((detailObject?.remediation as unknown[]).filter(
            (s) => typeof s === "string",
          ) as string[])
        : [];
      setError({
        message: apiErr.detail ?? "Failed to fetch reasoning trace.",
        remediation,
      });
      setStatus("error");
    }
  };

  return (
    <section className="surface space-y-3 p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold text-ink-900">
            Why did the planner pick this?
          </h3>
          <p className="text-xs text-ink-500">
            Re-runs the planner with chain-of-thought enabled.
            Adds 60-180s on local hardware — only fetched when you ask.
          </p>
        </div>
        <button
          type="button"
          onClick={handleClick}
          disabled={status === "loading"}
          className="rounded-lg border border-ink-300 bg-white px-3 py-1.5 text-xs font-medium text-ink-800 hover:border-ink-500 hover:text-ink-900 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {status === "loading"
            ? "Generating reasoning... (60-180s)"
            : status === "ok"
              ? "Refresh reasoning"
              : "Show reasoning"}
        </button>
      </header>

      {open && status === "loading" && (
        <div className="rounded-lg border border-ink-200 bg-ink-50 px-3 py-2 text-xs text-ink-700">
          Asking the model to reason out loud. This takes 60-180s on
          local hardware because the model generates a multi-thousand-
          token reasoning trace before the answer. Fast-path /goal calls
          skip this entirely.
        </div>
      )}

      {open && status === "error" && error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-900">
          <div className="font-semibold">Reasoning unavailable</div>
          <p className="mt-1">{error.message}</p>
          {error.remediation.length > 0 && (
            <ul className="mt-2 list-disc space-y-0.5 pl-5">
              {error.remediation.map((step, idx) => (
                <li key={idx}>{step}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {open && status === "ok" && explanation && (
        <ExplanationBody explanation={explanation} />
      )}
    </section>
  );
}

function ExplanationBody({ explanation }: { explanation: GoalExplainResponse }) {
  const supportsThinking = explanation.model_supports_thinking;
  const seconds = (explanation.duration_ms / 1000).toFixed(1);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-600">
        <span className="rounded border border-ink-200 bg-white px-2 py-0.5 font-medium">
          model: <code>{explanation.model}</code>
        </span>
        <span className="rounded border border-ink-200 bg-white px-2 py-0.5">
          {seconds}s
        </span>
        {supportsThinking ? (
          <span className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-emerald-800">
            reasoning trace captured
          </span>
        ) : (
          <span className="rounded border border-amber-300 bg-amber-50 px-2 py-0.5 text-amber-800">
            model has no reasoning trace
          </span>
        )}
      </div>

      {!supportsThinking && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          <code>{explanation.model}</code> is a non-reasoning model — it
          produces an answer directly without an intermediate
          chain-of-thought. To inspect actual planner reasoning, switch
          the planner to a Qwen3.x model
          (<code>PLANMYAGENTS_QWEN_MODEL=qwen3.5:35b</code>) and retry.
        </div>
      )}

      {supportsThinking && (
        <details className="rounded-lg border border-ink-200 bg-white" open>
          <summary className="cursor-pointer list-none border-b border-ink-100 px-3 py-2 text-xs font-semibold text-ink-700 hover:text-ink-900">
            <span className="mr-1 text-ink-400">▾</span>
            Reasoning trace (
            {explanation.thinking.split(/\s+/).filter(Boolean).length} words)
          </summary>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-ink-800">
            {explanation.thinking}
          </pre>
        </details>
      )}

      <details className="rounded-lg border border-ink-200 bg-white">
        <summary className="cursor-pointer list-none border-b border-ink-100 px-3 py-2 text-xs font-semibold text-ink-700 hover:text-ink-900">
          <span className="mr-1 text-ink-400">▸</span>
          Plan this re-run produced
        </summary>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-ink-800">
          {explanation.plan
            ? JSON.stringify(explanation.plan, null, 2)
            : explanation.content}
        </pre>
      </details>

      <p className="text-[11px] italic text-ink-500">
        This is a fresh planner run. The plan above may differ slightly
        from the original /goal response because the model now has its
        reasoning context available. Compare to confirm the original
        plan still makes sense given this reasoning.
      </p>
    </div>
  );
}
