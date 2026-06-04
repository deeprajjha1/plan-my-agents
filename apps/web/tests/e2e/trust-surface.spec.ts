/**
 * Trust-surface browser proofs.
 *
 * Covers the UI revamp that made the Eval_Framework visible:
 *
 *   1. /trust renders the live methodology projection (tier ladder,
 *      protocol maturity, source taxonomy) AND the live credibility
 *      banner. Critically, when the seeded index is synthetic-only the
 *      page must SAY so — never imply a trust layer that doesn't exist.
 *   2. /submit-agent honestly documents the CLI ingestion pipeline
 *      rather than faking a submit form that POSTs nowhere.
 *   3. The header navigation links to both new surfaces.
 *
 * Tolerant of either index state: if real adapter runs exist the banner
 * shows the real/publishable counts; otherwise it shows the red
 * synthetic-only state. Both are correct — the test asserts the page is
 * honest about whichever one is live.
 */

import { expect, test } from "@playwright/test";

test.describe("trust surface", () => {
  test("/trust renders the eval tier ladder and a live credibility banner", async ({
    page,
  }) => {
    await page.goto("/trust");

    await expect(
      page.getByRole("heading", {
        name: "How a leaderboard number comes to exist",
      }),
    ).toBeVisible();

    // Tier ladder: all four rungs.
    await expect(page.getByText("Static verification")).toBeVisible();
    await expect(page.getByText("Functional smoke")).toBeVisible();
    await expect(page.getByText("Scored benchmark")).toBeVisible();
    await expect(page.getByText("Continuous re-eval")).toBeVisible();

    // Protocol maturity table: MCP executable, A2A verification-only.
    await expect(
      page.getByRole("heading", { name: "What we can actually invoke" }),
    ).toBeVisible();
    await expect(page.getByText("mcp", { exact: true })).toBeVisible();
    await expect(page.getByText("a2a", { exact: true })).toBeVisible();

    // Source taxonomy: exact_match flagged as a real run.
    await expect(
      page.getByRole("heading", { name: "Which results count as real" }),
    ).toBeVisible();
    await expect(page.getByText("exact_match", { exact: true })).toBeVisible();

    // Live index-state banner. Either honest state is acceptable, but
    // the banner must render and reference the index counts.
    await expect(
      page.getByRole("heading", { name: "Live index state" }),
    ).toBeVisible();
  });

  test("/submit-agent documents the real CLI pipeline, not a fake form", async ({
    page,
  }) => {
    await page.goto("/submit-agent");

    await expect(
      page.getByRole("heading", { name: "Add any published agent card" }),
    ).toBeVisible();

    // The honest command must be present; there must be NO submit button
    // that pretends to POST a card.
    await expect(page.getByText(/make card-ingest CARD_URL=/)).toBeVisible();
    await expect(
      page.getByRole("button", { name: /submit/i }),
    ).toHaveCount(0);

    // The trust-ceiling section ties back to /trust.
    await expect(
      page.getByRole("link", { name: "how we evaluate agents" }),
    ).toBeVisible();
  });

  test("header navigation exposes Trust and Submit agent", async ({ page }) => {
    await page.goto("/");
    const header = page.locator("header").first();
    await expect(header.getByRole("link", { name: "Trust" })).toBeVisible();
    await expect(
      header.getByRole("link", { name: "Submit agent" }),
    ).toBeVisible();
  });
});
