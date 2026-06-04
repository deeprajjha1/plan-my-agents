# PlanMyAgents — Technical Architecture (v0)

> **What this is:** the v0 architecture of an **Agent Discovery Index + benchmarked routing layer**. The index is the asset; everything users see (`/goal`, `/categories`, `/agents/{id}`, `/search`, `/leaderboards/{cap}`, future `/export?target=n8n`, future `/trust/{cap}`) is a *surface* sitting on top of the index. See `AGENTS.md` for the index-vs-surfaces framing that drives engineering trade-offs.

> **Scope:** the v0 system that ships after discovery validates the wedge. Designed for solo-founder execution velocity, not for premature scale.

> **Principles:** boring tech that scales. Idempotency by default. Cost-bounded execution. Audit-everything. PII-safe by design.

---

## 1. System overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Customer (Browser)                         │
│        Next.js UI — goal input, refusal/plan/output, debug view      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  PlanMyAgents API (FastAPI on AWS EC2 v0)            │
│  - Auth (Clerk planned; dev mode blocked in production)             │
│  - Task lifecycle endpoints                                         │
│  - Stripe payment adapter exists; webhooks are future product work   │
│  - Public benchmark / discovery API (read-only)                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
        ┌──────────────────┼─────────────────┬──────────────────┐
        ▼                  ▼                 ▼                  ▼
   Postgres         In-process         Stripe              LLM
   + pgvector       execution +        adapter          (Local Qwen for
   (RDS prod /      upkeep now                         prototype;
    Docker dev)     workers later                       Claude/OpenAI/
        │                  │                                Gemini for hosted)
        │                  ▼
        │    ┌─────────────────────────────────────┐
        │    │  Request pipeline                   │
        │    │                                     │
        │    │  ┌─────────────────────────┐        │
        │    │  │ planner (LLM/rules)     │────────┤  registry-constrained
        │    │  └─────────────────────────┘        │
        │    │                                     │
        │    │  ┌─────────────────────────┐        │
        │    │  │ intent mapper (LLM)     │────────┤  goal → catalog capability IDs
        │    │  └─────────────────────────┘        │  + reusable constraints
        │    │                                     │
        │    │  ┌─────────────────────────┐        │
        │    │  │ coverage auditor (LLM)  │────────┤  promote optional → required
        │    │  └─────────────────────────┘        │  for explicit constraints
        │    │                                     │
        │    │  ┌─────────────────────────┐        │  MCP catalogs, A2A cards,
        │    │  │ discovery index         │────────┤  AI-agent dirs, OpenAPI,
        │    │  │   (configured + bounded │        │  vendor docs, marketplaces,
        │    │  │    live connectors)     │        │  Brave/Tavily/GitHub
        │    │  └─────────────────────────┘        │
        │    │                                     │
        │    │  ┌─────────────────────────┐        │  evidence quality from cards/
        │    │  │ verification + benchmark│────────┤  metadata/OpenAPI; scored runs
        │    │  └─────────────────────────┘        │  in benchmark store
        │    │                                     │
        │    │  ┌─────────────────────────┐        │  verified + benchmarked +
        │    │  │ promotion gate          │────────┤  adapter + ready_for_promotion
        │    │  └─────────────────────────┘        │  (generic adapters: protocol_beta,
        │    │                                     │   blocked in prod)
        │    │  ┌─────────────────────────┐        │
        │    │  │ router + executor       │────────┤  cost ceiling, retries,
        │    │  │ (Inngest in prod)       │        │  structured output, confidence
        │    │  └─────────────────────────┘        │
        │    │                                     │
        │    │  ┌─────────────────────────┐        │
        │    │  │ stitcher (LLM)          │────────┤  merge, score, flag confidence
        │    │  └─────────────────────────┘        │
        │    │                                     │
        │    │  refusal path (any gate fails) →    │  normalized intent,
        │    │  ranked workflow options,           │  per-capability gaps,
        │    │  rejected fallback explanations,    │  promotion next steps
        │    └─────────────────────────────────────┘
        │
        └── discovery_candidates · apis_without_agents · discovery_run_events
            · capability_demand_events · verification_records · benchmark_runs
            · agent_rankings (data moat)
```

### What is implemented today vs. the diagram

Boxes that currently exist in code:

- **Planner**, **intent mapper**, **coverage auditor**: implemented in
  `apps/api/planmyagents_api/planner/` and shared via `planmyagents_api.web.planning`
  between the local stdlib demo and the FastAPI app.
- **Discovery index** (configured + bounded live connectors), JSON / SQLite /
  Postgres + pgvector stores, dedupe, normaliser, query expansion, freshness:
  implemented in `apps/api/planmyagents_api/discovery/`.
- **Verification** + persisted **verification records**: implemented in
  `apps/api/planmyagents_api/discovery/verification.py` and
  `apps/api/planmyagents_api/discovery/verification_store.py`.
- **Benchmark runs + per-(provider, capability) rankings**: implemented in
  `apps/api/planmyagents_api/benchmark/` (loader, runner, scoring, store, rankings,
  scheduler).
- **Promotion gate** (verified + benchmarked + adapter + `ready_for_promotion`):
  implemented in `apps/api/planmyagents_api/registry/promotions.py`.
- **Router + executor**: implemented in `apps/api/planmyagents_api/agents/router.py`
  and `apps/api/planmyagents_api/workflows/`. Local execution only (no Inngest yet).
- **Embedding + keyword search** over the Postgres store: implemented in
  `apps/api/planmyagents_api/discovery/embeddings.py` and
  `apps/api/planmyagents_api/discovery/store.py` (`search_by_embedding`).
  The text composed for each candidate's embedding now includes the
  candidate's probed MCP tool names + descriptions and A2A skill names
  (`candidate_text_for_embedding` in `discovery/service.py`) so a generic
  capability tag like `general_research` no longer hides a tool literally
  named `send_email` from semantic search.
- **Generic protocol execution** (`apps/api/planmyagents_api/agents/protocol.py`):
  `GenericMcpAdapter` invokes JSON-RPC `tools/call` against discovered
  MCP HTTP servers (capability→tool auto-resolution or explicit
  `inputs.tool_name`). `GenericOpenApiAdapter` re-fetches the spec at
  execution time and builds the HTTP request with bearer / api-key-header /
  api-key-query auth (secrets via env vars). `GenericA2AAdapter` and
  `GenericAiAgentAdapter` return structured refusals because their wire
  formats aren't pinned down enough for a generic client. All four are
  hard-gated behind `PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION=true`,
  default OFF — an unsandboxed deploy cannot accidentally make live
  third-party calls.
- **Pre-plan discovery wrapper** (`plan_goal_with_pre_plan_discovery` in
  `web/planning.py`): when the constrained planner refuses on the first
  pass, the wrapper dispatches scouts against the goal-decomposer's per-
  sub-task `search_query` BEFORE the route handler does its own refusal-
  path discovery. Persisted candidates re-embed inline. The `/goal` route
  reads the pre-plan summary from `metadata['pre_plan_discovery']` and
  skips its own duplicate `_live_discovery` call when scouts already ran.
  Gated by `PLANMYAGENTS_PRE_PLAN_DISCOVERY` (default OFF) until the
  generic adapters above (Gap 4) make discovered providers immediately
  executable enough to justify the latency.
- **Post-goal MCP tool probe stage** (`_run_mcp_tool_probe_stage` in
  `discovery/post_goal_refresh.py`): the background refresh kicked off
  after every `/goal` now runs an `tools/list` probe pass against
  reachable MCP servers in parallel (8 workers default), capped at 20
  candidates / 5s per probe by default, persisting via `save_merge` so
  embeddings re-roll with the new tool data. Env-toggled
  (`PLANMYAGENTS_POST_GOAL_PROBE`, default ON in production).
- **Production FastAPI surface** with category browser routes and Pydantic
  models: implemented in `apps/api/planmyagents_api/web/app.py` and
  `apps/api/planmyagents_api/web/models.py`.
- **Next.js frontend** consuming the API: `apps/web/` (App Router, Tailwind,
  TypeScript) with category index, category detail, agent detail, and
  search pages.

What is *not* yet wired:

- Hosted Postgres + a deployed embedding model (we ship a deterministic-hash
  embedder so the system works with no external dependency by default).
- Inngest / Temporal orchestration; today the executor runs in-process.
- Clerk authentication and Stripe webhook integration (the architecture
  diagrams below describe the production stack; the local prototype does
  not require them and no Clerk/Inngest/Stripe-webhook code exists in the
  repo yet). The `StripePaymentAuthorization` adapter (Sprint 3a) DOES
  exist as a wrapper for the Order/PaymentIntent API — that is the
  agent-side use of Stripe, distinct from the planned Stripe-webhooks-as-
  workflow-trigger described in the diagrams.

Execution from the FastAPI `/goal` route is **wired** (Sprint 2 onwards):
the route accepts `execute=true` to run the plan in-process via
`WorkflowExecutor`, gated by the cost cap (Sprint 3a, S3a-7) and the
provider router's benchmark gate (`requires_benchmark_gate`). The
local stdlib demo at `scripts/serve_demo.py` is now a fallback path
for offline / no-Postgres environments, not the only execution surface.
First end-to-end live wrapper validation: Razorpay `payment_authorization`
graded 5/5 cases at 1.00 quality on 2026-05-15 (see
`packages/registry/agents.json` →`razorpay-payments.benchmark_status_evidence`).

---

## 2. Tech stack (locked-in for v0)

| Layer | Choice | Why |
|---|---|---|
| Backend language | **Python 3.12** | Best-in-class agent SDKs (Anthropic, OpenAI, LangGraph) |
| Web framework | **FastAPI** | Async-native, fast, type-safe with Pydantic |
| Workflow engine | **In-process execution + upkeep loop for v0** | Already implemented; migrate to Inngest / Temporal / queue workers when traffic justifies durability |
| Frontend | **Next.js 16 + React 19 + Tailwind** | Current app stack; Turbopack is used in dev and build scripts |
| Auth | **Clerk planned** | Auth config exists conceptually, but production auth surfaces are not wired yet |
| Database | **Postgres + pgvector; RDS for AWS launch; local Docker Postgres** | Production must use managed Postgres; local dev keeps parity through Docker |
| Cache + queues | **None required for v0** | Add Redis / SQS / RQ only when upkeep or scout queues outgrow one process |
| LLM (orchestrator + stitcher) | **Local Qwen for prototype; Claude Sonnet-class for hosted v0** | Start locally with Ollama/Qwen so planning is cheap and inspectable; keep hosted Claude/OpenAI/Gemini clients configurable for production quality |
| Payments | **Stripe PaymentIntents first; Connect later if needed** | v0 needs payment authorization and receipts, not legal escrow or connected-vendor payouts |
| Hosting (frontend) | **AWS EC2 behind CloudFront for v0** | Matches `docs/aws-hosting-guide.md`; Vercel remains an optional later simplification |
| Hosting (backend + workers) | **AWS EC2 + systemd / Docker for v0** | One low-cost host for web + API; split API/workers later |
| Observability | **CloudWatch basics first; Sentry/PostHog later** | Keep launch cost low and add product/error telemetry when public traffic starts |
| CI/CD | **GitHub Actions** | Free for public repos |

**Total monthly infra cost at zero customers on AWS v0:** roughly `$35-90/mo`
depending on RDS size and free-tier eligibility.
**At 100 paying customers / 10K records/day:** expect a larger split deployment
with worker queues, telemetry, and stronger database redundancy.

---

## 3. Repository structure

```
planmyagents/
├── README.md
├── BUSINESS_PLAN.md
├── PITCH_DECK.md
├── docs/                              # this folder
├── apps/
│   ├── web/                           # Next.js frontend
│   │   ├── app/                       # App Router pages
│   │   ├── components/                # shadcn-style
│   │   └── lib/
│   └── api/                           # FastAPI backend
│       ├── planmyagents_api/
│       │   ├── main.py
│       │   ├── routes/
│       │   │   ├── tasks.py
│       │   │   ├── webhooks.py
│       │   │   └── benchmark.py
│       │   ├── workflows/             # workflow execution + future Inngest functions
│       │   │   ├── executor.py
│       │   │   ├── scoring.py
│       │   │   ├── stitcher.py
│       │   │   └── models.py
│       │   ├── discovery/             # source connectors + candidate index
│       │   │   ├── sources/
│       │   │   ├── normalizer.py
│       │   │   ├── dedupe.py
│       │   │   └── index.py
│       │   ├── agents/                # adapters for promoted providers
│       │   │   ├── base.py            # AgentAdapter protocol
│       │   │   ├── apollo.py
│       │   │   ├── hunter.py
│       │   │   ├── clearbit.py
│       │   │   ├── firecrawl.py
│       │   │   └── ...
│       │   ├── models/                # SQLAlchemy / SQLModel
│       │   ├── llm/                   # Claude/OpenAI clients
│       │   ├── payments/              # Stripe PaymentIntent helpers
│       │   └── benchmark/             # scoring + ranking
│       ├── tests/
│       └── pyproject.toml
├── packages/
│   ├── registry/                      # active registry + discovered candidates
│   │   └── agents.json
│   └── benchmarks/                    # test cases per capability
│       ├── contact_enrichment/
│       ├── email_verification/
│       ├── web_scraping/
│       ├── semantic_search/
│       └── company_data/
├── infra/
│   ├── railway/
│   ├── vercel/
│   └── github-actions/
└── scripts/
    ├── seed_agents.py                 # populate registry from JSON
    └── run_benchmarks.py              # weekly cron
```

---

## 4. Database schema (Postgres)

The schema below is split into **(4.0) the discovery store as it ships
today** (deployed by `infra/postgres/init/001_planmyagents.sql`) and **(4.1+)
the planned runtime/payments/audit schema** (described first; partially
implemented — see callouts).

### 4.0 Discovery store schema — deployed today

Four physically separate tables, with the agentic / non-agentic split
enforced by Postgres:

```sql
-- Agentic candidates only: MCP servers, A2A agents, AI-native agents.
-- A CHECK constraint refuses any other provider_type at write time.
CREATE TABLE discovery_candidates (
  dedupe_key      TEXT PRIMARY KEY,
  provider_id     TEXT NOT NULL,
  display_name    TEXT NOT NULL,
  vendor          TEXT,
  provider_type   TEXT NOT NULL
                  CHECK (provider_type IN ('mcp_server', 'a2a_agent', 'ai_agent')),
  capabilities    JSONB NOT NULL,             -- list of {id, confidence, source}
  evidence_url    TEXT,
  evidence_quality TEXT,
  lifecycle_status TEXT NOT NULL DEFAULT 'discovered',
  route_status     TEXT NOT NULL DEFAULT 'will_fail',
  will_fail        BOOLEAN NOT NULL DEFAULT true,
  will_fail_reasons JSONB,
  required_env_vars JSONB,
  benchmark_status TEXT,
  embedding        VECTOR(384),                -- pgvector cosine on capability text
  raw_record       JSONB NOT NULL,
  first_seen_at    TIMESTAMPTZ DEFAULT now(),
  last_seen_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_discovery_candidates_provider ON discovery_candidates(provider_id);
CREATE INDEX idx_discovery_candidates_last_seen ON discovery_candidates(last_seen_at DESC);

-- "APIs without agents" — second-class supply.
-- Strictly api_provider / payment_provider rows.
-- Used by /open-mcp-opportunities and the /goal refusal payload.
CREATE TABLE apis_without_agents (
  dedupe_key      TEXT PRIMARY KEY,
  provider_id     TEXT NOT NULL,
  display_name    TEXT NOT NULL,
  vendor          TEXT,
  provider_type   TEXT NOT NULL
                  CHECK (provider_type IN ('api_provider', 'payment_provider')),
  openapi_url     TEXT,
  capabilities    JSONB NOT NULL,
  superseded_by_provider_id TEXT,              -- set when an agent wrapper appears
  raw_record      JSONB NOT NULL,
  first_seen_at   TIMESTAMPTZ DEFAULT now(),
  last_seen_at    TIMESTAMPTZ DEFAULT now()
);

-- Per-source / per-scout audit log: every discovery invocation.
-- Replaces the dead discovery_sources table from earlier sprints
-- (a backward-compatible view of the same name is provided for legacy SQL).
CREATE TABLE discovery_run_events (
  id                  BIGSERIAL PRIMARY KEY,
  source_name         TEXT NOT NULL,           -- 'mcp_registry_scout', 'static_seed', ...
  status              TEXT NOT NULL,           -- 'ok' | 'error' | 'timeout' | 'skipped'
  trigger             TEXT,                    -- 'goal_refusal' | 'upkeep' | 'manual' | ...
  candidates_returned INT NOT NULL DEFAULT 0,
  elapsed_ms          INT NOT NULL DEFAULT 0,
  error_class         TEXT,
  error_message       TEXT,
  details_json        JSONB,
  occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_run_events_source_date ON discovery_run_events(source_name, occurred_at DESC);

-- PII-safe capability demand log: every refused or unmet capability.
-- Goals are truncated, requesters are hashed.
CREATE TABLE capability_demand_events (
  id              BIGSERIAL PRIMARY KEY,
  capability_id   TEXT NOT NULL,
  goal_excerpt    TEXT,                        -- truncated to 240 chars
  requester_hash  TEXT,                        -- sha256 of requester id, never the raw id
  source          TEXT,                        -- 'goal_refusal' | 'planner_brainstorm' | ...
  details_json    JSONB,
  occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_demand_events_capability_date ON capability_demand_events(capability_id, occurred_at DESC);
```

Why the split (the hard rule in `AGENTS.md`):

- `discovery_candidates` is the **product index** — every row is something
  that, after promotion gates, could be routed to. The schema-level CHECK
  guarantees this.
- `apis_without_agents` is the **gap signal** — vendors with an API but no
  agent wrapper. It powers `/open-mcp-opportunities` directly.
- A `RoutingDiscoveryStore` facade
  (`apps/api/planmyagents_api/discovery/store.py`) routes by `provider_type` at
  save time, so existing callers see one surface. The same facade exists
  for the SQLite / JSON dev stores (no CHECK there; the facade enforces).
- The legacy `discovery_sources` name is preserved as a backward-compatible
  view over `discovery_run_events`, so any old SQL keeps working.

Callsites that need to know about the split:

| Callsite | What it reads |
|---|---|
| `POST /goal` (refusal payload) | `agentic_results` = `discovery_candidates`; `apis_without_agents` block = `apis_without_agents` |
| `GET /open-mcp-opportunities` | both stores, joined by capability + demand log |
| `GET /discovery/categories` and `/agents/{id}` | `discovery_candidates` only (these surfaces are agent-only by design) |

### 4.1 Planned runtime / payments / audit schema

Below is the planned production schema for the customer-facing workflow
runtime. **Implemented today:** `agents`, partial `tasks` (kept in
`packages/registry/agents.json` for the prototype). **Not yet
implemented:** `customers`, `sub_tasks`, `agent_calls` (we log into
`benchmark_runs` directly), `payment_authorizations`,
`audit_events`. These are documented because they shape the API + Stripe
integration when we move from prototype to hosted v0.

Use SQLModel or SQLAlchemy. Indices noted with `IDX`.

```sql
-- Customers (Clerk handles auth; this is our internal record)
CREATE TABLE customers (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clerk_user_id   TEXT UNIQUE NOT NULL,
  email           TEXT NOT NULL,
  org_name        TEXT,
  stripe_customer_id TEXT,
  plan            TEXT DEFAULT 'starter',  -- starter | growth | scale
  created_at      TIMESTAMPTZ DEFAULT now()
);

-- Agents (synced from packages/registry/agents.json on deploy)
CREATE TABLE agents (
  id              TEXT PRIMARY KEY,        -- 'apollo', 'firecrawl', ...
  display_name    TEXT NOT NULL,
  vendor          TEXT NOT NULL,
  capability      TEXT NOT NULL,           -- 'contact_enrichment', 'web_scraping', ...
  pricing_model   TEXT NOT NULL,           -- 'per_call', 'per_credit', 'per_page'
  unit_cost_usd   NUMERIC(10, 6) NOT NULL, -- our cost per unit
  rate_limit_rpm  INT,
  api_base_url    TEXT,
  is_active       BOOLEAN DEFAULT true,
  config_json     JSONB,                   -- auth method, special flags
  created_at      TIMESTAMPTZ DEFAULT now()
);

-- Tasks (top-level user requests)
CREATE TABLE tasks (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id     UUID NOT NULL REFERENCES customers(id),
  prompt          TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'planning',
                  -- planning | awaiting_approval | executing | stitching | completed | failed | denied
  plan_json       JSONB,
  cost_estimate_usd NUMERIC(10, 4),
  cost_actual_usd NUMERIC(10, 4),
  customer_charge_usd NUMERIC(10, 4),
  cost_ceiling_usd NUMERIC(10, 4),
  output_json     JSONB,
  denial_reason   TEXT,
  inngest_run_id  TEXT,                    -- for tracing
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_tasks_customer ON tasks(customer_id);
CREATE INDEX idx_tasks_status ON tasks(status);

-- Sub-tasks (planned units of work)
CREATE TABLE sub_tasks (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id         UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  ordinal         INT NOT NULL,
  description     TEXT NOT NULL,
  capability      TEXT NOT NULL,
  agent_id        TEXT NOT NULL REFERENCES agents(id),
  inputs_json     JSONB,
  status          TEXT DEFAULT 'pending',  -- pending | running | succeeded | failed | denied
  output_json     JSONB,
  cost_actual_usd NUMERIC(10, 6),
  attempts        INT DEFAULT 0,
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_sub_tasks_task ON sub_tasks(task_id);

-- Agent calls (one row per actual API hit; the unit of benchmark data)
CREATE TABLE agent_calls (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sub_task_id     UUID REFERENCES sub_tasks(id) ON DELETE SET NULL,
  agent_id        TEXT NOT NULL REFERENCES agents(id),
  capability      TEXT NOT NULL,
  request_json    JSONB,                   -- redacted of PII
  response_json   JSONB,                   -- redacted of PII
  status_code     INT,
  latency_ms      INT,
  cost_usd        NUMERIC(10, 6),
  succeeded       BOOLEAN,
  error_message   TEXT,
  idempotency_key TEXT UNIQUE,
  created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_agent_calls_agent_capability_date ON agent_calls(agent_id, capability, created_at DESC);

-- Benchmark runs (synthetic + real, used for ranking)
CREATE TABLE benchmark_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id        TEXT NOT NULL REFERENCES agents(id),
  capability      TEXT NOT NULL,
  test_case_id    TEXT NOT NULL,
  source          TEXT NOT NULL,           -- 'synthetic' | 'real_task'
  agent_call_id   UUID REFERENCES agent_calls(id),
  expected_json   JSONB,
  actual_json     JSONB,
  quality_score   NUMERIC(4, 3),           -- 0.000 to 1.000
  judge_model     TEXT,                    -- 'claude-sonnet-4.5'
  judge_reasoning TEXT,
  succeeded       BOOLEAN NOT NULL,
  latency_ms      INT,
  cost_usd        NUMERIC(10, 6),
  created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_benchmark_agent_capability_date ON benchmark_runs(agent_id, capability, created_at DESC);

-- Agent rankings (computed nightly)
CREATE TABLE agent_rankings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id        TEXT NOT NULL REFERENCES agents(id),
  capability      TEXT NOT NULL,
  computed_at     TIMESTAMPTZ NOT NULL,
  success_rate    NUMERIC(4, 3),
  avg_quality     NUMERIC(4, 3),
  p50_latency_ms  INT,
  p95_latency_ms  INT,
  avg_cost_usd    NUMERIC(10, 6),
  composite_score NUMERIC(4, 3),
  rank            INT,
  sample_size     INT,
  UNIQUE(agent_id, capability, computed_at)
);

-- Payment authorizations (one per customer per task)
CREATE TABLE payment_authorizations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id         UUID NOT NULL REFERENCES tasks(id),
  customer_id     UUID NOT NULL REFERENCES customers(id),
  authorized_amount_usd NUMERIC(10, 4) NOT NULL,
  captured_amount_usd NUMERIC(10, 4),
  refunded_amount_usd NUMERIC(10, 4),
  stripe_payment_intent_id TEXT,
  status          TEXT DEFAULT 'authorized', -- authorized | captured | partially_refunded | refunded | failed
  created_at      TIMESTAMPTZ DEFAULT now(),
  settled_at      TIMESTAMPTZ
);

-- Audit log (immutable; for dispute resolution)
CREATE TABLE audit_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id         UUID,
  event_type      TEXT NOT NULL,           -- 'task_created', 'plan_generated', 'plan_approved', 'sub_task_executed', etc.
  actor           TEXT,                    -- 'customer:<id>' | 'system' | 'agent:<id>'
  payload_json    JSONB,
  created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_audit_task ON audit_events(task_id, created_at);
```

---

## 5. Agent Adapter interface

Every agent integration implements this protocol. Keeps integrations swappable + testable.

```python
# apps/api/planmyagents_api/agents/base.py

from typing import Protocol, Any
from pydantic import BaseModel

class AgentRequest(BaseModel):
    capability: str          # e.g., 'contact_enrichment'
    inputs: dict[str, Any]   # capability-specific schema
    idempotency_key: str

class AgentResponse(BaseModel):
    succeeded: bool
    output: dict[str, Any] | None
    cost_usd: float
    latency_ms: int
    error: str | None
    raw_response: dict[str, Any]   # for debugging

class AgentAdapter(Protocol):
    agent_id: str
    capabilities: list[str]
    pricing_model: str

    async def execute(self, req: AgentRequest) -> AgentResponse: ...

    async def estimate_cost(self, req: AgentRequest) -> float: ...

    def health_check(self) -> bool: ...
```

Each agent file (`apps/api/planmyagents_api/agents/apollo.py`) implements this. ~150 lines per adapter.

---

## 6. Planned durable workflow execution model

Current v0 execution is in-process through `WorkflowExecutor`, with recurring
upkeep handled by scripts / timers. The Inngest design below is the future
durable workflow shape, not the current production requirement.

Three future durable functions chained:

### 6.1 `orchestrate_task`
```python
@inngest_client.create_function(
    fn_id="orchestrate_task",
    trigger=inngest.TriggerEvent(event="task/created"),
)
async def orchestrate_task(ctx, step):
    task_id = ctx.event.data["task_id"]

    plan = await step.run("generate_plan", lambda: generate_plan(task_id))

    if plan["unfeasible"]:
        await step.run("mark_denied", lambda: mark_task_denied(task_id, plan["reasons"]))
        return

    await step.run("save_plan", lambda: save_plan(task_id, plan))
    await step.run("notify_customer_for_approval", lambda: send_approval_email(task_id))

    approval = await step.wait_for_event(
        event="task/approved",
        timeout="48h",
        if_=f"async.data.task_id == '{task_id}'",
    )

    if approval is None:
        await step.run("expire_task", lambda: mark_task_expired(task_id))
        return

    await ctx.send_event(
        name="task/execute",
        data={"task_id": task_id},
    )
```

### 6.2 `execute_task` — fans out sub-tasks in parallel
```python
@inngest_client.create_function(
    fn_id="execute_task",
    trigger=inngest.TriggerEvent(event="task/execute"),
)
async def execute_task(ctx, step):
    task_id = ctx.event.data["task_id"]
    sub_tasks = await step.run("load_sub_tasks", lambda: load_sub_tasks(task_id))

    results = await step.parallel(
        tuple(
            step.invoke(
                f"exec_subtask_{st['id']}",
                function=execute_sub_task,
                data={"sub_task_id": st["id"]},
            )
            for st in sub_tasks
        )
    )

    await ctx.send_event(
        name="task/stitch",
        data={"task_id": task_id, "sub_task_results": results},
    )
```

### 6.3 `execute_sub_task` — single provider call with retries + cost logging
```python
@inngest_client.create_function(
    fn_id="execute_sub_task",
    retries=3,
    trigger=inngest.TriggerEvent(event="subtask/execute"),
)
async def execute_sub_task(ctx, step):
    sub_task = await step.run("load", lambda: load_sub_task(ctx.event.data["sub_task_id"]))
    agent = get_agent_adapter(sub_task["agent_id"])

    response = await step.run(
        "call_agent",
        lambda: agent.execute(AgentRequest(
            capability=sub_task["capability"],
            inputs=sub_task["inputs"],
            idempotency_key=sub_task["id"],
        )),
    )

    await step.run("log_call", lambda: log_agent_call(sub_task, agent, response))
    await step.run("log_benchmark", lambda: log_benchmark_run(sub_task, agent, response))

    return response.output
```

### 6.4 `stitch_results`
```python
@inngest_client.create_function(
    fn_id="stitch_results",
    trigger=inngest.TriggerEvent(event="task/stitch"),
)
async def stitch_results(ctx, step):
    task_id = ctx.event.data["task_id"]
    sub_task_results = ctx.event.data["sub_task_results"]

    final = await step.run("call_stitcher_llm", lambda: claude_stitch(task_id, sub_task_results))
    await step.run("save_output", lambda: save_task_output(task_id, final))
    await step.run("settle_payment", lambda: settle_payment_authorization(task_id))
    await step.run("notify_customer", lambda: send_completion_email(task_id))
```

---

## 7. Orchestrator prompt (the heart of the system)

This is the prompt that converts user intent into an executable plan. Iterate hard on this.

Initial implementation note: the local prototype should call a local Qwen model
through Ollama first, using this same constrained-registry prompt shape. The
model may decompose intent, but executable capabilities must still validate
against `packages/registry/agents.json`; any unknown capability becomes an
unsupported plan with explicit missing capabilities.

There is now a two-pass LLM layer before refusal discovery:

1. **Intent mapper.** Asks the local LLM to map the user's real-world goal to
   capability IDs from the source-derived discovery catalog, plus reusable
   constraints such as objects, quantities, dates, budgets, locations, delivery
   targets, and compliance/data-access requirements. The mapper is semantic, but
   the validator is deterministic: unknown capability IDs, low confidence,
   malformed JSON, or unavailable LLM output are rejected.
2. **Coverage auditor.** A second LLM pass takes the proposed mapping and the
   capability catalog and returns any catalog capabilities that should be
   promoted from optional/missing into required because they back an explicit
   user constraint (budget, date, destination, delivery, compliance, data
   access, identity, payment, freshness). The same deterministic validator runs
   on the auditor output. If the local LLM is unavailable, the auditor is
   skipped safely with a recorded note.

The combined output refines missing capabilities for refusal/discovery only. It
never makes a candidate executable. Production routing still requires verified
evidence + passed benchmark + reviewed adapter + `ready_for_promotion`.

```python
ORCHESTRATOR_SYSTEM_PROMPT = """
You are PlanMyAgents's planning agent. Your job is to convert a user task into an
executable plan using only the providers in the registry below.

REGISTRY (each provider's capability, pricing, provider type, and recent benchmark score):
{registry_with_rankings}

YOUR TASK:
1. Decompose the user request into atomic sub-tasks. Each sub-task must map
   to exactly one capability that one of the registered providers can perform.
2. For each sub-task, pick the BEST provider based on (a) capability match,
   (b) provider type priority, (c) recent benchmark score, and (d) cost.
   Prefer MCP/A2A/AI-agent providers when quality and reliability are comparable;
   use plain APIs as fallback providers.
3. Estimate total cost and latency. Add 20% buffer.
4. If ANY sub-task cannot be served by ANY active provider in the registry, mark the
   plan as `unfeasible: true` and explain SPECIFICALLY which capabilities
   are missing — do not invent capabilities or hallucinate that a provider
   can do something it can't.
5. Return strictly valid JSON matching the schema below.

OUTPUT SCHEMA:
{
  "unfeasible": boolean,
  "reasons": [string]   // populated if unfeasible
  "sub_tasks": [
    {
      "ordinal": int,
      "description": string,
      "capability": string,            // must be in registry
      "provider_id": string,           // must be an active provider in registry
      "inputs": object,                // capability-specific
      "estimated_cost_usd": number,
      "estimated_latency_ms": int,
      "depends_on": [int]              // ordinals of upstream sub-tasks
    }
  ],
  "total_estimated_cost_usd": number,
  "total_estimated_latency_ms": int,
  "confidence": number,                // 0-1, how confident you are in the plan
  "human_readable_summary": string     // 2-3 sentences explaining the plan
}

RULES:
- Never invent providers not in the registry.
- Never claim a capability a provider doesn't have.
- Never route to `discovered_agents`; they are demand signals only.
- If the user asks for something requiring real-time regulatory/legal
  certainty (e.g., "is this legal in country X"), mark unfeasible.
- If the user asks for verified personal data without consent context,
  flag in `reasons` and proceed with explicit disclosure.
- Prefer parallelism: if sub-tasks are independent, set `depends_on: []`.

USER TASK:
{user_task}
"""
```

### 7.1 Agent Discovery Index

Every unsupported plan is also a demand signal. If the planner identifies missing
capabilities that are not available in the active registry, PlanMyAgents should
search and enrich the Agent Discovery Index after refusing the current task.

This index is a first-class system, not a side effect. It is responsible for
finding candidate MCP servers, A2A agents, AI-native services, and API fallback
providers at large scale. See `docs/agent-discovery-index.md` for the detailed
design.

The pipeline searches in priority order:

1. MCP server registries and MCP catalogs
2. A2A Agent Cards and agent directories
3. AI-native agents/services with programmatic execution APIs
4. Plain API/tool providers only when no higher-priority agentic provider can satisfy the capability

It may use curated sources, web search, API documentation search, MCP server
registries, A2A Agent Cards, OpenAPI catalogs, vendor docs, and manual review.
Ad hoc runtime discovery should not grow `packages/registry/agents.json`. The
canonical registry should stay focused on promoted/routable providers plus any
small reviewed fixture snapshot. Discovery candidates belong in the discovery
store and are **not routable**.

Since the schema split (see §4.0), agentic candidates and API supply live in
**physically separate tables**:

- **`discovery_candidates`** holds only `mcp_server` / `a2a_agent` /
  `ai_agent` rows (Postgres CHECK constraint enforces this). They carry
  `lifecycle_status: discovered`, `route_status: will_fail`,
  `will_fail: true`, `will_fail_reasons`, and `required_env_vars`.
- **`apis_without_agents`** holds `api_provider` / `payment_provider`
  rows. These are *supply signal*, not routable agents. They carry
  `openapi_url`, capability list, and `superseded_by_provider_id`
  (populated once an agentic wrapper exists for the same capability).

`discovery_priority` (MCP/A2A/AI-agent ahead of plain APIs) is no longer
needed at query time — the two tables make the ranking implicit.

Promotion path:

`discovered` -> `candidate` -> `adapter_implemented` -> `benchmarked` -> `configured` -> active `providers`

Only active providers are available to the runtime router. A discovered provider
can be shown as "found for future onboarding", but the current user task must
still be refused until the provider is registered, configured, benchmarked, and
safe to execute.

Local heuristic utilities must not be registered as product providers. A
routable adapter should represent a real configured provider or callable
agent/protocol endpoint. When no such endpoint exists, the runtime should refuse
and report the missing capability or credential requirement. The router enforces
this at runtime: providers marked `runtime_mode: local`, `test`, `fixture`, or
`mock` are blocked unless `PLANMYAGENTS_ALLOW_DEV_PROVIDERS=true` is explicitly
set for local development.

Current prototype limitation:

- Implemented source connectors currently cover a static curated seed catalog,
  JSON candidate files/URLs, MCP catalog files/URLs, A2A Agent Card files/URLs,
  AI-agent directory files/URLs, web-doc manifest files/URLs, opt-in live
  GitHub repo/code research, Brave/Tavily web search, MCP registry search, A2A
  Agent Card search, OpenAPI/spec search, vendor-doc search, agent marketplace
  search, and configured live directory/spec URLs.
- Search is capability-overlap first, with query expansion and a text-match score
  against candidate metadata. Reusable intent-fit signals demote obvious
  mismatches such as internal database MCPs for public web/company research.
- The demo ranks end-to-end workflow options and currently treats only
  `mcp_server`, `a2a_agent`, and `ai_agent` as qualified candidates. APIs and
  payment providers are shown as rejected fallbacks.
- Payment candidates must declare compatibility with the selected booking
  provider before being presented as the best payment step in a booking flow.
- Hotel/lodging prompts infer `lodging_search`, `lodging_comparison`,
  `booking_execution`, and `payment_authorization` through the source-derived
  capability catalog.
- Debug output includes source coverage metadata so users can see whether the
  local runtime searched only configured sources or live research.
- Live research findings are stored as unverified, non-routable candidates until
  reviewed, adapted, benchmarked, and configured.
- Blocked demo requests run live discovery and persist non-routable findings to
  the discovery store during the first three-month learning window.
- There is no web-scale crawler or embedding search in the request path yet.
- Live connectors are bounded by API keys, result limits, timeouts, and explicit
  env flags. Missing keys or failed upstream calls return no candidates rather
  than fabricating providers.
- That is why a flight-booking query currently has A2A coverage for travel/fare
  and payment intent, but no qualified MCP/A2A/AI-agent candidate for booking
  execution. Duffel and other APIs are visible only as rejected fallbacks under
  the current demo scope.
- The current curated manifests ingest ~70 deduped non-routable candidates. The
  current local search path also reports freshness/staleness and
  promotion-readiness blockers, and local upkeep can persist to SQLite or
  Postgres. The next production milestone is promotion/benchmark scheduling and
  search scale, not more one-off API adapters.

### 7.2 Daily Registry Upkeep

PlanMyAgents should run a scheduled discovery/index upkeep job every day, independent of user
traffic. The job should:

1. Re-run searches for unresolved missing capabilities from recent failed tasks.
2. Check MCP registries, A2A Agent Cards, AI-agent catalogs, and vendor docs for
   newly available providers.
3. Refresh candidate metadata: auth requirements, docs URL, pricing, rate limits,
   changelog signals, and availability.
4. Re-run benchmarks for active providers and compare them against discovered
   candidates that have become implementable.
5. Open a review item when a candidate appears better than the current active
   provider mix.

Candidate verification and benchmark persistence are now explicit inputs to
promotion. Verification records evidence quality from agent cards, MCP metadata,
OpenAPI/docs, or source text. Benchmark stores persist scored runs. Promotion may
read these records, but runtime routing remains blocked until all gates pass.

The runtime path should never trust fresh web results directly. Web research and
source upkeep enrich the discovery store; only reviewed, configured, benchmarked
providers become available for execution through the registry.

The local repo includes `scripts/run_discovery_research.py` for broad
GitHub/URL-backed research, `scripts/run_discovery_scheduler.py` for a simple
interval runner, and `docker-compose.yml` for Postgres with `pgvector`.
Discovery scripts and the demo can use Postgres through
`PLANMYAGENTS_DISCOVERY_STORE_URL` or `--store postgresql://...`. The router still
loads promoted providers from `packages/registry/agents.json` by default, but can
merge promotion-ready DB candidates at startup when
`PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB=true`. A DB candidate is eligible
only after verified evidence, a reviewed adapter module, passed benchmarks,
`will_fail = false`, and `route_status = 'ready_for_promotion'`.
`scripts/generate_registry_from_db.py` can produce a registry artifact from those
promoted rows. Protocol-scale candidates default to generic MCP/A2A/OpenAPI/AI
adapters in `protocol_beta` mode, and the production router refuses those by
default. Production should replace the local scheduler with hosted jobs, Neon
Postgres, and keyword/vector indexes.

Live connector integration challenges:

- Search APIs add cost, quota limits, and variable latency, so request-path live
  discovery must stay bounded and eventually move to background jobs.
- GitHub code search needs a token for practical use and can return repos that
  mention MCP/A2A/OpenAPI without actually exposing a usable provider.
- MCP registries, A2A cards, OpenAPI specs, and marketplace listings do not share
  stable schemas, so normalization must preserve evidence and keep confidence
  low until verification succeeds.
- Fetching arbitrary docs/spec URLs creates SSRF, content-size, redirect, and
  privacy risks; production crawling needs allow/deny lists, network egress
  controls, redaction, and source trust scoring.
- Deduplication remains hard because the same provider can appear under a repo,
  package, vendor domain, marketplace slug, and docs domain.

### 7.3 Prototype Findings Incorporated

- The current demo uses local Qwen through Ollama for planning, intent mapping,
  and coverage auditing, with deterministic fallback when the local model is
  unavailable.
- Provider selection is automatic; the UI should never ask users to choose Hunter,
  Apollo, Amadeus, or any other provider directly.
- Failed plans produce non-routable discovery candidates rather than pretending
  execution is possible. The demo path is non-persistent; scheduled upkeep owns
  persistence.
- Deterministic planner fallback should use the source-derived capability
  catalog. New task domains should come from source ingestion and candidate
  metadata, not one-off planner branches for each user prompt.
- `will_fail` is an explicit state, not an error: it tells the product and user
  what must happen before a discovered provider can execute.
- The product identity is agentic-first. Plain APIs are useful fallback providers,
  but MCP/A2A/AI-agent providers should be discovered and benchmarked first.
- The local demo now emits structured request traces, but production still needs
  durable trace storage, redaction, audit events, and product analytics.
- Unsupported responses should not remain raw JSON in the production UI. The UI
  should show normalized intent, ranked workflow options, per-capability gaps,
  rejected fallback candidates, blocker reasons, and required promotion steps,
  with raw traces behind a debug view.
- Scenario/generalization fixtures are regression probes only. They should
  prevent prompt-specific hacks rather than becoming prompt-specific product
  logic.

---

## 8. Payment flow

### 8.1 Account model
- PlanMyAgents charges customers for workflow execution.
- In v0, PlanMyAgents pays providers through either:
  - PlanMyAgents-owned provider accounts (managed-provider mode), or
  - customer-provided provider API keys (BYOK mode).
- Stripe Connect is **not** required for v0. Use Connect later only if third-party agent vendors become connected sellers inside PlanMyAgents.

### 8.2 Money flow per task
```
1. Customer approves plan (cost ceiling: e.g. $15)
2. PlanMyAgents creates a Stripe PaymentIntent for the approved amount.
3. For v0, capture can happen upfront for small tasks, with refund of unused
   amount when appropriate. For larger tasks, use manual capture where Stripe
   authorization windows allow it.
4. Workflow executes; each provider call costs $X.
5. PlanMyAgents logs actual cost in agent_calls.cost_usd.
6. On task completion:
   - compute PlanMyAgents margin: customer charge - provider costs - Stripe fee
   - refund unused amount if product policy promises exact-cost settlement
   - mark task complete and issue itemized receipt
```

### 8.3 Cost ceiling enforcement
- Every task has `cost_ceiling_usd` (the customer-approved max)
- Orchestrator stops dispatching new sub-tasks once `sum(actual_costs) > 0.95 * ceiling`
- Remaining sub-tasks marked `denied: cost_ceiling_reached` in output
- Customer gets partial result + explicit explanation

### 8.4 Words to avoid until legal review
- Do not call the v0 flow "escrow" in customer-facing material.
- Do not promise funds are held in escrow unless a lawyer confirms the structure.
- Use "payment authorization," "cost ceiling," and "itemized settlement" instead.

---

## 9. Security & PII handling

### 9.1 PII categories handled
- Personal emails, phone numbers, LinkedIn URLs (from contact enrichment)
- Company names + URLs (not PII)
- Job titles + names (PII under GDPR)

### 9.2 Defense in depth
| Layer | Control |
|---|---|
| Transport | TLS 1.3 enforced everywhere |
| Storage | Postgres at-rest encryption (Neon default) |
| Logging | **Never log raw PII**. Use `redact_pii()` helper before any log/audit insert |
| LLM calls | Strip PII from prompts where possible. Never send PII to LLM judge for benchmarks (use synthetic test cases). |
| Customer data isolation | Every query scoped by `customer_id`; row-level security policies in Postgres |
| GDPR | Data Processing Agreement (DPA) with each customer. Right-to-delete API: `DELETE /api/v1/customer-data` purges all customer rows. |
| Sub-processor list | Public page listing every agent vendor + LLM provider used (required for GDPR transparency) |

### 9.3 PII redaction helper
```python
def redact_pii(s: str) -> str:
    s = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "<email>", s)
    s = re.sub(r"\+?\d{1,3}[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{4}", "<phone>", s)
    return s
```

---

## 10. Observability

| Tool | What we instrument |
|---|---|
| **Sentry** | All exceptions in API + workers; alert if error rate > 1% |
| **PostHog** | Frontend analytics (signup, task creation, plan approval, completion) |
| **Inngest dashboard** | Workflow runs, retries, failures, latency |
| **Custom dashboard** (Next.js admin) | Agent uptime, benchmark scores, daily MRR, cost-per-task histogram |

### 10.1 Key metrics to alert on
- Task failure rate > 5% (rolling 1hr)
- Agent call success rate < 90% for any agent (rolling 24hr)
- Cost overrun (actual > 1.2x estimate) on > 5% of tasks
- Stripe webhook failures
- Upkeep / worker failure rate > 0.1%

---

## 11. Deployment topology

```
GitHub repo (main branch)
    │
    │ manual v0 deploy or future GitHub Action
    ▼
AWS EC2 host
    │
    ├──▶ Next.js web service (systemd, port 3000)
    ├──▶ FastAPI service (systemd or Docker, port 8000)
    ├──▶ Hourly upkeep timer (discovery, verification, embeddings, benchmarks)
    └──▶ Nginx origin behind CloudFront / Route 53

External:
    RDS Postgres + pgvector — managed production state
    CloudFront + ACM        — TLS + edge
    Route 53                — planmyagents.com DNS
    SSM / Secrets Manager   — production secrets
    Stripe                  — adapter + future billing
    LLM providers           — Groq / OpenAI / Anthropic as configured
    Clerk                   — planned auth
```

---

## 12. Scaling considerations (for v1+, not v0)

| Bottleneck | When it bites | Mitigation |
|---|---|---|
| Discovery index size | Lakhs/millions of candidates | Store normalized candidates separately from routable providers; add vector + keyword indexes |
| Candidate noise/duplicates | Immediately | Deduplicate by domain, repo, endpoint, owner, and semantic similarity |
| Stale agent listings | Weeks | Daily recrawl and `last_seen_at` freshness scoring |
| Postgres rows (benchmark_runs grows fast) | ~10M rows | Partition by month; archive >12mo to cold storage |
| In-process upkeep or execution queue depth | Background work starts contending with API latency | Move upkeep to a worker service, then SQS / RQ / Inngest / Temporal |
| LLM API rate limits | ~1K rpm at Anthropic Pro | Tier 2/3 (apply via Anthropic) gives 5K+ rpm |
| Single provider rate limits | Varies by provider | Cache hot lookups when needed; request rate-limit increase from vendor |
| Stripe dispute rate | Should stay <0.1% | Clear receipts, refund policy, and cost-ceiling UX |
| Single-region latency for EU customers | After meaningful EU traffic | Add EU region and route Stripe + LLM by region |

---

## 12.5 Credibility track (parallel to the product track)

The product track described above (planner → discovery → verification → benchmark → safe routing → execution) widens *continuously* across every capability the LLM can name. Nothing in that track is gated on whether a leaderboard for capability `X` is publishable.

The credibility track answers a separate question: **for which capabilities are we willing to publish a leaderboard externally?** That decision is made by `apps/api/planmyagents_api/benchmark/credibility.py` and surfaced through the `credibility` field on `/leaderboards` and `/leaderboards/{capability}`.

### Ladder

| Status | Definition (default thresholds) |
|---|---|
| `synthetic_only` | All ranking rows use mock adapters. The page scores our own scoring code, not vendors. |
| `smoke_test` | At least one real ranking, but fewer than 3 real-adapter providers OR less than 30 real samples on any provider. |
| `developing` | Cross-vendor bar met; missing one publishability extra (freshness, holdout, breakdown). |
| `publishable` | ≥3 real-adapter providers, ≥30 real samples on at least one, last real run within 30 days. Defensible to publish externally. |

Thresholds are overridable via `PLANMYAGENTS_CREDIBILITY_MIN_REAL_PROVIDERS`, `PLANMYAGENTS_CREDIBILITY_MIN_SAMPLE_SIZE`, and `PLANMYAGENTS_CREDIBILITY_MAX_RUN_AGE_DAYS`.

### Stage-by-stage outputs (see `sprint.md` for the full plan)

| Stage | Engineering output | Non-engineering output |
|---|---|---|
| 0 | Audit script + classifier + UI banner + Make targets (`audit-ranking-sources`, `benchmark-credibility-report`) | First dated report at `reports/benchmark-credibility/{date}.md` |
| 1 | 3-4 real adapters + holdout module + per-difficulty UI columns + cost-per-correct field | One method note + honeypot domains we own |
| 2 | Each new adapter is a class; small UI tweaks | Cases + method notes for 3 more capabilities |
| 3 | Reliability dashboard + drift alerts + case rotation + RSS changelog | External red-team budget |
| 4 | Open-source the case format | arXiv-grade methodology note + analyst outreach |
| 5 | `/trust/{capability}` API + auth + billing + vendor profile pages | Pricing page + first 3 customer contracts |

### Why this is in the architecture doc

Treating the credibility track as separate-but-parallel keeps two engineering invariants visible:

1. **The product surface never narrows to credible capabilities.** The planner handles any goal; refusal is the only honest answer when no provider is verified.
2. **The leaderboard surface never *publicly* claims more than the credibility ladder allows.** A red `synthetic_only` banner is the correct UX when we don't yet have real adapter runs, even if the underlying ranking math works.

These two invariants are easy to violate accidentally (e.g. by removing the credibility banner because "the page looks better without it"). They are the reason this section exists.

---

## 13. v0 cut list (what's NOT in v0)

To ship in 12 weeks solo, the following are explicitly deferred or scoped:
- ❌ Custom agent registration (users adding their own agents) → v2
- ❌ Multi-tenant orgs / role-based access → v1.5
- ❌ x402 payment rail (Stripe-only in v0) → v0.5
- ✅ Narrow MCP/A2A discovery ingestion is in v0 scope, but the current prototype
  has only the static seed source
- ❌ Full protocol-native execution over arbitrary MCP/A2A providers → v1
- ❌ EU data residency → v1.5
- ❌ Workflow templates marketplace → v2
- ❌ Mobile app → never
- ❌ White-label / on-prem → enterprise tier (Year 2+)

If you find yourself building any of the above before v0 ships, stop.
