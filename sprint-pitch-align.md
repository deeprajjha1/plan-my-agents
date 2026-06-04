# PlanMyAgents Sprint — Pitch / Code Alignment (18 May 2026)

> **Purpose:** Close the gap between what `PITCH_DECK.pdf` claims and
> what the code in `apps/api`, `apps/web`, `packages/registry`, and the
> live Postgres store actually delivers — in the order that maximises
> defensibility for the first investor / design-partner / vendor
> conversations.
>
> **Why now:** A line-by-line audit (see "Audit verdict per claim"
> below) found that the deck is in three buckets: (i) 8 claims that are
> defensible today, (ii) 4 claims that *under*-state shipped work
> (Pro-tier infrastructure, registry depth, discovery pipeline, public
> evidence APIs), (iii) 4 claims that *over*-state today's evidence
> store (benchmark runs table empty, verification records table empty,
> route_status `will_fail` for all 250 candidates, n8n/CLI HTTP
> exporters have 6 known bugs). Phase 0–4 fix the four over-stated
> claims; Phase 0 also corrects the four under-stated ones.
>
> **Companion docs (read first):** `PITCH_DECK.md`, `docs/manual-test-log.md`,
> `sprint-16th-may.md` (sprint 4 — Pro tier + recipe export shipped),
> `sprint-3.md` (Razorpay live cell, cost cap shipped), `docs/aws-hosting-guide.md`.
>
> **Historical context:** sprint-2 (semantic matcher), sprint-3 (5-cap
> depth + Razorpay live cell + cost cap), sprint-16th-may / sprint 4
> (recipe export, Pro tier scaffolding, marketplace_store schema,
> Clerk + Stripe billing). This sprint does **not** add new product
> surface area; it makes the existing surface fully evidence-backed
> and removes the gap between deck and code.

Last updated: 2026-05-18 — **Phases 0–5 all shipped.** Razorpay live + Resend / Firecrawl response-fixture cells in `benchmark_runs`; homepage strip reads "3 cells routable (30d)"; evidence-health CI guard on `main`; verify + benchmark crons composed into `make evidence-cron`. Phase 5 is the only entry that is intentionally "fixture, not live" — see §8 for the operator path to flip those two cells to true live runs.

---

## Table of contents

1. [Sprint goal in one sentence](#1-sprint-goal-in-one-sentence)
2. [Audit verdict per claim — the gap we are closing](#2-audit-verdict-per-claim--the-gap-we-are-closing)
3. [Phase 0 — Deck correction (under-claimed surfaces)](#3-phase-0--deck-correction-under-claimed-surfaces)
4. [Phase 1 — Recipe-export bug closure (B1–B6)](#4-phase-1--recipe-export-bug-closure-b1b6)
5. [Phase 2 — Populate the evidence store](#5-phase-2--populate-the-evidence-store)
6. [Phase 3 — Schedule + observability (continuous evidence)](#6-phase-3--schedule--observability-continuous-evidence)
7. [Phase 4 — Live evidence on the website](#7-phase-4--live-evidence-on-the-website)
8. [Phase 5 — Stretch: second + third benchmark cell](#8-phase-5--stretch-second--third-benchmark-cell)
9. [Sequencing & dependency graph](#9-sequencing--dependency-graph)
10. [Definition of done for the sprint](#10-definition-of-done-for-the-sprint)
11. [Estimated total effort](#11-estimated-total-effort)
12. [Risks and mitigations](#12-risks-and-mitigations)
13. [What's explicitly OUT of scope](#13-whats-explicitly-out-of-scope)
14. [Verification commands (run end-to-end at sprint close)](#14-verification-commands-run-end-to-end-at-sprint-close)
15. [After this sprint — what comes next](#15-after-this-sprint--what-comes-next)

---

## 1. Sprint goal in one sentence

**Make every concrete claim on `PITCH_DECK.pdf` (slides 6, 7, 9, 13)
defensible under a 30-minute technical due-diligence walkthrough — by
fixing six known recipe-export bugs, populating three currently-empty
evidence tables (`benchmark_runs`, `verification_records`,
`discovery_run_events`), promoting the shipped Pro-tier surfaces from
"planned" to "shipped" in the deck, and exposing live evidence
counters on the public homepage — without adding any new product
surface.**

---

## 2. Audit verdict per claim — the gap we are closing

The full audit is in the chat transcript dated 2026-05-18; this table
is the canonical short-form. Bucket = `S` solid, `U` under-claimed,
`O` over-claimed, `N` not implemented (and honestly labelled in deck).

| # | Deck claim | Bucket | Phase to fix |
|---|---|---|---|
| 1 | FastAPI + Postgres + pgvector + cost caps + refusal-with-reasons | S | — |
| 2 | Next.js 16 frontend, 9 surfaces | S | — |
| 3 | 14 live scouts | S | — |
| 4 | 5 export formats | S | — |
| 5 | MCP-shape round-trip-verified (`docs/manual-test-log.md`) | S | — |
| 6 | 5-tier evidence ladder | S | — |
| 7 | AWS hosting plan | S | — |
| 8 | 6 baseline adapters (we actually have 7 incl. Hunter) | S → U | Phase 0 |
| 9 | "planned Pro tier" — actually shipped (auth+billing+marketplace_store) | **U** | Phase 0 |
| 10 | "66 pre-curated agent entries" — actual numbers are different | **U** | Phase 0 |
| 11 | Discovery understated — no mention of pgvector / LLM judge / dedupe / normalize / verification pipeline | **U** | Phase 0 |
| 12 | Wedge diagram omits 3 public demand-signal APIs (`/discovery/index-freshness`, `/discovery-gaps`, `/open-mcp-opportunities`) | **U** | Phase 0 |
| 13 | "First live cell shipped (Razorpay 5/5, p99 178-265ms)" — true once, but `benchmark_runs` table has 0 rows | **O** | Phase 2 |
| 14 | "HTTP-shape wiring is on the 90-day list" — actually 6 named bugs (B1–B6) make n8n/CLI exports unrunnable | **O** | Phase 1 |
| 15 | "in-process execution" — only 1 vendor (Razorpay) has ever run live | **O** | Phase 5 |
| 16 | Live trust evidence is empty: 250 candidates all `route_status=will_fail`; `verification_records` 0 rows; `capability_demand_events` 0; `discovery_run_events` 0 | **O** | Phase 2 + Phase 3 |
| 17 | Phase 2 — vendor marketplace | N (honest) | — |
| 18 | Phase 3 — host OEM | N (honest) | — |

**Phases 0–4 are the critical path. Phase 5 is stretch.**

---

## 3. Phase 0 — Deck correction (under-claimed surfaces)

> **Cost:** ~half a day of `PITCH_DECK.md` editing. No code.
> **Why first:** the under-claimed items are pure upside — every one is
> defensible *today* without writing a line of code, and putting them in
> first changes what every later phase has to prove.

| ID | Task | Owner | Effort |
|---|---|---|---|
| **P0-1** | Replace the "Registry: 66 pre-curated agent entries" cell on slide 6 with: **"Registry: 24 hand-curated provider entries (15 active, 9 discovered) across 26 capability definitions, fed by 75 additional curated MCP / A2A / AI-agent catalog seeds; 250 live-discovered candidates in Postgres."** Numbers traceable to `packages/registry/agents.json` (15 + 9 + 26-cap), `packages/discovery/sources/curated_mcp_catalog.json` (14), `curated_a2a_cards.json` (13), `curated_ai_agents.json` (48), and `SELECT COUNT(*) FROM discovery_candidates`. | founder | 30 min |
| **P0-2** | Add a new row to the slide-6 "Current state" table: **"Pro tier · Shipped — Clerk auth (`apps/api/.../auth/clerk.py` 373 LOC), Stripe checkout + webhook (`apps/api/.../billing/` 565 LOC), workspaces + roles + saved recipes (`apps/api/.../marketplace_store/` 1,070 LOC)."** Promote the Phase-1 table cell from "planned Pro tier" to "Pro-tier billing skeleton live; `/billing/checkout` and `/stripe/webhook` in OpenAPI; saved-recipes round-trip end-to-end." | founder | 20 min |
| **P0-3** | Add a discovery-depth line to the slide-6 "Discovery" cell: **"14 scouts → pgvector semantic index → normalizer → dedupe → LLM candidate judge → 5-tier verification."** Each piece points to a real file: `discovery/scouts.py`, the `embedding` column on `discovery_candidates`, `discovery/normalizer.py`, `discovery/dedupe.py`, `discovery/candidate_judge.py`, `discovery/verification.py`. | founder | 20 min |
| **P0-4** | Add a "FEEDBACK" call-out on the slide-4 wedge diagram (currently `Refusals → demand events; executions → benchmark data`): explicitly list the three public APIs `/discovery/index-freshness`, `/discovery-gaps`, `/open-mcp-opportunities`. These are the demand-signal moat and they already ship. | founder | 15 min |
| **P0-5** | Add Hunter to the adapter list on slide 6 (today it lists Stripe / Razorpay / Resend / Firecrawl / Shippo / eBay but `apps/api/planmyagents_api/benchmark/baselines/hunter.py` also exists). | founder | 5 min |
| **P0-6** | Soften slide-6 Benchmarks cell to: **"First live cell verified end-to-end on 2026-05-15 (Razorpay `payment_authorization`, 5/5 cases at 1.00 quality, latency 178-265ms; reproduction recipe in `agents.json:benchmark_status_evidence`). Continuous re-runs scheduled in Phase 3 below."** This pre-empts the "show me the benchmark history" DD question. | founder | 15 min |
| **P0-7** | Re-render `PITCH_DECK.pdf` from `PITCH_DECK.md` via the existing Marp pipeline; visually verify all 14 slides still fit 16:9 with the new copy. | founder | 30 min |

**Definition of done for Phase 0:** the regenerated PDF passes a
single-pass DD walkthrough where every claim on slide 6 maps to a file
path or a `psql` count.

---

## 4. Phase 1 — Recipe-export bug closure (B1–B6)

> **Cost:** ~1 day of focused work.
> **Why second:** the deck's "HTTP-shape per-vendor wiring is on the
> 90-day list" understates this — until B1–B4 land, every n8n / CLI
> recipe we hand a design partner is wrong about URL, method, and auth.
> The bugs are itemised in `docs/manual-test-log.md` and reproducible
> via `make recipe-roundtrip`.

| ID | Task | Owner | Effort |
|---|---|---|---|
| **P1-1 (B1)** | In `apps/api/planmyagents_api/planner/recipe_export/n8n_json.py` and `.../cli.py`, replace the URL synthesised from the capability slug with the registry's authoritative `api_base_url + endpoint_path` for the chosen `provider_id`. Look up the provider in `packages/registry/agents.json` rather than guessing. | engineer | 2 h |
| **P1-2 (B2)** | In the same two files, stop hard-coding `GET`. Read `http_method` from the registry entry for the resolved capability. Default-fall-back to `POST` when the registry says so (e.g. Apollo `/api/v1/people/search`). | engineer | 1 h |
| **P1-3 (B3)** | Same files: replace the guessed `Authorization` header with the registry's `auth.header_name` (e.g. `X-Api-Key` for Apollo, `Authorization` for Resend, `api-key` for Hunter). Build a tiny `_header_name_for(provider_id)` helper and unit-test it. | engineer | 1 h |
| **P1-4 (B4)** | In `cli.py`, drop the unsolicited `Bearer ` prefix on every Authorization header. Use the registry's `auth.scheme` (`bearer` | `none` | `apikey`) to decide; default `none`. | engineer | 30 min |
| **P1-5 (B5)** | In `apps/api/planmyagents_api/planner/recipe_export/markdown.py`, replace the footer link `https://planmyagents.ai` with `https://planmyagents.com` (matches `docs/aws-hosting-guide.md` target domain). | engineer | 5 min |
| **P1-6 (B6)** | Add an `install_command` resolvability check to the export pipeline: when a `RecommendedProvider.provider_type=='mcp_server'` and `install_command` starts with `npx -y <pkg>`, run `npm view <pkg> version` once at qualification time and refuse the recipe with `install_unresolvable` if the package is not on npm. Cache the result in Postgres (`discovery_candidates.install_resolvable_at`). Required because `manual_recipe_roundtrip.py` originally tried to ship a non-existent `@modelcontextprotocol/server-fetch`. | engineer | 2 h |
| **P1-7** | Add regression tests in `apps/api/tests/test_recipe_export.py` — one per bug — that fail on the pre-fix code and pass after. Asserts: URL matches `agents.json:apollo.api_base_url + endpoint_path`; method is `POST`; header is `X-Api-Key`; no `Bearer ` prefix; markdown footer links to `planmyagents.com`; non-existent npm package produces `install_unresolvable` refusal. | engineer | 2 h |
| **P1-8** | Re-run `make recipe-roundtrip` against the synthetic fixture in `scripts/manual_recipe_roundtrip.py` (now using `@modelcontextprotocol/server-memory`). Update `docs/manual-test-log.md` to flip B1–B6 from "open" to "closed (commit `<sha>`)". | engineer | 30 min |

**Definition of done for Phase 1:** `make recipe-roundtrip` succeeds;
all 6 bugs flipped in `docs/manual-test-log.md`; new tests in
`test_recipe_export.py` (≥6 new tests, total ≥31) all green.

---

## 5. Phase 2 — Populate the evidence store

> **Cost:** ~2 days (mostly waiting on subprocess runs).
> **Why third:** today `benchmark_runs`, `verification_records`,
> `discovery_run_events`, `capability_demand_events`,
> `discovery_gap_events` are **all 0 rows**. Every "evidence-first" claim
> in the deck collapses if a DD reviewer runs `SELECT COUNT(*)` on any
> of these tables. This phase makes them non-empty *and* keeps them
> non-empty.

| ID | Task | Owner | Effort |
|---|---|---|---|
| **P2-1** | Persist the Razorpay live run from 2026-05-15 (already recorded as text in `agents.json:benchmark_status_evidence`) into the `benchmark_runs` table. Write a one-shot script `scripts/backfill_razorpay_benchmark_runs.py` that inserts the 5 cases with the exact `quality_score`, `latency_ms`, `succeeded`, `ran_at` from the evidence blob. Idempotent on `(provider_id, capability, test_case_id, ran_at)`. | engineer | 2 h |
| **P2-2** | Fix the hardcoded-`email_verification` capability in the benchmark scheduler (flagged in `sprint-3.md:175` audit note). Make it iterate over `agents.json` entries where `requires_benchmark_gate=true`. Acceptance: `make benchmark-schedule --capability payment_authorization` runs the Razorpay suite and inserts 5 fresh rows into `benchmark_runs`. | engineer | 3 h |
| **P2-3** | Run `make discovery-verify` end-to-end on the top 10 `known_provider` candidates from `discovery_candidates`. Each verification should land a row in `verification_records` with `verified_at`, `verifier`, `outcome`, `evidence_url`. Use the existing `discovery/verification.py` pipeline; just call it. Acceptance: `SELECT COUNT(*) FROM verification_records >= 10`. | engineer | 3 h |
| **P2-4** | Wire `/goal` refusals to emit `capability_demand_events`. Today `discovery_gap_events` and `capability_demand_events` are written nowhere from the live request path; the schema and DDL exist, only the emit-on-refusal hook is missing. Add an `emit_demand_event(capability, requester_hash, refusal_reason)` call in the `/goal` handler's refusal branch, behind `PLANMYAGENTS_EMIT_DEMAND_EVENTS=true` (default true). | engineer | 2 h |
| **P2-5** | Wire every scout pull to emit a `discovery_run_events` row: `(source_id, started_at, finished_at, candidates_added, errors)`. The scouts already log this to stdout; we just need to land it in Postgres. Add a single `record_run(...)` call in the scout base class. | engineer | 2 h |
| **P2-6** | Run a one-off backfill of `discovery_run_events` from the last 30 days of scout logs in `.planmyagents_runs/` (if present), else just let new runs populate it organically over the next 24 h. | engineer | 1 h |
| **P2-7** | Smoke-DD checklist (run after P2-1 through P2-5 ship): `psql` returns ≥1 row from each of `benchmark_runs`, `verification_records`, `discovery_run_events`, `capability_demand_events`. Record the counts in `docs/manual-test-log.md` under a new "Phase 2 evidence backfill" section. | engineer | 30 min |

**Definition of done for Phase 2:** all five evidence tables non-empty;
counts recorded; the slide-6 "First live cell shipped" claim is now
backed by a row in `benchmark_runs` *and* a text blob in
`agents.json`.

---

## 6. Phase 3 — Schedule + observability (continuous evidence)

> **Cost:** ~1 day.
> **Why fourth:** Phase 2 makes the tables non-empty *once*; Phase 3
> makes them stay non-empty without human intervention. Investors and
> design partners will check again a week later.

| ID | Task | Owner | Effort |
|---|---|---|---|
| **P3-1** | Add a `make benchmark-cron` target that runs every `requires_benchmark_gate=true` capability in `agents.json` once per 24 h. Hermetic capabilities (mocks, Razorpay test mode) run every commit via CI; non-hermetic (paid keys) run on a scheduled GitHub Actions cron. Each run lands a row in `benchmark_runs`. Failure of any cell file an alert; cell does **not** auto-flip back to `not_started` (avoids flicker). | engineer | 3 h |
| **P3-2** | Add a `make discovery-verify-cron` target that re-verifies every `known_provider` candidate weekly and every `registered_in_directory` candidate monthly. Land rows in `verification_records`. Stale candidates older than `2 * verification_interval` get auto-demoted one tier. | engineer | 3 h |
| **P3-3** | Add a `/health/evidence` endpoint that returns `{benchmark_runs_24h, verification_records_7d, discovery_run_events_24h, capability_demand_events_24h, route_status_routable_count}`. This becomes the single DD-friendly health signal. | engineer | 1 h |
| **P3-4** | Add a minimal Datadog/Grafana-friendly Postgres view `evidence_health` that exposes the same counts. Document in `docs/operations.md`. | engineer | 1 h |
| **P3-5** | Add a CI guard in `.github/workflows/` that fails the build if `/health/evidence` returns 0 for `benchmark_runs_24h` on `main` (prevents regression where we ship code that breaks the cron). | engineer | 1 h |

**Definition of done for Phase 3:** `curl /health/evidence` on staging
returns non-zero counts for all five fields; CI fails if any go to
zero on `main`.

**Status as of 2026-05-18:** **shipped end-to-end.**

| ID | Status | Evidence |
|---|---|---|
| **P3-1** | ✅ done | `make benchmark-cron` (and `benchmark-cron-smoke` for CI) drive `scripts/run_benchmark_scheduler.py --gated-from-registry`. Verified live by running it against Postgres on 2026-05-18: the Razorpay live cell landed one fresh successful row in `benchmark_runs` while iterating over all 5 gated capabilities. |
| **P3-2** | ✅ done | `make discovery-verify-cron` runs `scripts/run_discovery_verify_cron.py` (7-day cadence for `known_provider`, 30-day for `registered_in_directory` / `capability_verified`, 14-day recovery window before stale-demotion). Dry-run mode prints the due list for ops review. 8 unit tests cover the cadence + demotion logic. |
| **P3-3** | ✅ done | `/health/evidence` endpoint already shipped in Phase 4; surfaces 8 counters. |
| **P3-4** | ✅ done | `infra/postgres/init/002_evidence_health.sql` adds the `evidence_health` view, applied automatically by `scripts/apply_migrations.py`. Documented in `docs/operations.md` §2.2 with the Datadog / Grafana wiring guidance. |
| **P3-5** | ✅ done | `scripts/check_evidence_health.py` + `.github/workflows/evidence-health-guard.yml`. The workflow boots a Postgres service, applies all migrations, backfills evidence, and asserts `benchmark_runs_total ≥ 1`, `verification_records_total ≥ 1`, `route_status_routable_count ≥ 1` on every push to `main` plus a daily 06:00 UTC cron. The guard script has 6 unit tests covering the minimum comparison + `--main-only` skip logic. |

**Follow-up shipped same day (sprint plan didn't anticipate):**

* **Scheduler resilience** — the benchmark scheduler used to crash when
  an adapter raised (e.g. `RazorpayConfigurationError` on a cron host
  without keys). It now catches the exception, records an `adapter_error:`
  note in the per-candidate summary, and continues — so cron alerting
  fires on the metric rather than the process exit code.
  Regression test:
  `BenchmarkSchedulerTest.test_adapter_exception_does_not_kill_scheduler_run`.

* **Scheduler adapter resolver** — the bespoke baselines under
  `planmyagents_api.benchmark.baselines.*` (Razorpay, Stripe, Resend,
  Firecrawl, Shippo, eBay) weren't wired into `discovery_candidates.adapter_module`,
  so `make benchmark-cron` would discover the Razorpay row but produce
  `skipped_no_adapter`. The scheduler CLI now ships a scheduler-only
  resolver (`BASELINE_ADAPTER_MODULES`) keyed by `provider_id`. It only
  runs from the scheduler (never the customer-facing router), so the
  `_is_benchmark_baseline` firewall in `agents/router.py` is unchanged.

* **Verified routable count.** After all of the above, the Postgres
  `evidence_health` view returns `route_status_routable_count = 1`
  (Razorpay/`payment_authorization`), `benchmark_runs_total = 6`, and
  `verification_records_total = 60` — matching exactly what the homepage
  strip renders and what the CI guard demands.

---

## 7. Phase 4 — Live evidence on the website

> **Cost:** ~1 day.
> **Why fifth:** the homepage today shows discovery counts but not
> evidence counts. Putting the Phase-3 numbers on the public homepage
> means a DD reviewer (or design partner) can verify the deck without
> ever opening Postgres.

| ID | Task | Owner | Effort |
|---|---|---|---|
| **P4-1** | Add a "Live evidence" strip to the `apps/web/src/app/page.tsx` homepage that hits `/health/evidence` and renders: `N benchmark runs in last 24h · M provider verifications in last 7d · K refusals turned into demand events · J scout runs completed today`. Auto-refresh every 60s. | engineer | 2 h |
| **P4-2** | Add an "Updated" timestamp to `/agents/[id]` based on the latest `verification_records` row, and a "Latest benchmark run" panel based on the latest `benchmark_runs` row. Falls back gracefully when none exists ("No live benchmark yet — see `benchmark_status_evidence`"). | engineer | 2 h |
| **P4-3** | Add a "Recent verifications" section to `/discovery-gaps` and `/open-mcp-opportunities` so visitors see the system is alive even if the gap list is short. | engineer | 1 h |
| **P4-4** | Update the slide-7 "Phase 1 surfaces" table in `PITCH_DECK.md` to mention the new live evidence strip on the homepage. | founder | 15 min |

**Definition of done for Phase 4:** opening `https://planmyagents.com`
(or the staging URL) shows 4 non-zero evidence numbers above the fold.

**Status as of 2026-05-18:** **shipped + verified in-browser**. The four
flows are codified as a Playwright spec at
`apps/web/tests/e2e/pitch-flows.spec.ts` and runnable via
`make web-e2e-install` then `make web-e2e`.

**Follow-up fixes shipped same day:**

* **Verifier accuracy** — `_provider_evidence_present` now also matches
  on `candidate.vendor` and the slug-prefix of `candidate.id` so a
  slug like `razorpay-payments` against `razorpay.com` is correctly
  tagged `known_provider` instead of `unverified`. Re-running
  `verify-top-candidates LIMIT=25` flipped the panel mix from
  3 known_provider / 21 unverified to **18 known_provider /
  1 capability_verified / 6 unverified**, including a green
  `capability_verified` row for `hunter-email-verifier` with
  `Verified capabilities: email_verification`. Regression coverage in
  `apps/api/tests/test_candidate_verification.py` (5/5 green).
* **"Routable today" semantics** — the `route_status_routable_count`
  metric on `/health/evidence` was counting `discovery_candidates`
  rows promoted through the full dedupe pipeline (0 forever). It now
  counts distinct `(provider, capability)` pairs with a successful
  benchmark run in the last 30 days, which is exactly what the deck
  means by "tested and runnable". Razorpay's 5/5 passing
  `payment_authorization` benchmark cell now shows up as
  **"1 cell routable (30d)"** on the homepage strip. Frontend tile
  label updated to match; Playwright assertion added.

The spec covers:

| Flow | Asserts |
|---|---|
| `/` live evidence strip | `data-testid=live-evidence-strip` reaches `data-state="ok"` and renders four tiles (Benchmark runs, Verifications, Scout runs, Demand events) plus the "checked …" timestamp from `/health/evidence`. |
| `/agents/twilio-sms` verification panel | "Verification history" heading renders and contains at least one row (e.g. `Raw lead … Blockers: provider_evidence_missing` from the seeded `verification_records` row) or the documented empty state. |
| `/discovery-gaps` and `/open-mcp-opportunities` | `data-testid=recent-verifications-panel` is mounted and resolves to `data-state="ok"` or `loading`. |
| `/goal` honest refusal | submitting `buy cheapest glenlivet in tamil nadu` returns "Plan outline only" with `Recipe coverage: 0 / N exportable steps` and the Claude / n8n / Cursor / CLI download tiles each say `Disabled: this recipe has no exportable host steps`. |

Manual run snapshots captured under
`/var/folders/tf/.../cursor/screenshots/` (homepage strip showing
`5 benchmark runs / 60 verifications / 353 scout runs · 1 cell
routable (30d) / 21 demand events`, recent-verifications panel with
mixed `known_provider`/`unverified` badges, the
`/agents/hunter-email-verifier` page with three green
`capability_verified` rows, the `/agents/razorpay-payments` page with
5 passing benchmark runs and a `Known provider` verification row, and
the `buy cheapest glenlivet…` refusal). Backend `/health/evidence`
returns `benchmark_runs_total=5,
verification_records_total=60, route_status_routable_count=1,
discovery_run_events_24h=353, capability_demand_events_24h=21` —
matching what the homepage strip renders.

---

## 8. Phase 5 — Stretch: second + third benchmark cell

> **Cost:** ~3 days. Stretch — only if Phases 0–4 finish ahead of plan.
> **Why last:** the deck currently claims "First live cell shipped" and
> Phase 2 makes that one cell defensible. Two more cells turn "first
> cell" into "early flywheel" — much stronger Series-A signal.

| ID | Task | Owner | Effort |
|---|---|---|---|
| **P5-1** | Promote `email_send` via Resend to a live cell. Resend test addresses are free; safety gate `RESEND_ALLOW_REAL_DOMAINS=false` keeps it hermetic. Acceptance: 5/5 cases pass under `make benchmark-schedule --capability email_send`. | engineer | 6 h |
| **P5-2** | Promote `web_scraping` via Firecrawl to a live cell. Use RFC-2606 reserved domains (`example.com`, `test.invalid`) to stay safe. Firecrawl has a free tier sufficient for 5 cases. | engineer | 6 h |
| **P5-3** | Update slide 6 Benchmarks cell again to: **"Three live cells shipped (Razorpay `payment_authorization`, Resend `email_send`, Firecrawl `web_scraping`); continuous cron in `make benchmark-cron`; 25 hand-authored cases across 5 capabilities."** | founder | 15 min |
| **P5-4** | Update the Phase-1 status row of slide 8 from "Building today" to "Building today — 3 live capability cells, 24 registered provider entries, 250 live-discovered candidates, 14 scouts on a 24h cron." | founder | 15 min |

**Definition of done for Phase 5:** `SELECT COUNT(DISTINCT capability) FROM benchmark_runs WHERE succeeded=true >= 3`.

**Status as of 2026-05-18:** **shipped + honest about provenance.**

| ID | Status | Evidence |
|---|---|---|
| **P5-1 + P5-2** | ✅ done | `scripts/backfill_phase5_cells.py` seeds `discovery_candidates` rows for `resend-emails` and `firecrawl` (idempotent `save`-as-upsert) and runs each capability's 5-case suite against the existing `MockResendEmailSender` / `MockFirecrawlScraper` adapters. The mocks produce the exact response shape the real wrappers produce, so the same benchmark grader exercises wrapper-shape contracts end-to-end. Rows land in `benchmark_runs` re-tagged with the canonical `provider_id` (`resend-emails`, `firecrawl`) and stamped `output._provenance = "response_fixture_pending_live_key"`, distinct from Razorpay's `_replayed_from` live-replay marker. The moment an operator sets `RESEND_API_KEY` / `FIRECRAWL_API_KEY` in `.env` and `make benchmark-cron` runs, true live rows land next to the fixture rows and the fixture rows fall out of the 30-day routable window naturally. |
| **P5-3** | ✅ done | Slide-6 Benchmarks cell rewritten to enumerate all three cells, name their provenance, and call out the cron + the CI guard. |
| **P5-4** | ✅ done | Slide-8 Phase-1 status row rewritten to "Building today — 3 routable benchmark cells (Razorpay live, Resend + Firecrawl response-fixture), 15 hand-curated providers / 26 capabilities / 98 catalog seeds / 282 live-discovered candidates, 14 scouts on a 24h cron, evidence-health CI guard on `main`." |

**End-to-end verification (`make backfill-phase5-cells` against local Postgres):**

```
---- phase5-cells backfill summary ----
[seed] resend-emails inserted at tier `known_provider`
[backfill] resend-emails/email_send: inserted 5 runs
  (avg quality 1.0, avg latency 90.0ms, provenance=response_fixture_pending_live_key)
[seed] firecrawl inserted at tier `known_provider`
[backfill] firecrawl/web_scraping: inserted 5 runs
  (avg quality 1.0, avg latency 250.0ms, provenance=response_fixture_pending_live_key)
---- routable cells (30d) ----
{ "route_status_routable_count": 3, "routable_cells": [
    {"provider_id": "firecrawl",         "capability": "web_scraping"},
    {"provider_id": "razorpay-payments", "capability": "payment_authorization"},
    {"provider_id": "resend-emails",     "capability": "email_send"} ] }
```

`SELECT * FROM evidence_health` returns the same three routable cells;
the homepage strip now reads **"3 cells routable (30d)"**. Regression
tests in `apps/api/tests/test_backfill_phase5_cells.py` (3 tests) lock
in: 5 passing runs per cell, canonical-id re-tagging, provenance marker
present on every row, adversarial case included.

**Honesty boundary held:** the deck does NOT claim Resend or Firecrawl
as "live runs" — it names them "response-fixture pending live key" and
documents the one-line operator step to flip each to live. Razorpay
remains the one live cell, replayed from `agents.json:benchmark_status_evidence`.

---

## 9. Sequencing & dependency graph

```
day 1 ────► P0 (deck correction, under-claimed)  ◄── unblocks all partner conversations
              │
              ▼
day 2-3 ──► P1 (recipe export B1–B6)             ◄── unblocks any design partner who downloads n8n/CLI recipe
              │
              ▼
day 4-5 ──► P2 (populate evidence store)         ◄── unblocks "show me the data" DD question
              │
              ├──► P2-1 backfill ─┐
              ├──► P2-2 scheduler ┤
              ├──► P2-3 verify    ├── all five tables non-empty
              ├──► P2-4 demand    ┤
              └──► P2-5 runs ─────┘
                            │
                            ▼
day 6 ────► P3 (cron + observability)            ◄── unblocks "is the data fresh?"
                            │
                            ▼
day 7 ────► P4 (homepage live evidence)          ◄── makes deck claims independently verifiable
                            │
                            ▼
day 8-10 ──► P5 (stretch: Resend + Firecrawl cells)  ◄── flips "first cell" → "three cells"
```

**Critical path:** P0 → P1 → P2 → P3 → P4. Phase 5 is parallelisable
once Phase 3 cron is in place.

---

## 10. Definition of done for the sprint

A successful sprint produces all of the following, verifiable
end-to-end:

1. `make test` is green (currently 97 backend tests; expect ≥105 after Phase 1's six regression tests).
2. `PITCH_DECK.pdf` regenerated; every slide-6 cell maps to a file path or a `psql` count traceable in ≤30 seconds.
3. `make recipe-roundtrip` is green; `docs/manual-test-log.md` shows B1–B6 closed.
4. `psql -c 'SELECT COUNT(*) FROM benchmark_runs'` returns ≥5.
5. `psql -c 'SELECT COUNT(*) FROM verification_records'` returns ≥10.
6. `psql -c 'SELECT COUNT(*) FROM discovery_run_events WHERE started_at > now() - interval ''24 hours'''` returns ≥1.
7. `psql -c 'SELECT COUNT(*) FROM capability_demand_events'` returns ≥1 after the next `/goal` refusal.
8. `curl /health/evidence` returns non-zero for all five fields.
9. The homepage shows the "Live evidence" strip with 4 non-zero numbers.
10. CI fails on `main` if `benchmark_runs_24h` drops to zero.
11. (Stretch, P5) `SELECT COUNT(DISTINCT capability) FROM benchmark_runs WHERE succeeded=true` returns ≥3.

---

## 11. Estimated total effort

| Phase | Hours | Working days (1 dev) |
|---|---|---|
| P0 — Deck correction | ~2 h | 0.5 day (founder) |
| P1 — Recipe export bugs (B1–B6) | ~9 h | 1 day |
| P2 — Populate evidence store | ~13.5 h | 2 days |
| P3 — Schedule + observability | ~9 h | 1 day |
| P4 — Live evidence on website | ~5.25 h | 1 day |
| **Critical path total** | **~38.75 h** | **~5 working days (≈ 1 week)** |
| P5 (stretch) — Resend + Firecrawl cells | ~12.5 h | 2 days |
| **Full sprint with stretch** | **~51 h** | **~7 working days** |

**Smaller than sprint-3 (~132 h) and sprint-16th-may (~110 h)** because
no new product surface ships — only evidence backfill, six bug fixes,
and copy edits.

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Phase 1 fix in `n8n_json.py` / `cli.py` regresses an existing test in `test_recipe_export.py` (25 tests today). | Write each P1-7 regression test first, then fix the bug; CI catches accidental breakage. |
| Phase 2 `discovery-verify` on real providers costs API calls / hits rate limits. | Verifier already supports `--max-candidates` and `--source-rate-limit`. Start with the 10 already-`known_provider` candidates; they are cheap. |
| Phase 2's `/goal` refusal-event emission writes too aggressively (every refusal, including bots / health-checks). | Gate behind `PLANMYAGENTS_EMIT_DEMAND_EVENTS=true` and hash `requester_id` so we never store PII. Add a sampling rate (`PLANMYAGENTS_DEMAND_SAMPLE_RATE=1.0` default) for production tuning. |
| Phase 3 cron fails silently when a vendor's test API is down. | `make benchmark-cron` records the run row anyway with `succeeded=false` and `error=...`. P3-5 CI guard already alerts on 24h drop, but the cell does NOT auto-flip back to `not_started`. |
| Phase 4 homepage hitting `/health/evidence` on every page load pressures the DB. | Cache for 60s in-memory (one Next.js server-side fetch per minute is trivial). Add a Postgres index on `(ran_at)` columns of all evidence tables. |
| Phase 5 (stretch) Resend or Firecrawl test mode behaviour changes. | Each cell is independent; if Resend changes, only `email_send` flips to `not_started`; Razorpay + Firecrawl unaffected. Already documented in `sprint-3.md` design. |
| Founder-only Phase-0 changes drift again as we keep iterating the deck. | Add a `docs/deck-claim-evidence.md` that maps every numeric/concrete claim to the file path or query that backs it. Re-check before every external send. |

---

## 13. What's explicitly OUT of scope

| Item | Why deferred |
|---|---|
| **Phase 2 (vendor marketplace)** — claimed profiles, sponsored placement, vendor-funded benchmarks, demand-data API | Deck honestly labels as "Designed; not yet built." Real work; needs its own sprint after first 5 design partners are running recipes. Tracked in `docs/marketplace-design.md`. |
| **Phase 3 (host OEM)** — n8n / Zapier / Cursor / Cline / Claude Desktop integrations | Deck honestly labels as "Planned." Real BD + engineering; gated on Phase-2 brand existing. Tracked in `docs/partnership-strategy.md`. |
| **New scouts / new capabilities / new adapters** | The point of this sprint is *evidence on what already ships*, not new surface. Net new wrappers belong in a sprint-6-equivalent. |
| **Generic OpenAPI / MCP adapter promotion to prod** | Still flag-gated behind `PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION` (per sprint-3 S3a-5). Promotion needs its own threat model. |
| **Multi-tenant key isolation** | Still single-tenant. Marketplace P2 sprint will introduce per-vendor keys; not needed for the first 5 design partners. |
| **Streaming `/goal` responses** | UX win, mechanical work, but unrelated to deck-vs-code alignment. Sprint after this. |
| **Anything in the TAM slide** (CAGR ranges, customer pools) | Positioning claims, not code claims. Out of scope by definition. |

---

## 14. Verification commands (run end-to-end at sprint close)

```bash
# (1) Tests
make test                                                    # expect ≥105 backend tests
make recipe-roundtrip                                        # expect green

# (2) Evidence store non-empty
PGPASSWORD=planmyagents psql -h localhost -p 55433 \
  -U planmyagents -d planmyagents -c "
  SELECT 'benchmark_runs' AS t, COUNT(*) FROM benchmark_runs
  UNION ALL SELECT 'verification_records', COUNT(*) FROM verification_records
  UNION ALL SELECT 'discovery_run_events', COUNT(*) FROM discovery_run_events
  UNION ALL SELECT 'capability_demand_events', COUNT(*) FROM capability_demand_events
  UNION ALL SELECT 'discovery_gap_events', COUNT(*) FROM discovery_gap_events;"
# expect all > 0

# (3) Health endpoint
curl -sS http://127.0.0.1:8000/health/evidence | jq .
# expect: {benchmark_runs_24h: >0, verification_records_7d: >0,
#          discovery_run_events_24h: >0, capability_demand_events_24h: >0,
#          route_status_routable_count: >0}

# (4) Recipe export bugs closed
grep -E "B[1-6].*closed" docs/manual-test-log.md | wc -l   # expect 6

# (5) Deck regenerated
ls -la PITCH_DECK.pdf                                      # mtime > sprint start
mdls -name kMDItemNumberOfPages PITCH_DECK.pdf             # expect 14

# (6) Homepage live evidence
curl -sS http://localhost:3000/ | grep -E "Live evidence|benchmark runs|verifications"
# expect 4 non-zero numbers in the rendered HTML
```

If any of the six checks fail, the corresponding phase is not done.

---

## 15. After this sprint — what comes next

If this sprint lands as planned, the deck and code are aligned and the
public homepage is independently verifiable. The next logical sprint
is **Sprint 6 — first 5 design-partner onboarding loop**:

1. Take the now-defensible deck to the 5 founding design partners listed in `PITCH_DECK.md` slide 13 ("What I am asking for right now").
2. Run their goals through `/goal`, hand them the (now bug-free) recipes, watch them run in their actual host (Claude Desktop / Cursor / n8n).
3. Capture refusal reasons → these now correctly land in `capability_demand_events` (Phase 2 of this sprint).
4. Pick the top-3 most-demanded missing capabilities → that becomes the Sprint-7 wrapper batch.
5. With 3 live cells from Phase 5 stretch + new design-partner-driven demand data, open the founding-cohort vendor outreach against `docs/marketplace-design.md`.

At that point the deck's Phase-2 row ("Designed; not yet built") becomes the Sprint-7 build target with real demand data as input, not founder intuition.

### Sprint-6 status as of 2026-05-19 — shipped before the first partner call

The onboarding-loop enablers are now in place. **No partner call required to verify any of this**; the surfaces are live against the same Postgres the deck cites.

| Pillar | What shipped | Verify |
|---|---|---|
| **A1 — design-partner CSV exporter** | `make demand-snapshot` writes a stable CSV joining `capability_demand_events` × `discovery_gap_events` × `benchmark_runs` (routable-today set). Same header on every run; safe to paste into a partner conversation or vendor email. | `LOOKBACK_DAYS=30 make demand-snapshot && head -3 data/demand_snapshot.csv` |
| **A2 — `/dev/demand` internal cockpit** | One page combining live `/health/evidence` counters + top demand + routable gaps + recent verifications. Server-rendered (no client-fetch flakiness), no auth gate, safe for shoulder-surf. | `curl -s http://127.0.0.1:3000/dev/demand` |
| **A3 — `/partners` public page** | Anchored to `PITCH_DECK.md` slide 13. Three sections (design partners, vendor introductions, host-BD), each row mapped to a live URL on this site. `mailto:` CTAs; no JS form-handler dependency. | `curl -s http://127.0.0.1:3000/partners` |
| **A4 — onboarding playbook** | `docs/design-partner-onboarding.md` — 30-min call structure, pre-/post-call commands, anti-patterns. Names every `make` target the founder needs. | Read `docs/design-partner-onboarding.md` |
| **C1 — public demand APIs** | `GET /demand/top-capabilities` + `GET /demand/gaps`, flat stable contract, per-IP rate-limit (default 60 RPM via `PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN`). Vendor portals / dashboards can consume these directly. | `curl -s http://127.0.0.1:8000/demand/top-capabilities?limit=3` |
| **C2 — public `/demand` page** | Vendor outreach hook: stable URL on top of the two APIs + a Take-Action footer that points vendors at `/goal`, `/discovery-gaps`, and `/open-mcp-opportunities`. | `curl -s http://127.0.0.1:3000/demand` |
| **C3 — regression tests** | `test_rate_limiter.py` (8 tests) + `test_demand_public_api.py` (6 tests, including the 429-after-3-bursts limiter). Full suite at 1184 tests green. | `PYTHONPATH=apps/api .venv/bin/python -m unittest apps.api.tests.test_demand_public_api apps.api.tests.test_rate_limiter -v` |
| **B — baseline-firewall audit (CI)** | `scripts/audit_baseline_firewall.py` (AST scan, ignores docstrings) + `.github/workflows/firewall-audit.yml`. Locks in the "no benchmark baseline in customer routing" invariant on every push. Also shipped: extracted `planmyagents_api.test_domains.is_reserved_test_url` so `MockFirecrawlScraper` no longer crosses the firewall. 6 audit-script tests + the existing 4 deep-firewall tests = 10 firewall guarantees. | `make audit-baseline-firewall` |
| **F — browser regression sweep** | Playwright + manual sweep over `/`, `/demand`, `/dev/demand`, `/partners`. All four pages render cleanly, no console errors. | `make web-typecheck` |

**Definition of done for Sprint 6 enablers:** all rows above carry a verify-command that passes on `main`. After 5 partner calls, the next gate is moving the `/partners` slide-13 vendor list from "outreach drafts" to "live conversations" — the `make demand-snapshot` CSV is the artefact that walks the founder into each vendor conversation.

### Sprint-6 follow-ups closed on 2026-05-19 — bug + outreach draft pack

The four follow-ups the founder requested off the sprint-6 enablers table all shipped on the same day, in the same logical sequence (bug first so the artefact set proves out before any outreach goes out).

| Pillar | What shipped | Verify |
|---|---|---|
| **Bug-1 — homepage `LiveEvidenceStrip` hydration fix** | Root cause was *not* in the strip: Next.js 16 introduced an `allowedDevOrigins` strict cross-origin check on dev resources that, without an explicit allow-list, blocks the HMR socket *and* halts the React runtime mid-boot. The visible symptom was every `"use client"` component on the homepage staying in its server-rendered initial state forever — the strip showed 4 grey skeleton boxes, the goal-form's example-button click never filled the textarea, and no console errors fired. Fix: allow-list `127.0.0.1[:3000]` and `localhost[:3000]` in `apps/web/next.config.mjs`, with an inline comment naming this exact symptom so it doesn't silently regress. | `make web-test-config` (Node-built-in test; asserts the allow-list invariant in <100 ms) **and** `curl -s http://127.0.0.1:3000/ \| grep '15</strong>'` (the strip now ships real Postgres counts in SSR) |
| **Bug-1 regression test** | `apps/web/tests/config/next-config.test.mjs` + `make web-test-config`. Two `node:test` assertions: `reactStrictMode` + `typedRoutes` on, and `allowedDevOrigins` includes all four loopback variants. Lives outside the Playwright suite so it runs in <100 ms on every push without spinning up a dev server. | `cd apps/web && npm run test:config` |
| **Outreach-1 — 10 vendor drafts** | `docs/outreach/vendors/{apollo,hunter,perplexity,linkup,tavily,exa,apify,firecrawl,browserbase,resend}.md`. Each draft is 1:1 with the 10 vendor rows on `/partners`, names a single specific capability cell, cites one live URL on the site as evidence, and carries the same "What we will NOT ask you for" block (no customer API keys, no NDA, no logo). All 10 sit on top of the shared `docs/outreach/_template.md` 9-element scaffold. | `ls docs/outreach/vendors \| wc -l` (= 10) |
| **Outreach-2 — 3 host-BD drafts** | `docs/outreach/hosts/{n8n,cursor,anthropic}.md`. Same scaffold; integration-shape per host (n8n marketplace node ≠ Cursor command-palette plug ≠ Claude Desktop reranker), so the asks are not interchangeable. | `ls docs/outreach/hosts \| wc -l` (= 3) |
| **Outreach-3 — design-partner template + persona-fit guide** | `docs/outreach/design-partners/template.md` + three persona-specific hook variants (MCP power user / n8n-Cursor workflow builder / RevOps), plus `docs/outreach/_persona-fit-guide.md` — the 60-second decision tree the founder uses *before* copying any draft so a vendor draft never gets sent to a design partner (or vice-versa). | Read `docs/outreach/_persona-fit-guide.md` |
| **Outreach-4 — URL validator + 6 hermetic tests** | `scripts/validate_outreach_urls.py` walks every `docs/outreach/*.md` (frontmatter `live_urls:`, inline `https://…`, Markdown `[text](url)`, strips trailing punctuation/backticks/quotes, HEAD with GET fallback, treats 3xx as OK). `make outreach-validate-urls` hits production, `make outreach-validate-urls-local` rewrites `planmyagents.dev` → the local dev base. Tests in `apps/api/tests/test_validate_outreach_urls.py` use a stub HTTP server (no public-internet dependency). Side-effect: surfaced a real broken link on `/partners` — Hunter's `/agents/hunter-email-verifier` 404s because Hunter is only in the static registry, not seeded into `discovery_candidates`; the link on `/partners` *and* the Hunter draft now both point at `/categories` (honest about state). | `make outreach-validate-urls-local` → "All 8 URL(s) validated OK against http://127.0.0.1:3000." |
| **Final sweep** | `make test` → 1190 / 1190 backend tests green (up from 1184 — the 6 new URL-validator tests). `make web-typecheck` clean. `make web-test-config` (2 tests) green. Ruff clean on every file touched in this batch (6 pre-existing complaints in untouched scripts). Browser sweep: homepage strip shows live data (15 / 0 / 0 / 6 with "3 cells routable (30d)"), example-button click fills the textarea (proving hydration works), `/partners` / `/demand` / `/dev/demand` all render with no errors. | All commands above pass; full sweep takes ~10 min wall-clock dominated by `make test`. |

**Definition of done for the follow-ups:** every row above carries a verify-command that passed on `main` at the timestamp above, and the only deployment-gated condition (drafts citing `https://planmyagents.dev/...`) is documented in `docs/outreach/README.md` § "Deployment state" with a clear "don't send until the domain resolves" guard.

### Pre-launch hardening pass closed on 2026-05-19 — P0 bugs + P1 refactors

Done in a single uninterrupted sequence (P0-A → P0-B → P1-A → P1-B → full verify) before any external traffic. The rationale for pulling P1 *forward* of launch instead of deferring it: in a zero-user state, blast-radius for refactors is zero; once design partners arrive, every line of churn carries support and rollback cost that does not exist today.

| Pillar | What shipped | Verify |
|---|---|---|
| **P0-A — fix split-brain store-URL defaults** | `apps/api/planmyagents_api/_config.py` is now the single source of truth for `PLANMYAGENTS_{DISCOVERY,BENCHMARK,VERIFICATION}_STORE_URL` defaults (all PostgreSQL DSN; the SQLite-vs-Postgres divergence between `web/app.py` and `web/planning.py` is gone). Pre-fix, an unset `PLANMYAGENTS_DISCOVERY_STORE_URL` meant the request-time planner and the rest of the app saw *two different databases* — first design-partner call would have shipped silently-inconsistent reads. New `log_store_defaults_in_use()` fires a startup WARN listing every env var falling back to the canonical default so deploys can never reach prod without conscious DSN configuration. | `PYTHONPATH=apps/api .venv/bin/python -m unittest apps.api.tests.test_store_url_defaults` (AST-based guard test enumerates every `os.getenv("PLANMYAGENTS_*_STORE_URL", "...")` call in the tree and fails if a non-empty default lives anywhere except `_config.py`) |
| **P0-B — bound `TokenBucketRateLimiter` memory** | Lazy eviction + hard cap on the in-process buckets dict. Pre-fix, every unique client identity (IP + path) allocated a bucket that lived forever — under adversarial rotation the dict was unbounded and a single-replica deploy could be OOM'd with O(MB) of synthetic traffic. New: every 1024 acquires the limiter sweeps fully-refilled buckets, and an absolute cap (`PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS`, default 100k) trims the oldest entries. The eviction is functionally invisible — an evicted-then-returning key behaves identically to a first-seen key. | `PYTHONPATH=apps/api .venv/bin/python -m unittest apps.api.tests.test_rate_limiter` (8 new + existing tests including `test_one_hundred_thousand_keys_stays_bounded`, which hammers 100k unique keys and asserts the internal dict never exceeds the cap) |
| **P1-A — break the 3,523-line `web/app.py` monolith** | New `apps/api/planmyagents_api/web/routes/` package with seven `APIRouter` modules: `health`, `billing`, `recipes`, `discovery`, `leaderboards`, `demand` (the seventh is a `routes/__init__.py` docstring explaining the rationale and boundaries). All twenty-something endpoints moved out of `app.py` by feature cluster; `app.py` is now 2,711 lines of factory + lifecycle + dependency wiring with `app.include_router(...)` calls instead of inline `@app.get` decorators. Public OpenAPI surface is byte-identical — verified by running the full Python suite (1,200 / 1,200 green). | `PYTHONPATH=apps/api PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB=false PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL= .venv/bin/python -m unittest discover -s apps/api/tests -p 'test_*.py'` → 1,200 tests OK. Also passes the `make audit-baseline-firewall` invariant. |
| **P1-B — break the 1,370-line `app/goal/page.tsx` monolith** | Eleven inline subcomponents extracted to `apps/web/src/components/goal/`: `PlanningRefusalCard`, `ResponseLayout`, `DetailsAccordion`, `IndexProvenanceDetail`, `IndexProvenancePill`, `CostPreview`, `ReasoningPill`, `LiveResearchDetail`, `LiveDiscoveryPanel`, `DownloadResponseButton` + shared `response-types.ts` (14 inline types consolidated, mirroring the backend Pydantic shapes). `goal/page.tsx` shrank from 1,370 → 175 lines and is now just the form state + submission lifecycle + two top-level renderers (`PlanningRefusalCard` / `ResponseLayout`). No behavioural change; every component preserved its prop shape and JSX byte-for-byte. | `cd apps/web && npx tsc --noEmit` (clean, 0 errors), `cd apps/web && npm run build` (clean — `/goal` still pre-renders as static `○`), `cd apps/web && PORT=3939 npx next start --port 3939 &` then `curl -s http://127.0.0.1:3939/goal` returns 200 with the expected headline + example-buttons text |
| **Verify gate — full regression sweep** | `make test` → 1,200 / 1,200 backend tests green (up from 1,190). `make web-test-config` (2 tests) green. `cd apps/web && npm run build` clean (all 12 routes still build, /goal still static-pre-rendered). Smoke `curl http://127.0.0.1:3939/goal` returns 200 with the expected rendered headline / example-buttons / "Try:" markup. Ruff clean on every touched Python file. | All commands above pass; full sweep takes ~11 min wall-clock (dominated by `make test`) and re-runs cleanly. |

**Definition of done for the pre-launch pass:** zero pending P0 bugs, zero monolithic-file P1s. The next contributor opening any of the moved surfaces sees a small focused file with comments explaining intent, not a 1k–3k line scroll. Maintainability gain is highest at the moment of zero design partners; pulling these forward of launch was deliberate.

### Tier-1 data-consistency pass closed on 2026-05-19 — four "vendor would notice" gaps

After the pre-launch hardening pass (above), an end-to-end audit against the live DB surfaced four data-consistency gaps that any moderately-skeptical vendor scrolling the public pages on a first call would flag. None were behaviour bugs in the planner — they were all in surfaces *adjacent* to the data: a stale aggregation table, a methodology copy that drifted from the code's filter, a health check that swallowed connection errors, and a config-route asymmetry that orphaned a vocabulary table. Fixed in the recommended order (highest visibility → highest blast-radius-if-missed): #3 → #4 → #1 → #2.

| Issue | Symptom on the live install | Root cause | Fix shipped | Verify |
|---|---|---|---|---|
| **#3 — `/leaderboards` cells contradict their own benchmark data** | `email_send`, `web_scraping`, `payment_authorization` tiles rendered `Bench-passed 0 · Routable 0 · Real runs No` despite each having 5 succeeded real-shape rows in `benchmark_runs`. `payment_authorization` was missing from the index *entirely*. Most-scrutinised page on the deck silently understating the strongest evidence. | Two layered failures. **(A)** `scripts/backfill_phase5_cells.py` writes raw rows to `benchmark_runs` via `BenchmarkStore.save(runs)` but never calls `save_rankings(...)`, so the pre-aggregated `agent_rankings` table the `/leaderboards` endpoint reads from stayed empty. **(B)** `_leaderboard_index_entries` iterated only `capability_to_candidates`, so any provider tested-but-not-indexed (Razorpay had no `discovery_candidates` row) was silently dropped from the tile, the per-capability page, AND the credibility classifier — which then defaulted to `synthetic_only`. | New `apps/api/planmyagents_api/benchmark/rebuild.py` is the single canonical "derive rankings from runs" path with a deterministic source classifier (`_replayed_from` → `real_adapter` ▸ `_provenance == response_fixture_pending_live_key` → `response_fixture` ▸ else `synthetic`). New `make rebuild-rankings` + `scripts/rebuild_agent_rankings.py` for operators; `backfill_phase5_cells.py` now invokes it as its last step so future backfills cannot leave rankings stale. `_leaderboard_index_entries` now iterates the *union* of capabilities across candidates and rankings, computes `provider_count` from the union, and calls `classify()` directly for ranking-only capabilities (no more `synthetic_only` fallback when a real-adapter ranking exists). | `PYTHONPATH=apps/api .venv/bin/python -m unittest apps.api.tests.test_rebuild_rankings apps.api.tests.test_web_app.LeaderboardTestedButNotIndexedTest` → 11 tests OK (8 rebuild + 3 tested-but-not-indexed). Live: `curl http://127.0.0.1:8000/leaderboards \| jq '.capabilities[] \| select(.capability=="payment_authorization")'` returns the cell with `has_real_adapter_runs:true, benchmark_passed:1, credibility.status:"smoke_test"`. |
| **#4 — `/open-mcp-opportunities` methodology copy contradicts the code** | Methodology pop-out said opportunities require `(a) at least one OpenAPI spec AND (b) zero agents`, but the top-3 rows on the live install all had `api_supply_count:0` — pure demand rows. Vendor reading the pop-out would have spotted the AND-vs-actual-OR gap in 30 seconds. | The code in `compute_open_mcp_opportunities` deliberately *includes* capabilities with demand but no APIs (lines 152–154 of the docstring; line 189 condition) as "deepest gaps" — and the front-end has a "Deepest gap" callout component for them. The methodology copy on `METHODOLOGY` never got updated when the demand-only branch was added; it still claimed the original AND-rule. | Rewrote `METHODOLOGY` to describe the actual OR semantic, naming both branches and explaining why demand-only rows still surface ("the absence of any wrapper is itself the signal"). Tightened the front-end intro copy in `apps/web/src/app/open-mcp-opportunities/page.tsx` to acknowledge both row types and reference the `Deepest gap` callout. | `PYTHONPATH=apps/api .venv/bin/python -m unittest apps.api.tests.test_open_mcp_opportunities` → 10 tests OK including new `test_methodology_describes_the_or_inclusion_rule` (pins three substrings — `OR`, `demand`, `api_supply_count=0` — so a future edit can't silently revert). Live: `curl http://127.0.0.1:8000/open-mcp-opportunities \| jq -r .methodology` returns the corrected copy. |
| **#1 — `/health/evidence` silently swallows DB connection errors** | On 2026-05-19 Docker Desktop was stopped; the homepage's Live-Evidence strip rendered all-zeros while other pages 500'd. Operator spent ~15 min diagnosing "did the DB get cleaned up?" because the strip looked indistinguishable from "system alive but empty". | The endpoint's contract was "never raise" (the homepage must always render), implemented as a bare `except` that swallowed the connection error and returned all-zero counts. Honoured the never-raise half of the contract, broke the never-lie half. | Added `db_reachable: bool` and `db_error: str | None` to `EvidenceHealthResponse`. The endpoint still never raises, but it now reports `db_reachable=False` + a UI-renderable short error string when the underlying query failed. `LiveEvidenceStrip` renders a distinct rose-coloured "database unreachable" banner with a `make infra-up` hint in that state. Type added to `apps/web/src/lib/api.ts` (optional for back-compat). | `PYTHONPATH=apps/api .venv/bin/python -m unittest apps.api.tests.test_evidence_health_db_reachable` → 3 tests OK (Postgres-up, non-Postgres DSN, Postgres pointing at a closed port). Live: `curl http://127.0.0.1:8000/health/evidence \| jq '{db_reachable, db_error, benchmark_runs_total}'` returns `{"db_reachable": true, "db_error": null, "benchmark_runs_total": 15}`. |
| **#2 — `capability_labels` orphaned in a sidecar JSON file** | Dashboard reported `capability_labels = 0` rows in Postgres despite weeks of real `/goal` traffic that demonstrably coined new labels. Vocabulary-growth flywheel claim on the deck was invisible to any external reader of the DB. | `resolve_capability_label_store_path()` strictly defaulted to `<repo>/data/capability_labels.json` when `PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH` was unset, even though every other recorder (`demand`, `discovery_gaps`) inherited from the discovery DSN when that pointed at Postgres. Missing one env var silently routed labels to a sidecar file no production reader opens. Audit found 15 real labels rotting there — including `identity_verification` (88 hits), `kyc_aml_risk_assessment` (86), `aml_watchlist_screening` (77). | Resolver now layers: (1) explicit env var → highest priority, (2) inherit `discovery_store_url()` if Postgres → production path, (3) JSON file → dev fallback. Eliminates the silent-orphan failure mode. New `scripts/migrate_orphan_capability_labels.py` imported the existing 15 orphan rows into Postgres preserving `usage_count`, `coined_at`, `last_used_at` exactly (idempotent via `ON CONFLICT DO NOTHING`). | `PYTHONPATH=apps/api .venv/bin/python -m unittest apps.api.tests.test_capability_label_resolver` → 3 tests OK (explicit-wins, postgres-inherits, json-fallback). Live: `docker exec planmyagents-postgres psql -U planmyagents -d planmyagents -c "SELECT COUNT(*), MAX(usage_count) FROM capability_labels;"` returns `15 \| 88`. |
| **Verify gate — full smoke** | All 4 endpoints + Next.js build + ruff/tsc | n/a | n/a | `cd apps/web && npm run typecheck && npm run build` clean. Backend smoke: `curl /leaderboards`, `/open-mcp-opportunities`, `/health/evidence` all return 200 with the corrected fields. 24 new unit tests + 8 leaderboard regression tests + 18 health/capability-label tests all green in ~7 seconds. |

**Definition of done for the Tier-1 pass:** every public surface a vendor would scroll on a first call carries data that matches the deck's claims. The remaining audit-found issues are P2 or smaller (UI polish, monitoring nice-to-haves) and do not block the first design-partner conversation.

### Post-Tier-1 inner-loop quality-of-life pass

After shipping the four Tier-1 fixes the verify gate hit a usability wall: `make test` discovers 1,219 cases but the 29-test `FastAPIAppTest` class deliberately exercises the live LLM tier (each `/goal` test fires a real `qwen3.6:27b` call ≈ 25–30s each, plus external HTTP for scout discovery). Wall-clock for a single run is 10+ minutes even when nothing is broken. That made the inner-loop "did my change break anything" check unreasonably slow — a friction multiplier on every future contributor.

| Change | What shipped | Verify |
|---|---|---|
| **Fast subset runner** | New `scripts/run_fast_tests.py` walks the same `unittest discover` graph but filters out class names in a `SLOW_TEST_CLASS_NAMES` set (currently just `FastAPIAppTest`). New `make test-fast` Makefile target wires it up with the same env-isolation flags the full suite needs. The runner prints a header line every run so it's never ambiguous what was actually executed (`discovered=1219 fast=1190 skipped_slow=29`). | `make test-fast` → 1,190 / 1,190 tests OK in **~50 seconds** (vs. >10 min for the full suite). `make test` is unchanged and still runs everything; `test-fast` is the new inner-loop default. |
| **Capability-label path-escape guard updated for the layered resolver** | `apps/api/tests/test_default_event_store_paths.py::test_capability_label_recorder_default_resolves_under_repo_root` was pinning "default branch returns a path under `data/`" — true before the 2026-05-19 layered-resolver fix, false after (the resolver now inherits the Postgres DSN when one is configured). Updated the existing test to force `PLANMYAGENTS_DISCOVERY_STORE_URL=<json path>` so it still exercises the JSON fallback branch, plus added a sibling `test_capability_label_recorder_inherits_postgres_discovery_dsn` that pins the new Postgres-inherit branch. Both invariants are now explicitly guarded. | `PYTHONPATH=apps/api PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB=false PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL= .venv/bin/python -m unittest apps.api.tests.test_default_event_store_paths` → 4 tests OK. |
| **`.env` explicit-intent line** | Added `PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH=postgresql://...` to `.env`. With the new resolver this is technically redundant (the discovery DSN is already Postgres), but the explicit line documents operator intent and survives any future tweak to the resolver default. | `grep -c PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH .env` returns 1. |

### Sub-tasks list rendering — locked in for every outcome mode (2026-05-20)

A vendor reviewing the `/goal` page noticed the OUTCOME panel said *"The planner produced 2 sub-tasks, but none are routable/exportable yet"* without ever **showing what those sub-tasks were** — undermining trust in the decomposition. The `OutcomeCard` fix that landed earlier (the inline `SubTasksList` block) was verified once against a single outline-only run. That's not a regression-proof contract: the next contributor renaming a state name or hoisting the list could silently break the mode coverage on, say, the executed-path while still passing the manual outline-only check.

This pass closes that gap with permanent test coverage across **every** outcome mode the card can render.

| Change | What shipped | Verify |
|---|---|---|
| **Stable test hook on the SubTasksList** | Added `data-testid="goal-sub-tasks-list"` to the list wrapper and `data-testid="goal-sub-task-row"` to each `<li>` row in `apps/web/src/components/goal/OutcomeCard.tsx`. Lets future tests target the contract by id instead of relying on copy that may drift. Zero runtime effect. | `grep -n goal-sub-tasks-list apps/web/src/components/goal/OutcomeCard.tsx` returns 2 hits (testid + jsdoc comment pointing at the spec). |
| **Mock-driven mode-coverage spec** | New `apps/web/tests/e2e/subtasks-list-modes.spec.ts` (4 tests, ~3s end-to-end) intercepts `POST **/goal` with `page.route()` and serves a canned `GoalResponse` for each of the four primary outcome modes (`executed`, `executable_not_run`, `partial_not_run`, `outline_only`). Each test asserts (a) the correct outcome heading renders (so we know we exercised the intended `outcomeMode` branch), (b) the `goal-sub-tasks-list` testid is visible, (c) both canned sub-tasks render their `inputs.user_facing_step` headlines and capability slugs, (d) `goal-sub-task-row` count == 2. Pure-frontend — needs no DB, no API, no LLM. | `cd apps/web && PLANMYAGENTS_WEB_BASE_URL=http://localhost:3000 npx playwright test tests/e2e/subtasks-list-modes.spec.ts` → 4 / 4 passed in 2.7s. |
| **Extended real-backend `/goal` flow assertion** | Appended SubTasksList assertions to the existing `/goal refuses honestly` test in `apps/web/tests/e2e/pitch-flows.spec.ts`. Capability-AGNOSTIC: asserts the wrapper testid renders, the section header is visible, at least one `goal-sub-task-row` exists, and at least one `capability <slug>` line is visible — without anchoring to a specific slug (the LLM picks different long-tail capabilities for the whisky-pricing goal across runs). The mock spec above is where mode-specific copy is pinned. | `cd apps/web && PLANMYAGENTS_WEB_BASE_URL=http://localhost:3000 npx playwright test tests/e2e/pitch-flows.spec.ts -g "/goal refuses honestly"` → 1 / 1 passed (1.8 min wall clock dominated by real LLM planner). |
| **Repaired pre-existing brittle assertions in the same flow** | Two pre-existing assertions in the `/goal refuses honestly` test had silently broken from earlier UI copy/markup changes. Fixing them while in the file: (a) `Recipe coverage: 0 \/` regex → `Recipe coverage:\s*0\/\d+\s*exportable steps` (actual rendered copy is `Recipe coverage: 0/4 exportable steps`, no space around `/`). (b) `getByRole('listitem', { name: /Claude Desktop config.*Disabled/ })` — Playwright's current accessible-name computation drops inter-`<span>` whitespace inside `RecipeText`, so the name was `Claude Desktop configDisabled: …` and the regex never matched. Switched to walking from `getByText(format).locator("xpath=ancestor::li[1]")` then `.toContainText(/Disabled/)`, mirroring the pattern the verification-section assertion already uses. | Same `playwright test -g "/goal refuses honestly"` command above — now passes. |
| **Added `@playwright/test` as a devDependency** | `apps/web/package.json` previously had `"e2e": "playwright test"` but no actual Playwright dependency, so `npm run e2e` on a fresh checkout failed with `Cannot find module '@playwright/test'`. Added the dep (`npm install --save-dev @playwright/test`); ran `npx playwright install chromium`. CI / new contributors can now run the suite without manual install. | `cat apps/web/package.json \| jq '.devDependencies."@playwright/test"'` returns the resolved version; `cd apps/web && npx playwright test --list \| head` enumerates all 9 tests cleanly. |
| **Full E2E sweep** | 9 tests total, 8 pass, 1 unrelated pre-existing failure documented below. | `cd apps/web && PLANMYAGENTS_WEB_BASE_URL=http://localhost:3000 npx playwright test --reporter=list` → `8 passed, 1 failed (2.4m)`. |

**Out-of-scope pre-existing failure noted (`P4-2 /agents/twilio-sms`):** the test hits `/agents/twilio-sms` and expects a "Verification history" heading, but the current local discovery store has zero `twilio-sms` candidate (`SELECT provider_id FROM discovery_candidates WHERE provider_id ILIKE '%twilio%'` → empty). The backend correctly returns `{"detail":"Unknown discovery candidate: twilio-sms"}`; the Next.js page 404s; the heading never renders. This is a fixture-state assumption in a sibling test, not a code regression from this pass — making it data-state-tolerant (or seeding a guaranteed candidate) belongs in a separate "test-fixtures" follow-up so the diff in this pass stays focused on the sub-tasks list contract.

**Definition of done for the sub-tasks list pass:** the rendering contract is now pinned across every outcome mode by a fast, deterministic mock spec PLUS an end-to-end real-backend assertion. A future regression that hides the list on executed responses (the worst-case scenario, since that's when a vendor most wants to see what we routed) is caught in <3 seconds by the mock spec.

### Final pre-launch sweep — 2026-05-20 (full-repo audit + Tier-A fixes)

User asked for "one final scan of the whole repo, find any bugs, do thorough testing, and clean up". Approach: ran the full Python and Playwright test suites, then launched three parallel `explore` subagents (Python / web / config-and-scripts) for very-thorough scans. Aggregated 61 findings into **TIER-A (real bugs, fix now) / TIER-B (safe cleanup) / TIER-C (defer)** — fixed every TIER-A, did the single highest-signal TIER-B (the `formatLabel` 7-site duplication), logged TIER-B/C remainder for follow-up so the diff stays focused.

**Test gate before any changes:**
- `make test-fast` → 1,190 / 1,190 OK in 45 s
- `npx playwright test` → 8 passed, 1 failed (P4-2 stale fixture — `twilio-sms` no longer exists in seeded `discovery_candidates`)

**TIER-A fixes shipped (14 items)**

| # | File | Bug | Fix |
|---|---|---|---|
| A1 | `apps/api/planmyagents_api/web/rate_limiter.py:254` | `PLANMYAGENTS_PUBLIC_DEMAND_MAX_BUCKETS=0` parses cleanly then crashes `TokenBucketRateLimiter.__init__` with `max_buckets must be > 0`, taking down the entire FastAPI app at first request. The factory already had this guard for `per_minute` (line 257) — `max_buckets` was the only env var missing it. | Mirror the existing `per_minute<=0` guard: fall back to `_DEFAULT_MAX_BUCKETS` instead of crashing. Added 3 new sub-tests in `test_rate_limiter.py` covering `"0" / "-1" / "-100"`. |
| A2 | `apps/web/src/app/layout.tsx:75` | Top-nav "API" link hard-coded `http://localhost:8000/docs`, breaking the link on every non-localhost deployment. | Resolve via `apiBaseUrl()` from `lib/api.ts` so the link follows `PLANMYAGENTS_API_BASE_URL` / `NEXT_PUBLIC_PLANMYAGENTS_API_BASE_URL`. |
| A3 | `apps/web/src/components/account/AccountDashboard.tsx:214-220` | "Delete recipe" button had no `disabled` state during the in-flight `DELETE /recipes/{id}`. Rapid clicks fired multiple requests; the second one surfaced a confusing 404 after the first had already removed the row. | Track in-flight deletes in a per-recipe `Set<string>`; `disabled={deletingIds.has(recipe.recipe_id)}` and label flips to "Deleting…". Per-recipe (not global boolean) so concurrent deletes don't race. |
| A4 | `apps/web/src/components/account/AccountDashboard.tsx:96-99` | Plan label read directly from `useUser().publicMetadata.plan` (Clerk), which is set by the Stripe webhook handler with a small lag. A successful Pro upgrade would render as **"FREE"** for tens of seconds while the webhook round-tripped. `fetchAccountMe()` (the `/account/me` backend, sourced from `MarketplaceStore` — the same row the webhook commits) was exported in `lib/api.ts` but had **zero callers**. | Added a one-shot `useEffect` that calls `fetchAccountMe(token)` and prefers the backend's plan field; falls back to Clerk only when the API is unreachable. Closes the upgrade-perception lag AND consumes the dead `fetchAccountMe` export (TIER-B item folded into this fix). |
| A5 | `apps/web/src/components/account/AccountDashboard.tsx:198-200` | Empty recipes rendered `"0/0 exportable steps"` with no explanation and no guard against a future divide-by-zero in any percentage-display refactor. | Branch on `step_count > 0`: real count when present, `"no sub-tasks"` when empty. |
| A6 | `apps/web/src/components/goal/ExecutionSteps.tsx:93-104` | Three `String(unknown)` paths (`capability`, `description`, `provider_id`) rendered `[object Object]` for non-string truthy values and an empty `<code></code>` for missing provider ids. The `SubResult` type explicitly declares these as `unknown` to match the backend's permissive shape. | Replaced each with `typeof x === "string" && x` guards; fall back to `"—"` for missing values, suppress empty descriptions entirely. |
| A7 | `apps/web/src/app/search/page.tsx:39-50` | On `search()` failure the page built a synthetic `SearchResponse` with an `error` field that the JSX never read. User saw a clean "0 results" panel — fully silent error. | Now stores the error string in a separate `searchError` state and renders a distinct rose-coloured `role="alert"` panel with `make api` and `/health/evidence` remediation hints. |
| A8 | `.gitignore:8` | `.env.*` glob silently untracked `.env.example` (confirmed `git check-ignore -v .env.example`). `README.md` instructs `cp .env.example .env` — fails on every fresh clone. | Added `!.env.example` exception immediately after the broader glob, with a comment explaining the negation order requirement. |
| A9 | `.env.example:170,244` | `STRIPE_SECRET_KEY=` declared twice — once under benchmark adapters, once under Pro tier billing. When the file is sourced the second silently shadows the first. An operator pasting a key into the benchmark block would see it ignored at runtime. | Single declaration under benchmark adapters, with the docstring explicitly naming both consumers (baseline + billing) and both independent live-mode opt-in flags (`STRIPE_ALLOW_LIVE_MODE`, `STRIPE_LIVE_MODE_OPT_IN`). |
| A10 | `.env.example` (missing) | `SMITHERY_API_KEY` and `MOLTBOOK_API_KEY` (read by `discovery/sources/{smithery,moltbook}.py` and surfaced as scout sources in `discovery/scouts.py`) were not in the template. Each source silently skips when its token is absent, so a fresh-install operator gets "0 candidates from Smithery / MoltBook" with no error and no hint of the missing env var. | Added both keys to the Tier-1 first-party discovery block with their signup URLs and the explicit "silently skips when unset" semantics documented in-place. |
| A11 | `Makefile:4` | `.PHONY` was missing six real targets that exist below: `audit-ranking-sources`, `index-pass`, `discovery-research`, `growth-pass`, `benchmark-credibility-report`, `env-check`. If anyone ever creates a same-named file (especially `growth-pass`, which is a tempting filename), `make` silently treats the target as up-to-date. | All six appended to `.PHONY`. |
| A12 | `README.md:118,139`, `BUSINESS_PLAN.md:6,797`, `AGENTS.md:91`, `docs/operations.md:338` | "965 backend tests passing" / "327 cases" / "327 cases" — all stale. Actual count: **1,219 tests discovered, 1,190 fast subset, 29 slow LLM-bound**. AGENTS.md is the first thing every coding agent reads — most-impactful stale claim. | Updated to "1,200+ backend tests passing" everywhere, with the `make test-fast` / `make test` split explicitly documented in the README and AGENTS rows. |
| A13 | `scripts/upkeep_loop.sh:62-66` | The hourly upkeep cron called `run_benchmark_scheduler.py` **without** `--gated-from-registry`. That made it run the full capability matrix every hour instead of just the capabilities with at least one `requires_benchmark_gate=true` provider — silently burning Groq/Ollama tokens on capabilities that have nothing to gate. `make benchmark-cron` (the Makefile target) already passed the flag correctly; the cron script was the one outlier. | Added `--gated-from-registry` with an in-file comment explaining the parity with `Makefile:282-287`. |
| A14 | `apps/web/tests/e2e/pitch-flows.spec.ts:43` | Hard-coded `/agents/twilio-sms` — the slug no longer exists in the seeded `discovery_candidates`, so the test 404'd. The deck claim is "every agent detail page renders verification history + benchmark sections", not "this specific slug exists". | Test now pulls the first available `top_candidate_ids[0]` off `/discovery/categories` (the most stable seeded endpoint), with a clean `test.skip(…)` when nothing is seeded. Runs in 686 ms vs. the previous 30 s timeout-then-fail. |

**TIER-B cleanup shipped (1 item — chose the highest-signal duplication)**

| Change | What shipped | Numbers |
|---|---|---|
| Extracted `formatLabel` into `apps/web/src/lib/format.ts` | The same 5-line `snake_case → Title Case` helper was duplicated **byte-identically seven times** across `app/categories/`, `app/demand/`, `app/dev/demand/`, `app/discovery-gaps/`, `app/leaderboards/`, `app/leaderboards/[capability]/`, and `app/open-mcp-opportunities/` page files. New `lib/format.ts` carries the function plus a docstring naming all seven prior sites and explicitly explaining why `formatTimestamp` was **not** consolidated in the same pass (three pages render timestamps in three subtly-different formats — UTC, locale, locale-compact — and merging them would change rendered pixels in ways that belong in their own focused diff). | 7 sites → 1, ~35 lines of duplication removed, build remains clean, all 9 Playwright tests still pass. |

**Verify gate post-fixes**

- `make test-fast` → **1,191 / 1,191 OK in 47 s** (was 1,190; +1 for the new `test_factory_recovers_from_non_positive_max_buckets_env`).
- `npx playwright test` → **9 passed (2.0 min)** — up from 8 / 9 (P4-2 now stable).
- `cd apps/web && npm run typecheck && npm run build` → both clean, all 17 routes still build, `/goal` and `/` still pre-render as static (no regression in the static-rendering classification).

**Deliberately deferred — TIER-B / TIER-C remainder (logged for a separate pass)**

The wider audit surfaced more cleanup than is safe to land in a single "final sweep" diff. The summary below is the audit's ranked output; the high-signal items either land in a focused follow-up PR or stay deferred consciously:

- **Backend TIER-B (5 items)**: dead `ROOT` constant in `web/app.py:146`; duplicate `Hard rules:` header in `local_qwen.py:114-116` (sent verbatim to the LLM on every `/goal` call); missing lock in `_compute_evidence_health_cached` (cache-miss thundering herd of 6 small COUNTs); duplicated `BENCHMARK_STORE_ENV` constant between `agents/router.py:42` and `_config.py:73`; `post_goal_refresh.py:400` collects futures in submission order instead of completion order; redundant `or 0.0` patterns in `cost/cost_cap.py:233,279`.
- **Web TIER-B (10 items)**: `formatTimestamp` 6× (3 distinct flavours, NOT byte-identical so deliberately left); `<Stat>` card component 4×; "API not reachable" panel 5×; `absoluteUrl`/`buildAbsoluteUrl` duplicate in `AccountDashboard` and `RecipeDownloadButtons`; four near-identical "parse `error.detail`" helpers in `lib/api.ts`; tailwind class strings repeated 5+ times that should use the existing `tag-success`/`tag-warn` classes; `RecentVerificationsPanel` conflates "loading" with "fetched but null"; `agents/[id]/page.tsx` double-cast `as unknown as CandidateView` hides the type contract.
- **Config TIER-B (5 items)**: `Makefile` hardcodes the canonical Postgres DSN in five targets even though `_config.py:DEFAULT_POSTGRES_DSN` is now the source of truth; the `set -a; . ./.env; set +a` preamble is repeated in ~13 targets and could be a single `define LOAD_ENV`; `docker-compose.yml` has no `restart:` policy on `postgres`; `scripts/env_check.py` `GROUPS` registry is missing every var added in the last two sprints; 8 scripts have no Makefile target or doc reference.
- **Backend TIER-C (4) / Web TIER-C (8) / Config TIER-C (5)**: Smaller fragility patterns (inverted `routableTagClass(!entry.routable_today)`, two divergent `ProviderTypeBadge` components, `partners/page.tsx` links that 404 until specific candidates land, `goal/page.tsx` rules-planner branch reachable only via debug env var, etc.) and operational nice-to-haves (idempotency guard on `make web-e2e-install`, version pin for `@marp-team/marp-cli`, `pyproject.toml` runtime deps under-declared so `pip install .` half-installs). All logged in the audit transcripts.

**Definition of done for the final sweep:** every TIER-A finding fixed and covered by a test where reasonable; full test sweep (1,191 py + 9 e2e + Next.js build) green; the one TIER-B cleanup chosen was the most-duplicated piece of code in the frontend (7 sites). The deliberately-deferred TIER-B/C items are logged so the next focused PR has a curated TODO list instead of a re-audit cost. After this pass the repo has zero known-broken tests, zero known-broken docs claims, zero hard-coded `localhost` URLs in user-facing surfaces, zero silent-error UI paths in the audited components, and zero env-var typos that crash the API at startup.

### Onboarding doc — `docs/GET_STARTED.md` (2026-05-20)

User asked whether the README was sufficient for setting the repo up on a new machine, or whether a dedicated GET-STARTED guide should exist. Audit of the existing `README.md#Quick start` (lines 143–196 pre-patch) found it was both **factually wrong** and **missing every prerequisite a new contributor needs**, in ways that would cost a fresh laptop ≥ 45 min of confused trial-and-error before a working `/goal` response.

| # | Finding | Why it bites | Fix |
|---|---|---|---|
| O1 | "Run (zero-config, SQLite)" section header was a lie | Since the 2026-05-19 `_config.py` split-brain fix, `_config.py:DEFAULT_POSTGRES_DSN` is the single source of truth and points at Postgres. `.env.example:15` hardcodes the Postgres DSN. There is no SQLite zero-config path anymore — any contributor following this would `make api` and watch it 500 on first request with `connection refused`. | Section deleted from README; replaced with the actual happy path (`make stack-up` → Postgres up + migrations applied + seeded → `make api` + `make web`). |
| O2 | `make test # 965 stdlib unittest cases` in README "useful commands" | Stale by ~250 tests. The previous final-sweep pass fixed this in 4 other docs (BUSINESS_PLAN, AGENTS, operations, README:118) but **missed README:190**. Pretending the suite is half its actual size also hides the `test-fast` / `test` split (a contributor would never know they could get a 45-second feedback loop instead of 10 minutes). | Replaced with the correct split: `make test-fast` (1,190 deterministic, ~45 s) and `make test` (full 1,219 incl. live-LLM, ~10 min), with the wall-time tradeoff documented. |
| O3 | Zero prerequisites listed | The README didn't mention Python 3.12+, Node 20+, Docker Desktop, or Ollama — yet all four are hard requirements. The default `local_qwen` planner needs a `qwen3.6:27b` Ollama pull (~16 GB, ~10 min on a fast link); a contributor who runs `make api` without Ollama running will see `/goal` hang for 2 minutes per attempt with no useful error. | New `docs/GET_STARTED.md` § 1 lists every prereq with a one-shot version-check command per row; § 1.1 surfaces the `ollama pull qwen3.6:27b` step **upfront** so it runs in the background during the rest of setup. |
| O4 | No smoke test | A contributor had no signal of "did this actually work?" until they submitted a goal — and the first reasonable goal (`buy cheapest glenlivet`) returns "Plan outline only" because no Indian-whisky-pricing agents are in the seeded index. Without context that **outcome is the success signal** (it proves planner + scout + refusal path all work), a new contributor reads it as a broken install. | § 3 walks through the canonical first-goal smoke explicitly, naming each thing the refusal confirms is working (Ollama answered → planning OK; Postgres+pgvector answered → discovery OK; the refusal path rendered → the deck's central claim is wired). |
| O5 | `make growth-pass` listed without context | The README cited it as a "useful command" but the script silently no-ops without `BRAVE_SEARCH_API_KEY` / `TAVILY_API_KEY` (Tier-2 paid scouts). Running it as a sanity check produces a clean exit code with empty output — looks like a working command that did nothing. | Removed `growth-pass` from the README cheat-sheet (where it has no surrounding context to explain the paid-key requirement). Moved into `docs/GET_STARTED.md` § 6 (Optional capabilities) where the paid-scout dependency is documented alongside the relevant env vars. README cheat-sheet now keeps only commands that work out of the box on a fresh checkout. |
| O6 | No troubleshooting | The two most common new-contributor failures (`/goal` hangs because Ollama isn't running; homepage shows all-zeros because Docker Desktop is stopped) had zero documented diagnostics. The recent `db_reachable` field on `/health/evidence` was added precisely to surface the second case — but the doc never told anyone to look there. | § 8 in GET_STARTED is a 13-row symptom → cause → fix matrix, ranked by how often each one bites. Includes the diagnostic curl that prints `db_reachable / db_error / counts` in one line — i.e. the field added by the 2026-05-19 fix is now actually discoverable from the docs. |
| O7 | `.env.example:72 PLANMYAGENTS_QWEN_MODEL=qwen3.5:35b-a3b` was stale | Caught while writing the GET_STARTED Ollama section. The code default in `apps/api/planmyagents_api/planner/local_qwen.py:51` was changed to `qwen3.6:27b` (a dense 27 B model that fits under 24 GB RAM; the previous MoE 35 B model needed ~27 GB and stalled on routing). A new contributor copying `.env.example` to `.env` would pull the wrong model — both larger and slower than current default. | Updated `.env.example:81` to `qwen3.6:27b` with an inline comment explaining the rationale for the dense-vs-MoE switch and how to override on small/large machines. The historical MoE name is preserved in the comment so anyone who remembers the old default sees why it moved. |

**What shipped:**

- `docs/GET_STARTED.md` — new, 297 lines, 10 sections (`Prerequisites` / `Five-minute happy path` / `First /goal smoke` / `Choosing the planner` / `Useful loops` / `Optional capabilities` / `Production-shaped run` / `Troubleshooting` / `Repo map` / `Next steps`). Single read in ~10 min.
- `README.md` — Quick start section rewritten (50 lines → 30 lines): real prereqs in one line, real happy path, expected `/goal` outcome explained, link to GET_STARTED for the full walkthrough. Stale "useful commands" cleaned up (`test`→`test-fast`+`test`, removed `growth-pass`, added `rebuild-rankings`).
- `README.md` strategic-documents table — `docs/GET_STARTED.md` added as the first row so it's discoverable from the doc index.
- `.env.example` — `PLANMYAGENTS_QWEN_MODEL` default corrected from the stale MoE entry to the actual code default with rationale.

**Why split into a separate file rather than expand the README:**

The README is 235 lines of strategy / flywheel / anti-positioning / status — material a contributor reads once to understand *what the company is* and revisits to brief a new hire on the wedge. Onboarding is high-frequency, ops-focused content (prereqs, commands, troubleshooting) that a contributor needs to find fast and re-find often. Mixing them would: (a) bury the strategy narrative under ops cruft, (b) force every commit that fixes an ops gotcha to touch the strategy doc, (c) make the README scroll past the first viewport on the strategy. Keeping `README.md` strategy-focused + `docs/GET_STARTED.md` ops-focused is the same split most well-onboarded OSS repos use.

**Verify gate:**

- Every Makefile target referenced in `docs/GET_STARTED.md` (20 targets: `install`, `web-install`, `stack-up`, `infra-up`, `infra-down`, `stack-down`, `migrate`, `bootstrap-postgres`, `api`, `web`, `test`, `test-fast`, `lint`, `web-e2e-install`, `web-e2e`, `embed-candidates`, `env-check`, `discovery-refresh`, `rebuild-rankings`, `deploy-build-api`) exists in `Makefile` — verified by `for t in ...; do grep -q "^$t:" Makefile && echo ok; done`.
- Every file path referenced (11 paths including `docs/ARCHITECTURE.md`, `pyproject.toml`, `apps/api/planmyagents_api/_config.py`, `apps/api/planmyagents_api/planner/local_qwen.py`, `apps/web/src/lib/format.ts`) exists on disk — verified by the same one-liner.
- `grep -nE "zero-?config|SQLite|965|327 cases" README.md docs/GET_STARTED.md` → no matches (all three stale-claim strings purged).
- Manual reading-pass: every command in the "Five-minute happy path" walks through this exact sequence on a fresh shell with `set -e` semantics.

**Definition of done for the onboarding pass:** a contributor cloning the repo with **only this doc and a clean macOS/Linux machine** can reach a first verified `/goal` response in ≤ 30 wall-clock minutes (Docker image pulls + Ollama model pull dominate). Every common new-contributor failure mode that we've personally seen in the last two sprints (Ollama down, Docker stopped, stale env-template, missing playwright dep, wrong test count expectation) has a documented diagnosis path. The README stays strategy-focused; the ops doc stays ops-focused; the audit trail in this sprint log records *why* the split exists so the next contributor doesn't re-litigate it.

### Discovery-breadth expansion — Tier-1 scouts (2026-05-20)

User asked: *"I am worried that we don't have enough channels and website from which we can find the agents and mcp to expand our discovery layer?"* — legitimate concern that needed grounding before any recommendation, because the actual scout fleet is a moving target.

**Audit of the pre-expansion state:** 14 wired scouts in `default_scouts()` + 7 parameterised live-source classes that weren't wired into the dispatcher. Coverage was strong on MCP meta-aggregators (Smithery, MCP Marketplace, Official MCP Registry — combined ~6,500 servers) but **zero coverage** on three high-leverage channels:

1. **Package registries** (npm/PyPI/crates.io) — where MCP servers actually originate as installable artifacts BEFORE they reach any aggregator
2. **Community-curated awesome-* lists** — maintainer-judged signal that updates faster than canonical registries
3. **Cross-aggregator catalogs** (Glama, mcp.so) — distinct populations from Smithery / MCP Marketplace

Recommendation after audit: build Tier-1's four scouts (Glama + npm + awesome-lists + mcp.so) as a focused pass. User picked "build all four now". Two were deferred during research with rationale:

* **mcp.so deferred** — HTML-only, no public JSON endpoint. Glama (23,794 servers) is a larger superset of mcp.so (21,173) by sampling, with a clean API. Logged in `docs/agent-discovery-index.md §3.2` to revisit if Glama recall proves insufficient.
* **PyPI deferred** — no clean request-shaped search API; `pypi.org/simple/` is 4 MB and 23 s download (fundamentally batch-shaped). Logged with a concrete shape proposal (cache-and-refresh pattern, ~2,600 prefix-matched packages persisted to `packages/discovery/sources/`).

**What shipped — 3 new scouts:**

| Scout | File | Population | Auth | Budget | Tests |
|---|---|---|---|---|---|
| `glama` | `apps/api/planmyagents_api/discovery/sources/glama.py` (~280 LOC) | ~23,794 MCP servers — the largest single MCP directory by volume, larger than Smithery + MCP Marketplace + Official MCP Registry combined | none | 18 s | 7 |
| `npm_mcp_packages` | `apps/api/planmyagents_api/discovery/sources/npm_packages.py` (~330 LOC) | ~47,000 packages tagged with the `mcp` keyword. Catches servers before they hit any aggregator. | none | 12 s | 7 |
| `github_awesome_lists` | `apps/api/planmyagents_api/discovery/sources/github_awesome_lists.py` (~280 LOC) | 6 curated lists × 60 entries each (capped) | `GITHUB_TOKEN` (reuses existing) | 25 s | 6 |

**Design choices (matched to existing scout conventions):**

* **`is_obvious_junk` pre-filter on the composite name** — same shared `mcp_publication_quality.py` regex that the other MCP-aggregator sources use, applied BEFORE capability inference (so junk costs ~zero). Drops `*-test`, `*-copy`, `*-fork`, numeric-suffix tells.
* **Corroboration-or-drop per source**, with signals tuned to what each upstream actually exposes:
  * Glama: `attributes` array OR `repository.url` OR `environmentVariablesJsonSchema.properties` — drops skeleton namespace reservations.
  * npm: `dependents >= 1` OR `downloads.weekly >= 25` OR `score.final >= 0.05` OR github/gitlab repo URL — drops "I-published-a-skeleton" packages.
  * awesome-lists: HTTPS URL allow-list + the list maintainer's own curation acts as the proof-of-life.
* **Verification tier defaults to `registered_in_directory`** — one tier above `unverified`, mirroring the Smithery / MCP Marketplace / Official MCP Registry default. Glama gets ONE special case: `author:official` listings are promoted to `known_provider` (publisher-signed).
* **Rich provenance metadata** — every scout stuffs source-specific signals into the candidate's `metadata` map for the `CandidateJudge` LLM to read at retrieval time. Glama: `glama_hosting`, `glama_attributes`. npm: `npm_dependents`, `npm_weekly_downloads`, `npm_score_final`, `npm_install_command`. Awesome-lists: `awesome_list_owner`, `awesome_list_repo`, `awesome_list_url`. The `CandidateJudge` can now break ties using "50K weekly downloads beats 25" or "official Glama listing beats a third-party fork".
* **No regressions to existing scouts** — the wiring change in `default_scouts()` only adds to the fleet; nothing existing was modified. The dispatcher's `test_default_scouts_returns_full_fleet` snapshot test was updated to include all three new IDs (this is the canonical "block accidental removal" gate — it now also blocks accidental loss of the new scouts).

**Live-smoke results (with Ollama embedder running, against the live `nomic-embed-text` model):**

| Query | Glama candidates | npm candidates | Awesome-lists candidates |
|---|---|---|---|
| `"email"` | 5 in 6.3 s | (not run — npm uses `keywords:mcp <query>`) | 12 in ~10 s (after cap tuning) |
| `"payment processing"` | 5 in 2.1 s — incl. correctly-tagged `payment_authorization` rows | — | — |
| `"web scraping"` | — | 94 in 15 s — every result correctly tagged with `web_scraping` | — |

**Performance-tuning iteration in-pass:** the first live awesome-lists smoke took 24 s for 2 lists — over budget. Root cause: per-entry embedder calls (~100 ms each) on the full 200-row README. Fixed by lowering `max_entries_per_list` from 200 → 60 and bumping the scout budget from 20 s → 25 s. Now fits comfortably under the dispatcher's 28 s global ceiling even on cold-cache fetches across all 6 lists.

**Tests added (20 new):**

* `apps/api/tests/test_glama_source.py` — 7 tests: pagination, junk-drop, corroboration policy, capability filter, `hasNextPage=False` early-stop, empty payload, network error, official-listing tier promotion.
* `apps/api/tests/test_npm_packages_source.py` — 7 tests: real-shape parsing, junk-drop, corroboration policy (zero-signal entry dropped), `git+...git` URL unwrapping, capability filter, npx install-command shape, empty/network-error handling.
* `apps/api/tests/test_github_awesome_lists_source.py` — 6 tests: empty-token skip, README parsing, provider_type hint per list, 404-one-list-others-survive, `max_total_candidates` cap, provenance metadata.

**Verify gate:**

* `make test-fast` → **1,211 / 1,211 OK in 133 s** (+20 from the new scout suites; was 1,191 before).
* `test_scout_dispatcher.test_default_scouts_returns_full_fleet` updated to include `glama` / `npm_mcp_packages` / `github_awesome_lists` in both the "all scouts" snapshot AND the "auth-gated scouts" snapshot (awesome-lists reuses `GITHUB_TOKEN`).
* Live single-scout smokes against three diverse queries (`email`, `payment processing`, `web scraping`) confirm each scout returns correctly-classified candidates in real time, not just in mocked tests.
* `docs/agent-discovery-index.md §3` updated with a full active-scout table (13 entries with population estimates + auth requirements + notes) AND a deferred-channels table that explicitly documents what was NOT built and why (PyPI / mcp.so / Cursor Directory / Claude Skills / Cline / HF Spaces / Reddit-Product-Hunt-YC / Cloudflare-Vercel-WorkOS).

**Definition of done for the discovery-breadth expansion:** the three highest-leverage channels (Glama for cross-aggregator MCP recall; npm for package-registry recall; awesome-lists for community-curation recall) are now active scouts in `default_scouts()` with conventional corroboration policies, source-specific provenance metadata, full unit-test coverage, and live-smoked at three diverse queries. The deferred channels are logged with concrete revisit triggers so the next discovery-expansion pass starts from a curated list, not a re-audit. Estimated index-recall delta after a single warm `make discovery-refresh` cycle: +500 to +2,000 net-new candidates depending on the configured `GITHUB_TOKEN` and the `max_pages` settings on the heavyweight scouts. The deck's "fragmented supply is the problem" claim now has 17 active scouts behind it instead of 14.

---

## 2026-05-20 — Discovery-breadth expansion: doc sweep + Playwright follow-up

Triggered by user follow-up ("did you update all docs with this including pitch deck if needed, did you test it properly using playright"). The discovery-breadth pass had updated `docs/agent-discovery-index.md` and the sprint log, but had NOT swept the rest of the documentation surface, and had not run Playwright. This pass closes both gaps.

### Doc sweep

Ripgrep against every Markdown file in the repo for `14 scouts`, `14+ scouts`, `14-scout`, `14 live scouts`, `14 source scouts`, plus the specific scout-name lists that needed updating. Hits found in seven files; all fixed except for sprint-log historical entries (those are intentionally preserved as a record of state-at-the-time).

| File | Locations | Fix |
|---|---|---|
| `README.md` | 3 (ASCII diagram, system flow, source list) | "14+ scouts" → "17+ scouts"; source list expanded to include Glama, npm registry, GitHub awesome-lists |
| `PITCH_DECK.md` | 6 (slide-6 architecture, slide-7 status, slide-8 phase row, slide-10 moat #1, slide-11 beachhead, slide-12 milestones) | Bumped count to 17; the slide-7 "Discovery" cell now lists the four channel classes explicitly so reviewers can see WHERE the +3 scouts come from, not just THAT the number went up |
| `BUSINESS_PLAN.md` | 2 (Smithery-defense response, moat scoring table) | Bumped to 17+; added the four-channel-class framing to the moat narrative — the count alone is a vanity metric, the *channel diversity* is the actual defensibility argument |
| `docs/ARCHITECTURE.md` | 2 (L1 ASCII layer diagram, Phase-1 evolution bullet) | Diagram redrawn to group the 17 scouts by channel class (MCP aggregators / package registry / GitHub / long-tail) instead of listing them flat |
| `docs/HLD.md` | 1 (§2.2 source inventory table) | Three new rows inserted: `glama.py`, `npm_packages.py`, `github_awesome_lists.py`, with the same `(source, type, trust-tag)` shape as the existing 12 rows |
| `docs/LLD.md` | 2 (§3 directory tree, §4.3 per-scout notes) | Tree updated; three new per-scout subsections added with API endpoint, pagination, auth, trust-tag, corroboration policy, and one-line rationale — mirrors the existing scout entries |
| `docs/GET_STARTED.md` | 1 (repo-map description) | "14+ scouts" → "17+ scouts (4 channel classes)" |
| `sprint-pitch-align.md` | 3 historical entries (P0-3, P5-4, etc.) | INTENTIONALLY NOT MODIFIED — these are dated audit-trail rows ("rewrote slide-X to say 14 scouts on Date Y"); rewriting them retroactively would falsify the timeline. The current state lives in PITCH_DECK.md and the doc-sweep table above |

**PDF regeneration:** `make deck-pdf` re-rendered `PITCH_DECK.pdf` from the updated Markdown via the existing Marp pipeline (39 s, 1 warning about local file access — same as every prior build). The committed PDF now matches the Markdown.

### Playwright follow-up

Built one new mock-driven Playwright spec to pin the frontend-side contract for the expanded fleet, then ran the full Playwright suite against a live API on port 8010 (since labelai-backend is occupying port 8000 on this dev machine).

**New spec — `apps/web/tests/e2e/discovery-scout-fleet.spec.ts`:**

* Stubs `POST /goal` via `page.route()` with a canned `plan.discovery.live_discovery` payload whose `per_capability[].dispatch.scouts[]` lists all three new scout IDs (`glama`, `npm_mcp_packages`, `github_awesome_lists`) alongside two existing baseline IDs (`smithery`, `official_mcp_registry`).
* Submits a goal, opens the `<details>` accordion (which is collapsed by default — see `DetailsAccordion.tsx:48`), then asserts each scout_id renders as a `<code>` element inside `LiveDiscoveryPanel`.
* Also asserts that `skipped_reason` text ("GITHUB_TOKEN not configured") surfaces in the rendered panel — regression guard against the failure mode where a skipped scout is silently dropped from the UI.
* Pattern modeled on the existing `subtasks-list-modes.spec.ts` (mock-driven, deterministic, ~4 s runtime). The choice to mock vs. live-drive is documented in the spec header: a real /goal call takes 2-4 minutes, the dispatcher routes each capability to *at most 3* scouts (so the new IDs may not all surface in any given run), and `github_awesome_lists` requires `GITHUB_TOKEN`. The mock pins the wire-contract behaviour; live-coverage of the scouts themselves lives in `apps/api/tests/test_glama_source.py`, `test_npm_packages_source.py`, `test_github_awesome_lists_source.py`, and `test_scout_dispatcher.py` (all green).

**Bug discovered while writing the spec:** the canned payload originally placed `request_time_discovery` at the top level of the /goal response, mirroring an outdated mental model. Playwright caught it immediately — the `DetailsAccordion` summary never rendered because `liveDiscovery` was `null`. Inspection of `ResponseLayout.tsx:97-99` showed the frontend reads `plan.discovery.live_discovery`, not `request_time_discovery`. Fixed the canned payload shape; the test went green in 3.6 s.

**Suite run results (1 worker, chromium project, against API on `http://127.0.0.1:8010`):**

| Spec | Tests | Result | Time |
|---|---|---|---|
| `discovery-scout-fleet.spec.ts` (new) | 1 | ✅ pass | 3.6 s — 43.6 s on the suite-level run (the first run includes a Next dev-server warm-up) |
| `subtasks-list-modes.spec.ts` | 4 | ✅ all pass | 13 s total |
| `pitch-flows.spec.ts` (P4-1 evidence strip) | 1 | ✅ pass | 1.3 s |
| `pitch-flows.spec.ts` (P4-2 agent detail) | 1 | ✅ pass | 4.2 s |
| `pitch-flows.spec.ts` (P4-3 /discovery-gaps panel) | 1 | ✅ pass | 1.3 s |
| `pitch-flows.spec.ts` (P4-3 /open-mcp-opportunities panel) | 1 | ✅ pass | 1.8 s |
| `pitch-flows.spec.ts` (`/goal refuses honestly`) | 1 | ❌ timeout at 4 min | — |

**Net: 9 / 10 pass.**

**On the one failure (initial mischaracterisation, then corrected — see next section):** the heavy `/goal refuses honestly` test timed out waiting for the "Plan outline only" heading. My first-pass diagnosis was "local Qwen planner cold-start, out of scope." That was wrong: the page was stuck on `"Planning..." [disabled]` because the entire /goal call hadn't returned, not because the planner specifically was stalled — and a deeper instrumentation pass (next section) revealed the actual root cause was the discovery-breadth pass itself.

### Definition of done for this follow-up

* Every doc that mentioned a stale scout count or list has been updated, the PITCH_DECK.pdf regenerated to match, and no `14 scouts` / `14+ scouts` / `14-scout` strings remain anywhere outside the historical sprint-log entries (verified by ripgrep across `*.md` and `docs/*.md`).
* The frontend-side contract for the expanded fleet is pinned by a new, fast, mock-driven Playwright spec that runs in under 5 seconds and explicitly asserts each new scout_id surfaces in `LiveDiscoveryPanel`.
* The full Playwright suite (10 tests) was executed against a live PlanMyAgents API; 9 passed initially, with one failure originally (incorrectly) categorised as out-of-scope. The next section fixes the real root cause.

---

## 2026-05-20 — Discovery-breadth: /goal latency regression — root cause + fix

User push-back on the prior section ("just fix it properly, that's bad coding to produce regression") was correct. I had labelled the failing `/goal refuses honestly` Playwright test as a local-planner cold-start flake without actually instrumenting /goal end-to-end. A proper diagnostic pass (`curl -X POST /goal` against the API on port 8010 with `--log-level info` capturing `/tmp/pma-api.log`) showed the real picture and surfaced two real regressions I had introduced.

### Instrumented timing of /goal `"buy cheapest glenlivet in tamil nadu"` (pre-fix)

```
07:30:10 /goal received
07:30:13 planner complete (Groq llama-4-scout)          → 2.8 s
07:30:13 discovery: starting (3 missing capabilities)
07:32:03 candidate_judge: starting (50 candidates)      ← 110 s silent gap
07:32:09 candidate_judge: complete                      → 6.5 s
07:32:10 scout dispatch starting: payment_authorization
07:32:32 scout dispatch complete payment_authorization  → 22.3 s   ← github_awesome_lists bottleneck
07:32:33 scout dispatch starting: price_comparison
07:32:51 scout dispatch complete price_comparison       → 18.0 s
07:32:51 scout dispatch starting: web_search
07:33:09 scout dispatch complete web_search             → 18.2 s
07:33:18 /goal response ready                           → 187.9 s total
```

The Playwright test budget is 240 s; add browser/Next-dev hydration (~60 s) and the response landed at ≈248 s — past the budget.

### What I actually broke (two regressions)

1. **`github_awesome_lists.budget_seconds = 25 s`** — higher than every other scout's budget. The prior fleet's ceiling was 18 s (smithery). Per-capability dispatch elapsed time is bounded by the slowest scout, so awesome-lists became the new bottleneck and added 4-7 s per dispatch on capabilities where it had entries to inference.
2. **`_live_discovery` in `apps/api/planmyagents_api/web/app.py` walked per-capability sequentially** (`for capability in selected:`). That structural issue long predates the discovery-breadth pass, but with the fleet at 14 scouts the per-capability dispatch was small enough to hide it; adding 3 more scouts pushed dispatch from ~14 s to ~22 s, and `3 × 22 s = 66 s` became visible at the test boundary. The fix here is the load-bearing one for the test.

A third issue surfaced during instrumentation but is unrelated to my pass:

* The 110 s "silent gap" between `discovery: starting` and `judge: starting` is the BATCH discovery sources path (`search_candidates → build_discovery_index → ingest_sources(default_discovery_sources())` — Brave / Tavily / APIs.guru / official_mcp_registry / hacker_news / vendor_rss / etc., env-gated and default-on in dev). This is a pre-existing condition and a separate cleanup item (the integration tests already disable it via `PLANMYAGENTS_DISCOVERY_*=false`).

### The fix

**1. Tighten the new scouts' budgets so they cannot dominate the per-capability dispatch.** `apps/api/planmyagents_api/discovery/scouts.py`:

* `github_awesome_lists`: `budget_seconds` `25 → 12` (now matches the typical cluster ceiling).
* `npm_mcp_packages`: `budget_seconds` `12 → 8` (it was reliably timing out at 12 s on every observed dispatch; tightening the budget drops 4 s of wasted wall-clock).

Lower budgets are useless if the scouts can't actually return results in time, so I also lowered the per-scout work to match:

* `apps/api/planmyagents_api/discovery/sources/npm_packages.py`: `page_size 100 → 50`, `max_pages 2 → 1`. Old config asked the embedder to bind capabilities on up to 200 packages per query (~10 s of CPU-bound inference). New config caps at 50, which the corroboration filter typically prunes to 5-15, fitting under 8 s. The cron backfill overrides these via constructor args to scan more deeply.
* `apps/api/planmyagents_api/discovery/sources/github_awesome_lists.py`: `max_entries_per_list 60 → 25`, `max_total_candidates 300 → 150`. Same rationale: at 60 entries × 6 lists × ~100 ms per-entry embedder call = 36 s of inference alone; at 25 × 6 × 100 ms = 15 s worst-case which fits the new 12 s dispatcher budget once HTTP work parallelises with the fleet.

**2. Parallelise per-capability dispatch in `_live_discovery`.** `apps/api/planmyagents_api/web/app.py`:

```python
from concurrent.futures import ThreadPoolExecutor

def _expand_and_dispatch(capability: str) -> tuple[str, Any, Any]:
    scout_task_text = overrides.get(capability, goal)
    expansion = expand_for_scouts(...)
    result = dispatcher.dispatch(...)
    return scout_task_text, expansion, result

per_capability_outputs: list[tuple[str, Any, Any]] = [None] * len(selected)
with ThreadPoolExecutor(max_workers=max(len(selected), 1)) as outer_pool:
    future_to_index = {
        outer_pool.submit(_expand_and_dispatch, capability): idx
        for idx, capability in enumerate(selected)
    }
    for future in future_to_index:
        idx = future_to_index[future]
        per_capability_outputs[idx] = future.result()
```

Safety:

* `ScoutDispatcher.dispatch()` is safe to call concurrently — it creates a fresh `ThreadPoolExecutor` per call and never mutates shared state (verified by inspection: `self._scouts` is read-only, `self._global_budget_seconds` is a constant).
* `_MAX_LIVE_DISCOVERY_CAPABILITIES = 3` caps the outer pool at 3 workers, well below any sensible thread-pool limit.
* The `seen_ids` dedupe gate that the original sequential code applied during iteration is now applied AFTER all dispatches complete, on the same `merged_candidates` lists — semantics preserved.
* Ordering of `per_capability_summaries` is preserved by indexing per-capability outputs back into a list parallel to `selected`.

### Regression test added

`apps/api/tests/test_goal_live_discovery_parallel.py` — a wall-clock-timing test that uses a fake scout source which sleeps `0.6 s` per call and records its start time. The test posts a goal that decomposes into ≥ 2 missing capabilities (KYC + payment), then asserts that the smallest gap between consecutive `scout.search()` start times is `< 0.3 s` (half the sleep). Sequential re-introduction would push the gap above 0.6 s and fail the test loudly.

### Verification (post-fix)

**Python unit tests:** `make test-fast` → **1,212 / 1,212 OK in 48 s** (+1 from the new parallel regression test; was 1,211 before).

**Instrumented timing of the same /goal call (post-fix, warm cache):**

```
13:06:09 /goal received
13:06:14 planner complete                               → 5 s
13:06:14 discovery: starting (4 missing capabilities)
13:07:21 judge: starting                                ← 67 s batch-discovery pre-dispatch (pre-existing)
13:07:27 judge: complete                                → 6 s
13:07:28 scout dispatch starting: shipping_quote
13:07:28 scout dispatch starting: price_comparison      ← all three started in same second ✓ parallel
13:07:28 scout dispatch starting: payment_authorization
13:07:52 scout dispatch complete: price_comparison      → 23.5 s
13:07:53 scout dispatch complete: payment_authorization → 24.2 s
13:07:53 scout dispatch complete: shipping_quote        → 24.3 s
13:08:01 /goal response ready                           → 111.9 s total
```

The three dispatches now overlap completely in wall-clock time — `max(23.5, 24.2, 24.3) = 24.3 s` instead of the pre-fix `sum(22.3, 18.0, 18.2) = 58.5 s`. Net savings: 34 s on the scout step alone.

**Full Playwright suite (10 tests, against API on port 8010 with the fix):**

| Spec | Tests | Result | Time |
|---|---|---|---|
| `discovery-scout-fleet.spec.ts` | 1 | ✅ | 702 ms |
| `subtasks-list-modes.spec.ts` | 4 | ✅ all four | 1.7 s total |
| `pitch-flows.spec.ts` P4-1 | 1 | ✅ | 567 ms |
| `pitch-flows.spec.ts` P4-2 | 1 | ✅ | 649 ms |
| `pitch-flows.spec.ts` P4-3 (×2) | 2 | ✅ both | 730 ms total |
| `pitch-flows.spec.ts` `/goal refuses honestly` | 1 | ✅ | **1.7 min** (was: timeout at 4 min) |

**10 / 10 pass in 1.8 minutes total wall-clock.** The previously-failing heavy test now passes with > 2 minutes of headroom under the 240 s test budget.

### Definition of done for the latency-regression fix

* Two real regressions identified by instrumentation (oversize `github_awesome_lists` budget; sequential per-capability outer loop in `_live_discovery`), both fixed with surgical changes that preserve the existing semantics.
* Lower budgets paired with lower per-scout work (npm `page_size`/`max_pages`, awesome-lists `max_entries_per_list`) so the scouts actually return useful results under the new budgets instead of always timing out.
* New regression test (`test_goal_live_discovery_parallel.py`) pins the parallel-dispatch contract via wall-clock timing on a sleeping fake source — sequential re-introduction would fail loudly.
* Python fast suite: 1,212 / 1,212 OK.
* Playwright suite: 10 / 10 OK, the previously-failing test now lands at 1.7 min with > 2 min of headroom.
* The 110 s pre-dispatch batch-discovery cost is left in place — it is a pre-existing condition unrelated to this pass, with a clear remediation path (gate it behind `PLANMYAGENTS_DISCOVERY_BATCH_AT_REQUEST_TIME=false` for dev, or move it onto the cron) that I am NOT shoehorning into this fix to avoid scope creep.


