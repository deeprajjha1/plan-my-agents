"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth, useUser } from "@clerk/nextjs";

import {
  type AccountMe,
  type SavedRecipe,
  createBillingCheckoutSession,
  deleteSavedRecipe,
  fetchAccountMe,
  listSavedRecipes,
  apiBaseUrl,
} from "@/lib/api";

/**
 * Account dashboard — identity, plan, saved recipes, billing CTA.
 *
 * All API calls are forwarded with the Clerk session JWT via
 * `useAuth().getToken()`. The hook returns `null` until Clerk has
 * loaded so we render a soft loading state instead of flashing the
 * empty list.
 */
export function AccountDashboard() {
  const { isLoaded: isAuthLoaded, isSignedIn, getToken } = useAuth();
  const { user } = useUser();

  const [recipes, setRecipes] = useState<SavedRecipe[]>([]);
  const [loadingRecipes, setLoadingRecipes] = useState(true);
  const [recipesError, setRecipesError] = useState<string | null>(null);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  // Track per-recipe in-flight deletes so we can both disable the
  // button (no double-submit) and signal "Deleting…" to the user.
  // Backed by a Set rather than a single boolean because multiple
  // deletes can be in-flight concurrently; reusing one boolean would
  // race.
  const [deletingIds, setDeletingIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  // /account/me is the source-of-truth for plan/email — Clerk
  // publicMetadata.plan is set by the Stripe webhook handler with a
  // small lag, so reading it directly meant a successful Pro upgrade
  // could render as "FREE" for tens of seconds while the webhook
  // round-tripped. Fall back to Clerk only while the API is
  // unreachable.
  const [account, setAccount] = useState<AccountMe | null>(null);

  const refreshRecipes = useCallback(async () => {
    if (!isSignedIn) {
      setRecipes([]);
      setLoadingRecipes(false);
      return;
    }
    setLoadingRecipes(true);
    setRecipesError(null);
    try {
      const token = await getToken();
      const payload = await listSavedRecipes(token);
      setRecipes(payload.recipes);
    } catch (err) {
      setRecipesError(extractDetail(err));
    } finally {
      setLoadingRecipes(false);
    }
  }, [getToken, isSignedIn]);

  useEffect(() => {
    if (isAuthLoaded) {
      void refreshRecipes();
    }
  }, [isAuthLoaded, refreshRecipes]);

  // Pull the backend's view of plan/email so post-checkout upgrades
  // surface immediately rather than waiting for Clerk metadata to
  // propagate. Best-effort: if /account/me fails (API down, no token)
  // we fall back to Clerk in the render path below.
  useEffect(() => {
    if (!isAuthLoaded || !isSignedIn) {
      setAccount(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const token = await getToken();
        const me = await fetchAccountMe(token);
        if (!cancelled) setAccount(me);
      } catch {
        if (!cancelled) setAccount(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken, isAuthLoaded, isSignedIn]);

  const handleUpgrade = useCallback(async () => {
    setCheckoutError(null);
    setRedirecting(true);
    try {
      const token = await getToken();
      const session = await createBillingCheckoutSession(token);
      window.location.href = session.url;
    } catch (err) {
      setCheckoutError(extractDetail(err));
      setRedirecting(false);
    }
  }, [getToken]);

  const handleDelete = useCallback(
    async (recipeId: string) => {
      // Guard against double-submit: rapid clicks pre-fix would fire
      // multiple DELETE /recipes/{id} requests and could surface a
      // confusing 404 on the second one when the first had already
      // removed the row.
      setDeletingIds((current) => {
        if (current.has(recipeId)) return current;
        const next = new Set(current);
        next.add(recipeId);
        return next;
      });
      try {
        const token = await getToken();
        await deleteSavedRecipe(token, recipeId);
        setRecipes((current) => current.filter((r) => r.recipe_id !== recipeId));
      } catch (err) {
        setRecipesError(extractDetail(err));
      } finally {
        setDeletingIds((current) => {
          if (!current.has(recipeId)) return current;
          const next = new Set(current);
          next.delete(recipeId);
          return next;
        });
      }
    },
    [getToken],
  );

  if (!isAuthLoaded) {
    return <PageShell title="Account">Loading…</PageShell>;
  }
  if (!isSignedIn) {
    return (
      <PageShell title="Account">
        <p className="text-sm text-ink-600">
          You must <a href="/sign-in" className="text-blue-600 hover:underline">sign in</a> to see your account.
        </p>
      </PageShell>
    );
  }

  // Prefer the backend's truth; fall back to Clerk metadata when the
  // API call hasn't landed yet (or failed). This handles the
  // post-upgrade window where Stripe webhook → MarketplaceStore has
  // committed but Clerk metadata hasn't synced.
  const apiPlan =
    account && "authenticated" in account && account.authenticated
      ? account.plan
      : null;
  const clerkPlan = user?.publicMetadata?.plan
    ? String(user.publicMetadata.plan)
    : null;
  const planLabel = apiPlan ?? clerkPlan ?? "free";
  const onProPlan = planLabel === "pro" || planLabel === "enterprise";

  return (
    <PageShell title="Account">
      <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm">
        <h2 className="text-base font-semibold text-ink-900">Identity</h2>
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm text-ink-700 sm:grid-cols-3">
          <div>
            <dt className="text-xs uppercase tracking-wide text-ink-400">Email</dt>
            <dd className="mt-0.5">{user?.primaryEmailAddress?.emailAddress ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-ink-400">Clerk user id</dt>
            <dd className="mt-0.5 font-mono text-xs text-ink-500">{user?.id}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-ink-400">Plan</dt>
            <dd className="mt-0.5">
              <span
                className={
                  onProPlan
                    ? "rounded-md bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-900"
                    : "rounded-md bg-ink-100 px-2 py-0.5 text-xs font-semibold text-ink-700"
                }
              >
                {planLabel.toUpperCase()}
              </span>
            </dd>
          </div>
        </dl>
      </section>

      {!onProPlan && (
        <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm">
          <h2 className="text-base font-semibold text-ink-900">Upgrade to Pro</h2>
          <p className="mt-2 text-sm text-ink-600">
            Pro adds unlimited saved recipes, higher rate limits, and early
            access to Cursor + n8n recipe formats. Billing is handled by
            Stripe. Test mode is the default in dev — no card will be
            charged.
          </p>
          <button
            type="button"
            onClick={handleUpgrade}
            disabled={redirecting}
            className="mt-4 rounded-md bg-ink-900 px-4 py-2 text-sm font-medium text-white hover:bg-ink-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {redirecting ? "Redirecting…" : "Upgrade — Stripe Checkout"}
          </button>
          {checkoutError && (
            <p className="mt-2 text-xs text-rose-600" role="alert">
              {checkoutError}
            </p>
          )}
        </section>
      )}

      <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm">
        <header className="flex items-baseline justify-between gap-2">
          <h2 className="text-base font-semibold text-ink-900">Saved recipes</h2>
          <button
            type="button"
            onClick={() => void refreshRecipes()}
            className="text-xs text-ink-500 hover:text-ink-900"
          >
            Refresh
          </button>
        </header>
        {loadingRecipes ? (
          <p className="mt-3 text-sm text-ink-500">Loading…</p>
        ) : recipesError ? (
          <p className="mt-3 text-sm text-rose-600" role="alert">
            {recipesError}
          </p>
        ) : recipes.length === 0 ? (
          <p className="mt-3 text-sm text-ink-500">
            No saved recipes yet. Run a goal and click{" "}
            <strong>Save this recipe</strong> on the results page.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-ink-100">
            {recipes.map((recipe) => (
              <li
                key={recipe.recipe_id}
                className="flex items-start justify-between gap-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink-900">
                    {recipe.goal}
                  </p>
                  <p className="mt-0.5 text-xs text-ink-500">
                    Format <code className="text-ink-700">{recipe.format}</code> ·
                    saved {recipe.created_at.replace(/\.\d+/, "").replace("T", " ")}
                  </p>
                  {recipe.recipe_coverage &&
                    (recipe.recipe_coverage.step_count > 0 ? (
                      <p className="mt-1 text-xs text-ink-500">
                        <span className="rounded-full border border-ink-200 px-2 py-0.5 text-ink-700">
                          {coverageLabel(recipe.recipe_coverage.status)}
                        </span>{" "}
                        {recipe.recipe_coverage.exportable_step_count}/
                        {recipe.recipe_coverage.step_count} exportable steps
                      </p>
                    ) : (
                      // Empty plans should not render "0/0 exportable
                      // steps" — it's confusing rather than informative.
                      // This also avoids a divide-by-zero in any future
                      // percentage rendering.
                      <p className="mt-1 text-xs text-ink-500">
                        <span className="rounded-full border border-ink-200 px-2 py-0.5 text-ink-700">
                          {coverageLabel(recipe.recipe_coverage.status)}
                        </span>{" "}
                        no sub-tasks
                      </p>
                    ))}
                  {recipe.notes && (
                    <p className="mt-1 text-xs text-ink-600">{recipe.notes}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <a
                    href={absoluteUrl(recipe.download_url)}
                    className="rounded-md border border-ink-300 px-3 py-1 text-xs text-ink-700 hover:border-ink-500 hover:text-ink-900"
                    download
                  >
                    Download
                  </a>
                  <button
                    type="button"
                    onClick={() => void handleDelete(recipe.recipe_id)}
                    disabled={deletingIds.has(recipe.recipe_id)}
                    className="rounded-md border border-rose-200 px-3 py-1 text-xs text-rose-700 hover:border-rose-500 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {deletingIds.has(recipe.recipe_id) ? "Deleting…" : "Delete"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </PageShell>
  );
}

function PageShell({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-ink-900">{title}</h1>
      {children}
    </div>
  );
}

function coverageLabel(status: string) {
  if (status === "complete") return "Runnable";
  if (status === "partial") return "Partial";
  if (status === "gap_only") return "Gap-only";
  if (status === "empty") return "Empty";
  return status;
}

function extractDetail(err: unknown): string {
  if (err && typeof err === "object" && "detail" in err) {
    const detail = (err as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return "Something went wrong. Try again.";
}

function absoluteUrl(path: string): string {
  if (!path) return path;
  if (/^https?:\/\//i.test(path)) return path;
  const base = (apiBaseUrl() || "").replace(/\/+$/, "");
  if (!base) return path;
  return `${base}${path.startsWith("/") ? "" : "/"}${path}`;
}
