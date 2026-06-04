import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the four end-to-end flows that prove the
 * sprint-pitch-align deliverables are live in the actual browser:
 *
 *   1. Live evidence strip on /
 *   2. Verification history on /agents/twilio-sms
 *   3. Recent verifications panel on /discovery-gaps and
 *      /open-mcp-opportunities
 *   4. /goal request returns a "Plan outline only" refusal with
 *      every host-format download disabled.
 *
 * Tests assume:
 *   - The FastAPI backend is up on PLANMYAGENTS_API_BASE_URL (default
 *     http://127.0.0.1:8000) with the evidence tables populated
 *     (run `make evidence-backfill verify-top-candidates` first).
 *   - The Next.js dev server is reachable at PLANMYAGENTS_WEB_BASE_URL
 *     (default http://localhost:3000). If it isn't, this config will
 *     start `npm run dev` for the duration of the test run.
 *
 * Run with:
 *   cd apps/web
 *   npm install --save-dev @playwright/test
 *   npx playwright install chromium
 *   npx playwright test
 */

const WEB_BASE_URL =
  process.env.PLANMYAGENTS_WEB_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 4 * 60 * 1000,
  expect: { timeout: 30 * 1000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: WEB_BASE_URL,
    actionTimeout: 30 * 1000,
    navigationTimeout: 60 * 1000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: process.env.PLANMYAGENTS_WEB_BASE_URL
    ? undefined
    : {
        command: "npm run dev",
        url: WEB_BASE_URL,
        reuseExistingServer: true,
        timeout: 120 * 1000,
      },
});
