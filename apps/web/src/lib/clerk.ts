/**
 * Clerk feature-flag helpers.
 *
 * The Pro tier is placeholder-safe: when no
 * `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` is set, we render the rest of the
 * app without ClerkProvider. This keeps the discovery / leaderboards /
 * goal pages working for anonymous users in dev while the founder is
 * still wiring their Clerk instance.
 *
 * `isClerkEnabled` is evaluated at module load. Next.js inlines
 * `NEXT_PUBLIC_*` env vars at build time, so this works in both server
 * and client components without a runtime fetch.
 */

export const CLERK_PUBLISHABLE_KEY: string =
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "";

export const isClerkEnabled: boolean = Boolean(CLERK_PUBLISHABLE_KEY);
