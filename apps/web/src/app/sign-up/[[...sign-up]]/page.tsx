/**
 * Hosted Clerk sign-up catch-all route. See sign-in/page.tsx for the
 * rationale on the catch-all path and the placeholder-safe fallback.
 */

import { isClerkEnabled } from "@/lib/clerk";

export default async function SignUpPage() {
  if (!isClerkEnabled) {
    return (
      <div className="mx-auto max-w-md rounded-2xl border border-ink-200 bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-ink-900">
          Sign up is not yet configured
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
  const { SignUp } = await import("@clerk/nextjs");
  return (
    <div className="flex justify-center py-10">
      <SignUp
        path="/sign-up"
        routing="path"
        signInUrl="/sign-in"
        forceRedirectUrl="/account"
      />
    </div>
  );
}
