/**
 * LiveDiscoveryPanel surfaces every scout the backend dispatches.
 *
 * Background
 * ----------
 * 2026-05-20: we expanded the request-time scout fleet from 14 to 17
 * by adding `glama`, `npm_mcp_packages`, and `github_awesome_lists`
 * (see `sprint-pitch-align.md` → "Discovery-breadth expansion" and
 * `docs/agent-discovery-index.md`). The frontend renders whatever
 * `scout_id` strings the backend returns, so the wire contract is
 * already covered by unit tests on the API side. What this spec pins
 * is the *frontend-side* contract: the new scout IDs survive the
 * `LiveDiscoveryStatus → DetailsAccordion → LiveDiscoveryPanel` chain
 * and are visible to a vendor opening the response panel.
 *
 * Approach
 * --------
 * Stub `POST /goal` via `page.route()` with a canned payload whose
 * `request_time_discovery.per_capability[].dispatch.scouts[]` lists
 * all three new scout IDs (alongside two existing ones for a sanity
 * baseline). Submit the goal, open the Details accordion (it is
 * collapsed by default — see `DetailsAccordion`), and assert each
 * new scout_id is visible inside the LiveDiscoveryPanel block.
 *
 * Why mock instead of driving a real /goal call?
 * ----------------------------------------------
 * A real /goal takes 2-4 minutes (LLM planner + scout dispatch +
 * judge), the dispatcher routes each capability to *up to 3* scouts
 * (so the new IDs may not all surface on a given run), and the live
 * fleet depends on `GITHUB_TOKEN` for `github_awesome_lists`. The
 * mock is deterministic and runs in a few seconds; the live coverage
 * happens in `apps/api/tests/test_scout_dispatcher.py` and the
 * per-source unit tests.
 */

import { expect, test, type Page } from "@playwright/test";

const SUBMIT_GOAL = "stub goal — intercepted by page.route for scout-fleet check";

type CannedScout = {
  scout_id: string;
  status: "ok" | "timeout" | "error" | "skipped";
  candidate_count: number;
  elapsed_ms: number;
  skipped_reason?: string | null;
  error?: string | null;
};

const FLEET_UNDER_TEST: CannedScout[] = [
  // Existing scouts — sanity baseline so we know the panel renders
  // at all and the test isn't accidentally asserting on an empty list.
  { scout_id: "smithery", status: "ok", candidate_count: 3, elapsed_ms: 412 },
  {
    scout_id: "official_mcp_registry",
    status: "ok",
    candidate_count: 1,
    elapsed_ms: 287,
  },
  // The three scouts added on 2026-05-20.
  { scout_id: "glama", status: "ok", candidate_count: 4, elapsed_ms: 538 },
  {
    scout_id: "npm_mcp_packages",
    status: "ok",
    candidate_count: 2,
    elapsed_ms: 621,
  },
  {
    scout_id: "github_awesome_lists",
    status: "skipped",
    candidate_count: 0,
    elapsed_ms: 0,
    skipped_reason: "GITHUB_TOKEN not configured",
  },
];

// The frontend reads `liveDiscovery` from `plan.discovery.live_discovery`
// (see ResponseLayout.tsx:97). The wire contract therefore nests the
// scout dispatch block under `plan.discovery` — NOT at the top level.
const TOTAL_CANDIDATES = FLEET_UNDER_TEST.reduce(
  (n, s) => n + s.candidate_count,
  0,
);

const CANNED_GOAL_RESPONSE = {
  ok: true,
  executed: false,
  plan: {
    status: "executable",
    sub_tasks: [
      {
        capability: "email_send",
        description: "Send a welcome email",
        inputs: {
          user_facing_step: "Send welcome email",
          search_query: "transactional email",
          acceptance_criteria: "delivered",
        },
      },
    ],
    recipe_coverage: {
      step_count: 1,
      exportable_step_count: 1,
      gap_count: 0,
      status: "executable",
    },
    discovery: {
      live_discovery: {
        status: "ran",
        scouts_dispatched: FLEET_UNDER_TEST.length,
        candidates_found_total: TOTAL_CANDIDATES,
        candidates_freshly_discovered: 4,
        candidates_persisted: 4,
        per_capability: [
          {
            capability: "email_send",
            query_expansion: {
              used_llm: true,
              fallback_reason: null,
            },
            dispatch: {
              sub_task_id: "subtask-0",
              capability: "email_send",
              total_elapsed_ms: 1858,
              merged_candidate_count: TOTAL_CANDIDATES,
              scouts: FLEET_UNDER_TEST,
            },
          },
        ],
        newly_discovered_candidates: [],
      },
    },
  },
};

async function stubGoalEndpoint(page: Page): Promise<void> {
  await page.route("**/goal", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CANNED_GOAL_RESPONSE),
    });
  });
}

test.describe("LiveDiscoveryPanel surfaces the expanded scout fleet", () => {
  test("renders every scout_id from request_time_discovery, including the three added on 2026-05-20", async ({
    page,
  }) => {
    await stubGoalEndpoint(page);

    await page.goto("/");
    await page
      .getByRole("textbox", { name: "Goal" })
      .fill(SUBMIT_GOAL);
    await page.getByRole("button", { name: "Submit" }).click();

    // Wait for the response panel to mount before opening the
    // Details accordion. The sub-tasks list is the most reliable
    // anchor — it renders on every outcome mode.
    await expect(page.getByTestId("goal-sub-tasks-list")).toBeVisible({
      timeout: 15_000,
    });

    // DetailsAccordion is a <details> element collapsed by default.
    // Click the summary to open it.
    const detailsSummary = page.getByText(
      /Details \(provenance, cost, scouts, promotion progress\)/,
    );
    await expect(detailsSummary).toBeVisible();
    await detailsSummary.click();

    // LiveDiscoveryPanel header is the load-bearing copy that proves
    // the panel mounted.
    await expect(
      page.getByText("Just searched the live web for this request"),
    ).toBeVisible();

    // The per-capability <details> inside the panel opens
    // automatically because merged_candidate_count > 0 (see
    // LiveDiscoveryPanel:82 `open={cap.dispatch.merged_candidate_count > 0}`).
    // Each scout renders as <code>{scout_id}</code>.
    for (const scout of FLEET_UNDER_TEST) {
      const code = page.locator("code", { hasText: scout.scout_id });
      await expect(code).toBeVisible();
    }

    // The skipped scout must surface its skip reason so vendors know
    // why a fleet member contributed zero candidates — guards against
    // the regression where skipped_reason is silently swallowed.
    // Use `getByText({ exact: true })` to avoid colliding with the
    // raw JSON dump at the bottom of the response panel (which also
    // contains the same string inside a <pre>).
    await expect(
      page.getByText("GITHUB_TOKEN not configured", { exact: true }),
    ).toBeVisible();
  });
});
