/**
 * Config invariants for apps/web/next.config.mjs
 *
 * This is the regression test for the LiveEvidenceStrip
 * "stuck-in-loading" bug we shipped a fix for on 2026-05-19.
 *
 * Root cause was NOT in the strip itself: it was that Next.js 16
 * introduced a strict cross-origin check on dev resources
 * (`/_next/webpack-hmr`, RSC streaming chunks, ...). Without
 * an explicit `allowedDevOrigins` entry, requests from
 * `127.0.0.1:3000` to a dev server bound on `localhost:3000`
 * get blocked, and — critically — Next aborts the client
 * runtime mid-boot. The visible symptom is that every
 * `"use client"` component (the strip, the goal form, ...)
 * stays in its server-rendered initial state forever, with
 * no React errors, because hydration never starts.
 *
 * The fix is to allow-list both hostnames in
 * `next.config.mjs`. This test asserts the allow-list stays
 * there so the symptom can't silently come back.
 *
 * Run:
 *   node --test apps/web/tests/config/next-config.test.mjs
 *
 * Or (from apps/web):
 *   npm run test:config
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import nextConfig from "../../next.config.mjs";

test("next.config exports an object with reactStrictMode + typedRoutes on", () => {
  assert.equal(typeof nextConfig, "object");
  assert.equal(nextConfig.reactStrictMode, true);
  assert.equal(nextConfig.typedRoutes, true);
});

test("next.config.allowedDevOrigins allow-lists both 127.0.0.1 and localhost (with and without port)", () => {
  // The actual property must be an array (string is deprecated in Next 16).
  assert.ok(
    Array.isArray(nextConfig.allowedDevOrigins),
    "allowedDevOrigins must be set as an array — without it Next 16 " +
      "blocks the HMR socket from 127.0.0.1 and the client runtime " +
      "fails to hydrate. See LiveEvidenceStrip bug fix, 2026-05-19.",
  );

  // The exact host the curl-based docs / pitch deck use:
  assert.ok(
    nextConfig.allowedDevOrigins.includes("127.0.0.1"),
    "127.0.0.1 must be in allowedDevOrigins — homepage demo loads it.",
  );

  // The port-qualified host the Next dev server actually serves:
  assert.ok(
    nextConfig.allowedDevOrigins.includes("127.0.0.1:3000"),
    "127.0.0.1:3000 must be in allowedDevOrigins — Next matches " +
      "Origin headers with the port.",
  );

  // Belt + suspenders: keep `localhost` in too so the same .env / curl
  // pair works regardless of which loopback alias the user uses.
  assert.ok(
    nextConfig.allowedDevOrigins.includes("localhost"),
    "localhost must be in allowedDevOrigins.",
  );
  assert.ok(
    nextConfig.allowedDevOrigins.includes("localhost:3000"),
    "localhost:3000 must be in allowedDevOrigins.",
  );
});
