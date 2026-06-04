/**
 * Shared display-format helpers used across the public pages.
 *
 * Lives separately from `lib/api.ts` to keep the API client free of
 * presentation concerns (the same backend types are sometimes
 * rendered in different shells — a goal page sub-task vs. a saved
 * recipe row in the account dashboard).
 *
 * History
 * -------
 * Extracted on 2026-05-20 as part of the pre-launch cleanup pass.
 * Prior to extraction `formatLabel` was duplicated **byte-identically
 * seven times** across:
 *
 *   - `app/categories/page.tsx`
 *   - `app/demand/page.tsx`
 *   - `app/dev/demand/page.tsx`
 *   - `app/discovery-gaps/page.tsx`
 *   - `app/leaderboards/page.tsx`
 *   - `app/leaderboards/[capability]/page.tsx`
 *   - `app/open-mcp-opportunities/page.tsx`
 *
 * Timestamp helpers were intentionally **not** consolidated in the same
 * pass — three pages render timestamps with subtly different formats
 * (ISO+UTC, locale, locale-compact) and merging them would change
 * pixels in ways that should land in their own focused diff.
 */

/**
 * Render a snake_case capability or status slug as a human title.
 *
 * `email_send` → `Email Send`. Leaves single-token ids unchanged
 * apart from initial-cap. Empty / whitespace input returns the input
 * verbatim so the caller never has to special-case "—" placeholders.
 */
export const formatLabel = (id: string): string =>
  id
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
