# PlanMyAgents — UI Plan & Revamp Documentation (2026-06-04)

> Author: UI revamp pass. Purpose: document the web surface holistically —
> what each page is, what backend it reads, whether it reflects the honest
> current state of its component, and what the forward plan is. This is the
> companion to `docs/honest-scope-audit.md` (which audits the *engine*); this
> doc audits and plans the *surfaces*.
>
> Governing rule (from `AGENTS.md` Hard Rule #1 and the Index-vs-Surfaces
> framing): **no surface may claim more than the underlying index data
> warrants.** Every page below is graded against that bar, not against a
> marketing aspiration.

---

## TL;DR

The web app is a Next.js 16 App Router + Tailwind surface over the FastAPI
index. The engineering is honest and mature: status pills separate
verification / benchmark / routability, the leaderboard shows a credibility
banner, and refusals are first-class. The revamp this sprint closed the
single biggest UI gap:

> **The eval framework — the asset the business plan calls the moat — had
> zero UI.** It lived entirely in CLI/cron jobs and only showed up indirectly
> as leaderboard numbers.

Two new surfaces fix that: **`/trust`** (the live evaluation methodology +
credibility histogram) and **`/submit-agent`** (the honest card-ingestion
explainer). Both are read-only projections of code that already governs
behaviour, so they cannot overclaim.

---

## 1. Stack & how to run it

| Concern | Value |
|---|---|
| Framework | Next.js 16 (App Router, typed routes) |
| Styling | Tailwind 3.4 with a custom `ink`/`accent` palette |
| Bundler | **webpack** (`next dev` / `next build`) |
| API client | `apps/web/src/lib/api.ts` (typed `fetch`, `cache: "no-store"`) |
| Auth | Clerk (optional; `ClerkOptionalProvider` degrades gracefully) |
| e2e | Playwright (`apps/web/tests/e2e/`) |

> **Turbopack note.** Next 16 defaults `dev`/`build` to Turbopack, but
> Turbopack requires native bindings that are blocked by system policy on the
> current build host (`library load disallowed by system policy`). The
> `package.json` scripts were moved to the supported webpack path
> (`next dev` / `next build`, no `--turbopack`). If a future host supports the
> native binding, re-adding `--turbopack` is a one-line revert.

Local run:

```bash
# 1. infra
make infra-up                       # Postgres pgvector container
scripts/db/bootstrap_postgres.sh    # seed candidates + runs + verifications

# 2. API (reads .env for store URLs)
set -a; . ./.env; set +a
PYTHONPATH=apps/api .venv/bin/uvicorn planmyagents_api.web.app:app --port 8000

# 3. web (points at the API)
cd apps/web && PLANMYAGENTS_API_BASE_URL=http://127.0.0.1:8000 \
  NEXT_PUBLIC_PLANMYAGENTS_API_BASE_URL=http://127.0.0.1:8000 \
  npm run dev

# 4. e2e (against the running web + api)
cd apps/web && npm run e2e:install     # one-time chromium download
PLANMYAGENTS_WEB_BASE_URL=http://localhost:3000 npm run e2e
```

---

## 2. Design system

Centralised so the colour story is identical on every page. **Do not
hard-code colour classes for status concepts** — use the helpers.

### 2.1 Palette (`tailwind.config.ts`)

| Token | Use |
|---|---|
| `ink-50 … ink-900` | Neutral text/surfaces (50 = page bg, 900 = primary ink) |
| `accent-50 … accent-600` | Links, focus rings, primary accent |
| `success / warn / danger -500` | Semantic single-tone accents |

### 2.2 Component classes (`globals.css`)

| Class | Meaning |
|---|---|
| `.surface` | White rounded card with border + subtle shadow |
| `.surface-muted` | Same shape, `ink-50` fill (for stat tiles / nested blocks) |
| `.tag` + `.tag-{success,warn,danger,neutral,info}` | Status pills |
| `.step-chip` + `-active/-done/-blocked` | Discovery progress strip |

### 2.3 Status-pill helpers (`src/lib/tags.ts`) — the honesty layer

| Helper | Maps |
|---|---|
| `verificationStatusLabel/TagClass/Tooltip` | `capability_verified → "Tested by PlanMyAgents"`, `known_provider`, `community_listed`, `unverified_example`, raw lead |
| `benchmarkStatusLabel/TagClass` | `passed / failed / not tested` |
| `routableTagLabel/TagClass` | `Runnable today` vs `Cannot run yet` |
| `credibilityLabel/TagClass` | `publishable / developing / smoke_test / synthetic_only` |
| `protocolMaturityLabel/TagClass` | **(new)** `executable → "Executable"`, `refusal_only → "Verification-only"`, `planned → "Planned"` |

The colour rule that ties them together: **green = we can prove it, amber =
we can only see it/claim it, red = we tested only our own code (synthetic),
neutral = roadmap/unknown.**

### 2.4 Display helpers (`src/lib/format.ts`)

`formatLabel(snake_case) → "Title Case"`. Per-page timestamp formatting is
intentionally *not* consolidated (three pages render subtly different
formats; merging would change pixels in an unrelated diff).

---

## 3. Information architecture

```
PUBLIC SURFACES (read from the index)
├─ /                       Goal planner (home; re-exports /goal)
├─ /trust              ★   How we evaluate agents (eval methodology, LIVE)
├─ /submit-agent       ★   Add any published agent card (ingestion explainer)
├─ /categories             Capability clusters
│   └─ /categories/[cluster]
├─ /leaderboards           Per-capability ranked tables (credibility-gated)
│   └─ /leaderboards/[capability]
├─ /agents/[id]            Agent detail (verification + benchmark history)
├─ /search                 Embedding + keyword search
├─ /open-mcp-opportunities Capabilities with API supply but no agent
├─ /discovery-gaps         Capabilities users asked for, zero routable
├─ /demand                 Public demand-signal leaderboard
└─ /partners               Partner / vendor narrative

ACCOUNT / BILLING (Clerk-gated)
├─ /account                Saved recipes + plan
├─ /sign-in, /sign-up

DEV / INTERNAL
└─ /dev/demand             Internal demand inspector

★ = added in the 2026-06-04 revamp.
```

Navigation (`layout.tsx`): primary **Goal** button + a **Browse** group
(Categories · Leaderboards · Trust · Search · Open MCP opportunities ·
Discovery gaps · Submit agent) + API-docs link + auth nav.

---

## 4. Per-page status (honest grading)

Legend: **Solid** = reflects component reality well; **OK** = works, minor
polish possible; **Gap** = missing or misleading.

| Route | Backend | State | Notes |
|---|---|---|---|
| `/` `/goal` | `POST /goal`, `POST /goal/explain` | **Solid** | Plans, routes when routable, refuses honestly. Rich refusal card + recipe-coverage. Largest component set (`components/goal/*`). |
| `/trust` ★ | `GET /eval/methodology` | **Solid** | Live tier ladder, protocol maturity, source taxonomy, **live** credibility histogram. Shows red synthetic-only banner when warranted. |
| `/submit-agent` ★ | none (static) | **Solid** | Documents the real `make card-ingest` CLI + claim-level trust ceiling. No fake form. |
| `/categories` | `GET /discovery/categories` | **OK** | Honest counts per cluster. Could surface protocol mix more. |
| `/categories/[cluster]` | `GET /discovery/categories/{id}` | **OK** | Candidate cards with status pills. |
| `/leaderboards` | `GET /leaderboards` + `/discovery-gaps` | **Solid** | Credibility pill per cell; "world hasn't built this" gap tile. |
| `/leaderboards/[capability]` | `GET /leaderboards/{cap}` | **Solid** | Full credibility banner with reasons + unblockers when not publishable. Untested providers shown, never hidden. |
| `/agents/[id]` | `GET /discovery/agents/{id}` | **OK → Gap** | Strong verification/benchmark/tools sections. **Gap: protocol-invocation maturity not shown here yet** (it is on `/trust`). Candidate for a follow-up pill. |
| `/search` | `GET /discovery/search` | **OK** | Embedding + keyword; surfaces backend + embedder honestly; explicit error panel. |
| `/open-mcp-opportunities` | `GET /open-mcp-opportunities` | **Solid** | "Deepest gap" callout for demand-without-spec. Methodology block. |
| `/discovery-gaps` | `GET /discovery-gaps` | **Solid** | Zero-yield-first ordering; methodology block; linked from refusal flow. |
| `/demand` | `GET /demand/top-capabilities`, `/demand/gaps` | **OK** | Public demand leaderboard. |
| `/partners` | static | **OK** | Narrative page. |
| `/account` | `GET /account/me`, `/recipes` | **OK** | Clerk-gated saved recipes. |
| `/dev/demand` | demand APIs | **OK** | Internal-only inspector. |

---

## 5. The 2026-06-04 revamp (what shipped)

### 5.1 Backend — `GET /eval/methodology` (read-only)

`apps/api/planmyagents_api/web/routes/eval.py`. A **pure projection** of code
that already governs behaviour — no new store, no new persisted state:

- **Tier ladder** from `eval.models.EvalTier` (+ `_TIER_ORDER`): static
  verification → functional smoke → scored benchmark → continuous re-eval,
  each mapped to a credibility band.
- **Protocol maturity** from `eval.protocols.default_registry()`: MCP +
  OpenAPI `executable`; A2A + free-form `ai_agent` `refusal_only`; ACP + ANP
  `planned`. `can_invoke` is derived strictly from `maturity == executable`.
- **Source taxonomy** from `eval.models.REAL_EVAL_SOURCES` /
  `NON_REAL_EVAL_SOURCES`: only `exact_match` / `judge` are real.
- **Live credibility histogram** computed via the same
  `_leaderboard_index_entries` builder `/leaderboards` uses, so the page can
  never disagree with the leaderboard about how many cells are publishable.

Pydantic models in `web/models.py` (`EvalTierModel`, `EvalProtocolModel`,
`EvalSourceModel`, `CredibilityDistributionEntry`, `EvalMethodologyResponse`).
Router mounted in `web/app.py`.

### 5.2 Frontend — `/trust`

`apps/web/src/app/trust/page.tsx`. Sections:

1. **Live index-state banner** — green/amber/red depending on the real
   histogram. When every cell is `synthetic_only` (today: 64/64, 0 real-run
   capabilities) it renders red and says so explicitly.
2. **The evaluation ladder** — the four rungs with their credibility bands.
3. **What we can actually invoke** — per-protocol maturity cards.
4. **Which results count as real** — the source taxonomy table.
5. **Scoring methods & standards alignment** — exact-match,
   four-step tool-use decomposition, rubric-judge; pointer to the spec.

### 5.3 Frontend — `/submit-agent`

`apps/web/src/app/submit-agent/page.tsx`. Documents the real four-step
ingestion pipeline (resolve & parse → extract claims → verify existence not
quality → block quality on invocation) and the trust ceiling. Uses the honest
CLI command rather than a self-serve form that would POST nowhere (a public
ingest endpoint is tracked in the `agent-card-ingestion` spec).

### 5.4 Supporting changes

- `src/lib/api.ts` — `fetchEvalMethodology()` + types.
- `src/lib/tags.ts` — `protocolMaturityLabel/TagClass`.
- `src/app/layout.tsx` — nav links for Trust + Submit agent.
- `src/app/goal/page.tsx` — homepage thesis links to `/trust`.
- `package.json` — webpack dev/build scripts.

### 5.5 Tests

- `apps/api/tests/web/test_eval_methodology.py` (4 tests): tier ladder order,
  real-source flagging, non-executable protocols never `can_invoke`, no
  publishable cell on a synthetic-only store.
- `apps/web/tests/e2e/trust-surface.spec.ts` (3 tests): `/trust` live render,
  `/submit-agent` no-fake-form, nav links present.
- Fixed a pre-existing stale-regex bug in `pitch-flows.spec.ts` (P4-2 expected
  the raw enum instead of the rendered "Tested by PlanMyAgents" label).
- Fast suite 1315 → 1319; web typecheck + build green.

---

## 6. Forward UI roadmap

Prioritised. Each item names *which surface* it deepens and why (per the
Index-vs-Surfaces discipline). None may overclaim.

### Phase A — close honesty gaps on existing pages (small, high-value)

1. **Protocol-maturity pill on `/agents/[id]`.** The agent detail header
   shows verification/benchmark/routable but not whether its protocol is
   invocable today. Reuse `protocolMaturityTagClass`. *Risk: low — the
   mapping must come from the backend (`protocol_for_candidate`) to avoid
   TS-vs-Python drift; expose it on the agent payload rather than re-deriving
   in the client.*
2. **Link `/leaderboards` credibility pills to `/trust`.** A buyer who sees
   "Synthetic only" should be one click from understanding why.
3. **`/trust` deep-link from the goal refusal card.** When a goal refuses for
   lack of a benchmarked agent, link to `/trust` to explain the bar.

### Phase B — make the eval ladder navigable (medium)

4. **Per-capability trust drill-down.** Today `/trust` shows the *aggregate*
   histogram. Add `/trust/[capability]` reading `/leaderboards/{cap}`'s
   credibility verdict so the ladder is browsable per cell. (The future
   `/trust/{cap}` API in the Index-vs-Surfaces doc is the backend for this.)
5. **Provenance viewer on a benchmark run.** `EvalProvenance` is rich
   (tier, run_mode, protocol, scoring_method, judge_model_id, case/ground-truth
   versions). Surface it read-only on `/agents/[id]` benchmark rows so a DD
   reviewer can audit a number's lineage.

### Phase C — the real-cell unlock reflected in UI (depends on engine work)

6. **When the first real cell lands** (a discovered MCP server scored
   end-to-end), the `/trust` banner flips from red → amber automatically (it's
   live). Add a small "what changed" delta so the milestone is visible.
7. **Self-serve `POST /agents/ingest`** → turn `/submit-agent` from explainer
   into a real form. Gated behind the spec; until then the explainer stays.

### Phase D — polish

8. FAQ page (the remaining piece of the Phase-7 "methodology, FAQ,
   trust-narrative" trio).
9. Mobile nav (the Browse group overflows on narrow viewports).
10. Loading/skeleton states for server-component pages (currently rely on
    Next streaming; explicit skeletons would smooth slow API calls).

---

## 7. Conventions for future UI work

- **Typed routes:** after adding a route, run `npm run build` to regenerate
  `.next/types/` or `tsc --noEmit` will fail on the new `href`.
- **Status concepts go through `tags.ts`.** Never hard-code colour classes for
  verification/benchmark/routable/credibility/protocol-maturity.
- **Pydantic models live only in `web/models.py`.** No parallel response
  shapes elsewhere.
- **New read surfaces should be projections.** Prefer reusing the existing
  store loaders + classifiers over computing fresh numbers in the route, so
  two surfaces can never disagree.
- **Honesty review before polish.** If a page can render a state that claims
  more than the index warrants, that is a bug, not a polish item.
- **Verify with the real stack.** Typecheck + `next build` + Playwright
  against a live API, not mocks, before calling a UI change done.

---

## 8. Where this is tracked

- Milestone record: `sprint.md` (2026-06-04 note + Phase 7 update).
- Engine reality this UI must not exceed: `docs/honest-scope-audit.md`.
- Eval spec the `/trust` page projects: `.kiro/specs/agent-eval-framework/`.
- Card-ingestion spec `/submit-agent` documents:
  `.kiro/specs/agent-card-ingestion/`.
