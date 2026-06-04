"use client";

import type { GoalResponse } from "@/lib/api";

/**
 * One-click "download the full /goal response as JSON" button.
 *
 * Useful when the page-rendered summary loses fidelity (nested
 * provenance, raw scout output, etc.). The downloaded file name is
 * timestamped so users can compare two runs side-by-side without
 * overwriting.
 */
export function DownloadResponseButton({
  response,
}: {
  response: GoalResponse;
}) {
  const handleDownload = () => {
    const blob = new Blob([JSON.stringify(response, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    anchor.download = `planmyagents-goal-${stamp}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  };
  return (
    <div>
      <button
        type="button"
        onClick={handleDownload}
        className="rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-xs font-medium text-ink-700 transition hover:border-ink-400 hover:text-ink-900"
      >
        Download JSON
      </button>
    </div>
  );
}
