# Local Infrastructure

PlanMyAgents uses SQLite for the current local discovery store, but the
production-shaped path is Postgres with `pgvector`.

## Start

```bash
docker compose up -d postgres
```

Default connection:

```text
postgresql://planmyagents:planmyagents@localhost:55433/planmyagents
```

Use it for discovery persistence:

```bash
export PLANMYAGENTS_DISCOVERY_STORE_URL=postgresql://planmyagents:planmyagents@localhost:55433/planmyagents
python3 scripts/run_discovery_upkeep.py
python3 scripts/run_discovery_research.py --limit 25
```

Or pass the URL directly:

```bash
python3 scripts/run_discovery.py \
  --query "find email verification agents" \
  --store postgresql://planmyagents:planmyagents@localhost:55433/planmyagents
```

## Why Postgres + pgvector

- One durable store for the four discovery tables, verification, benchmark
  runs, and promotion state.
- `JSONB` keeps raw candidate metadata and capability payloads flexible while
  the schema is evolving.
- `pgvector` is enough for the first vector search index; a separate vector DB
  can be introduced later only if scale or recall requires it.

## Discovery schema deployed

`infra/postgres/init/001_planmyagents.sql` provisions four physically
separate discovery tables (the schema is also documented in
`docs/technical-architecture.md` §4.0 and `docs/agent-discovery-index.md` §3.5):

| Table | Holds |
|---|---|
| `discovery_candidates` | Agentic candidates only (`mcp_server` / `a2a_agent` / `ai_agent`). A `CHECK` constraint refuses any other `provider_type` at write time. |
| `apis_without_agents` | `api_provider` and `payment_provider` rows — vendors with an API but no agent wrapper. `superseded_by_provider_id` is set when an agent wrapper appears. |
| `discovery_run_events` | Per-source / per-scout audit log: status, candidates returned, elapsed ms, trigger. A backward-compatible `discovery_sources` view projects legacy columns for any old SQL. |
| `capability_demand_events` | PII-safe demand log: truncated goal, sha256-hashed requester id. Powers the demand side of `/open-mcp-opportunities`. |

If you bring an older local SQLite/JSON store forward, run:

```bash
make migrate-apis-without-agents
```

Idempotent. Moves any legacy `api_provider`/`payment_provider` rows out of
`discovery_candidates` into `apis_without_agents`. Postgres performs the
same migration inline inside `001_planmyagents.sql` before adding the CHECK.

## Routing Rule

Rows inserted by live discovery are non-routable. They should stay in
`lifecycle_status = 'discovered'` and `route_status = 'will_fail'` until a human
or promotion job verifies the endpoint, implements or configures the adapter,
and passes benchmarks.

`packages/registry/agents.json` is still the promoted-provider bootstrap used by
the router. Postgres is now the durable discovery/index/benchmark store; replacing
or generating the promoted registry from DB is a later migration step.

## Promotion Flow

Discovery rows become routable only through an explicit promotion step:

```bash
python3 scripts/promote_discovery_candidate.py \
  --store "$PLANMYAGENTS_DISCOVERY_STORE_URL" \
  --candidate-id google-a2a-currency-agent \
  --dry-run
```

For MCP/A2A/AI-agent/API candidates, the promotion command defaults to a generic
protocol adapter and marks the generated provider as `protocol_beta`, which is
blocked from production routing. When a real production adapter and benchmark
are ready, pass `--adapter-module module:Class` and remove `--dry-run`. To
generate a registry artifact from DB-promoted candidates:

```bash
python3 scripts/generate_registry_from_db.py \
  --store "$PLANMYAGENTS_DISCOVERY_STORE_URL" \
  --output .planmyagents_runs/generated-agents.json
```

To let the demo/router merge promoted DB candidates at startup:

```bash
export PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB=true
export PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL="$PLANMYAGENTS_DISCOVERY_STORE_URL"
```
