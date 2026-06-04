/**
 * sprint-pitch-align browser proofs
 *
 * Codifies the four flows we manually verified at the end of Phase 4 so
 * any future regression is caught before the deck-PDF pass:
 *
 *   1. P4-1  Live evidence strip on /         shows real Postgres counts
 *   2. P4-2  /agents/twilio-sms               shows a verification record
 *   3. P4-3  /discovery-gaps and
 *            /open-mcp-opportunities          show "Recent verifications"
 *   4. /goal "refuses honestly when nothing is ready yet" — returns
 *      "Plan outline only" with every host-format download disabled.
 *
 * Spec is intentionally tolerant of empty / loading states so a fresh
 * dev box without seeded evidence still produces actionable diffs
 * rather than red checkmarks for the wrong reason.
 */

import { expect, test } from "@playwright/test";

const GOAL_TIMEOUT_MS = 4 * 60 * 1000; // /goal takes ~2 minutes with discovery + LLM

test.describe("sprint-pitch-align P4 flows", () => {
  test("P4-1 — homepage live evidence strip pulls real Postgres counts", async ({
    page,
  }) => {
    await page.goto("/");

    const strip = page.getByTestId("live-evidence-strip");
    await expect(strip).toBeVisible();
    await expect(strip).toHaveAttribute("data-state", "ok", {
      timeout: 30_000,
    });

    await expect(strip.getByText("Benchmark runs")).toBeVisible();
    await expect(strip.getByText("Verifications")).toBeVisible();
    await expect(strip.getByText(/Scout runs/)).toBeVisible();
    await expect(strip.getByText(/Demand events/)).toBeVisible();
    await expect(strip.getByText(/cells? routable \(30d\)/)).toBeVisible();
    await expect(strip.getByText(/checked /)).toBeVisible();
  });

  test("P4-2 — agent detail page renders verification + benchmark sections", async ({
    page,
    request,
  }) => {
    // Pre-2026-05-20: this test hard-coded `/agents/twilio-sms`,
    // which 404'd after the discovery store was rebuilt without that
    // candidate. The deck claim is "every agent detail page renders
    // verification history + benchmark sections" — not "this specific
    // slug always exists" — so pick the first top-candidate id off
    // `/discovery/categories` (the most stable seeded endpoint).
    const apiBase =
      process.env.PLANMYAGENTS_API_BASE_URL ?? "http://127.0.0.1:8000";
    const categoriesResponse = await request.get(
      `${apiBase}/discovery/categories`,
    );
    expect(categoriesResponse.ok()).toBeTruthy();
    const categoriesPayload = (await categoriesResponse.json()) as {
      categories: Array<{
        top_candidate_ids?: string[];
        totals?: { candidates?: number };
      }>;
    };
    const firstWithCandidate = categoriesPayload.categories.find(
      (cat) => (cat.top_candidate_ids?.length ?? 0) > 0,
    );
    test.skip(
      !firstWithCandidate,
      "no seeded discovery candidates — run `make discovery-refresh` first",
    );
    const providerId = firstWithCandidate!.top_candidate_ids![0];

    await page.goto(`/agents/${providerId}`);

    const verificationHeading = page.getByRole("heading", {
      name: "Verification history",
    });
    await expect(verificationHeading).toBeVisible();

    // Should be at least one verification row OR the documented empty
    // state. Either way the section renders — that's what the deck
    // claims and what the API contract guarantees.
    const verifySection = verificationHeading.locator(
      "xpath=ancestor::section[1]",
    );
    await expect(verifySection).toContainText(
      /(Raw lead|provider_evidence_missing|evidence_fetch_failed|Tested by PlanMyAgents|capability_verified|No verification checks recorded yet)/,
    );

    await expect(
      page.getByRole("heading", { name: "Recent benchmark runs" }),
    ).toBeVisible();
  });

  test("P4-3 — /discovery-gaps mounts the recent-verifications panel", async ({
    page,
  }) => {
    await page.goto("/discovery-gaps");
    const panel = page.getByTestId("recent-verifications-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute("data-state", /ok|loading/, {
      timeout: 30_000,
    });
    await expect(panel.getByText("Recent verifications")).toBeVisible();
  });

  test("P4-3 — /open-mcp-opportunities mounts the recent-verifications panel", async ({
    page,
  }) => {
    await page.goto("/open-mcp-opportunities");
    const panel = page.getByTestId("recent-verifications-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute("data-state", /ok|loading/, {
      timeout: 30_000,
    });
  });

  test("/goal refuses honestly when nothing is ready yet", async ({
    page,
  }) => {
    test.setTimeout(GOAL_TIMEOUT_MS + 30_000);

    await page.goto("/");
    const goalBox = page.getByRole("textbox", { name: "Goal" });
    await goalBox.fill("buy cheapest glenlivet in tamil nadu");

    await page.getByRole("button", { name: "Submit" }).click();

    const outcome = page.getByRole("heading", { name: "Plan outline only" });
    await expect(outcome).toBeVisible({ timeout: GOAL_TIMEOUT_MS });

    await expect(
      page.getByText(/none are routable\/exportable yet/),
    ).toBeVisible();
    // RecipeDownloadButtons renders "Recipe coverage: 0/N exportable
    // steps (gap-only runbook)" — the `0` proves nothing is
    // exportable, which is the deck claim being tested here.
    await expect(
      page.getByText(/Recipe coverage:\s*0\/\d+\s*exportable steps/),
    ).toBeVisible();

    // Each non-markdown host format must explicitly tell the user why
    // it is disabled — this is the deck claim that we don't ship
    // empty/broken recipes. We walk from the format label up to its
    // <li> ancestor rather than relying on `getByRole('listitem',
    // {name: ...})` because the accessible-name computation in
    // Playwright's current accessibility snapshot drops the inter-
    // span whitespace between the label and the detail spans inside
    // RecipeText, which breaks the regex `<label>.*Disabled`.
    for (const format of [
      "Claude Desktop config",
      "n8n workflow JSON",
      "Cursor MCP config",
      "Bash CLI script",
    ]) {
      const label = page.getByText(format, { exact: true }).first();
      await expect(label).toBeVisible();
      const li = label.locator("xpath=ancestor::li[1]");
      await expect(li).toContainText(/Disabled/);
    }

    // Markdown row stays enabled and renders as a download link.
    const markdownLabel = page
      .getByText("Human-readable runbook", { exact: true })
      .first();
    await expect(markdownLabel).toBeVisible();
    await expect(
      markdownLabel.locator("xpath=ancestor::li[1]"),
    ).not.toContainText(/Disabled/);

    // The OUTCOME panel must surface the planner's decomposition so a
    // vendor can immediately spot intent misframing. Added 2026-05-19;
    // pre-fix the panel only said "produced N sub-tasks" without
    // showing what they were. See OutcomeCard SubTasksList.
    //
    // We assert capability-AGNOSTICALLY here: the LLM picks different
    // long-tail capabilities for the whisky goal across runs
    // (store_locator, price_comparison, shipping_quote,
    // payment_authorization, …). Anchoring to a specific slug would
    // flake. Mode-specific copy + per-mode coverage live in
    // subtasks-list-modes.spec.ts (mocked).
    const subTasksList = page.getByTestId("goal-sub-tasks-list");
    await expect(subTasksList).toBeVisible();
    await expect(
      subTasksList.getByText("Sub-tasks the planner produced"),
    ).toBeVisible();
    const subTaskRows = subTasksList.getByTestId("goal-sub-task-row");
    // The "Plan outline only" subtitle already told us N>=1; the list
    // must render at least one row, and each row must surface its
    // capability slug so a vendor can challenge the routing decision.
    await expect(subTaskRows.first()).toBeVisible();
    expect(await subTaskRows.count()).toBeGreaterThanOrEqual(1);
    await expect(
      subTasksList.getByText(/^capability\s+\S+$/).first(),
    ).toBeVisible();
  });
});
