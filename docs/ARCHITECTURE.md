# PlanMyAgents — System Architecture

> **Version:** 2.0 (May 2026)
> **Status:** Authoritative. Supersedes v1 (Phase‑1 only) and `docs/technical-architecture.md` (the v0 pre‑pivot document, kept for provenance).
> **Audience:** Engineering leads, infra reviewers, advisors evaluating defensibility, future hires.
> **Companion docs:** [HLD.md](HLD.md) for subsystem detail, [LLD.md](LLD.md) for module‑level design, [marketplace-design.md](marketplace-design.md) for Phase‑2 vendor‑side surfaces, [partnership-strategy.md](partnership-strategy.md) for Phase‑3 partner API.

---

## 0. What this document is

This is the cross‑cutting architectural blueprint for PlanMyAgents — the trust, recipe, and marketplace platform for the AI agent economy. It defines:

- **Design principles** that constrain every engineering choice (including the vendor‑neutrality firewall)
- **Logical architecture** — the layered view across all three product phases
- **Physical architecture** — deployment topology and infra dependencies
- **Trust model** — how candidates and vendors earn / lose verification status
- **Marketplace mechanics** — how vendor‑side surfaces plug into the engine
- **Partnership platform** — how third‑party hosts (n8n, Cursor, Anthropic) consume our index
- **Scalability strategy** — how the system grows from 1K to 100M monthly searches with 1K paying vendors
- **Security and privacy** — what we hold, what we never hold
- **Observability** — how we know the system is healthy
- **Cost discipline** — how each subsystem stays inside budget
- **Failure modes** — what graceful degradation looks like
- **Evolution strategy** — the explicit 3‑phase architectural roadmap

It does *not* go into per‑subsystem behavior (see HLD) or per‑module API contracts (see LLD).

---

## 1. Architectural vision

The system is a **continuously refreshed, ranked, benchmarked, and marketplace‑powered index of AI agents** with two distinct sets of user‑facing surfaces:

**Demand‑side surfaces (Phase 1, user‑facing):**
1. Discovery search — fast, ranked lookups against the index
2. Goal decomposition + recipe — LLM turns a natural‑language goal into a runnable agent workflow
3. Benchmark API — programmatic access to ranking + benchmark data
4. Public gap leaderboard — demand signals as content

**Supply‑side surfaces (Phase 2, vendor‑facing):**
5. Vendor portal — claimed profile management
6. Verified Benchmark certification — paid extended testing
7. Sponsored category placement — disclosed paid visibility
8. Demand‑Data API — programmatic access to refusal / demand signals
9. Lead routing (opt‑in)

**Partnership surfaces (Phase 3, host‑facing):**
10. Partner API — programmatic access to our recipe + recommendation engine, consumed by n8n, Zapier, Cursor, Cline, Claude Desktop, AWS Bedrock, etc.
11. Enterprise registry — private deployment with customer‑specific index overlay

All three surface families sit on top of one shared asset (the **Agent Discovery Index**), which is the moat. The architecture's first job is to keep that index **fresh, deduped, verified, ranked, and trustworthy** at low marginal cost. Its second job is to expose marketplace mechanics that monetize vendor‑side without compromising vendor‑neutrality. Its third job is to expose partner APIs that distribute the engine to other hosts.

**What we are explicitly not building:**

- An execution engine for arbitrary customer workflows with stored credentials
- A hand‑coded connector library (vendor‑specific Razorpay / Stripe / Shopify wrappers etc.)
- A pay‑to‑rank marketplace (money buys visibility, never ranking position)
- A workflow builder UI (we hand off recipes to n8n / Zapier / Claude Desktop / Cursor)

These constraints — what we **don't** build — are as important to the architecture as what we do.

---

## 2. Design principles

The eleven principles below are non‑negotiable. Every PR, every infra choice, every roadmap item is evaluated against them.

### P1 — The index is the asset. Everything else is a surface.

We invest engineering depth in scout fleet, dedupe, embeddings, verification, judging, and benchmark. UIs and APIs over the index are intentionally thin; if the index is right, surfaces become easy. If the index is wrong, no UI can save the product.

### P2 — Trust ladder over binary verified/unverified.

Five tiers (`capability_verified` > `registered_in_directory` > `known_provider` > `community_listed` > `unverified`). Degradation is graceful; the ladder gives buyers and vendors a comprehensible mental model.

### P3 — Refuse with reasons; never fabricate.

When we cannot route a sub‑task to a qualified provider, we refuse and name the blocker per capability. Fabricated recommendations destroy trust irreversibly; honest refusals are valuable signal that fuels the demand leaderboard *and* the marketplace's Demand‑Data API.

### P4 — BYO credentials. Always.

We do not hold customer secrets, even when execution is enabled. Storing one customer key crosses into Stripe / Plaid compliance burden and breaks the trust position.

### P5 — Protocol over vendor.

Adapters are written **only** for open protocols (MCP / A2A / OpenAPI). We do not write vendor‑specific code paths. If a vendor wants in, they expose a protocol‑compliant interface; we index their compliance. This caps engineering cost and removes the connector‑arms‑race trap.

### P6 — Async by default; degrade gracefully on the hot path.

`/goal` and `/search` must be responsive under sub‑10‑second latency. Heavy work (post‑goal refresh, MCP tool probes, benchmark runs, sponsored‑placement reconciliation) goes to async background tasks.

### P7 — Cost‑bounded by construction.

Every subsystem has a budget. Cost caps are enforced via `spend_ledger.py`; the escalating LLM client routes from cheap → expensive by policy. We never let a single user query exceed a per‑request cost ceiling.

### P8 — Determinism in the small, intelligence in the large.

Hash routing, dedupe keys, slug canonicalization, ranking weights, and trust ladder transitions are deterministic. LLM intelligence is reserved for cases where it provides real lift (decomposition, judging, semantic matching).

### P9 — Append‑only audit, mutable state minimized.

Every write leaves an event. We can reconstruct system state at any historical moment — critical for benchmark disputes, vendor neutrality audits, and enterprise compliance.

### P10 — Boring tech that scales.

FastAPI, Postgres with pgvector, async Python, Next.js, file‑based fixtures. Zero exotic infrastructure. Optimizes for founder velocity at seed and credibility at Series A.

### P11 — **The vendor‑neutrality firewall.** *(New in v2)*

**Money buys *visibility* — sponsored placement, claimed profile features, lead routing — and optionally *execution attribution* (tier 8).**
**Money *never* buys *ranking position*.**

This rule is enforced architecturally:
- The ranker takes no `paid` / `sponsored` / `vendor_relationship` / `execution_fee_rate` input. CI lint asserts `ranker.py` does not import `marketplace.*`.
- Sponsored placements are physically separate UI sections, always *below* natural #1
- Verified Benchmark results are published whatever they are; the vendor pays for the run, not the result
- **Optional execution fee (tier 8) is hard‑capped at 2% in code AND contract.** Vendor cannot pay a higher rate to influence position — the lever does not exist. Rate is per‑vendor opt‑in, default off, vendor‑paid (never user‑paid), disclosed in recipe metadata.
- Every paid relationship is disclosed in‑surface and machine‑readable in the API
- Sponsorship CRUD and execution‑fee opt‑ins both write to `vendor_audit_events` (append‑only, externally reviewable in year 2 SOC2 scope)

A breach of P11 is treated as a P0 production incident on par with a credential leak. Once compromised, vendor‑neutrality cannot be uncompromised — and the moat collapses.

---

## 3. Logical architecture — the ten layers (Phase 1 + 2 + 3)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  L10 — PARTNER API  (Phase 3)                                           │
│  /partner/v1/search · /partner/v1/recipe · /partner/v1/recommend ·      │
│  /partner/v1/benchmark · OEM SDK · auth per partner · usage quota       │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  L9 — VENDOR PORTAL  (Phase 2)                                          │
│  /vendor/claim · /vendor/profile · /vendor/benchmark/request ·          │
│  /vendor/sponsor · /vendor/leads · /vendor/demand-data ·                │
│  Sponsorship disclosure engine · Vendor-neutrality audit table          │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  L8 — USER SURFACES  (Phase 1)                                          │
│  Next.js UI · /goal · /search · /benchmark · /open-mcp-opportunities ·  │
│  /agents/{id} · /leaderboards/{cap} · /recipe/export                    │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  L7 — DEMAND + FEEDBACK                                                 │
│  Discovery gap recorder · Capability demand ledger · Public gap         │
│  leaderboard · Benchmark history surface · Demand-Data export           │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  L6 — RANKING + RECIPE                                                  │
│  Per-capability provider partition · Workflow option synthesis ·        │
│  Recipe export (Claude / n8n / Cursor / markdown / CLI) ·               │
│  Sponsored-placement renderer (with disclosure)                         │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  L5 — OPTIONAL EXECUTION                                                │
│  GenericProtocolAdapter (MCP / A2A / OpenAPI) · BYO-credentials         │
│  runtime · Cost cap + spend ledger · Per-request audit                  │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  L4 — TRUST + VERIFICATION                                              │
│  Verification tier ladder · LLM candidate judge · Qualification gate ·  │
│  MCP tool probe · Vendor self-claim verification (DNS / domain email)   │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  L3 — SEMANTIC LAYER                                                    │
│  Capability catalog (26 routable + open-ended) · Embedder · Capability  │
│  index (pgvector cosine) · Goal decomposer · Label reconciler           │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  L2 — NORMALIZATION + STORAGE                                           │
│  Candidate normalizer · Dedupe (trust-tier-aware) · Discovery store     │
│  (Postgres + pgvector / SQLite) · Marketplace store (vendors,           │
│  claimed_profiles, benchmark_certs, sponsorships, partner_integrations) │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  L1 — DISCOVERY SOURCES                                                 │
│  17+ scouts across 4 channel classes:                                   │
│   * MCP aggregators: Smithery · MCP Marketplace · Glama ·               │
│     official MCP Registry                                               │
│   * Package registry: npm (MCP-tagged packages)                         │
│   * GitHub: code search · recently-pushed · awesome-* list scraper      │
│   * Long-tail: APIs.guru · Hacker News · Vendor RSS · Moltbook ·        │
│     AI directories · curated JSON manifests                             │
│  Plus: Vendor self-submission (Phase 2) · Partner-contributed (Phase 3) │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Layer responsibilities (one‑line each)

| L | Layer | Phase | Single responsibility |
|---|---|---|---|
| L1 | Discovery sources | 1 + 2 + 3 | Pull candidates from public ecosystem + vendor self‑submission + partner contributions |
| L2 | Normalization + storage | 1 + 2 | One schema for agents, one for vendors, one for sponsorships, one for partner integrations |
| L3 | Semantic layer | 1 | Map natural‑language goals and capabilities to indexed entities |
| L4 | Trust + verification | 1 + 2 | Five‑tier ladder, judge, qualify, probe, vendor self‑claim verification |
| L5 | Optional execution | 1 | BYO‑credentials, protocol‑only, sandbox |
| L6 | Ranking + recipe | 1 + 2 | Per‑capability best fit; recipe export; sponsored placement rendering with disclosure |
| L7 | Demand + feedback | 1 + 2 | Gaps become signals; signals fuel public leaderboards + Demand‑Data API |
| L8 | User surfaces | 1 | Thin web / API surfaces over the index |
| L9 | Vendor portal | 2 | Vendor self‑serve: claim, benchmark, sponsor, leads, demand data |
| L10 | Partner API | 3 | Third‑party host integration (n8n, Cursor, Anthropic, AWS, etc.) |

### 3.2 The three paths through the system

**Path A — Discovery search (user, read‑mostly):**
```
user query → embed query → pgvector cosine search → top-N candidates
           → trust badge + benchmark badge + sponsored marker → rank → return
```
Latency target: p95 < 800 ms.

**Path B — Goal → Recipe (user, intelligence + read):**
```
user goal → goal decomposer (LLM, escalating client)
          → label reconciler → pre-plan discovery
          → per-capability provider partition
          → LLM candidate judge → recipe generator
          → return + emit demand events
          → background: post-goal refresh + MCP probes
```
Latency target: p95 < 10 s synchronous; background refresh under 60 s.

**Path C — Vendor portal (vendor, write‑heavy):**
```
vendor signup → claim listing (DNS / domain-email verification)
              → edit claimed profile → optionally request Verified Benchmark
              → optionally subscribe to sponsorship / lead routing / demand data
              → recurring: respond to community feedback, renew badge
```

**Path D — Partner API (host, programmatic):**
```
partner host → authenticate (API key, scoped)
             → /partner/v1/recipe (decomposes user's goal, returns recipe)
             → host renders / embeds in their UI
             → host's user executes in host's environment
             → optional: callback to /partner/v1/feedback for execution result
```

---

## 4. Physical architecture — deployment topology

### 4.1 Year 1 (seed → Series A)

```
                    ┌─────────────────────────────────┐
                    │  CDN (Cloudflare)               │
                    │  Static assets, edge cache, WAF │
                    └────────────────┬────────────────┘
                                     │
                  ┌──────────────────┴──────────────────┐
                  ▼                                     ▼
        ┌─────────────────────┐               ┌─────────────────────┐
        │  Next.js frontend   │               │  FastAPI backend    │
        │  AWS EC2 origin     │  ───HTTPS───▶ │  AWS EC2 origin     │
        └─────────────────────┘               └─────────┬───────────┘
                                                        │
                       ┌────────────────────────────────┼────────────────────────────────┐
                       ▼                                ▼                                ▼
            ┌─────────────────────┐         ┌─────────────────────┐         ┌─────────────────────┐
            │ Postgres + pgvector │         │  LLM providers      │         │  Embedding provider │
            │  Managed RDS for    │         │  Groq · OpenAI ·    │         │  OpenAI · Ollama    │
            │  AWS launch         │         │  Anthropic          │         │                     │
            └─────────────────────┘         └─────────────────────┘         └─────────────────────┘
                       ▲
                       │
            ┌──────────┴──────────┐
            │  Async workers      │
            │  · Discovery refresh│
            │  · Post-goal probe  │
            │  · Benchmark runs   │
            │  · Sponsorship      │
            │    reconciliation   │
            └─────────────────────┘
```

### 4.2 Year 2 (post Series A — marketplace + partner scale)

- Multi‑region Postgres reads (us‑east, eu‑west)
- Dedicated benchmark runner cluster (isolated cost, cleaner SLA)
- Worker pool for scout dispatch (separated from API process)
- **Dedicated vendor‑portal subdomain (`vendor.planmyagents.com`) with its own deployment for blast‑radius isolation**
- **Partner API gateway with per‑partner rate limiting + usage telemetry**
- Edge cache for `/discovery/search` and `/partner/v1/search`
- Optional: on‑prem deployment kit for enterprise customers

### 4.3 Why these choices

| Choice | Why |
|---|---|
| FastAPI + asyncio | Discovery requires concurrent scout I/O; FastAPI's async model is cleanest fit |
| Postgres + pgvector | One DB for relational + vector; mature, hostable anywhere |
| Next.js + CloudFront-backed EC2 origin | Fast path to credible SSR/SEO while staying on the AWS launch path |
| EC2 first, ECS/ALB later | Solo‑founder ops simplicity and lower v0 cost; split services when traffic or isolation demands it |
| Managed Postgres | Backups, PITR, observability without DevOps headcount |
| Groq + OpenAI + Anthropic via escalating client | Cheap‑and‑fast for decomposition, escalate for stubborn cases |
| CloudFront CDN | Edge cache, TLS via ACM, and Route 53 integration for `planmyagents.com` |
| Async via `asyncio.create_task` → graduating to Redis+RQ/SQS at scale | Don't over‑engineer at seed |
| **Separate vendor‑portal deployment (Y2)** | Vendor‑side bugs don't take down user‑side; security blast radius isolated |
| **Partner API gateway with per‑partner quotas (Y2)** | Distinct rate limits per partner; quota accounting for revenue share |

---

## 5. Data lifecycle

### 5.1 How a candidate enters the system

```
Scout source (Smithery / GitHub / vendor self-submission / partner contribution)
        │
        ▼
[ 1. Raw fetch ]      Scout module emits `raw` dicts
        │
        ▼
[ 2. Normalize ]      candidate_normalizer.py → standard DiscoveryCandidate
        │
        ▼
[ 3. Tag ]            verification_status seeded from source
        │
        ▼
[ 4. Dedupe + merge ] dedupe.py → match by canonical id; merge capabilities
        │
        ▼
[ 5. Persist ]        Save to `discovery_candidates` or `apis_without_agents`
        │
        ▼
[ 6. Embed ]          candidate_text_for_embedding (tools/skills aware)
        │
        ▼
[ 7. Async judge ]    LLM scores relevance per capability
        │
        ▼
[ 8. Async probe ]    MCP tool probe; upgrades to capability_verified
        │
        ▼
[ 9. Vendor claim ]   (Phase 2) Vendor self-claim attaches claimed_profile row
        │
        ▼
[ 10. Benchmark cert] (Phase 2) Verified Benchmark run on demand
```

### 5.2 How a recipe is read on the hot path

```
User goal → decompose → reconcile → pre-plan discovery → read candidates
         → provider partition → judge → rank
         → sponsored-placement injection (with disclosure)
         → recipe generation → return + emit demand events
```

### 5.3 How a vendor signup flows (Phase 2)

```
Vendor signup → DNS / domain-email verification
              → claimed_profile row created
              → vendor edits profile (description, screenshots, docs)
              → optional: request Verified Benchmark → benchmark_certifications row + scheduled run
              → optional: create sponsorship → sponsored_placements row (always disclosed)
              → optional: subscribe to demand-data API → partner_integration row
```

### 5.4 What is **never** persisted

- Customer credentials (API keys, OAuth tokens, MCP server auth headers)
- Vendor‑side PII returned by an MCP tool call
- Per‑user goal text in plaintext beyond 30 days (rolled into demand events and discarded)
- **Vendor → ranking position mapping** (no such mapping exists; the firewall is architectural)

---

## 6. Trust model

### 6.1 Per‑candidate trust — the verification ladder (unchanged)

| Tier | Meaning | How earned |
|---|---|---|
| `capability_verified` | Connected, listed tools, matched capability shape | MCP tool probe success |
| `registered_in_directory` | Trusted upstream registry lists it | Source‑tagged (Smithery / MCP Marketplace / official MCP Registry) |
| `known_provider` | Vendor‑owned domain proves ownership | Cross‑reference check, or **(Phase 2)** vendor self‑claim via DNS / domain email |
| `community_listed` | Community curator listed it | Source‑tagged (Moltbook, AgentGuild) |
| `unverified` | Generic scout, no further validation | Default |

### 6.2 Per‑vendor trust — the claim ladder (Phase 2, new)

| Tier | Meaning | How earned |
|---|---|---|
| `claim_verified_dns` | Vendor proved ownership via DNS TXT record | Highest claim tier |
| `claim_verified_email` | Vendor proved ownership via domain‑hosted email | Standard claim tier |
| `claim_pending` | Vendor initiated claim; verification in progress | Time‑bounded; reverts to `unclaimed` after 14 days |
| `unclaimed` | No vendor has claimed this listing | Default |

A claimed listing gets a "claimed" badge in the UI; an unclaimed listing shows "claim this listing" CTA to the vendor. **Claiming has no ranking impact** (P11).

### 6.3 Per‑benchmark trust — the credibility banner (unchanged)

| Banner | Meaning |
|---|---|
| `publishable` | Real workflow runs, ≥3 attempts, public methodology, right‑of‑reply honored |
| `developing` | Methodology stable, sample size below threshold |
| `smoke_test` | Single run, smoke‑level confidence |
| `synthetic_only` | Synthetic inputs, no real workflow data |

**Phase 2 addition: Verified Benchmark Badge.** A vendor pays for an extended publishable run (≥250 samples). The vendor cannot choose whether to publish — the result is published whatever it is. Refusal to honor publication voids the badge and the vendor's claim status drops to `unclaimed` for 90 days.

### 6.4 Per‑sponsorship disclosure (Phase 2, new)

Every sponsored placement carries:
- Machine‑readable `is_sponsored: true` in API responses
- UI badge "Sponsored" always visible at the placement
- Placement is in a dedicated "Sponsored" section; never above natural #1
- Disclosure metadata (`sponsor_vendor`, `sponsorship_start`, `sponsorship_end`) available via `/disclosure/{candidate_id}`

### 6.5 Trust failure modes (and what we do)

| Failure | Detection | Response |
|---|---|---|
| Vendor self‑inflates capability | Tool probe fails | Downgrade to `known_provider` from `capability_verified` |
| Registry includes spam | Quality filter at scout layer | Filter + manual flag |
| Benchmark dispute | Vendor submits methodology challenge | Right‑of‑reply, public response, re‑run with fixed methodology |
| Stale candidate | Last‑seen aging | `lifecycle_status='dormant'` after N days no source confirmation |
| **Vendor disputes Verified Benchmark result** *(Phase 2)* | Vendor refuses publication | Badge voided; vendor `unclaimed` for 90 days; public note explaining refusal |
| **Sponsored placement displayed without disclosure** *(Phase 2)* | CI lint + UI rendering test | P0 incident; rollback; postmortem |
| **Suspected pay‑for‑ranking attempt** *(Phase 2)* | Audit of ranking input fields | P0 incident; external review; no exceptions |

---

## 7. Marketplace mechanics (Phase 2)

Full design: [marketplace-design.md](marketplace-design.md). Summary here.

### 7.1 The vendor‑neutrality firewall — architectural enforcement

```python
# ranker.py — NEVER references vendor payment / sponsorship status
def rank_candidates(...):
    score = (
          weights.cosine * cosine_similarity
        + weights.verification * verification_tier_weight
        + weights.benchmark * benchmark_score
        + weights.freshness * freshness
        + weights.cost_penalty * (1 - cost_normalized)
    )
    # NO `paid`, NO `sponsored`, NO `vendor_relationship`. Period.
    return scored
```

```python
# sponsored_placement.py — separate concern, injected AFTER ranking
def render_with_sponsored_section(natural_ranking, sponsored_pool):
    # Sponsored placements live in a clearly-marked separate section
    # ALWAYS below natural #1
    return {
        "sponsored": sponsored_pool[:1],  # one slot per category, max
        "natural": natural_ranking,
    }
```

### 7.2 Vendor portal data model (added in Phase 2)

| Entity | Key | Description |
|---|---|---|
| `Vendor` | `vendor_id` | Vendor account (vendor employee or vendor itself) |
| `ClaimedProfile` | `(vendor_id, candidate_id)` | Vendor‑edited overlay on top of normalized candidate |
| `BenchmarkCertification` | `(vendor_id, candidate_id, capability)` | Paid Verified Benchmark commitment |
| `SponsoredPlacement` | `(vendor_id, capability, start, end)` | Disclosed paid placement |
| `LeadRoutingSubscription` | `(vendor_id, capability)` | Opt‑in lead notification |
| `DemandDataSubscription` | `(vendor_id, scope)` | API access to demand signals |

### 7.3 Sponsorship reconciliation (async, daily)

```python
# Daily cron, runs at 01:00 UTC
async def reconcile_sponsorships():
    active = sponsored_placements.fetch_active()
    for sp in active:
        if sp.end < now():
            sp.deactivate()
            audit.log("sponsorship_ended", sp)
        if sp.invoice_overdue_days > 30:
            sp.suspend()
            audit.log("sponsorship_suspended_payment", sp)
```

---

## 8. Partnership platform (Phase 3)

Full design: [partnership-strategy.md](partnership-strategy.md). Summary here.

### 8.1 Partner API design principles

- **Versioned and stable.** `/partner/v1/*` contracts are stable; v2 is additive only.
- **Per‑partner authentication and quota.** Each partner integration has its own API key, scope, rate limit, and usage telemetry.
- **Same data, packaged for the partner's use case.** A partner doesn't need `/goal`'s full richness; they typically want `/partner/v1/recommend?capability=X` or `/partner/v1/recipe?goal=...&output=n8n_json`.
- **Callback‑friendly.** Partners can post execution results back to `/partner/v1/feedback` so we accumulate real‑world benchmark data without becoming the executor.
- **OEM SDK.** Tier‑1 partners get a thin SDK (TypeScript + Python) wrapping the Partner API.

### 8.2 Partner integration types

| Type | Example | Endpoint shape |
|---|---|---|
| **Embed our search** | n8n nodes "Find an MCP for…" | `/partner/v1/search?q=...` |
| **Embed our recipe** | Cursor recipe templates | `/partner/v1/recipe?goal=...&output=cursor_prompt` |
| **Embed our recommendation** | Claude Desktop "Suggested MCPs" | `/partner/v1/recommend?capability=...` |
| **Embed our benchmark** | Vendor‑neutral comparison widget | `/partner/v1/benchmark/{capability}` |
| **Push execution feedback** | Any host that runs the recipe | `/partner/v1/feedback` POST |

### 8.3 Partner data flow

```
Partner host (e.g., n8n)
        │  API key
        ▼
Partner API gateway (rate limit + quota + audit)
        │
        ▼
PlanMyAgents engine (same as user-facing path)
        │
        ▼
Partner-shaped response (JSON or SDK call)
        │
        ▼
Partner host renders / embeds
        │
        ▼
Partner host's user executes
        │  (optional)
        ▼
Callback → /partner/v1/feedback → benchmark_runs table
```

---

## 9. Scalability strategy

### 9.1 Scaling axes (updated for Phase 2 + 3)

| Axis | Year 1 target | Year 3 target | Strategy |
|---|---|---|---|
| Index size | 5K candidates | 100K candidates | Source fleet + dedupe; pgvector handles ~10M vectors on single node |
| Search QPS | 10 / s | 5K / s | Edge cache; read replicas; query result cache |
| Goal QPS | 1 / s | 100 / s | LLM cost compresses; escalating client; worker pool |
| **Vendor portal QPS** *(P2)* | n/a | 100 / s | Separate deployment; isolated DB schema; standard CRUD scaling |
| **Partner API QPS** *(P3)* | n/a | 50K / s | Gateway + dedicated read replicas; per‑partner quota |
| Scout refreshes / day | 10 | 500 | In‑process → dedicated workers |
| Benchmark runs / day | 50 | 10K | Dedicated benchmark runner cluster |
| Concurrent users | 100 | 50K | Standard FastAPI + Postgres scaling |
| **Vendors paying** *(P2)* | n/a | 1K | Marketplace store; vendor portal scales independently |
| **Tier‑1 partner integrations** *(P3)* | n/a | 10–20 | Per‑partner gateway routes |

### 9.2 What does **not** scale by adding hardware

- **Trust** — earned through methodology, public disputes, time
- **Benchmark credibility** — earned through reproducibility and vendor cooperation
- **Vendor‑neutrality reputation** — earned by never crossing P11
- **Tier‑1 partnership relationships** — earned by long‑term BD discipline

Architecture choices for those: open methodology docs, public scoring pipelines, vendor‑neutrality audit trail, BD discipline + advisor bench.

---

## 10. Security and privacy

### 10.1 Threat model

| Threat | Asset at risk | Defense |
|---|---|---|
| Customer credential leak | API keys (if executor mode) | We don't store them |
| Index poisoning (fake agents) | Recommendation integrity | Trust ladder, quality filter, judge, periodic re‑probe |
| Recommendation manipulation | Ranking bias | P11 firewall; methodology transparency |
| Goal text leakage | User intent privacy | 30‑day retention; never sold |
| Cost attack | OPEX | Per‑request cost cap; user rate limits |
| **Vendor account takeover** *(P2)* | Vendor profile / sponsorship | Strong claim verification; 2FA required for vendor portal |
| **Sponsored placement displayed without disclosure** *(P2)* | Vendor‑neutrality reputation | CI lint + UI rendering test + audit; P0 if breached |
| **Partner API key leak** *(P3)* | Quota abuse, attribution confusion | Per‑partner key rotation; usage anomaly detection |
| DDoS | Availability | Cloudflare, rate limits |

### 10.2 What we never hold

- Customer credentials
- Customer business data beyond goal text
- Vendor side outputs of executed tool calls
- **Mapping of vendor payment → ranking position** (does not exist)

### 10.3 Compliance path

- Y1: Privacy policy + minimal collection
- Y1.5: SOC2 Type 1 prep (driven by first enterprise design partner)
- Y2: SOC2 Type 2 GA; GDPR compliance audit; **vendor‑neutrality firewall audit (year‑1 incident‑free record)**
- Y3: ISO 27001 for international enterprise customers

---

## 11. Observability strategy

### 11.1 What we measure (updated)

| Layer | Metric | Why |
|---|---|---|
| User surface | `/goal` p50/p95/p99 latency, success rate, refusal rate | UX |
| User surface | `/search` p50/p95, cache hit rate | UX |
| **Vendor portal** *(P2)* | Vendor signup conversion, claim verification rate, time‑to‑first‑profile | Vendor UX |
| **Partner API** *(P3)* | Per‑partner QPS, error rate, latency, quota consumption | Partner SLO |
| Decomposer | Tier used, tokens, duration | Cost + quality |
| Discovery | Scout success rate, candidates emitted, dedupe ratio | Index health |
| Trust | Candidates per verification tier; vendors per claim tier | Trust health |
| Judge | Acceptance rate, rejection reasons, fallback used | Judge calibration |
| Spend | Per‑request + per‑day LLM + embedding cost | Unit economics |
| Demand | Top demanded capabilities with no qualified provider | Roadmap + content |
| **Marketplace** *(P2)* | Vendors paying, MRR by SKU, sponsorship reconciliation lag | Revenue health |
| **Vendor‑neutrality** *(P2)* | Sponsored placements above natural #1 (must be ZERO) | Firewall integrity |

### 11.2 Audit ledgers (append‑only)

- `discovery_run_events` — every scout invocation
- `capability_demand_events` — every refusal / unmet demand
- `spend_ledger` — every chargeable LLM / embedding / protocol call
- **`vendor_audit_events`** *(P2)* — every vendor signup, claim, edit, sponsorship CRUD, benchmark request
- **`partner_audit_events`** *(P3)* — every partner API call (sampled for high‑volume partners)

---

## 12. Cost discipline

### 12.1 Per‑subsystem budgets (Year 1, at 10K WAU)

| Subsystem | Cost driver | Budget |
|---|---|---|
| Scout fleet | Source API quotas + compute | < $1K / mo |
| Embeddings | OpenAI | < $500 / mo |
| LLM decomposer | Groq primary, OpenAI fallback | < $1.5K / mo |
| LLM judge | Per‑candidate scoring | < $1K / mo |
| Benchmark runs | Capability‑specific compute + LLM eval | < $500 / mo |
| Postgres + pgvector | Storage + reads | < $500 / mo |
| Frontend + backend hosting | One AWS EC2 instance behind CloudFront | < $100 / mo at v0 scale |
| **Vendor portal infra** *(P2 mid‑year)* | Additional deployment | < $300 / mo |
| Misc (logging, CDN, monitoring) | | < $300 / mo |
| **Total** | | **< $6.5K / mo** |

At 100K WAU + 100 paying vendors (Year 2): roughly 4× linear → ~$30K / month.

### 12.2 Cost guardrails enforced in code

- `spend_ledger.py` — per‑request and per‑day caps
- `escalating_client.py` — N‑tier LLM strategy
- `CachedEmbedder` — never re‑embed unchanged text
- Async post‑goal refresh — heavy work off the hot path
- Scout dispatch timeout — bounded per source
- Result caching for `/search` and `/partner/v1/search`
- **Per‑partner quota enforcement** *(P3)*

---

## 13. Failure modes and graceful degradation

| Subsystem fails | User / Vendor / Partner experience | System behavior |
|---|---|---|
| LLM provider primary down | None | Escalating client falls through |
| Embedding provider down | Search degrades to text match | Logged for alerting |
| One scout fails | None | Other scouts cover; flagged |
| All scouts fail during pre‑plan | Slower response | Post‑goal refresh tries again |
| Postgres primary down | Read‑only mode (search + recipe still work) | Background workers pause |
| Postgres + replicas down | Hard outage on /goal | /open‑mcp‑opportunities + static pages still served |
| MCP tool probe times out | Candidate stays at current tier | Re‑scheduled |
| Benchmark runner stuck | Stale benchmark badge | Cron‑based liveness |
| Sponsored disclosure violation *(P2)* | Sponsored badge missing | CI lint catches; manual review |
| **Vendor portal down** *(P2)* | Vendors can't sign up / edit | User‑side unaffected; vendor portal has independent deploy |
| **Partner API gateway down** *(P3)* | Partner integrations degrade | Per‑partner status page; SLA credit |
| **Vendor‑neutrality firewall breach** *(P2)* | P0 incident; immediate rollback | Postmortem; external audit |

---

## 14. Evolution strategy — three phases

### Phase 1 — Plan + Discover + Recipe (now → Year 1 Q4)
- Single backend process; in‑process async workers
- Single‑region deploy
- SQLite supported for dev; Postgres mandatory for prod
- 17+ scouts on cron schedule, spanning four channel classes (canonical MCP registries, third-party aggregators, package registries, community lists)
- 1 founder + 0–7 hires
- Pro tier launches Q2; Benchmark API beta Q3

### Phase 2 — Marketplace (Year 1 Q4 → Year 2 Q4)
- **Vendor portal subsystem (separate deploy on `vendor.planmyagents.com`)**
- **Marketplace data store** (vendors, claimed_profiles, benchmark_certifications, sponsored_placements, **execution_fee_subscriptions, execution_fee_charges**)
- **Sponsored‑placement renderer** in `/categories/{cluster}` and `/search`
- **Vendor‑neutrality audit table** + automated firewall‑breach detection (covers both sponsorship rendering and execution‑fee rate cap)
- **Async sponsorship reconciliation** worker
- **Verified Benchmark scheduler** with vendor‑funded benchmark runs
- **Optional execution‑fee billing pipeline** — runtime hook in `GenericProtocolAdapter` emits an `execution_fee_charge` row whenever a sandboxed call routes to an opted‑in vendor; rate cap enforced at write time; monthly invoice generated; never blocks the user call
- Separate API process from worker pool (Redis + RQ or SQS)
- Multi‑region Postgres reads
- SOC2 Type 2 prep
- 12 hires

### Phase 3 — Partnership Platform (Year 2 → Year 4)
- **Partner API gateway** with per‑partner authentication, quota, telemetry
- **OEM SDK** (TypeScript + Python wrappers)
- **`/partner/v1/feedback` callback ingest** → benchmark history
- **Enterprise registry deployment kit** (single‑binary + Postgres + worker image for on‑prem)
- **Per‑partner data residency** (EU, APAC) for enterprise compliance
- Multi‑LLM provider observability and policy router
- Recipe marketplace surface (community‑contributed verified recipes)
- 25+ hires
- International (EU, APAC) hosting

### What stays the same across all phases
- Trust ladder
- Recipe‑first, BYO‑credentials philosophy
- Vendor‑neutrality firewall (P11)
- Open benchmark methodology
- Protocol‑only adapter policy

### What we re‑evaluate at each stage
- Vector store: pgvector vs Qdrant / Pinecone (at > 1M candidates)
- Workflow runner: in‑process async vs dedicated workers (at > 100 RPS)
- LLM provider mix every 6 months
- Cloud provider: AWS first for the public `planmyagents.com` launch; ECS/ALB or another managed host can be reconsidered when traffic demands it

---

## 15. Cross‑references

- **High‑level subsystem design**: [HLD.md](HLD.md)
- **Low‑level module design**: [LLD.md](LLD.md)
- **Marketplace mechanics**: [marketplace-design.md](marketplace-design.md)
- **Partnership strategy**: [partnership-strategy.md](partnership-strategy.md)
- **Operations runbook**: [operations.md](operations.md)
- **Discovery subsystem details**: [agent-discovery-index.md](agent-discovery-index.md)
- **Benchmark methodology**: [benchmark-methodology.md](benchmark-methodology.md)
- **Strategic positioning**: [BUSINESS_PLAN.md](../BUSINESS_PLAN.md), [PITCH_DECK.md](../PITCH_DECK.md), [README.md](../README.md)
- **Historical pre‑pivot architecture**: [technical-architecture.md](technical-architecture.md)
