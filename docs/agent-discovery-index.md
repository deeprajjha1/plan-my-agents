# Agent Discovery Index

> This is the core product correction: PlanMyAgents is not primarily a wrapper
> around Hunter, Apollo, Firecrawl, or any small set of APIs. The long-term
> product is a continuously updated index of callable agents and agentic
> providers, with benchmarked routing and gated execution.

---

## 1. Why This Exists

Users will not know which agent, MCP server, A2A endpoint, AI-native service, or
fallback API can perform a subtask. PlanMyAgents should answer:

> Given this user goal, what callable agents/providers exist, which ones appear
> capable, which ones are executable today, which ones will fail, and which ones
> perform best based on benchmarks?

The current code has a registry and a small curated discovery catalog. That is
only a safety model and prototype. It is not yet a robust discovery engine.

---

## 2. Current Reality

Implemented:

- static registry at `packages/registry/agents.json`
- `discovered_agents` entries with `will_fail: true`
- local Qwen + Groq planner paths that identify missing capabilities, with
  deterministic-rules fallback
- registry enrichment for unsupported goals
- `apps/api/planmyagents_api/discovery/` package with source protocol, static source,
  JSON file/URL source, MCP catalog file/URL source, A2A Agent Card file/URL
  source, AI-agent directory file/URL source, web-doc manifest file/URL source,
  opt-in GitHub repo/code research, Brave/Tavily web search, MCP registry search,
  A2A Agent Card search, OpenAPI/spec search, vendor-doc search, agent
  marketplace search, configured live directory/spec URLs, normalizer, dedupe,
  local JSON/SQLite/Postgres stores, query expansion, in-memory index, and service layer
- **request-time scout dispatcher** (`scouts.py`) that runs official MCP
  registry, APIs.guru, Hacker News, vendor RSS, and GitHub
  code/recently-pushed scouts in parallel with per-scout time budgets and a
  global cap when `/goal` identifies capabilities that have no local match
- **physically separate tables** for agentic candidates vs APIs without
  agents (see §3.5 below); a `RoutingDiscoveryStore` facade partitions by
  `provider_type` at save time
- **discovery run audit log** (`discovery_run_events`) capturing every
  source/scout invocation with status, candidates returned, elapsed ms, and
  trigger (`goal_refusal` / `upkeep` / `manual`)
- **capability demand log** (`capability_demand_events`) recording every
  refused or unmet capability with truncated goal + hashed requester
- CLI search through `scripts/run_discovery.py`
- daily/local upkeep through `scripts/run_discovery_upkeep.py`
- broad research through `scripts/run_discovery_research.py`
- local recurring upkeep wrapper through `scripts/run_discovery_scheduler.py`
- curated source manifests with ~70 deduped non-routable candidates across MCP,
  A2A, AI-agent, API, and payment source types
- freshness/staleness metadata and stale-candidate filtering in search results
- promotion-readiness blockers per candidate
- per-capability gap reports for unsupported responses
- ranked workflow options for unsupported responses
- agent/MCP-only qualified-candidate policy in the demo report
- rejected fallback candidate reporting for excluded APIs/payment providers
- payment-to-booking compatibility checks before payment candidates are treated
  as best for a booking flow
- source-derived capability catalog for planner fallback
- hotel/lodging configured candidate coverage through source manifests
- source coverage metadata that states when live web/GitHub crawling was not run
- `/open-mcp-opportunities` surface that ranks capabilities where APIs
  without agents exist but no agentic wrapper does (joined with demand log)
- routing gate: discovered candidates are never executable

Important limitation:

- Blocked runtime requests now trigger live discovery for the planned or missing
  capabilities during the first three-month learning window. The path searches
  existing `discovered_agents`, curated/configured sources, live GitHub repo/code
  research, Brave/Tavily-backed connector families, and explicit research URLs
  where configured, then persists non-routable findings to the discovery store.
  That store can be local SQLite/JSON or Postgres via
  `PLANMYAGENTS_DISCOVERY_STORE_URL`. It does not yet do web-scale crawling,
  autonomous deep crawling, or embedding search.
- Promotion is explicit. A DB candidate must have verified evidence, an adapter
  module, passed benchmarks, `will_fail = false`, and
  `route_status = 'ready_for_promotion'` before registry generation or startup
  routing can include it.
- Generic protocol adapters cover scale across MCP, A2A, OpenAPI/API, and
  AI-agent candidates. They are emitted as `protocol_beta` providers and are
  blocked from production routing by default.
- Candidate matching is primarily capability overlap. The task description adds
  query expansion plus a text-ranking boost against candidate
  name/vendor/capability fields.
- Public web/company/marketing research prompts should not surface internal
  database MCPs such as Postgres/SQLite unless the user explicitly asks for SQL,
  Postgres, or internal database access.
- Generalization eval fixtures probe different task families, but they are not
  product routing rules. The product path remains capability inference, intent-fit
  ranking, discovery, verification, benchmark gates, and refusal/promotion.
- The local demo now treats only `mcp_server`, `a2a_agent`, and `ai_agent` as
  qualified workflow candidates. API and payment providers remain visible as
  rejected fallbacks, not as qualified winners. Agentic candidates also need
  verified public evidence that the specific capability exists before they can
  be qualified.
- For a flight-booking prompt, the current curated corpus has A2A candidates for
  `travel_search`, `fare_comparison`, and `payment_authorization`, but no
  qualified MCP/A2A/AI-agent candidate for `booking_execution`. Duffel is shown
  as a rejected API fallback under the current scope.
- For hotel/lodging prompts, the planner infers `lodging_search`,
  `lodging_comparison`, `booking_execution`, and `payment_authorization` through
  the source-derived capability catalog. The current configured sources include
  hotel API fallbacks, but no verified MCP/A2A/AI hotel-booking capability
  evidence. Live refusal discovery can enrich candidates, but a local miss is
  still not proof that no such agent exists.

Not implemented yet:

- web-scale crawling
- production-scale scheduled ingestion across MCP/A2A/OpenAPI/vendor/marketplace
  sources
- embeddings/search index
- production-grade entity resolution beyond basic domain/vendor dedupe
- capability extraction from docs
- broader synonym expansion beyond the current curated map
- hosted scheduled freshness checks
- benchmark scheduling per discovered agent
- large-scale storage/indexing for lakhs or millions of candidates

Partially implemented next:

- candidate evidence verification command and library
- benchmark run persistence for JSON/Postgres stores
- promotion can derive benchmark status from persisted benchmark runs

---

## 3. Discovery Sources

Search and ingestion should run in this priority order:

1. MCP server registries and MCP catalogs
2. A2A Agent Cards and agent directories
3. AI-native services with programmatic execution APIs
4. Agent marketplaces and public tool directories
5. GitHub repositories that expose MCP/A2A/tool servers
6. **Package registries (npm / PyPI / crates.io)** — where MCP servers actually originate as installable artifacts before they reach any aggregator
7. **Community-curated awesome-* lists** — fast-moving maintainer-judged signal
8. OpenAPI catalogs and vendor docs
9. Plain API providers as fallback

The point is not to immediately execute what is found. The point is to build a
searchable, trusted candidate index.

### 3.1 Active scouts (2026-05-20)

| Scout id | Channel | Population | Auth | Notes |
|---|---|---|---|---|
| `official_mcp_registry` | MCP — canonical | ~200–500 blessed servers | none | Anthropic's protocol-authors' catalogue |
| `mcp_marketplace` | MCP — aggregator | ~1,400 servers | none | Third-party security-graded directory |
| `smithery` | MCP — largest with API | ~5,000 servers | `SMITHERY_API_KEY` | Bearer token; silently skipped without |
| `glama` | MCP — largest overall | **~23,794 servers** | none | Cursor-paginated JSON API, no auth (2026-05-20) |
| `npm_mcp_packages` | Package registry | **~47,000 MCP-tagged packages** | none | Catches servers BEFORE they hit any aggregator. Rich corroboration signal (weekly downloads, dependents, npm composite score) (2026-05-20) |
| `github_awesome_lists` | Community curation | ~6 curated lists × 100–500 entries each | `GITHUB_TOKEN` | Parses awesome-mcp-servers / awesome-ai-agents READMEs (2026-05-20) |
| `github_code_search` | GitHub — content | massive | `GITHUB_TOKEN` | Finds repos containing protocol-specific code |
| `github_recently_pushed` | GitHub — recency | massive | `GITHUB_TOKEN` | Last-7-day window |
| `apis_guru` | OpenAPI | ~2,000 specs | none | Wrappable APIs (api_provider rows, not mcp_server) |
| `hacker_news_agent_watch` | News firehose | top + recent items | none | LLM-classified ("is this announcement actually an agent?") |
| `vendor_rss` | Vendor blogs | 362 curated feeds | none | LLM-classified |
| `moltbook` | Social network | invite-only beta | `MOLTBOOK_API_KEY` | Long-tail recall via author profiles |
| `curated_static` / `curated_mcp_catalog` / `curated_a2a_cards` / `curated_ai_agents` / `curated_web_docs` | Hand-vetted JSON | ~1,761 ground-truth entries | none | Always-on local seeds in `packages/discovery/sources/` |

**Why each new scout is its own source rather than a knob on an existing one:**

* **`glama` vs. `smithery` vs. `mcp_marketplace`:** different response shapes (Glama uses cursor pagination + namespace/slug pair; Smithery is `qualifiedName` + page/pageSize; Marketplace is `results` + page/limit). Distinct corroboration signals (Glama: `attributes` + `environmentVariablesJsonSchema`; Smithery: `isDeployed` + `useCount`; Marketplace: `securityScore` + `installCommand`). One file per source keeps the normaliser readable.
* **`npm_mcp_packages` vs. `mcp_marketplace`:** Marketplace exposes a curated `installCommand`; npm exposes the package directly with weekly downloads + dependents + npm composite score. The corroboration policies branch on those distinct signals.
* **`github_awesome_lists` vs. `github_code_search`:** code search finds repos that *contain* the literal text "model context protocol"; awesome-lists find repos that a human has *judged* worth listing. Independent recall populations and very different precision profiles.

### 3.2 Deferred channels (2026-05-20, logged for follow-up)

| Channel | Why deferred | What it'd take |
|---|---|---|
| **PyPI agent/MCP packages** | No clean request-shaped search API. Simple-index endpoint is 4 MB; XML-RPC search deprecated. | Cache-and-refresh pattern: pull `pypi.org/simple/`, prefix-filter (`mcp-*`, `agent-*`, `langchain-*`, `crewai-*`), persist to `packages/discovery/sources/`. Scout reads cache, fetches per-package metadata live for top-N matches. ~2,600 candidate packages today. |
| **mcp.so directory** | HTML-only; no public JSON endpoint. ~21,173 servers but Glama's 23,794 is a superset by sampling. | Brittle scraper or wait for them to publish an API. Revisit if Glama recall proves insufficient. |
| **Cursor Directory / Claude Skills / Cline marketplace** | Tier-2 in the original audit — partnership-aligned but separate work cycle. | One scout each; URL discovery + HTML parse for Cursor / Cline; API for Claude Skills if/when Anthropic publishes one. |
| **Hugging Face Spaces** | 500K Spaces; need to filter to agent/MCP-shaped ones via metadata tag. | HF API is free + well-documented; need a relevance filter on Space metadata + a polite poll cadence. |
| **Reddit / Product Hunt / YC batch pages** | Demand-signal flavour, not supply-signal. Different value proposition. | Each is a separate scout with LLM classification (Reddit is the highest-signal). |
| **Cloudflare Agents / Vercel AI directory / WorkOS Apps** | Small populations today; revisit as they grow. | One scout each, scrape-friendly. |

---

## 3.5 Storage Schema

The discovery store is **four physically separate tables**, not one.
Postgres enforces the split with a CHECK constraint; the
`RoutingDiscoveryStore` facade enforces it for SQLite/JSON dev stores.

| Table | Holds | Powers |
|---|---|---|
| `discovery_candidates` | `mcp_server`, `a2a_agent`, `ai_agent` only — CHECK constraint enforced | `/discovery/categories`, `/discovery/agents/{id}`, `/discovery/search`, the agentic block of `/goal` refusal payload |
| `apis_without_agents` | `api_provider`, `payment_provider` — vendors with an API but no agent wrapper, including `superseded_by_provider_id` once an agent appears | `/open-mcp-opportunities` (supply side), the `apis_without_agents` block of `/goal` refusal payload |
| `discovery_run_events` | Per-source / per-scout audit log (status, candidates returned, elapsed ms, trigger) | Observability: was the scout slow? did it skip? when did the official MCP registry last return anything? |
| `capability_demand_events` | PII-safe demand signals (truncated goal, hashed requester, source) | `/open-mcp-opportunities` (demand side), prioritising which APIs to wrap |

Why physical separation (not a discriminator column):

- The product positioning is **agents first**. An `api_provider` row showing
  up in `/agents/{id}` or `/discovery/categories` is a product bug, not a
  filter bug. The schema makes that class of bug impossible.
- `/open-mcp-opportunities` and `/goal` refusal payloads need both shapes
  but with different fields — `ApiWithoutAgentRecord` is a deliberately
  slimmer model than `DiscoveryCandidate` (no `will_fail`,
  `benchmark_status`, etc.) since "promote this API" is not a thing.
- Migration of legacy non-agentic rows is built into the Postgres
  migration (`INSERT ... SELECT` then `DELETE` then add CHECK) and into a
  one-shot CLI for local SQLite/JSON: `make migrate-apis-without-agents`.

See §4 of `docs/technical-architecture.md` for the SQL.

---

## 4. Discovery Pipeline

### Step 1: Query Understanding

For every unsupported user goal, the planner extracts:

- missing capabilities
- task category
- input/output expectations
- compliance/risk constraints
- whether the task needs real-world execution, payments, identity, or regulated data

The deterministic fallback should not grow one branch per user example. It builds
a capability catalog from discovered candidate metadata:

- candidate IDs and display names
- provider/vendor names and URLs
- capability IDs
- capability notes/descriptions

User terms are matched against that catalog. New domains should appear because
source ingestion found candidates with new capabilities, not because an engineer
patched `if "hotel" in prompt`.

### Step 2: Source Search

The discovery service searches source catalogs using capability terms and
expanded synonyms. Example for a travel task:

- `travel_search`
- `flight_search`
- `fare_comparison`
- `booking_execution`
- `MCP flight booking server`
- `A2A travel agent card`

Current v0 behavior is smaller than market-wide discovery: it searches the static
seed source plus any configured JSON/MCP/A2A/AI-directory/web-doc source files or
URLs, applies query expansion, matches by capability overlap, then ranks by
provider type and text match. The broad research job can additionally query
public GitHub and inspect explicitly supplied URLs, persisting findings with
evidence URLs as unverified candidates. This is useful for exercising the
lifecycle safely, but it is not yet a web-scale crawler.

### Step 3: Candidate Extraction

For each discovered candidate, extract:

- provider name
- provider type: `mcp_server`, `a2a_agent`, `ai_agent`, `api_provider`, `payment_provider`
- capabilities
- input schema
- output schema
- auth requirements
- pricing hints
- docs URL
- owner/vendor
- protocol
- rate limits
- compliance notes
- last seen timestamp

### Step 4: Normalization

Convert every candidate into a common registry schema. This must work for MCP,
A2A, AI-agent APIs, and plain REST APIs.

### Step 5: Deduplication

The same provider may appear in docs, GitHub, MCP directories, and marketplace
listings. Deduplicate by:

- canonical domain
- package/repo URL
- provider name
- endpoint URL
- semantic similarity of descriptions
- owner identity

### Step 6: Search Indexing

Store both structured fields and embeddings so runtime can ask:

> What candidates can satisfy `contact_enrichment` for EU B2B SaaS prospects?

Index requirements:

- keyword search over names/docs/capabilities
- vector search over task descriptions
- filters by provider type, lifecycle state, auth availability, benchmark status
- freshness scoring

### Step 7: Gated Promotion

Discovery never makes a provider executable. Promotion path:

`discovered` -> `candidate` -> `adapter_or_protocol_verified` -> `benchmarked` -> `configured` -> `routable`

The canonical provider registry should not grow with every unsupported user
query. Ad hoc demo discovery is non-persistent; scheduled upkeep persists
candidates to the local discovery store. The local implementation supports JSON
and SQLite stores; the production version should move this to Postgres plus
search indexes, while `agents.json` remains a small promoted-provider registry or
test fixture.

### Step 8: Workflow Option Ranking

Capability matches are not enough for real workflows. The demo now produces
ranked workflow options:

- qualified candidate types: `mcp_server`, `a2a_agent`, `ai_agent`
- excluded fallback types: `api_provider`, `payment_provider`
- rejected candidates remain visible with rejection reasons
- payment candidates must declare compatibility with the selected booking
  provider before they can be treated as best for `payment_authorization`
- flow score considers coverage, provider cohesion, compatibility blockers, and
  unresolved gaps

If API keys or OAuth setup are required, candidate remains:

- `will_fail: true`
- `route_status: will_fail`
- `required_env_vars` populated

---

## 5. Runtime Behavior

When a user submits a goal:

1. Qwen/Groq/rules planner decomposes the task into capabilities.
2. Router searches active routable providers first.
3. If none exist, router searches the **agentic** discovery index
   (`discovery_candidates` only) and triggers the scout dispatcher for
   capabilities still uncovered.
4. The response has two physically separate blocks:
   - `agentic_results` — qualified `mcp_server` / `a2a_agent` /
     `ai_agent` candidates, with promotion blockers and required env
     vars
   - `apis_without_agents` — supply signals from
     `apis_without_agents` plus any inline API results from the request
5. The current task is refused unless all required subtasks have routable
   providers. The refusal also writes one `capability_demand_events` row
   per missing capability and one `discovery_run_events` row per scout
   invocation.

Runtime must not call a freshly discovered candidate.

Surfaces that read this index:

| Surface | Reads | Notes |
|---|---|---|
| `POST /goal` | `discovery_candidates` + `apis_without_agents` + live scouts | Returns agentic and non-agentic blocks separately |
| `GET /discovery/categories` and `/discovery/categories/{cluster}` | `discovery_candidates` | Agent-only by design |
| `GET /discovery/agents/{provider_id}` | `discovery_candidates` + verification + benchmarks | Agent-only by design |
| `GET /discovery/search` | `discovery_candidates` (pgvector when wired) | Agent-only by design |
| `GET /leaderboards/{capability}` | `agent_rankings` + credibility classifier | Honest banner per leaderboard |
| `GET /open-mcp-opportunities` | `apis_without_agents` + `capability_demand_events` + `discovery_candidates` (to mark "covered") | The "gap as signal" surface |

Runtime responses should separate user-facing product output from internal
debugging. The user view should show:

- normalized intent, including typo/alias corrections when relevant
- per-capability gaps
- best discovered candidates for each missing capability
- exact blocker: credentials, adapter, benchmark, compliance, or protocol support
- next promotion step

The debug view can show raw planner JSON, trace IDs, timings, and discovery
ranking details — including per-scout `discovery_run_events` for the
request.

---

## 5.5 Benchmark-baseline firewall (post 16-May-2026 pivot)

Hand-written vendor adapters (Razorpay, Stripe, Resend, Firecrawl,
Shippo, eBay Browse, Hunter) live under
`apps/api/planmyagents_api/benchmark/baselines/` and are reachable
ONLY by the benchmark runner. They are NEVER part of the customer
routing path under the 16-May-2026 product spec.

This is enforced at four layers — any one alone would be sufficient;
the four-layer belt-and-braces design is deliberate so a sloppy edit
at one layer can't quietly weaken the firewall:

| Layer | Enforcement |
|---|---|
| Registry data (`packages/registry/agents.json`) | Each baseline entry carries `"is_benchmark_baseline": true` and `"adapter_module": ""` so the router's dynamic loader cannot fall back into them. |
| Routing code (`apps/api/planmyagents_api/agents/router.py`) | `ProviderRouter.route()` filters baselines out before they reach the executable / configured / discovered branches. `ProviderRouter.provider_for()` raises `PermissionError` if a baseline is requested by id. |
| Sandbox runtime (`apps/api/planmyagents_api/agents/sandbox_runner.py`) | The BYO-credentials sandbox builds adapters from the recipe step, bypassing the registry router entirely; it imports only from `planmyagents_api.agents.protocol`. |
| Test gate (`tests/test_provider_router.py::BenchmarkBaselineFirewallTest` + `tests/test_benchmark_baseline_firewall.py`) | Asserts the firewall both behaviourally (a configured baseline is never returned by `route()`) and structurally (no routing module imports `planmyagents_api.benchmark.baselines.*`). Sprint 4 T5 adds a CI lint to make the structural check part of every PR. |

If you find yourself wanting to add a hand-written wrapper for
customer execution, **stop and re-read the spec** (`BUSINESS_PLAN.md`
§7, `docs/ARCHITECTURE.md` §14). The answer is always: improve
discovery so we surface the vendor's official MCP server / OpenAPI
spec, then execute via `GenericMcpAdapter` / `GenericOpenApiAdapter` /
`GenericA2AAdapter` against credentials the user provides in the
sandbox UI. Hand-written wrappers exist only to score the
discovered providers against a known-good reference for benchmark
publication.

---

## 6. Daily Upkeep

A scheduled job should run daily:

- revisit unresolved missing capabilities from failed tasks
- crawl MCP/A2A/agent/API sources
- detect new candidates
- detect stale/dead providers
- refresh docs/pricing/auth metadata
- re-run benchmarks for active providers
- flag new candidates that may outperform current providers

The discovery index should become fresher every day, even without user traffic.

---

## 7. Scale Target

Near-term target:

- thousands of discovered candidates
- hundreds of normalized capabilities
- dozens of benchmarked/routable providers

Long-term target:

- lakhs of indexed agents/providers
- millions of listings/capability records if agent catalogs become large
- small routable subset gated by benchmark/configuration/compliance

The moat is not just the raw list. The moat is:

- normalized capability mapping
- benchmark results
- execution outcomes
- freshness data
- refusal and failure history
- routing decisions learned from real tasks

---

## 8. Immediate Implementation Plan

Next code milestone should be **discovery freshness and promotion readiness**,
not another API adapter.

Implement:

1. Local SQLite/Postgres-backed discovery index.
2. Scheduled upkeep wrapper around `scripts/run_discovery_upkeep.py`.
3. Broad GitHub/vendor-doc/open-web candidate research job.
4. Candidate normalizer improvements for source-specific metadata.
5. Deduplication key improvements for repo/package/endpoints.
6. Broader query expansion for synonyms and common typos.
7. Benchmark scheduling for candidates selected for promotion.
8. Promotion-readiness review UI for candidates.

Only after this exists should we add more execution adapters.
