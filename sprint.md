# PlanMyAgents Sprint Plan

> Purpose: track what is actually done, what is partially done, what is pending,
> and why. This file separates local prototype milestones from production-grade
> phases so progress is not misleading.

Last updated: 2026-06-04 (Eval_Framework UI surfaces `/trust` + `/submit-agent` — see notes below + `docs/honest-scope-audit.md`)

> **2026-06-03 milestone reframe (the spine for the current sprint).**
> An engineering audit (`docs/honest-scope-audit.md`) found the gap between
> "benchmarked routing layer" as claimed and as built. The benchmark
> *engine* (runner, scorer, per-capability rankings, credibility classifier)
> is real and tested, but **no discovered agent has been scored end-to-end
> and persisted into the ranking store**. The only cells that ever reached
> the store are mock (`email_verification`, `contact_enrichment`) and
> hand-coded API baselines (Razorpay live; Resend/Firecrawl fixture) — and
> the baselines are firewalled out of routing by design. The credibility
> classifier therefore reports the public leaderboards as `synthetic_only`.
>
> Reposition (not descope): "benchmarked routing of discovered agents"
> stays the moat, but moves from implied-done to the **explicit, top-priority
> Phase-2 unlock this raise funds**. The first deliverable is a single *real
> cell*: execute a discovered MCP server (generic MCP adapter, behind the
> sandbox) against benchmark cases, score it, persist it, and let the
> classifier promote that capability past `synthetic_only`. Until that lands,
> no surface may claim more than `synthetic_only` warrants (AGENTS.md Hard
> Rule #1). README, PITCH_DECK, and BUSINESS_PLAN have been corrected to
> match; this note is the milestone-level record of why.

> **2026-06-04 — agent-card ingestion shipped; A2A quality attestation
> blocked-on-invocation.** Users/vendors can now submit an Agent Card URL for
> any domain (`make card-ingest CARD_URL=...`); the platform resolves it,
> runs existence + claim verification (reusing `verify_candidate`), and
> indexes the agent capped at claim-level trust (`benchmark_status` stays
> `not_started`, non-routable). This is registration/resolution, not a
> crawler — discovering unknown agents on unknown domains stays out of scope.
> A2A **quality** attestation remains blocked until A2A invocation is wired
> into `agents/protocol.py`; `CardIngestionService.attest_quality(...)`
> returns `blocked_on_invocation` (`source="refused"`, non-real to the
> credibility classifier) and will route through the `EvalFramework` once the
> A2A invocation surface exists. Spec: `.kiro/specs/agent-card-ingestion/`.
> Detail: `docs/honest-scope-audit.md` addendum.

> **2026-06-04 — Eval_Framework made visible: `/trust` + `/submit-agent`
> surfaces.** The eval framework was the asset the business plan calls the
> moat, yet it had **zero UI** — it lived entirely in CLI/cron jobs and only
> showed up indirectly as leaderboard numbers. Added a read-only
> `GET /eval/methodology` endpoint (`web/routes/eval.py`) that is a pure
> projection of code that already governs behaviour: the cheapest-first tier
> ladder (`eval.models.EvalTier`), the per-protocol invocation maturity
> registry (`eval.protocols.default_registry` — MCP/OpenAPI executable;
> A2A/ai_agent verification-only; ACP/ANP planned), the ranking-source
> taxonomy (`REAL_EVAL_SOURCES` vs `NON_REAL_EVAL_SOURCES`), and a **live**
> per-capability credibility histogram computed from the same leaderboard
> index `/leaderboards` reads. The new `/trust` page renders it and, per
> AGENTS.md Hard Rule #1, shows the uncomfortable truth in red when every
> cell is `synthetic_only` (today: 64/64, 0 real-run capabilities). The new
> `/submit-agent` page honestly documents the `make card-ingest` CLI pipeline
> and its claim-level trust ceiling rather than faking a self-serve form that
> POSTs nowhere. No new machinery, no new persisted state — integration only.
> The Next.js dev/build scripts moved off `--turbopack` (native bindings are
> blocked by system policy on the build host; webpack is the supported path).
> Tests: `apps/api/tests/web/test_eval_methodology.py` (4) +
> `apps/web/tests/e2e/trust-surface.spec.ts` (3). Full fast suite 1315→1319.

---

## Product Direction

PlanMyAgents is an **Agent Discovery Index + benchmarked routing layer**.

The product should:

1. Discover callable agents/providers from MCP catalogs, A2A Agent Cards,
   AI-agent directories, GitHub/tool listings, OpenAPI/vendor docs, and fallback
   APIs.
2. Normalize and dedupe those candidates into a searchable index.
3. Map user tasks to candidate capabilities.
4. Mark unverified candidates as `will_fail`.
5. Promote only configured, benchmarked, smoke-tested candidates into routable
   providers.
6. Execute workflows only through promoted providers.

Hunter, Apollo, Firecrawl, Amadeus, Duffel, and similar APIs are useful
fallback/smoke-test providers. They are not the strategic center.

### Index vs. Surfaces

The **index** is the asset. The user-visible surfaces (`/goal`,
`/categories`, `/agents/{id}`, `/search`, `/leaderboards/{cap}`, the future
`/export?target=n8n` and `/trust/{cap}` API) are different access patterns
on top of the same index. They are not competing products; they are
competing user journeys. Engineering decisions should be made through this
lens:

- **Index work** (more sources, OpenAPI/docs extraction, dedupe, embedding
  deployment, freshness) helps every surface and grows the underlying
  asset. Prefer it when in doubt.
- **Surface work** (planner depth, leaderboard credibility, search ranking,
  export formats) helps one surface only. Fine to do, but be explicit about
  *which* surface you're investing in this sprint and why.
- **External pitch language** ("trust layer for the agent/tool economy")
  describes what the index *aspires to be*. This file uses the structural
  framing to keep engineering decisions grounded in what's actually shipped.

### Discovery source tiers (added 2026-05-11 after honest audit)

Not all "discovery sources" are created equal. We separate them into two
tiers because confusing them undermines the product story:

- **Tier 1 — first-party.** We hit official endpoints directly: GitHub code
  search (`api.github.com/search/code`), public catalog JSON URLs (e.g.
  `api.apis.guru/v2/list.json`, `smithery.ai/api/servers`), and curated
  vendor doc URLs we crawl ourselves. These make us *the discovery layer*.
  Goal: rich enough that Tier 2 is redundant.
- **Tier 2 — third-party search APIs.** Tavily, Brave, Exa. Today the
  `Live*Source` classes in `apps/api/planmyagents_api/discovery/sources/live.py`
  (LiveMcpRegistrySource, LiveA2AAgentCardSource, LiveOpenApiSpecSource,
  LiveVendorDocsSource, LiveAgentMarketplaceSource) are all wrappers
  around `_search_family(provider, api_key, ...)` — i.e. Brave/Tavily
  queries with topic-specific keyword presets. They are scaffolding,
  useful only for the long tail of vendor blogs Tier 1 doesn't cover yet.
  We want to *outgrow* this tier (see "First-party crawler coverage"
  track below).

When `make env-check` reports a Tier 2 toggle as `[set]`, that is **not**
"we have a registry crawler." It means "we are willing to pay Tavily/Brave
to run a topic-filtered web search." Treat it as a fallback only.

---

## Prototype Milestones

| Milestone | Status | Evidence | Notes |
|---|---|---|---|
| Static provider registry | Complete | `packages/registry/agents.json` | Active registry plus `discovered_agents` snapshot. |
| Registry schema | Complete | `packages/registry/agents.schema.json` | Includes provider type, discovered candidates, and `will_fail`. |
| Benchmark loader/scoring/runner | Complete | `apps/api/planmyagents_api/benchmark/` | Works locally with YAML cases and reports. |
| Email verification benchmark cases | Complete | `packages/benchmarks/email_verification/` | Narrow but useful first benchmark slice. |
| Local Qwen planner | Complete | `apps/api/planmyagents_api/planner/local_qwen.py` | Uses Ollama/Qwen and validates JSON output. |
| LLM intent-to-capability mapper | Complete as local architecture slice | `apps/api/planmyagents_api/planner/intent_mapper.py` | Semantically maps user goals to source-derived catalog capabilities, then deterministically rejects unknown IDs, low confidence, malformed JSON, or unavailable LLM output. |
| LLM coverage auditor | Complete as local architecture slice | `apps/api/planmyagents_api/planner/intent_mapper.py` (`_audit_mapping_coverage`) | Second LLM pass that promotes optional/missing catalog capabilities into required when explicit user constraints (budget, date, destination, delivery, compliance, data access) are not yet covered. Skipped safely if the local LLM is unavailable. |
| Candidate verification | Complete as local architecture slice | `apps/api/planmyagents_api/discovery/verification.py`, `scripts/run_candidate_verification.py` | Records evidence quality from agent cards, MCP metadata, OpenAPI/docs, or source text; verification is an explicit promotion input, not a routing trigger. |
| Benchmark store | Complete as local architecture slice | `apps/api/planmyagents_api/benchmark/store.py`, `scripts/planmyagents_bench.py` | Persists scored runs as a separate promotion input. Verification + benchmark + adapter + `ready_for_promotion` are required for routing. |
| Generic protocol adapters (`protocol_beta`) | Complete as local architecture slice | `apps/api/planmyagents_api/agents/router.py`, registry generator | MCP/A2A/OpenAPI/AI-agent candidates default to generic adapters in `protocol_beta` mode and are refused in production routing until reviewed. |
| Honest refusal logic | Complete | planner + demo responses | Unsupported or unsafe plans are refused with reasons. |
| Discovery enrichment safety model | Complete | `planmyagents_api.discovery` + registry compatibility wrapper | Failed tasks can enrich non-routable candidates. |
| Discovery package v0 | Complete as local architecture slice | `apps/api/planmyagents_api/discovery/` | Has source protocol, static seed, JSON/MCP/A2A/AI-directory/web-doc file/URL connectors, live GitHub/URL research sources, normalizer, dedupe, query expansion, JSON/SQLite stores, index, service. It is not web-scale crawling yet. |
| Discovery CLI | Complete | `scripts/run_discovery.py`, `scripts/run_discovery_upkeep.py`, `scripts/run_discovery_research.py`, `scripts/run_discovery_scheduler.py` | Searches v0 discovery index, refreshes SQLite/JSON stores, runs broad research, and supports local scheduled upkeep. |
| Registry-driven routing | Complete | `apps/api/planmyagents_api/agents/router.py` | No provider selection in UI. |
| Hunter adapter | Complete | `apps/api/planmyagents_api/agents/hunter.py` | Real adapter, used as smoke test only. |
| Workflow executor/stitcher | Complete | `apps/api/planmyagents_api/workflows/` | Executes validated plans through real configured providers. |
| Demo UI | Complete as local prototype | `scripts/serve_demo.py` | Local-only demo; emits structured trace logs, shows ranked workflow options, per-capability gaps, rejected fallback candidates, and raw debug JSON. |
| Benchmark scheduler + rankings | Complete | `apps/api/planmyagents_api/benchmark/scheduler.py`, `apps/api/planmyagents_api/benchmark/rankings.py`, `scripts/run_benchmark_scheduler.py` | Picks verified candidates, runs YAML benchmark cases through real adapters or labelled mock fallbacks, persists `benchmark_runs` + `agent_rankings`, and writes `benchmark_status` back to the discovery store. |
| Per-(provider, capability) rankings + benchmark store v2 | Complete | `apps/api/planmyagents_api/benchmark/store.py` | JSON + Postgres backends with `agent_rankings` table; composite score weighted by success rate, quality, and cost efficiency. |
| Verification record persistence | Complete | `apps/api/planmyagents_api/discovery/verification_store.py`, updated `scripts/run_candidate_verification.py` | JSON + Postgres backends; `run_candidate_verification.py` now appends a `VerificationRecord` per check so the agent detail page can show evidence + blocker history. |
| Postgres production path | Complete | `infra/postgres/init/001_planmyagents.sql`, `scripts/apply_migrations.py`, `make migrate` | Schemas for `discovery_candidates` (with pgvector), `benchmark_runs`, `agent_rankings`, and `verification_records`; explicit migration script + Makefile target. |
| Embedding + keyword search | Complete | `apps/api/planmyagents_api/discovery/embeddings.py`, `apps/api/planmyagents_api/discovery/store.py` (`search_by_embedding`), `scripts/embed_discovery_candidates.py` | Pluggable embedder (`DeterministicHashEmbedder` default, `OllamaEmbedder` via `PLANMYAGENTS_EMBEDDING_MODEL`); pgvector cosine search on the Postgres store with in-memory text fallback for local SQLite/JSON. |
| Production FastAPI surface | Complete | `apps/api/planmyagents_api/web/app.py`, `apps/api/planmyagents_api/web/models.py`, `make api` | Routes: `/health`, `/discovery/categories`, `/discovery/categories/{cluster}`, `/discovery/agents/{id}`, `/discovery/search`, `/benchmark/runs`, `POST /goal`. Pydantic boundary models with permissive payloads where the domain dataclasses already enforce shape. |
| Next.js web app scaffold | Complete | `apps/web/`, `make web`, `make web-build` | Next.js 16 + App Router + Tailwind. Pages: category index, category detail, agent detail, search. Honest status pills separate known/listed evidence, benchmark state, and runnable status. Production build green; consumes the FastAPI app. |
| Test suite | Complete for current scope | 918 tests via `make test` (as of 2026-05-15; sprint-3.md tracks the rolling count). The Gap 1–4 closure adds 7 + 6 + 16 + 29 = 58 new regressions covering tool-aware embedding text, the post-goal MCP probe stage, the pre-plan discovery wrapper, and the generic protocol adapters' real invocation paths (MCP `tools/call`, OpenAPI request builder, structured A2A / AI-agent refusals). | Should remain green before any new work. The original 345-test scope expanded through sprints 2 and 3 with embedding regressions, LLM tier escalation tests, cost-cap + spend-ledger tests, six commodity-API wrapper test packs (Stripe, Razorpay, Resend, Firecrawl, Shippo, eBay Browse), benchmark-runner integration tests, slug canonicalization, default event-store path resolution, and the pgvector-unwrap regression. The original suite (intent mapper, coverage auditor, generalization eval, candidate verification, benchmark store, benchmark rankings, benchmark scheduler, verification store, embeddings, FastAPI app, registry promotion, discovery index, provider router, capability demand log, apis-without-agents store + routing facade, discovery run audit log, scout dispatcher, `/open-mcp-opportunities` endpoint, and the heuristic+LLM agent classifier guarding vendor_rss / hacker_news ingestion) is still in place. |

---

## Current Discovery Reality

Today, discovery search is intentionally narrow:

1. The planner extracts missing capabilities, for example `travel_search`,
   `fare_comparison`, `lodging_search`, `lodging_comparison`,
   `booking_execution`, and `payment_authorization`. Unsupported plans are
   refined by the LLM intent mapper, which semantically maps the user's goal to
   source-derived catalog capabilities without prompt-specific branches, and by
   a second LLM coverage auditor that promotes optional capabilities into
   required when explicit user constraints (budgets, dates, destinations,
   delivery, compliance, data access) are not yet covered. The validator stays
   deterministic: unknown IDs, low confidence, malformed JSON, or unavailable
   LLM all reject.
2. The discovery service loads existing promoted/discovered snapshots from
   `packages/registry/agents.json`, but the demo no longer writes ad hoc query
   results back to that canonical registry.
3. It searches configured discovery sources:
   `StaticDiscoverySource`, `JsonDiscoverySource`, `McpCatalogSource`,
   `A2AAgentCardSource`, `AiAgentDirectorySource`, and `WebDocDiscoverySource`.
   Explicit research jobs can also add `GitHubResearchSource` and
   `UrlResearchSource`.
4. Query expansion normalizes common typos such as `ticker` -> `ticket` and
   `fransciso` -> `francisco`, then infers capability terms.
5. Candidates are matched by capability overlap first, with text ranking against
   candidate metadata.
   Public web/company/marketing prompts must demote internal database MCPs
   unless the user explicitly asks for SQL/Postgres/internal data.
   This is enforced through reusable intent-fit scoring, not prompt-specific
   product branches.
6. Planner fallback also builds a source-derived capability catalog from
   candidate names, capability IDs, and capability notes. This is the mechanism
   for new domains; avoid adding query-specific branches for each new example.
7. Results remain `will_fail` and non-routable.
8. The demo report qualifies only `mcp_server`, `a2a_agent`, and `ai_agent`
   candidates for workflow options. API and payment providers can still appear
   as rejected fallbacks with explicit rejection reasons.
9. Agentic candidates must have verified public evidence for the specific
   capability before they count as qualified.
10. Workflow options are ranked end-to-end rather than one capability at a time.
   Payment candidates must be compatible with the selected booking provider.

For the flight-booking query, the current demo shows A2A candidates for travel,
fare comparison, and payment authorization, but no qualified MCP/A2A/AI-agent
candidate for `booking_execution`. Duffel and other APIs remain visible only as
rejected fallbacks under the current qualification policy. There is now file/URL
ingestion for JSON, MCP catalogs, A2A Agent Cards, AI-agent directories, and
web-doc manifests, plus opt-in bounded live connectors for Brave/Tavily web
search, GitHub code search, MCP registries, A2A Agent Cards, OpenAPI specs,
vendor docs, and agent marketplaces. Live findings remain unverified and
non-routable until reviewed, adapted, benchmarked, and configured. Embedding
search and web-scale crawling are still not in the runtime path.

For hotel/lodging prompts, the planner infers lodging-specific search and
comparison capabilities from the configured candidate catalog. The current
configured sources include hotel API fallbacks, but no verified MCP/A2A/AI
hotel-booking capability evidence. The UI and debug JSON expose
`source_coverage` to make clear whether live research ran for that request, so
local misses must not be interpreted as market-wide absence.

---

## Next Phase

With the data backbone, FastAPI surface, and Next.js scaffold in place, the
next phase is **real GTM workflow proof + public discovery/benchmark
leaderboard polish**.

Priority order:

1. Stand up the deployed Postgres + pgvector instance behind
   `PLANMYAGENTS_DISCOVERY_STORE_URL` and run the benchmark scheduler on a
   schedule (cron / Inngest job) so `agent_rankings` stays fresh.
2. Run live discovery on every blocked request during the first three-month
   learning window while keeping results non-routable.
3. Promote one real paid or agentic GTM provider path once credentials /
   design-partner approval exists, so at least one card on the category browser
   is `Routable today`.
4. Add compatibility metadata and flow-level ranking records so workflows are
   scored as coherent provider sets, not isolated capability matches.
5. Replace curated manifests with larger scheduled/live source ingestion so
   capability inference expands organically as sources expand.
6. Wire `OllamaEmbedder` (`PLANMYAGENTS_EMBEDDING_MODEL=nomic-embed-text` or
   similar) on the deployed Postgres so search-within-category uses semantic
   similarity, not the deterministic-hash fallback.
7. Promote verified DB candidates through an explicit gate: verified evidence,
   adapter module, passed benchmark, `ready_for_promotion`, and optional
   registry generation via `scripts/generate_registry_from_db.py`.
8. Maintain a generalization eval suite for representative task families. These
   fixtures are regression probes, not supported-use-case code paths.
9. Wire execution into the FastAPI `/goal` route once the planner pipeline is
   considered stable enough to back from the production frontend rather than
   the local stdlib demo.

---

## Production Phases

### Phase -1: Customer And Workflow Validation

Status: **Pending**

Done:

- Discovery call script exists.
- Cold email template exists.
- Business thesis and wedge are documented.

Pending:

- Run 10-15 customer calls.
- Recruit 3 design partners.
- Get at least one person willing to pay for a manual/concierge workflow.
- Select one narrow workflow for v0.

Why not complete:

- No real users, design partners, or paid workflow evidence yet.

---

### Phase 0: Repo And App Scaffold

Status: **Complete for local v0; production scale pending**

Done:

- Python API package structure exists.
- Registry, benchmark, planner, agents, discovery, and workflow packages exist.
- Local demo server exists.
- Standard-library unit tests exist.
- `make quality` runs unit tests, compile checks, JSON validation, Ruff, and
  discovery smoke checks.

Pending:

- Real FastAPI app entrypoint.
- Database setup and migrations.
- Web frontend scaffold.
- CI configuration.
- Pytest adoption or an explicit decision to stay on `unittest`.

Why partial:

- The current repo is a local prototype, not a deployable app scaffold.

---

### Phase 1: Agent Discovery Index v0

Status: **Partially Done**

Done:

- `DiscoverySource` protocol.
- Static source connector.
- JSON file/URL source connector.
- MCP catalog file/URL source connector.
- A2A Agent Card file/URL source connector.
- AI-agent directory file/URL source connector.
- Web/doc manifest file/URL source connector.
- Bounded live connectors (opt-in via env flags + API keys): Brave/Tavily web
  search, GitHub code search, MCP registry search, A2A Agent Card search,
  OpenAPI spec search, vendor-doc search, agent-marketplace search, and
  configured live directory/spec URL fetchers.
- Candidate normalizer.
- Dedupe logic.
- Local JSON and SQLite candidate stores; Postgres + `pgvector` wired via
  `PLANMYAGENTS_DISCOVERY_STORE_URL`.
- In-memory searchable index.
- Synonym and typo-tolerant query expansion (without noisy semantic-search
  expansion when domain capabilities are explicit).
- `search_candidates(capability, task_description)` service path.
- Daily upkeep script for configured sources.
- Curated source manifests with ~70 deduped non-routable candidates across MCP,
  A2A, AI-agent, API, and payment source types.
- Freshness/staleness metadata in candidate search results.
- Stale-candidate filtering via discovery service and CLI.
- Promotion-readiness report for each search result.
- Per-capability gap report for unsupported responses.
- Ranked workflow options for unsupported responses.
- Agent/MCP-only qualified-candidate policy in the demo report.
- Rejected fallback candidate reporting for APIs/payment providers.
- Payment/booking compatibility checks in workflow ranking.
- Non-persistent demo discovery so ad hoc prompts do not grow `agents.json`.
- Source-derived capability catalog for planner fallback.
- Lodging/hotel configured candidate coverage through source manifests.
- Debug `source_coverage` metadata that distinguishes configured-source search
  from live web/GitHub research.
- Discovery service used by unsupported-plan enrichment.
- CLI for local discovery search.
- Broad research CLI for opt-in live GitHub/URL discovery with evidence URLs.
- Local scheduler wrapper for recurring discovery research upkeep.
- Non-routable `will_fail` lifecycle.

Pending:

- Web-scale crawling deployed against the Postgres + pgvector store.
- A live `OllamaEmbedder` deployment for richer recall (the deterministic-hash
  embedder ships as the zero-dependency default).

Why partial:

- All ingestion, dedupe, normalisation, persistence (Json/SQLite/Postgres),
  pgvector schema, embedding-aware search, and FastAPI exposure are in place.
  What is left is operational: pointing it at a deployed Postgres instance and
  a hosted embedder, plus growing source coverage.

---

### Phase 2: Benchmark Foundation

Status: **Mostly Done**

Done:

- YAML benchmark loader.
- Scoring engine.
- Benchmark runner.
- Markdown report generator.
- 20 email verification benchmark cases.
- Persistent benchmark store (`benchmark_runs` table, JSON + Postgres).
- Per-(provider, capability) `agent_rankings` table with sample size, success
  rate, quality, p50/p95 latency, average cost, and composite score.
- Benchmark scheduler (`make benchmark-schedule`) that picks verified
  candidates, runs cases through real or labelled mock adapters, persists
  runs + rankings, and writes `benchmark_status` back to the discovery store.
- Benchmark data is exposed on the agent detail page in the Next.js app.

Pending:

- 100+ benchmark cases across target capabilities (today: only
  `email_verification` has a deep case set).
- LLM-as-judge path for softer outputs.
- Cron / Inngest scheduler that runs the benchmark scheduler on an interval
  against the deployed Postgres instance.

Why mostly done:

- Benchmark core, persistence, scheduling, ranking, and surfacing through the
  API + UI all work end-to-end. What remains is breadth (more capabilities)
  and operations (recurring scheduling against deployed infra).

---

### Phase 3: Verified Provider Adapters

Status: **Started**

Done:

- Common provider adapter protocol.
- Mock adapters for tests.
- Real Hunter adapter.
- Router refuses configured providers without executable adapters.

Pending:

- Promote candidates from discovery index before adding more adapters.
- Implement adapters only for selected verified providers.
- Add sandbox tests per adapter.
- Add redaction and provider-call audit logging.

Why only started:

- More adapters should not be added blindly. The discovery index should select
  which candidates deserve implementation.

---

### Phase 4: Planner, Intent Mapper, And Cost Estimator

Status: **Partially Done**

Done:

- Local Qwen planner via Ollama.
- Rules fallback.
- Registry-constrained plan validation.
- Unsupported capabilities become refusals.
- Unresolved dependency placeholders are refused.
- Unsupported external capabilities can trigger discovery enrichment.
- LLM intent-to-capability mapper that maps user goals to source-derived
  catalog capabilities, with a deterministic validator (rejects unknown IDs,
  low confidence, malformed JSON, or unavailable LLM output).
- LLM coverage auditor that promotes optional/missing catalog capabilities into
  required when explicit user constraints (budget, date, destination, delivery,
  compliance, data access) are not yet covered. Skips safely if the local LLM
  is unavailable.
- Standard-library dataclass validation now guards planner, discovery, and
  workflow response surfaces in local tests.

Pending:

- Cost estimator.
- Dependency-aware plan schema.
- Planner-to-discovery search integration in the FastAPI `/goal` route (the
  local demo already wires this; the production route currently returns the
  plan only).
- Planner-facing intent normalization in responses. Discovery search already
  normalizes terms like `ticker` -> `ticket` and `fransciso` -> `francisco`,
  but the planner/demo still needs to expose normalized intent as a
  first-class output.
- Plan persistence.
- Explicit confidence scoring for plans.

Done since the previous milestone:

- Pydantic/FastAPI boundary models for the production API
  (`apps/api/planmyagents_api/web/models.py`).

Why partial:

- Planning, intent mapping, and coverage auditing exist, plus the API surface
  is shaped, but cost, dependency execution, persistence, and execution from
  the production API are not in yet.

---

### Phase 5: Execution Engine

Status: **Partially Done**

Done:

- `WorkflowExecutor`.
- Runtime response scoring.
- Stitcher producing records, sources, cost, and confidence.
- Demo uses workflow executor.
- Refuses non-routable sub-tasks.

Pending:

- General dependency-aware execution.
- Async orchestration with Inngest or Temporal.
- Retries and backoff.
- Persistent task state.
- Provider-call logs.
- CSV/JSON artifact export.
- Partial-result UX.

Why partial:

- Local execution path exists, but it is not durable, dependency-aware, or
  production orchestrated.

---

### Phase 6: Public Benchmark / Discovery Leaderboard

Status: **First version Done; data depth pending**

Done:

- Markdown benchmark report generator.
- Capability cluster categorisation
  (`apps/api/planmyagents_api/discovery/categories.py`) that groups candidates into
  human-readable categories (Lead Intelligence, Payments, Travel, etc.).
- Public category index page in the Next.js app
  (`apps/web/src/app/page.tsx`) showing per-category candidate counts,
  verified counts, benchmark-passed counts, routable-today counts, top three
  candidates, and provider-type breakdown.
- Public category detail page
  (`apps/web/src/app/categories/[cluster]/page.tsx`) with per-candidate cards
  showing verification status, benchmark status, routability, and required
  credentials.
- Public agent detail page (`apps/web/src/app/agents/[id]/page.tsx`) with
  vendor info, promotion-readiness blockers/required steps, capability list
  with confidence, per-capability rankings (sample size, success rate,
  quality, p50/p95 latency, average cost, composite score, source), and
  verification + recent-runs history.
- Search page (`apps/web/src/app/search/page.tsx`) backed by
  `/discovery/search`, which falls back to in-memory text search when the
  store is local and uses pgvector cosine search when the store is Postgres
  with embeddings.

Pending:

- Methodology page that explains the composite-score weighting and
  benchmark-status thresholds.
- Per-category sample-size and freshness disclaimers (the data is on the
  cards; we need explicit copy).
- Wider benchmark coverage so more cards say `Bench: passed` instead of
  `Bench: not started`.
- Public deployment behind a stable URL once a Postgres store is hosted.

Why first version is done:

- A user can land on the homepage, browse all 21 capability clusters, drill
  into one to see ranked candidates, and open any candidate to see its
  verification + benchmark history. Honest counts rather than marketing
  claims. The remaining work is depth and operational deployment.

---

### Phase 7: Minimal Product UI

Status: **First version Done**

Done:

- Local demo UI for goal submission and execution
  (`scripts/serve_demo.py`).
- Real Next.js app at `apps/web/` with App Router, Tailwind, TypeScript,
  category index, category detail, agent detail, search, and a 404 fallback.
- No provider selection in the UI; users browse what the discovery layer has
  found.
- Honest status pills (verification, benchmark, routability) consistent
  across all pages.
- Production build passes (`make web-build`) and the app renders live data
  against the FastAPI app (`make api`).

Pending:

- Task history.
- Plan approval screen.
- CSV/JSON downloads.
- Authentication if needed for pilots.
- A `/goal` page in the Next.js UI once the FastAPI `/goal` route also
  executes (today it plans only).
- ~~Methodology, FAQ, and trust-narrative pages.~~ Methodology + trust
  narrative shipped 2026-06-04 as `/trust` (live eval-ladder + protocol
  maturity + credibility histogram) and `/submit-agent` (card-ingestion
  explainer). FAQ still pending.

Why first version is done:

- The user can browse, filter, and search agents through a real
  production-shaped frontend; the remaining work is depth (history,
  approvals, downloads, auth) rather than greenfield UI.

---

### Phase 8: Design Partner Pilots

Status: **Pending**

Done:

- Pilot criteria documented.
- YC/application positioning drafts exist.

Pending:

- 3 design partners.
- 10+ real workflows.
- Paid or concierge pilot.
- Comparison against current process.
- Repeated usage evidence.

Why not complete:

- No customer validation has happened yet.

---

### Phase 9: Production Hardening

Status: **Pending**

Done:

- Some safety rules exist in code and docs.
- API keys are environment-driven.
- No discovered provider is executable by default.
- Local demo emits structured per-request trace logs with planner, discovery,
  execution, and response timing events.

Pending:

- Logging/redaction.
- Durable request trace storage.
- Sentry/PostHog or equivalent.
- Rate limiting.
- Audit logs.
- Privacy policy.
- Terms.
- Sub-processor list.
- Data deletion endpoint.
- Secret management.
- CI/CD.

Why not complete:

- Hardening comes after the core discovery/index/execution loop is real.

---

## What shipped on 2026-05-11 (night — "LLM-driven planning + retrieval-time judge + honest refusal")

User feedback that motivated this: *"again we are hardcoding rules
like dumb kid, hey listen use common sense and think hard, the
planning layer should be intelligently handed over to llm and if llm
is doing a poor job of classification of subtasks or filtering non
relevant agents from retrieved global search then be vocal and we
should fallback to higher quality llm model maybe hosted in groq."*

This refactor moves PlanMyAgents from "LLM with silent regex fallback"
to "LLM-driven by default, honest refusal when no LLM tier is
available." Five concrete architectural changes:

1. **Escalating chat client** (`apps/api/planmyagents_api/llm/escalating_client.py`).
   Single source of truth for all LLM calls. Tries local Qwen first;
   on transport error / empty response / schema-invalid output (per
   caller-supplied `QualityCheck`), escalates to Groq Llama-3.3-70B.
   When both tiers fail, raises `NoLlmTierAvailableError` carrying
   structured `EscalationMetadata` (which tier was attempted, what
   error, etc.). Wired into the intent mapper, planner, candidate
   judge, query expander, and docs extractor.

2. **`PlanningUnavailableError` + 503 refusal in `/goal`**
   (`apps/api/planmyagents_api/web/planning.py`, `…/web/app.py`). The
   silent-fallback-to-substring-rules path is removed from the
   default mode (now `escalating`). When both LLM tiers cannot
   produce a plan, `/goal` returns a 503 whose detail carries
   `{error, message, llm_quality, remediation}`. The frontend renders
   a red "Reasoning unavailable" card with concrete remediation
   steps (e.g. "Set GROQ_API_KEY in .env").

3. **`CandidateJudge` (LLM-driven retrieval-time relevance filter)**
   (`apps/api/planmyagents_api/discovery/candidate_judge.py`). Replaces
   blind trust in substring-derived capability tags. Every `/goal`
   request batch-calls the LLM with the goal + required capabilities
   + 25 raw candidates and asks "which of these actually serve this
   goal?". Verdicts are *not* persisted (per-request semantics). The
   judge prevented the school-project-as-fare-comparison incident by
   construction: even when ingest tags are wrong, the judge sees the
   actual description against the actual goal and rejects.

4. **MCP registry junk pre-filter + audit script**
   (`apps/api/planmyagents_api/discovery/sources/official_mcp_registry.py`,
   `scripts/audit_mcp_registry_candidates.py`). Token-aware regex
   that drops obvious test/demo/school/tutorial entries at ingest
   *before* any LLM call. Goal-independent and zero-cost. The audit
   script applies the same regex to existing rows and (with
   `--apply`) flips `lifecycle_status='rejected'`. On the live
   database this catches the original "ai.smithery/aicastle-school-…"
   row plus 9 more obvious-junk siblings, while preserving names that
   look junk-shaped but aren't (e.g. `Lattiq x402`, usernames with 3-
   to 5-digit suffixes).

5. **`llm_quality` provenance everywhere**
   (`/goal` response payload + `apps/web/src/app/goal/page.tsx`'s
   `ReasoningPill`). Every response now carries
   `plan.planner.llm_quality.planner` (escalation metadata for the
   plan) and `plan.discovery.candidate_judge` (escalation metadata
   for the filter). The frontend renders a green / amber / red pill
   so a quiet escalation is always visible to the user. There is no
   way for the system to silently degrade to substring rules without
   showing it.

Test count: 380 (was 327 before the refactor). New test files:
`test_escalating_client.py`, `test_candidate_judge.py`; new tests in
`test_official_mcp_registry_source.py`, `test_planner_groq_mode.py`,
`test_web_app.py`.

Required env addition: `GROQ_API_KEY` (free tier:
https://console.groq.com). Without it, the fallback tier is missing
and a Qwen failure becomes an honest 503 refusal — which is the
correct behavior, just not the most user-friendly one.

Known follow-up (not done in this session): kill the
`inferred_capabilities` substring path in
`apps/api/planmyagents_api/discovery/query.py`. The intent mapper LLM
already does the goal-to-capabilities mapping; the substring path is
redundant defense-in-depth. Removing it requires careful test
updates because some legacy callers still rely on it.

## Current Code Fixes Needed

Highest priority:

1. Move demo away from raw JSON output toward structured artifact views.
2. Add benchmark scheduling for promoted candidates.
3. Add embedding/keyword search over the discovery store.
4. Add Pydantic/FastAPI boundary models for the production API.
5. Add production Postgres/search-index deployment path beyond local SQLite.

Medium priority:

1. Add cost estimator.
2. Add dependency-aware execution.
3. Add CSV/JSON export.
4. Add provider-call audit records.

Lower priority:

1. Additional API adapters.
2. Public leaderboard.
3. Payment flow.
4. Production web app polish.

---

## Next Sprint

Sprint goal: **Real GTM workflow proof, benchmark scheduling, and production search.**

Scope:

1. Select a real provider/agent-backed GTM workflow for promotion once credentials
   or a public callable agent endpoint is available.
2. Add benchmark scheduling for candidates selected for promotion.
3. Add embedding/keyword search over the discovery store.
4. Add production Postgres/search-index deployment path beyond local SQLite.
5. Add Pydantic/FastAPI boundary models for the production API.

Definition of done:

- Candidate persistence no longer depends only on registry JSON.
- Scheduled upkeep and broad research can run locally without growing
  `agents.json`.
- Promotion readiness can be reviewed per candidate.
- CI or local quality tooling enforces tests, compile checks, JSON validation,
  Ruff linting, and discovery smoke checks.
- Full test suite passes.

---

## Index Work — Real-Time Per-Subtask Discovery (Scout Architecture)

> Added 2026-05-11 in response to: *"how can we also do real-time web crawl
> to find most suitable agents for our subtasks planned by our planning
> LLM?"* This is **the cross-surface upgrade** that turns the index from
> "what we already crawled" into "what we can find right now for this
> specific user's specific subtasks." See "Discovery source tiers" above.

### Why this matters (the strategic insight)

Today the planner does this:

```
GoalRequest → Qwen → list of (capability, constraints) sub-tasks
            → query local discovery store for each capability
            → if store has nothing → honest refusal
```

The "honest refusal" path is wasted information. The planner's output is
a **structured query** — far richer than a Tavily-style free-text web
search. Capability names map to known query families. Constraints
(region, budget, jurisdiction) narrow the search. We can use that
structure to fire **targeted** crawls at **first-party** sources in
parallel, in seconds, with no Tavily/Brave dependency for the core loop.

Concrete example. Today, "buy a house in Dubai with crypto" decomposes
into ~3 sub-tasks. The planner gives us:

```
[
  {capability: "real_estate_listing_search",  region: "AE-DU"},
  {capability: "crypto_payment_processing",   currency: "USDT"},
  {capability: "legal_verification",          jurisdiction: "AE"}
]
```

For each sub-task that has zero local matches, we fire a `ScoutDispatcher`
which runs *only the scouts likely to find that capability*, in parallel,
with a hard time budget. Each scout returns provenance-tagged candidates
that get persisted into the discovery store and surfaced in the response
labelled **"freshly discovered for this request."** Next time anyone asks
for the same capability, it's already in the store — discovery becomes a
self-improving loop.

### Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  POST /goal { goal: "..." }                                            │
└──────────────┬─────────────────────────────────────────────────────────┘
               │
               ▼
        Qwen Planner ──── plan.sub_tasks: [(capability, constraints), ...]
               │
               ▼
   ┌───────────────────────────┐
   │ For each sub_task:        │
   │   if local store has hits │
   │     → use them             │
   │   else                    │
   │     → ScoutDispatcher     │
   └───────────────────────────┘
               │
               ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ ScoutDispatcher.dispatch(capability, constraints)            │
   │ ─ runs all enabled scouts IN PARALLEL with a time budget     │
   │   (default 8s), then merges + dedupes candidates             │
   ├──────────────────────────────────────────────────────────────┤
   │ • OfficialMcpRegistryScout    (no key, uses /servers?search) │
   │ • ApisGuruScout               (no key, in-memory fuzzy match)│
   │ • GitHubMcpRepoScout          (uses GITHUB_TOKEN if set)     │
   │ • GitHubA2ACardScout          (searches `agent.json` paths)  │
   │ • WellKnownAgentCardScout     (fetches /.well-known/agent.json│
   │                                from a curated vendor list)   │
   │ • CuratedDirectoryScout       (re-scans static/web_doc cats) │
   │ ─ Optional opt-in tier (off by default):                     │
   │ • TavilyWebFallbackScout      (only if TAVILY_API_KEY set)   │
   │ • BraveWebFallbackScout       (only if BRAVE_SEARCH_API_KEY) │
   └──────────────────────────────────────────────────────────────┘
               │
               ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ Merge + dedupe + rank                                        │
   │ Persist new candidates to discovery store (cache for next    │
   │ request; future identical sub-tasks skip the live crawl)     │
   │ Annotate each with `discovered_at_request_id`                │
   └──────────────────────────────────────────────────────────────┘
               │
               ▼
   /goal response: per sub-task includes a `live_discovery` block:
     {
       "ran": true,
       "scouts_executed": ["github_mcp", "apis_guru", "official_mcp_registry"],
       "scouts_skipped": ["tavily", "brave"],   // opt-in not configured
       "elapsed_ms": 6230,
       "newly_discovered_count": 7,
       "newly_discovered_candidates": [{...provenance, ...verification:none}]
     }
```

### Why scouts (instead of one big crawler)

1. **Modular**: each scout is ~150 lines, owns one source, easy to test.
2. **Parallel**: `asyncio.gather(*scouts, timeout=BUDGET)` gives us concurrent
   fanout. A budget of 8s with 6 scouts running in parallel means the
   slowest scout determines the wall-clock cost, not the sum.
3. **Provenance-pure**: every candidate carries which scout found it,
   which query was used, and what the raw upstream response was. The
   `/leaderboards` credibility classifier can then refuse to mark anything
   as `publishable` if the only evidence is from a Tavily-class scout.
4. **Honest failure**: a scout with no key (e.g. `GitHubMcpRepoScout` with
   no `GITHUB_TOKEN`) returns `{ran: false, reason: "no_key"}` instead
   of silently no-op'ing. The `live_discovery` block surfaces that.
5. **Layered budget**: each scout has its own latency budget. If the
   official MCP registry returns in 1.2s, we don't wait the full 8s for
   slower scouts to finish.

### What we already have vs. what's new

| Piece | Status |
|---|---|
| `OfficialMcpRegistrySource` (Tier-1 batch source) | **Shipped 2026-05-11** ✓ |
| `ApisGuruSource` (Tier-1 batch source) | **Shipped 2026-05-11** ✓ |
| `GitHubCodeSearchSource` (Tier-1 live, key-gated) | **Shipped earlier** ✓ |
| Per-source candidate provenance in discovery store | **Already there** ✓ |
| Cost preview on `/goal` plan responses | **Shipped earlier** ✓ |
| `live_research_status` field on `/goal` | **Shipped earlier** (still relevant; describes the batch-side story) |
| `HackerNewsAgentWatcherSource` (event-driven Tier-1) | **Shipped 2026-05-11 (afternoon)** ✓ |
| `GitHubRecentlyPushedSource` (event-driven, key-gated) | **Shipped 2026-05-11 (afternoon)** ✓ |
| `VendorRssSource` + 13-feed curated seed list | **Shipped 2026-05-11 (afternoon)** ✓ |
| `Scout` protocol + `ScoutDispatcher` + parallel budget | **Shipped 2026-05-11 (afternoon)** ✓ |
| Scout subclass adapters around existing sources | **Shipped — `default_scouts()` factory** ✓ |
| LLM query expansion (Qwen/Groq) per scout | **Shipped 2026-05-11 (afternoon)** ✓ |
| `live_discovery` block per sub-task in `/goal` response | **Shipped 2026-05-11 (afternoon)** ✓ |
| Persist newly-discovered candidates from request-time scouts | **Shipped — reuses existing `discovery_candidates` table** ✓ |
| Frontend "freshly discovered for this request" label on goal page | **Shipped 2026-05-11 (afternoon)** ✓ |
| Honest disclosure when no scout returned anything | **Shipped — per-scout status in `live_discovery.per_capability[].dispatch.scouts`** ✓ |

### What shipped on 2026-05-11 (afternoon, "Both, in order")

The user asked for **Phase 1 then Phase 2 in one session**. Both shipped.
This sub-section documents the actual code, the actual measurements, and
the deliberate compromises so future readers understand what's really in
production vs. still aspirational.

**Phase 1 — Event-driven Tier-1 sources (the firehose subscribers).** Each
source is a normal `DiscoverySource` and is opt-out via env var, so it
runs both during `make discovery-refresh` AND inside the request-time
scout fleet.

* `apps/api/planmyagents_api/discovery/sources/hacker_news.py` —
  `HackerNewsAgentWatcherSource`. Polls HN's free Firebase API
  (`top/new/beststories.json` + per-item fetch). Parallelized with a
  16-thread pool: 200-300 items in ~7-10s wall (sequential would be
  ~140s). Filters by an explicit MCP/A2A/agent keyword set (excludes
  generic "AI" matches because the entire HN frontpage mentions AI now)
  and a `min_score=2` upvote filter (kills personal-blog single-upvote
  posts). Live measurement on 2026-05-11: surfaced 1 real new "agent
  framework" launch on the day it was posted.
* `apps/api/planmyagents_api/discovery/sources/github_recently_pushed.py` —
  `GitHubRecentlyPushedSource`. Hits `/search/repositories?q=…
  pushed:>YYYY-MM-DD&sort=updated`. Eight curated query patterns
  (`mcp in:description`, `topic:mcp-server`, `topic:a2a`,
  `topic:agent-framework`, etc.). Defaults to a `pushed_within_days=30`
  window for the batch refresh and `=7` for request-time scout use.
  No-ops silently when `GITHUB_TOKEN` is unset.
* `apps/api/planmyagents_api/discovery/sources/vendor_rss.py` —
  `VendorRssSource`. Stdlib `xml.etree.ElementTree` (no `feedparser`
  dependency). Parses both RSS 2.0 and Atom shapes. Strips inline HTML
  before keyword matching. Parallelized with an 8-thread pool over the
  feed URLs (~5s wall for 13 feeds). Curated seed list at
  `packages/discovery/sources/vendor_rss_feeds.json` — 13 entries, each
  hand-verified live (200 OK with the polite User-Agent). Caught 32
  candidates on first run (hnrss.org's MCP keyword feed alone produced
  ~25 of them).
* `scripts/run_discovery_upkeep.py` wires all three into `make
  discovery-refresh` with `--no-hacker-news` / `--no-github-recently-pushed`
  / `--no-vendor-rss` opt-out flags.
* `apps/api/tests/test_hacker_news_source.py`,
  `test_github_recently_pushed_source.py`, `test_vendor_rss_source.py`
  — 19 unit tests, all mocked HTTP. End-to-end live integration via
  `make discovery-refresh` confirmed:

  ```
  source_id                count
  apis_guru                315
  official_mcp_registry    116
  vendor_rss               32
  …
  hacker_news_agent_watch  1
  total                    531
  ```

**Phase 2 — Request-time `ScoutDispatcher`.** Built on top of the new
event sources; reused all existing first-party sources as scouts.

* `apps/api/planmyagents_api/discovery/scouts.py` —
  - `Scout` dataclass: `scout_id`, `source`, `budget_seconds`,
    `requires_token` (env var name; missing token → silent skip).
  - `ScoutResult`: per-scout status (`ok`/`timeout`/`error`/`skipped`),
    elapsed_ms, error/skip reason. Carried through to the API response
    so the UI can render an honest scoreboard, not just merged candidates.
  - `ScoutDispatcher`: runs all scouts in parallel via
    `ThreadPoolExecutor` + per-future `result(timeout=…)`. Crucial
    detail: `executor.shutdown(wait=False)` so that a slow scout doesn't
    block the dispatch return after its budget expired (Python threads
    can't be killed; the orphan finishes in the background and exits).
    Layered budget: per-scout AND a global cap; whichever is smaller
    wins.
  - `default_scouts()` factory: 6 scouts (official_mcp_registry,
    apis_guru, hacker_news_agent_watch, vendor_rss, github_code_search,
    github_recently_pushed). Per-scout budgets calibrated to measured
    latency: 4s for HN, 6s for vendor_rss, 8s for official MCP registry
    (its cursor pagination is slow on a cold cache).
* `apps/api/planmyagents_api/discovery/query_expansion.py` —
  `expand_for_scouts(capability, task_text, chat_client)`. Asks the
  planner LLM to rewrite one capability gap into one search phrase per
  scout (each phrase tuned to that source — "Show HN: …" for HN,
  "topic:… OR …" for GitHub code search, etc.). If LLM unavailable or
  returns invalid JSON, falls back to a deterministic rule-based
  expander so the system stays functional offline. Strict JSON-only
  prompt; tolerates fenced-code wrapping; drops unknown scout ids and
  oversized strings from the LLM payload before passing to the
  dispatcher.
* `apps/api/planmyagents_api/web/app.py` — `_live_discovery(...)` wired into
  `_refusal_discovery`. Defaults ON (`PLANMYAGENTS_LIVE_DISCOVERY=true`),
  capped at 3 missing capabilities per request to bound latency.
  Persists newly-found candidates to the discovery store, computes a
  `freshly_discovered_at_request` flag per candidate (true iff the id
  wasn't in the local-store search results for this request). Returns
  a fully populated `live_discovery` block with per-capability scout
  scoreboards.
* `apps/web/src/app/goal/page.tsx` — new `LiveDiscoveryPanel`
  component. Shows headline counts (dispatched/persisted/freshly
  discovered), a per-capability collapsible with each scout's
  status/elapsed/cand-count tag, and the discovered candidate list with
  a green "FRESH" pill on candidates that came from this request.
* `apps/api/tests/test_scout_dispatcher.py`,
  `test_query_expansion.py`, `test_goal_live_discovery.py` — 18 new
  tests covering parallelism (3 × 0.4s scouts in <0.9s wall), timeout
  behavior (slow scout cancelled at budget without blocking fast scouts),
  error isolation (one scout raising doesn't kill the dispatch), token
  gating, dedupe, query-expansion JSON parsing/fallbacks, and the full
  /goal endpoint with mocked scouts.

**Live end-to-end measurement on 2026-05-11.** With the dev server
running and no chat client patched, calling the dispatcher directly with
`task_description="kyc verification mcp server"`, `capability=None`:

```
WALL: 7.4s | merged: 463 candidates
  github_code_search       skipped   (no GITHUB_TOKEN)
  github_recently_pushed   skipped   (no GITHUB_TOKEN)
  official_mcp_registry    ok    7352 ms   116 cand
  apis_guru                ok       0 ms   830 cand   (parallel completion)
  hacker_news_agent_watch  ok       0 ms     0 cand
  vendor_rss               ok       0 ms    31 cand
```

The /goal smoke test against the live server (with the local Qwen 35B
planner) showed live_discovery firing per missing capability with the
expected `live_discovery.status="ran"`, `scouts_dispatched=2`, and
`candidates_persisted=131`. The planner itself dominated wall time (35B
local model on a laptop is slow); the discovery side stayed inside its
budget.

**Total backend test count: 262/262 pass** (was 244 before this session,
244 → 262 adds the 18 new dispatcher / expander / goal-integration
tests). **Lint: clean.** **Frontend: typechecks clean.**

---

### What shipped on 2026-05-11 (evening — "Path A.2: APIs without agents + Open MCP opportunities")

The user explicitly chose Path A (only runnable agents/MCPs/A2A
qualify) and lit up the gap-as-signal positioning: *"I want A because
it also gives me or others an opportunity to see what type of agents or
mcp doesnt exists."* This session translates that positioning into
durable code: api_provider rows from APIs.guru are no longer rolled up
under "candidates", we always log the requested capability so the gap
isn't just rhetoric, and there's a public surface ranking the gaps.

**Naming decisions (locked).**

* **"APIs without agents"** — the underlying data label. Used in
  `_refusal_discovery`'s payload (`apis_without_agents` block) and in
  log/explanatory text.
* **"Open MCP opportunities"** — the public-facing page name and
  endpoint slug. Front of house only.

**Code shipped.**

* `apps/api/planmyagents_api/discovery/demand_store.py` —
  `DemandEvent`, `DemandSummary`, `JsonDemandStore`, `SqliteDemandStore`,
  `PostgresDemandStore`, and a `demand_store_for_path(...)` factory
  that mirrors the discovery-store factory. Schema: append-only
  `capability_demand_events` table (`id`, `capability_id`,
  `goal_excerpt`, `requester_hash`, `requested_at`, `has_local_match`,
  `has_apis_without_agents`, `sub_task_id`). `_summarize` aggregates
  by capability_id, ranked by request count desc, with the freshest
  goal excerpts kept as samples (we deliberately surface fresh asks,
  not week-1 fossils). PII-safe by construction: goal text truncated
  to 280 chars; requester hashed (sha256 short-prefix) so a public
  surface can never leak who asked.
* `apps/api/planmyagents_api/discovery/demand_recorder.py` —
  always-on, opt-out (set `PLANMYAGENTS_DEMAND_RECORDING_ENABLED=false`
  for tests). `record_refusal_demand(...)` is the single entry point;
  swallows every exception and never blocks `/goal`. Reads
  `PLANMYAGENTS_DEMAND_STORE_PATH` (defaults to
  `data/capability_demand_events.jsonl`).
* `apps/api/planmyagents_api/web/app.py` — `_refusal_discovery` rewritten to:
  - Partition `payload["results"]` into agentic vs `api_provider`.
  - Replace `candidates` with the agent-only list (so the planner UI
    no longer mixes raw OpenAPI specs next to real agents).
  - Surface a new `apis_without_agents` block with the count, a
    per-capability breakdown of the top-5 vendors, and a
    `/open-mcp-opportunities` link.
  - Add a top-level `total_agentic_candidates` field for the headline
    UI; keep `total_candidates` for engineering transparency.
  - Call `record_refusal_demand(...)` exactly once per request.
* `apps/api/planmyagents_api/discovery/open_mcp_opportunities.py` —
  `compute_open_mcp_opportunities(candidates, demand_summaries)` returns
  an `OpenMcpOpportunitiesReport`. Rules:
  - Capability counts as "agent-covered" if any AGENTIC_PROVIDER_TYPES
    candidate declares it (regardless of verification — this view is
    about *the universe of public agent code*, not our own benchmark
    coverage).
  - "API without agent" = `api_provider` row with at least one
    capability that isn't agent-covered.
  - Capability with demand but zero APIs is still listed (the deepest
    gap; the page calls this out explicitly).
  - Score = `demand_request_count + api_supply_count`. Linear, on
    purpose; tunable later when there's real data.
* `apps/api/planmyagents_api/web/app.py` — new `GET /open-mcp-opportunities`
  endpoint. Optional `capability=...` filter, `limit` 1-200 (default
  50). Returns the report payload with `methodology` text inline so the
  surface is self-explaining.
* `apps/web/src/app/open-mcp-opportunities/page.tsx` — public Next.js
  page rendering the ranked capabilities, per-capability vendor table
  (with OpenAPI spec links), demand totals, sample goal excerpts, and
  the methodology block. Server-rendered against the API; gracefully
  degrades to "API not reachable, run `make api`" if the backend is
  down. Linked from the global header next to "Search."
* `apps/web/src/lib/api.ts` — new `fetchOpenMcpOpportunities()` and
  matching TS types (`ApiWithoutAgent`, `OpenMcpOpportunityCapability`,
  `OpenMcpOpportunitiesResponse`).
* `.env.example` — documented `PLANMYAGENTS_DEMAND_STORE_PATH` and
  `PLANMYAGENTS_DEMAND_RECORDING_ENABLED`.

**Tests shipped (29 new, 291/291 total).**

* `apps/api/tests/test_demand_store.py` (15 tests) — event builder
  truncation/hashing/blank-id handling, JSON store roundtrip + sort,
  SQLite roundtrip, factory selection, recorder ON/OFF behavior,
  recorder swallow-on-failure path, no-op-with-empty-capabilities.
* `apps/api/tests/test_open_mcp_opportunities.py` (8 tests) —
  capability with APIs but no agent surfaces, capability with agent
  filtered out, capability with demand only and zero supply included,
  score ranking by combined demand+supply, max-APIs-per-capability
  cap, methodology text present, JSON shape stable.
* `apps/api/tests/test_open_mcp_opportunities_endpoint.py` (6 tests) —
  endpoint returns only uncovered capabilities, capability filter
  applied, methodology text inline, refusal payload keeps
  `api_provider` out of `candidates`, refusal includes
  `apis_without_agents` block, refusal records demand events.
* Existing `test_web_app.py` and `test_goal_live_discovery.py` updated
  to redirect demand store to a tempdir so tests don't pollute
  `data/`.

**Verification.**

* `python -m unittest discover -s apps/api/tests` → 291/291 pass
  (was 262/262, +29 new tests).
* `ruff check apps/api` → clean.
* `next build` → all 9 routes generate cleanly including the new
  `/open-mcp-opportunities`.

**What's intentionally NOT done in this session (and why).**

* No vocabulary normalizer for `capability_id` strings. Today the
  demand log will fragment ("real_estate_dubai" vs
  "real_estate_uae_dubai"). The rank-by-demand surface still works,
  but adjacent capabilities won't aggregate. Tracked as a separate
  effort because it requires offline LLM clustering against the full
  capability space.
* No automatic decay for stale demand events. A capability asked for
  100 times in week 1 still ranks above one asked for 50 times in week
  4. Fine for v1; revisit when there's > 6 months of data.
* No GitHub-stars / vendor-popularity boost on the API supply side.
  Two vendors offering an API for the same capability rank equally.
  Future signal we can pull from the existing `evidence_url` once we
  enrich it with stars / monthly downloads.
* No moderation queue on the public page. Today every `api_provider`
  row from APIs.guru appears. If APIs.guru ingests something
  inappropriate we inherit it. Acceptable for a developer-facing
  surface; revisit if/when this becomes a consumer-facing landing page.
* `LiveResearchBanner` cleanup (called out in the brutal audit) not
  done — the banner copy now correctly says we ran the local store
  search; live discovery payload is separately rendered. The
  cleanup-of-cleanup is cosmetic and didn't make this session's cut.

### Concrete deliverables (next sprint) — original list, now annotated

| # | Deliverable | Effort | Status |
|---|---|---|---|
| 1 | Define `Scout` protocol (`run(capability, constraints, budget_seconds)` → `ScoutResult`) | 0.5 day | **Done 2026-05-11** ✓ |
| 2 | `ScoutDispatcher` with parallelism + per-scout budget + merge/dedupe | 1 day | **Done — threads, not asyncio (cleaner for sync sources)** ✓ |
| 3 | Adapt `OfficialMcpRegistrySource` + `ApisGuruSource` + `GitHubCodeSearchSource` into scouts | 0.5 day | **Done — `default_scouts()` covers all 6** ✓ |
| 4 | New `WellKnownAgentCardScout` — fetch `/.well-known/agent.json` per vendor in parallel | 1 day | **Deferred** — superseded by HN / RSS / GitHub recently-pushed sources, which have far better recall on real announcements. The well-known scout remains a viable Tier-1 add when we have a curated vendor list of 50+ entries; today the existing `A2AAgentCardSource` covers the cases we know about. |
| 5 | Wire `ScoutDispatcher` into `/goal` (only fire for sub-tasks where local store has 0 hits) | 1 day | **Done — fires for missing_capabilities, capped at 3 per request** ✓ |
| 6 | Persist newly-discovered candidates with `discovered_at_request_id` | 0.5 day | **Done without a migration** — `freshly_discovered_at_request` is computed at response time by diffing scout output against the local-store result set. No new column. |
| 7 | Extend `/goal` response with per-sub-task `live_discovery` block | 0.5 day | **Done — see `_live_discovery()` in app.py** ✓ |
| 8 | Update goal page UI to show "freshly discovered" candidates with provenance pills | 1 day | **Done — see `LiveDiscoveryPanel` in goal/page.tsx** ✓ |
| 9 | Tests: scout-by-scout unit tests + dispatcher integration test + end-to-end goal test | 1 day | **Done — 18 new tests, 262/262 total** ✓ |

**Actual time: one session.** Original estimate was 6.5 eng days; reality
included the event-driven Phase 1 sources too (~3 sources + tests +
wiring + RSS curation + parallelization) which added ~2 days of
material. So the *revised* estimate is closer to 8.5 eng days of work
collapsed into one focused session.

### Known follow-ups identified during this session

* `freshly_discovered_at_request` semantics — currently true iff the
  candidate's `provider_id` wasn't in the local-store search results
  for this request. That's right for a "this user just discovered X"
  pill but doesn't distinguish "first time we ever saw X" from "we had
  X but it didn't match this query." If we want the stronger semantics,
  add a `discovered_at` ISO timestamp column to `discovery_candidates`
  and compare against `now() - 1h` at response time.
* APIs.guru returns the same ~830 candidates per dispatch — too noisy
  for the freshly-discovered list. Consider sampling / per-capability
  bucket caps (e.g., max 20 from any one source per dispatch).
* The local Qwen 35B planner dominates /goal wall time on a laptop. Not
  a discovery problem. If we want sub-10s p95 on /goal end-to-end, we
  switch to Groq for the planner (`PLANMYAGENTS_PLANNER=groq`); the
  discovery side already meets its budget.
* `vendor_rss` keyword filter is currently very tight — it surfaces
  ~32 candidates from 13 feeds. Loosening the keyword list (or running
  the LLM-expanded query against feed entries instead of a fixed
  keyword set) would likely 3× the recall. Not done in this session
  because it raises false-positive risk; track separately.

### Definition of done

- A goal whose capabilities are not in the local store (e.g.
  `crypto_payment_processing`) returns a plan that includes
  freshly-discovered candidates from at least 2 scouts within 10 seconds.
- The `/goal` response's `live_discovery` block honestly reports which
  scouts ran, which were skipped (and why — usually "no_key"), and
  per-scout latency.
- Newly-discovered candidates are visible in the local store on the
  next request without re-running the scouts (the index grows
  organically).
- Credibility classifier still refuses to mark anything from
  Tavily/Brave-class scouts as `publishable`.
- No scout adds >2s p95 to goal latency on its own.

### Out of scope (deliberately)

- **Building our own web crawler.** A general-purpose web crawler is the
  wrong investment for an *index of agents*. If a vendor doesn't show up
  in a registry, on GitHub, at a well-known URL, or via a vetted scout,
  that's the long-tail Tavily case — not a reason to build Scrapy.
- **LLM-driven scout selection.** Could be useful later (let Qwen decide
  which scouts to fire per sub-task) but adds latency + cost + opacity.
  Start with deterministic "fire all enabled scouts in parallel" and
  measure precision/recall before adding model-in-the-loop.
- **Per-vendor adapter generation.** The scouts return *candidates*, not
  *runnable adapters*. Routing still requires the existing
  benchmark/verification gates.

---

## Index Work — First-Party Crawler Coverage Track

> Added 2026-05-11. This track tees up Tier-1 discovery sources to replace
> the Tavily/Brave wrappers currently doing the work. See "Discovery source
> tiers" above for context. This is **index work** in the Index vs. Surfaces
> framing, so it lifts every surface (goal, categories, agents, search,
> leaderboards, export).

### Why this track exists

Audit on 2026-05-11 found that 5 of 8 `Live*Source` classes
(`LiveMcpRegistrySource`, `LiveA2AAgentCardSource`, `LiveOpenApiSpecSource`,
`LiveVendorDocsSource`, `LiveAgentMarketplaceSource`) all route through
`_search_family(provider, api_key, ...)` in `live.py` — i.e. they are
Brave/Tavily queries with different keyword presets, not registry crawlers.

The product positioning is "Agent Discovery Index." Depending on Tavily as
the primary discovery loop hollows that out. We need real first-party
sources that make GitHub the only third-party API in the loop.

### What's actually first-party today

- `GitHubCodeSearchSource` → `api.github.com/search/code` ✓
- `GitHubResearchSource` → `api.github.com` ✓
- `StaticDiscoverySource` → in-repo curated JSON (`packages/discovery/sources/`)
- `JsonDiscoverySource` → fetches user-configured JSON URLs
- `McpCatalogSource` → fetches user-configured MCP catalog URLs
- `A2AAgentCardSource` → fetches user-configured A2A directory URLs
- `WebDocDiscoverySource` → fetches user-configured vendor doc URLs
- `LiveUrlDirectorySource` → fetches user-configured URL lists

The latter five are "first-party" in the sense that they don't depend on
any third-party search vendor — but they need URLs to work, and right now
those URLs are unconfigured. That is exactly what this track fixes.

### Concrete deliverables

| # | Deliverable | Cost | Eng effort |
|---|---|---|---|
| 1 | Verify `apis.guru/v2/list.json` schema parses through `JsonDiscoverySource`; if not, add an adapter. Wire as a default value when `PLANMYAGENTS_DISCOVERY_JSON_SOURCES` is unset. Adds ~4,000 OpenAPI specs to the index. | $0 | 0.5 day |
| 2 | Verify Smithery's catalog JSON (`smithery.ai/api/servers` or equivalent) parses through `McpCatalogSource`; add adapter if shape differs. Adds the bulk of the public MCP server population. | $0 | 0.5 day |
| 3 | Wire `mcp.so` and `glama.ai` as additional MCP catalog locations. | $0 | 0.5 day |
| 4 | Build `WellKnownAgentCardSource` — given a list of vendor base URLs, fetch `/.well-known/agent.json` (the A2A discovery convention) and emit candidates. | $0 | 1 day |
| 5 | Hand-curate a `vendor_doc_urls.json` (top ~30 agent vendors' docs roots) and load via `WebDocDiscoverySource`. | $0 | 0.5 day (curation, not eng) |
| 6 | Demote the `Live*` Brave/Tavily wrappers to clearly-named `WebSearch*Fallback` sources, so the toggle name matches reality. | $0 | 0.5 day |
| 7 | Stretch: build `RegistryCrawlerSource` that politely fetches a real registry's HTML/JSON list page and follows pagination. First targets: Replit Agents, LangChain Hub. | $0 | 2 days |

**Total Tier-1 effort to genuinely outgrow Tavily for discovery: ~5 eng
days + 0.5 days curation. No new cash.**

### Definition of done

- `make env-check` shows the project running with `TAVILY_API_KEY` and
  `BRAVE_SEARCH_API_KEY` both empty, and discovery still grows the index
  on `make growth-pass` via Tier-1 sources.
- `discovery_candidates.source_id` distribution after one growth pass shows
  >70% of new candidates from Tier-1 sources (today it's near 0%).
- `live_research` field on `/goal` responses honestly reports which Tier-1
  sources ran and how many candidates each contributed.

### Out of scope for this track

- Building our own Common-Crawl-style web crawler. We're an *index of
  agents*, not a *web search engine*. If a vendor doesn't have a
  discoverable presence on GitHub, in a public registry, or at a
  well-known URL, that's a Tier-2 fallback case, not a reason to write
  a crawler.
- Replacing GitHub. GitHub IS first-party for the agent ecosystem and we
  intentionally lean on it.

---

## Leaderboard Surface — Credibility Depth Roadmap

> **Scope correction (2026-05-10).** An earlier draft of this section called
> itself the "Trust-Layer Credibility Roadmap" and read as if it were the
> whole company plan. It isn't. It is the **depth roadmap for one surface
> on the index — `/leaderboards/{capability}`**. The other surfaces
> (`/goal`, `/categories`, `/agents/{id}`, `/search`, future
> `/export?target=n8n`, future `/trust/{cap}` API) have their own depth
> roadmaps to be written when prioritised. The "trust layer" framing is a
> useful external pitch line; it is not a planning constraint.
>
> **Why deepen the leaderboard surface at all.** A leaderboard surface
> users can cite is the strongest distribution channel for the index — when
> a buyer or analyst links to `/leaderboards/web_scraping`, every other
> surface (`/goal`, `/search`, `/agents/{id}`) gets the inbound traffic
> for free. So leaderboard depth pays back broader index value. But it is
> *one* investment among several; see "What goes next" below for the live
> options.

### The three tracks, side by side

| Track | What changes weekly | Gating signal |
|---|---|---|
| **Index breadth/depth** (always-on) | Sources, ingestion pipelines, dedupe, OpenAPI/docs extraction, embeddings, freshness | None — keep growing the asset |
| **Other surfaces** (one at a time) | Planner execution, search ranking, n8n export, etc. | None — depth roadmaps written when prioritised |
| **Leaderboard surface** (depth-first, capability-by-capability) | One capability is hardened from `synthetic_only` → `publishable` per ~month, when this surface is the prioritised investment | `is_publishable(capability)` from `apps/api/planmyagents_api/benchmark/credibility.py` |

### Credibility ladder for each capability

| Status | What it means | UI signal |
|---|---|---|
| `synthetic_only` | Every ranking row is from a mock adapter; the page scores our scoring code, not the vendors. | Red `tag-danger` pill + banner |
| `smoke_test` | At least one ranking is real but the bar for cross-vendor comparison is not met (too few providers, too few samples). | Yellow `tag-warn` pill + banner |
| `developing` | Cross-vendor bar met; missing one publishability extra (freshness, holdout, breakdown). | Blue `tag-info` pill + banner |
| `publishable` | Defensible to put on Hacker News and stand behind every number. | Green `tag-success` pill, no banner |

Default thresholds (overridable via env): ≥ 3 real-adapter providers per
capability, ≥ 30 real samples on at least one provider, last real run
within 30 days.

### Persona walking the journey

**Maya** — founding engineer at a Series A SaaS company. Needs to verify
10,000 emails. Today she Googles vendor blogs, picks based on logo
recognition, hopes for the best.

| Stage | What Maya experiences | Does Maya pay us? |
|---|---|---|
| 0 (today + 3 days) | Lands on `/leaderboards/email_verification`, sees a red banner saying we have only synthetic data. Bounces. *Correct behaviour — we don't lie to her.* | No |
| 1 (~Week 3) | Page now shows 4 vendors with real cost-per-correct, honeypot catch rate, p95 latency, and a method note. She picks NeverBounce, integrates directly. Bookmarks the page. | No |
| 2 (~Week 7) | Needs scraping. Comes back to us on the same brand. Picks Firecrawl from `/leaderboards/web_scraping`. | No |
| 3 (~Week 10) | Sees a drift alert — NeverBounce regressed; their CTO publicly fixes it; Maya considers switching. | No |
| 4 (~Month 4-5) | Her CTO hears us mentioned on Latent Space. CTO emails her: "have you seen this PlanMyAgents thing?" She replies "yeah, been using their data for 3 months." | No |
| 5 (~Month 6) | Maya pitches CTO: instead of manual quarterly reviews, call PlanMyAgents Trust API. CTO signs up at $499/mo. | **Yes — first dollar** |

### Stage-by-stage scope (leaderboard surface only)

| Stage | Status | Length | Engineering | Content / data | Cash | Output |
|---|---|---|---|---|---|---|
| 0 — Truth audit | **shipped 2026-05-10** | 3 days | classifier + audit script + report script + UI banner + Make targets | First dated report at `reports/benchmark-credibility/{date}.md` | $0 | Honest baseline `0 publishable / 29` — keeps the leaderboard surface from over-claiming, **valuable regardless of which surface we deepen next** |
| 1 — First credible cell | sequenced when leaderboard is prioritised | ~2 weeks | 1.5 weeks (3-5 real OR mocked adapters, holdout module, per-difficulty UI, cost-per-correct field) | 30+ cases + method note + (real path only) honeypot/test pages we control | **mocks-first: ~$0**; real-adapter swap (Stage 1.5): **~$30-200** depending on tier choice (see vendor breakdown below) | One `developing`-grade leaderboard on mocks; `publishable` after Stage 1.5 |
| 2 — Three more cells | sequenced after Stage 1 | ~4 weeks | 1.5 weeks (each new adapter is a class) | Cases + method notes for 3 capabilities | mocks-first: ~$0; real swap: ~$200-600 across all three | 4 `publishable` leaderboards |
| 3 — Anti-gaming | sequenced after Stage 2 | ~3 weeks | 3 weeks (reliability dashboard, drift alerts, case rotation, RSS changelog) | — | optional red-team budget ~$2-4k | Reliability column + changelog feed |
| 4 — Third-party signal | continuous, low engineering | — | small repo cleanup | arXiv-grade methodology note; open-source the case format; analyst outreach | — | Citable artefact + analyst mention(s) |
| 5 — Trust API | sequenced after Stage 3 | ~4 weeks | 4 weeks (auth, billing, `/trust/{capability}` endpoint, vendor profile pages) | Pricing page; 3 customer contracts | — | First paying API customer for *this surface* |

**Calibrated Stage 1.5 vendor cash (verified 2026-05-10, see web search
notes):**

- `web_scraping` — Firecrawl free→$19/mo · Apify $5/mo free credit→$29/mo
  Starter · ScrapingBee 1,000 free credits→$49/mo · Browserbase $0-39/mo ·
  Bright Data pay-as-you-go $1.50/1K records, no minimum. **Frugal path
  ~$5-50; comfortable path ~$140-200.**
- `email_verification` — Kickbox 100 free→$10/1,000 · NeverBounce
  pay-as-you-go $0.008/credit · ZeroBounce $39 minimum 2,000 credits
  (forced) · Hunter already paid. Plus 3-5 honeypot `.com` domains
  ~$30-75. **Total ~$70-175.**

(An earlier draft of this section said "~$6-9k" — that was padded
guesswork; the verified numbers are an order of magnitude lower because
all five web-scraping vendors and three email vendors have real
pay-as-you-go or low-tier subscription paths.)

**Total to first paying customer for the leaderboard surface:** ~13
calendar weeks of focused engineering + content work *if* the leaderboard
surface is the prioritised investment the whole way. ~$200-700 cash
total. **Note: paying-customer revenue from this surface is not the only
or first revenue path** — the planner surface has its own usage-based
revenue path that can run earlier or in parallel; see surface-by-surface
plans when written.

### Stage 0 — what just shipped

Already in `main`:

| Asset | Location |
|---|---|
| Credibility classifier | `apps/api/planmyagents_api/benchmark/credibility.py` |
| Pydantic boundary model | `apps/api/planmyagents_api/web/models.py` (`CredibilityModel`) |
| Wiring into `/leaderboards` and `/leaderboards/{capability}` | `apps/api/planmyagents_api/web/app.py` |
| Audit CLI | `scripts/audit_ranking_sources.py` (`make audit-ranking-sources`) |
| Report CLI | `scripts/benchmark_credibility_report.py` (`make benchmark-credibility-report`) |
| First report | `reports/benchmark-credibility/{date}.md` (committed each run) |
| UI pill on leaderboard index | `apps/web/src/app/leaderboards/page.tsx` |
| UI banner on per-capability page | `apps/web/src/app/leaderboards/[capability]/page.tsx` |
| Tests | `apps/api/tests/test_credibility.py` + extended `test_web_app.py` |

Today's honest baseline (committed in
`reports/benchmark-credibility/2026-05-10.md`): **0 publishable, 29
synthetic** out of 29 capabilities with rankings. That number is the
starting line; Stages 1-3 are designed to move it.

### What this roadmap explicitly does NOT do

- It does **not** narrow the planner. The planner keeps handling any goal
  the LLM can decompose, regardless of whether the underlying capability is
  publishable.
- It does **not** gate discovery. New candidates keep landing in the store
  on every upkeep run.
- It does **not** require buying API keys for "every agent in the world."
  Per-cell coverage is 5-15 vendors max; the long tail stays in the
  discovery store as `not_started`, the same hybrid coverage every
  real-world rater uses (Gartner, Wirecutter, MLPerf, DB-Engines).
- It does **not** describe the whole company plan. It deepens the
  `/leaderboards/{cap}` surface specifically; other surfaces have their
  own depth roadmaps to be written when prioritised.
- It does **not** assume the leaderboard surface generates first revenue.
  The planner surface has its own usage-based revenue path that can ship
  earlier; if/when that's the prioritised play, the leaderboard depth
  stages here pause without losing Stage 0's already-shipped value.

---

## Sprint executed 2026-05-10 (evening) — Index pass + planner-surface polish

After the Index-vs-Surfaces reframe earlier in the day, the next-sprint pick
was "Index pass + small planner-surface improvements." Shipped:

| Change | Files | Why |
|---|---|---|
| `make index-pass` chain target | `Makefile` | Single command for `discovery-refresh + openapi-enricher + docs-extractor + verify-candidates`. Track A in one entry point. Live research stays opt-in via `run_discovery_research.py` so we don't accidentally burn API keys. |
| Cost preview on `/goal` plan | `apps/api/planmyagents_api/planner/cost_estimator.py`, `apps/api/planmyagents_api/web/app.py` | Reads cheapest *credible* (non-synthetic) ranking per capability and returns per-sub-task + total estimate on the plan response. Synthetic / mock prices are excluded by design — the preview either shows real numbers or honestly says "no real-adapter pricing." |
| Cost preview UI | `apps/web/src/app/goal/page.tsx` | New `CostPreview` block with per-sub-task line items and credibility notes. |
| Download JSON button | `apps/web/src/app/goal/page.tsx` | Turns any `/goal` response into a downloadable artefact (`planmyagents-goal-{stamp}.json`). Closes the "I planned/executed something but can't keep it" loop without needing a persistence layer yet. |
| Tests | `apps/api/tests/test_cost_estimator.py` (6) + extended `test_web_app.py` | Cost estimator unit tests + integration assertion that `/goal` exposes the field. |
| Note about gap I found | this section | The FastAPI `/goal` route already executes via `WorkflowExecutor` — earlier sprint plan said "wire execution" but it's been wired since the Phase 5 work. Verified by running the route end-to-end against the live store. The gap is now around persistence, audit logs, and partial-result UX, not the basic execute path. |

Index pass first run: ~5.7 minutes against ~70 candidates; refreshed
discovery, OpenAPI extracts, docs, verification snapshots. No live research
keys configured locally so Brave/Tavily/GitHub passes were skipped (by
design).

### Still genuinely missing on the planner surface

These are the items the cost-preview + JSON-download work *did not* close.
Listed honestly so the next sprint pick is grounded:

1. **Plan persistence + task history.** Today every refresh loses the work.
   Needs a `task_runs` table, `POST /goal` write path, `GET /history`
   endpoint, and a frontend list view.
2. **Provider-call audit log.** Each adapter invocation should write a
   structured record (provider, capability, request hash, response status,
   cost, latency) to a `provider_calls` table.
3. **CSV / artifact export of executed records.** JSON download exists; CSV
   needs a per-capability flattening rule (out of scope for this sprint
   because the rule is opinionated and benefits from real data first).
4. **Plan approval screen.** Currently "submit" jumps to "execute" via a
   checkbox. A two-step "review plan, then approve execution" flow would
   reduce surprise once cost previews carry real numbers.
5. **Speed.** Local Qwen 3.5 35B planner takes ~22s per call. Acceptable
   for a demo; painful for repeated use. Caching identical goals or
   switching to a Groq-hosted model behind an env flag are both reasonable
   next moves.

---

### What shipped on 2026-05-11 (late evening — "DB schema split: agents/MCPs vs APIs without agents vs run audit log")

The user asked: *"Can you refactor the db schema and separate agents/mcp
from mcp opportunities as separate tables and what is this
discovery_sources table?"* Both halves got an honest answer.

**`discovery_sources` triage.** The table existed in the postgres init
script but no application code ever read or wrote it. Confirmed via
exhaustive grep — zero `INSERT INTO`, zero `SELECT FROM`. The user
chose to wire it up as a real per-source / per-scout audit log instead
of dropping it. Renamed to `discovery_run_events` (the original schema
was thin and unused; the new one has `candidates_returned`, `elapsed_ms`,
`trigger`, `status` columns the operator dashboard actually needs). A
backward-compat `discovery_sources` view projects the legacy columns
so any pre-existing operator query keeps working.

**Schema changes (in `infra/postgres/init/001_planmyagents.sql`).**

* `discovery_candidates` is now agent-only. Enforced by
  `CHECK (provider_type IN ('mcp_server', 'a2a_agent', 'ai_agent'))`.
* `apis_without_agents` — new slim table for vendors with an API but no
  agent wrapper. Drops `will_fail`, `will_fail_reasons`,
  `verification_status`, `lifecycle_status`, `route_status`,
  `benchmark_status`, `adapter_module`, `required_env_vars`,
  `compatible_provider_ids`, `embedding`. All of those would be
  permanently false / empty / not-applicable for an unwrapped OpenAPI
  spec and just added noise. Includes `superseded_by_provider_id` so
  when somebody finally ships an MCP wrapping a known API we can mark
  the row as no-longer-an-opportunity.
* `discovery_run_events` — per-source audit log (replaces dead
  `discovery_sources` table; old name kept as a view).
* `capability_demand_events` — moved into the canonical postgres
  migration so a fresh `make db-up` provisions it. (`PostgresDemandStore`
  still runs `CREATE TABLE IF NOT EXISTS` on init as defense in depth;
  the schema in the two places matches exactly.)
* One-shot SQL migration at the end of `001_planmyagents.sql` lifts any
  legacy api_provider / payment_provider rows from `discovery_candidates`
  into `apis_without_agents` before the CHECK constraint goes on.
  Idempotent via `ON CONFLICT (dedupe_key) DO NOTHING`.

**Storage layer (in `apps/api/planmyagents_api/discovery/`).**

* `apis_without_agents_store.py` — new `ApiWithoutAgentRecord` model +
  `JsonApisWithoutAgentsStore` / `SqliteApisWithoutAgentsStore` /
  `PostgresApisWithoutAgentsStore`. The slim record rejects agentic
  provider types in `__post_init__` so the type system enforces the
  separation alongside the SQL CHECK.
* `record_from_discovery_candidate(candidate)` — the single conversion
  helper. Existing source adapters (`ApisGuruSource`, etc.) keep
  emitting `DiscoveryCandidate(provider_type="api_provider", ...)`; the
  storage facade does the routing. This kept the source diff size to
  zero and avoided a big-bang rewrite.
* `RoutingDiscoveryStore` (the new return type of `discovery_store_for_path(...)`) —
  wraps the agentic store + the apis-without-agents store. Same
  `.load()` / `.save([candidates])` contract as before, so no caller
  needed updating. Adds `.load_apis_without_agents()` and
  `.save_merge(candidates)`. The latter fixes a pre-existing latent bug:
  `_live_discovery` was calling `.save([only_new_candidates])` on a
  replace-semantic JSON / SQLite backend, silently wiping the local
  store on every /goal call. Postgres was upsert-semantic and unaffected.
  `save_merge` does load-merge-save so all backends behave consistently.
* `RoutingDiscoveryStore.load()` and `.load_apis_without_agents()` filter
  / fall back to legacy rows in the agentic store mid-migration, so the
  surface is correct even before the operator runs the migration script.
* `RoutingDiscoveryStore.migrate_legacy_non_agentic_rows()` — idempotent
  one-shot migration for SQLite / JSON dev environments. Returns the
  count migrated. Production postgres uses the SQL migration instead.
* New `make migrate-apis-without-agents` target +
  `scripts/migrate_apis_without_agents.py` CLI invoke it.

**Discovery run audit log (`run_log.py`).**

* `DiscoveryRunEvent` + three storage backends + a factory.
* `DiscoveryRunLogger` is a buffered, environment-driven logger.
  `DiscoveryRunLogger.default()` resolves config from
  `PLANMYAGENTS_RUN_LOG_ENABLED` and `PLANMYAGENTS_RUN_LOG_STORE_PATH`.
  Disabled in tests by default to avoid side-effect files.
* `DiscoveryRunLogger.record(...)` is a context manager that times the
  source call, records `status='ok'/'error'/'skipped'/'timeout'`,
  captures `candidates_returned`, and re-raises any exception so
  callers see it. Logger failures are *swallowed* (the audit log must
  never break discovery).
* Wired into `DiscoveryIndex.ingest_sources(...)` (trigger='batch') and
  `ScoutDispatcher.dispatch(...)` (trigger='scout'), so every source
  invocation across both pipelines produces one row.

**`compute_open_mcp_opportunities` refactor.**

* Old signature: `candidates: list[DiscoveryCandidate]` (mixed agentic
  and api_provider).
* New signature: `agentic_candidates`, `api_records`, separately. Caller
  loads each from the appropriate store. Adds support for
  `superseded_by_provider_id` — when an MCP wraps a known API the row
  is filtered out of supply.

**`/goal` refusal payload.**

* `payload["candidates"]` is filtered defensively — even if a
  `StaticDiscoverySource` emits `api_provider` rows into the in-memory
  index, the consumer filters them out before populating
  `agentic_results`. Belt-and-suspenders alongside the storage CHECK.
* The `apis_without_agents` block is built from
  `RoutingDiscoveryStore.load_apis_without_agents()` plus any inline
  api_provider rows from per-request source ingestion (converted to
  `ApiWithoutAgentRecord` via `_api_record_from_result_payload`).
* Demand events still recorded via `record_refusal_demand`.

**Tests (327 total, was 292; +35 new).**

* `test_apis_without_agents_store.py` — 21 cases covering model
  invariants, three backends, factory dispatch, routing facade
  behavior (save partitions; load filters; load-apis falls back to
  legacy rows; migration is idempotent; save_merge preserves
  existing rows).
* `test_discovery_run_log.py` — 14 cases covering event JSON round
  trip, three backends, factory dispatch, logger context-manager
  status transitions, logger error-safety (swallows store failures),
  and `default()` env-var honoring.
* Updated `test_open_mcp_opportunities.py` for the new signature.
* Updated `test_open_mcp_opportunities_endpoint.py` and other integration
  test fixtures to redirect `PLANMYAGENTS_RUN_LOG_STORE_PATH` and the
  demand-store path to a tempdir each test run, so nothing leaks into
  `apps/data/`.
* `test_discovery_index.py`, `test_scout_dispatcher.py`,
  `test_generalization_eval.py` set
  `PLANMYAGENTS_RUN_LOG_ENABLED=false` at module init for the same
  reason.
* `test_store_factory_accepts_postgres_urls` updated to assert on the
  wrapped `agentic_store` since the factory now returns the facade.

**QA results.**

* `327/327` backend tests passing (Python 3.14, ~1.5s).
* `ruff check apps/api` clean.
* `next build` clean (apps/web compiles, all 9 routes including
  `/open-mcp-opportunities` build).
* No side-effect files in `apps/data/` after a full test run.

**Honest non-goals on this pass.**

* Source adapters still emit `DiscoveryCandidate(provider_type="api_provider", ...)`
  — the slim record is built at the storage boundary. A future tightening
  pass can have the sources construct `ApiWithoutAgentRecord` directly,
  but that's a much larger change with no functional benefit today.
* `payment_provider` rows are co-located in `apis_without_agents` with
  api_provider rows. They share the "vendor with programmatic interface
  but no agent wrapper" property. If we ever need to distinguish them in
  the public surface, the `provider_type` column already preserves the
  distinction.
* `PostgresDemandStore` and `PostgresApisWithoutAgentsStore` were
  verified at the schema-shape level (the SQL in
  `001_planmyagents.sql` matches the `CREATE TABLE` statements the
  store classes run defensively on init). They were not exercised
  against a live postgres in this pass — Docker isn't running locally
  in this session. The dev SQLite + JSON backends, which have full
  test coverage, are the canonical integration check today.

---

## Operating Rule

Do not add another one-off provider adapter unless it is selected through the
discovery index and needed for the chosen workflow.

Local utilities, mocks, and fixtures may be used in tests, benchmarks, and local
development only. Mark them with `runtime_mode: local`, `test`, `fixture`, or
`mock`; the router must refuse them in normal product routing unless
`PLANMYAGENTS_ALLOW_DEV_PROVIDERS=true` is explicitly set.
