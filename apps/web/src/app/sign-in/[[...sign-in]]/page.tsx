/**
 * Hosted Clerk sign-in catch-all route.
 *
 * Clerk renders its own polished sign-in widget inside this page; we
 * just provide the slot. The `[[...sign-in]]` catch-all is required
 * by Clerk so its flow can navigate through multiple steps (email →
 * code → MFA → done) without losing URL state.
 *
 * When Clerk is not yet configured (NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
 * missing) the page renders an explicit placeholder rather than
 * throwing.
 */

import { isClerkEnabled } from "@/lib/clerk";

export default async function SignInPage() {
  if (!isClerkEnabled) {
    return (
      <div className="mx-auto max-w-md rounded-2xl border border-ink-200 bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-ink-900">
          Sign in is not yet configured
        </h1>
        <p className="mt-2 text-sm text-ink-600">
          The Pro tier is being wired up. Set{" "}
          <code className="rounded bg-ink-100 px-1 py-0.5 text-xs">
            NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
          </code>{" "}
          and restart to enable account-backed features.
        </p>
      </div>
    );
  }
  const { SignIn } = await import("@clerk/nextjs");
  return (
    <div className="flex justify-center py-10">
      <SignIn
        path="/sign-in"
        routing="path"
        signUpUrl="/sign-up"
        forceRedirectUrl="/account"
      />
    </div>
  );
}
