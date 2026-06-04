CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- discovery_run_events
--
-- Per-source / per-scout audit log. Replaces the previous unused
-- `discovery_sources` table (kept as a view below for backward compat).
-- One row per source invocation: when it ran, how long it took, how many
-- candidates it returned, and any error message. Used by the operator
-- dashboard to see which sources are slow / broken / starved.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_run_events (
  id BIGSERIAL PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  query TEXT NOT NULL DEFAULT '',
  searched_capabilities TEXT[] NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'ok',          -- 'ok' | 'error' | 'skipped' | 'timeout'
  error TEXT NOT NULL DEFAULT '',
  candidates_returned INTEGER NOT NULL DEFAULT 0,
  elapsed_ms INTEGER NOT NULL DEFAULT 0,
  trigger TEXT NOT NULL DEFAULT 'batch',      -- 'batch' | 'scout' | 'manual'
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_discovery_run_events_source_started
  ON discovery_run_events(source_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_discovery_run_events_status
  ON discovery_run_events(status, started_at DESC);

-- Backward-compat view so any existing operator query referencing the old
-- `discovery_sources` table still works while the application is migrated.
-- The view is read-only and intentionally projects only the legacy columns.
CREATE OR REPLACE VIEW discovery_sources AS
  SELECT
    id,
    source_id,
    source_type,
    query,
    searched_capabilities,
    status,
    error,
    started_at,
    completed_at
  FROM discovery_run_events;

-- ---------------------------------------------------------------------------
-- discovery_candidates  (AGENT-ONLY after this migration)
--
-- Holds rows where provider_type ∈ {mcp_server, a2a_agent, ai_agent}.
-- API providers (OpenAPI specs without an agent wrapper) and payment
-- providers move to `apis_without_agents`. Enforced by the CHECK
-- constraint below.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_candidates (
  id BIGSERIAL PRIMARY KEY,
  dedupe_key TEXT NOT NULL,
  provider_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  vendor TEXT NOT NULL DEFAULT '',
  vendor_url TEXT NOT NULL DEFAULT '',
  provider_type TEXT NOT NULL,
  lifecycle_status TEXT NOT NULL DEFAULT 'discovered',
  route_status TEXT NOT NULL DEFAULT 'will_fail',
  will_fail BOOLEAN NOT NULL DEFAULT TRUE,
  will_fail_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
  verification_status TEXT NOT NULL DEFAULT 'unverified',
  evidence_url TEXT NOT NULL DEFAULT '',
  adapter_module TEXT NOT NULL DEFAULT '',
  benchmark_status TEXT NOT NULL DEFAULT 'not_started',
  capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
  required_env_vars TEXT[] NOT NULL DEFAULT '{}',
  compatible_provider_ids TEXT[] NOT NULL DEFAULT '{}',
  source_id TEXT NOT NULL DEFAULT '',
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_candidate JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1536),
  UNIQUE (dedupe_key),
  UNIQUE (provider_id, provider_type, evidence_url)
);

-- ---------------------------------------------------------------------------
-- apis_without_agents
--
-- Vendors who expose an OpenAPI / REST spec but no MCP / A2A / AI-agent
-- wrapper exists. Distinct from `discovery_candidates` because they aren't
-- agent-callable today and shouldn't be ranked next to real agents.
--
-- Slimmer schema: no will_fail/benchmark/adapter_module columns (all of
-- those would be permanently false/empty for an unwrapped API).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS apis_without_agents (
  id BIGSERIAL PRIMARY KEY,
  dedupe_key TEXT NOT NULL,
  provider_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  vendor TEXT NOT NULL DEFAULT '',
  vendor_url TEXT NOT NULL DEFAULT '',
  provider_type TEXT NOT NULL,                -- 'api_provider' | 'payment_provider'
  openapi_url TEXT NOT NULL DEFAULT '',
  capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_id TEXT NOT NULL DEFAULT '',
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_record JSONB NOT NULL DEFAULT '{}'::jsonb,
  superseded_by_provider_id TEXT NOT NULL DEFAULT '',  -- set when an agent wrapping this API appears
  UNIQUE (dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_apis_without_agents_provider_type
  ON apis_without_agents(provider_type);

CREATE INDEX IF NOT EXISTS idx_apis_without_agents_capabilities
  ON apis_without_agents USING GIN (capabilities);

CREATE INDEX IF NOT EXISTS idx_apis_without_agents_superseded
  ON apis_without_agents(superseded_by_provider_id)
  WHERE superseded_by_provider_id <> '';

-- One-shot backfill: lift any pre-existing api_provider / payment_provider
-- rows out of discovery_candidates and into apis_without_agents. Idempotent
-- via ON CONFLICT.
INSERT INTO apis_without_agents (
  dedupe_key, provider_id, display_name, vendor, vendor_url,
  provider_type, openapi_url, capabilities, source_id,
  first_seen_at, last_seen_at, raw_record
)
SELECT
  dedupe_key, provider_id, display_name, vendor, vendor_url,
  provider_type, evidence_url, capabilities, source_id,
  first_seen_at, last_seen_at, raw_candidate
FROM discovery_candidates
WHERE provider_type IN ('api_provider', 'payment_provider')
ON CONFLICT (dedupe_key) DO NOTHING;

DELETE FROM discovery_candidates
WHERE provider_type IN ('api_provider', 'payment_provider');

-- Now safe to enforce the agent-only invariant. Drop first to make this
-- migration re-runnable.
ALTER TABLE discovery_candidates
  DROP CONSTRAINT IF EXISTS discovery_candidates_agentic_only_check;

ALTER TABLE discovery_candidates
  ADD CONSTRAINT discovery_candidates_agentic_only_check
  CHECK (provider_type IN ('mcp_server', 'a2a_agent', 'ai_agent'));

CREATE UNIQUE INDEX IF NOT EXISTS idx_discovery_candidates_dedupe_key
  ON discovery_candidates(dedupe_key);

CREATE INDEX IF NOT EXISTS idx_discovery_candidates_provider_type
  ON discovery_candidates(provider_type);

CREATE INDEX IF NOT EXISTS idx_discovery_candidates_lifecycle
  ON discovery_candidates(lifecycle_status, route_status);

CREATE INDEX IF NOT EXISTS idx_discovery_candidates_verification
  ON discovery_candidates(verification_status);

CREATE INDEX IF NOT EXISTS idx_discovery_candidates_capabilities
  ON discovery_candidates USING GIN (capabilities);

CREATE INDEX IF NOT EXISTS idx_discovery_candidates_embedding
  ON discovery_candidates USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- ---------------------------------------------------------------------------
-- capability_demand_events
--
-- Append-only log of every /goal refusal: one row per missing capability.
-- Powers the demand-side ranking on /open-mcp-opportunities. PII-safe by
-- construction: goal text truncated to 280 chars in the writer; requester
-- hashed (sha256 short-prefix) before insert.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS capability_demand_events (
  id BIGSERIAL PRIMARY KEY,
  capability_id TEXT NOT NULL,
  goal_excerpt TEXT NOT NULL,
  requester_hash TEXT NOT NULL DEFAULT '',
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  has_local_match BOOLEAN NOT NULL DEFAULT FALSE,
  has_apis_without_agents BOOLEAN NOT NULL DEFAULT FALSE,
  sub_task_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_capability_demand_events_capability
  ON capability_demand_events(capability_id, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_capability_demand_events_requested_at
  ON capability_demand_events(requested_at DESC);

-- ---------------------------------------------------------------------------
-- discovery_gap_events
--
-- Outcome-side companion to capability_demand_events. Each row records ONE
-- ``/goal`` request's post-discovery outcome for ONE missing capability:
-- how many scouts dispatched, how many returned zero, how many candidates
-- the judge evaluated, how many it accepted. ``judge_accepted = 0`` is the
-- brutally-honest "the world has not built this yet" signal that the
-- /discovery-gaps leaderboard ranks by.
--
-- The Python store (PostgresDiscoveryGapsStore.apply_schema) also creates
-- this table; defining it here means the docker-init pass produces a fully
-- consistent schema even before any Python boot has happened (needed by
-- the 002_evidence_health.sql view that aggregates this table on cold-boot).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_gap_events (
  id BIGSERIAL PRIMARY KEY,
  capability_id TEXT NOT NULL,
  goal_excerpt TEXT NOT NULL,
  goal_hash TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  scouts_dispatched INTEGER NOT NULL DEFAULT 0,
  scouts_returned_zero INTEGER NOT NULL DEFAULT 0,
  judge_evaluated INTEGER NOT NULL DEFAULT 0,
  judge_accepted INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_discovery_gap_events_capability
  ON discovery_gap_events(capability_id);
CREATE INDEX IF NOT EXISTS idx_discovery_gap_events_observed_at
  ON discovery_gap_events(observed_at);

CREATE TABLE IF NOT EXISTS benchmark_runs (
  id BIGSERIAL PRIMARY KEY,
  provider_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  test_case_id TEXT NOT NULL,
  difficulty TEXT NOT NULL DEFAULT '',
  score DOUBLE PRECISION NOT NULL,
  succeeded BOOLEAN NOT NULL,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
  output JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT NOT NULL DEFAULT '',
  run_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_provider_capability
  ON benchmark_runs(provider_id, capability, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_rankings (
  provider_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  sample_size INTEGER NOT NULL DEFAULT 0,
  success_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
  avg_quality_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  p50_latency_ms INTEGER NOT NULL DEFAULT 0,
  p95_latency_ms INTEGER NOT NULL DEFAULT 0,
  avg_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
  composite_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  last_run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  benchmark_status TEXT NOT NULL DEFAULT 'not_started',
  source TEXT NOT NULL DEFAULT 'synthetic',
  rank INTEGER NOT NULL DEFAULT 0,
  weights JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (provider_id, capability)
);

CREATE INDEX IF NOT EXISTS idx_agent_rankings_capability
  ON agent_rankings(capability, composite_score DESC);

CREATE TABLE IF NOT EXISTS verification_records (
  id BIGSERIAL PRIMARY KEY,
  provider_id TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence_url TEXT NOT NULL DEFAULT '',
  verified_capabilities TEXT[] NOT NULL DEFAULT '{}',
  blockers JSONB NOT NULL DEFAULT '[]'::jsonb,
  notes JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_verification_records_provider
  ON verification_records(provider_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- marketplace_store schema (Sprint 4 T1-B-1 — Pro tier + future vendor tables)
--
-- Lives in its OWN Postgres schema so blast-radius is isolated from
-- discovery / benchmark data and so the vendor-neutrality firewall (T5)
-- can statically lint that no ranking module imports from this schema.
-- See: planmyagents_api/marketplace_store/store.py for the canonical
-- source of truth; this init script just mirrors it for fresh local
-- Postgres bootstraps.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS marketplace_store;

CREATE TABLE IF NOT EXISTS marketplace_store.users (
  user_id        UUID PRIMARY KEY,
  clerk_user_id  TEXT UNIQUE NOT NULL,
  email          TEXT NOT NULL,
  plan           TEXT NOT NULL DEFAULT 'free',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (plan IN ('free', 'pro', 'enterprise'))
);

CREATE TABLE IF NOT EXISTS marketplace_store.workspaces (
  workspace_id   UUID PRIMARY KEY,
  name           TEXT NOT NULL,
  plan           TEXT NOT NULL DEFAULT 'free',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (plan IN ('free', 'pro', 'enterprise'))
);

CREATE TABLE IF NOT EXISTS marketplace_store.workspace_members (
  workspace_id   UUID NOT NULL REFERENCES marketplace_store.workspaces(workspace_id) ON DELETE CASCADE,
  user_id        UUID NOT NULL REFERENCES marketplace_store.users(user_id) ON DELETE CASCADE,
  role           TEXT NOT NULL,
  PRIMARY KEY (workspace_id, user_id),
  CHECK (role IN ('owner', 'admin', 'member'))
);

CREATE TABLE IF NOT EXISTS marketplace_store.default_workspaces (
  user_id        UUID PRIMARY KEY REFERENCES marketplace_store.users(user_id) ON DELETE CASCADE,
  workspace_id   UUID NOT NULL REFERENCES marketplace_store.workspaces(workspace_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS marketplace_store.saved_recipes (
  recipe_id      UUID PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES marketplace_store.workspaces(workspace_id) ON DELETE CASCADE,
  user_id        UUID NOT NULL REFERENCES marketplace_store.users(user_id) ON DELETE CASCADE,
  goal           TEXT NOT NULL,
  recipe_json    JSONB NOT NULL,
  format         TEXT NOT NULL,
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_marketplace_saved_recipes_user
  ON marketplace_store.saved_recipes(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_marketplace_saved_recipes_workspace
  ON marketplace_store.saved_recipes(workspace_id, created_at DESC);
