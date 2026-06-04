/**
 * ClerkOptionalProvider — wraps the app in <ClerkProvider> ONLY when
 * a Clerk publishable key is configured at build time.
 *
 * Rationale (T1-B placeholder safety): the founder is bringing up the
 * Pro tier incrementally. Before a Clerk dev instance exists, the
 * public surface area (discovery, leaderboards, /goal anonymous use)
 * must still ship. Forcing ClerkProvider would throw at app boot.
 *
 * When auth IS configured, this component is a thin pass-through to
 * the real <ClerkProvider>; when not, it renders children directly so
 * everything except /sign-in, /sign-up, /account keeps working.
 */

import type { ReactNode } from "react";

import { CLERK_PUBLISHABLE_KEY, isClerkEnabled } from "@/lib/clerk";

export function ClerkOptionalProvider({ children }: { children: ReactNode }) {
  if (!isClerkEnabled) {
    return <>{children}</>;
  }
  // Dynamic require avoids importing @clerk/nextjs from the server bundle
  // when the user has not set up Clerk yet — keeps SSR fast and avoids
  // any "missing publishableKey" runtime errors during local dev.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { ClerkProvider } = require("@clerk/nextjs");
  return (
    <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY}>
      {children}
    </ClerkProvider>
  );
}
