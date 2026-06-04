"use client";

/**
 * Inner save form — uses Clerk's useAuth hook so it MUST live inside
 * <ClerkProvider>. Kept separate from SaveRecipeButton so the latter
 * can be safely rendered when Clerk is disabled (no hook usage).
 */

import { useState } from "react";
import { useAuth, useUser } from "@clerk/nextjs";

import { saveRecipe, type RecipeDownload } from "@/lib/api";

type SaveRecipeFormProps = {
  goalId: string;
  recipes: RecipeDownload[];
  onCancel: () => void;
};

type Status =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "error"; message: string }
  | { kind: "saved"; recipeId: string };

export function SaveRecipeForm({
  goalId,
  recipes,
  onCancel,
}: SaveRecipeFormProps) {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const { user } = useUser();
  const [format, setFormat] = useState(recipes[0]?.format ?? "markdown");
  const [notes, setNotes] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  if (!isLoaded) {
    return <p className="mt-3 text-sm text-blue-900/70">Loading…</p>;
  }
  if (!isSignedIn) {
    return (
      <div className="mt-3 space-y-2 text-sm text-blue-900/90">
        <p>
          <a href="/sign-in" className="font-medium underline">
            Sign in
          </a>{" "}
          to keep this recipe in your account.
        </p>
        <button
          type="button"
          onClick={onCancel}
          className="text-xs text-blue-700 hover:underline"
        >
          Not now
        </button>
      </div>
    );
  }

  if (status.kind === "saved") {
    return (
      <div className="mt-3 rounded-lg border border-emerald-200 bg-white p-3 text-sm">
        <p className="text-emerald-900">
          Saved as <code className="text-emerald-700">{status.recipeId}</code>.
          Visit{" "}
          <a href="/account" className="font-medium underline">
            /account
          </a>{" "}
          to see all your recipes.
        </p>
      </div>
    );
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus({ kind: "saving" });
    try {
      const token = await getToken();
      const saved = await saveRecipe(token, {
        goal_id: goalId,
        format,
        notes: notes.trim() || undefined,
      });
      setStatus({ kind: "saved", recipeId: saved.recipe_id });
    } catch (err) {
      const detail =
        err && typeof err === "object" && "detail" in err
          ? String((err as { detail?: unknown }).detail ?? "")
          : "";
      setStatus({
        kind: "error",
        message: detail || "Could not save. Please retry.",
      });
    }
  };

  return (
    <form onSubmit={handleSave} className="mt-3 space-y-3 text-sm text-blue-900">
      <p className="text-xs text-blue-900/70">
        Saving as <strong>{user?.primaryEmailAddress?.emailAddress ?? "you"}</strong>.
      </p>
      <label className="block">
        <span className="block text-xs font-medium uppercase tracking-wide text-blue-900/70">
          Default format
        </span>
        <select
          value={format}
          onChange={(e) => setFormat(e.target.value)}
          className="mt-1 w-full rounded-md border border-blue-300 bg-white px-2 py-1.5 text-sm"
        >
          {recipes.map((r) => (
            <option key={r.format} value={r.format}>
              {r.label}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="block text-xs font-medium uppercase tracking-wide text-blue-900/70">
          Notes <span className="text-blue-900/50">(optional)</span>
        </span>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          maxLength={2000}
          placeholder="e.g. used this for the sales-csv pipeline"
          className="mt-1 w-full rounded-md border border-blue-300 bg-white px-2 py-1.5 text-sm"
        />
      </label>
      {status.kind === "error" && (
        <p className="text-xs text-rose-600" role="alert">
          {status.message}
        </p>
      )}
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={status.kind === "saving"}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {status.kind === "saving" ? "Saving…" : "Save recipe"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-xs text-blue-700 hover:underline"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
