/**
 * Sub-tasks list renders across every OutcomeCard mode.
 *
 * Background
 * ----------
 * 2026-05-19: a vendor reviewing the /goal page noticed the OUTCOME
 * panel said "The planner produced 2 sub-tasks, but none are
 * routable/exportable yet" without ever showing what those sub-tasks
 * WERE — undermining trust in the decomposition. We landed a
 * `SubTasksList` block inside `OutcomeCard` that renders the
 * decomposition on every outcome. A Playwright check confirmed the
 * list renders for the outline-only path under the real backend,
 * but the other four modes (executed / executable_not_run /
 * partial_not_run / refused) are hard to exercise organically without
 * polluting the production DB (the routability gate is intentional).
 *
 * Approach
 * --------
 * Use `page.route()` to intercept `POST /goal` and return a canned
 * payload per outcome mode. The frontend renders without any backend
 * dependency; the list rendering invariant is what we pin here.
 *
 * What this guards against
 * ------------------------
 * - Re-introducing the "produced N sub-tasks" copy without rendering
 *   them.
 * - Accidentally gating SubTasksList on a specific outcome mode (the
 *   regression that would silently hide the decomposition from
 *   executed responses, which is exactly when a vendor wants to see
 *   what we routed).
 * - The headline / capability slug rendering contract per row.
 */

import { expect, test, type Page } from "@playwright/test";

const SUBMIT_GOAL = "stub goal — intercepted by page.route";

type CannedSubTask = {
  capability: string;
  description?: string;
  inputs?: Record<string, unknown>;
};

type CannedResponse = {
  ok: boolean;
  executed: boolean;
  summary?: string;
  answer?: string | null;
  plan: {
    status: string;
    sub_tasks: CannedSubTask[];
    missing_capabilities?: string[];
    recipe_coverage?: {
      step_count: number;
      exportable_step_count: number;
      gap_count: number;
      status: "executable" | "partial" | "gap_only";
    };
  };
};

const TWO_SUB_TASKS: CannedSubTask[] = [
  {
    capability: "email_send",
    description: "Send a welcome email to the new user",
    inputs: {
      user_facing_step: "Send welcome email",
      search_query: "transactional email sending",
      acceptance_criteria: "delivered email with subject and body",
    },
  },
  {
    capability: "user_profile_lookup",
    description: "Fetch the new user's display name to personalise the greeting",
    inputs: {
      user_facing_step: "Look up user's display name",
      search_query: "user profile API",
      acceptance_criteria: "returns display_name field",
    },
  },
];

const CANNED_RESPONSES: Record<string, CannedResponse> = {
  executed: {
    ok: true,
    executed: true,
    summary: "Executed 2 sub-tasks.",
    answer: "Welcome email sent to bob@example.com.",
    plan: {
      status: "executable",
      sub_tasks: TWO_SUB_TASKS,
      recipe_coverage: {
        step_count: 2,
        exportable_step_count: 2,
        gap_count: 0,
        status: "executable",
      },
    },
  },
  executable_not_run: {
    ok: true,
    executed: false,
    plan: {
      status: "executable",
      sub_tasks: TWO_SUB_TASKS,
      recipe_coverage: {
        step_count: 2,
        exportable_step_count: 2,
        gap_count: 0,
        status: "executable",
      },
    },
  },
  partial_not_run: {
    ok: true,
    executed: false,
    plan: {
      status: "executable",
      sub_tasks: TWO_SUB_TASKS,
      recipe_coverage: {
        step_count: 2,
        exportable_step_count: 1,
        gap_count: 1,
        status: "partial",
      },
    },
  },
  outline_only: {
    ok: true,
    executed: false,
    plan: {
      status: "executable",
      sub_tasks: TWO_SUB_TASKS,
      recipe_coverage: {
        step_count: 2,
        exportable_step_count: 0,
        gap_count: 2,
        status: "gap_only",
      },
    },
  },
};

/**
 * Stub `POST /goal` to return the canned payload for `mode`.
 *
 * Intercepts every request to either the same-origin `/goal` (which
 * Next.js proxies to the FastAPI backend) and the direct
 * `127.0.0.1:8000/goal` endpoint so the test passes regardless of
 * how `NEXT_PUBLIC_API_URL` is configured.
 */
async function stubGoalEndpoint(
  page: Page,
  mode: keyof typeof CANNED_RESPONSES,
): Promise<void> {
  const payload = CANNED_RESPONSES[mode];
  await page.route("**/goal", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}

async function submitGoal(page: Page): Promise<void> {
  await page.goto("/");
  const goalBox = page.getByRole("textbox", { name: "Goal" });
  await goalBox.fill(SUBMIT_GOAL);
  await page.getByRole("button", { name: "Submit" }).click();
}

async function assertSubTasksListRenders(page: Page): Promise<void> {
  const list = page.getByTestId("goal-sub-tasks-list");
  await expect(list).toBeVisible({ timeout: 15_000 });
  // Header label is the load-bearing copy: regression would either
  // silently drop the section or rename it past recognition.
  await expect(list.getByText("Sub-tasks the planner produced")).toBeVisible();
  // Both canned sub-tasks must render with their user-facing-step
  // headlines (the SubTasksList prefers `inputs.user_facing_step`
  // over `description` — see stepLabelFor in OutcomeCard).
  await expect(list.getByText("Send welcome email")).toBeVisible();
  await expect(list.getByText("Look up user's display name")).toBeVisible();
  // Capability slugs must be visible (debug-grade info for vendors).
  await expect(list.getByText(/capability\s+email_send/)).toBeVisible();
  await expect(list.getByText(/capability\s+user_profile_lookup/)).toBeVisible();
  // Two rows expected for this fixture.
  await expect(list.getByTestId("goal-sub-task-row")).toHaveCount(2);
}

test.describe("OutcomeCard sub-tasks list renders across every outcome mode", () => {
  test("executed — list renders alongside the green Done card", async ({
    page,
  }) => {
    await stubGoalEndpoint(page, "executed");
    await submitGoal(page);
    await expect(
      page.getByRole("heading", { name: "Done" }),
    ).toBeVisible({ timeout: 15_000 });
    await assertSubTasksListRenders(page);
  });

  test("executable_not_run — list renders alongside the 'ready, not executed' card", async ({
    page,
  }) => {
    await stubGoalEndpoint(page, "executable_not_run");
    await submitGoal(page);
    await expect(
      page.getByRole("heading", { name: "Plan ready, not executed" }),
    ).toBeVisible({ timeout: 15_000 });
    await assertSubTasksListRenders(page);
  });

  test("partial_not_run — list renders alongside the amber 'partial' card", async ({
    page,
  }) => {
    await stubGoalEndpoint(page, "partial_not_run");
    await submitGoal(page);
    await expect(
      page.getByRole("heading", { name: "Plan partially ready" }),
    ).toBeVisible({ timeout: 15_000 });
    await assertSubTasksListRenders(page);
  });

  test("outline_only — list renders alongside the amber 'outline only' card", async ({
    page,
  }) => {
    await stubGoalEndpoint(page, "outline_only");
    await submitGoal(page);
    await expect(
      page.getByRole("heading", { name: "Plan outline only" }),
    ).toBeVisible({ timeout: 15_000 });
    await assertSubTasksListRenders(page);
  });
});
