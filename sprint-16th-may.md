# PlanMyAgents Sprint 4 Plan — Post‑Pivot Execution (16 May 2026)

> **Purpose:** Take the codebase from "Phase 1 engine works (965 tests
> green) but no recipe export, no Pro tier, no vendor portal, no
> partner API" → "Y1 Q1–Q2 milestones from the rewritten spec are
> shippable" without breaking what works.
>
> **What changed:** The 16‑May strategic rewrite locked in Option F+
> (planning + discovery engine → recipe + benchmark + marketplace +
> partnership platform). This sprint executes the immediate **2–3
> month** slice of the new 24‑month roadmap.
>
> **Companion docs (read first):** [BUSINESS_PLAN.md §12](BUSINESS_PLAN.md#12-product-roadmap-24-months),
> [docs/ARCHITECTURE.md §14](docs/ARCHITECTURE.md#14-evolution-strategy--three-phases),
> [docs/HLD.md §10–§11](docs/HLD.md), [docs/LLD.md §23–§24](docs/LLD.md),
> [docs/marketplace-design.md](docs/marketplace-design.md),
> [docs/partnership-strategy.md](docs/partnership-strategy.md).
>
> **Historical context:** sprint.md (sprawling pre‑pivot), sprint‑2.md
> (semantic matcher), sprint‑3.md (5 cap depth + cost cap). This file
> picks up exactly where sprint‑3 finished. Sprint‑3 Track 3a was
> partially done (4 of 5 hand‑written adapters shipped; benchmark
> infrastructure ready); we now reposition those adapters as **benchmark‑
> only** (per new spec — no proprietary vendor wrappers in customer
> execution path) and pivot effort to the new‑spec surfaces.

Last updated: 2026‑05‑16 — sprint plan only (no code changes yet).

---

## Table of contents

1. [Sprint goal in one sentence](#1-sprint-goal-in-one-sentence)
2. [How this sprint differs from sprint‑3](#2-how-this-sprint-differs-from-sprint-3)
3. [Code reuse inventory — the audit](#3-code-reuse-inventory--the-audit)
4. [Track T0 — Cleanup + repositioning](#4-track-t0--cleanup--repositioning-week-1)
5. [Track T1 — Phase‑1 finish (Recipe + Pro)](#5-track-t1--phase-1-finish-recipe-export--pro-tier)
6. [Track T2 — Cursor Tier‑1 enablement + BYO sandbox UI](#6-track-t2--cursor-tier-1-enablement--byo-creds-sandbox-ui)
7. [Track T3 — Vendor portal alpha (Phase‑2 foundation)](#7-track-t3--vendor-portal-alpha-phase-2-foundation)
8. [Track T4 — Partner gateway skeleton (Phase‑3 foundation)](#8-track-t4--partner-gateway-skeleton-phase-3-foundation)
9. [Track T5 — Vendor‑neutrality firewall enforcement (P11)](#9-track-t5--vendor-neutrality-firewall-enforcement-p11)
10. [Sequencing & dependency graph](#10-sequencing--dependency-graph)
11. [Definition of done for Sprint 4](#11-definition-of-done-for-sprint-4)
12. [Estimated total effort](#12-estimated-total-effort)
13. [Risks and mitigations](#13-risks-and-mitigations)
14. [What's explicitly OUT of scope](#14-whats-explicitly-out-of-scope)
15. [After Sprint 4 — what comes next](#15-after-sprint-4--what-comes-next)

---

## 1. Sprint goal in one sentence

**Ship recipe export + Pro tier billing skeleton + BYO‑creds sandbox UI + vendor portal alpha + partner API gateway skeleton + firewall enforcement — repositioning all hand‑written vendor adapters as benchmark‑only — so that by sprint end we can credibly approach n8n / Cursor / Anthropic for Tier‑1 partnership conversations *and* open the founding marketplace cohort outreach with a working vendor sign‑up flow.**

---

## 2. How this sprint differs from sprint‑3

| Dimension | Sprint 3 | Sprint 4 |
|---|---|---|
| Driving spec | Original engine‑only roadmap | Option F+ (planning + discovery → recipe + benchmark + marketplace + partnerships) |
| Hand‑written vendor adapters | Built (razorpay, stripe, resend, firecrawl, shippo, ebay, hunter) | **Repositioned as benchmark‑only**; not reachable from `/goal` customer path |
| Recipe export | Implicit (just print the plan) | **Explicit endpoint with 5 formats** (claude_desktop_json, n8n_json, cursor_prompt, markdown, cli) — drives Tier‑1 partnerships |
| Pro tier | Out of scope | **In scope as billing skeleton** — auth, plans table, saved_recipes table, Stripe billing portal |
| Marketplace | Out of scope | **Alpha** — vendor portal scaffolding, claim verification, profile editor; no sponsored placement / execution fee yet |
| Partner API | Out of scope | **Skeleton** — per‑partner key auth + 2 endpoints (`/partner/v1/search` + `/partner/v1/recipe`); no SDK yet |
| Firewall (P11) | N/A | **In scope** — CI lint asserting ranker has no marketplace imports; firewall_audit cron; rate cap DB CHECK |
| New backends | None | `marketplace_store` schema + `partner_store` schema (separate from `discovery_store`) |
| New frontend app | None | `apps/vendor-web/` Next.js skeleton |

---

## 3. Code reuse inventory — the audit

This is the audit the user asked for: **for every new‑spec surface, what code already exists that we can reuse vs build from scratch**. Numbers are LOC.

### 3.1 Phase 1 surfaces — almost entirely reusable

| New‑spec surface (HLD/LLD reference) | Existing code (reuse) | Status | Action |
|---|---|---|---|
| 14+ discovery scouts (L1) | `apps/api/planmyagents_api/discovery/sources/*.py` — 18 modules (smithery, mcp_marketplace, official_mcp_registry, github_recently_pushed, apis_guru, a2a, ai_directory, hacker_news, moltbook, mcp.py, mcp_publication_quality, vendor_rss, web_doc, json_source, live, research, static, base) | ✅ **Reuse as‑is** | Add vendor self‑submission scout in T3 |
| Discovery store (L2) | `discovery/store.py` (801 LOC), `apis_without_agents_store.py`, `verification_store.py`, `demand_store.py`, `discovery_gaps_store.py` — all Postgres + SQLite | ✅ **Reuse as‑is** | None |
| Normalizer + dedupe (L2) | `discovery/normalizer.py`, `discovery/dedupe.py` (with `_best_verification_status` post‑Fix 3b) | ✅ **Reuse as‑is** | None |
| Embedding pipeline (L3) | `discovery/embeddings.py`, `discovery/capability_index.py` (pgvector wrapper) | ✅ **Reuse as‑is** | None |
| Capability catalog (L3) | `planner/capability_catalog.py`, `registry/capability_descriptions.py`, 26 routable caps in `packages/registry/capabilities.json` | ✅ **Reuse as‑is** | None |
| Goal decomposer (L3) | `planner/goal_decomposer.py` + `planner/capability_brainstorm.py` + `planner/human_fallback_suggester.py` | ✅ **Reuse as‑is** | None |
| Label reconciler (L3) | `planner/label_reconciler.py` + `planner/capability_label_store.py` + `capability_label_recorder.py` | ✅ **Reuse as‑is** | None |
| Trust ladder + qualification gate (L4) | `discovery/verification.py`, `discovery/gaps.py` (with `QUALIFIED_VERIFICATION_STATUSES`) | ✅ **Reuse as‑is** | None |
| LLM candidate judge (L4) | `discovery/candidate_judge.py` | ✅ **Reuse as‑is** | None |
| MCP tool probe (L4) | `discovery/post_goal_refresh.py` (Gap 2 work) | ✅ **Reuse as‑is** | None |
| Generic protocol adapters (L5) | `agents/protocol.py` (1082 LOC — `GenericMcpAdapter`, `GenericOpenApiAdapter`, `GenericA2AAdapter`, `GenericAiAgentAdapter`) | ✅ **Reuse as‑is** — this is the ONLY adapter family that touches the customer execution path under the new spec | None |
| Spend ledger / cost cap (L5) | `cost/spend_ledger.py`, `cost/cost_cap.py` (S3a‑7) | ✅ **Reuse as‑is** | Hook execution‑fee charger off the same code path in T5 |
| Per‑capability provider partition (L6) | `web/planning.py` (`_capability_provider_status`, `_planned_sub_tasks_from_decomposition`, `_apply_decomposer` — Fixes 2a/2b) | ✅ **Reuse as‑is** | None |
| Workflow scoring + stitching (L6) | `workflows/scoring.py` (425 LOC), `workflows/stitcher.py` (69 LOC), `workflows/models.py` (127 LOC) | ✅ **Reuse as‑is** | None |
| Pre‑plan discovery + post‑goal refresh (L7) | `discovery/pre_plan_discovery.py`, `discovery/post_goal_refresh.py` (Gap 3 work) | ✅ **Reuse as‑is** | None |
| Demand recorder + gap ledger (L7) | `discovery/demand_recorder.py`, `discovery/discovery_gaps_recorder.py`, `discovery/open_mcp_opportunities.py` | ✅ **Reuse as‑is** | Add Demand‑Data export (with K‑anonymity) in T3 |
| User surfaces (L8) | `web/app.py` (2467 LOC), `web/planning.py` (1440 LOC), `web/models.py`; frontend `apps/web/src/app/{goal,search,leaderboards,categories,agents,discovery-gaps,open-mcp-opportunities}` | ✅ **Reuse as‑is** | Add `/recipe/export` route in T1 |
| Benchmark runner + store + scoring + scheduler (used in L6) | `benchmark/runner.py`, `benchmark/store.py`, `benchmark/scoring.py`, `benchmark/scheduler.py`, `benchmark/credibility.py`, `benchmark/rankings.py`, `benchmark/report.py` | ✅ **Reuse as‑is** | Repurpose `WorkflowExecutor` integration in T0 |

**Verdict:** Phase 1 engine is ~95% complete. New‑spec Phase 1 deliverable (recipe export endpoint) is a thin shim over the existing `web/planning.py` output.

### 3.2 Hand‑written vendor adapters — reposition, don't delete

Sprint‑3 Track 3a built 6 production wrappers (Razorpay, Stripe, Resend, Firecrawl, Shippo, eBay) plus Hunter. The new spec rules out hand‑written vendor wrappers in the **customer execution path** but these adapters remain valuable as **benchmark cells** (their adversarial test cases validate the discovery+benchmark pipeline end‑to‑end).

| File | LOC | Old role | New role | Action in T0 |
|---|---|---|---|---|
| `agents/razorpay.py` | 368 | `WorkflowExecutor` adapter for `payment_authorization` | Benchmark cell only — used by `benchmark/runner.py` to score discovered MCP candidates against a known‑good baseline | **Reposition** — keep file, move under `benchmark/baselines/`, drop registration from `router.py` |
| `agents/stripe.py` | 418 | Same | Same | **Reposition** as above |
| `agents/resend.py` | 340 | `email_send` adapter | Benchmark baseline for `email_send` | **Reposition** as above |
| `agents/firecrawl.py` | 315 | `web_scraping` adapter | Benchmark baseline for `web_scraping` | **Reposition** as above |
| `agents/shippo.py` | 367 | `shipping_quote` adapter | Benchmark baseline for `shipping_quote` | **Reposition** as above |
| `agents/ebay.py` | 367 | `price_comparison` adapter | Benchmark baseline for `price_comparison` | **Reposition** as above |
| `agents/hunter.py` | 129 | `contact_enrichment` adapter | Benchmark baseline for `contact_enrichment` | **Reposition** as above |
| `agents/base.py` | 23 | `ProviderAdapter` interface | Split into `BenchmarkAdapter` (used by baselines above) + keep `ProviderAdapter` for `GenericProtocolAdapter` only | **Refactor** in T0 |
| `agents/router.py` | 332 | Routes capability → provider for execution | Routes capability → recommendation (no execute verb); benchmark router (separate) routes → baseline | **Refactor** in T0 |
| `agents/mock.py` | 790 | Mock adapters for hermetic CI | Same | ✅ Reuse |
| `agents/protocol.py` | 1082 | `GenericProtocolAdapter` family | THE customer execution path (BYO‑creds only) | ✅ Reuse |
| `workflows/executor.py` | 245 | Generic workflow executor | Repurpose as `BenchmarkRunner` (benchmark‑only) + spawn new `SandboxRunner` (BYO‑creds, generic‑adapter‑only) | **Split** in T0 |

**Total reused/repositioned LOC: ~2,300 — none of it deleted.** The hand‑written adapters remain testable baselines for benchmark cells, which is exactly what we need to populate the founding cohort's first 3 Verified Benchmarks.

### 3.3 New‑spec Phase 1 surfaces — small additions

| New‑spec surface | Existing similar code | Action | New LOC est. |
|---|---|---|---|
| `/recipe/export?format=...` endpoint | None — `/goal` returns plan but no downloadable artifact | **Build new** but reuse `web/planning.py` output structure | ~400 |
| Recipe generators (claude_desktop_json, n8n_json, cursor_prompt, markdown, cli) | None | **Build new** as `planner/recipe_export.py` with 5 sub‑modules | ~600 |
| `/disclosure` page + endpoint | None | **Build new** (initially returns empty arrays for sponsored/execution_fee/founding lists; populated later) | ~150 |
| Founding‑vendor static page (`/founding-vendors`) | None | **Build new** Next.js page reading curated JSON | ~80 |

### 3.4 New‑spec Phase 2 (Marketplace) surfaces — NONE exist

| New‑spec surface | Existing similar code | Action | New LOC est. |
|---|---|---|---|
| Vendor portal Next.js app | `apps/web/` exists as template | **Build new `apps/vendor-web/`** scaffolded from `apps/web/` | ~3000 across alpha pages |
| Vendor signup + Clerk auth + 2FA | None (no auth anywhere in API today) | **Build new** `marketplace/vendor_signup.py`; first auth integration in the project | ~250 |
| Claim verification (DNS + email) | None | **Build new** `marketplace/claim_verification.py` per LLD §23.1 | ~350 |
| Profile editor + moderation | None | **Build new** `marketplace/profile_editor.py` | ~300 |
| Benchmark certification flow | `benchmark/runner.py` + `benchmark/scheduler.py` exist | **Build new** `marketplace/benchmark_request.py` (LLD §23.3) on top of existing benchmark runner | ~400 |
| Sponsored placement renderer | None | **Build new** `marketplace/sponsored_renderer.py` (LLD §23.2) — but **NO** writes / vendor‑facing CRUD in Sprint 4 alpha (defer to Sprint 5) | ~250 (read‑only stub) |
| Lead routing | None | **Defer to Sprint 5** | 0 in this sprint |
| Demand‑Data API | `demand_recorder.py` exists | **Build new** `marketplace/demand_data_export.py` with K‑anonymity wrapper | ~200 |
| Execution fee subscription + charger + invoice runner | None | **Build new** `marketplace/execution_fee_*.py` (LLD §23.6); DB schema in T3, runtime hook in T5 | ~500 |
| Vendor audit + firewall audit | None | **Build new** `marketplace/audit.py` + `firewall_audit.py` (LLD §23.2 ending) | ~200 |
| Marketplace store (Postgres schema + interface) | `discovery/store.py` pattern exists | **Build new** following same pattern; 12 tables per LLD §3.11 | ~600 |

### 3.5 New‑spec Phase 3 (Partner API) surfaces — NONE exist

| New‑spec surface | Existing similar code | Action | New LOC est. |
|---|---|---|---|
| Partner gateway (key auth + rate limit + quota) | None | **Build new** `partner/gateway.py` per LLD §24.1 | ~300 |
| Partner store (Postgres schema + interface) | `discovery/store.py` pattern | **Build new**; 4 tables per LLD §3.12 | ~250 |
| `/partner/v1/recipe` | None (but reuses `/recipe/export` from T1) | **Thin shim** over T1 work | ~80 |
| `/partner/v1/search` | None (but reuses `/discovery/search`) | **Thin shim** | ~80 |
| `/partner/v1/feedback` ingest | `benchmark/runner.py` records runs | **Build new** `partner/feedback_ingest.py` writing into `benchmark_runs` | ~200 |
| TS / Python SDKs | None | **Defer to Sprint 5** — only API in Sprint 4 | 0 in this sprint |
| Partner audit + usage telemetry | None | **Build new** `partner/audit.py` | ~150 |

### 3.6 Summary table

| Phase | Reusable existing LOC | Net‑new LOC est. | Refactor effort |
|---|---|---|---|
| Phase 1 (engine + recipe + Pro + sandbox) | ~28,000 | ~1,800 | Low |
| Phase 2 (marketplace alpha) | ~600 (benchmark runner reuse) | ~5,200 | Medium |
| Phase 3 (partner API skeleton) | ~200 (gateway thin shims) | ~1,300 | Low |
| T0 cleanup (reposition adapters) | n/a | ~150 (test additions) | Medium |
| **Total** | **~28,800 reused** | **~8,450 new** | **Medium** |

**Headline:** Sprint 4 is ~22% net‑new code over an already‑working 34K‑LOC engine. The hard work is **scoping, schema design, vendor‑neutrality enforcement, and the new auth + multi‑tenant deployment** — not greenfield engine work.

---

## 4. Track T0 — Cleanup + repositioning (week 1)

Pre‑requisite for every other track. **Must complete before T1–T5 begin write‑level work** because it touches `agents/`, `workflows/`, and `router.py`.

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T0‑1** | Split `agents/base.py` into `BenchmarkAdapter` (interface for hand‑written baselines used by benchmark runner only) and keep `ProviderAdapter` (only `GenericProtocolAdapter` implements) | `agents/base.py` (23 LOC) | ~80 LOC | 1 h |
| **T0‑2** | Move 7 hand‑written adapter files to `benchmark/baselines/` and refactor each to implement `BenchmarkAdapter` instead of `ProviderAdapter`. **No deletion** — they are critical for benchmark cells. | All 7 files (2,304 LOC) | ~0 — purely move + import update | 4 h |
| **T0‑3** | Remove `requires_benchmark_gate=true` registration of the 7 baselines from `router.py`; `agents.json` cleanup — keep them in `benchmark/baselines.json` (or similar) so benchmark runner can find them | `router.py` (332 LOC) | ~150 LOC for benchmark‑side registry | 3 h |
| **T0‑4** | Refactor `workflows/executor.py` into two roles: `benchmark/runner.py` (already exists; absorb the benchmark‑gated paths) + new `agents/sandbox_runner.py` (BYO‑creds, generic‑adapter‑only, used by future `/execute` endpoint) | `workflows/executor.py` (245 LOC) | ~200 LOC sandbox_runner | 4 h |
| **T0‑5** | Update `.env.example` — strip vendor‑specific API keys (RAZORPAY_*, STRIPE_*, SHIPPO_*, EBAY_*, FIRECRAWL_*, RESEND_*, HUNTER_*) from required block; move under `# Benchmark baseline credentials (optional, hold-out)` block | `.env.example` | ~30 LOC restructure | 1 h |
| **T0‑6** | Strip curated `packages/discovery/sources/*.json` of entries that reference the hand‑written wrappers in their `provider_id` field (we still index them as MCPs/APIs but don't pre‑classify them as ours) | curated JSON files | ~10 lines per file | 2 h |
| **T0‑7** | **Regression test:** run full `make quality` — assert 965 tests still pass after the repositioning. Add 3 new tests asserting hand‑written baselines are NOT reachable from `/goal` (i.e., never appear in `workflow_options[].steps[].best_match.id`) | n/a | ~120 LOC test | 3 h |
| **T0‑8** | Document the repositioning in `docs/agent-discovery-index.md` and `docs/operations.md` — explicitly note that `benchmark/baselines/` is benchmark‑only | docs | ~50 lines | 1 h |

**Total T0 effort: ~19 h ≈ 2.5 days.**

**Definition of done for T0:**
1. `make test` shows 965+ passing, no regressions
2. `grep "from .razorpay" apps/api/planmyagents_api/web/` returns nothing
3. `grep "BenchmarkAdapter" apps/api/planmyagents_api/` returns the 7 baseline files
4. `/goal` for a payment goal returns recommendations from `GenericProtocolAdapter`‑routed MCP servers, not from `razorpay.py`
5. `make benchmark-schedule --capability payment_authorization` still runs (uses baselines)

---

## 5. Track T1 — Phase‑1 finish (recipe export + Pro tier)

Closes the explicit Q1‑Q2 milestones from BUSINESS_PLAN §12.1–§12.2 + PITCH_DECK 12‑month plan.

### 5.1 T1‑A — Recipe export endpoint (the keystone for Tier‑1 partnerships)

Every Tier‑1 partnership (n8n, Cursor, Anthropic) needs an "Add to my host" experience. That experience is downstream of `/recipe/export`.

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T1‑A1** | Implement `GET /recipe/export?goal_id=...&workflow_option=N&format=F` per LLD §2.5 | `web/planning.py` output + `web/app.py` route pattern | ~400 LOC new route + serializers | 6 h |
| **T1‑A2** | Implement 5 recipe generators under `planner/recipe_export/`: `claude_desktop_json.py`, `n8n_json.py`, `cursor_prompt.py`, `markdown.py`, `cli.py` | `web/planning.py` data structures | ~600 LOC across 5 files | 12 h (2.5 h each + shared scaffolding) |
| **T1‑A3** | Cache recipe artifacts in new `recipe_cache` table; key on `(goal_id, workflow_option, format)`; TTL 7 days | `discovery/store.py` pattern | ~150 LOC | 3 h |
| **T1‑A4** | Update frontend `apps/web/src/app/goal/` to add per‑format "Download recipe" buttons | existing `goal` page | ~200 LOC TS/React | 4 h |
| **T1‑A5** | Test suite: per‑format golden fixtures, hermetic generation, round‑trip parsing where applicable (claude_desktop_json and n8n_json must be valid JSON) | `tests/test_*.py` patterns | ~400 LOC | 6 h |

**T1‑A total: ~31 h ≈ 4 days.**

### 5.2 T1‑B — Pro tier billing skeleton

Per HLD §14.1 and LLD §3.10 (existing planned tables `users`, `workspaces`, `workspace_members`, `saved_recipes`). LLD already designed; just need to implement.

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T1‑B1** | Run DDL migration for `users`, `workspaces`, `workspace_members`, `saved_recipes` (already designed in LLD §3.10) | LLD §3.10 schema | ~80 LOC migration file | 2 h |
| **T1‑B2** | Wire Clerk auth — Next.js `@clerk/nextjs` + FastAPI dependency that resolves Clerk JWT → `users.user_id` | none (first auth) | ~250 LOC | 6 h |
| **T1‑B3** | Stripe billing portal handoff — `GET /billing/portal` redirects to Stripe customer portal; webhook ingestion at `/stripe/webhook` updates `plan` column on `users` table | none | ~300 LOC + webhook security | 8 h |
| **T1‑B4** | Implement saved recipes — `POST /recipes/save`, `GET /recipes`, `DELETE /recipes/{id}`, with workspace‑scoped RBAC | `discovery/store.py` pattern | ~250 LOC | 5 h |
| **T1‑B5** | Frontend: gated "Save recipe" button on `/goal` page; `/dashboard/recipes` list; upgrade CTA for free users | existing pages | ~400 LOC TS/React | 8 h |
| **T1‑B6** | Test suite — auth middleware, RBAC, save/list/delete, Stripe webhook signature verification, gate enforcement | tests/ patterns | ~350 LOC | 6 h |

**T1‑B total: ~35 h ≈ 4.5 days.**

**T1 grand total: ~66 h ≈ 8.5 days for one developer; ~4 days if A and B parallelised across two developers.**

**Definition of done for T1:**
1. A user can paste a goal → see plan → click "Download recipe (claude_desktop_json)" → get a valid JSON file that drops into Claude Desktop config
2. A user can sign in with Clerk, save a recipe to their workspace, see it on their dashboard
3. A user can upgrade to Pro via Stripe; webhook updates `users.plan='pro'` within 60 s
4. New tests cover all 5 recipe formats + auth flow + Stripe webhook
5. Total test count ≥ 1,100 (from 965)

---

## 6. Track T2 — Cursor Tier‑1 enablement + BYO‑creds sandbox UI

Cursor partnership requires (a) recipe export already (T1‑A) and (b) "Add to Cursor" button on every recipe + Cursor‑specific recipe shape. BYO‑creds sandbox is the runtime that triggers tier‑8 execution fee in T5.

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T2‑1** | "Add to Cursor" button on `/goal` page recipe download — generates a `cursor.directory` deep‑link URL with the MCP config inline (per Cursor's MCP config schema) | T1‑A2 cursor_prompt generator | ~120 LOC | 3 h |
| **T2‑2** | `/agents/{id}` page — add "Add to Cursor / Add to Claude Desktop / Copy n8n template" trio of buttons | existing agent profile page | ~200 LOC | 4 h |
| **T2‑3** | BYO‑creds sandbox UI — new `/sandbox/{recipe_id}` page where user pastes credentials (per‑step) in browser session, clicks "Run this step", sees structured output, can iterate | none | ~600 LOC TS/React | 14 h |
| **T2‑4** | `POST /sandbox/execute` API endpoint — gated by `PLANMYAGENTS_SANDBOX_ENABLED` env var, takes recipe step + credentials in header, invokes `GenericProtocolAdapter` via new `agents/sandbox_runner.py` (T0‑4 output), enforces cost cap, returns structured result | T0‑4 sandbox_runner + existing `cost/spend_ledger.py` + `agents/protocol.py` | ~300 LOC | 6 h |
| **T2‑5** | Telemetry on "Add to {host}" button clicks → store in `recipe_export_telemetry` table per (recipe_id, host); becomes the success metric for Tier‑1 partnerships | discovery/store.py pattern | ~150 LOC | 3 h |
| **T2‑6** | Test suite — Cursor MCP config validation, sandbox runner BYO‑creds isolation, cost cap respected in sandbox, telemetry write | tests/ patterns | ~300 LOC | 6 h |

**T2 total: ~36 h ≈ 4.5 days.**

**Definition of done for T2:**
1. Every recipe in `/goal` UI has working "Add to Cursor", "Add to Claude Desktop", "Copy n8n template" buttons
2. A user can paste an API key in `/sandbox/recipe_id`, run a single step, see real output; key never persisted server‑side (verify via DB query)
3. Telemetry shows per‑host click counts (lays the foundation for Tier‑1 partnership KPI: "5K WAU from Cursor by Q4")
4. Test count ≥ 1,150

---

## 7. Track T3 — Vendor portal alpha (Phase‑2 foundation)

Alpha = sign‑up + claim verification + profile editor only. NO sponsored placement writes, NO execution‑fee writes (those depend on T5 firewall enforcement before they can go live).

### 7.1 T3‑A — Marketplace store + vendor signup

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T3‑A1** | DDL migration for `marketplace_store` schema (LLD §3.11) — 12 tables. **Include** `execution_fee_subscriptions` and `execution_fee_charges` (with DB CHECK constraint `rate_pct <= 2.0`) so the schema is ready; CRUD endpoints come in Sprint 5 | LLD §3.11 schema | ~400 LOC migration | 4 h |
| **T3‑A2** | New `marketplace/store/` package — `interface.py` + `postgres.py` (Postgres‑only, no SQLite for marketplace — pgvector‑free schema makes it simpler) | `discovery/store.py` pattern | ~600 LOC | 12 h |
| **T3‑A3** | `marketplace/vendor_signup.py` — `POST /vendor/signup`; Clerk + 2FA mandatory; creates Vendor + vendor_members rows | T1‑B2 Clerk middleware | ~250 LOC | 5 h |
| **T3‑A4** | `marketplace/rbac.py` — Owner / Editor / Billing / Read‑only roles; server‑enforced on every `/vendor/*` route | T1‑B2 Clerk middleware | ~200 LOC | 4 h |
| **T3‑A5** | `marketplace/audit.py` — every vendor action writes to `vendor_audit_events` (append‑only) | None | ~150 LOC | 3 h |

### 7.2 T3‑B — Claim verification

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T3‑B1** | `marketplace/claim_verification.py` — DNS TXT polling worker + email verification flow (LLD §23.1) | None | ~350 LOC | 8 h |
| **T3‑B2** | `POST /vendor/claim/{candidate_id}` + `POST /vendor/claim/{candidate_id}/verify` endpoints | T3‑A1 schema | ~150 LOC | 3 h |
| **T3‑B3** | DNS poller background worker — runs every 5 min; checks pending claims; updates `claim_tier` on success | T3‑B1 module | ~120 LOC | 3 h |
| **T3‑B4** | Email verification — token‑emailed code; `POST /vendor/claim/{id}/verify` accepts code | T3‑B1 module | ~150 LOC | 4 h |

### 7.3 T3‑C — Claimed profile editor

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T3‑C1** | `marketplace/profile_editor.py` — `PUT /vendor/profile/{candidate_id}` with server‑enforced invariants (domain match, char limits, P11 firewall: never let vendor set `verification_status`/`is_sponsored`/`benchmark_score`/ranking fields) | None | ~300 LOC | 6 h |
| **T3‑C2** | Moderation pipeline — auto‑checks (profanity filter, URL resolves under vendor domain) + manual queue for first edit per vendor | None | ~200 LOC | 4 h |
| **T3‑C3** | UI overlay — when a claimed profile exists for a candidate, `/agents/{id}` (user‑facing) shows the vendor‑edited fields with "Claimed by vendor" badge | T3‑A2 store + existing agents page | ~250 LOC TS/React | 5 h |

### 7.4 T3‑D — Vendor portal Next.js app

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T3‑D1** | Scaffold `apps/vendor-web/` from `apps/web/` template — Next.js + Tailwind + Clerk | `apps/web/` template | ~500 LOC scaffold | 4 h |
| **T3‑D2** | Vendor portal pages: `/signup`, `/dashboard`, `/claim/{id}`, `/agents/{id}/profile`, `/audit` | none | ~1500 LOC TS/React across 5 pages | 24 h |
| **T3‑D3** | Vendor‑neutrality footer — every page carries the disclosure link to `/disclosure`; "Powered by PlanMyAgents" attribution | none | ~50 LOC | 1 h |
| **T3‑D4** | Deployment — separate vendor deployment for `vendor.planmyagents.com`; CI auto‑deploys from same monorepo | none | ~80 LOC deploy config + GH Actions | 3 h |

### 7.5 T3‑E — Demand‑Data API + founding cohort static page

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T3‑E1** | `marketplace/demand_data_export.py` — K‑anonymity wrapper (LLD §23.4) over existing `demand_recorder.py` data | `discovery/demand_recorder.py` + `demand_store.py` | ~200 LOC | 4 h |
| **T3‑E2** | `GET /vendor/demand-data` endpoint with subscription scope check | T3‑A2 store | ~80 LOC | 2 h |
| **T3‑E3** | `/founding-vendors` static Next.js page listing cohort with badges | curated JSON | ~80 LOC TS/React | 2 h |

### 7.6 T3‑F — Tests

| ID | Task | New | Effort |
|---|---|---|---|
| **T3‑F1** | Test suite for marketplace_store CRUD + RBAC + audit | ~600 LOC | 10 h |
| **T3‑F2** | Test suite for claim verification (mocked DNS + mocked email) | ~400 LOC | 7 h |
| **T3‑F3** | Test suite for profile editor invariants (must reject vendor‑set ranking fields) | ~250 LOC | 4 h |
| **T3‑F4** | Test suite for K‑anonymity (must suppress at K<100) | ~150 LOC | 3 h |

**T3 grand total: ~129 h ≈ 16 days for one developer; ~8 days with two devs splitting backend/frontend.**

**Definition of done for T3:**
1. A vendor can sign up at `vendor.planmyagents.com`, claim apollo‑mcp via DNS, edit description, and the user‑facing `/agents/apollo-mcp` page shows the claimed badge + edited description
2. `vendor_audit_events` has rows for every action; reviewable via `GET /vendor/audit`
3. `marketplace_store.execution_fee_subscriptions` table exists with DB CHECK constraint (verify: `INSERT … rate_pct=3.0` is rejected by Postgres)
4. `GET /vendor/demand-data?capability=web_search` returns aggregated data with `suppressed_segments` when K<100
5. `apps/vendor-web/` deploys to `vendor.planmyagents.com` (staging)
6. Test count ≥ 1,500

---

## 8. Track T4 — Partner gateway skeleton (Phase‑3 foundation)

Skeleton only — 2 endpoints + auth + audit. Full Partner API and SDKs deferred to Sprint 5/6 (per BUSINESS_PLAN §12.5 Y2 Q2 GA target).

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T4‑1** | DDL migration for `partner_store` schema (LLD §3.12) — 4 tables | LLD §3.12 schema | ~200 LOC migration | 3 h |
| **T4‑2** | `partner/store/` package (Postgres) | `discovery/store.py` pattern | ~250 LOC | 5 h |
| **T4‑3** | `partner/gateway.py` — per‑partner API key auth + QPS rate limit + monthly query budget enforcement (LLD §24.1) | None | ~300 LOC | 7 h |
| **T4‑4** | `GET /partner/v1/search` — thin shim over existing `/discovery/search` with partner‑formatted response | existing search route | ~80 LOC | 2 h |
| **T4‑5** | `POST /partner/v1/recipe` — thin shim over T1‑A `/recipe/export` with partner header (`X-Partner-ID`) for telemetry | T1‑A route | ~120 LOC | 3 h |
| **T4‑6** | `partner/audit.py` — every partner call logged to `partner_audit_events`; sampled for high‑volume partners | None | ~150 LOC | 3 h |
| **T4‑7** | Admin script `scripts/create_partner_key.py` — generates new partner key, sets scopes/limits, prints once (for founding partner provisioning) | None | ~100 LOC | 2 h |
| **T4‑8** | Test suite — key auth, scope enforcement, rate limit, audit | tests/ patterns | ~400 LOC | 7 h |

**T4 total: ~32 h ≈ 4 days.**

**Definition of done for T4:**
1. Admin runs `scripts/create_partner_key.py --partner_id=cursor` → gets a key
2. `curl -H "Authorization: Bearer …" /partner/v1/recipe -d '{…}'` returns a recipe
3. `curl` without key returns 401; `curl` over QPS limit returns 429
4. `partner_audit_events` has rows for every call
5. Test count ≥ 1,600

---

## 9. Track T5 — Vendor‑neutrality firewall enforcement (P11)

**Must ship BEFORE T3 vendor portal goes write‑live for sponsored placement or execution fee.** Currently T3 alpha is read‑mostly + claim/profile only, so T5 can ship in parallel and be ready when Sprint 5 adds sponsored placement and execution fee writes.

| ID | Task | Reuse | New | Effort |
|---|---|---|---|---|
| **T5‑1** | CI lint: assert `apps/api/planmyagents_api/agents/router.py` and `workflows/scoring.py` (the ranker) do not import any `marketplace.*` module. Run on every PR. | none | ~80 LOC GH Action + Python checker | 3 h |
| **T5‑2** | DB constraints already in T3‑A1 (`rate_pct <= 2.0` CHECK). Add Python‑side defense: `marketplace/execution_fee_subscription.py::MAX_RATE_PCT = Decimal("2.0")` constant + guard, raises `FirewallViolation` (per LLD §23.6 stub) | T3‑A1 schema | ~150 LOC stub (no runtime hook in this sprint) | 3 h |
| **T5‑3** | `marketplace/firewall_audit.py` — runs nightly via cron; checks: (a) no sponsored placement above natural #1, (b) no execution fee rate > 2%, (c) every active sponsorship has a live disclosure URL. Writes to `firewall_audit_runs` table. Pager alert on breach. | T3‑A2 store | ~250 LOC | 6 h |
| **T5‑4** | `GET /disclosure` page + endpoint — returns JSON inventory of active sponsorships, verified benchmark holders, founding vendors, execution‑fee opt‑in vendors. **In this sprint:** populates the page even though all lists are empty initially (proves the surface works). | T3 marketplace store | ~150 LOC | 4 h |
| **T5‑5** | Documentation — every P11‑touching module gets a doc header comment "CRITICAL: do not import marketplace.* modules from this file. CI enforces." | files in `agents/`, `workflows/` | ~30 lines | 1 h |
| **T5‑6** | Test suite: lint passes, lint fails on a synthetic violation, audit detects synthetic breach, disclosure endpoint returns expected shape | tests/ patterns | ~300 LOC | 6 h |

**T5 total: ~23 h ≈ 3 days.**

**Definition of done for T5:**
1. CI lint blocks any PR that adds `from .marketplace` import to `agents/` or `workflows/`
2. Running `python -m apps.api.planmyagents_api.marketplace.firewall_audit` produces a `firewall_audit_runs` row with `breaches_found=0`
3. `GET /disclosure` returns valid JSON (empty arrays expected this sprint)
4. Synthetic test inserts `rate_pct=3.0` directly into DB and verifies Postgres raises CHECK constraint error
5. Test count ≥ 1,650

---

## 10. Sequencing & dependency graph

```
Week 1                Week 2-3                  Week 4-6               Week 7-8
┌─────────┐          ┌────────────┐            ┌────────────┐         ┌────────────┐
│   T0    │ ──────▶  │   T1-A     │ ────┐      │            │         │            │
│ Cleanup │          │  Recipe    │     │      │            │         │            │
│ (2.5d)  │          │  export    │     │      │            │         │            │
└─────────┘          │  (4d)      │     │      │            │         │            │
     │               └────────────┘     ├─▶    │   T2       │  ──┐    │            │
     │               ┌────────────┐     │      │ Cursor +   │    │    │            │
     │               │   T1-B     │     │      │ Sandbox    │    │    │            │
     ├──────▶        │  Pro tier  │ ────┘      │   (4.5d)   │    │    │            │
     │               │ billing    │            └────────────┘    │    │            │
     │               │  (4.5d)    │                              │    │            │
     │               └────────────┘                              │    │            │
     │                                                           │    │            │
     │               ┌────────────┐            ┌────────────┐    │    │            │
     │               │   T3-A     │            │   T3-B/C   │    │    │            │
     ├──────▶        │ Vendor     │ ───────▶   │ Claim +    │ ───┼──▶ │   T3-D     │
     │               │ store +    │            │ Profile    │    │    │ Vendor     │
     │               │ signup     │            │  (5d)      │    │    │ portal UI  │
     │               │  (5.5d)    │            └────────────┘    │    │  (5d)      │
     │               └────────────┘            ┌────────────┐    │    └────────────┘
     │                                         │   T3-E     │    │
     │                                         │ Demand-API │ ───┘
     │                                         │ + founding │
     │                                         │  (1d)      │
     │                                         └────────────┘
     │
     │               ┌────────────┐            ┌────────────┐
     ├──────▶        │   T4       │ ───────▶   │   T4 cont. │
     │               │ Partner    │            │ (2 routes  │
     │               │ store +    │            │  + audit)  │
     │               │ gateway    │            │  (2d)      │
     │               │  (2d)      │            └────────────┘
     │               └────────────┘
     │
     │               ┌────────────┐
     └──────▶        │   T5       │  (can run anytime week 2-6 once T3-A merged)
                     │ Firewall   │
                     │ (3d)       │
                     └────────────┘
```

**Critical dependencies:**

| Dependency | Why |
|---|---|
| T0 → everything | Repositioning `agents/` and `workflows/` must complete before any new code reaches in |
| T1‑A → T2‑1, T2‑2 | "Add to Cursor" buttons need recipe export to work |
| T1‑A → T4‑5 | `/partner/v1/recipe` is a thin shim over `/recipe/export` |
| T1‑B → T3‑A3 | Vendor signup needs Clerk auth which T1‑B sets up first |
| T3‑A1 → T5‑2, T5‑3 | Firewall enforcement needs the marketplace tables to enforce against |
| T0‑4 → T2‑4 | Sandbox runner extracted in T0 is the engine T2 sandbox UI calls |

---

## 11. Definition of done for Sprint 4

End‑of‑sprint demo (the actual user‑visible delta a VC or partner would see):

1. **A developer can:** paste a goal → see plan → click "Add to Cursor" → drop into Cursor → run the recipe → see results — all within 5 minutes
2. **A developer can:** sign up for Pro via Stripe → save 3 recipes → see them on their dashboard
3. **A developer can:** open `/sandbox/recipe_id`, paste an API key in browser session, run a single recipe step, see structured output; nothing persisted server‑side
4. **A vendor can:** sign up at `vendor.planmyagents.com` (staging) → claim apollo‑mcp via DNS → edit description → see the edit live on `/agents/apollo-mcp` with "Claimed by vendor" badge
5. **A partner can:** be issued an API key via admin script → call `POST /partner/v1/recipe` → get a partner‑formatted recipe back → call again past their quota → get 429
6. **The firewall holds:** any PR that adds `from .marketplace` import to `agents/router.py` fails CI; `firewall_audit_runs` row exists with `breaches_found=0`
7. **Tests:** ≥ 1,650 backend tests passing (from 965), no regressions; `make quality` green
8. **Docs:** `docs/operations.md` updated with vendor‑portal deployment + partner key provisioning runbook; `docs/agent-discovery-index.md` updated with benchmark‑baseline repositioning note

---

## 12. Estimated total effort

| Track | Hours | Days (1 dev) | Days (2 devs parallel) |
|---|---|---|---|
| T0 — Cleanup | 19 | 2.5 | 1.5 |
| T1 — Recipe + Pro | 66 | 8.5 | 4.5 |
| T2 — Cursor + Sandbox | 36 | 4.5 | 2.5 |
| T3 — Vendor portal | 129 | 16 | 8 |
| T4 — Partner gateway | 32 | 4 | 2 |
| T5 — Firewall | 23 | 3 | 1.5 |
| **Total** | **305 h** | **38.5 days** | **20 days** |

**Calendar:**
- 1 dev (founder only): ~8 calendar weeks
- 2 devs (founder + Q2 backend hire): ~4–5 calendar weeks
- 3 devs (founder + backend + frontend): ~3–4 calendar weeks (with vendor‑portal frontend on dedicated dev)

**Recommended:** target 6‑week sprint end‑to‑end with 2 devs. Aligns with BUSINESS_PLAN Q1 (now → +3 months) milestone.

---

## 13. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Repositioning hand‑written adapters breaks benchmark cells | Medium | High | T0‑7 regression test gate; preserve every adapter file under `benchmark/baselines/`; restore from git if benchmark runs break |
| Clerk auth integration takes longer than 6 h (T1‑B2) | Medium | Medium | Time‑box at 12 h; if not done, mock Clerk locally and continue T1‑B3/4/5 against the mock |
| Stripe webhook security harder than estimated | Low | Medium | Use Stripe's published signature verification example; defer Pro tier ship by 2 days if needed (recipe export is the partnership‑critical piece) |
| Vendor portal scope creep (T3 estimate is largest) | High | High | Strict alpha cut: NO sponsored placement, NO execution‑fee writes, NO lead routing; those wait for Sprint 5 |
| DNS poller false negatives (intermittent failures) | Medium | Low | Retry with backoff; alert vendor only after 14 days of failures |
| Multi‑tenant isolation bug in marketplace_store | Low | **Existential** | RBAC tests at endpoint level + integration tests asserting vendor A cannot read vendor B's rows; pen‑test before public alpha |
| Cursor's MCP config schema changes mid‑sprint | Low | Medium | Lock to Cursor's published v1 schema; subscribe to their changelog; add schema validation in T1‑A2 |
| Firewall lint produces false positives blocking PRs | Low | Low | Allowlist for test files that legitimately import both; clear error message pointing to lint config |
| Sandbox UI used to ex‑filtrate keys (security) | Low | High | Credentials live ONLY in browser session storage; never in localStorage; never sent to any URL other than the user's chosen provider; CSP locks down outbound origins |
| 965 baseline test count drifts during refactor | Medium | Medium | T0‑7 regression test; CI fails on test count decrease without explicit `--allow-test-count-decrease` flag |

---

## 14. What's explicitly OUT of scope

| Out | Why deferred |
|---|---|
| Sponsored placement CRUD + UI | Depends on T5 firewall being battle‑tested for ≥ 1 month before going live; Sprint 5 |
| Verified Benchmark request flow + right‑of‑reply | Sprint 5; requires sponsored placement infrastructure first |
| Optional execution fee runtime hook | Sprint 5; tables exist in T3‑A1 but the adapter hook + invoice runner come after |
| Partner SDKs (TS + Python) | Sprint 6; API contracts stable first, then SDK wraps |
| Lead routing | Sprint 5 |
| Anthropic methodology partnership announcement | BUSINESS_PLAN Q4 milestone; relationship building in Sprint 4 but no announcement |
| Enterprise registry private beta | Q4 milestone; Sprint 6/7 |
| Multi‑region Postgres reads | Year 2 |
| SOC2 Type 1 prep | Q3 milestone; Sprint 5/6 |
| Cline / Continue.dev partnerships | Year 2 Q2 |
| GitHub / Microsoft conversations | Year 2 Q3+ |
| Cloud platform OEM deals | Year 2 Q3+ |
| Anything in `docs/marketplace-design.md` §§5–8 (verified bench, sponsored, lead routing) | Sprint 5 — alpha is signup + claim + profile + demand‑data only |
| Cleanup of `evaluation/`, `research/`, `cli/` subpackages | Not blocking; revisit when adding new evals |

---

## 15. After Sprint 4 — what comes next

**Sprint 5 target (Q2 → Q3, ~6 weeks):**
- Verified Benchmark request flow (`marketplace/benchmark_request.py` per LLD §23.3)
- Sponsored placement CRUD + UI + reconciliation cron (LLD §23.2)
- Optional execution fee runtime hook in `agents/protocol.py` + invoice runner (LLD §23.6)
- Lead routing (`marketplace/lead_routing.py` per LLD §23.5)
- Cursor partnership integration LIVE (signed by Q3 per BUSINESS_PLAN §12.3)
- First 3 founding marketplace cohort vendors signed
- First publishable benchmark cell (`web_search` likely)

**Sprint 6 target (Q3 → Q4, ~6 weeks):**
- Anthropic methodology partnership announcement
- Vendor portal GA (out of alpha)
- Partner SDKs (TS + Python npm/PyPI publish)
- Enterprise registry private beta with 1 design partner
- SOC2 Type 1 prep
- First execution‑fee opt‑ins from founding cohort (under 12‑month waiver)
- Series A readiness gate: $50K MRR, 25K WAU, 3 enterprise pilots, 3 publishable benchmark cells, 2 of 3 Tier‑1 partner integrations live, 5 paying marketplace vendors

---

## 16. Open questions (need founder decision before sprint kickoff)

| Question | Default if no decision | Impact |
|---|---|---|
| Run T3 with 1 or 2 devs? | 1 dev (founder) | Calendar 8 weeks vs 5 weeks; founder hire timing |
| Vendor portal hosted at `vendor.planmyagents.com` from day 1, or path under `planmyagents.com/vendor`? | Separate subdomain (per HLD §10.2) | DNS + deployment setup ~1 extra day |
| Sandbox UI gated behind feature flag in production, or alpha‑only at staging? | Feature flag in prod, default OFF | Lets founding cohort test it; ~0 effort delta |
| `marketplace_store` Postgres‑only, or also SQLite for dev? | Postgres‑only (LLD §3.11 written that way) | Adds dependency on docker compose for local marketplace dev; ~0 risk |
| Stripe Live mode in Sprint 4, or Test mode only until Q2 end? | Test mode only — Live waits for first Pro paying user post‑public‑launch | Lower risk of accidental real charges in dev |
| Run founding cohort outreach concurrently with Sprint 4 or wait for T3 done? | Concurrent — founder sends LOIs to first 3 vendors in week 4 | If Sprint 4 slips, vendors are aware of timeline; trust is preserved |

---

## 17. Cross‑references

- **Strategic context driving this sprint:** [BUSINESS_PLAN.md §12 product roadmap](BUSINESS_PLAN.md#12-product-roadmap-24-months), [PITCH_DECK.md 12‑month plan slide](PITCH_DECK.md)
- **Architectural specs being built against:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/HLD.md](docs/HLD.md), [docs/LLD.md](docs/LLD.md)
- **Marketplace mechanics:** [docs/marketplace-design.md](docs/marketplace-design.md) — but Sprint 4 implements only §3 vendor portal + §4 claimed profile + §8 demand data
- **Partnership context:** [docs/partnership-strategy.md](docs/partnership-strategy.md) — Sprint 4 enables the n8n + Cursor outreach
- **Prior sprints (context only):** [sprint.md](sprint.md), [sprint‑2.md](sprint-2.md), [sprint‑3.md](sprint-3.md)
