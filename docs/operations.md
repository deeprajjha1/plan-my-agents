# Operations

End-to-end ops for PlanMyAgents. Covers local Postgres, hosted Postgres,
embedding backfill, benchmark scheduling, and deployment of the API + web app.
For an AWS-specific production path under `www.planmyagents.com`, see
[`docs/aws-hosting-guide.md`](aws-hosting-guide.md).

The whole stack is designed to be:

- **Idempotent** — every script can be re-run without breaking state.
- **Honest** — synthetic / mock data is always tagged `source: synthetic` so
  the UI can label it clearly and never imply real performance.
- **Optional in pieces** — the deterministic embedder works with no extra
  dependencies; semantic embeddings are opt-in via `PLANMYAGENTS_EMBEDDING_MODEL`.

## 1. Local stack (single command)

```bash
cp .env.example .env
make stack-up       # docker compose + bootstrap-postgres
make api &          # FastAPI on http://127.0.0.1:8000
make web-install    # one-time
make web            # Next.js on http://127.0.0.1:3000
```

`stack-up` runs `infra-up` (docker compose Postgres + pgvector) followed by
`bootstrap-postgres` which:

1. Applies all schemas (`scripts/apply_migrations.py`).
2. Refreshes discovery candidates from curated sources.
3. Verifies candidate evidence and persists verification history.
4. Backfills embeddings for every candidate.
5. Runs the benchmark scheduler so cards have honest numbers.

## 2. Recurring upkeep (cron)

```bash
# crontab -e
0 * * * * cd /path/to/agent-manager && ./scripts/upkeep_loop.sh \
    >> .planmyagents_runs/upkeep.log 2>&1
```

`scripts/upkeep_loop.sh` is the same pipeline as bootstrap minus migrations,
intended to be safe to re-run while the API + web app are serving traffic.

### 2.1 Evidence cron (sprint-pitch-align Phase 3)

The homepage strip, the recent-verifications panel, and the deck's
"evidence-first" claim all depend on the five `*_events` / `*_runs` /
`*_records` tables being non-empty. Three cron targets keep them fresh:

| Target | Cadence | What it does | Backed-up table |
|---|---|---|---|
| `make benchmark-cron` | every 24 h | Runs every capability with `requires_benchmark_gate=true` in `agents.json`. Drops the historical hardcoded `email_verification` filter. Honours `CASES_PER_CAPABILITY=N` (default 5). | `benchmark_runs` |
| `make discovery-verify-cron` | weekly for `known_provider`, monthly for `registered_in_directory` / `capability_verified` | Re-fetches the candidate's `evidence_url`, re-runs the verifier, persists one row, and auto-demotes one tier when the recovery window (`2 × cadence`) elapses without a successful verification. Capped at `MAX_CANDIDATES` (default 50). | `verification_records` |
| `make evidence-cron` | every 24 h | Composite: `verify-top-candidates` → `benchmark-cron`. Wire this one in cron and the other two run automatically. | both of the above |

Bootstrap into crontab:

```bash
# crontab -e
0 6 * * * cd /path/to/agent-manager && \
    PLANMYAGENTS_DISCOVERY_STORE_URL=postgresql://... make evidence-cron \
    >> .planmyagents_runs/evidence-cron.log 2>&1
```

The Makefile targets auto-load `.env`, so a single `PLANMYAGENTS_DISCOVERY_STORE_URL`
in `.env` is enough for production. All three are idempotent and safe to
re-run; if nothing is due, `discovery-verify-cron` exits 0 with `due: 0`.

### 2.2 Evidence health view + dashboards

`infra/postgres/init/002_evidence_health.sql` defines a `evidence_health`
Postgres view that exposes the same eight counters the FastAPI
`/health/evidence` endpoint returns — `benchmark_runs_24h`,
`verification_records_7d`, `discovery_run_events_24h`,
`capability_demand_events_24h`, `discovery_gap_events_24h`,
`route_status_routable_count`, `benchmark_runs_total`,
`verification_records_total`. `scripts/apply_migrations.py` applies it
automatically; pointing any Datadog / Grafana / Metabase / dbt instance at
the view is enough to chart cron health without depending on the API
being up.

```sql
SELECT * FROM evidence_health;
```

The view's column names and semantics MUST stay in lock-step with
`EvidenceHealthResponse` (`apps/api/planmyagents_api/web/models.py`). If
you change one, change the other — otherwise the homepage and the
dashboard will silently disagree.

### 2.3 Evidence-health CI guard

`scripts/check_evidence_health.py` fails the build (exit 1) when any
configured counter drops below its minimum. The shipped
`.github/workflows/evidence-health-guard.yml` runs it on every push to
`main` and on a daily cron, enforcing:

* `benchmark_runs_total ≥ 1`
* `verification_records_total ≥ 1`
* `route_status_routable_count ≥ 1`

Locally:

```bash
make check-evidence-health  # uses MIN_* env overrides; defaults to 1 for each
```

Runbook when the guard turns red:

1. `make health-evidence` — confirm the zero is real, not a flaky read.
2. `SELECT * FROM evidence_health;` — same counts, queried directly.
3. If `benchmark_runs_total = 0`: `make benchmark-cron` and inspect
   the per-candidate summaries for `adapter_error:...` notes (usually a
   missing `*_KEY_ID` env var on the cron host).
4. If `verification_records_total = 0`: `make discovery-verify-cron-dry`
   to see which candidates are due, then drop `--dry-run` to actually
   run them.
5. If `route_status_routable_count = 0`: at least one
   `(provider_id, capability)` pair needs a successful row in
   `benchmark_runs` within the last 30 days — pick a hermetic cell
   (Razorpay test mode is the canonical one) and re-run with valid
   creds in `.env`.

## 3. Hosted Postgres

The bootstrap script accepts a `POSTGRES_URL` override:

```bash
POSTGRES_URL=postgresql://USER:PASS@HOST:PORT/DB ./scripts/bootstrap_postgres.sh
```

Or set `PLANMYAGENTS_DISCOVERY_STORE_URL` (and the matching `_BENCHMARK_` and
`_VERIFICATION_` URLs) in `.env`. Recommended hosted options:

- [Neon](https://neon.tech) (`pg_vector` is native, the dev tier is free).
- [Supabase](https://supabase.com) (`pgvector` enabled with one click).
- Self-managed Postgres 14+ with `CREATE EXTENSION vector;`.

The `pgvector` extension is required. `scripts/apply_migrations.py` runs
`CREATE EXTENSION IF NOT EXISTS vector` before creating the discovery tables.

## 4. Semantic embeddings (optional)

The default embedder is `DeterministicHashEmbedder` — zero external
dependencies, deterministic outputs, useful for keyword recall. To switch to a
real semantic backend, run [Ollama](https://ollama.com/):

```bash
ollama pull nomic-embed-text
export PLANMYAGENTS_EMBEDDING_MODEL=nomic-embed-text
make embed-candidates    # re-embeds every candidate against the new model
```

The API picks up the model from the same env var; the `/health` endpoint
returns the active embedder name.

## 5. Deploying the API

A production-ready Dockerfile lives at `infra/api/Dockerfile`.

```bash
make deploy-build-api       # docker build -f infra/api/Dockerfile -t planmyagents-api:local .

# Run against your hosted Postgres
docker run --rm -p 8000:8000 \
  -e PLANMYAGENTS_DISCOVERY_STORE_URL=postgresql://... \
  -e PLANMYAGENTS_BENCHMARK_STORE_URL=postgresql://... \
  -e PLANMYAGENTS_VERIFICATION_STORE_URL=postgresql://... \
  -e PLANMYAGENTS_CORS_ALLOW_ORIGINS=https://YOUR-FRONTEND \
  planmyagents-api:local
```

The container exposes `8000`, runs as a non-root `planmyagents` user, and
includes a `HEALTHCHECK` that hits `/health` every 30s.

Suggested platforms:

- AWS EC2 + RDS — current public-domain launch path; see
  [`docs/aws-hosting-guide.md`](aws-hosting-guide.md).
- [Railway](https://railway.app) — still viable for a quick non-AWS prototype.
- [Fly.io](https://fly.io) — still viable for a quick non-AWS prototype.
- Any Kubernetes cluster — the image is plain enough to drop into a Deployment.

## 6. Deploying the web app

The Next.js app at `apps/web/` is App-Router, statically renders the `/goal`
shell, and server-renders all the data-driven pages.

### Vercel / managed Next.js host (optional)

A `vercel.json` is included so a default project can pick up the right build,
but the current `planmyagents.com` launch guide uses AWS CloudFront + EC2.

1. New Vercel project pointing at `apps/web/`.
2. Set environment variables:
   - `PLANMYAGENTS_API_BASE_URL` — used by server components.
   - `NEXT_PUBLIC_PLANMYAGENTS_API_BASE_URL` — used by the `/goal` page
     client-side fetch.
3. Deploy. The build runs `npm install && npm run build`.

### Self-hosted

```bash
cd apps/web
npm install
npm run build
npm run start          # next start --port 3000
```

Set `PLANMYAGENTS_API_BASE_URL` in the runtime env so server components reach
the API.

## 7. Discovery enrichers (Setup / Tools / Skills metadata)

Discovery sources only know the bare minimum about a candidate (name, vendor
URL, declared capabilities). Enrichers fill in the *Setup / Tools / Skills*
metadata used by the agent detail page and future auto-adapter generation.
Each enricher is opt-in, idempotent, and skips candidates that already have
their target field populated.

| Make target | What it does | When to run |
|---|---|---|
| `make mcp-tool-probe` | POSTs `tools/list` to every MCP candidate with an HTTP(S) `vendor_url` and persists the returned tool schemas. | After any MCP HTTP endpoint is added. |
| `make openapi-enricher` | Fetches each candidate's `openapi_url`, normalises the top-N operations into `tools` with a synthesised input schema. | Whenever new candidates carry an `openapi_url`. |
| `make docs-extractor` | LLM-backed pass over each candidate's `evidence_url`. Fills `docs.auth_method` / `install_steps` / `usage_examples`. Defaults to a `--limit 5` to keep model spend bounded. | Optionally, on a slow cron after discovery refresh. |

Each script writes back to the configured discovery store so the next API
read picks up the new fields without further glue.

## 8. Planner backends

`PLANMYAGENTS_PLANNER` selects how user goals are decomposed:

| Mode | Backend | Notes |
|---|---|---|
| `escalating` (default) | Escalating chat client that walks an ordered tier list (Groq scout → Groq 70B → Qwen → local) and stops at the first tier that returns a confidence-passing decomposition. This is the production default; `_DEFAULT_PLANNER_MODE` in `web/planning.py`. | The escalation order is configured by `GROQ_MODELS` (comma-separated list, ordered). Without `GROQ_API_KEY` the Groq tiers are skipped and the client effectively falls back to local Qwen, so the `escalating` default is also safe in fully offline environments. |
| `local_qwen` | Local Ollama Qwen via `OllamaQwenClient`, single-tier. | No API key needed; quality varies. Ships with a beefed-up prompt that includes worked-example decompositions for cross-border real-estate, regulated payments, immigration, and travel goals. Use this mode when you specifically want to disable LLM escalation for debugging. |
| `groq` | Groq's hosted Llama 3.3 / Mixtral / Qwen via `GroqChatClient`, single-model. | Set `GROQ_API_KEY`. Optional: `GROQ_MODEL` (default `llama-3.3-70b-versatile`), `GROQ_BASE_URL`. Same prompt and same JSON contract as `local_qwen`. Single-model only — for the production multi-tier escalation use `escalating` instead. |
| `rules` | Deterministic rule planner | Used as the fallback when the chosen LLM is unreachable. |

When `PLANMYAGENTS_BRAINSTORM=1` is set, the planner runs an extra
*capability brainstorm* call after producing an `unsupported` plan. It asks
the LLM to propose additional snake_case capability ids the catalog doesn't
yet know about. New ids are merged into `missing_capabilities` and persisted
to `.planmyagents_runs/capability-demand.jsonl` as a discovery demand signal.

### 8a. Pre-plan discovery and generic protocol execution (Gap 3 + Gap 4)

Three env vars control the new "discover, then plan, then invoke"
pipeline that closes the honest-scope gaps from the 2026-05-15 audit.
All three default to OFF in production until an operator opts in:

| Env var | Default | What it does |
|---|---|---|
| `PLANMYAGENTS_PRE_PLAN_DISCOVERY` | `false` | When ON, `/goal` runs scouts against the goal-decomposer's per-sub-task `search_query` BEFORE the route handler does its own refusal-path discovery, persisting via `save_merge` so embeddings re-roll inline. The route handler reads the pre-plan summary from `metadata['pre_plan_discovery']` and skips its own duplicate `_live_discovery` call when scouts already ran. Adds ~5–35s latency on first refusal in exchange for cache warmth and a richer in-response gap explanation. Flip to `true` after promoting your first generic-adapter-routable MCP server. |
| `PLANMYAGENTS_PRE_PLAN_MAX_CAPABILITIES` | `3` | Hard cap on capabilities the pre-plan stage will dispatch scouts for in a single request (worst-case-latency guard). |
| `PLANMYAGENTS_POST_GOAL_PROBE` | `true` | When ON, every post-goal background refresh probes reachable MCP servers for `tools/list` (parallel, 8 workers, 5s per-probe timeout, 20-candidate cap) and persists via `save_merge` so the new tool data flows into pgvector immediately. Set to `false` to skip the probe stage (e.g. tests, or environments with hostile MCP timeouts). |
| `PLANMYAGENTS_POST_GOAL_PROBE_LIMIT` | `20` | Number of candidates probed per refresh tick. Raise for thicker MCP fleets. |
| `PLANMYAGENTS_POST_GOAL_PROBE_TIMEOUT_SEC` | `5.0` | Per-probe HTTP timeout. |
| `PLANMYAGENTS_POST_GOAL_PROBE_MAX_WORKERS` | `8` | ThreadPoolExecutor width. |
| `PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION` | `false` | Master switch for the generic protocol adapters (`GenericMcpAdapter`, `GenericOpenApiAdapter`, `GenericA2AAdapter`, `GenericAiAgentAdapter`). When OFF, every `execute()` returns a structured "execution is disabled" refusal — no third-party HTTP is performed even by accident. When ON, MCP candidates with HTTP transport are invocable via JSON-RPC `tools/call`, and OpenAPI candidates with a usable `openapi_url` are invocable via the spec's declared operations. A2A and AI-agent providers still return structured refusals (their wire formats aren't pinned down enough for a generic client). |

The intended progression for a new operator:

1. Bring up the discovery store + curated sources (`make stack-up`).
2. Confirm `make test` is green and `make discovery-refresh` populates
   the store.
3. Enable `PLANMYAGENTS_POST_GOAL_PROBE=true` so MCP servers get their
   tool surface populated automatically.
4. Sandbox-only: enable `PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION=true`
   with a non-prod cost-cap config and exercise the
   `GenericMcpAdapter` / `GenericOpenApiAdapter` paths against trusted
   targets.
5. After confirming a handful of generic-adapter-routable capabilities
   in the dev sandbox, flip `PLANMYAGENTS_PRE_PLAN_DISCOVERY=true` so
   the planner sees freshly-discovered candidates within the same
   `/goal` request, not just on the next one.

## 9. Make targets reference

The canonical product UI is **two processes**: FastAPI (`make api`, port
`8000`) and Next.js (`make web`, port `3000`). Everything else in this
table is supporting infrastructure.

### Boot

| Target | What it does |
|---|---|
| `make install` | Create the Python venv + install backend deps |
| `make web-install` | `npm install` inside `apps/web/` |
| `make api` | FastAPI dev server on `:8000` |
| `make web` | Next.js dev server on `:3000` |
| `make web-build` | `next build` (production build) |
| `make demo` | Legacy stdlib demo UI on `:8787` (predates the FastAPI/Next.js stack; kept for the planner + execution walkthrough) |

### Postgres + bootstrap

| Target | What it does |
|---|---|
| `make infra-up` / `make infra-down` | `docker compose up/down postgres` only |
| `make stack-up` | `infra-up` + apply migrations + bootstrap data (idempotent) |
| `make stack-down` | `docker compose down` |
| `make bootstrap-postgres` | One-shot data bootstrap (migrations + refresh + verify + embed + benchmark) |
| `make migrate` | Apply Postgres schemas only (`scripts/apply_migrations.py`) |
| `make migrate-apis-without-agents` | One-shot migration for local SQLite/JSON stores: moves any legacy `api_provider`/`payment_provider` rows out of `discovery_candidates` into `apis_without_agents`. Idempotent. Postgres performs the same migration inline in `001_planmyagents.sql` |

### Discovery pipeline

| Target | What it does |
|---|---|
| `make discovery-refresh` | Refresh discovery candidates from curated sources |
| `make upkeep` | Daily upkeep tick (no migrations) |
| `make discovery-research` | Broad GitHub/URL research pass |
| `make growth-pass` | Full live-research + index pass — useful right after first install |
| `make index-pass` | Curated refresh + OpenAPI / docs enrichers + verification |
| `make mcp-tool-probe` | Probe MCP HTTP endpoints for `tools/list` |
| `make openapi-enricher` | Resolve `openapi_url` into `tools` |
| `make docs-extractor` | LLM-backed Setup metadata extractor |
| `make env-check` | Print which discovery connectors are wired and which are missing keys |

### Benchmarks + credibility

| Target | What it does |
|---|---|
| `make verify-candidates` | Run verification + persist verification history |
| `make embed-candidates` | Backfill embeddings (deterministic by default; Ollama-backed when `PLANMYAGENTS_EMBEDDING_MODEL` is set) |
| `make benchmark-schedule` | Run the benchmark scheduler |
| `make audit-ranking-sources` | Honest snapshot of which source provided each ranking entry |
| `make benchmark-credibility-report` | Write `reports/benchmark-credibility/{date}.md` |
| `make generalization-eval` | Run the generalization eval suite (planner / discovery probes) |

### Quality + deploy

| Target | What it does |
|---|---|
| `make test` | Backend stdlib `unittest` suite — full 1,219 cases incl. 29 live-LLM `/goal` integration tests (~10 min). Use `make test-fast` for the deterministic 1,190 subset (~45 s) during inner-loop iteration. |
| `make lint` | `ruff check` over `apps/` + `scripts/` |
| `make quality` | All tests + lint + JSON validation + smoke pipelines |
| `make deploy-build-api` | Build the API container image |

## 10. Benchmark baselines (post 16-May-2026 pivot)

The seven hand-written vendor adapters (Razorpay, Stripe, Resend,
Firecrawl, Shippo, eBay Browse, Hunter) live under
`apps/api/planmyagents_api/benchmark/baselines/` and are reachable
ONLY from the benchmark runner / benchmark scheduler. They are
firewalled out of the customer `/goal` execution path by:

1. `is_benchmark_baseline: true` flag on each registry entry in
   `packages/registry/agents.json`.
2. `ProviderRouter._is_benchmark_baseline()` filter applied to every
   routing decision (see `apps/api/planmyagents_api/agents/router.py`).
3. `ProviderRouter.provider_for(...)` raises `PermissionError` if a
   baseline is requested by id.
4. Test gate: `tests/test_provider_router.py::BenchmarkBaselineFirewallTest`
   and `tests/test_benchmark_baseline_firewall.py` lock in the
   invariants. Any PR that breaks them fails CI.

### Running a baseline benchmark manually

The legacy invocation path still works — the `import` path changed
from `planmyagents_api.agents.<vendor>` to
`planmyagents_api.benchmark.baselines.<vendor>`:

```bash
RAZORPAY_KEY_ID=rzp_test_... RAZORPAY_KEY_SECRET=... \
    PYTHONPATH=apps/api .venv/bin/python -c '
from planmyagents_api.benchmark.runner import run_sync
from planmyagents_api.benchmark.baselines.razorpay import RazorpayPaymentAuthorization
from pathlib import Path
runs = run_sync(
    provider=RazorpayPaymentAuthorization(),
    capability="payment_authorization",
    benchmarks_dir=Path("packages/benchmarks"),
)
for r in runs:
    print(r.test_case_id, r.score.quality_score)
'
```

### Adding a new baseline

1. Add the adapter module under
   `apps/api/planmyagents_api/benchmark/baselines/<vendor>.py`. It must
   structurally satisfy
   `planmyagents_api.benchmark.baselines.BenchmarkAdapter`.
2. Register the registry entry in `packages/registry/agents.json` with
   `"is_benchmark_baseline": true`, `"adapter_module": ""`, and
   `"benchmark_adapter_module": "planmyagents_api.benchmark.baselines.<vendor>:<Class>"`.
3. Add the baseline id to
   `tests/test_benchmark_baseline_firewall.py::EXPECTED_BENCHMARK_BASELINE_IDS`
   in the same PR. The test asserts both sets match so the firewall
   surface is reviewed every time it widens.
4. Ship a paired benchmark case YAML under
   `packages/benchmarks/<capability>/` — the baseline only earns its
   keep by scoring discovered MCP / OpenAPI / A2A providers against a
   known-good reference.

### When NOT to add a baseline

Per the 16-May-2026 product spec we DO NOT add wrappers for
production execution. If the goal is "let users execute against
vendor X", the answer is: improve discovery so we surface vendor X's
official MCP server (or OpenAPI spec) and execute via the
generic-protocol adapter family. Hand-written wrappers exist only
to give the benchmark runner a reference signal; never as a
customer-facing path.

## 11. Pro tier (Sprint 4 T1-B)

The Pro tier wires Clerk-based authentication, Stripe Checkout
billing, and saved recipes on top of the recipe-export endpoint
(T1-A). All four pieces are **placeholder-safe** — missing env vars
keep the public read-only surface working and degrade only the
account-backed actions to clean 401/503 responses.

### 11.1 Env vars

```bash
# Clerk (apps/api/planmyagents_api/auth/clerk.py)
PLANMYAGENTS_CLERK_ISSUER=https://wise-koala-12.clerk.accounts.dev
# Optional — derived from issuer if not set.
PLANMYAGENTS_CLERK_JWKS_URL=
# Optional CSV — accepted azp values for the JWT (frontend origins).
PLANMYAGENTS_CLERK_AUTHORIZED_PARTIES=http://localhost:3000

# Dev-mode bypass — accept X-Dev-Clerk-User-Id and X-Dev-Clerk-User-Email
# headers. Refused at process boot when PLANMYAGENTS_ENVIRONMENT=production.
# PLANMYAGENTS_AUTH_MODE=dev

# Marketplace store — Pro-tier users + saved_recipes tables.
# Same DSN as the discovery store is fine in prod; tables live in a
# separate `marketplace_store` Postgres schema for blast-radius isolation.
PLANMYAGENTS_MARKETPLACE_STORE_URL=postgresql://...
# SQLite for local dev:
# PLANMYAGENTS_MARKETPLACE_STORE_PATH=.planmyagents_runs/marketplace.sqlite

# Stripe Checkout — Pro plan billing
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRO_PRICE_ID=price_...
STRIPE_CHECKOUT_SUCCESS_URL=http://localhost:3000/account?upgrade=success
STRIPE_CHECKOUT_CANCEL_URL=http://localhost:3000/account?upgrade=cancelled

# Frontend (apps/web/.env.local) — only what the browser bundle sees.
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
```

### 11.2 Live-mode safety belt

Setting `STRIPE_SECRET_KEY` to a `sk_live_...` key without
`STRIPE_LIVE_MODE_OPT_IN=true` **refuses at config load time** so a
misconfigured dev shell can never accidentally charge real cards.
See `apps/api/planmyagents_api/billing/stripe_client.py:stripe_config_from_env`.

### 11.3 Apply the schema

```bash
make migrate    # runs scripts/apply_migrations.py against PLANMYAGENTS_DISCOVERY_STORE_URL
                # — now also creates marketplace_store.users / workspaces /
                # workspace_members / default_workspaces / saved_recipes.
```

### 11.4 Webhook setup (Stripe Test mode)

1. In the [Stripe Dashboard](https://dashboard.stripe.com/test/webhooks)
   add an endpoint pointing at `https://<your-api-host>/stripe/webhook`.
2. Subscribe to: `checkout.session.completed`,
   `customer.subscription.updated`, `customer.subscription.deleted`.
3. Copy the signing secret (`whsec_...`) into `STRIPE_WEBHOOK_SECRET`.
4. Run a test event from the dashboard; tail logs for
   `stripe.webhook.handled` confirmations.

The webhook handler is idempotent — Stripe retries on 5xx and replaying
the same event id never double-charges (`set_plan` is a no-op when the
plan is already correct).

### 11.5 Local dev without Clerk keys

Start the API with `PLANMYAGENTS_AUTH_MODE=dev` and the frontend
without `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`. The header shows a
"Pro tier: soon" badge; `/recipes` API calls accept
`X-Dev-Clerk-User-Id` / `X-Dev-Clerk-User-Email` headers in lieu of a
JWT. Refused at boot if `PLANMYAGENTS_ENVIRONMENT=production`.
