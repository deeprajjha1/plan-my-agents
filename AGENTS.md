# PlanMyAgents — Agent Guidance

> Repo-wide guidance for AI coding agents (Cursor, Claude, etc.).
> Equivalent to `CLAUDE.md` — same content; the modern conventional name is
> `AGENTS.md`.

## What this product is (one paragraph, structural)

PlanMyAgents is an **Agent Discovery Index + benchmarked routing layer**.
The **index** is the asset: candidates, capabilities, evidence, verification
records, benchmark runs, agent rankings, credibility verdicts. Everything
the user sees — `/goal`, `/categories`, `/agents/{id}`, `/search`,
`/leaderboards/{cap}` — is a **surface** on top of that one index. The
external pitch ("trust layer for the agent/tool economy") describes what
the index *aspires to be*; this doc describes what the index *is today* so
engineering decisions stay honest.

## The Index vs. the Surfaces (read before any large change)

```
SURFACES  →  /goal · /categories · /agents/{id} · /search · /leaderboards
             /open-mcp-opportunities
             (future) /export?target=n8n · /trust/{cap} API
                              │ all read from
                              ▼
INDEX     →  discovery_candidates (agentic only)
             apis_without_agents  (APIs with no agent wrapper)
             discovery_run_events (per-source audit log)
             capability_demand_events (PII-safe demand signals)
             + capabilities · evidence · verification_records
             + benchmark_runs · agent_rankings · credibility verdicts
                              ▲ ingested by
INGESTION →  MCP catalogs · A2A Agent Cards · AI-agent directories
             OpenAPI · vendor docs · Brave/Tavily/GitHub
             official MCP registry · APIs.guru · HN · vendor RSS (scouts)
```

The split between `discovery_candidates` and `apis_without_agents` is
physical (separate tables, postgres-side CHECK on `provider_type`) — see
hard rule 6 below.

Implications for engineering decisions:

1. **Work that grows or strengthens the index helps every surface.** Prefer
   it when in doubt. Examples: more sources, OpenAPI/docs extraction,
   embedding deployment, dedupe quality, freshness signals.
2. **Work that deepens one surface only helps that surface.** Fine to do,
   but be explicit about which surface you're deepening and why it's the
   right surface to invest in this sprint.
3. **No surface is allowed to claim more than the underlying index data
   warrants.** This is what the credibility classifier
   (`apps/api/planmyagents_api/benchmark/credibility.py`) enforces for the
   `/leaderboards` surface. Other surfaces should adopt similar honest-
   labelling patterns when they make claims.

## The two tracks of work

1. **Index track (always-on, breadth + depth)** — discovery sources, ingestion
   pipelines, dedupe, normalization, OpenAPI/docs extraction, embeddings,
   freshness, search. This is what makes the asset bigger and better.
2. **Surface tracks (one at a time)** — planner, leaderboards (with
   credibility classifier), search, future n8n export, future Trust API.
   Each surface has its own depth roadmap. The "Trust-Layer Credibility
   Roadmap" in `sprint.md` is the depth roadmap for **one specific surface
   (the leaderboard)**, not for the whole company.

When in doubt: a change that narrows the planner is almost always wrong;
a change that makes any surface claim more than the index data warrants
is always wrong.

## Where things live

| Concern | Path |
|---|---|
| FastAPI app + endpoints | `apps/api/planmyagents_api/web/app.py` |
| Pydantic boundary models | `apps/api/planmyagents_api/web/models.py` |
| Discovery agentic-candidate store + sources + service | `apps/api/planmyagents_api/discovery/store.py`, `apps/api/planmyagents_api/discovery/service.py` |
| Discovery routing facade (partitions by `provider_type` at save) | `apps/api/planmyagents_api/discovery/store.py` (`RoutingDiscoveryStore`) |
| APIs-without-agents store (the second-class table) | `apps/api/planmyagents_api/discovery/apis_without_agents_store.py` |
| Open MCP Opportunities computation (agent-coverage gap + demand) | `apps/api/planmyagents_api/discovery/open_mcp_opportunities.py` |
| Capability demand event log (PII-safe, append-only) | `apps/api/planmyagents_api/discovery/demand_store.py`, `apps/api/planmyagents_api/discovery/demand_recorder.py` |
| Discovery run audit log (per-source / per-scout timing + status) | `apps/api/planmyagents_api/discovery/run_log.py` |
| Discovery sources (one file per source) | `apps/api/planmyagents_api/discovery/sources/` |
| Request-time scout dispatcher | `apps/api/planmyagents_api/discovery/scouts.py` |
| LLM query expansion for scouts | `apps/api/planmyagents_api/discovery/query_expansion.py` |
| Planner + intent mapping + coverage audit | `apps/api/planmyagents_api/planner/` |
| Benchmark store, runner, scheduler | `apps/api/planmyagents_api/benchmark/` |
| Credibility classifier (do not bypass) | `apps/api/planmyagents_api/benchmark/credibility.py` |
| Provider adapters (HunterEmailVerifier etc.) | `apps/api/planmyagents_api/agents/` |
| CLI scripts (Make-driven) | `scripts/*.py` |
| Tests (stdlib `unittest`, 1,200+ cases — `make test-fast` for the 1,190 deterministic subset, `make test` for the full suite incl. live-LLM `/goal` integration) | `apps/api/tests/` |
| Next.js App Router frontend | `apps/web/src/app/` |
| Frontend API client + tag helpers | `apps/web/src/lib/api.ts`, `apps/web/src/lib/tags.ts` |
| Canonical Postgres migration | `infra/postgres/init/001_planmyagents.sql` |
| Live milestone tracker | `sprint.md` |
| Architecture doc (includes credibility track) | `docs/technical-architecture.md` |
| Discovery architecture (sources, normalisation, dedupe) | `docs/agent-discovery-index.md` |
| Operations runbook | `docs/operations.md` |
| Auto-generated credibility reports | `reports/benchmark-credibility/{date}.md` |

## Hard rules

1. **Never ship a leaderboard UI that claims more than `credibility.status`
   warrants.** If `status == "synthetic_only"` the page must show the red
   banner. Removing or downgrading the banner is a bug, not a polish.
2. **Never add a new provider adapter without going through discovery
   first.** Adapters are gated by verified evidence + benchmark + adapter +
   `ready_for_promotion`. Generic protocol adapters default to
   `protocol_beta` and are blocked from production routing.
3. **Never gate the planner on credibility.** Refusal is fine; pretending a
   capability does not exist because we lack benchmarks is not.
4. **Never use mocks/fixtures in production routing.** Mark them with
   `runtime_mode: local | test | fixture | mock`. The router refuses them
   unless `PLANMYAGENTS_ALLOW_DEV_PROVIDERS=true`.
5. **Never grow `packages/registry/agents.json` from ad-hoc demo prompts.**
   Demo discovery is non-persistent by design. Persistent growth comes from
   `make upkeep`, `make discovery-refresh`, and `scripts/run_*` jobs.
6. **Never put `api_provider` or `payment_provider` rows in
   `discovery_candidates`.** The Postgres schema enforces this with a
   `CHECK` constraint; the routing facade
   (`RoutingDiscoveryStore`) enforces it for SQLite / JSON dev stores.
   APIs without agents are second-class supply and live in
   `apis_without_agents`. The `/goal` refusal path, the
   `/open-mcp-opportunities` surface, and any new surface that mixes the
   two must read them from their respective stores — not from a single
   filtered query. If you find yourself adding a `WHERE provider_type IN
   (...)` filter on `discovery_candidates`, you are likely doing
   something wrong; load from the right store instead.

## Common workflows

### Run the full quality suite
```bash
make quality           # tests + compile + json-validate + ruff + smoke checks
```

### Refresh the credibility surface end-to-end
```bash
make audit-ranking-sources         # honest snapshot of source field
make benchmark-credibility-report  # writes reports/benchmark-credibility/{date}.md
```

### Backend tests (stdlib unittest, NOT pytest)
```bash
PYTHONPATH=apps/api .venv/bin/python -m unittest discover apps/api/tests -v
```

### Frontend
```bash
cd apps/web && npm run typecheck && npm run build
```

### Run the FastAPI dev server
```bash
PYTHONPATH=apps/api .venv/bin/uvicorn planmyagents_api.web.app:app \
  --host 127.0.0.1 --port 8000 --reload
```

### Run the Next.js prod server against it
```bash
cd apps/web && PLANMYAGENTS_API_BASE_URL=http://127.0.0.1:8000 \
  NEXT_PUBLIC_PLANMYAGENTS_API_BASE_URL=http://127.0.0.1:8000 \
  npm run start -- --port 3000
```

## Editing conventions

- Backend uses **Ruff** (config in `pyproject.toml`). Run `ruff check apps
  scripts --fix` before opening a change.
- Tests are stdlib `unittest`, not pytest. Don't add a pytest dependency
  silently.
- Pydantic models live exclusively in `apps/api/planmyagents_api/web/models.py`.
  Don't introduce parallel response shapes elsewhere.
- The Next.js app uses **typed routes** (`experimental.typedRoutes: true`).
  After adding a new route, run `npm run build` to regenerate
  `.next/types/`.
- Comments should explain *non-obvious intent / trade-offs*, not narrate
  what the code does.
- Frontend tag colours are centralised in `apps/web/src/lib/tags.ts`. Use
  `credibilityLabel` and `credibilityTagClass` for credibility pills; do
  not hard-code colour classes elsewhere.

## When the user asks for a strategic / product change

Strategic changes (positioning, monetisation, scope) belong in:

1. `sprint.md` — for milestone-level changes (the source of truth)
2. `BUSINESS_PLAN.md` — §8 for revenue-line shifts
3. `docs/technical-architecture.md` — for architectural shifts

Do not silently update only one of these and leave the others stale. The
"why" must live in the docs, not just the chat transcript.
