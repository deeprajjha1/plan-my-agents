# Agent Registry — v0 Specification

> This document is the **human-readable spec** for the current seed registry.
> The machine-readable equivalent lives at `packages/registry/agents.json`.
> The broader discovery/index architecture lives in `docs/agent-discovery-index.md`.

> **Pricing data is approximate as of May 2026.** Always re-verify on each vendor's pricing page before commitment. Vendors change pricing quarterly.

---

## 1. Role Of This Registry

The registry is not meant to be the whole universe of agents. It has two roles:

1. `agents`: active/routable providers that have adapters, credentials,
   benchmarks, and operational review.
2. `discovered_agents`: non-routable candidates discovered from user demand,
   source catalogs, MCP/A2A directories, AI-agent listings, or API docs.

For now, most seed entries are API providers because they are easy to benchmark.
That is a bootstrap choice, not the product identity. PlanMyAgents should index
MCP servers, A2A agents, and AI-native execution providers first whenever those
sources exist.

---

## 2. Promotion Criteria

Promote a discovered candidate into active `agents` only when:

1. **Capability fits the data extraction & enrichment vertical** (our beachhead)
2. **Prefer agentic providers first**: MCP servers, A2A agents, and AI-native services outrank plain APIs when quality and reliability are comparable
3. **Documented programmatic interface** (MCP/A2A/API; no scraping required to integrate)
4. **Pay-as-you-go pricing available** (no enterprise contract for v0)
5. **Reliable enough to benchmark repeatedly**
6. **Real differentiation from each other** (so the "router" actually has choices to make)

These 10 cover the 5 capability categories we benchmark in v0.

---

## 3. Capability categories (v0)

| ID | Capability | What it does | Providers we route to |
|---|---|---|---|
| `contact_enrichment` | Get verified contact details (name, title, work email, LinkedIn) for a person at a company | Apollo, Clearbit, People Data Labs |
| `email_verification` | Verify if an email address is deliverable + active | Hunter.io, Apollo |
| `web_scraping` | Extract structured content from arbitrary web pages | Firecrawl, Apify, Diffbot |
| `semantic_search` | Find relevant web content for a natural-language query | Tavily, Exa |
| `company_data_lookup` | Get firmographic + growth data on a company | Clearbit, Crustdata, Diffbot |

---

## 4. Seed API Providers

The providers below are seed/fallback providers. They are useful for early
benchmarks and execution tests, but they should not distract from building the
Agent Discovery Index.

### 4.1 Apollo
- **agent_id:** `apollo`
- **vendor:** Apollo.io
- **capabilities:** `contact_enrichment`, `email_verification`, `company_data_lookup`
- **pricing model:** per credit (1 credit ≈ 1 contact lookup)
- **approximate cost:** $0.04–$0.08 per contact (varies by plan; assume $0.05 in v0)
- **API base URL:** `https://api.apollo.io/v1`
- **auth:** API key in `X-Api-Key` header
- **rate limit:** ~100 RPM on standard plan
- **API quality:** Excellent (well-documented, stable)
- **PII handling:** vendor is GDPR-compliant; review their DPA
- **gotchas:**
  - Email addresses sometimes returned as `email_status: "guessed"` — must filter/flag in stitcher
  - Bulk endpoints exist but rate-limited harder than single-call
  - Some emails behind extra credit (catch-all detection)
- **sample request:**
```http
POST /v1/people/match HTTP/1.1
X-Api-Key: <KEY>
Content-Type: application/json

{
  "first_name": "Jane",
  "last_name": "Doe",
  "organization_domain": "acme.com",
  "reveal_personal_emails": false
}
```
- **sample response (success):**
```json
{
  "person": {
    "id": "abc123",
    "first_name": "Jane",
    "last_name": "Doe",
    "title": "CTO",
    "email": "jane@acme.com",
    "email_status": "verified",
    "linkedin_url": "https://linkedin.com/in/janedoe"
  }
}
```
- **test cases:** 50 (`packages/benchmarks/contact_enrichment/`)

---

### 4.2 Hunter.io
- **agent_id:** `hunter`
- **vendor:** Hunter.io
- **capabilities:** `email_verification`, `contact_enrichment` (via email finder)
- **pricing model:** per request
- **approximate cost:** $0.005–$0.015 per email find/verify
- **API base URL:** `https://api.hunter.io/v2`
- **auth:** API key as query param `api_key`
- **rate limit:** ~100 RPM on Starter plan
- **API quality:** Excellent (very stable, simple)
- **gotchas:** verifier returns `result: deliverable | undeliverable | risky | unknown` — `risky` should be flagged but not auto-discarded
- **sample request:**
```http
GET /v2/email-verifier?email=jane@acme.com&api_key=<KEY> HTTP/1.1
```
- **sample response:**
```json
{
  "data": {
    "result": "deliverable",
    "score": 95,
    "regexp": true,
    "gibberish": false,
    "disposable": false,
    "webmail": false,
    "mx_records": true,
    "smtp_server": true,
    "smtp_check": true,
    "accept_all": false
  }
}
```

---

### 4.3 Clearbit (now part of HubSpot)
- **agent_id:** `clearbit`
- **vendor:** Clearbit (HubSpot-owned)
- **capabilities:** `company_data_lookup`, `contact_enrichment`
- **pricing model:** per call
- **approximate cost:** $0.01–$0.05 per company lookup; $0.10–$0.30 per person lookup
- **API base URL:** `https://company.clearbit.com/v2/companies` (and `person.clearbit.com`)
- **auth:** API key Bearer token
- **rate limit:** 600 RPM
- **API quality:** Good (now HubSpot-owned; verify standalone API support and packaging before relying on it)
- **gotchas:** post-HubSpot acquisition, standalone API packaging may change; verify each endpoint is still active
- **sample request:**
```http
GET /v2/companies/find?domain=acme.com HTTP/1.1
Authorization: Bearer <KEY>
```

---

### 4.4 People Data Labs
- **agent_id:** `pdl`
- **vendor:** People Data Labs
- **capabilities:** `contact_enrichment`, `company_data_lookup`
- **pricing model:** per record
- **approximate cost:** $0.10–$0.30 per person enrichment (premium for verified emails)
- **API base URL:** `https://api.peopledatalabs.com/v5`
- **auth:** API key as query param `api_key`
- **rate limit:** ~10 RPM on standard; higher tiers available
- **API quality:** Good (deeper data than Apollo for some segments, especially eng/tech roles)
- **gotchas:**
  - Higher cost than Apollo, but better at finding emails for senior eng roles
  - Likelihood scores on every field — must surface in stitcher
- **sample request:**
```http
GET /v5/person/enrich?profile=https://linkedin.com/in/janedoe&api_key=<KEY> HTTP/1.1
```

---

### 4.5 Firecrawl
- **agent_id:** `firecrawl`
- **vendor:** Firecrawl (firecrawl.dev)
- **capabilities:** `web_scraping`
- **pricing model:** per page
- **approximate cost:** $0.001–$0.005 per page (depends on plan; assume $0.002)
- **API base URL:** `https://api.firecrawl.dev/v1`
- **auth:** API key Bearer token
- **rate limit:** ~30 concurrent crawls on Hobby; higher tiers
- **API quality:** Excellent (LLM-friendly markdown output is killer for stitching)
- **gotchas:**
  - JS-heavy sites work but cost slightly more
  - Has a `extract` mode that uses LLM to extract structured data — useful for benchmark
- **sample request:**
```http
POST /v1/scrape HTTP/1.1
Authorization: Bearer <KEY>
Content-Type: application/json

{
  "url": "https://acme.com/product",
  "formats": ["markdown", "links"]
}
```

---

### 4.6 Apify
- **agent_id:** `apify`
- **vendor:** Apify
- **capabilities:** `web_scraping` (via "actors" — pre-built scrapers for specific sites)
- **pricing model:** per actor run (varies wildly: $0.001–$0.10 per result)
- **API base URL:** `https://api.apify.com/v2`
- **auth:** API token
- **rate limit:** depends on actor + plan
- **API quality:** Good (many actors, but quality varies per actor)
- **gotchas:**
  - Hundreds of community actors — only whitelist a few high-quality ones (e.g., LinkedIn scrapers, Google Maps scraper, Twitter scraper) for v0
  - Some actors are slow (2–10 min per run)
- **specific actors to whitelist for v0:**
  - `apify/google-search-scraper` — Google SERP scraping
  - `clockworks/free-linkedin-scraper` — LinkedIn (use carefully, ToS-grey)
  - `compass/crawler-google-places` — Google Maps / business listings
- **sample request:**
```http
POST /v2/acts/<actor_id>/runs?token=<KEY> HTTP/1.1
Content-Type: application/json

{
  "queries": "EU HR tech SaaS",
  "maxResults": 100
}
```

---

### 4.7 Tavily
- **agent_id:** `tavily`
- **vendor:** Tavily
- **capabilities:** `semantic_search` (AI-native search optimized for RAG/agent use)
- **pricing model:** per search
- **approximate cost:** $0.005 per search (basic) — $0.015 (advanced)
- **API base URL:** `https://api.tavily.com`
- **auth:** API key in body or header
- **rate limit:** generous on paid; 1000/mo free
- **API quality:** Excellent (purpose-built for agentic use, structured output)
- **sample request:**
```http
POST /search HTTP/1.1
Content-Type: application/json

{
  "api_key": "<KEY>",
  "query": "Acme HR Tech recent product launches 2026",
  "search_depth": "advanced",
  "max_results": 5
}
```

---

### 4.8 Exa
- **agent_id:** `exa`
- **vendor:** Exa (formerly Metaphor)
- **capabilities:** `semantic_search` (embeddings-based semantic web search)
- **pricing model:** per request
- **approximate cost:** $0.005–$0.01 per search; +$0.001 per content fetch
- **API base URL:** `https://api.exa.ai`
- **auth:** API key in `x-api-key` header
- **rate limit:** ~30 RPM standard
- **API quality:** Excellent (different "find" semantics than Tavily — Exa is best for finding *similar* pages)
- **gotchas:**
  - `findSimilar` endpoint is the differentiator — useful for "find more companies like X"
  - Returns clean snippets + URLs
- **sample request:**
```http
POST /search HTTP/1.1
x-api-key: <KEY>
Content-Type: application/json

{
  "query": "EU B2B HR tech SaaS companies $5-50M ARR",
  "num_results": 20,
  "use_autoprompt": true
}
```

---

### 4.9 Crustdata
- **agent_id:** `crustdata`
- **vendor:** Crustdata
- **capabilities:** `company_data_lookup` (specifically: company growth metrics, headcount trends, hiring signals)
- **pricing model:** custom (typically $500–$2,000/mo subscription)
- **approximate cost:** treat as $0.01–$0.05 per data point for v0 routing math
- **API base URL:** `https://api.crustdata.com`
- **auth:** API token Bearer
- **rate limit:** depends on contract
- **API quality:** Good (proprietary data — eng team growth, dept size, etc.)
- **why include:** the only agent in v0 that surfaces *growth signals* (headcount trends, recent hires) — uncorrelated with other 9, so good for benchmark diversity
- **sample request:**
```http
POST /v1/screener/company HTTP/1.1
Authorization: Bearer <KEY>
Content-Type: application/json

{
  "filters": {
    "industry": "Software",
    "headcount": {"gte": 50, "lte": 500},
    "hq_country": ["DE", "FR", "NL", "UK"]
  }
}
```

---

### 4.10 Diffbot
- **agent_id:** `diffbot`
- **vendor:** Diffbot
- **capabilities:** `web_scraping` (Knowledge Graph–based extraction), `company_data_lookup`
- **pricing model:** per call
- **approximate cost:** $0.01–$0.05 per call (varies by API)
- **API base URL:** `https://api.diffbot.com`
- **auth:** API token as query param `token`
- **rate limit:** ~5 concurrent on standard
- **API quality:** Good (different model than Firecrawl — Diffbot uses ML to identify entities; better for highly-structured pages)
- **gotchas:**
  - Knowledge Graph queries are powerful but can be slow (1–3s)
  - Article API is the most reliable; Product/Article auto-detection can misfire
- **sample request:**
```http
GET /v3/article?token=<KEY>&url=https://acme.com/blog/launch HTTP/1.1
```

---

## 5. Seed Provider Capability Matrix

| Provider | contact_enrichment | email_verification | web_scraping | semantic_search | company_data_lookup |
|---|---|---|---|---|---|
| Apollo | ✅ primary | ✅ secondary | — | — | ✅ secondary |
| Hunter | ✅ secondary | ✅ primary | — | — | — |
| Clearbit | ✅ secondary | — | — | — | ✅ primary |
| People Data Labs | ✅ tertiary (high-quality) | — | — | — | ✅ tertiary |
| Firecrawl | — | — | ✅ primary | — | — |
| Apify | — | — | ✅ secondary | ✅ tertiary (Google SERP) | — |
| Tavily | — | — | — | ✅ primary | — |
| Exa | — | — | — | ✅ primary (similarity) | — |
| Crustdata | — | — | — | — | ✅ specialty (growth data) |
| Diffbot | — | — | ✅ tertiary | — | ✅ tertiary |

This gives the orchestrator real choices for early benchmarking, but this matrix
is not the discovery index. The discovery index should grow far beyond these
seed providers.

---

## 6. Onboarding checklist for adding a new provider (v1+)

When promoting a discovered provider into the active registry, follow this checklist:

- [ ] Provider has a documented programmatic interface (MCP, A2A, AI-agent API, or fallback REST API)
- [ ] Vendor offers PAYG or low-tier pricing ($500/mo or less to start)
- [ ] Vendor has GDPR-compliant DPA available
- [ ] Capability fits an existing benchmark category (or we add a new category with 50+ test cases)
- [ ] Implement `AgentAdapter` protocol in `apps/api/planmyagents_api/agents/<vendor>.py`
- [ ] Add 50 test cases to `packages/benchmarks/<capability>/`
- [ ] Run benchmark suite to seed `benchmark_runs` table
- [ ] Add to `packages/registry/agents.json`
- [ ] Add to capability matrix in this doc
- [ ] Smoke test 3 real customer tasks before enabling for production routing
- [ ] Document gotchas in vendor's section
- [ ] Add to public sub-processor list (GDPR transparency)

### Discovery candidates

> **Note:** since the schema split, discovery candidates live in the
> Postgres / SQLite **discovery store**, not in `packages/registry/agents.json`.
> The two tables are `discovery_candidates` (agentic only — `mcp_server` /
> `a2a_agent` / `ai_agent`, enforced by a `CHECK` constraint) and
> `apis_without_agents` (`api_provider` / `payment_provider`). See
> `docs/agent-discovery-index.md` §3.5. `discovered_agents` in
> `packages/registry/agents.json` is retained only as a small reviewed
> fixture for the seed bootstrap.

Unsupported user tasks add candidates to the discovery store. These entries
are demand signals, not active providers. They must remain non-routable with:

- `provider_type`: stored in `discovery_candidates` if `mcp_server` /
  `a2a_agent` / `ai_agent`, otherwise in `apis_without_agents`.
  The `RoutingDiscoveryStore` facade routes by this field at save time.
- `will_fail: true`
- `route_status: will_fail`
- `required_env_vars` populated when API credentials are required
- `benchmark_status: not_started` until a real benchmark suite exists

Promote a discovered candidate into active `agents` only after the checklist
above is complete.

Do not promote local heuristic utilities into active `agents`. A routable entry
must represent a real configured provider or callable agent/protocol endpoint.
If no such endpoint exists, the product should refuse rather than substitute a
local approximation. Test doubles must be marked with `runtime_mode: local`,
`test`, `fixture`, or `mock`; the router blocks them unless
`PLANMYAGENTS_ALLOW_DEV_PROVIDERS=true` is explicitly set.

Current implementation caveat: unsupported-task enrichment currently uses the
static discovery seed source, existing `discovered_agents`, and configured
JSON/MCP/A2A/AI-directory/web-doc file or URL sources. Dedicated upkeep jobs can
also run opt-in GitHub/URL research, but findings remain unverified and
non-routable. For travel-booking requests, Amadeus, Duffel, and Stripe appear
with no extra sources because they are seeded candidates for the missing
travel/payment capabilities, not because the system searched the whole provider
universe.

### Daily discovery/upkeep

Every day, PlanMyAgents should research the provider universe for unresolved
capabilities:

1. Search MCP registries and MCP server catalogs.
2. Search A2A Agent Cards and agent directories.
3. Search AI-native service catalogs.
4. Search plain API/vendor docs only as fallback.
5. Update `discovered_agents` with docs URLs, auth requirements, pricing, and
   capability hypotheses.
6. Re-benchmark active providers and flag better candidates for review.

Fresh discoveries never become routable directly. They remain `will_fail` until
credentials, adapters, benchmarks, and smoke tests are complete.

Current implementation finding: Amadeus and Duffel are useful fallback candidates
for travel workflows, and Stripe is a fallback payment candidate, but they are
API/payment-provider entries, not AI agents. If an MCP server, A2A agent, or
AI-native travel agent is found and performs better, it should outrank those
APIs during promotion.

---

## 7. Things to track per provider (operational)

For each agent we route to, monitor:
- **uptime** (status page integration where available)
- **API contract changes** (subscribe to vendor changelogs)
- **pricing changes** (quarterly manual review)
- **rate limit headroom** (alert at 80% of quota)
- **payment to vendor balance** (top up before exhaustion)
- **vendor relationship** (PoC name, account email)

These live in a separate ops doc (not committed) — `internal-ops/agent-vendor-tracker.md`.
