/**
 * /account — Pro-tier account dashboard.
 *
 * Renders three blocks:
 *
 *   1. **Identity card**     — current Clerk identity + plan.
 *   2. **Saved recipes list** — Pro perk; lets the user replay or delete
 *      a recipe they previously saved from the /goal results page.
 *   3. **Upgrade CTA**        — when on the free plan, a single-button
 *      handoff to Stripe Checkout.
 *
 * Placeholder-safe: when Clerk isn't configured we render an
 * explainer card rather than crashing the page or showing a fake
 * sign-in widget.
 */

import { isClerkEnabled } from "@/lib/clerk";
import { AccountDashboard } from "@/components/account/AccountDashboard";

export const dynamic = "force-dynamic";

export default function AccountPage() {
  if (!isClerkEnabled) {
    return (
      <div className="mx-auto max-w-xl rounded-2xl border border-ink-200 bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-ink-900">
          Account features coming soon
        </h1>
        <p className="mt-2 text-sm text-ink-600">
          The Pro tier — saved recipes, Stripe-backed Pro plan, and
          per-account history — is wired up on the backend but Clerk
          isn&apos;t configured yet on this deployment.
        </p>
        <p className="mt-3 text-sm text-ink-600">
          Set{" "}
          <code className="rounded bg-ink-100 px-1 py-0.5 text-xs">
            NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
          </code>{" "}
          (and the matching backend env vars from{" "}
          <code className="rounded bg-ink-100 px-1 py-0.5 text-xs">
            .env.example
          </code>
          ) to enable accounts.
        </p>
      </div>
    );
  }
  return <AccountDashboard />;
}
