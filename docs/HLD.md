# PlanMyAgents — High‑Level Design (HLD)

> **Version:** 2.0 (May 2026)
> **Scope:** Subsystem responsibilities, inter‑component contracts, sequence diagrams for primary flows, logical data model across all three product phases.
> **Read this after:** [ARCHITECTURE.md](ARCHITECTURE.md).
> **Read this before:** [LLD.md](LLD.md).
> **Companion docs for Phase 2/3:** [marketplace-design.md](marketplace-design.md), [partnership-strategy.md](partnership-strategy.md).
> **Audience:** Senior engineers integrating with the system, reviewers evaluating subsystem boundaries, future leads inheriting a subsystem.

---

## Table of contents

1. [Subsystem map](#1-subsystem-map)
2. [Discovery sources (L1)](#2-discovery-sources-l1)
3. [Normalization and storage (L2)](#3-normalization-and-storage-l2)
4. [Semantic layer (L3)](#4-semantic-layer-l3)
5. [Trust and verification (L4)](#5-trust-and-verification-l4)
6. [Optional execution (L5)](#6-optional-execution-l5)
7. [Ranking and recipe (L6)](#7-ranking-and-recipe-l6)
8. [Demand and feedback (L7)](#8-demand-and-feedback-l7)
9. [User surfaces (L8)](#9-user-surfaces-l8)
10. [Vendor portal (L9 — Phase 2)](#10-vendor-portal-l9--phase-2)
11. [Partner API (L10 — Phase 3)](#11-partner-api-l10--phase-3)
12. [Logical data model](#12-logical-data-model)
13. [Primary flows](#13-primary-flows)
14. [Cross‑cutting concerns](#14-cross-cutting-concerns)
15. [Deployment view](#15-deployment-view)

---

## 1. Subsystem map

```mermaid
flowchart TB
    subgraph L10[L10 - Partner API - Phase 3]
        PAPI[Partner API Gateway]
        SDK[OEM SDK]
    end

    subgraph L9[L9 - Vendor Portal - Phase 2]
        Vendor[Vendor Account]
        Claim[Claimed Profile]
        BenchCert[Benchmark Certification]
        Sponsor[Sponsored Placement]
        Leads[Lead Routing]
        DemandAPI[Demand Data API]
        FirewallAudit[Vendor-neutrality Audit]
    end

    subgraph L8[L8 - User Surfaces - Phase 1]
        Web[Next.js Frontend]
        API[FastAPI - /goal /search /benchmark /opportunities /recipe]
    end

    subgraph L7[L7 - Demand and Feedback]
        Gaps[Gap recorder]
        Demand[Capability demand ledger]
        Leaderboard[Public gap leaderboard]
    end

    subgraph L6[L6 - Ranking and Recipe]
        Partition[Provider partition]
        Ranker[Per-cap ranker]
        SponsoredRender[Sponsored renderer with disclosure]
        Recipe[Recipe generator]
    end

    subgraph L5[L5 - Optional Execution]
        Adapter[GenericProtocolAdapter]
        Spend[Spend ledger]
        Audit[Per-request audit]
    end

    subgraph L4[L4 - Trust and Verification]
        Tier[Trust tier ladder]
        Judge[LLM judge]
        Gate[Qualification gate]
        Probe[MCP tool probe]
        ClaimVerify[Vendor claim verification]
    end

    subgraph L3[L3 - Semantic]
        Cat[Capability catalog]
        Emb[Embedder]
        Idx[Capability index]
        Decomp[Goal decomposer]
        Recon[Label reconciler]
    end

    subgraph L2[L2 - Normalize and Store]
        Norm[Normalizer]
        Dedupe[Dedupe + merge]
        Store[(Discovery store)]
        MktStore[(Marketplace store)]
    end

    subgraph L1[L1 - Discovery Sources]
        Smithery
        MCPMkt[MCP Marketplace]
        OfficialMCP[Official MCP Registry]
        GitHub
        APIsGuru
        BraveTavily[Brave / Tavily]
        A2A[A2A directories]
        VendorDocs[Vendor docs]
        HN[Hacker News]
        Moltbook
        Curated[Curated JSON]
        VendorSelf[Vendor self-submission - P2]
        PartnerContrib[Partner contribution - P3]
    end

    L1 --> Norm
    Norm --> Dedupe
    Dedupe --> Store
    Vendor --> MktStore
    Claim --> MktStore
    BenchCert --> MktStore
    Sponsor --> MktStore
    Store --> Idx
    Emb --> Idx
    Cat --> Decomp
    Cat --> Recon
    Idx --> Recon
    Decomp --> Recon
    Recon --> Gate
    Store --> Gate
    Judge --> Gate
    Probe --> Tier
    ClaimVerify --> Tier
    Tier --> Dedupe
    Gate --> Partition
    Partition --> Ranker
    Ranker --> SponsoredRender
    Sponsor --> SponsoredRender
    SponsoredRender --> Recipe
    Adapter --> Spend
    Spend --> Audit
    Gate --> Gaps
    Gaps --> Demand
    Demand --> Leaderboard
    Demand --> DemandAPI
    Recipe --> API
    Recipe --> PAPI
    Leaderboard --> API
    Store --> API
    Store --> PAPI
    API --> Web
    PAPI --> SDK
    Sponsor --> FirewallAudit
```

---

## 2. Discovery sources (L1)

### 2.1 Responsibility

Pull provider candidates from the public agent ecosystem **and** (Phase 2+) accept vendor self‑submissions and partner contributions. Each scout owns one source and is otherwise independent.

### 2.2 Source inventory

| Scout module | Source | Type emitted | Trust tag |
|---|---|---|---|
| `smithery.py` | Smithery API | `mcp_server` | `registered_in_directory` |
| `mcp_marketplace.py` | MCP Marketplace | `mcp_server` | `registered_in_directory` |
| `glama.py` | Glama public JSON API (~23K MCP servers) | `mcp_server` | `registered_in_directory` |
| `official_mcp_registry.py` | Official MCP Registry | `mcp_server` | `registered_in_directory` |
| `npm_packages.py` | npm registry search, `mcp` tag (~47K packages) | `mcp_server` / `a2a_agent` | `registered_in_directory` (corroborated by `dependents > 0` or `downloads/week > 50`) |
| `github_code_search.py` | GitHub code search | `mcp_server` / `a2a_agent` | `unverified` (upgraded by probe) |
| `github_awesome_lists.py` | Curated GitHub `awesome-*` list READMEs (requires `GITHUB_TOKEN`) | `mcp_server` / `ai_agent` / `a2a_agent` | `community_listed` |
| `apis_guru.py` | APIs.guru directory | `api_provider` | `community_listed` |
| `brave_web.py` / `tavily_web.py` | Brave / Tavily web | `ai_agent` / `api_provider` | `unverified` |
| `a2a_directory.py` | A2A vendor directories | `a2a_agent` | `known_provider` |
| `vendor_docs.py` | Vendor docs scrape | `ai_agent` | `known_provider` |
| `hacker_news.py` | HN show/launch | `mcp_server` / `ai_agent` | `unverified` |
| `moltbook.py` / `agentguild.py` | Community curators | `ai_agent` | `community_listed` |
| `curated_*.json` | Seed manifests | mixed | `known_provider` |
| `ai_directories.py` | AI directories | `ai_agent` | `community_listed` |
| **`vendor_self_submission.py`** *(Phase 2)* | Vendor portal claim flow | any | `known_provider` after claim verification |
| **`partner_contribution.py`** *(Phase 3)* | Partner host that contributes their own catalog | any | `community_listed` by default |

### 2.3 Common scout contract

```python
@runtime_checkable
class Scout(Protocol):
    name: str
    requires: list[str]            # env vars required
    rate_limit: ScoutRateLimit

    async def fetch(self, query: ScoutQuery) -> list[RawCandidate]: ...
```

### 2.4 Failure isolation

A scout failing must never block dispatch. Dispatcher runs all scouts in parallel with per‑scout timeouts; failures logged and reported, never propagated.

---

## 3. Normalization and storage (L2)

### 3.1 Responsibility

Take heterogeneous `RawCandidate` records and produce a canonical `DiscoveryCandidate` shape. Persist with dedupe; preserve highest trust tier on merge. (Phase 2+) Also persist vendor accounts, claimed profiles, benchmark certifications, sponsored placements, and partner integrations in a separate `marketplace_store`.

### 3.2 Components

- **`candidate_normalizer.py`** — `RawCandidate` → `DiscoveryCandidate`
- **`dedupe.py`** — merge with `_best_verification_status`
- **`discovery_store/`** — Postgres / SQLite backend for agent index
  - `discovery_candidates`, `apis_without_agents`, `discovery_run_events`, `capability_demand_events`
- **`marketplace_store/`** *(Phase 2)* — separate Postgres schema for vendor‑side data
  - `vendors`, `claimed_profiles`, `benchmark_certifications`, `sponsored_placements`, `lead_routing_subscriptions`, `demand_data_subscriptions`, `vendor_audit_events`
- **`partner_store/`** *(Phase 3)* — separate Postgres schema for partner integrations
  - `partner_integrations`, `partner_api_keys`, `partner_audit_events`

### 3.3 Why physically separate stores

| Store | Why isolated |
|---|---|
| Discovery store | Hot path reads; vector index; high read QPS |
| Marketplace store | Different access pattern (vendor self‑serve write‑heavy); blast‑radius isolation; vendor‑neutrality audit needs isolated tables |
| Partner store | Per‑partner quota accounting; isolated rate limit DB; can be sharded per partner if needed |

### 3.4 Canonical `DiscoveryCandidate` shape

```python
@dataclass
class DiscoveryCandidate:
    id: str
    provider_type: Literal["mcp_server", "a2a_agent", "ai_agent", "api_provider"]
    display_name: str
    vendor: str
    vendor_url: str | None
    capabilities: list[CapabilityClaim]
    verification_status: VerificationTier
    docs: ProviderDocs
    evidence_url: str | None
    metadata: dict[str, Any]
    embedding: list[float] | None
    last_seen_at: datetime
    lifecycle_status: Literal["active", "dormant", "rejected"]
    sources: list[str]
    # Phase 2 additions (computed at read-time from marketplace_store):
    claimed_profile_id: str | None  # if vendor has claimed
    is_sponsored: bool              # set ONLY by sponsored-placement engine
    benchmark_certification: BenchmarkCert | None
```

---

## 4. Semantic layer (L3)

Unchanged from v1; this is the proprietary engine.

### 4.1 Responsibility

Bridge natural language ↔ structured capability catalog. Four capabilities:

1. Embed any text into a vector
2. Find capability matches by cosine similarity
3. Decompose a goal into sub‑tasks (LLM)
4. Reconcile free‑text capability slugs against the catalog

### 4.2 Components

- **Capability catalog** (`packages/registry/`) — 26 routable + open‑ended labels
- **Embedder** (`discovery/embedders.py`) — OpenAI / Ollama / DeterministicHash
- **`CachedEmbedder`** — LRU cache
- **Capability index** (`discovery/capability_index.py`) — pgvector cosine
- **Goal decomposer** (`planner/goal_decomposer.py`) — LLM with capability_descriptions context
- **Label reconciler** (`planner/label_reconciler.py`) — semantic slug canonicalization

### 4.3 Key invariant

A capability slug returned by the semantic layer is either (a) a member of the routable catalog or (b) a registered `CapabilityLabel` — never an arbitrary LLM hallucination that survives to the recipe.

---

## 5. Trust and verification (L4)

### 5.1 Responsibility

Decide which candidates are trustworthy enough to recommend, elevate candidates via accumulated evidence, **and (Phase 2) verify vendor ownership claims**.

### 5.2 Components

- **Trust tier ladder** — `_best_verification_status` in `dedupe.py`
- **LLM candidate judge** (`discovery/candidate_judge.py`)
- **Qualification gate** (`discovery/gaps.py`) — `QUALIFIED_VERIFICATION_STATUSES`
- **MCP tool probe** (`discovery/post_goal_refresh.py`)
- **Vendor claim verification** *(Phase 2)* (`marketplace/claim_verification.py`)
  - DNS TXT record polling
  - Domain‑hosted email verification
  - Result: claim tier (`claim_verified_dns` > `claim_verified_email` > `claim_pending` > `unclaimed`)
  - **Claim does NOT promote `verification_status`** by itself — claim and capability‑verification are independent ladders

### 5.3 Contract with L6 (Ranking + Recipe)

L4 produces candidates with `(verification_tier, judge_decision, claim_tier)` annotations. L6 consumes:
- Only `qualified` candidates may be recommended as best‑fit
- Claim tier shows as a separate "Claimed" badge in UI; not part of the ranking weight
- Unqualified candidates exposed in `/search` (with badge) but never in recipe

---

## 6. Optional execution (L5)

Unchanged from v1.

### 6.1 Responsibility

When the user opts in to BYO‑credentials sandbox execution, this layer runs a recipe step against the live MCP / A2A / OpenAPI endpoint **without storing credentials**.

### 6.2 Components

- **`GenericProtocolAdapter`** (`agents/protocol.py`) — MCP / A2A / OpenAPI adapters
- **Spend ledger** (`agents/spend_ledger.py`) — per‑request + per‑day + per‑provider caps
- **Per‑request audit** — append‑only log; args hash never plaintext

### 6.3 Contract with the user session

Credentials live only in browser session; passed per request in headers; never persisted server‑side; cleared from process memory immediately after call.

---

## 7. Ranking and recipe (L6)

### 7.1 Responsibility

For each sub‑task in a decomposed goal, pick the best qualified candidate. Synthesize a workflow. Produce an exportable recipe only when the step is actually runnable; otherwise produce a diagnostic plan with blockers. **(Phase 2)** Render any active sponsored placements in a clearly disclosed, structurally separate section.

### 7.2 Components

- **Provider partition** (in `web/planning.py`) — executable / configurable / no_provider
- **Per‑capability ranker** — cosine × verification × benchmark × freshness × cost (NEVER any vendor‑payment input — P11)
- **Workflow option synthesizer** (`planner/workflow_options.py`)
- **Recipe generator** — claude_desktop_json / n8n_json / cursor_prompt / markdown / cli
- **Sponsored placement renderer** *(Phase 2)* (`marketplace/sponsored_placement.py`)
  - Reads active sponsorships from marketplace store
  - Injects in dedicated "Sponsored" section
  - Always below natural #1
  - Disclosure metadata in API response

### 7.3 The vendor‑neutrality firewall (architectural)

```python
def rank_candidates(...) -> list[RankedCandidate]:
    # ⛔ This function MUST NOT import from marketplace.* modules.
    # ⛔ CI lint enforces this.
    score = (
          weights.cosine * cosine_sim
        + weights.verification * tier_weight
        + weights.benchmark * bench_score
        + weights.freshness * freshness
        + weights.cost_penalty * (1 - cost_norm)
    )
    # NO sponsorship / claim / vendor-payment input. Period.
```

```python
def render_with_sponsored_section(
    natural_ranking: list[RankedCandidate],
    sponsored_pool: list[SponsoredPlacement],
) -> CategoryView:
    return CategoryView(
        sponsored=[sponsored_pool[0]] if sponsored_pool else [],  # max 1
        natural=natural_ranking,  # natural #1 stays #1
        disclosure_note="Sponsored placements are paid promotions...",
    )
```

### 7.4 Contract with L8 (Surfaces) and L10 (Partner API)

`/goal` response carries `plan.sub_tasks` + `workflow_options` + recipe artifacts. `/partner/v1/recipe` returns the partner‑formatted recipe directly. Both go through the same ranker + sponsored renderer.

---

## 8. Demand and feedback (L7)

### 8.1 Responsibility

Convert every refusal into a measurable demand signal; surface aggregate signals as the public gap leaderboard **and** as the Demand‑Data API (Phase 2).

### 8.2 Components

- **Gap recorder** (`discovery/gaps.py`)
- **Capability demand ledger** — `capability_demand_events` table
- **Public gap leaderboard** — `/open-mcp-opportunities`
- **Demand‑Data API** *(Phase 2)* — `/vendor/demand-data` with K‑anonymity aggregation thresholds

### 8.3 Privacy invariants

- Goal text never persists past 30 days in plaintext
- Only capability slug + hash in demand events
- Demand‑Data API enforces K‑anonymity (no segment exposed at K < 100)
- Re‑identification attempts are TOS violations with legal teeth

---

## 9. User surfaces (L8)

### 9.1 Backend API (FastAPI)

| Endpoint | Method | Purpose |
|---|---|---|
| `/goal` | POST | Goal → decomposed plan + recipe |
| `/discovery/search` | GET | Free search over the index |
| `/discovery/candidates/{id}` | GET | Per‑candidate detail |
| `/discovery/refresh` | POST | Trigger async refresh (admin) |
| `/benchmark/{capability}` | GET | Benchmark data (paid for full) |
| `/open-mcp-opportunities` | GET | Public gap leaderboard |
| `/recipe/export?goal_id=...&format=...` | GET | Download recipe |
| `/disclosure` | GET | Live list of active sponsorships + verified benchmark holders + founding vendors |
| `/health`, `/ready` | GET | Orchestrator probes |
| `/admin/*` | varies | Admin / observability |

### 9.2 Frontend (Next.js)

| Path | Page | Backend dependency |
|---|---|---|
| `/` | Cluster index | aggregated candidates |
| `/categories/{cluster}` | Cluster detail + sponsored section | `/discovery/search` + sponsored API |
| `/agents/{provider_id}` | Per‑agent profile (with "Claimed" badge if applicable) | `/discovery/candidates/{id}` |
| `/search` | Free search UI | `/discovery/search` |
| `/leaderboards/{capability}` | Benchmark leaderboards | `/benchmark/{capability}` |
| `/open-mcp-opportunities` | Public gap leaderboard | `/open-mcp-opportunities` |
| `/goal` | Goal → recipe interactive UI | `/goal` POST |
| `/disclosure` | Vendor‑neutrality transparency page | `/disclosure` |

---

## 10. Vendor portal (L9 — Phase 2)

### 10.1 Responsibility

Vendor‑facing self‑serve product. Vendors claim listings, edit profiles, request Verified Benchmarks, create sponsored placements, subscribe to lead routing and Demand‑Data API.

### 10.2 Deployment

Separate Next.js app at `vendor.planmyagents.com`. Separate backend deployment (same FastAPI codebase but with `vendor_*` routes only enabled). **Reason: blast‑radius isolation** — a vendor‑portal bug must never take down user‑facing surfaces.

### 10.3 Components

| Module | Responsibility |
|---|---|
| `marketplace/vendor_signup.py` | Account creation (Clerk + 2FA) |
| `marketplace/claim_verification.py` | DNS / domain‑email ownership proof |
| `marketplace/profile_editor.py` | Claimed profile CRUD; auto + manual moderation pipeline |
| `marketplace/benchmark_request.py` | Verified Benchmark request flow; methodology agreement; right‑of‑reply |
| `marketplace/sponsored_placement.py` | Sponsorship CRUD; disclosure injection; firewall audit |
| `marketplace/lead_routing.py` | Opt‑in lead notification; anonymized inbox |
| `marketplace/demand_data_export.py` | K‑anonymity aggregation; subscription tier enforcement |
| `marketplace/audit.py` | All vendor actions → `vendor_audit_events` |
| `marketplace/billing.py` | Stripe portal handoff |
| `marketplace/rbac.py` | Vendor account hierarchy (Owner / Editor / Billing / Read‑only) |

### 10.4 API surface

| Endpoint | Method | Purpose |
|---|---|---|
| `/vendor/signup` | POST | Create vendor account |
| `/vendor/claim/{candidate_id}` | POST | Initiate ownership claim |
| `/vendor/claim/{candidate_id}/verify` | POST | Verify DNS or email |
| `/vendor/profile/{candidate_id}` | GET / PUT | Read / write claimed profile |
| `/vendor/benchmark/request` | POST | Request Verified Benchmark |
| `/vendor/benchmark/{cert_id}/dispute` | POST | Right‑of‑reply submission |
| `/vendor/sponsor` | POST / DELETE | Create / cancel sponsored placement |
| `/vendor/sponsor/{sponsorship_id}` | GET / PUT | Read / update sponsorship |
| `/vendor/leads` | GET | List inbound leads |
| `/vendor/leads/{lead_id}/respond` | POST | Respond via anonymized inbox |
| `/vendor/demand-data` | GET | Query demand signal data (subscriber tier) |
| `/vendor/execution-fee/opt-in` | POST | Opt into tier‑8 execution fee (signs rate‑cap addendum); rate ≤ 2% |
| `/vendor/execution-fee/status` | GET | Current opt‑in status, rate, charges this period |
| `/vendor/execution-fee/opt-out` | POST | Opt out (immediate; charges already accrued still billed) |
| `/vendor/billing` | GET | Stripe portal handoff |
| `/vendor/audit` | GET | Vendor's own audit log |
| `/vendor/team` | GET / POST / DELETE | RBAC team member management |

### 10.5 Contract with L4 (Trust) and L6 (Ranking)

- Vendor claim verification updates `claim_tier` (separate from `verification_status`)
- Vendor‑edited profile fields are visible in UI; ranker ignores them
- Sponsored placements are read by L6 renderer; not by ranker

### 10.6 Firewall audit (P11 enforcement)

```python
# Runs nightly
async def firewall_audit():
    sponsorships = sponsored_placements.fetch_active()
    for sp in sponsorships:
        natural_rank = ranker.position_for(sp.candidate_id, sp.capability)
        if natural_rank == 1 and sp.was_displayed_above_natural_1:
            raise P0Incident("vendor_neutrality_breach", sp)
    # Also: assert ranker.py imports do not include any marketplace.* module
    assert_no_marketplace_imports_in_ranker()
```

---

## 11. Partner API (L10 — Phase 3)

### 11.1 Responsibility

Programmatic access to the engine, packaged for third‑party hosts (n8n, Cursor, Cline, Continue, Claude Desktop, AWS Bedrock, etc.).

### 11.2 Deployment

Dedicated API gateway (Kong / Tyk / managed service) at `api.planmyagents.com/partner/v1/*`. Per‑partner authentication, rate limit, and usage telemetry. Same underlying engine as user‑facing routes.

### 11.3 Components

| Module | Responsibility |
|---|---|
| `partner/gateway.py` | API key auth; per‑partner rate limiting; usage telemetry |
| `partner/recipe_export.py` | Goal → partner‑formatted recipe |
| `partner/search.py` | Partner‑formatted search |
| `partner/recommend.py` | One‑shot best‑agent recommendation |
| `partner/benchmark_widget.py` | Embeddable benchmark comparison |
| `partner/feedback_ingest.py` | Callback ingest → `benchmark_runs` |
| `partner/demand_export.py` | Demand signal data for partner |
| `partner/audit.py` | All partner API calls → `partner_audit_events` |
| `partner/sdk_ts/` | TypeScript SDK |
| `partner/sdk_py/` | Python SDK |

### 11.4 API surface

| Endpoint | Method | Purpose |
|---|---|---|
| `/partner/v1/search` | GET | Discovery search (partner‑formatted) |
| `/partner/v1/recipe` | POST | Goal → partner‑formatted recipe |
| `/partner/v1/recommend` | GET | One‑shot best‑agent recommendation |
| `/partner/v1/benchmark/{capability}` | GET | Benchmark widget data |
| `/partner/v1/feedback` | POST | Execution result callback |
| `/partner/v1/demand` | GET | Demand signal data |
| `/partner/v1/health` | GET | Partner‑facing health endpoint with SLO indicators |

### 11.5 Authentication

```http
POST /partner/v1/recipe HTTP/1.1
Authorization: Bearer pma_partner_v1_n8n_...
X-Partner-ID: n8n
X-Partner-Version: 1.0.4
```

API keys scoped by:
- Endpoint allowlist (different partners have different scopes)
- Rate limit (per‑partner QPS quota)
- Usage telemetry (billed if applicable)

---

## 12. Logical data model

### 12.1 Entity overview

```mermaid
erDiagram
    DiscoveryCandidate ||--o{ CapabilityClaim : "has"
    DiscoveryCandidate ||--o{ DiscoverySource : "seen-in"
    DiscoveryCandidate ||--o{ JudgeDecision : "judged-as"
    DiscoveryCandidate ||--o{ ProbeResult : "probed-as"
    DiscoveryCandidate ||--o{ BenchmarkRun : "benchmarked-by"
    DiscoveryCandidate ||--o{ ClaimedProfile : "claimed-by-vendor"
    DiscoveryCandidate ||--o{ SponsoredPlacement : "sponsored-as"
    DiscoveryCandidate ||--o{ BenchmarkCertification : "certified-as"
    Capability ||--o{ CapabilityClaim : "claimed-as"
    Capability ||--o{ CapabilityDescription : "described-by"
    Capability ||--o{ CapabilityDemandEvent : "demanded-as"
    Capability ||--o{ BenchmarkRun : "scored-on"
    Capability ||--o{ SponsoredPlacement : "sponsored-in"
    Goal ||--o{ DecomposedSubTask : "decomposed-into"
    DecomposedSubTask }o--|| Capability : "suggests"
    Goal ||--o{ CapabilityDemandEvent : "raises"

    Vendor ||--o{ ClaimedProfile : "owns"
    Vendor ||--o{ BenchmarkCertification : "pays-for"
    Vendor ||--o{ SponsoredPlacement : "books"
    Vendor ||--o{ LeadRoutingSubscription : "subscribes-to"
    Vendor ||--o{ DemandDataSubscription : "subscribes-to"
    Vendor ||--o{ VendorAuditEvent : "audited-as"

    PartnerIntegration ||--o{ PartnerAPIKey : "uses"
    PartnerIntegration ||--o{ PartnerAuditEvent : "audited-as"

    User ||--o{ Goal : "submitted"
    User ||--o{ SavedRecipe : "owns"
```

### 12.2 Top‑level entities

| Entity | Key | Phase | Description |
|---|---|---|---|
| `DiscoveryCandidate` | `(provider_type, id)` | 1 | Agent / MCP server / A2A agent / AI service |

| `Capability` | `slug` | 1 | Routable capability |
| `CapabilityClaim` | `(candidate_id, capability_slug)` | 1 | M:N candidate ↔ capability |
| `CapabilityDescription` | `slug` | 1 | Hand‑curated description |
| `JudgeDecision` | `(candidate_id, capability, goal_hash)` | 1 | LLM judgment result |
| `ProbeResult` | `(candidate_id, probe_kind, timestamp)` | 1 | MCP tool list etc. |
| `BenchmarkRun` | `(candidate_id, capability, run_id)` | 1 | One benchmark execution |
| `Goal` | `id` | 1 | User‑submitted goal |
| `DecomposedSubTask` | `(goal_id, ordinal)` | 1 | One sub‑task |
| `CapabilityDemandEvent` | `(timestamp, capability, goal_hash)` | 1 | Refusal / unmet demand |
| `DiscoveryRunEvent` | `(timestamp, scout, run_id)` | 1 | Scout invocation |
| `ApiWithoutAgent` | `(provider_id)` | 1 | API in APIs.guru with no MCP/A2A |
| `User` | `id` | 1 | Authenticated user (Pro+) |
| `SavedRecipe` | `(user_id, recipe_id)` | 1 | Pro user's saved recipe |
| **`Vendor`** | `vendor_id` | **2** | **Vendor account** |
| **`ClaimedProfile`** | `(vendor_id, candidate_id)` | **2** | **Vendor‑edited overlay on candidate** |
| **`BenchmarkCertification`** | `(vendor_id, candidate_id, capability)` | **2** | **Paid Verified Benchmark commitment** |
| **`SponsoredPlacement`** | `(vendor_id, capability, start, end)` | **2** | **Disclosed paid placement** |
| **`LeadRoutingSubscription`** | `(vendor_id, capability)` | **2** | **Opt‑in lead notification** |
| **`DemandDataSubscription`** | `(vendor_id, scope)` | **2** | **API access to demand data** |
| **`Lead`** | `lead_id` | **2** | **A qualified, opt‑in lead from a user goal** |
| **`ExecutionFeeSubscription`** | `(vendor_id)` | **2** | **Vendor opt‑in to tier‑8 execution fee with rate ≤ 2% and start/end dates** |
| **`ExecutionFeeCharge`** | `(charge_id)` | **2** | **One charge row per sandboxed call that routed to an opted‑in vendor; rolled up monthly into invoice** |
| **`VendorAuditEvent`** | `(timestamp, vendor_id, action)` | **2** | **Every vendor action** |
| **`PartnerIntegration`** | `partner_id` | **3** | **Third‑party host integration** |
| **`PartnerAPIKey`** | `key_id` | **3** | **Scoped API key per integration** |
| **`PartnerAuditEvent`** | `(timestamp, partner_id, action)` | **3** | **Partner API call (sampled)** |

### 12.3 Schema deep dive

See [LLD.md §3](LLD.md#3-database-schema) for full DDL including all Phase 2 + 3 tables.

---

## 13. Primary flows

### 13.1 `/goal` → recipe (Phase 1 canonical path) — with sponsored placement injection

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant W as Next.js
    participant A as FastAPI /goal
    participant D as Decomposer
    participant R as LabelReconciler
    participant PPD as PrePlanDiscovery
    participant S as DiscoveryStore
    participant P as ProviderPartition
    participant J as LLMJudge
    participant RK as Ranker
    participant SP as SponsoredRenderer
    participant MK as MarketplaceStore
    participant RG as RecipeGen
    participant L as DemandLedger
    participant PR as PostGoalRefresh

    U->>W: paste goal
    W->>A: POST /goal {goal}
    A->>D: decompose(goal, catalog_descriptions)
    D-->>A: sub_tasks + suggested_capability_ids
    A->>R: reconcile(suggested_ids)
    R-->>A: canonicalized_ids
    A->>PPD: pre_plan_discovery(thin_caps)
    PPD->>S: search_candidates each cap
    PPD-->>A: enriched store
    A->>S: search top-K per cap
    S-->>A: candidate sets
    A->>P: partition(candidates, router)
    P-->>A: executable / configurable / no_provider
    A->>J: judge(top-K per executable cap, goal)
    J-->>A: ranked judgments
    A->>RK: rank(candidates, judgments)
    RK-->>A: natural ranking (NEVER touches sponsorship)
    A->>MK: fetch active sponsorships per cap
    MK-->>A: sponsored pool
    A->>SP: render_with_sponsored_section(natural, sponsored)
    SP-->>A: category view (sponsored separate, natural unchanged)
    A->>RG: build_workflow_options
    RG-->>A: workflow_options + recipe URLs
    A->>L: emit demand events
    A->>PR: schedule async MCP probe
    A-->>W: plan + workflow_options + recipes + disclosure
    W-->>U: rendered plan + download recipe CTA
```

### 13.2 Vendor signup + claim flow (Phase 2)

```mermaid
sequenceDiagram
    actor V as Vendor
    participant VW as Vendor Portal
    participant VA as Vendor API
    participant CV as ClaimVerification
    participant DNS as Vendor DNS
    participant MK as MarketplaceStore
    participant Aud as VendorAuditEvents

    V->>VW: signup at vendor.planmyagents.com
    VW->>VA: POST /vendor/signup
    VA->>MK: create Vendor row
    VA->>Aud: log "vendor_signup"
    V->>VW: claim apollo-mcp listing
    VW->>VA: POST /vendor/claim/apollo-mcp
    VA->>CV: initiate DNS verification
    CV-->>V: "add TXT record planmyagents-verify=abc123"
    V->>DNS: add TXT record
    loop every 5 min for 14 days
        CV->>DNS: poll for TXT record
        alt found
            CV->>MK: update ClaimedProfile to claim_verified_dns
            CV->>Aud: log "claim_verified_dns"
            CV-->>V: email "you're verified"
        end
    end
    V->>VW: edit profile (description, screenshots)
    VW->>VA: PUT /vendor/profile/apollo-mcp
    VA->>MK: update ClaimedProfile
    VA->>Aud: log "profile_edit"
```

### 13.3 Verified Benchmark certification (Phase 2)

```mermaid
sequenceDiagram
    actor V as Vendor
    participant VA as Vendor API
    participant MK as MarketplaceStore
    participant Stripe
    participant BR as BenchmarkRunner
    participant BS as BenchmarkStore
    participant Pub as PublicSurface

    V->>VA: request Verified Benchmark for apollo-mcp / company_data_lookup
    VA-->>V: methodology agreement + invoice
    V->>VA: sign agreement + pay
    VA->>Stripe: charge
    Stripe-->>VA: ok
    VA->>MK: create BenchmarkCertification (status=scheduled)
    VA->>BR: schedule extended run (≥250 samples)
    BR->>BR: execute samples (over days)
    BR->>BS: write results
    BR->>MK: update BenchmarkCertification (status=awaiting_reply)
    VA-->>V: email "results ready; 7-day right of reply"
    alt vendor disputes methodology
        V->>VA: submit dispute
        VA->>VA: external arbitration
        VA->>BR: re-run if methodology error
    end
    BR->>Pub: publish results to /benchmark/{cap}
    BR->>MK: update BenchmarkCertification (status=published)
    BR->>Pub: badge live on /agents/{id}
```

### 13.4 Sponsored placement creation + display (Phase 2)

```mermaid
sequenceDiagram
    actor V as Vendor
    participant VA as Vendor API
    participant MK as MarketplaceStore
    participant Stripe
    participant SR as SponsoredRenderer
    participant U as User
    participant W as Frontend
    participant FA as FirewallAudit

    V->>VA: POST /vendor/sponsor {capability: "web_search", start, end, budget}
    VA->>MK: create SponsoredPlacement (max 1 per category)
    VA->>Stripe: charge
    Stripe-->>VA: ok
    VA-->>V: sponsorship active
    Note over MK: nightly firewall audit runs
    FA->>MK: list active sponsorships
    FA->>FA: assert each is in disclosure section, never above natural #1
    U->>W: visit /categories/web_search
    W->>SR: GET category view
    SR->>MK: fetch active sponsorships for web_search
    SR->>SR: inject in dedicated "Sponsored" section
    SR-->>W: response with sponsored section + natural ranking
    W-->>U: render with clear "Sponsored" badges + disclosure note
```

### 13.5 Partner API call (Phase 3)

```mermaid
sequenceDiagram
    actor PH as Partner Host (e.g., n8n)
    participant PG as Partner Gateway
    participant Q as Quota Manager
    participant A as Recipe Engine (same as /goal)
    participant PA as Partner Audit

    PH->>PG: POST /partner/v1/recipe (with API key)
    PG->>PG: authenticate API key
    PG->>Q: check rate limit + quota
    alt within quota
        PG->>A: invoke engine (same as /goal but partner-formatted output)
        A-->>PG: recipe in partner format
        PG->>PA: log call (sampled if high volume)
        PG-->>PH: recipe response
    else over quota
        PG-->>PH: 429 with reset time
    end
    Note over PH: partner's user executes
    PH->>PG: POST /partner/v1/feedback (execution result)
    PG->>A: ingest into benchmark_runs
```

### 13.6 BYO‑credentials sandbox execution (Phase 1 + Phase 2 execution‑fee hook)

```mermaid
sequenceDiagram
    actor U as User
    participant W as Next.js
    participant A as FastAPI /execute (gated)
    participant Adapter as GenericProtocolAdapter
    participant SL as SpendLedger
    participant Aud as AuditLedger
    participant MK as MarketplaceStore
    participant EF as ExecutionFeeCharger
    participant Provider as MCP / A2A / OpenAPI server

    U->>W: enter credentials in session
    W->>A: POST /execute {recipe_step, credentials in header}
    A->>SL: check spend cap
    alt within cap
        A->>Adapter: execute(step, credentials)
        Adapter->>Provider: protocol call
        Provider-->>Adapter: result
        Adapter-->>A: structured result
        A->>SL: record charge
        A->>Aud: log call (args hash; no plaintext)
        Note over A,MK: Phase-2 execution-fee hook (non-blocking)
        A->>MK: is vendor of this provider opted-in?
        alt opted-in
            MK-->>A: yes, rate=1.5%
            A->>EF: record charge (vendor-side; rate ≤ 2% enforced)
            EF->>MK: append execution_fee_charges row
        else not opted-in
            MK-->>A: no
        end
        A-->>W: result (credentials never returned; disclosure.execution_fee_active in metadata if charged)
    else over cap
        A-->>W: 402 with cap context
    end
```

**Invariants in this sequence:**
- The execution‑fee hook is **non‑blocking** — user response is never delayed by marketplace store lookup (cached + fail‑open)
- Rate cap enforced at write time in `ExecutionFeeCharger.record_charge` — rate > 2% raises P0 firewall alert
- Hook only fires when `Adapter` is `GenericProtocolAdapter` (BYO‑creds sandbox). User running same recipe in their own Claude Desktop / Cursor / n8n triggers no charge.
- User's price (what they pay the vendor) is unchanged. The fee is vendor‑paid out of their own margin, billed monthly out‑of‑band.

---

## 14. Cross‑cutting concerns

### 14.1 Authentication and authorization

- **Public surfaces** (search, opportunities, leaderboards, disclosure): unauthenticated; IP rate‑limited
- **Pro+ user surfaces**: Clerk JWT
- **Vendor portal** *(Phase 2)*: Clerk + 2FA mandatory; vendor‑org‑scoped RBAC
- **Partner API** *(Phase 3)*: per‑partner API key with endpoint allowlist + quota
- **Admin**: separate auth path; IP‑allowlisted in production

### 14.2 Rate limiting

- Public IP: 60 / min
- Authenticated user: 600 / min
- Vendor portal: 1200 / min per vendor org
- Partner API: per‑partner quota (varies by plan)
- Admin: no rate limit (manually used)

### 14.3 Logging

- Structured JSON logs with `request_id`, `goal_id`, `user_id` / `vendor_id` / `partner_id`
- Per‑stage `elapsed_ms` for hot path
- Log shipping to managed service (Datadog / Better Stack / managed Loki)

### 14.4 Telemetry

- OpenTelemetry traces for cross‑subsystem latency
- Metrics: hot path latency; scout success; judge acceptance; spend rate; vendor signup conversion; partner API QPS per partner

### 14.5 Error handling philosophy

- Structured refusals over generic 500s wherever possible
- Refusals contain `next_step` so the UI can guide the user
- LLM errors fall through the escalating client without user‑visible disruption
- Vendor‑facing errors include "support ticket" links for paid tiers
- Partner API errors include partner‑specific status page links

### 14.6 Configuration

- All config via env vars (`.env.example` documents)
- Per‑subsystem feature flags
- Per‑partner config via DB (not env)
- No secrets in code

### 14.7 Vendor‑neutrality audit (cross‑cutting)

- CI lint asserts `ranker.py` does not import `marketplace.*` modules
- Nightly cron asserts no sponsored placement is displayed above natural #1
- Quarterly internal audit; annual external audit (Y2+)

---

## 15. Deployment view

### 15.1 Topology (Year 1)

```mermaid
flowchart LR
    subgraph Edge[AWS Edge]
        CDN[CloudFront + ACM + Route 53]
    end

    subgraph Frontend[AWS EC2 v0]
        UWeb[User Next.js]
        VWeb[Vendor Next.js - P2]
    end

    subgraph Backend[AWS EC2 v0 - split later]
        UAPI[User FastAPI]
        VAPI[Vendor FastAPI - P2]
        PAPI[Partner Gateway - P3]
        Worker[Async Worker]
    end

    subgraph DB[RDS Postgres + pgvector]
        DiscDB[(Discovery store)]
        MktDB[(Marketplace store - P2)]
        PartDB[(Partner store - P3)]
    end

    subgraph LLM[LLM Providers]
        Groq
        OpenAI
        Anthropic
    end

    subgraph Emb[Embedding]
        OAIEmb[OpenAI Embedding]
        Ollama[Self-hosted Ollama]
    end

    subgraph Obs[Observability]
        Logs[Logs]
        Metrics[Metrics]
        Sentry[Error Tracking]
    end

    CDN --> UWeb
    CDN --> VWeb
    CDN --> UAPI
    CDN --> VAPI
    CDN --> PAPI
    UWeb --> UAPI
    VWeb --> VAPI
    UAPI --> DiscDB
    VAPI --> MktDB
    VAPI --> DiscDB
    PAPI --> DiscDB
    PAPI --> PartDB
    Worker --> DiscDB
    Worker --> MktDB
    Worker --> LLM
    Worker --> Emb
    UAPI --> LLM
    UAPI --> Emb
    All --> Obs
```

### 15.2 Environments

| Env | Hostname(s) | Backend | DB |
|---|---|---|---|
| Local | `localhost:3000` / `:8000` | Single process | SQLite or local Docker Postgres |
| Dev | `dev.planmyagents.com` | Single-region dev host | Managed Postgres |
| Staging | `staging.planmyagents.com` + `vendor-staging.planmyagents.com` | Staging host | Managed Postgres |
| Production | `www.planmyagents.com` + `api.planmyagents.com` | AWS EC2 v0 behind CloudFront; split services later | RDS Postgres + pgvector |

### 15.3 CI/CD

- GitHub Actions on every PR: `make test`, `make lint`, schema validation, **firewall lint** (P11 enforcement)
- Auto‑deploy `main` → staging
- Manual promotion → production with smoke test gate
- Database migrations via versioned SQL files under `infra/postgres/migrations/`

### 15.4 SLOs (steady‑state target by Year 1 end)

| SLO | Target |
|---|---|
| `/goal` p95 latency | < 10 s |
| `/discovery/search` p95 latency | < 800 ms |
| `/partner/v1/recipe` p95 latency | < 8 s |
| `/vendor/*` p95 latency | < 2 s |
| API availability (monthly) | 99.5% |
| Discovery index freshness (max lag) | 24 h |
| Per‑request spend cap breach rate | < 0.1% |
| **Vendor‑neutrality firewall breaches** | **0** |

---

## 16. Cross‑references

- **Cross‑cutting architecture**: [ARCHITECTURE.md](ARCHITECTURE.md)
- **Module‑level design**: [LLD.md](LLD.md)
- **Marketplace mechanics**: [marketplace-design.md](marketplace-design.md)
- **Partnership strategy**: [partnership-strategy.md](partnership-strategy.md)
- **Operations runbook**: [operations.md](operations.md)
- **Discovery subsystem deep dive**: [agent-discovery-index.md](agent-discovery-index.md)
- **Benchmark methodology**: [benchmark-methodology.md](benchmark-methodology.md)
