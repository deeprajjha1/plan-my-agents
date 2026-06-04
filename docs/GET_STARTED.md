# Get started

> **Audience:** a new contributor cloning this repo on a fresh laptop.
> **Goal of this doc:** clone → first verified `/goal` response in roughly 30 minutes (≈ 2 min of typing + 25 min of model + Docker downloads).
> **If you only have 5 minutes:** read [§ 2 — Five-minute happy path](#2--five-minute-happy-path) and bookmark [§ 8 — Troubleshooting](#8--troubleshooting).

The README is the strategy document. This file is the ops document. They are deliberately separate so a contributor doesn't have to scroll past the product narrative every time they pull the repo.

---

## 1 — Prerequisites

PlanMyAgents runs as **two processes** (FastAPI on `:8000`, Next.js on `:3000`) backed by **three external services** (Postgres + pgvector, Ollama, and optionally Groq). Confirm each tool is present before you start, in this order — every line below is a one-shot check you can paste.

| Tool | Minimum version | Check command | Notes |
|---|---|---|---|
| Python | `3.12` | `python3 --version` | Pinned in `pyproject.toml:5` (`requires-python = ">=3.12"`). Earlier versions will fail `make install` with a pip-resolver error. |
| Node | `20` (LTS) | `node --version` | Next.js 16 requires Node ≥ 20. `nvm install --lts && nvm use --lts` if you're on something older. |
| Docker Desktop | any recent | `docker info >/dev/null && echo OK` | Required for the Postgres + pgvector container. **If `docker info` exits non-zero, start Docker Desktop FIRST** — `make infra-up` will hang silently otherwise. |
| Ollama | latest | `ollama --version` | Required for the default `local_qwen` planner. If you have a Groq key you can skip Ollama — see [§ 4](#4--choosing-the-planner). [Download](https://ollama.com/) |
| `make` | any | `make --version` | macOS ships it; on Linux `sudo apt install build-essential`. |
| `psql` (optional) | any | `psql --version` | Not strictly required but useful for the troubleshooting flow in § 8. `brew install libpq && brew link --force libpq`. |

### 1.1 Pull the Ollama model up front

The default planner uses a 27B-parameter dense Qwen 3.6 model that is **~16 GB on disk** and **~17–19 GB resident**. Start this pull NOW so it can run in the background while you do the rest of the setup:

```bash
ollama serve &        # background, listens on http://127.0.0.1:11434
ollama pull qwen3.5:35b    # ~22 GB, takes 10–20 min on a fast link
```

If you're on a memory-constrained machine (< 24 GB RAM), substitute a smaller model and remember to override `PLANMYAGENTS_QWEN_MODEL` in your `.env`:

```bash
ollama pull qwen3:8b
# Then set PLANMYAGENTS_QWEN_MODEL=qwen3:8b in .env
```

### 1.2 If you have a Groq API key

Skip the Ollama pull entirely and use the hosted [Groq](https://console.groq.com/keys) inference instead. Free tier is fast and generous; see [§ 4](#4--choosing-the-planner) for how to wire it.

---

## 2 — Five-minute happy path

Assumes you finished § 1 (in particular: Docker Desktop running, `ollama pull qwen3.5:35b` finished).

```bash
# 1. Clone + Python + Node deps (one-time, ~3–5 min)
git clone <repo-url> agent-manager
cd agent-manager
make install          # creates .venv and installs from requirements.txt
make web-install      # cd apps/web && npm install

# 2. Wire your env (one-time)
cp .env.example .env
# Open .env and (optionally) paste GROQ_API_KEY, GITHUB_TOKEN, etc.
# The defaults work for a local run — no edits strictly required.

# 3. Start Postgres + run migrations + seed (one-time per fresh checkout)
make stack-up         # docker-compose Postgres + scripts/apply_migrations.py
                      # finishes with "Stack ready." + how-to-run-api hint

# 4. Start the two app processes (in two separate terminals)
make api              # terminal A → http://127.0.0.1:8000
make web              # terminal B → http://127.0.0.1:3000
```

Open [http://localhost:3000](http://localhost:3000). You should see the homepage with the **Live evidence** strip showing non-zero counts (`Benchmark runs`, `Verifications`, `Scout runs · 24h`, `Demand events · 24h`). If you see all zeros and an "API not reachable" banner, jump to [§ 8](#8--troubleshooting).

---

## 3 — First `/goal` smoke (proves the LLM tier works)

The homepage shows backend health. The `/goal` form proves the **full plan → discover → route → refuse-honestly stack** works end-to-end. This is the test that catches "did Ollama start?" / "did pgvector apply?" / "is my Groq key valid?" all at once.

1. On the homepage, in the **Goal** box, paste: `buy cheapest glenlivet in tamil nadu`.
2. Click **Submit**. Expect a 60–120 second wait while the local LLM plans (the page shows progressive "Scout runs", "Planner reasoning" tiles).
3. The outcome card should render **"Plan outline only"** with a list of 3–5 sub-tasks (`store_locator`, `price_comparison`, `shipping_quote`, etc.) and a `Recipe coverage: 0/N exportable steps (gap-only runbook)` line.

That outcome — *"the planner produced sub-tasks, none are routable"* — is the **correct** behaviour for a long-tail goal with no benchmarked Indian-whisky-pricing agents in the index. **Seeing it is the success signal.** It confirms:

- Ollama answered (planning worked)
- Postgres + pgvector answered (candidate scout worked)
- The vendor-neutral refusal path renders (the deck's central claim)

If `/goal` hangs past 4 minutes, see [§ 8](#8--troubleshooting) — almost always Ollama-related.

---

## 4 — Choosing the planner

`PLANMYAGENTS_PLANNER` (set in `.env`) picks which LLM tier the `/goal` endpoint uses:

| Value | When to use | Cost | Latency per goal | Setup |
|---|---|---|---|---|
| `local_qwen` (default) | You ran `ollama pull` and have a beefy laptop. | Free (your electricity). | 60–120 s | Ollama + model pulled (§ 1.1) |
| `groq` | You have a free Groq key and want fast iteration. | Free up to generous quota. | 2–5 s | Set `GROQ_API_KEY` in `.env` |
| `escalating` | Production-shaped — tries Groq first, falls back to local Ollama. | Mostly free. | 2–5 s typical | Both above |
| `rules` | Debugging the routing layer without an LLM in the path. | Free. | < 1 s | Nothing extra. **Output is not real planning** — substring rules only. |

Inner-loop tip: while iterating on UI / routing changes, set `PLANMYAGENTS_PLANNER=groq` (or `rules`) so `/goal` takes seconds instead of minutes.

---

## 5 — Useful loops

### 5.1 Inner-loop test runs

| Command | What it runs | Wall time | When to use |
|---|---|---|---|
| `make test-fast` | 1,190 tests; skips the 29 `FastAPIAppTest` cases that fire live LLM calls | **~45 s** | Default during edit-loop iteration. |
| `make test` | Full 1,219 tests including the slow LLM ones | ~10 min | Before committing anything that touches the planner / `/goal` path. |
| `make lint` | `ruff check` on the Python source | ~3 s | Before commit. |
| `cd apps/web && npm run typecheck` | `tsc --noEmit` | ~2 s | After any TypeScript edit. |
| `cd apps/web && npm run build` | Next.js production build | ~6 s | Before opening a PR. |

### 5.2 Playwright end-to-end

First-time setup:

```bash
make web-e2e-install         # one-time: installs @playwright/test + chromium
```

Then:

```bash
make web-e2e                 # runs the full 9-test suite (~2 min)
# or, for a single test:
cd apps/web && PLANMYAGENTS_WEB_BASE_URL=http://localhost:3000 \
  npx playwright test tests/e2e/subtasks-list-modes.spec.ts
```

The suite assumes the API (`make api`) AND the web (`make web`) are both up. If either is down the tests fail with a connect-refused, not a useful error.

### 5.3 Frontend route map

| Path | What it shows | Backing endpoint(s) |
|---|---|---|
| `/` | Homepage — Live evidence strip + goal input | `/health/evidence` |
| `/goal` | Submitted-goal result page (sub-tasks list, recipe panel, planner reasoning) | `/goal`, `/goal/explain` |
| `/categories` | All capability clusters | `/discovery/categories` |
| `/categories/{cluster}` | Per-cluster candidate cards | `/discovery/categories/{cluster}` |
| `/agents/{provider_id}` | Vendor info, capabilities, ranking history, verification + benchmark sections | `/discovery/agents/{provider_id}` |
| `/search` | Keyword + embedding search across the discovery store | `/discovery/search` |
| `/leaderboards` | Capability tile grid | `/leaderboards` |
| `/leaderboards/{capability}` | Per-capability provider rankings + credibility banner | `/leaderboards/{capability}` |
| `/open-mcp-opportunities` | Capabilities where APIs exist but no MCP/A2A wraps them, OR pure demand-only gaps | `/open-mcp-opportunities` |
| `/discovery-gaps` | Capabilities with /goal demand but zero supply | `/discovery/gaps` |
| `/demand` | Public demand-signal page (vendor BD hook) | `/demand/top-capabilities`, `/demand/gaps` |
| `/account` | Pro-tier dashboard (requires Clerk) | `/account/me`, `/recipes` |
| `/partners` | Phase-2 partner landing page | static |

---

## 6 — Optional capabilities (unlocked by API keys)

Every key below is OPTIONAL. The stack runs end-to-end without any of them — you just get a smaller candidate index and fewer benchmark cells.

| Key | Unlocks | Where to get it | Cost |
|---|---|---|---|
| `GROQ_API_KEY` | Hosted Llama / Qwen planner (10–50× faster than local Ollama) | [console.groq.com/keys](https://console.groq.com/keys) | Free tier generous |
| `GITHUB_TOKEN` | GitHub code-search + recently-pushed scouts (huge candidate volume) | `gh auth token` or [github.com/settings/tokens](https://github.com/settings/tokens) | Free |
| `SMITHERY_API_KEY` | Smithery MCP-registry scout | [smithery.ai](https://smithery.ai) → Settings → API Keys | Free |
| `MOLTBOOK_API_KEY` | MoltBook A2A agent-card catalog scout | [moltbook.com](https://moltbook.com) (invite-only beta) | Free |
| `BRAVE_SEARCH_API_KEY` | Brave search scout (Tier-2 paid) | [brave.com/search/api](https://brave.com/search/api/) | 2,000 free queries/mo |
| `TAVILY_API_KEY` | Tavily search scout (Tier-2 paid alternative) | [tavily.com](https://tavily.com) | 1,000 free searches/mo |
| `EXA_API_KEY` | Exa neural search scout | [exa.ai](https://exa.ai) | Paid |
| `HUNTER_API_KEY`, `RESEND_API_KEY`, `FIRECRAWL_API_KEY`, `SHIPPO_API_TOKEN`, `EBAY_OAUTH_TOKEN`, `STRIPE_SECRET_KEY`, `RAZORPAY_KEY_ID`/`SECRET` | Benchmark-baseline cells for `email_verification`, `email_send`, `web_scraping`, `shipping_quote`, `price_comparison`, `payment_authorization` (Stripe + Razorpay) | Per-vendor signup link in `.env.example` | All free tier |
| `PLANMYAGENTS_CLERK_ISSUER` (+ `STRIPE_*`, `PLANMYAGENTS_MARKETPLACE_STORE_*`) | Pro-tier auth + saved-recipes + checkout (`/account` page) | [clerk.com](https://clerk.com) (free dev tier) + Stripe test mode | Free in dev |

Run `make env-check` at any point to see what's wired vs. missing. **Note:** the registry the `env-check` script consults is intentionally narrower than `.env.example` itself — if you want to confirm a specific key is being read at runtime, grep for it under `apps/api/planmyagents_api/`.

---

## 7 — Production-shaped run

For anything beyond a local laptop run (CI, a hosted demo, a VPS):

```bash
# One-shot: brings up Postgres, applies migrations, seeds reference data.
make stack-up

# Or piecewise, if you want to inspect each step:
make infra-up           # docker compose up -d postgres
make migrate            # scripts/apply_migrations.py — idempotent
make bootstrap-postgres # seeds reference rows (capabilities, baseline registry)

# Tear down:
make stack-down
```

### 7.1 When to run `make migrate` separately

The pgvector Postgres image only auto-runs the SQL files in `infra/postgres/init/*.sql` **on first volume creation**. If you have a pre-existing `planmyagents_postgres_data` Docker volume from a previous checkout, files like `002_evidence_health.sql` will **not** apply automatically. `make migrate` is the catch-up path — idempotent, safe to re-run.

### 7.2 Building the API container

```bash
make deploy-build-api    # docker build -f infra/api/Dockerfile -t planmyagents-api:local .
```

See `docs/operations.md` § 5 for the full hosted-deployment runbook.

---

## 8 — Troubleshooting

Symptoms ranked by how often they bite a new contributor:

| Symptom | Most likely cause | Fix |
|---|---|---|
| `/goal` hangs past 2 min, never resolves | Ollama not running OR model not pulled OR wrong model name in `.env` | `curl http://127.0.0.1:11434/api/tags` should list `qwen3.5:35b`. If not: `ollama serve &` then `ollama pull qwen3.5:35b`. |
| `/goal` returns immediately with `"error": "GroqChatError ..."` | `PLANMYAGENTS_PLANNER=groq` but `GROQ_API_KEY` empty or wrong | Either paste a valid key into `.env` (free tier at [console.groq.com](https://console.groq.com/keys)) or fall back: `PLANMYAGENTS_PLANNER=local_qwen`. |
| Homepage shows all-zero counts + rose "database unreachable" banner | Postgres container not running | `docker ps \| grep planmyagents-postgres` — if empty, `make infra-up`. If Docker Desktop itself is stopped, start it first. |
| `make infra-up` hangs silently | Docker Desktop not running | Start Docker Desktop, wait for the whale icon to settle, retry. |
| API logs `"two different databases"` warning at startup | `.env` has only some of the store URLs set; one path is falling back to a different DSN | Set ALL of `PLANMYAGENTS_{DISCOVERY,BENCHMARK,VERIFICATION,PROMOTED_PROVIDER,MARKETPLACE}_STORE_URL` to the same Postgres DSN, or unset all of them to let `_config.py:DEFAULT_POSTGRES_DSN` take over. |
| `/search` returns 500 | No embeddings written yet | `make embed-candidates` (creates embeddings for the seeded discovery candidates). |
| `/agents/<slug>` returns 404 even though the homepage shows candidates | Slug typo — the API key is `provider_id`, not the human display name | Click through from `/categories` or `/search`; both link to the canonical id. |
| Playwright E2E fails with `Cannot find module '@playwright/test'` | First-time clone, deps not yet installed | `make web-e2e-install` (one-time, installs `@playwright/test` + chromium). |
| Playwright fails connect-refused on `http://localhost:3000` | Web dev server not running | `make web` in another terminal first. |
| `make test` hangs forever | Trying to run the slow `FastAPIAppTest` suite (29 live-LLM tests) without Ollama up | Either start Ollama first, or use `make test-fast` (skips them — runs in 45 s). |
| `cp .env.example .env` says "No such file or directory" | Fresh clone before 2026-05-20 had `.env.example` accidentally `.gitignore`'d | Already fixed on `main` (`.gitignore` now has `!.env.example`). If you cloned earlier, `git pull`. |
| `STRIPE_SECRET_KEY` set in the benchmark block is ignored | Pre-2026-05-20 the env var was declared twice and the second silently shadowed the first | Already fixed on `main`. `git pull`. |
| New scout source (Smithery/MoltBook) shows "0 candidates" | Token missing — both sources silently skip when their key is unset | Add `SMITHERY_API_KEY` / `MOLTBOOK_API_KEY` to `.env`; see [§ 6](#6--optional-capabilities-unlocked-by-api-keys). |
| `/leaderboards` tiles show `Bench-passed 0 · Routable 0` for cells that you know have runs | Pre-aggregated `agent_rankings` table is stale | `make rebuild-rankings` (rebuilds from raw `benchmark_runs`; idempotent). |

If you hit something not listed here, the fastest diagnostic flow is:

```bash
curl -s http://127.0.0.1:8000/health/evidence | jq '{db_reachable, db_error, benchmark_runs_total, verification_records_total}'
```

`db_reachable: false` plus a string `db_error` is now the canonical "DB is the problem" signal (added in the 2026-05-19 health-endpoint fix).

---

## 9 — Where things live

```
agent-manager/
├── apps/
│   ├── api/                     FastAPI backend
│   │   └── planmyagents_api/
│   │       ├── _config.py       Single source of truth for store URL defaults
│   │       ├── web/             FastAPI app, routes, planning glue
│   │       │   ├── app.py       App factory + lifecycle (large; under-decomposition still)
│   │       │   ├── planning.py  /goal handler — picks planner tier, runs full stack
│   │       │   ├── routes/      One APIRouter per feature cluster (health, billing, recipes, …)
│   │       │   ├── models.py    Pydantic response shapes (mirror the frontend api.ts types)
│   │       │   └── rate_limiter.py   Token-bucket; bounded memory per 2026-05-19 fix
│   │       ├── planner/         LLM planners (local_qwen, groq, escalating, rules)
│   │       ├── discovery/       17+ scouts (4 channel classes), candidate judge, verification ladder
│   │       ├── benchmark/       Per-capability baselines + ranking compute + rebuild
│   │       ├── agents/          Protocol adapters (MCP, A2A, OpenAPI), sandbox runner
│   │       ├── cost/            Per-goal + daily spend caps
│   │       ├── billing/         Stripe checkout + webhook handling
│   │       ├── marketplace_store/  Pro-tier user + saved-recipe rows
│   │       └── ...
│   └── web/                     Next.js 16 frontend
│       └── src/
│           ├── app/             Route files (one folder per URL)
│           ├── components/      Shared UI (goal/, account/, …)
│           ├── lib/
│           │   ├── api.ts       Typed API client — every backend endpoint
│           │   ├── format.ts    Display-format helpers (formatLabel, …)
│           │   └── tags.ts      Status-badge classes
│           └── ...
├── packages/
│   └── registry/                Static provider registry (gated capabilities, baseline firewall)
├── scripts/                     One-shot CLI tools (discovery refresh, migrations, backfills, …)
├── infra/
│   ├── postgres/init/           SQL files auto-run on first Postgres volume creation
│   └── api/Dockerfile           Production API container
├── docs/                        Architecture, HLD, LLD, operations, GET_STARTED (this file)
├── data/                        Local JSON event logs (dev fallback when no Postgres DSN)
├── .env.example                 Annotated template — copy to .env
├── Makefile                     Every target you'll run
└── sprint-pitch-align.md        Sprint log — read for "why is this code this shape?" context
```

---

## 10 — Next steps

You have a working install. Now:

- **Read the strategy:** [`README.md`](../README.md) (the flywheel, positioning, marketplace shape).
- **Read the architecture:** [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) → [`docs/HLD.md`](HLD.md) → [`docs/LLD.md`](LLD.md) in that order.
- **Read the sprint log:** [`sprint-pitch-align.md`](../sprint-pitch-align.md) — every recent code-shape decision is justified there with verify commands. The most recent "Final pre-launch sweep — 2026-05-20" section catches you up on the last batch of bugs we fixed.
- **Run an experiment:** try a different goal that's likely to be executable today (e.g. `"send an email to bob@example.com saying hi"` once you have `RESEND_API_KEY`) and watch the outcome card flip from "outline-only" to "executable_not_run" with the run button enabled.
- **Find your wedge:** open [`sprint-pitch-align.md`](../sprint-pitch-align.md) and look at the TIER-B / TIER-C remainder list at the bottom of the 2026-05-20 sweep. Each item is a cleanly-scoped first PR.

Welcome to the codebase.
