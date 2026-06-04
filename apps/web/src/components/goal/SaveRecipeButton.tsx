"use client";

/**
 * Save-recipe widget — T1-B-6.
 *
 * Behaviour by auth state:
 *
 *   - Clerk not configured       → not rendered (placeholder-safe).
 *   - Clerk configured, signed-out → "Sign in to save" link.
 *   - Signed-in, recipe unsaved  → format dropdown + notes textarea + Save.
 *   - Signed-in, recipe saved    → success card with link to /account.
 *
 * The save POSTs to /recipes which looks up the cached plan by
 * goal_id and persists the full plan_payload. Subsequent downloads
 * re-render that payload through /recipe/export, so format choices
 * are not locked in at save time (the user can change their mind
 * later from /account).
 */

import { useState } from "react";

import type { RecipeDownload } from "@/lib/api";
import { isClerkEnabled } from "@/lib/clerk";
import { SaveRecipeForm } from "./SaveRecipeForm";

type SaveRecipeButtonProps = {
  goalId: string | null | undefined;
  recipes: RecipeDownload[] | null | undefined;
};

export function SaveRecipeButton({ goalId, recipes }: SaveRecipeButtonProps) {
  if (!isClerkEnabled || !goalId || !recipes || recipes.length === 0) {
    return null;
  }
  return <SaveRecipeWidget goalId={goalId} recipes={recipes} />;
}

function SaveRecipeWidget({
  goalId,
  recipes,
}: {
  goalId: string;
  recipes: RecipeDownload[];
}) {
  const [expanded, setExpanded] = useState(false);
  const coverage = recipes.find((recipe) => recipe.coverage)?.coverage;
  const isDiagnostic = coverage?.status === "gap_only";

  return (
    <aside className="rounded-2xl border border-blue-200 bg-blue-50/40 p-5">
      <header className="flex items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-blue-900">
          {isDiagnostic ? "Save diagnostic plan" : "Save this recipe"}
        </h2>
        {!expanded && (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          >
            Save
          </button>
        )}
      </header>
      {!expanded ? (
        <p className="mt-2 text-sm text-blue-900/80">
          {isDiagnostic
            ? "Keep this gap-only runbook in your account so you can revisit it when discovery improves."
            : "Keep this recipe in your account so you can come back and re-download it after the 7-day cache expires."}
        </p>
      ) : (
        <SaveRecipeForm
          goalId={goalId}
          recipes={recipes}
          onCancel={() => setExpanded(false)}
        />
      )}
    </aside>
  );
}
