"use client";

/**
 * Recipe download buttons (Sprint 4 T1-A4).
 *
 * Rendered on the /goal results page once the API has returned a
 * plan with `recipes`. Each button is a direct GET to /recipe/export
 * — the browser handles the download via the Content-Disposition
 * header on the server response.
 *
 * Why direct anchor links (not fetch + blob): the API already sets
 * the right Content-Type + Content-Disposition. A plain <a download>
 * works in every browser, doesn't require extra fetch round-trips,
 * and keeps the download URL shareable (the user can right-click ->
 * "Copy link" and paste into a teammate's chat).
 */

import { useMemo } from "react";

import { apiBaseUrl, type RecipeCoverage, type RecipeDownload } from "@/lib/api";

const FORMAT_HINTS: Record<string, string> = {
  claude_desktop_json:
    "Merge into ~/Library/Application Support/Claude/claude_desktop_config.json",
  cursor_prompt: "Merge into .cursor/mcp.json + paste the system prompt template",
  n8n_json: "Import JSON into n8n via 'Import from File'",
  markdown: "Human-readable runbook; safe to share",
  cli: "Bash script with step-gated prompts; chmod +x then run",
};

const FORMAT_ICON: Record<string, string> = {
  claude_desktop_json: "Claude",
  cursor_prompt: "Cursor",
  n8n_json: "n8n",
  markdown: "MD",
  cli: "CLI",
};

const HOST_FORMATS = new Set(["claude_desktop_json", "cursor_prompt", "n8n_json", "cli"]);

export type RecipeDownloadButtonsProps = {
  goalId: string | null | undefined;
  recipes: RecipeDownload[] | null | undefined;
};

export function RecipeDownloadButtons({
  goalId,
  recipes,
}: RecipeDownloadButtonsProps) {
  const items = useMemo(() => {
    if (!recipes || recipes.length === 0) return [];
    return recipes.map((entry) => ({
      ...entry,
      // The backend emits a relative URL; resolve it against the
      // configured API base so the link works both in dev (different
      // ports) and behind a reverse proxy.
      absolute_url: buildAbsoluteUrl(entry.download_url),
      hint: FORMAT_HINTS[entry.format] ?? "",
      icon: FORMAT_ICON[entry.format] ?? entry.format.slice(0, 3).toUpperCase(),
      disabled: isDisabledForCoverage(entry),
    }));
  }, [recipes]);

  if (!goalId || items.length === 0) {
    return null;
  }
  const coverage = firstCoverage(items);
  const status = coverage?.status ?? "unknown";
  const hasGaps = Boolean(coverage && coverage.gap_count > 0);

  return (
    <section
      aria-labelledby="recipe-download-heading"
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
    >
      <header className="mb-3 flex items-baseline justify-between gap-2">
        <h2 id="recipe-download-heading" className="text-base font-semibold text-slate-900">
          Download recipe
        </h2>
        <span className="text-xs text-slate-500">
          goal_id <code className="text-slate-700">{goalId}</code> · cached 7 days
        </span>
      </header>
      <p className="mb-4 text-sm text-slate-600">
        PlanMyAgents does not execute on your behalf. Download the recipe in the
        format you prefer and run it in your own environment with your own
        credentials.
      </p>
      {coverage && (
        <div
          className={`mb-4 rounded-xl border px-3 py-2 text-sm ${
            status === "complete"
              ? "border-emerald-200 bg-emerald-50 text-emerald-900"
              : "border-amber-200 bg-amber-50 text-amber-900"
          }`}
        >
          <p className="font-medium">
            Recipe coverage: {coverage.exportable_step_count}/{coverage.step_count} exportable
            steps ({statusLabel(status)})
          </p>
          {hasGaps && (
            <p className="mt-1 text-xs">
              {coverage.gap_count} step{coverage.gap_count === 1 ? "" : "s"} will render as
              gap notes. Host-specific downloads are disabled when nothing is
              exportable.
            </p>
          )}
        </div>
      )}
      <ul className="grid gap-2 sm:grid-cols-2">
        {items.map((entry) => (
          <li key={entry.format}>
            {entry.disabled ? (
              <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-100 px-3 py-2 opacity-70">
                <RecipeIcon label={entry.icon} />
                <RecipeText
                  label={entry.label}
                  detail="Disabled: this recipe has no exportable host steps. Use Markdown as a gap runbook."
                />
              </div>
            ) : (
              <a
                href={entry.absolute_url}
                className="group flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 transition-colors hover:border-slate-400 hover:bg-white"
                download
              >
                <RecipeIcon label={entry.icon} />
                <RecipeText label={entry.label} detail={entry.hint || entry.format} link />
              </a>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function RecipeIcon({ label }: { label: string }) {
  return (
              <span className="inline-flex h-9 w-12 items-center justify-center rounded-md border border-slate-300 bg-white text-[10px] font-semibold uppercase tracking-wide text-slate-700">
      {label}
              </span>
  );
}

function RecipeText({
  label,
  detail,
  link = false,
}: {
  label: string;
  detail: string;
  link?: boolean;
}) {
  return (
              <span className="flex flex-col">
      <span className={`text-sm font-medium text-slate-900 ${link ? "group-hover:underline" : ""}`}>
        {label}
                </span>
                <span className="text-xs text-slate-500">
        {detail}
                </span>
              </span>
  );
}

function firstCoverage(items: Array<RecipeDownload & { coverage?: RecipeCoverage }>) {
  return items.find((item) => item.coverage)?.coverage;
}

function isDisabledForCoverage(entry: RecipeDownload) {
  return (
    entry.coverage?.status === "gap_only" &&
    HOST_FORMATS.has(entry.format) &&
    entry.format !== "markdown"
  );
}

function statusLabel(status: string) {
  if (status === "complete") return "complete";
  if (status === "partial") return "partial";
  if (status === "gap_only") return "gap-only runbook";
  if (status === "empty") return "empty";
  return status;
}

function buildAbsoluteUrl(path: string): string {
  if (!path) return path;
  if (/^https?:\/\//i.test(path)) return path;
  const base = (apiBaseUrl() || "").replace(/\/+$/, "");
  if (!base) return path; // SSR-safe fallback: relative URL still works in browser
  return `${base}${path.startsWith("/") ? "" : "/"}${path}`;
}
