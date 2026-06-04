# PlanMyAgents — Low‑Level Design (LLD)

> **Version:** 2.0 (May 2026)
> **Scope:** Module APIs, database schemas, algorithms, error handling, concurrency, configuration, test strategy — across Phase 1 (engine), Phase 2 (marketplace), and Phase 3 (partner API).
> **Read this after:** [ARCHITECTURE.md](ARCHITECTURE.md) and [HLD.md](HLD.md).
> **Companion docs for Phase 2/3:** [marketplace-design.md](marketplace-design.md), [partnership-strategy.md](partnership-strategy.md).
> **Audience:** Engineers shipping code, reviewers approving PRs, hires onboarding to a module.
> **Conventions:** Python type hints are the source of truth for runtime shape. SQL DDL is the source of truth for storage. JSON Schema in `packages/registry/` is the source of truth for catalog data.

---

## Table of contents

1. [Module map](#1-module-map)
2. [API contracts](#2-api-contracts)
   - 2.1 `POST /goal`
   - 2.2 `GET /discovery/search`
   - 2.3 `GET /benchmark/{capability}`
   - 2.4 `GET /open-mcp-opportunities`
   - 2.5 `GET /recipe/export`
   - 2.6 **Vendor API (Phase 2)** — `/vendor/*`
   - 2.7 **Partner API (Phase 3)** — `/partner/v1/*`
3. [Database schema](#3-database-schema)
   - 3.1–3.9 Discovery + benchmark + spend tables
   - 3.10 **Marketplace tables (Phase 2)**
   - 3.11 **Partner tables (Phase 3)**
4. [Discovery sources](#4-discovery-sources)
5. [Normalizer and dedupe](#5-normalizer-and-dedupe)
6. [Embedding pipeline](#6-embedding-pipeline)
7. [Capability index](#7-capability-index)
8. [Goal decomposer](#8-goal-decomposer)
9. [Label reconciler](#9-label-reconciler)
10. [LLM candidate judge](#10-llm-candidate-judge)
11. [Trust ladder state machine](#11-trust-ladder-state-machine)
12. [Provider partition and ranker](#12-provider-partition-and-ranker)
13. [Recipe generator](#13-recipe-generator)
14. [Generic protocol adapters](#14-generic-protocol-adapters)
15. [Spend ledger](#15-spend-ledger)
16. [Demand and gap ledger](#16-demand-and-gap-ledger)
17. [Pre‑plan discovery and post‑goal refresh](#17-pre-plan-discovery-and-post-goal-refresh)
18. [Error handling matrix](#18-error-handling-matrix)
19. [Concurrency patterns](#19-concurrency-patterns)
20. [Configuration (env vars)](#20-configuration-env-vars)
21. [Test strategy](#21-test-strategy)
22. [Migration strategy](#22-migration-strategy)
23. [**Marketplace algorithms (Phase 2)**](#23-marketplace-algorithms-phase-2)
   - 23.1 Claim verification (DNS + email)
   - 23.2 Sponsored placement renderer (with firewall audit)
   - 23.3 Verified Benchmark scheduling + right‑of‑reply
   - 23.4 K‑anonymity for Demand‑Data API
   - 23.5 Lead routing
   - 23.6 **Optional execution fee — runtime hook + billing (tier 8)**
24. [**Partner API gateway (Phase 3)**](#24-partner-api-gateway-phase-3)
   - 24.1 Authentication + quota
   - 24.2 Per‑partner usage telemetry
   - 24.3 Feedback ingest pipeline
   - 24.4 SDK design

---

## 1. Module map

```
apps/api/planmyagents_api/
├── web/
│   ├── app.py                         # FastAPI app, route registration
│   ├── planning.py                    # /goal orchestration, _apply_decomposer, partition
│   ├── routes/                        # one router per surface
│   └── middleware.py                  # request id, structured logging
├── planner/
│   ├── goal.py                        # GoalPlan, PlannedSubTask dataclasses
│   ├── goal_decomposer.py             # decompose_goal, GoalDecomposition
│   ├── label_reconciler.py            # canonicalize_slugs
│   ├── label_store.py                 # CapabilityLabelStore (persist learned labels)
│   ├── workflow_options.py            # build_workflow_options
│   └── escalating_client.py           # N-tier LLM with fallback
├── discovery/
│   ├── service.py                     # candidate_text_for_embedding, search facade
│   ├── store/
│   │   ├── interface.py               # abstract DiscoveryStore
│   │   ├── postgres.py                # production backend
│   │   └── sqlite.py                  # dev backend
│   ├── dedupe.py                      # _best_verification_status, merge_candidates
│   ├── candidate_normalizer.py        # raw → DiscoveryCandidate
│   ├── candidate_judge.py             # LLM batch judge
│   ├── capability_index.py            # pgvector cosine search wrapper
│   ├── embedders.py                   # OpenAI / Ollama / DeterministicHash
│   ├── gaps.py                        # QUALIFIED_VERIFICATION_STATUSES, gap reports
│   ├── pre_plan_discovery.py          # pre-plan scout dispatch wrapper
│   ├── post_goal_refresh.py           # async refresh + MCP tool probe
│   ├── mcp_publication_quality.py     # MCP quality filter
│   └── sources/                       # 17+ scout modules
│       ├── smithery.py
│       ├── mcp_marketplace.py
│       ├── glama.py                   # Glama JSON API (~23K MCP servers)
│       ├── official_mcp_registry.py
│       ├── npm_packages.py            # npm registry, `mcp` tag (~47K pkgs)
│       ├── github_code_search.py
│       ├── github_awesome_lists.py    # curated `awesome-*` READMEs (token-gated)
│       ├── apis_guru.py
│       ├── brave_web.py
│       ├── tavily_web.py
│       ├── a2a_directory.py
│       ├── vendor_docs.py
│       ├── hacker_news.py
│       ├── moltbook.py
│       ├── agentguild.py
│       └── ai_directories.py
├── registry/
│   ├── capabilities.py                # catalog loader (26 routable)
│   ├── capability_descriptions.py     # descriptions loader
│   └── provider_router.py             # ProviderRouter, RouteDecision
├── agents/
│   ├── protocol.py                    # GenericProtocolAdapter family
│   ├── router.py                      # provider selection (routable caps)
│   ├── spend_ledger.py                # cost cap enforcement
│   └── audit.py                       # per-request audit
├── benchmark/
│   ├── methodology.py                 # per-capability scoring functions
│   ├── runner.py                      # benchmark execution
│   └── store.py                       # benchmark history persistence
├── marketplace/                       # Phase 2 — vendor portal
│   ├── vendor_signup.py               # vendor account creation (Clerk + 2FA)
│   ├── claim_verification.py          # DNS + domain-email ownership proof
│   ├── profile_editor.py              # claimed profile CRUD + moderation
│   ├── benchmark_request.py           # Verified Benchmark request + right-of-reply
│   ├── sponsored_placement.py         # sponsorship CRUD + firewall enforcement
│   ├── sponsored_renderer.py          # render with disclosure (called from L6)
│   ├── lead_routing.py                # opt-in lead notification + anonymized inbox
│   ├── demand_data_export.py          # K-anonymity aggregation + tier enforcement
│   ├── execution_fee_subscription.py  # tier-8 opt-in / opt-out / addendum / cap
│   ├── execution_fee_charger.py       # non-blocking runtime hook from adapter
│   ├── invoice_runner.py              # monthly close → Stripe invoice
│   ├── audit.py                       # vendor action audit → vendor_audit_events
│   ├── billing.py                     # Stripe portal handoff
│   ├── rbac.py                        # Owner / Editor / Billing / Read-only
│   ├── firewall_audit.py              # nightly + per-PR firewall integrity check (incl. rate cap)
│   └── store/                         # marketplace_store DB backend
│       ├── interface.py
│       └── postgres.py
└── partner/                           # Phase 3 — partner API
    ├── gateway.py                     # API key auth + per-partner rate limit + telemetry
    ├── recipe_export.py               # goal → partner-formatted recipe
    ├── search.py                      # partner-formatted search
    ├── recommend.py                   # one-shot best-agent recommendation
    ├── benchmark_widget.py            # embeddable benchmark comparison
    ├── feedback_ingest.py             # callback → benchmark_runs
    ├── demand_export.py               # demand signal for partner
    ├── audit.py                       # partner API call audit (sampled)
    ├── sdk_ts/                        # TypeScript SDK (npm package)
    ├── sdk_py/                        # Python SDK (PyPI package)
    └── store/                         # partner_store DB backend
        ├── interface.py
        └── postgres.py

packages/
├── registry/
│   ├── capabilities.json
│   ├── capability_descriptions.json
│   └── capability_descriptions.schema.json
└── discovery/sources/
    ├── curated_ai_agents.json
    ├── curated_mcp_servers.json
    └── ...

apps/
├── web/                               # Next.js user-facing frontend
└── vendor-web/                        # Next.js vendor portal (Phase 2; vendor.planmyagents.com)
infra/postgres/                        # migrations, init SQL
scripts/                               # ops scripts
```

---

## 2. API contracts

### 2.1 `POST /goal`

**Request:**
```json
{
  "goal": "Find 100 EU CTOs at Series A startups, verify emails, return CSV",
  "options": {
    "max_alternatives_per_capability": 3,
    "recipe_formats": ["claude_desktop_json", "n8n_json", "markdown"],
    "enable_protocol_execution": false,
    "credentials": {}
  }
}
```

**Response (200):**
```json
{
  "plan": {
    "status": "executable" | "unsupported" | "needs_human_input",
    "summary": "5-step workflow: company lookup → contact enrichment → email verification → CSV export",
    "sub_tasks": [
      {
        "capability": "company_data_lookup",
        "description": "Find Series A EU startups",
        "inputs": {
          "user_facing_step": "Locate companies",
          "search_query": "Series A startups Europe 2025-2026",
          "acceptance_criteria": ["company.country in EU", "funding_stage == 'Series A'"]
        }
      }
    ],
    "refusal_reasons": [],
    "missing_capabilities": []
  },
  "workflow_options": [
    {
      "score": 0.92,
      "estimated_total_cost_usd": 5.30,
      "estimated_time_minutes": 12,
      "steps": [
        {
          "capability": "company_data_lookup",
          "best_match": {
            "id": "apollo-mcp",
            "display_name": "Apollo MCP",
            "verification_status": "known_provider",
            "trust_badge": "known_provider",
            "benchmark_badge": "publishable",
            "cost_estimate_usd": 0.02,
            "setup_url": "https://docs.apollo.io/mcp"
          },
          "alternatives": [
            {"id": "pdl-mcp", "verification_status": "registered_in_directory", "cost_estimate_usd": 0.05}
          ]
        }
      ]
    }
  ],
  "recipes": [
    {"format": "claude_desktop_json", "download_url": "/recipe/export?goal_id=...&format=claude_desktop_json"},
    {"format": "n8n_json", "download_url": "/recipe/export?goal_id=...&format=n8n_json"}
  ],
  "discovery": {
    "gap_events": [],
    "scouts_dispatched": ["smithery", "mcp_marketplace"],
    "scouts_elapsed_ms": {"smithery": 412, "mcp_marketplace": 380}
  },
  "metadata": {
    "decomposer": {
      "tier_used": "primary",
      "provider_status": {
        "company_data_lookup": {"status": "executable", "provider": "apollo-mcp"},
        "contact_enrichment": {"status": "executable", "provider": "hunter-mcp"},
        "email_verification": {"status": "executable", "provider": "neverbounce-mcp"}
      },
      "executable_capabilities": ["company_data_lookup", "contact_enrichment", "email_verification"]
    }
  }
}
```

**Errors:**

| HTTP | Code | When |
|---|---|---|
| 400 | `invalid_goal` | Empty or non‑string goal |
| 422 | `goal_too_long` | Goal exceeds 4000 chars |
| 429 | `rate_limited` | IP / user / API key rate cap |
| 500 | `decomposer_unavailable` | All LLM tiers exhausted (rare) |
| 503 | `discovery_store_unavailable` | Postgres unreachable |

### 2.2 `GET /discovery/search`

```
GET /discovery/search?q=web+search&provider_type=mcp_server&min_trust=known_provider&limit=20
```

**Response (200):**
```json
{
  "query": "web search",
  "filters": {"provider_type": ["mcp_server"], "min_trust": "known_provider"},
  "candidates": [
    {
      "id": "perplexity-mcp",
      "display_name": "Perplexity MCP",
      "provider_type": "mcp_server",
      "verification_status": "capability_verified",
      "capabilities": ["web_search"],
      "score": 0.94,
      "benchmark_summary": {"capability": "web_search", "credibility": "publishable", "p95_latency_ms": 1200},
      "docs": {"setup_url": "https://...", "auth_method": "api_key"}
    }
  ],
  "total": 47,
  "page": 1,
  "has_more": true
}
```

### 2.3 `GET /benchmark/{capability}`

```
GET /benchmark/web_search?providers=perplexity-mcp,linkup-mcp&metrics=p95_latency_ms,success_rate,cost_per_query_usd
Authorization: Bearer pmpa_live_...
```

**Response (200):**
```json
{
  "capability": "web_search",
  "credibility": "publishable",
  "last_run_at": "2026-05-14T08:00:00Z",
  "sample_size": 250,
  "results": [
    {
      "provider_id": "perplexity-mcp",
      "metrics": {"p95_latency_ms": 1200, "success_rate": 0.97, "cost_per_query_usd": 0.005}
    }
  ],
  "methodology_url": "https://planmyagents.com/methodology/web_search"
}
```

### 2.4 `GET /open-mcp-opportunities`

```json
{
  "as_of": "2026-05-16T13:00:00Z",
  "opportunities": [
    {
      "capability": "vat_validation",
      "demand_count_30d": 142,
      "current_best_alternative": null,
      "api_providers_available": ["vies.ec.europa.eu"],
      "suggested_protocol_wraps": ["mcp_server"]
    }
  ]
}
```

### 2.5 `GET /recipe/export` (planned)

```
GET /recipe/export?goal_id=...&workflow_option=0&format=claude_desktop_json
```

Returns the recipe as `Content-Type: application/json` or `text/yaml` or `text/markdown` per format.

### 2.6 Vendor API (Phase 2) — `/vendor/*`

Available at `vendor.planmyagents.com` (separate Next.js + FastAPI deployment for blast‑radius isolation). All endpoints require Clerk JWT + 2FA. RBAC enforced server‑side.

#### 2.6.1 `POST /vendor/signup`

**Request:**
```json
{
  "vendor_name": "Apollo",
  "vendor_domain": "apollo.io",
  "owner_email": "deepraj@apollo.io"
}
```

**Response (201):**
```json
{
  "vendor_id": "vnd_01HXYZ...",
  "owner_user_id": "usr_01HXYZ...",
  "verification_required": true
}
```

#### 2.6.2 `POST /vendor/claim/{candidate_id}`

**Request:**
```json
{
  "method": "dns" | "email",
  "email": "deepraj@apollo.io"
}
```

**Response (202):**
```json
{
  "claim_id": "clm_01HXYZ...",
  "method": "dns",
  "instructions": {
    "record_type": "TXT",
    "record_host": "_planmyagents.apollo.io",
    "record_value": "planmyagents-verify=abc123def456"
  },
  "expires_at": "2026-05-30T00:00:00Z",
  "polling_interval_seconds": 300
}
```

#### 2.6.3 `POST /vendor/claim/{candidate_id}/verify`

Triggers an on‑demand DNS or email verification check. Idempotent; polled in the background every 5 minutes for 14 days regardless.

**Response (200, success):**
```json
{
  "status": "verified",
  "claim_tier": "claim_verified_dns",
  "claimed_profile_id": "cpf_01HXYZ..."
}
```

#### 2.6.4 `GET / PUT /vendor/profile/{candidate_id}`

Read or update the claimed profile fields.

**Request body (PUT):**
```json
{
  "display_name": "Apollo MCP",
  "description": "Apollo's MCP server for B2B contact data...",
  "vendor_url": "https://apollo.io",
  "docs_url": "https://docs.apollo.io/mcp",
  "setup_url": "https://docs.apollo.io/mcp/setup",
  "screenshots": ["https://cdn.apollo.io/mcp-screenshot-1.png", "..."],
  "categories_claimed": ["company_data_lookup", "contact_enrichment"]
}
```

**Server invariants (enforced):**
- `display_name`, `vendor_url`, `docs_url`, `setup_url` must resolve under verified `vendor_domain`
- `description` ≤ 4000 chars; runs through moderation pipeline
- `categories_claimed` accepted but each is independently re‑judged before display
- Fields `verification_status`, `is_sponsored`, `benchmark_score`, ranking position are **server‑set only**

#### 2.6.5 `POST /vendor/benchmark/request`

**Request:**
```json
{
  "candidate_id": "apollo-mcp",
  "capability": "company_data_lookup",
  "tier": "medium"
}
```

**Response (202):**
```json
{
  "certification_id": "bct_01HXYZ...",
  "methodology_url": "https://planmyagents.com/methodology/company_data_lookup",
  "agreement_required": true,
  "agreement_url": "/vendor/benchmark/bct_01HXYZ.../agreement",
  "invoice_url": "/vendor/benchmark/bct_01HXYZ.../invoice",
  "estimated_complete_at": "2026-06-15T00:00:00Z"
}
```

The vendor must accept the methodology agreement (which includes binding commitment to publication) and pay the invoice. Refusal to publish post‑completion voids the badge and downgrades `claim_tier` to `unclaimed` for 90 days.

#### 2.6.6 `POST /vendor/benchmark/{certification_id}/dispute`

**Request:**
```json
{
  "objection_type": "methodology_error",
  "summary": "Sample inputs use deprecated v1 endpoint",
  "supporting_evidence_url": "https://docs.apollo.io/changelog/v2-migration"
}
```

Triggers external arbitration. Re‑run only if methodology error confirmed.

#### 2.6.7 `POST / GET / PUT / DELETE /vendor/sponsor`

```json
POST /vendor/sponsor
{
  "capability": "web_search",
  "start_date": "2026-07-01",
  "end_date": "2026-09-30",
  "budget_usd": 50000
}
```

**Server invariants:**
- Only **one** sponsored slot per capability per quarter (server enforces, returns 409 on conflict)
- Sponsorship is auto‑activated only after invoice is paid
- Every CRUD writes to `vendor_audit_events`
- Sponsorship metadata is exposed publicly at `/disclosure` (machine‑readable JSON also)

#### 2.6.8 `GET /vendor/leads`

```json
{
  "leads": [
    {
      "lead_id": "ld_01HXYZ...",
      "capability": "contact_enrichment",
      "qualified_at": "2026-05-15T14:23:00Z",
      "user_consent_email_shared": false,
      "anonymized_summary": "Senior RevOps user, seeking contact enrichment at 100/day volume"
    }
  ],
  "next_cursor": "..."
}
```

#### 2.6.9 `POST /vendor/leads/{lead_id}/respond`

Replies via the anonymized inbox unless user has explicitly shared email.

#### 2.6.10 `GET /vendor/demand-data`

```
GET /vendor/demand-data?capability=vat_validation&window=30d
```

```json
{
  "capability": "vat_validation",
  "demand_count_30d": 142,
  "current_best_alternative": null,
  "api_providers_available": ["vies.ec.europa.eu"],
  "geo_distribution": {"US": 40, "EU": 50, "APAC": 10},
  "host_distribution": {"claude_desktop": 30, "cursor": 25, "n8n": 15, "other": 30}
}
```

**K‑anonymity enforced:** if any segment count < 100, segment is suppressed and `suppressed_segments: ["geo", "host"]` is returned instead.

#### 2.6.11 `POST /vendor/execution-fee/opt-in`

**Request:**
```json
{
  "rate_pct": 1.5,
  "addendum_signed_version": "v1.2026-05",
  "starts_at": "2026-07-01"
}
```

**Server invariants:**
- `rate_pct` must satisfy `0 < rate_pct <= 2.0`; otherwise 422
- `addendum_signed_version` must match current vendor‑neutrality addendum version on file
- Vendor must already have a `claimed_profile` row
- Endpoint writes to `marketplace_store.execution_fee_subscriptions` and `vendor_audit_events`

**Response (201):**
```json
{
  "subscription_id": "efs_01HXYZ...",
  "vendor_id": "vnd_01HXYZ...",
  "rate_pct": 1.5,
  "status": "active",
  "starts_at": "2026-07-01T00:00:00Z",
  "addendum_version": "v1.2026-05",
  "disclosure_url": "https://planmyagents.com/disclosure"
}
```

#### 2.6.12 `GET /vendor/execution-fee/status`

```json
{
  "subscription": {
    "subscription_id": "efs_01HXYZ...",
    "rate_pct": 1.5,
    "status": "active",
    "starts_at": "2026-07-01T00:00:00Z"
  },
  "current_period": {
    "month": "2026-08",
    "sandboxed_calls_routed": 12450,
    "total_vendor_revenue_attributed_usd": 622.50,
    "fee_amount_usd": 9.34,
    "invoice_status": "pending_period_close"
  }
}
```

#### 2.6.13 `POST /vendor/execution-fee/opt-out`

Immediate opt‑out; charges already accrued for the period still billed. Cooling‑off period of 90 days before vendor can opt in again (prevents on/off gaming).

#### 2.6.14 Vendor portal errors

| HTTP | Code | When |
|---|---|---|
| 400 | `claim_method_invalid` | Method must be `dns` or `email` |
| 403 | `domain_mismatch` | Email or URL not under verified `vendor_domain` |
| 404 | `candidate_not_found` | Unknown `candidate_id` |
| 409 | `slot_taken` | Capability already has active sponsorship in that window |
| 410 | `claim_expired` | 14‑day verification window passed without success |
| 422 | `agreement_not_signed` | Must sign methodology agreement before benchmark schedules |
| 422 | `execution_fee_rate_too_high` | Rate > 2.0% (P11 hard cap enforcement) |
| 422 | `execution_fee_addendum_outdated` | Vendor must sign current vendor‑neutrality addendum version |
| 429 | `vendor_rate_limited` | Per‑vendor rate cap |
| 451 | `firewall_violation_attempted` | Attempt to set ranking‑position field (P11 enforcement) |

### 2.7 Partner API (Phase 3) — `/partner/v1/*`

Available at `api.planmyagents.com/partner/v1/*` via dedicated gateway. Per‑partner API keys, scoped, with quota.

#### 2.7.1 Authentication

```http
POST /partner/v1/recipe HTTP/1.1
Host: api.planmyagents.com
Authorization: Bearer pma_partner_v1_n8n_<random>
X-Partner-ID: n8n
X-Partner-Version: 1.0.4
Content-Type: application/json
```

API keys are scoped by:
- **Endpoint allowlist** — each integration declares which endpoints it can call
- **Rate limit (QPS)** — per‑partner quota
- **Daily / monthly query budget** — per‑partner, billed per overage
- **Data scope** — e.g., which capabilities Demand‑Data is allowed for

#### 2.7.2 `POST /partner/v1/recipe`

**Request:**
```json
{
  "goal": "find 100 EU CTOs and verify their emails",
  "output_format": "n8n_json" | "cursor_prompt" | "claude_desktop_json" | "markdown" | "cli",
  "user_session_id": "anon_abc123",
  "user_consent_lead_routing": false,
  "host_context": {"host_name": "n8n", "host_version": "1.50.0"}
}
```

**Response (200):**
```json
{
  "recipe_id": "rcp_01HXYZ...",
  "format": "n8n_json",
  "content": "<base64-encoded JSON>",
  "estimated_total_cost_usd": 5.30,
  "estimated_time_minutes": 12,
  "steps": [
    {
      "step_id": "step_1",
      "capability": "company_data_lookup",
      "provider_id": "apollo-mcp",
      "provider_setup_url": "https://docs.apollo.io/mcp"
    }
  ],
  "disclosure": {
    "sponsored_steps": [],
    "methodology_url": "https://planmyagents.com/methodology"
  }
}
```

#### 2.7.3 `GET /partner/v1/search`

```
GET /partner/v1/search?q=web+search&host=n8n&limit=10
```

Same shape as `/discovery/search` but with `host` filter so the partner can request "agents that work with my host."

#### 2.7.4 `GET /partner/v1/recommend`

```
GET /partner/v1/recommend?capability=web_search&host=claude_desktop
```

One‑shot best‑agent recommendation for embed in host UI ("Suggested MCP for web search").

#### 2.7.5 `GET /partner/v1/benchmark/{capability}`

Benchmark widget data: top 5 providers, key metrics, methodology link, "Powered by PlanMyAgents" attribution required in host UI.

#### 2.7.6 `POST /partner/v1/feedback`

**Request:**
```json
{
  "recipe_id": "rcp_01HXYZ...",
  "step_id": "step_2",
  "capability": "contact_enrichment",
  "provider_id": "hunter-mcp",
  "result": {
    "status": "success" | "failure" | "timeout",
    "latency_ms": 1240,
    "cost_usd": 0.05,
    "error_code": null,
    "user_satisfaction": 5
  }
}
```

Ingested into `benchmark_runs` (origin = `partner_feedback`). This is how Partner API generates real‑world benchmark data without making us the executor.

#### 2.7.7 `GET /partner/v1/demand`

Same as `/vendor/demand-data` but partner‑scoped per integration agreement.

#### 2.7.8 Partner API errors

| HTTP | Code | When |
|---|---|---|
| 401 | `invalid_api_key` | Missing or bad key |
| 403 | `endpoint_not_allowed` | Endpoint not in partner scope |
| 403 | `data_scope_not_allowed` | Capability outside partner data scope |
| 429 | `partner_rate_limited` | Per‑partner QPS cap hit |
| 429 | `partner_quota_exceeded` | Monthly query budget exhausted |
| 503 | `engine_unavailable` | Underlying engine degraded |

---

## 3. Database schema

### 3.1 `discovery_candidates`

```sql
CREATE TABLE discovery_candidates (
    provider_type   TEXT NOT NULL,             -- mcp_server | a2a_agent | ai_agent
    id              TEXT NOT NULL,             -- canonical slug
    display_name    TEXT NOT NULL,
    vendor          TEXT NOT NULL,
    vendor_url      TEXT,
    capabilities    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{id, confidence}]
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    docs            JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_url    TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding       VECTOR(1536),              -- pgvector; null until embedded
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lifecycle_status TEXT NOT NULL DEFAULT 'active',
    sources         TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    probe_failure_count INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider_type, id)
);

CREATE INDEX idx_dc_verification ON discovery_candidates(verification_status);
CREATE INDEX idx_dc_lifecycle    ON discovery_candidates(lifecycle_status);
CREATE INDEX idx_dc_capabilities ON discovery_candidates USING GIN (capabilities);
CREATE INDEX idx_dc_embedding    ON discovery_candidates
   USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_dc_last_seen    ON discovery_candidates(last_seen_at);
```

### 3.2 `apis_without_agents`

```sql
CREATE TABLE apis_without_agents (
    provider_id     TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    vendor          TEXT NOT NULL,
    api_docs_url    TEXT,
    capabilities    JSONB NOT NULL DEFAULT '[]'::jsonb,
    source          TEXT NOT NULL,             -- e.g. "apis.guru"
    seen_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_awa_capabilities ON apis_without_agents USING GIN (capabilities);
```

### 3.3 `discovery_run_events` (append‑only)

```sql
CREATE TABLE discovery_run_events (
    event_id        BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL,
    scout           TEXT NOT NULL,
    capability      TEXT,
    query           TEXT,
    status          TEXT NOT NULL,             -- ok | timeout | error | skipped
    candidates_emitted INTEGER NOT NULL DEFAULT 0,
    elapsed_ms      INTEGER,
    error_message   TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_dre_run         ON discovery_run_events(run_id);
CREATE INDEX idx_dre_scout       ON discovery_run_events(scout, occurred_at DESC);
```

### 3.4 `capability_demand_events` (append‑only)

```sql
CREATE TABLE capability_demand_events (
    event_id        BIGSERIAL PRIMARY KEY,
    capability      TEXT NOT NULL,
    goal_hash       TEXT NOT NULL,             -- SHA-256 of normalized goal
    refusal_reason  TEXT,
    suggested_provider TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cde_cap_occurred ON capability_demand_events(capability, occurred_at DESC);
```

### 3.5 `capability_labels` (label reconciliation persistence)

```sql
CREATE TABLE capability_labels (
    slug            TEXT PRIMARY KEY,          -- LLM-coined slug, e.g., "email_dispatch"
    canonical_slug  TEXT,                       -- if canonicalized to a catalog cap
    description     TEXT,
    embedding       VECTOR(1536),
    seen_count      INTEGER NOT NULL DEFAULT 1,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cl_canonical ON capability_labels(canonical_slug);
CREATE INDEX idx_cl_embedding ON capability_labels USING ivfflat (embedding vector_cosine_ops);
```

### 3.6 `judge_decisions`

```sql
CREATE TABLE judge_decisions (
    candidate_id    TEXT NOT NULL,
    provider_type   TEXT NOT NULL,
    capability      TEXT NOT NULL,
    goal_hash       TEXT NOT NULL,
    decision        TEXT NOT NULL,             -- accept | reject
    confidence      REAL NOT NULL,
    reason          TEXT,
    judged_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (candidate_id, provider_type, capability, goal_hash)
);
```

### 3.7 `probe_results`

```sql
CREATE TABLE probe_results (
    candidate_id    TEXT NOT NULL,
    provider_type   TEXT NOT NULL,
    probe_kind      TEXT NOT NULL,             -- mcp_list_tools | a2a_skill_list
    status          TEXT NOT NULL,             -- ok | error | timeout
    payload         JSONB,                     -- tool list etc.
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (candidate_id, provider_type, probe_kind, occurred_at)
);
```

### 3.8 `benchmark_runs`

```sql
CREATE TABLE benchmark_runs (
    run_id          UUID PRIMARY KEY,
    candidate_id    TEXT NOT NULL,
    provider_type   TEXT NOT NULL,
    capability      TEXT NOT NULL,
    metric          TEXT NOT NULL,             -- success_rate | p95_latency_ms | cost_per_query_usd
    value           DOUBLE PRECISION NOT NULL,
    sample_size     INTEGER NOT NULL,
    credibility     TEXT NOT NULL,             -- publishable | developing | smoke_test | synthetic_only
    methodology_ver TEXT NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_br_cap_metric ON benchmark_runs(capability, metric, occurred_at DESC);
```

### 3.9 `spend_ledger`

```sql
CREATE TABLE spend_ledger (
    entry_id        BIGSERIAL PRIMARY KEY,
    request_id      UUID NOT NULL,
    user_id         TEXT,
    subject         TEXT NOT NULL,             -- llm_decomposer | llm_judge | embedding | protocol_execute
    provider        TEXT,                       -- groq | openai | anthropic | apollo | ...
    cost_usd        DOUBLE PRECISION NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_sl_request ON spend_ledger(request_id);
CREATE INDEX idx_sl_user_day ON spend_ledger(user_id, occurred_at);
```

### 3.10 Pro / user tables (planned, Q2)

```sql
CREATE TABLE users (
    user_id         UUID PRIMARY KEY,
    clerk_user_id   TEXT UNIQUE NOT NULL,
    email           TEXT NOT NULL,
    plan            TEXT NOT NULL DEFAULT 'free',  -- free | pro | enterprise
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE workspaces (
    workspace_id    UUID PRIMARY KEY,
    name            TEXT NOT NULL,
    plan            TEXT NOT NULL DEFAULT 'free',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE workspace_members (
    workspace_id    UUID REFERENCES workspaces(workspace_id),
    user_id         UUID REFERENCES users(user_id),
    role            TEXT NOT NULL,             -- owner | admin | member
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE saved_recipes (
    recipe_id       UUID PRIMARY KEY,
    workspace_id    UUID REFERENCES workspaces(workspace_id),
    user_id         UUID REFERENCES users(user_id),
    goal            TEXT NOT NULL,
    recipe_json     JSONB NOT NULL,
    format          TEXT NOT NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 3.11 Marketplace tables (Phase 2)

Lives in `marketplace_store` schema — physically separate Postgres schema for blast‑radius isolation.

```sql
CREATE SCHEMA marketplace_store;

-- Vendor account (one per organization)
CREATE TABLE marketplace_store.vendors (
    vendor_id         UUID PRIMARY KEY,
    vendor_name       TEXT NOT NULL,
    vendor_domain     TEXT NOT NULL UNIQUE,         -- verified-owned domain
    primary_owner_id  UUID NOT NULL,                 -- references users.user_id
    stripe_customer_id TEXT,
    plan_tier         TEXT NOT NULL DEFAULT 'free', -- free | pro | premium | enterprise
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    suspended_at      TIMESTAMPTZ,
    suspended_reason  TEXT
);

CREATE INDEX vendors_domain_idx ON marketplace_store.vendors(vendor_domain);

-- RBAC: who in the vendor org can do what
CREATE TABLE marketplace_store.vendor_members (
    vendor_id  UUID REFERENCES marketplace_store.vendors(vendor_id),
    user_id    UUID NOT NULL,
    role       TEXT NOT NULL,                       -- owner | editor | billing | readonly
    PRIMARY KEY (vendor_id, user_id)
);

-- A claim attempt with verification metadata
CREATE TABLE marketplace_store.claim_attempts (
    claim_id         UUID PRIMARY KEY,
    vendor_id        UUID REFERENCES marketplace_store.vendors(vendor_id),
    candidate_id     TEXT NOT NULL,                  -- references discovery_candidates.id
    method           TEXT NOT NULL,                  -- dns | email
    challenge_token  TEXT NOT NULL,
    challenge_email  TEXT,                           -- for email method
    status           TEXT NOT NULL DEFAULT 'pending',-- pending | verified | expired | revoked
    expires_at       TIMESTAMPTZ NOT NULL,
    verified_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX claim_attempts_candidate_idx ON marketplace_store.claim_attempts(candidate_id);
CREATE INDEX claim_attempts_pending_idx ON marketplace_store.claim_attempts(status) WHERE status = 'pending';

-- Successfully claimed listing — the vendor's edits live here as an overlay
CREATE TABLE marketplace_store.claimed_profiles (
    claimed_profile_id  UUID PRIMARY KEY,
    vendor_id           UUID REFERENCES marketplace_store.vendors(vendor_id),
    candidate_id        TEXT NOT NULL,
    claim_tier          TEXT NOT NULL,               -- claim_verified_dns | claim_verified_email
    display_name        TEXT,
    description         TEXT,                        -- markdown, ≤ 4000 chars
    vendor_url          TEXT,
    docs_url            TEXT,
    setup_url           TEXT,
    screenshots         JSONB DEFAULT '[]'::jsonb,
    categories_claimed  JSONB DEFAULT '[]'::jsonb,
    moderation_status   TEXT NOT NULL DEFAULT 'pending', -- pending | approved | flagged
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vendor_id, candidate_id)
);

CREATE INDEX claimed_profiles_candidate_idx ON marketplace_store.claimed_profiles(candidate_id);

-- Vendor-paid Verified Benchmark commitment
CREATE TABLE marketplace_store.benchmark_certifications (
    certification_id    UUID PRIMARY KEY,
    vendor_id           UUID REFERENCES marketplace_store.vendors(vendor_id),
    candidate_id        TEXT NOT NULL,
    capability          TEXT NOT NULL,
    tier                TEXT NOT NULL,               -- simple | medium | complex
    price_usd           NUMERIC(12,2) NOT NULL,
    methodology_version TEXT NOT NULL,
    agreement_signed_at TIMESTAMPTZ,
    invoice_paid_at     TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'awaiting_agreement',
                        -- awaiting_agreement | scheduled | running | awaiting_reply | published | voided
    scheduled_run_at    TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    published_at        TIMESTAMPTZ,
    voided_at           TIMESTAMPTZ,
    voided_reason       TEXT,
    benchmark_run_id    UUID,                        -- references benchmark_runs.run_id
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vendor_id, candidate_id, capability)
);

CREATE INDEX bench_certs_status_idx ON marketplace_store.benchmark_certifications(status);

-- Disclosed paid placement
CREATE TABLE marketplace_store.sponsored_placements (
    sponsorship_id     UUID PRIMARY KEY,
    vendor_id          UUID REFERENCES marketplace_store.vendors(vendor_id),
    candidate_id       TEXT NOT NULL,
    capability         TEXT NOT NULL,
    start_date         DATE NOT NULL,
    end_date           DATE NOT NULL,
    budget_usd         NUMERIC(12,2) NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending_payment',
                       -- pending_payment | active | ended | suspended
    invoice_paid_at    TIMESTAMPTZ,
    suspended_at       TIMESTAMPTZ,
    suspended_reason   TEXT,
    public_disclosure_url TEXT NOT NULL,             -- always live
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (start_date < end_date)
);

-- Server-enforced: max 1 active sponsorship per capability per window
CREATE UNIQUE INDEX sponsored_unique_active_per_cap
ON marketplace_store.sponsored_placements (capability, start_date, end_date)
WHERE status = 'active';

CREATE INDEX sponsored_status_idx ON marketplace_store.sponsored_placements(status);

-- Opt-in lead notification subscription
CREATE TABLE marketplace_store.lead_routing_subscriptions (
    subscription_id   UUID PRIMARY KEY,
    vendor_id         UUID REFERENCES marketplace_store.vendors(vendor_id),
    capability        TEXT NOT NULL,
    price_per_lead    NUMERIC(8,2) NOT NULL,
    monthly_cap_usd   NUMERIC(10,2),
    status            TEXT NOT NULL DEFAULT 'active',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vendor_id, capability)
);

-- One lead from a user goal (only if user opted in)
CREATE TABLE marketplace_store.leads (
    lead_id                 UUID PRIMARY KEY,
    capability              TEXT NOT NULL,
    matched_vendor_id       UUID REFERENCES marketplace_store.vendors(vendor_id),
    user_session_id         TEXT NOT NULL,
    user_consent_given_at   TIMESTAMPTZ NOT NULL,
    user_email_shared       BOOLEAN NOT NULL DEFAULT FALSE,
    anonymized_summary      TEXT,
    routed_at               TIMESTAMPTZ,
    responded_at            TIMESTAMPTZ,
    marked_spam_at          TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX leads_vendor_idx ON marketplace_store.leads(matched_vendor_id);

-- Demand-Data API subscription
CREATE TABLE marketplace_store.demand_data_subscriptions (
    subscription_id  UUID PRIMARY KEY,
    vendor_id        UUID REFERENCES marketplace_store.vendors(vendor_id),
    tier             TEXT NOT NULL,                  -- starter | pro | enterprise
    scopes           JSONB NOT NULL DEFAULT '[]'::jsonb, -- list of capability slugs
    price_yearly_usd NUMERIC(10,2) NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending_payment',
    starts_at        TIMESTAMPTZ NOT NULL,
    expires_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Append-only audit of every vendor action
CREATE TABLE marketplace_store.vendor_audit_events (
    event_id      BIGSERIAL PRIMARY KEY,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    vendor_id     UUID,
    user_id       UUID,
    action        TEXT NOT NULL,                     -- vendor_signup | claim_started | claim_verified_dns | profile_edit | benchmark_requested | sponsorship_created | sponsorship_suspended | lead_responded | demand_data_query | ...
    target_id     TEXT,
    metadata      JSONB
);

CREATE INDEX vendor_audit_vendor_idx ON marketplace_store.vendor_audit_events(vendor_id);
CREATE INDEX vendor_audit_action_idx ON marketplace_store.vendor_audit_events(action);

-- Firewall integrity audit (P11 enforcement)
CREATE TABLE marketplace_store.firewall_audit_runs (
    run_id              UUID PRIMARY KEY,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    breaches_found      INT NOT NULL DEFAULT 0,
    breach_details      JSONB,                       -- empty array if no breaches
    auditor             TEXT NOT NULL                -- automated | quarterly_internal | annual_external
);

-- Tier-8 optional execution fee opt-in
CREATE TABLE marketplace_store.execution_fee_subscriptions (
    subscription_id     UUID PRIMARY KEY,
    vendor_id           UUID REFERENCES marketplace_store.vendors(vendor_id),
    rate_pct            NUMERIC(4,2) NOT NULL,        -- e.g., 1.50
    addendum_version    TEXT NOT NULL,                -- e.g., 'v1.2026-05'
    addendum_signed_at  TIMESTAMPTZ NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active', -- active | opted_out | suspended
    starts_at           TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ,
    cooling_off_until   TIMESTAMPTZ,                  -- 90 days after opt-out
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (rate_pct > 0 AND rate_pct <= 2.0)         -- HARD CAP enforced at the table level (P11)
);

-- One row per opted-in vendor; vendor can only have one active subscription
CREATE UNIQUE INDEX execution_fee_one_active_per_vendor
ON marketplace_store.execution_fee_subscriptions (vendor_id)
WHERE status = 'active';

-- One charge per sandboxed call routed to an opted-in vendor
CREATE TABLE marketplace_store.execution_fee_charges (
    charge_id           UUID PRIMARY KEY,
    subscription_id     UUID REFERENCES marketplace_store.execution_fee_subscriptions(subscription_id),
    vendor_id           UUID REFERENCES marketplace_store.vendors(vendor_id),
    candidate_id        TEXT NOT NULL,
    capability          TEXT NOT NULL,
    sandboxed_call_id   UUID NOT NULL,                -- audit linkage
    vendor_revenue_attributed_usd NUMERIC(10,4) NOT NULL,
    rate_pct_applied    NUMERIC(4,2) NOT NULL,
    fee_amount_usd      NUMERIC(10,4) NOT NULL,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    invoice_period      TEXT NOT NULL,                -- 'YYYY-MM'
    invoice_status      TEXT NOT NULL DEFAULT 'pending_period_close', -- pending_period_close | invoiced | paid | written_off
    CHECK (rate_pct_applied > 0 AND rate_pct_applied <= 2.0)
);

CREATE INDEX exec_charges_vendor_period_idx
ON marketplace_store.execution_fee_charges (vendor_id, invoice_period);

CREATE INDEX exec_charges_invoice_status_idx
ON marketplace_store.execution_fee_charges (invoice_status)
WHERE invoice_status = 'pending_period_close';
```

### 3.12 Partner tables (Phase 3)

Lives in `partner_store` schema.

```sql
CREATE SCHEMA partner_store;

CREATE TABLE partner_store.partner_integrations (
    partner_id      TEXT PRIMARY KEY,                -- e.g., 'n8n', 'cursor', 'anthropic'
    display_name    TEXT NOT NULL,
    integration_tier TEXT NOT NULL,                  -- tier_1 | tier_2 | tier_3
    plan            TEXT NOT NULL,                   -- free | royalty | oem | enterprise
    monthly_query_budget INT,                        -- NULL = unlimited
    qps_limit       INT NOT NULL DEFAULT 10,
    endpoint_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    data_scopes     JSONB NOT NULL DEFAULT '[]'::jsonb,
    revenue_share_pct NUMERIC(5,2),                  -- for royalty deals
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    suspended_at    TIMESTAMPTZ
);

CREATE TABLE partner_store.partner_api_keys (
    key_id          UUID PRIMARY KEY,
    partner_id      TEXT REFERENCES partner_store.partner_integrations(partner_id),
    key_hash        TEXT NOT NULL UNIQUE,            -- bcrypt of the actual key
    key_prefix      TEXT NOT NULL,                   -- e.g., 'pma_partner_v1_n8n_'
    status          TEXT NOT NULL DEFAULT 'active',  -- active | rotated | revoked
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    rotated_at      TIMESTAMPTZ
);

CREATE INDEX partner_keys_hash_idx ON partner_store.partner_api_keys(key_hash);

-- Append-only audit of partner API calls (sampled for high-volume partners)
CREATE TABLE partner_store.partner_audit_events (
    event_id      BIGSERIAL PRIMARY KEY,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    partner_id    TEXT REFERENCES partner_store.partner_integrations(partner_id),
    endpoint      TEXT NOT NULL,
    request_hash  TEXT NOT NULL,                     -- SHA-256 of normalized request
    response_status INT NOT NULL,
    latency_ms    INT,
    sampled       BOOLEAN NOT NULL DEFAULT TRUE      -- false = full ingest; true = sampled
);

CREATE INDEX partner_audit_partner_idx ON partner_store.partner_audit_events(partner_id);

-- Per-partner usage roll-up (for billing + telemetry)
CREATE TABLE partner_store.partner_usage_daily (
    partner_id    TEXT REFERENCES partner_store.partner_integrations(partner_id),
    usage_date    DATE NOT NULL,
    endpoint      TEXT NOT NULL,
    request_count BIGINT NOT NULL DEFAULT 0,
    error_count   BIGINT NOT NULL DEFAULT 0,
    p50_ms        INT,
    p95_ms        INT,
    p99_ms        INT,
    PRIMARY KEY (partner_id, usage_date, endpoint)
);
```

---

## 4. Discovery sources

### 4.1 Common scout interface

```python
@runtime_checkable
class Scout(Protocol):
    name: str
    requires: list[str]            # env vars required to enable
    rate_limit: ScoutRateLimit

    async def fetch(self, query: ScoutQuery) -> list[RawCandidate]: ...
```

```python
@dataclass(frozen=True)
class ScoutQuery:
    capability_slug: str | None
    free_text: str
    max_results: int = 25
    deadline_s: float = 10.0
```

### 4.2 Dispatcher

```python
async def dispatch_scouts(
    *,
    query: ScoutQuery,
    enabled_scouts: list[Scout],
) -> list[ScoutResult]:
    """
    Run all enabled scouts concurrently with per-scout deadline.
    Never raises; returns per-scout success/failure record.
    """
    tasks = [
        asyncio.wait_for(scout.fetch(query), timeout=scout.rate_limit.deadline_s)
        for scout in enabled_scouts
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [_to_scout_result(s, r) for s, r in zip(enabled_scouts, results)]
```

### 4.3 Per‑scout notes

#### `smithery.py`
- Endpoint: `https://api.smithery.ai/v1/servers?search=...`
- Pagination: cursor‑based, max 100 per page
- Auth: `SMITHERY_API_KEY` (env var)
- Tags `verification_status="registered_in_directory"`
- Quality filter: `mcp_publication_quality.is_publishable(server)` drops drafts and broken entries

#### `mcp_marketplace.py`
- Similar to Smithery; different schema
- Tags `verification_status="registered_in_directory"`

#### `glama.py`
- Endpoint: `https://glama.ai/api/mcp/v1/servers` (public, no auth)
- Pagination: cursor‑based, default page size 100; ~23,000 servers indexed
- Tags `verification_status="registered_in_directory"`
- Corroboration policy: requires a non‑empty `repository.url` *or* at least one curator attribute (e.g. `attributes` list non‑empty) — drops un‑attributed Glama drafts
- Pre‑filter: `is_obvious_junk` drops fork/test/demo names before LLM inference

#### `official_mcp_registry.py`
- The Anthropic‑maintained registry
- Most trusted upstream source
- Tags `verification_status="registered_in_directory"`

#### `npm_packages.py`
- Endpoint: `https://registry.npmjs.org/-/v1/search?text=keywords:mcp` (public, no auth)
- Pagination: offset/size, max 250 per page; ~47,000 MCP‑tagged packages
- Tags `verification_status="registered_in_directory"`
- Corroboration policy: package must have `dependents > 0` *or* weekly `downloads > 50` *or* npm `score.final >= 0.40`; otherwise dropped before LLM inference
- Normalizes `metadata["npm_install_command"]` (matches MCP Marketplace convention) and `docs.install_steps[0]` to a runnable `npx -y <pkg>` invocation when the package's bin entry is present

#### `github_code_search.py`
- Searches GitHub for `mcp.json` or repo topics tagged `mcp-server`
- Heuristic for `vendor` extraction from repo owner
- Tags `verification_status="unverified"`; later upgraded by probe

#### `github_awesome_lists.py`
- Hand‑curated list of `awesome-*` repos (e.g. `punkpeye/awesome-mcp-servers`, `appcypher/awesome-mcp-servers`, `wong2/awesome-mcp-servers`)
- Fetches README via GitHub Contents API (requires `GITHUB_TOKEN`)
- Markdown parser extracts `[name](url)` link items; capped at `max_entries_per_list=60` to stay inside the dispatcher's 25 s budget
- Tags `verification_status="community_listed"` (the curator is a human, but the entries themselves are unprobed)
- Provider‑type hint from the list path (e.g. lists named `*mcp*` default to `mcp_server`, `*a2a*` to `a2a_agent`)
- Skip‑with‑warning when the configured list returns 404 (curator moved the README); does not crash the dispatcher

#### `apis_guru.py`
- Pulls APIs.guru directory (OpenAPI specs index)
- Emits `api_provider` candidates → saved to `apis_without_agents` table
- Powers `/open-mcp-opportunities`

#### `brave_web.py` / `tavily_web.py`
- Web search APIs scoped to known agent/MCP terminology
- Heuristic extraction of vendor + provider name
- Tags `verification_status="unverified"`

#### `a2a_directory.py`
- A2A vendor directories + agent‑card discovery
- Tags `verification_status="known_provider"` when card hosted at vendor domain

#### `vendor_docs.py`
- Hand‑curated list of vendor docs URLs (e.g. Apollo, Hunter, Perplexity)
- Scraper extracts agent / capability claims
- Tags `verification_status="known_provider"`

#### `hacker_news.py`
- HN Algolia search for "show HN MCP" / "launch agent"
- Extracts URL + vendor
- Tags `verification_status="unverified"`

#### `moltbook.py` / `agentguild.py`
- Community curators
- Tags `verification_status="community_listed"`
- Corroboration filter: at least one other source must list to survive into store

#### `curated_*.json`
- Hand‑maintained seed manifests in `packages/discovery/sources/`
- Tags `verification_status="known_provider"`
- Bootstrap source for v0 launch and offline development

---

## 5. Normalizer and dedupe

### 5.1 `candidate_normalizer.py`

```python
def normalize(
    raw: dict,
    *,
    source: str,
    default_provider_type: str,
    default_verification: str = "unverified",
) -> DiscoveryCandidate:
    """
    Coerce a source-specific raw dict into the canonical DiscoveryCandidate.
    Raises NormalizerError on missing required fields.
    """
    cid = _canonical_id(raw)
    return DiscoveryCandidate(
        id=cid,
        provider_type=raw.get("provider_type", default_provider_type),
        display_name=raw["display_name"] or cid,
        vendor=_extract_vendor(raw),
        vendor_url=raw.get("vendor_url"),
        capabilities=_normalize_capabilities(raw.get("capabilities", [])),
        verification_status=raw.get("verification_status", default_verification),
        docs=_normalize_docs(raw.get("docs", {})),
        evidence_url=raw.get("evidence_url"),
        metadata=raw.get("metadata", {}),
        embedding=None,
        last_seen_at=datetime.utcnow(),
        lifecycle_status="active",
        sources=[source],
    )
```

### 5.2 Dedupe algorithm (`dedupe.py`)

**Input:** incoming candidate `X`, existing store row `Y` (or `None`).

**Output:** merged candidate `Z`.

```python
def merge_candidates(x: DiscoveryCandidate, y: DiscoveryCandidate | None) -> DiscoveryCandidate:
    if y is None:
        return x
    return replace(
        y,
        capabilities=_union_capabilities(y.capabilities, x.capabilities),
        verification_status=_best_verification_status(y.verification_status, x.verification_status),
        sources=list(set(y.sources) | set(x.sources)),
        last_seen_at=max(y.last_seen_at, x.last_seen_at),
        docs={**y.docs, **x.docs},
        metadata={**y.metadata, **x.metadata},
        display_name=y.display_name or x.display_name,
        vendor=y.vendor or x.vendor,
        vendor_url=y.vendor_url or x.vendor_url,
        evidence_url=y.evidence_url or x.evidence_url,
        lifecycle_status=y.lifecycle_status,  # never auto-resurrect rejected
    )


_VERIFICATION_PRIORITY = {
    "capability_verified": 5,
    "registered_in_directory": 4,
    "known_provider": 3,
    "community_listed": 2,
    "unverified_example": 1,
    "unverified": 0,
}


def _best_verification_status(existing: str, incoming: str) -> str:
    return existing if (
        _VERIFICATION_PRIORITY.get(existing, 0)
        >= _VERIFICATION_PRIORITY.get(incoming, 0)
    ) else incoming
```

**Complexity:** O(|capabilities_x| + |capabilities_y|) per merge. Bulk merge in `save_merge` is O(N × M) for N incoming, M existing matches; we batch by canonical id to keep each merge constant‑time.

### 5.3 Canonical id derivation

```python
def _canonical_id(raw: dict) -> str:
    # Examples:
    #   "smithery:Forage Shopping" -> "forage-shopping"
    #   GitHub repo "apollo-graphql/apollo-mcp" -> "apollo-graphql-apollo-mcp"
    base = raw.get("id") or raw.get("slug") or raw.get("name") or ""
    return slugify(base)
```

Collisions across vendors are deliberately allowed within `provider_type` — the primary key `(provider_type, id)` disambiguates and `vendor_url` keeps provenance.

---

## 6. Embedding pipeline

### 6.1 Embedder interface

```python
class Embedder(Protocol):
    dimension: int

    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
```

### 6.2 Backends

```python
class OpenAIEmbedder:
    dimension = 1536
    def __init__(self, *, model: str = "text-embedding-3-small", api_key: str): ...
    def embed(self, text: str) -> list[float]: ...   # POST embeddings; batched 16 max
```

```python
class OllamaEmbedder:
    dimension = 768
    def __init__(self, *, base_url: str = "http://127.0.0.1:11434", model: str = "nomic-embed-text"): ...
```

```python
class DeterministicHashEmbedder:
    """Hermetic fallback. NEVER use in production for actual semantic search."""
    dimension = 256
    def embed(self, text: str) -> list[float]:
        # Hash text into deterministic 256-d unit vector. Used in tests + offline only.
        ...
```

### 6.3 `CachedEmbedder`

```python
class CachedEmbedder:
    def __init__(self, inner: Embedder, *, max_entries: int = 50_000): ...
    def embed(self, text: str) -> list[float]:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]
        vec = self.inner.embed(text)
        self._cache[key] = vec
        return vec
```

### 6.4 Tool‑aware candidate text builder (`service.py`)

```python
_MAX_TOOL_NAME_CHARS = 60
_MAX_TOOL_DESCRIPTION_CHARS = 200
_MAX_TOOLS_EMBEDDED = 12
_MAX_CAPABILITY_NOTE_CHARS = 200


def candidate_text_for_embedding(c: DiscoveryCandidate) -> str:
    parts = [
        c.display_name,
        f"vendor: {c.vendor}",
        f"type: {c.provider_type}",
        " ".join(cap["id"] for cap in c.capabilities[:20]),
    ]
    parts.extend(_tool_surface_parts(c.metadata.get("tools", []), c.metadata.get("skills", [])))
    return " | ".join(p for p in parts if p)
```

The tool/skill surface is critical: vendor descriptions are vague; tool names/descriptions reveal what the agent actually does. Including them in the embed text dramatically improves search recall for niche capabilities.

### 6.5 Embed‑on‑write vs embed‑on‑demand

Embed‑on‑write (the chosen pattern) keeps search read‑path cheap. The write path may incur per‑candidate embedding cost; cached embedder collapses retries.

---

## 7. Capability index

### 7.1 Cosine search (`capability_index.py`)

```python
class CapabilityIndex:
    def __init__(self, store: DiscoveryStore, embedder: Embedder): ...

    def search(
        self,
        *,
        capability_slug: str | None = None,
        free_text: str | None = None,
        provider_type: list[str] | None = None,
        min_verification: str = "unverified",
        top_k: int = 20,
    ) -> list[ScoredCandidate]:
        query_vec = self._build_query_vector(capability_slug, free_text)
        return self.store.vector_search(
            query_vec=query_vec,
            provider_type=provider_type,
            min_verification=min_verification,
            top_k=top_k,
        )
```

### 7.2 SQL (Postgres + pgvector)

```sql
SELECT
    provider_type, id, display_name, vendor, capabilities,
    verification_status, docs, metadata,
    1 - (embedding <=> $1::vector) AS cosine_similarity
FROM discovery_candidates
WHERE
    lifecycle_status = 'active'
    AND ($2::text[] IS NULL OR provider_type = ANY($2))
    AND _verification_priority(verification_status) >= _verification_priority($3)
    AND embedding IS NOT NULL
ORDER BY embedding <=> $1::vector
LIMIT $4;
```

`<=>` is pgvector's cosine distance operator; we convert to similarity for human‑readable scoring.

### 7.3 Hybrid scoring

```python
score = (
      0.55 * cosine_similarity
    + 0.20 * verification_tier_weight
    + 0.15 * benchmark_score_normalized
    + 0.05 * freshness_normalized
    + 0.05 * (1 - cost_normalized)
)
```

Weightings are configurable per surface (e.g., `/search` weights cosine higher; recipe recommendation weights verification + benchmark higher).

---

## 8. Goal decomposer

### 8.1 Interface

```python
@dataclass
class DecomposedSubTask:
    user_facing_step: str
    capability_slug: str
    search_query: str
    acceptance_criteria: list[str]


@dataclass
class GoalDecomposition:
    intent_summary: str
    sub_tasks: list[DecomposedSubTask]
    suggested_capability_ids: list[str]
    catalog_reused_capabilities: list[str]
    new_capabilities: list[str]
    confidence: float


def decompose_goal(
    *,
    goal: str,
    catalog: CapabilityCatalog,
    catalog_descriptions: dict[str, CapabilityDescription] | None = None,
    chat_client: ChatClient,
) -> GoalDecomposition: ...
```

### 8.2 Prompt structure

**System prompt (excerpt):**
```
You are an agent workflow planner. Given a user's goal, break it into
ordered sub-tasks. For each sub-task emit a capability slug.

STRONGLY prefer reusing capability slugs from `router_supported` below
over coining new ones. Reuse when the meaning is even a loose match;
only coin a new slug when no router_supported capability fits.

Return JSON only. Schema:
{
  "intent_summary": str,
  "sub_tasks": [{user_facing_step, capability_slug, search_query, acceptance_criteria: [str]}],
  "suggested_capability_ids": [str],
  "confidence": float
}
```

**User payload (built by `_build_messages`):**
```json
{
  "goal": "...",
  "catalog_hint": {
    "router_supported": [
      {"id": "web_search", "description": "Find pages on the public web", "examples": ["weather today", "Crunchbase profile"]},
      {"id": "payment_authorization", "description": "Charge a card / authorize payment", "examples": ["pay $50 to vendor"]}
    ],
    "other_known_capability_ids": ["email_send", "calendar_book"]
  }
}
```

### 8.3 Output parser

- Strict JSON parsing; on parse error, escalating client retries with next tier
- `_decomposition_from_payload` partitions `suggested_capability_ids` into `catalog_reused_capabilities` (ids in `described_ids`) and `new_capabilities` (everything else)

### 8.4 Escalating client policy

```
Tier 1: Groq (Llama 3.x 70B)            - cheap, fast, ~80% success
Tier 2: OpenAI (gpt-4o-mini)            - fallback, structured output mode
Tier 3: Anthropic (claude-3-haiku)      - last-resort fallback
```

Each tier has its own retry policy (max 1 retry on parse error) before escalating.

---

## 9. Label reconciler

### 9.1 Algorithm

```python
def canonicalize_slugs(
    *,
    suggested_slugs: list[str],
    catalog: CapabilityCatalog,
    embedder: Embedder,
    label_store: CapabilityLabelStore,
    cosine_threshold: float = 0.82,
) -> tuple[list[str], CanonicalizationReport]:
    canonical = []
    report = CanonicalizationReport()
    for slug in suggested_slugs:
        if slug in catalog:
            canonical.append(slug)
            continue
        if (cached := label_store.get(slug)) and cached.canonical_slug:
            canonical.append(cached.canonical_slug)
            report.note_cache_hit(slug, cached.canonical_slug)
            continue
        slug_vec = embedder.embed(slug)
        best_match, score = _best_catalog_match(slug_vec, catalog, embedder)
        if score >= cosine_threshold:
            canonical.append(best_match)
            label_store.record(slug, canonical_slug=best_match, embedding=slug_vec)
            report.note_canonicalized(slug, best_match, score)
        else:
            canonical.append(slug)  # truly new
            label_store.record(slug, canonical_slug=None, embedding=slug_vec)
            report.note_new_label(slug)
    return canonical, report
```

### 9.2 Why threshold = 0.82

Empirically tuned: below 0.82 produces false canonicalizations (e.g., `email_send` → `web_search`). Above 0.90 leaves many valid synonyms uncanonicalized. The 0.82 default is a configurable env var (`PLANMYAGENTS_RECONCILER_COSINE_THRESHOLD`).

---

## 10. LLM candidate judge

### 10.1 Interface

```python
@dataclass
class JudgeDecision:
    candidate_id: str
    provider_type: str
    capability: str
    decision: Literal["accept", "reject"]
    confidence: float
    reason: str


def judge_candidates(
    *,
    candidates: list[DiscoveryCandidate],
    capability: str,
    goal: str,
    chat_client: ChatClient,
    batch_size: int = 10,
) -> list[JudgeDecision]: ...
```

### 10.2 Batching strategy

- Up to 10 candidates per LLM call (token budget)
- Single prompt: "Here are 10 candidates for capability X; for each return accept|reject + confidence + reason."
- Response parsed; missing entries default to `reject` with `reason="missing_in_response"`
- Failed batches re‑split to size 1 (one candidate per call) before giving up

### 10.3 Prompt skeleton

```
SYSTEM: You evaluate AI agent candidates for relevance to a capability.

USER: Capability: {capability}
      User's broader goal: {goal_summary}
      Candidates: [{id, display_name, vendor, capabilities_claimed, tools_summary}]

Return JSON: [{"id", "decision": "accept"|"reject", "confidence": 0..1, "reason": str}]
```

### 10.4 Persistence

Every decision persisted to `judge_decisions` keyed by `(candidate_id, provider_type, capability, goal_hash)`. Cached for re‑use within the same `goal_hash` for ~10 minutes (config: `PLANMYAGENTS_JUDGE_CACHE_TTL_S`).

---

## 11. Trust ladder state machine

### 11.1 Transitions

```
                 ┌──────────────────────────────────────────────────────┐
                 │                                                      │
                 │     ┌────────────┐ probe ok    ┌────────────┐        │
                 │     │capability_ │ ←─────────  │registered_ │        │
                 │     │verified    │             │in_directory│        │
                 │     └────────────┘             └────────────┘        │
                 │           ▲                            ▲             │
                 │           │ probe ok                   │             │
                 │           │                            │             │
                 │     ┌────────────┐                ┌────────────┐     │
                 │     │known_      │ ──── dedupe ─→ │community_  │     │
                 │     │provider    │                │listed      │     │
                 │     └────────────┘                └────────────┘     │
                 │           ▲                            ▲             │
                 │           │                            │             │
                 │     ┌────────────┐                ┌────────────┐     │
                 │     │unverified_ │                │unverified  │     │
                 │     │example     │                │            │     │
                 │     └────────────┘                └────────────┘     │
                 │                                                      │
                 └──────────────────────────────────────────────────────┘

Allowed transitions: upward via probe success / source upgrade.
Downward only on explicit revocation (e.g., probe consistently fails N times).
```

### 11.2 Transition rules (codified in `dedupe._best_verification_status`)

- Merge during dedupe: pick higher tier
- Probe success on `mcp_server` with shape match: promote to `capability_verified`
- Probe failure N consecutive times (config `PLANMYAGENTS_PROBE_DEMOTION_THRESHOLD`): demote one tier
- Source removed from upstream (Smithery removes server): demote to `unverified`, then `dormant` after 30d

---

## 12. Provider partition and ranker

### 12.1 Partition (`web/planning.py`)

```python
def _capability_provider_status(
    suggested: list[str],
    *,
    router: ProviderRouter,
) -> dict[str, dict[str, Any]]:
    status_map = {}
    for cap in suggested:
        decision = router.route(cap)
        if not decision.provider_id and not decision.candidates:
            status_map[cap] = {"status": "no_provider", "candidates": []}
        elif decision.provider_id and decision.provider_id in router.executable_providers:
            status_map[cap] = {
                "status": "executable",
                "provider": decision.provider_id,
                "candidates": decision.candidates,
            }
        else:
            status_map[cap] = {
                "status": "configurable",
                "candidates": decision.candidates,
                "env_vars_required": decision.required_env_vars,
            }
    return status_map
```

### 12.2 Per‑capability ranker

```python
def rank_candidates(
    candidates: list[DiscoveryCandidate],
    *,
    capability: str,
    goal: str,
    judge_decisions: list[JudgeDecision],
    benchmark_lookup: BenchmarkLookup,
    weights: ScoreWeights = DEFAULT_WEIGHTS,
) -> list[RankedCandidate]:
    scored = []
    for c in candidates:
        judge = next((j for j in judge_decisions if j.candidate_id == c.id), None)
        if judge and judge.decision == "reject":
            continue
        score = (
            weights.cosine * c.cosine_similarity
          + weights.verification * _tier_weight(c.verification_status)
          + weights.benchmark * benchmark_lookup.score(c.id, capability)
          + weights.freshness * _freshness(c.last_seen_at)
          + weights.cost_penalty * (1 - _cost_norm(c, capability))
        )
        scored.append(RankedCandidate(candidate=c, score=score, explanation=...))
    return sorted(scored, key=lambda r: -r.score)
```

### 12.3 Tie‑breaks

If two candidates score within ε (default 0.02): prefer (1) higher verification tier, (2) more recent benchmark, (3) lexicographic id (stable).

---

## 13. Recipe generator

### 13.1 Interface (planned, Q1)

```python
def export_recipe(
    *,
    workflow_option: WorkflowOption,
    format: Literal["claude_desktop_json", "n8n_json", "cursor_prompt", "markdown", "cli"],
) -> RecipeArtifact:
    """Return the recipe rendered in the requested format."""
```

### 13.2 Format examples

#### `claude_desktop_json`

```json
{
  "mcpServers": {
    "apollo": {
      "command": "npx",
      "args": ["-y", "@apollo/mcp-server"],
      "env": {"APOLLO_API_KEY": "${APOLLO_API_KEY}"}
    },
    "hunter": {
      "command": "npx",
      "args": ["-y", "@hunter/mcp-server"],
      "env": {"HUNTER_API_KEY": "${HUNTER_API_KEY}"}
    }
  }
}
```

#### `n8n_json`

```json
{
  "nodes": [
    {
      "id": "planmyagents-http-1",
      "name": "Step 1: find_companies",
      "type": "n8n-nodes-base.httpRequest"
    }
  ],
  "connections": {}
}
```

#### `cursor_prompt`

```
You are a workflow agent. Use these MCP tools:
- apollo: company_data_lookup
- hunter: contact_enrichment
- neverbounce: email_verification

Workflow:
1. Find Series A EU startups via apollo
2. For each, enrich CTO contacts via hunter
3. Verify each email via neverbounce
4. Return as CSV
```

#### `markdown`

```markdown
# Recipe — Find 100 EU CTOs

## Steps
1. **Find Series A EU startups** — `apollo-mcp` ($0.02 / company)
2. **Enrich CTO contacts** — `hunter-mcp` ($0.05 / contact)
3. **Verify deliverability** — `neverbounce-mcp` ($0.008 / verify)

## Setup
- `APOLLO_API_KEY`, `HUNTER_API_KEY`, `NEVERBOUNCE_API_KEY`
- All three available via Claude Desktop config above

## Estimated total cost: $5.30 | Estimated time: 12 minutes
```

---

## 14. Generic protocol adapters

### 14.1 `GenericMcpAdapter`

```python
class GenericMcpAdapter:
    async def execute(
        self,
        *,
        candidate: DiscoveryCandidate,
        tool_name: str,
        arguments: dict,
        credentials: dict | None = None,
    ) -> AdapterResult:
        if not self._protocol_execution_enabled():
            return AdapterResult.refused(
                reason="execution_disabled",
                next_step="enable_via_env_var_or_use_recipe_export",
            )
        if not self._validate_tool_name(tool_name, candidate):
            return AdapterResult.refused(reason="unknown_tool", next_step="probe_first")
        transport = self._transport_for(candidate, credentials)
        try:
            response = await transport.call(tool_name, arguments)
            return AdapterResult.ok(payload=response.content)
        except McpTransportError as e:
            return AdapterResult.failed(reason=str(e))
```

### 14.2 `GenericOpenApiAdapter`

```python
class GenericOpenApiAdapter:
    async def execute(
        self,
        *,
        candidate: DiscoveryCandidate,
        operation_id: str,
        parameters: dict,
        credentials: dict | None = None,
    ) -> AdapterResult:
        if not self._protocol_execution_enabled():
            return AdapterResult.refused(reason="execution_disabled", next_step="...")
        spec = await self._fetch_openapi_spec(candidate)
        op = spec.operations[operation_id]
        request = op.build_request(parameters, credentials)
        response = await self._client.request(request)
        return AdapterResult.from_http(response, op.response_schema)
```

### 14.3 `GenericA2AAdapter` / `GenericAiAgentAdapter`

Both return structured refusals today:

```python
class GenericA2AAdapter:
    async def execute(self, **kwargs) -> AdapterResult:
        if not self._protocol_execution_enabled():
            return AdapterResult.refused(reason="execution_disabled", next_step="...")
        return AdapterResult.refused(
            reason="not_implemented",
            next_step="use_recipe_export",
            note="A2A skill invocation is not yet implemented in the generic adapter. "
                 "Use the recipe export to run this in your own A2A client.",
        )
```

### 14.4 Reserved input keys (MCP)

```python
_MCP_RESERVED_INPUT_KEYS = frozenset({"tool_name", "arguments"})

def _build_arguments(inputs: dict) -> tuple[str, dict]:
    tool_name = inputs["tool_name"]
    arguments = {k: v for k, v in inputs.items() if k not in _MCP_RESERVED_INPUT_KEYS}
    # Note: "body", "headers", etc. are all valid argument fields and pass through.
    return tool_name, arguments
```

---

## 15. Spend ledger

### 15.1 Interface

```python
class SpendLedger:
    def record(self, *, request_id: UUID, subject: str, provider: str, cost_usd: float, user_id: str | None = None) -> None: ...

    def cumulative(self, *, request_id: UUID) -> float: ...
    def daily(self, *, user_id: str) -> float: ...
    def hourly_provider(self, provider: str) -> float: ...

    def assert_within_cap(self, *, request_id: UUID, additional_usd: float, cap: SpendCap) -> None:
        """Raise SpendCapExceeded if the additional charge would breach cap."""
```

### 15.2 Caps (configurable)

| Cap | Default | Env var |
|---|---|---|
| Per request | $0.50 | `PLANMYAGENTS_SPEND_CAP_REQUEST_USD` |
| Per user per day | $50 | `PLANMYAGENTS_SPEND_CAP_USER_DAILY_USD` |
| Per provider per hour (system‑wide) | $25 | `PLANMYAGENTS_SPEND_CAP_PROVIDER_HOURLY_USD` |

### 15.3 Failure mode

Hard refusal at cap. Never partial execution. Returns 402 to the user with current spend + cap breakdown.

---

## 16. Demand and gap ledger

### 16.1 Gap report builder

```python
def build_gap_report(
    *,
    plan: GoalPlan,
    candidates_by_capability: dict[str, list[DiscoveryCandidate]],
) -> GapReport:
    gaps = []
    for cap in plan.missing_capabilities:
        cs = candidates_by_capability.get(cap, [])
        qualified = [c for c in cs if _is_qualified_candidate(c)]
        rejected = [_rejection_record(c) for c in cs if not _is_qualified_candidate(c)]
        gaps.append(CapabilityGap(
            capability=cap,
            qualified_count=len(qualified),
            rejected=rejected,
            api_providers_available=_apis_for(cap),
        ))
    return GapReport(gaps=gaps)


QUALIFIED_VERIFICATION_STATUSES = frozenset({
    "capability_verified",
    "registered_in_directory",
    "known_provider",
})

AGENTIC_PROVIDER_TYPES = frozenset({"mcp_server", "a2a_agent", "ai_agent"})


def _is_qualified_candidate(c: dict) -> bool:
    return (
        c.get("provider_type") in AGENTIC_PROVIDER_TYPES
        and c.get("verification_status") in QUALIFIED_VERIFICATION_STATUSES
    )
```

### 16.2 Demand event emission

Triggered at end of `/goal` request whenever `plan.missing_capabilities` is non‑empty:

```python
for cap in plan.missing_capabilities:
    demand_ledger.emit(
        capability=cap,
        goal_hash=sha256(normalize_goal(goal)),
        refusal_reason=_first_refusal_for(cap),
    )
```

---

## 17. Pre‑plan discovery and post‑goal refresh

### 17.1 `pre_plan_discovery.py`

```python
def pre_plan_discovery_enabled() -> bool:
    return os.getenv("PLANMYAGENTS_PRE_PLAN_DISCOVERY", "true").lower() == "true"

def max_pre_plan_capabilities() -> int:
    return int(os.getenv("PLANMYAGENTS_PRE_PLAN_MAX_CAPS", "5"))

async def run_pre_plan_discovery(
    *,
    goal: str,
    decomposer_output: GoalDecomposition,
    discovery_store: DiscoveryStore,
    embedder: Embedder,
    scouts: list[Scout],
) -> PrePlanDiscoverySummary:
    thin_caps = _select_capabilities_and_queries(
        decomposer_output, max_caps=max_pre_plan_capabilities()
    )
    queries = [_maybe_chat_client_for_query_expansion(c, goal) for c in thin_caps]
    results = await dispatch_scouts(query=q, enabled_scouts=scouts for q in queries)
    for result in results:
        for raw in result.candidates:
            candidate = normalize(raw, source=result.scout_name, ...)
            discovery_store.save_merge(candidate)
            embedder.embed(candidate_text_for_embedding(candidate))
    return PrePlanDiscoverySummary(scouts_run=[...], candidates_added=...)
```

### 17.2 `post_goal_refresh.py` — MCP tool probe stage

```python
_MAX_PROBE_CONCURRENCY = 6
_PROBE_TIMEOUT_S = 8.0

async def _run_mcp_tool_probe_stage(
    *,
    discovery_store: DiscoveryStore,
    candidates: list[DiscoveryCandidate],
) -> None:
    mcp_candidates = [c for c in candidates if c.provider_type == "mcp_server" and not c.metadata.get("tools")]
    semaphore = asyncio.Semaphore(_MAX_PROBE_CONCURRENCY)

    async def probe(c: DiscoveryCandidate) -> None:
        async with semaphore:
            try:
                tools = await asyncio.wait_for(_list_mcp_tools(c), timeout=_PROBE_TIMEOUT_S)
                discovery_store.save_merge(replace(
                    c,
                    metadata={**c.metadata, "tools": tools},
                    verification_status=_best_verification_status(c.verification_status, "capability_verified"),
                ))
            except (asyncio.TimeoutError, McpTransportError) as e:
                discovery_store.record_probe_failure(c, error=str(e))

    await asyncio.gather(*(probe(c) for c in mcp_candidates), return_exceptions=True)
```

---

## 18. Error handling matrix

| Layer | Error class | HTTP code | Body shape |
|---|---|---|---|
| Surface validation | `InvalidGoalError` | 400 | `{error: "invalid_goal", message: ...}` |
| Surface validation | `RateLimitedError` | 429 | `{error: "rate_limited", retry_after_s: ...}` |
| Decomposer all tiers exhausted | `DecomposerExhaustedError` | 500 | `{error: "decomposer_unavailable"}` |
| Discovery store unreachable | `StoreUnavailableError` | 503 | `{error: "discovery_store_unavailable"}` |
| Scout timeout | logged, NOT propagated | n/a | scout absent from result set |
| Judge LLM error | logged, downgraded to accept‑all | n/a | judge.confidence = 0 |
| Adapter refused (gated) | `AdapterRefusedError` | 422 | structured refusal payload |
| Spend cap hit | `SpendCapExceeded` | 402 | `{error: "spend_cap_exceeded", current_usd, cap_usd}` |
| Auth required (Pro endpoint) | `AuthRequiredError` | 401 | `{error: "auth_required"}` |

**Principle:** the only errors that surface as 5xx are infrastructure failures we cannot mask. Everything else is a structured refusal with `next_step`.

---

## 19. Concurrency patterns

### 19.1 Hot path

- `/goal` orchestration uses `asyncio.gather` to run scout dispatch + label reconciliation + per‑capability fetch concurrently
- Per‑capability LLM judge calls batched (≤ 10 candidates per call) and the batches are issued concurrently
- Embeddings on the hot path use the LRU‑cached embedder (no re‑embed on identical text)

### 19.2 Background work

- Async tasks via `asyncio.create_task` at seed scale
- Migration path: extract to dedicated worker process (RQ / SQS) when WAU > 25K or queue depth becomes hard to bound in‑process
- Cron via systemd timer / system cron for AWS v0, or EventBridge / worker scheduler later; idempotent triggers only

### 19.3 Concurrency limits

| Operation | Limit | Why |
|---|---|---|
| Scout dispatch | ≤ 14 in parallel (one per scout) | Source rate limits |
| MCP tool probe | semaphore = 6 | Upstream MCP server load |
| Embedding writes | batched 16 | OpenAI batch limit |
| LLM judge | concurrent batches ≤ 5 | Provider rate limit |
| Postgres writes | pool of 20 | Standard managed Postgres conn count |

---

## 20. Configuration (env vars)

### 20.1 Discovery

| Var | Default | Notes |
|---|---|---|
| `PLANMYAGENTS_DISCOVERY_STORE_URL` | `sqlite:///...` | Postgres in prod |
| `PLANMYAGENTS_DISCOVERY_SCOUTS` | all | Comma‑separated whitelist |
| `PLANMYAGENTS_PRE_PLAN_DISCOVERY` | `true` | Toggle pre‑plan scout dispatch |
| `PLANMYAGENTS_PRE_PLAN_MAX_CAPS` | `5` | Cap on capabilities pre‑plan dispatches |
| `PLANMYAGENTS_POST_GOAL_PROBE` | `true` | Toggle MCP tool probe |
| `PLANMYAGENTS_SCOUT_DEADLINE_S` | `10` | Per‑scout deadline |

### 20.2 Semantic

| Var | Default |
|---|---|
| `PLANMYAGENTS_EMBEDDER` | `openai` (prod), `ollama` (dev), `deterministic_hash` (tests) |
| `PLANMYAGENTS_EMBEDDER_MODEL` | `text-embedding-3-small` |
| `PLANMYAGENTS_LABEL_RECONCILER` | `true` |
| `PLANMYAGENTS_SLUG_CANONICALIZATION` | `true` |
| `PLANMYAGENTS_RECONCILER_COSINE_THRESHOLD` | `0.82` |

### 20.3 LLM

| Var | Default |
|---|---|
| `GROQ_API_KEY` | — (required for primary tier) |
| `OPENAI_API_KEY` | — (required for fallback + embeddings) |
| `ANTHROPIC_API_KEY` | — (optional last‑resort) |
| `PLANMYAGENTS_DECOMPOSER_TIERS` | `groq,openai,anthropic` |
| `PLANMYAGENTS_JUDGE_CACHE_TTL_S` | `600` |

### 20.4 Execution

| Var | Default |
|---|---|
| `PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION` | `false` |
| `PLANMYAGENTS_SPEND_CAP_REQUEST_USD` | `0.50` |
| `PLANMYAGENTS_SPEND_CAP_USER_DAILY_USD` | `50` |
| `PLANMYAGENTS_SPEND_CAP_PROVIDER_HOURLY_USD` | `25` |

### 20.5 Observability

| Var | Default |
|---|---|
| `PLANMYAGENTS_LOG_LEVEL` | `INFO` |
| `PLANMYAGENTS_LOG_FILE` | `.planmyagents_runs/api.log` |
| `PLANMYAGENTS_OTLP_ENDPOINT` | unset (planned) |

---

## 21. Test strategy

### 21.1 Layers and counts

| Layer | Test count (current) | Style |
|---|---|---|
| Unit (per module) | ~700 | Hermetic, no network, deterministic embedder |
| Integration | ~200 | Spawn FastAPI; sqlite store; stubbed scouts/LLMs |
| Contract (API request/response shape) | ~30 | Pin every `/goal` and `/search` response field |
| Smoke (live external) | ~10 | Optional, gated by env flag |
| **Total backend** | **~965** | All `make test` runs in < 90 s |

### 21.2 Test conventions

- Hermetic by default — `tests/__init__.py` forces `PLANMYAGENTS_PRE_PLAN_DISCOVERY=false`, `PLANMYAGENTS_EMBEDDER=deterministic_hash`
- Use `unittest` not pytest (stdlib only for backend, fewer deps in CI)
- Fixtures via `tests/fixtures/` JSON files (mirror real API shapes)
- Each `Fix N` we ship gets a `test_FIX_N_*.py` to pin the regression

### 21.3 Property tests / fuzzing

- Slug canonicalization fuzz: random slugs, assert no canonicalization that crosses semantic distance > X
- Dedupe priority property test: any two candidates merged, result tier == max(input tiers)
- Capability id stability: round‑trip `normalize → store → read` yields same canonical id

### 21.4 Coverage targets

- Module coverage > 85% on every new module before merge
- No coverage gate on legacy code (we backfill incrementally)
- 100% line + branch coverage required for: `dedupe.py`, `gaps.py`, `spend_ledger.py`, `protocol.py`

---

## 22. Migration strategy

### 22.1 Schema migrations

- Versioned SQL files in `infra/postgres/migrations/NNN_*.sql`
- Applied via `make migrate`
- Forward‑only; rollback by issuing inverse migration as new file
- Every migration tested in CI against fresh + populated databases

### 22.2 Data backfills

- Standalone scripts in `scripts/` (e.g., `audit_rss_hn_candidates.py`)
- Dry‑run by default; `--apply` flag for mutation
- Logged to `discovery_run_events`

### 22.3 Backward compatibility

- API request shape: additive‑only; never break existing clients
- Response shape: new fields added freely; existing fields never removed without 3‑month deprecation
- Database schema: new columns nullable‑first; column removal requires 2‑release deprecation
- Env vars: defaults must preserve prior behavior; new flags default to current behavior

---

## 23. Marketplace algorithms (Phase 2)

### 23.1 Claim verification (DNS + email)

**Module:** `marketplace/claim_verification.py`

```python
@dataclass
class ClaimChallenge:
    claim_id: UUID
    method: Literal["dns", "email"]
    challenge_token: str
    challenge_email: str | None
    expires_at: datetime

class ClaimVerifier:
    def __init__(
        self,
        store: MarketplaceStore,
        dns_resolver: DnsResolver,
        email_sender: EmailSender,
        poll_interval: timedelta = timedelta(minutes=5),
    ) -> None: ...

    async def start_dns_challenge(
        self, vendor_id: UUID, candidate_id: str
    ) -> ClaimChallenge:
        token = secrets.token_urlsafe(16)
        challenge = ClaimChallenge(
            claim_id=uuid4(),
            method="dns",
            challenge_token=token,
            challenge_email=None,
            expires_at=datetime.utcnow() + timedelta(days=14),
        )
        await self.store.save_claim_attempt(vendor_id, candidate_id, challenge)
        return challenge

    async def poll_dns_challenges(self) -> None:
        """Background worker — runs every 5 minutes."""
        pending = await self.store.fetch_pending_dns_claims()
        for claim in pending:
            vendor = await self.store.get_vendor(claim.vendor_id)
            expected = f"planmyagents-verify={claim.challenge_token}"
            host = f"_planmyagents.{vendor.vendor_domain}"
            records = await self.dns_resolver.resolve_txt(host)
            if expected in records:
                await self.store.mark_claim_verified(
                    claim.claim_id, claim_tier="claim_verified_dns"
                )
                await self.store.create_claimed_profile(
                    vendor_id=claim.vendor_id,
                    candidate_id=claim.candidate_id,
                    claim_tier="claim_verified_dns",
                )
                await self._notify_verified(claim)
            elif claim.expires_at < datetime.utcnow():
                await self.store.mark_claim_expired(claim.claim_id)
```

**Email method:** vendor receives a one‑time code at `<owner>@<vendor_domain>`; vendor pastes it back; we mark `claim_verified_email`. Strictly weaker than DNS; can be revoked if domain ownership later disputed.

**Invariant:** claim verification updates `claim_tier` only. `verification_status` (the trust ladder) is independent and only updated via capability‑level evidence (probe / judge / source tagging).

### 23.2 Sponsored placement renderer (with firewall audit)

**Module:** `marketplace/sponsored_renderer.py`

```python
# CRITICAL: imported ONLY by L6 surfaces (category views, search) — NEVER by ranker.py
# CI lint asserts ranker.py imports do not include any marketplace.* module

@dataclass
class CategoryView:
    capability: str
    sponsored: list[SponsoredCandidate]   # max 1
    natural: list[RankedCandidate]        # untouched by sponsorship
    disclosure_note: str

class SponsoredRenderer:
    def __init__(self, marketplace_store: MarketplaceStore) -> None: ...

    async def render(
        self,
        capability: str,
        natural_ranking: list[RankedCandidate],
    ) -> CategoryView:
        active_sponsorships = await self.marketplace_store.fetch_active_sponsorships(
            capability=capability, as_of=datetime.utcnow()
        )
        # Hard invariant: at most 1 active sponsorship per capability per window
        assert len(active_sponsorships) <= 1, "firewall_violation: multiple active sponsors"

        sponsored = []
        if active_sponsorships:
            sp = active_sponsorships[0]
            candidate = await self._fetch_candidate(sp.candidate_id)
            sponsored = [SponsoredCandidate(
                candidate=candidate,
                sponsorship_id=sp.sponsorship_id,
                disclosure_url=sp.public_disclosure_url,
            )]
        return CategoryView(
            capability=capability,
            sponsored=sponsored,
            natural=natural_ranking,   # NEVER modified
            disclosure_note=(
                "Sponsored placements are paid promotions and are clearly labeled. "
                "Rankings are vendor-neutral and not influenced by payment."
            ),
        )
```

**Nightly firewall audit (`marketplace/firewall_audit.py`):**

```python
async def run_firewall_audit() -> FirewallAuditReport:
    breaches: list[dict] = []
    # 1. No sponsored placement is rendered above natural #1
    active = await store.fetch_active_sponsorships()
    for sp in active:
        natural_rank = await ranker_position_for(sp.candidate_id, sp.capability)
        if natural_rank == 1:
            # Natural #1 happens to be the sponsor — fine.
            continue
        # Render output must place sponsored BELOW natural #1
        rendered = await render_category(sp.capability)
        natural_first = rendered.natural[0].candidate_id if rendered.natural else None
        if rendered.sponsored and natural_first != rendered.sponsored[0].candidate_id:
            # OK: sponsored is in its own section, natural #1 is intact
            pass
        else:
            breaches.append({"sponsorship_id": sp.sponsorship_id, "kind": "ranking_displacement"})

    # 2. Static import audit — ranker.py must NOT import marketplace.*
    if uses_marketplace_imports("ranker.py"):
        breaches.append({"kind": "ranker_imports_marketplace_module"})

    # 3. Every active sponsorship has a live disclosure URL
    for sp in active:
        if not await disclosure_url_live(sp.public_disclosure_url):
            breaches.append({"sponsorship_id": sp.sponsorship_id, "kind": "disclosure_offline"})

    report = FirewallAuditReport(
        run_at=datetime.utcnow(),
        breaches_found=len(breaches),
        breach_details=breaches,
        auditor="automated",
    )
    await store.save_firewall_audit_run(report)
    if breaches:
        await pager.fire_p0("vendor_neutrality_firewall_breach", report)
    return report
```

### 23.3 Verified Benchmark scheduling + right‑of‑reply

**Module:** `marketplace/benchmark_request.py`

```python
class BenchmarkCertificationFlow:
    async def request(self, vendor_id, candidate_id, capability, tier) -> str:
        price = PRICE_TABLE[tier]
        cert = BenchmarkCertification(
            certification_id=uuid4(),
            vendor_id=vendor_id,
            candidate_id=candidate_id,
            capability=capability,
            tier=tier,
            price_usd=price,
            methodology_version=current_methodology_version(capability),
            status="awaiting_agreement",
        )
        await store.save(cert)
        await audit.log(vendor_id, "benchmark_requested", cert.certification_id)
        return cert.certification_id

    async def sign_agreement(self, cert_id, vendor_id) -> None:
        cert = await store.get(cert_id, vendor_id)
        cert.agreement_signed_at = datetime.utcnow()
        cert.status = "scheduled"
        await store.save(cert)
        await scheduler.enqueue_benchmark_run(cert.certification_id)

    async def on_run_complete(self, cert_id, benchmark_run_id) -> None:
        cert = await store.get(cert_id)
        cert.benchmark_run_id = benchmark_run_id
        cert.completed_at = datetime.utcnow()
        cert.status = "awaiting_reply"
        await store.save(cert)
        await email.notify_vendor_reply_window_open(cert)
        # 7-day right-of-reply window starts now

    async def publish_if_no_dispute(self, cert_id) -> None:
        """Cron: 7 days after on_run_complete, auto-publish if no dispute filed."""
        cert = await store.get(cert_id)
        if cert.status != "awaiting_reply":
            return
        if cert.completed_at + timedelta(days=7) > datetime.utcnow():
            return
        if await has_open_dispute(cert_id):
            return
        await self._publish(cert)

    async def dispute(self, cert_id, vendor_id, dispute) -> None:
        cert = await store.get(cert_id, vendor_id)
        if cert.status != "awaiting_reply":
            raise InvalidStatus("dispute window closed")
        await arbitration.route(cert_id, dispute)
        await audit.log(vendor_id, "benchmark_disputed", cert_id)

    async def void(self, cert_id, reason) -> None:
        """Vendor refused publication. Badge voided + claim status downgraded."""
        cert = await store.get(cert_id)
        cert.status = "voided"
        cert.voided_at = datetime.utcnow()
        cert.voided_reason = reason
        await store.save(cert)
        await store.downgrade_claim_status(
            cert.vendor_id, days=90, reason="refused_benchmark_publication"
        )
```

### 23.4 K‑anonymity for Demand‑Data API

**Module:** `marketplace/demand_data_export.py`

```python
K_ANONYMITY_THRESHOLD = 100

class DemandDataExporter:
    async def export(
        self,
        vendor_id: UUID,
        capability: str,
        window: timedelta = timedelta(days=30),
    ) -> DemandDataResponse:
        sub = await store.get_subscription(vendor_id)
        if capability not in sub.scopes:
            raise PermissionDenied("capability not in subscription scope")

        events = await ledger.fetch_events(capability, window)
        total = len(events)

        # Top-level total is always exposed (signal that demand exists)
        response = DemandDataResponse(
            capability=capability,
            demand_count_30d=total,
            current_best_alternative=await store.best_qualified_provider(capability),
            api_providers_available=await store.api_providers_for(capability),
        )

        # Geo / host distributions only if each segment >= K_ANONYMITY_THRESHOLD
        geo = aggregate_by(events, "geo_country_band")
        if all(count >= K_ANONYMITY_THRESHOLD for count in geo.values()):
            response.geo_distribution = geo
        else:
            response.suppressed_segments.append("geo")

        host = aggregate_by(events, "host")
        if all(count >= K_ANONYMITY_THRESHOLD for count in host.values()):
            response.host_distribution = host
        else:
            response.suppressed_segments.append("host")

        await audit.log(vendor_id, "demand_data_query", capability)
        return response
```

**Hard invariants:**
- **Never** expose individual goal text
- **Never** expose user identity / IP / org
- Suppress any segment with < 100 events
- TOS prohibits re‑identification with legal teeth

### 23.5 Lead routing

**Module:** `marketplace/lead_routing.py`

```python
class LeadRouter:
    async def maybe_create_lead(
        self,
        capability: str,
        candidate_id: str,
        user_session_id: str,
        user_consent_given_at: datetime,
        user_email_shared: bool,
        anonymized_summary: str,
    ) -> Lead | None:
        vendor = await store.vendor_owning_candidate(candidate_id)
        if not vendor:
            return None  # candidate unclaimed
        sub = await store.lead_subscription(vendor.vendor_id, capability)
        if not sub or sub.status != "active":
            return None

        # Spend cap enforcement
        spent_this_month = await store.lead_spend_this_month(vendor.vendor_id)
        if sub.monthly_cap_usd and spent_this_month + sub.price_per_lead > sub.monthly_cap_usd:
            await alerts.notify_lead_cap_hit(vendor.vendor_id)
            return None

        lead = Lead(
            lead_id=uuid4(),
            capability=capability,
            matched_vendor_id=vendor.vendor_id,
            user_session_id=user_session_id,
            user_consent_given_at=user_consent_given_at,
            user_email_shared=user_email_shared,
            anonymized_summary=anonymized_summary,
            routed_at=datetime.utcnow(),
        )
        await store.save(lead)
        await charge_vendor(vendor.vendor_id, sub.price_per_lead, lead.lead_id)
        await notify_vendor_new_lead(vendor, lead)
        return lead
```

**Privacy guarantees:**
- Lead only created if user explicitly opts in per request
- Email shared only if user explicitly toggles it on (default off)
- Anonymized inbox by default; vendor cannot probe for user identity
- User can mark lead spam → reduces vendor's lead allocation; flagged in vendor audit

### 23.6 Optional execution fee — runtime hook + billing

**Modules:** `marketplace/execution_fee_subscription.py`, `marketplace/execution_fee_charger.py`, `agents/protocol.py` (hook point), `marketplace/invoice_runner.py` (monthly).

```python
# marketplace/execution_fee_subscription.py

MAX_RATE_PCT = Decimal("2.0")  # P11 HARD CAP — never edit without P0 review

class ExecutionFeeSubscriptionService:
    async def opt_in(
        self,
        vendor_id: UUID,
        rate_pct: Decimal,
        addendum_version: str,
        starts_at: datetime,
    ) -> ExecutionFeeSubscription:
        if not (Decimal("0") < rate_pct <= MAX_RATE_PCT):
            raise FirewallViolation(
                "execution_fee_rate_too_high",
                detail=f"rate_pct must be in (0, {MAX_RATE_PCT}], got {rate_pct}",
            )
        current_addendum = await self.addendum_repo.current_version()
        if addendum_version != current_addendum:
            raise InvalidAddendum("execution_fee_addendum_outdated")

        # Enforce one active subscription per vendor (DB also enforces via unique index)
        if await self.store.has_active_subscription(vendor_id):
            raise Conflict("already_opted_in")

        # If vendor is in cooling-off period (just opted out), reject
        if await self.store.in_cooling_off(vendor_id):
            raise Conflict("cooling_off_period_active")

        sub = ExecutionFeeSubscription(
            subscription_id=uuid4(),
            vendor_id=vendor_id,
            rate_pct=rate_pct,
            addendum_version=addendum_version,
            addendum_signed_at=datetime.utcnow(),
            status="active",
            starts_at=starts_at,
        )
        await self.store.save(sub)
        await self.audit.log(vendor_id, "execution_fee_opt_in", sub.subscription_id,
                             metadata={"rate_pct": float(rate_pct)})
        return sub

    async def opt_out(self, vendor_id: UUID) -> None:
        sub = await self.store.fetch_active(vendor_id)
        if not sub:
            return
        sub.status = "opted_out"
        sub.ended_at = datetime.utcnow()
        sub.cooling_off_until = datetime.utcnow() + timedelta(days=90)
        await self.store.save(sub)
        await self.audit.log(vendor_id, "execution_fee_opt_out", sub.subscription_id)
```

```python
# marketplace/execution_fee_charger.py

class ExecutionFeeCharger:
    """Cached lookup + write. Non-blocking on the user's hot path."""

    def __init__(self, store, cache_ttl: timedelta = timedelta(minutes=5)):
        self._store = store
        self._cache: TTLCache = TTLCache(maxsize=10_000, ttl=cache_ttl.total_seconds())

    async def maybe_charge(
        self,
        candidate_id: str,
        capability: str,
        sandboxed_call_id: UUID,
        vendor_revenue_attributed_usd: Decimal,
    ) -> ExecutionFeeCharge | None:
        # Step 1: lookup vendor for this candidate (fail-open if vendor unclaimed)
        vendor = await self._store.vendor_owning_candidate(candidate_id)
        if not vendor:
            return None

        # Step 2: fetch active subscription (cached)
        sub = self._cache.get(("active_sub", vendor.vendor_id))
        if sub is None:
            sub = await self._store.fetch_active_subscription(vendor.vendor_id)
            self._cache[("active_sub", vendor.vendor_id)] = sub
        if not sub:
            return None

        # Step 3: enforce rate cap at write time (defense-in-depth, also at DB level)
        if not (Decimal("0") < sub.rate_pct <= MAX_RATE_PCT):
            await firewall_alert.fire(
                "execution_fee_rate_cap_breach_attempt",
                detail={"vendor_id": str(vendor.vendor_id), "rate_pct": float(sub.rate_pct)},
            )
            return None  # fail-open; never charge if cap is breached

        # Step 4: compute fee, write charge
        fee = (vendor_revenue_attributed_usd * sub.rate_pct / Decimal("100")).quantize(Decimal("0.0001"))
        charge = ExecutionFeeCharge(
            charge_id=uuid4(),
            subscription_id=sub.subscription_id,
            vendor_id=vendor.vendor_id,
            candidate_id=candidate_id,
            capability=capability,
            sandboxed_call_id=sandboxed_call_id,
            vendor_revenue_attributed_usd=vendor_revenue_attributed_usd,
            rate_pct_applied=sub.rate_pct,
            fee_amount_usd=fee,
            invoice_period=datetime.utcnow().strftime("%Y-%m"),
        )
        await self._store.save_charge(charge)
        return charge
```

**Hook point in `agents/protocol.py`:**

```python
class GenericProtocolAdapter:
    async def execute(self, step: RecipeStep, credentials: Credentials) -> StepResult:
        result = await self._call_protocol(step, credentials)
        await self.spend_ledger.record(step, result)
        await self.audit.log(step, result)

        # Phase-2 execution-fee hook (non-blocking, fail-open)
        if self.execution_fee_charger:  # injected only when marketplace module enabled
            try:
                # Wrapped in a task so a marketplace-store hiccup never delays user response
                asyncio.create_task(
                    self.execution_fee_charger.maybe_charge(
                        candidate_id=step.provider_id,
                        capability=step.capability,
                        sandboxed_call_id=step.call_id,
                        vendor_revenue_attributed_usd=result.vendor_revenue_usd or Decimal(0),
                    )
                )
            except Exception:
                # Never fail the user call on a charge attempt
                pass

        # Disclosure metadata for recipe-step response (so user knows if fee was charged)
        result.disclosure["execution_fee_active"] = self.execution_fee_charger is not None
        return result
```

**Monthly invoice runner (`marketplace/invoice_runner.py`):**

```python
async def close_monthly_invoices(period: str) -> None:
    """Runs at 02:00 UTC on the 1st of each month. Closes prior period."""
    vendors_with_charges = await store.distinct_vendors_with_pending_charges(period)
    for vendor_id in vendors_with_charges:
        charges = await store.fetch_pending_charges(vendor_id, period)
        total = sum(c.fee_amount_usd for c in charges)
        if total < MIN_BILLABLE_USD:  # e.g., $10 floor
            # Roll over to next period to avoid micro-invoices
            await store.roll_over_charges(charges, next_period(period))
            continue
        invoice_id = await stripe.create_invoice(
            customer_id=await store.stripe_customer(vendor_id),
            line_items=[{
                "description": f"PlanMyAgents execution-fee attribution, {period}",
                "amount_cents": int(total * 100),
            }],
        )
        await store.mark_charges_invoiced(charges, invoice_id)
        await audit.log(vendor_id, "execution_fee_invoiced",
                        metadata={"period": period, "total_usd": float(total)})
```

**Invariants enforced across the pipeline:**

| Invariant | Where enforced |
|---|---|
| Rate ≤ 2% | DB `CHECK` constraint + service layer + charger guard |
| One active subscription per vendor | DB unique index + service `has_active_subscription` |
| 90‑day cooling‑off after opt‑out | Service `in_cooling_off` guard |
| User price never changes | Adapter does not modify `result.user_price`; fee is debited from a separate vendor margin field |
| User response never delayed by charge attempt | `asyncio.create_task` + try/except in adapter hook |
| Cap breach is P0 alert | `firewall_alert.fire` at write time |
| Disclosure always present | Recipe step response includes `disclosure.execution_fee_active` |

**Test strategy for this module (100% line + branch coverage required):**
- `tests/marketplace/test_execution_fee_subscription.py` — opt‑in / opt‑out / rate cap / cooling‑off
- `tests/marketplace/test_execution_fee_charger.py` — happy path, no vendor, no subscription, cap breach simulation
- `tests/marketplace/test_execution_fee_runtime.py` — adapter hook does not block on marketplace failure
- `tests/marketplace/test_invoice_runner.py` — period close, micro‑invoice rollover, Stripe failure handling
- `tests/marketplace/test_firewall_audit_execution_fee.py` — audit detects any charge with `rate_pct > 2.0` as P0

---

## 24. Partner API gateway (Phase 3)

### 24.1 Authentication + quota

**Module:** `partner/gateway.py`

```python
class PartnerAuthMiddleware:
    async def __call__(self, request: Request, call_next):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer pma_partner_v1_"):
            return JSONResponse({"error": "invalid_api_key"}, status_code=401)
        key = auth.removeprefix("Bearer ").strip()
        key_record = await partner_store.fetch_key_by_hash(bcrypt_hash(key))
        if not key_record or key_record.status != "active":
            return JSONResponse({"error": "invalid_api_key"}, status_code=401)
        integration = await partner_store.fetch_integration(key_record.partner_id)
        if request.url.path not in integration.endpoint_scopes:
            return JSONResponse({"error": "endpoint_not_allowed"}, status_code=403)

        # Rate limit (sliding window per partner)
        if await rate_limiter.exceeded(integration.partner_id, integration.qps_limit):
            return JSONResponse({"error": "partner_rate_limited"}, status_code=429)

        # Monthly quota
        if integration.monthly_query_budget:
            used = await usage_tracker.usage_this_month(integration.partner_id)
            if used >= integration.monthly_query_budget:
                return JSONResponse({"error": "partner_quota_exceeded"}, status_code=429)

        request.state.partner = integration
        response = await call_next(request)
        await usage_tracker.record_call(
            integration.partner_id,
            request.url.path,
            response.status_code,
            elapsed_ms=...,
        )
        return response
```

### 24.2 Per‑partner usage telemetry

Rolled up daily by a worker to `partner_usage_daily` (see §3.12). Used for:
- Per‑partner status pages (`/partner/v1/health` shows partner's own SLO numbers)
- Billing reconciliation (royalty + per‑query)
- Detecting anomalies (sudden spike → potential key leak)

### 24.3 Feedback ingest pipeline

**Module:** `partner/feedback_ingest.py`

```python
class PartnerFeedbackIngestor:
    async def ingest(self, partner_id: str, payload: FeedbackPayload) -> None:
        # Validate recipe_id was issued to this partner
        recipe = await recipe_store.fetch(payload.recipe_id)
        if not recipe or recipe.issued_to_partner != partner_id:
            raise InvalidPayload("recipe_id not issued to this partner")

        # Ingest as benchmark run (origin=partner_feedback)
        await benchmark_store.record_run(
            BenchmarkRun(
                run_id=uuid4(),
                candidate_id=payload.provider_id,
                capability=payload.capability,
                origin="partner_feedback",
                origin_partner_id=partner_id,
                status=payload.result.status,
                latency_ms=payload.result.latency_ms,
                cost_usd=payload.result.cost_usd,
                user_satisfaction=payload.result.user_satisfaction,
                ran_at=datetime.utcnow(),
            )
        )
        await partner_audit.log(partner_id, "feedback_ingest", payload.recipe_id)
```

This is how Partner API generates real‑world benchmark data without us being the executor — partners run the recipes in their hosts, then hand back result telemetry.

### 24.4 SDK design

**TypeScript (`partner/sdk_ts/`)** — published as `@planmyagents/sdk`:

```typescript
export interface PlanMyAgentsConfig {
  apiKey: string;
  partnerId: string;
  baseUrl?: string;
}

export class PlanMyAgentsClient {
  constructor(config: PlanMyAgentsConfig) { /* ... */ }
  async recipe(input: RecipeRequest): Promise<RecipeResponse> { /* ... */ }
  async search(input: SearchRequest): Promise<SearchResponse> { /* ... */ }
  async recommend(input: RecommendRequest): Promise<RecommendResponse> { /* ... */ }
  async benchmark(capability: string): Promise<BenchmarkResponse> { /* ... */ }
  async feedback(input: FeedbackPayload): Promise<void> { /* ... */ }
}
```

**Python (`partner/sdk_py/`)** — published as `planmyagents` on PyPI:

```python
from planmyagents import PlanMyAgentsClient

client = PlanMyAgentsClient(api_key=os.environ["PMA_API_KEY"], partner_id="n8n")

recipe = await client.recipe(goal="find 100 EU CTOs", output_format="n8n_json")
```

**SDK invariants:**
- Auto‑retry on 5xx with exponential backoff (cap 3 retries)
- Surface 4xx as typed exceptions
- Telemetry headers (`X-Sdk-Version`, `X-Sdk-Language`) always sent
- No silent error swallowing

---

## 25. Cross‑references

- **Strategic context**: [BUSINESS_PLAN.md](../BUSINESS_PLAN.md), [PITCH_DECK.md](../PITCH_DECK.md), [README.md](../README.md)
- **Cross‑cutting architecture**: [ARCHITECTURE.md](ARCHITECTURE.md)
- **Subsystem responsibilities**: [HLD.md](HLD.md)
- **Marketplace mechanics**: [marketplace-design.md](marketplace-design.md)
- **Partnership strategy**: [partnership-strategy.md](partnership-strategy.md)
- **Operations**: [operations.md](operations.md)
- **Discovery subsystem deep dive**: [agent-discovery-index.md](agent-discovery-index.md)
- **Benchmark methodology**: [benchmark-methodology.md](benchmark-methodology.md)
