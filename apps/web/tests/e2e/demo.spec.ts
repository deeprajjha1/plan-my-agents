/**
 * PlanMyAgents — 1-minute product demo (goal + results, no planning wait)
 *
 * Records a browser video similar to canvases/yc_demo_recording.webm but
 * includes the homepage goal flow. POST /goal is mocked via demo-goal-fixture.ts
 * so the viewer never sits through "Planning…".
 *
 * Edit goal + result: tests/e2e/demo-goal-fixture.ts
 * Render video: ../../scripts/render_goal_demo_video.sh
 */

import { expect, test } from "@playwright/test";

import { DEMO_GOAL, DEMO_GOAL_RESPONSE } from "./demo-goal-fixture";

test.use({
  video: { mode: "on", size: { width: 800, height: 500 } },
  viewport: { width: 800, height: 500 },
});

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function showTitleCard(
  page: import("@playwright/test").Page,
  title: string,
  subtitle: string,
  durationMs = 2800,
) {
  await page.evaluate(
    ({ title, subtitle }) => {
      document.getElementById("__demo_title_card__")?.remove();

      const card = document.createElement("div");
      card.id = "__demo_title_card__";
      card.style.cssText = `
        position: fixed; inset: 0; z-index: 99999; display: flex; flex-direction: column;
        align-items: center; justify-content: center; background: rgba(10, 10, 20, 0.88);
        backdrop-filter: blur(6px); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        animation: fadeIn 0.35s ease;
      `;

      const h = document.createElement("h2");
      h.textContent = title;
      h.style.cssText =
        "font-size: 1.55rem; font-weight: 700; color: #fff; margin: 0 0 0.4rem 0; text-align: center; max-width: 520px; line-height: 1.25;";

      const p = document.createElement("p");
      p.textContent = subtitle;
      p.style.cssText =
        "font-size: 0.95rem; color: rgba(255,255,255,0.72); margin: 0; text-align: center; max-width: 480px; line-height: 1.45;";

      const style = document.createElement("style");
      style.textContent = "@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }";
      document.head.appendChild(style);

      card.appendChild(h);
      card.appendChild(p);
      document.body.appendChild(card);
    },
    { title, subtitle },
  );

  await pause(durationMs);

  await page.evaluate(() => {
    const card = document.getElementById("__demo_title_card__");
    if (card) {
      card.style.transition = "opacity 0.4s ease";
      card.style.opacity = "0";
      setTimeout(() => card.remove(), 400);
    }
  });

  await pause(450);
}

async function stubGoalEndpoint(page: import("@playwright/test").Page) {
  await page.route("**/goal", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DEMO_GOAL_RESPONSE),
    });
  });
}

async function submitMockedGoal(page: import("@playwright/test").Page) {
  const goalBox = page.getByRole("textbox", { name: "Goal" });
  await goalBox.fill(DEMO_GOAL);
  await page.getByRole("button", { name: "Submit" }).click();
  await expect(page.getByTestId("goal-sub-tasks-list")).toBeVisible({
    timeout: 10_000,
  });
}

async function typeGoal(page: import("@playwright/test").Page, text: string) {
  const goalBox = page.getByRole("textbox", { name: "Goal" });
  await goalBox.click();
  await pause(250);
  for (const char of text) {
    await goalBox.pressSequentially(char, { delay: 22 });
  }
}

test("PlanMyAgents goal demo — 1 min recording", async ({ page }) => {
  await stubGoalEndpoint(page);

  // ACT 1 — Hook
  await page.goto("/");
  await pause(1200);
  await showTitleCard(
    page,
    "10,000+ AI tools. Most are broken.",
    "PlanMyAgents discovers, benchmarks, and routes the ones that actually work.",
    3200,
  );

  // ACT 2 — Goal (mocked; no planning wait)
  await showTitleCard(
    page,
    "Describe your outcome in plain English",
    "We decompose the goal, match verified providers, and refuse honestly when nothing is ready.",
    2800,
  );

  await typeGoal(page, DEMO_GOAL);
  await pause(500);
  await page.getByRole("button", { name: "Submit" }).click();
  await expect(page.getByTestId("goal-sub-tasks-list")).toBeVisible({
    timeout: 10_000,
  });
  const subTasks = page.getByTestId("goal-sub-tasks-list");
  await expect(subTasks.getByText("Verify email address")).toBeVisible();
  await expect(subTasks.getByText("Send welcome email")).toBeVisible();
  await expect(page.getByText("Plan partially ready")).toBeVisible();

  await pause(2800);
  await page.mouse.wheel(0, 220);
  await pause(3200);
  await page.mouse.wheel(0, 220);
  await pause(3000);

  // ACT 3 — Index surfaces (same arc as yc_demo_recording.webm)
  await showTitleCard(
    page,
    "Under the hood: a live index",
    "Agents grouped by capability — with honest benchmark and routability counts.",
    2600,
  );

  await page.goto("/categories");
  await pause(2000);
  await page.mouse.wheel(0, 280);
  await pause(2200);
  await page.mouse.wheel(0, 280);
  await pause(2000);

  await showTitleCard(
    page,
    "Every capability is ranked",
    "Real benchmark runs — not pay-to-rank listings.",
    2600,
  );

  await page.goto("/leaderboards");
  await pause(2500);
  await page.mouse.wheel(0, 260);
  await pause(2200);

  // ACT 4 — Goal result (instant re-submit; mock skips planning wait)
  await page.goto("/");
  await pause(600);
  await submitMockedGoal(page);
  await showTitleCard(
    page,
    "Your plan — ready to export",
    "Sub-tasks, matched providers, and recipes for Claude, Cursor, or n8n.",
    2800,
  );
  await pause(4000);

  await showTitleCard(
    page,
    "PlanMyAgents",
    "Trust infrastructure for the AI agent economy · planmyagents.com",
    5000,
  );
  await pause(800);
});
