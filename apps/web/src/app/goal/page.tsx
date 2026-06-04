"use client";

import Link from "next/link";
import { useState } from "react";

import { type GoalResponse, submitGoal } from "@/lib/api";
import { LiveEvidenceStrip } from "@/components/LiveEvidenceStrip";
import { PlanningRefusalCard } from "@/components/goal/PlanningRefusalCard";
import { ResponseLayout } from "@/components/goal/ResponseLayout";

const EXAMPLE_GOALS = [
  "Verify the email jane.doe@acme.com",
  "Find a CTO email at acme.com",
  "Find an MCP server that lists Postgres tables",
];

type SubmitStatus = "idle" | "loading" | "ok" | "error";

export default function GoalPage() {
  const [goal, setGoal] = useState("");
  const [execute, setExecute] = useState(false);
  const [status, setStatus] = useState<SubmitStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  // Carries the structured 503 PlanningUnavailableError payload (or
  // any other object-shaped error.detail) so the UI can render
  // remediation steps + LLM tier provenance instead of a raw string.
  const [errorDetail, setErrorDetail] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [response, setResponse] = useState<GoalResponse | null>(null);
  // The exact goal text that produced the current response. Tracked
  // separately from `goal` (the editable textarea) so the "Why did
  // the planner pick this?" explain button always re-runs the prompt
  // the user actually submitted, even if they have since edited the
  // textbox to compose a follow-up.
  const [submittedGoal, setSubmittedGoal] = useState<string>("");

  const submit = async (overrides?: { goal?: string; execute?: boolean }) => {
    const finalGoal = (overrides?.goal ?? goal).trim();
    if (!finalGoal) return;
    const finalExecute = overrides?.execute ?? execute;
    setStatus("loading");
    setError(null);
    setErrorDetail(null);
    setResponse(null);
    setSubmittedGoal(finalGoal);
    try {
      const data = await submitGoal({ goal: finalGoal, execute: finalExecute });
      setResponse(data);
      setStatus("ok");
    } catch (err) {
      const apiErr = err as {
        detail?: string;
        detailObject?: Record<string, unknown> | null;
      };
      const detail = apiErr.detail ?? String(err);
      setError(detail);
      setErrorDetail(apiErr.detailObject ?? null);
      setStatus("error");
    }
  };

  const handleQuickFill = (example: string) => {
    setGoal(example);
  };

  const handleRetryWithExecute = () => {
    setExecute(true);
    submit({ execute: true });
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-ink-900">
          What outcome do you want?
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-ink-600">
          PlanMyAgents plans the required capabilities, routes through tested
          and runnable providers when one exists, and refuses honestly when
          nothing is ready yet. Browse{" "}
          <Link
            href="/categories"
            className="text-accent-600 hover:underline"
          >
            categories
          </Link>
          ,{" "}
          <Link
            href="/leaderboards"
            className="text-accent-600 hover:underline"
          >
            leaderboards
          </Link>
          , or{" "}
          <Link href="/search" className="text-accent-600 hover:underline">
            search
          </Link>{" "}
          to inspect the underlying providers.
        </p>
      </header>

      <LiveEvidenceStrip />

      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className="surface space-y-4 p-5"
      >
        <label className="block">
          <span className="text-xs uppercase tracking-wide text-ink-400">
            Goal
          </span>
          <textarea
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            rows={3}
            placeholder="e.g. Verify the email jane.doe@acme.com"
            className="mt-1 w-full rounded-lg border border-ink-200 px-3 py-2 text-sm focus:border-accent-500 focus:outline-none"
            required
          />
        </label>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-ink-400">Try:</span>
          {EXAMPLE_GOALS.map((example) => (
            <button
              type="button"
              key={example}
              onClick={() => handleQuickFill(example)}
              className="rounded-full border border-ink-200 bg-white px-3 py-1 text-xs text-ink-700 transition hover:border-ink-400 hover:text-ink-900"
            >
              {example}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-sm text-ink-600">
            <input
              type="checkbox"
              checked={execute}
              onChange={(event) => setExecute(event.target.checked)}
              className="h-4 w-4 rounded border-ink-300 text-accent-600 focus:ring-accent-500"
            />
            Execute the plan if every sub-task is routable
          </label>
          <button
            type="submit"
            disabled={status === "loading"}
            className="h-10 rounded-lg bg-ink-900 px-4 text-sm font-medium text-white hover:bg-ink-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {status === "loading" ? "Planning..." : "Submit"}
          </button>
        </div>
      </form>

      {status === "error" && errorDetail && (
        <PlanningRefusalCard detail={errorDetail} />
      )}

      {(response || status === "error") && (
        <ResponseLayout
          response={response}
          errorDetail={status === "error" ? error : null}
          onRetryExecute={handleRetryWithExecute}
          submittedGoal={submittedGoal}
        />
      )}
    </div>
  );
}
