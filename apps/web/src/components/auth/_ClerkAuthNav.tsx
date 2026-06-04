"use client";

import { UserButton, useAuth } from "@clerk/nextjs";

/**
 * Inner component for the header auth slot.
 *
 * Lives in its own file so AuthNav can dynamic-import it (and skip the
 * @clerk/nextjs bundle entirely when Clerk isn't configured).
 *
 * Clerk 7 removed the `<SignedIn>` / `<SignedOut>` control components
 * from `@clerk/nextjs`; we use `useAuth()` directly to branch on auth
 * state, which keeps the bundle smaller and gives us explicit loading
 * handling.
 */
export default function ClerkAuthNav() {
  const { isLoaded, isSignedIn } = useAuth();
  if (!isLoaded) {
    return null;
  }
  if (!isSignedIn) {
    return (
      <a
        href="/sign-in"
        className="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-medium text-ink-700 hover:border-ink-500 hover:text-ink-900"
      >
        Sign in
      </a>
    );
  }
  return (
    <div className="flex items-center gap-3">
      <a href="/account" className="text-sm text-ink-600 hover:text-ink-900">
        Account
      </a>
      <UserButton />
    </div>
  );
}
