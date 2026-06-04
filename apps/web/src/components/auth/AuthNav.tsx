"use client";

/**
 * Header auth slot.
 *
 * Three states, depending on configuration + sign-in status:
 *
 *   1. Clerk not configured → small "Pro tier coming soon" badge
 *      (placeholder-safe so the header doesn't shift when keys land).
 *   2. Clerk configured, signed out → "Sign in" link to /sign-in.
 *   3. Clerk configured, signed in  → Clerk's <UserButton> + "Account" link.
 *
 * We dynamic-import Clerk hooks so the bundle doesn't pull them in
 * when Clerk is disabled.
 */

import dynamic from "next/dynamic";
import Link from "next/link";

import { isClerkEnabled } from "@/lib/clerk";

// Dynamic-imported inner component so bundlers don't trip when Clerk
// isn't installed in environments that strip optional deps.
const ClerkAuthNav = dynamic(() => import("./_ClerkAuthNav"), {
  ssr: false,
  loading: () => null,
});

export function AuthNav() {
  if (!isClerkEnabled) {
    return (
      <Link
        href="/account"
        title="Set NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY to enable the Pro tier"
        className="rounded-md border border-dashed border-ink-300 px-2 py-1 text-xs uppercase tracking-wide text-ink-400 hover:border-ink-500 hover:text-ink-700"
      >
        Pro tier: soon
      </Link>
    );
  }
  return <ClerkAuthNav />;
}
