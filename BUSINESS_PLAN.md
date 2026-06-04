# PlanMyAgents — Business Plan

> The trust, recipe, and marketplace platform for the AI agent economy.

**Version:** 2.1 (May 2026 — post‑pivot to discovery + recipe + marketplace + partnerships)
**Stage:** Pre‑revenue, pre‑incorporation, working prototype with 1,200+ backend tests passing
**Ask:** $3–5M seed, 18‑month runway, Series A readiness in 12 months

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [The market shift](#2-the-market-shift)
3. [Customer and pain — both sides](#3-customer-and-pain--both-sides)
4. [Product — the three‑phase ladder](#4-product--the-three-phase-ladder)
5. [Strategic positioning](#5-strategic-positioning)
6. [Business model — the 9‑line revenue ladder](#6-business-model--the-9-line-revenue-ladder)
7. [Marketplace economics (Phase 2)](#7-marketplace-economics-phase-2)
8. [Partnership ecosystem (Phase 3)](#8-partnership-ecosystem-phase-3)
9. [Go‑to‑market](#9-go-to-market)
10. [Competitive landscape](#10-competitive-landscape)
11. [Moat and defensibility](#11-moat-and-defensibility)
12. [Product roadmap (24 months)](#12-product-roadmap-24-months)
13. [Financial plan](#13-financial-plan)
14. [Team and hiring](#14-team-and-hiring)
15. [Risks and mitigations](#15-risks-and-mitigations)
16. [Fundraising narrative](#16-fundraising-narrative)
17. [Appendix: glossary and FAQ](#17-appendix-glossary-and-faq)

---

## 1. Executive summary

**The market shift.** Between late 2024 and mid 2026, three things happened: Anthropic shipped MCP, Google et al shipped A2A, and every major LLM client (Claude Desktop, Cursor, Cline, ChatGPT, Gemini) added native tool use. The result is a fragmented, exploding supply of AI agents — over 1,000 MCP servers, hundreds of A2A agents, thousands of AI‑native services per month being shipped. Nobody ranks them. Nobody verifies claims. Nobody runs a vendor‑neutral marketplace where vendors can be discovered and buyers can find trusted agents.

**The opportunity.** Every dense supply ecosystem produces a discovery + marketplace layer worth billions: Hugging Face for models (~$4.5B), G2 for SaaS (~$2B+), Capterra (now part of $25B+ Gartner Digital Markets), npm for packages, App Store for apps, Steam for games. The agent ecosystem is 10× the surface area of any of those — and its discovery + marketplace + partnership layer is empty.

**Our product is a three‑phase platform:**

1. **Phase 1 — Plan + Discover + Recipe** *(now → Y1)*: Decompose goals into sub‑tasks, discover candidate providers, rank them with evidence, export recipes when steps are actually exportable, and refuse with reasons when gates fail.
2. **Phase 2 — Marketplace** *(Y1 end → Y2)*: Vendors claim listings, get tested benchmarks, run sponsored category placements (with full disclosure), pay for demand‑data API access.
3. **Phase 3 — Partnership Platform** *(Y2 → Y4)*: OEM into n8n / Zapier / Claude Desktop / Cursor / Cline; private enterprise registries; standards influence; cloud platform channels.

**The proprietary engine** under all three phases is **Planning + Discovery** — LLM goal decomposition, embedding‑driven candidate matching, trust ladder verification, LLM judging, ranker. **Recipe + Benchmark + Marketplace** are how we monetize the engine.

**The wedge** is developer agent workflows — densest MCP/A2A supply, fastest validation, viral via dev communities — followed by GTM data workflows and then horizontal expansion.

**The moat** is six compounding assets: discovery freshness × verification evidence × benchmark history × refusal demand data × **marketplace network effects** × **Tier‑1 partnership distribution**. Today, three of these are real (discovery freshness, verification evidence, refusal demand data); benchmark history is the asset we are building — the engine and credibility classifier exist, but no discovered agent has been scored end‑to‑end yet (see status note below). The marketplace and partnership assets are Phase 2/3.

> **Honest status note (read before any benchmark/trust claim).** The
> benchmark *engine* (runner, field‑weighted scorer, per‑capability
> rankings, and a conservative credibility classifier) is built and
> tested. What it has scored so far are *baseline* wrappers around
> commodity APIs (Razorpay live; Resend/Firecrawl fixture‑backed) plus
> mock adapters — none of which is a *discovered agent*, and the
> baselines are deliberately firewalled out of user‑facing routing. The
> credibility classifier therefore reports the public leaderboards as
> `synthetic_only` today. "Benchmarked routing of discovered agents" is
> the central roadmap item this raise funds, not a present capability.
> The BYO‑credentials sandbox that the optional execution fee (tier 8)
> depends on is currently a seeded stub. Full detail:
> `docs/honest-scope-audit.md`.

**The ask.** $3–5M seed funds 18 months of: shipping recipe export, reaching 25K weekly active developers, **closing the discovered‑agent benchmark loop so the first capabilities pass the credibility classifier's `developing`/`publishable` bar**, signing **Tier‑1 partnerships with n8n + Cursor + Anthropic** (distribution + dev mindshare + protocol authority), running a parallel **founding marketplace cohort workstream** to land 5–10 paying vendors with the first 3 publishable Verified Benchmarks, closing 3 enterprise design partners, and reaching $50K MRR — the Series A gate.

**Anti‑pitch.** We are *not* Zapier for agents — we partner with Zapier and n8n, we don't compete on connector count. We are *not* an executor with stored credentials — we never hold customer keys. We are *not* a pay‑to‑rank marketplace — vendors pay for visibility, never for ranking position. We do, in Phase 2, offer an **optional 1–2% vendor‑opt‑in execution fee** (Amazon Associates–style attribution, paid by the vendor out of their margin, user price never changes, capped at 2%, decoupled from ranking) — this is a vendor‑side attribution mechanism, not a universal take rate. The first two anti‑positions remove the connector‑arms‑race trap; the third (with the execution‑fee cap) is the trust firewall that makes us defensible as we scale.

---

## 2. The market shift

### 2.1 The 14‑month supply explosion

| Timeline | Event | Supply impact |
|---|---|---|
| Nov 2024 | Anthropic launches MCP | First open standard for LLM tool integration |
| Q1 2025 | First MCP registries (Smithery, MCP Marketplace) appear | ~200 servers |
| Q2 2025 | A2A protocol announced (Google, Salesforce, SAP, ServiceNow) | First agent‑to‑agent interop standard |
| Q3 2025 | OpenAI Apps + GPT function‑calling refresh | Multi‑vendor tool standardization |
| Q4 2025 | Cursor, Cline, Continue.dev all ship native MCP | Buyer surface explodes |
| Q1 2026 | Official MCP Registry launches | Quality bar raised, supply still fragmented |
| Q2 2026 | n8n + Make + Zapier add AI / MCP nodes | Workflow tools acknowledge they need an upstream discovery layer they can't build |
| Now | 1,000+ MCP servers; 200+ A2A cards; 1,000+ AI‑native services | **No trusted ranking, no vendor‑neutral marketplace, no partnership API** |

### 2.2 Why the discovery + marketplace layer never emerged organically

Every comparable supply ecosystem produced a discovery + marketplace layer within 3–5 years of standardization:

| Ecosystem | Standardization | Discovery + marketplace layer |
|---|---|---|
| Open‑source packages | npm 2010 | npm registry (2010), libraries.io |
| ML models | TensorFlow 2015 / PyTorch 2016 | Hugging Face Hub (2018) |
| SaaS | REST + OAuth ~2010 | G2 (2012), Capterra (2010s), Gartner Digital Markets |
| Mobile apps | iOS 2008 | App Store launch‑day, Play Store |
| Steam games | Steamworks ~2003 | Steam Store with reviews & rankings |
| Browser extensions | Chrome Web Store 2011 | Chrome Web Store |

The MCP/A2A ecosystem is at the equivalent of "npm in 2011 plus G2 in 2012" — supply is dense, supplier behavior is unaligned, and **no trusted ranking + vendor‑neutral marketplace exists**. The first credible layer captures category‑defining brand value.

### 2.3 What we are betting on (and what would invalidate)

**We are betting on:**
- Continued MCP / A2A adoption (already exponential)
- Multi‑model, multi‑LLM‑host reality (no single vendor wins all)
- Buyer pain around discovery being severe enough to pay for trust
- **Vendors paying to be discovered and benchmarked** (G2 / Capterra precedent)
- **Workflow tools and LLM hosts wanting upstream discovery they don't have to build** (n8n, Cursor, Claude Desktop precedent)

**What would invalidate:**
- A frontier model vendor launches a *vendor‑neutral, multi‑host, multi‑protocol* marketplace (defense: very unlikely; their incentive is single‑host capture)
- MCP gets replaced by a closed‑vendor standard (defense: we already cover A2A and AI‑native services in parallel)
- The agent ecosystem stalls — agents fail to deliver real workflow value (this is the real existential risk; pace of progress in early 2026 suggests unlikely)

---

## 3. Customer and pain — both sides

The product serves a two‑sided market: **users** (Phase 1) and **vendors** (Phase 2 onward). Pain on each side is severe and distinct.

### 3.1 Demand‑side ICP — three tiers, sequenced

**Tier 1 — Primary (Year 1):** Developer / agent builder
- Builds with Claude Desktop, Cursor, Cline, Continue.dev, or own LLM application
- Wires 5–30 MCP servers / A2A agents into their workflow
- 2–4 hour weekly cost of hand‑evaluating new agents
- Pays for dev tools ($20–100 / month willingly)
- Viral via HN, dev Twitter, Reddit, agent‑dev Discords

**Tier 2 — Secondary (Year 2):** Knowledge worker / operator (RevOps, recruiter, analyst)
- Already uses Clay, Apollo, Hunter, Bardeen, Apify
- Wants guided agent workflows without writing code
- Pays company card ($50–200 / user / month)
- Cares deeply about output quality and verifiable cost

**Tier 3 — Tertiary (Year 2–3):** Enterprise tech / platform team
- Asked to "standardize on which agents we approve internally"
- Needs private registry, governance, on‑prem, SLA
- Procurement cycle $50K–500K

### 3.2 Supply‑side ICP — three vendor archetypes

**Archetype A — Independent agent vendor** (Apollo, Hunter, Perplexity, Linkup, Tavily, Apify, Browserbase, Exa)
- Wants vendor‑neutral validation to win enterprise deals
- Wants exposure across all LLM hosts and workflow tools at once
- Today: lists self in 7 catalogs; no neutral benchmark; can't measure demand
- Will pay: claimed profile, verified benchmark badge, sponsored placement, demand‑data API

**Archetype B — Open‑source MCP author** (community devs publishing MCP servers)
- Wants their MCP discovered + adopted
- Doesn't have budget — wants free tier and earns badge through quality
- Will benefit from: free claimed profile, free probe‑based capability_verified status
- Their adoption fuels the marketplace's "free tier credibility" without revenue immediately

**Archetype C — Enterprise vendor with public API** (Snowflake, Databricks, Datadog, Stripe, Zendesk)
- Will eventually publish official MCP servers
- Wants their MCP to be *trusted* the moment it ships
- Will pay: enterprise verified benchmark, sponsored placement, lead routing, integration into our enterprise registry

### 3.3 Demand‑side buyer journey today (what we replace)

Status quo workflow for a developer adding a new capability to their LLM app:

1. Google "best MCP for X" → 5 conflicting blog posts, 2 outdated
2. Browse Smithery / MCP Marketplace → 30 results, no ranking, no benchmark
3. Pick one, install, test in Claude Desktop → 30% chance it works first try
4. If it breaks, repeat with fallback → another hour
5. Compare costs / latency manually → never done because tedious
6. Write internal Notion doc; six months later it's stale

**Total cost per new capability: 2–4 engineer hours plus opportunity cost.**

**Replacing this:** 1 search → ranked top 3 with verification + benchmark → recipe drops straight into config. **Total time: under 5 minutes.**

### 3.4 Supply‑side vendor journey today (what we replace)

Status quo for an agent vendor wanting to be adopted:

1. Submit to Smithery, MCP Marketplace, official MCP Registry, GitHub topic, AI directories — 7 separate forms
2. Hope LLM hosts curate yours into Claude Desktop / Cursor docs (they don't, no methodology)
3. Hope someone benchmarks you (no one does at commercial workflow level)
4. Get inbound contact from one buyer; have no demand data to size the market
5. Rebuild your own analytics from referrer logs; never accurate
6. Spend marketing budget on vendor lists and dev community posts; CPL is brutal

**Replacing this:** one claimed profile → verified benchmark badge → sponsored placement when desired → demand‑data API to size markets. Single vendor‑neutral marketplace recognized across all LLM hosts and workflow tools.

### 3.5 Quantified willingness‑to‑pay

| Cohort | Anchor | Willingness to pay |
|---|---|---|
| Developer | Time wasted (2–4 hr/wk) + dev tool comp | $20–50 / user / mo |
| Operator | Time wasted + Clay/Apollo comp | $50–200 / user / mo |
| Enterprise | Gartner Magic Quadrant license comp | $50K–500K / yr |
| Vendor (claimed profile) | G2 vendor profile baseline | $500–2K / yr |
| Vendor (verified benchmark) | Underwriters Laboratories tested‑badge comp | $5–50K / cycle |
| Vendor (sponsored placement) | Capterra sponsored listing comp | $10–100K / qtr |
| Vendor (demand‑data API) | Gartner Research subscription comp | $10–50K / yr |

---

## 4. Product — the three‑phase ladder

### 4.1 Phase 1 — Plan + Discover + Recipe (now → Y1)

The proprietary engine + free user tier + Pro tier. Four user‑facing surfaces:

| Surface | User | What they get | Pricing |
|---|---|---|---|
| `/discovery/search` | Developer | Ranked candidate list with trust + benchmark badges | Free |
| `/goal` → recipe | Developer / operator | Decomposed sub‑tasks; best agent per task; recipe download (Claude / n8n / Cursor / markdown) | Free → Pro |
| `/benchmark/{cap}` | Vendor‑neutral buyer | Programmatic comparison data | Paid API |
| `/open-mcp-opportunities` | Builders | Capabilities where APIs exist but no MCP/A2A wraps them | Free |

**Pro tier ($20–50 / user / month):** saved recipes, team workspaces, freshness alerts, recipe versioning, weekly index digest.

### 4.2 Phase 2 — Marketplace (Y1 end → Y2)

Vendor‑side surfaces. Five revenue lines, one shared firewall (rankings stay neutral; money buys *visibility*, never *ranking position*).

| Surface | Vendor offer | Pricing |
|---|---|---|
| **Claimed agent profile** | Claim listing, edit description, add screenshots/docs, respond to community | $500–2K / yr |
| **Verified Benchmark badge** | Extended benchmark (≥250 samples, p95 latency, success rate, cost) — result published whatever it is | $5–50K / cycle |
| **Sponsored category placement** | "Sponsored" badge in category pages; never above natural #1 | $10–100K / qtr |
| **Lead routing (opt‑in)** | When user goal needs vendor X's capability, vendor optionally notified (with user consent) | $10–100 / qualified lead |
| **Demand‑Data API** | Vendor pulls demand signal data ("what are users asking for that no agent fulfills?") | $10–50K / yr |

### 4.3 Phase 3 — Partnership Platform (Y2 → Y4)

OEM + enterprise + standards. Distribution becomes the moat.

| Surface | Partner | Deal shape |
|---|---|---|
| **Recipe export integration** | n8n, Zapier, Make | Native template export from `/recipe`; co‑marketing |
| **"Add to host" buttons** | Cursor, Cline, Continue.dev, Claude Desktop | One‑click recipe install in target host |
| **Joint methodology** | Anthropic (MCP), Google (A2A) | Co‑published quality methodology; "Tested by PlanMyAgents" badge in host UIs |
| **Cloud OEM** | AWS Bedrock, Azure AI Foundry, GCP Vertex | OEM index into their dev experience |
| **Enterprise registry** | Fortune 500 platform teams | Private agent registry, on‑prem, governance, SLA |
| **Standards influence** | MCP WG, A2A WG, OpenAPI Initiative | Implementer‑of‑record for "trusted discovery" |

### 4.4 Optional execution — BYO‑credentials sandbox (across all phases)

> **Build status:** seeded stub today. `agents/sandbox_runner.py` defines
> the contract and raises `SandboxNotYetWiredError`; the live
> `/sandbox/execute` path is scheduled work, not shipped. Tier‑8 revenue
> and the "collect benchmark data on real workflows" mechanism both
> depend on this path and are gated on it being built.

A user can choose to execute a recipe step from within the PlanMyAgents UI rather than copying it to their LLM host. **Constraints (the contract the wired version must honor):**

- Credentials live in user's browser session only (never persisted server‑side)
- Execution goes through `GenericProtocolAdapter` for MCP / A2A / OpenAPI only (A2A and free‑form AI‑agent invocation are honest refusals until those wire formats stabilize; MCP and OpenAPI are the executable surfaces)
- No hand‑coded vendor wrappers
- Cost cap enforced per session via `spend_ledger.py`
- Every call audited; no telemetry data flows back to vendors
- Gated by env var (off by default in production until trust review)

**Why this matters:** once wired, it lets us collect benchmark data on real workflows without becoming an executor, and it is the path by which a discovered MCP agent gets executed against benchmark cases. The user is always in the driver seat.

---

## 5. Strategic positioning

### 5.1 What we are

> *"The trust, recipe, and marketplace platform for the AI agent economy — G2 + Hugging Face + Gartner Digital Markets, for an ecosystem 10× the surface area of any of them."*

The vendor‑neutral, multi‑protocol, multi‑LLM‑host discovery + ranking + recipe + marketplace + partnership platform for AI agents, MCP servers, and A2A agents.

### 5.2 What we are deliberately not

| Anti‑position | Why we refuse |
|---|---|
| Zapier / n8n / Make competitor | Connector arms race; they're our Tier‑1 distribution partners |
| Workflow builder UI | Layer above us; we feed those tools |
| Pay‑to‑rank marketplace | Trust firewall; rankings stay neutral; money buys visibility only |
| Hand‑coded vendor wrapper farm | We index public agents through open protocols only |
| Executor with stored credentials | Compliance burden, trust risk, no moat |
| Frontier model competitor | Different problem; we ride on every LLM host |
| Marketplace with transaction fees on every recipe execution | Compromises BYO‑credentials trust; deferred to "vendor‑opt‑in only" Phase 3 option |

### 5.3 The category we are creating

There is no existing category for "vendor‑neutral, benchmarked, recipe‑first, two‑sided agent marketplace." We are creating it. The mental model we want adopted:

> *"Before I add a new agent to my workflow, I check PlanMyAgents — like I check npm for packages, Hugging Face for models, G2 for SaaS, App Store for apps."*

### 5.4 Why "trust" is the keyword (vs "discovery" or "marketplace" alone)

Discovery is a feature; Smithery has it. Marketplace is a model; G2 has it. **Trust is the asset that compounds across both sides** — buyers trust us to recommend; vendors trust us to be neutral; LLM hosts trust us as the methodology authority. Without trust, we are one of many catalogs. With trust, we are *the* layer.

---

## 6. Business model — the 9‑line revenue ladder

Each tier unlocks **after** the previous one has proof. We do not ship all nine on Day 1.

| # | Tier | Product | Unlocks after | ARPU / ACV |
|---|---|---|---|---|
| **0** | Free | Discovery search, basic recipe, public leaderboards | Day 1 | $0 (acquisition + brand) |
| **1** | Pro | Saved recipes, teams, private notes, recipe versioning, freshness alerts, watchlists | 10K weekly active developers | $20–50 / user / mo |
| **2** | Benchmark API | Programmatic ranking + benchmark data | 3+ publishable benchmark cells (today: **0** — gated on closing the discovered‑agent benchmark loop) | $500–5K / mo |
| **3** | Enterprise registry | Private registry, on‑prem, governance, SLA, audit, SSO | 3+ design partners | $50K–500K / yr |
| **4** | **Claimed Vendor Profile** | Vendor‑edit access; analytics; community response | 50+ verified agentic candidates per category | $500–2K / yr per vendor |
| **5** | **Verified Benchmark Badge** | Extended ≥250‑sample run; published result whatever it is | 1+ publishable benchmark cell live (today: **0** — same gate as tier 2) | $5–50K / cycle per vendor |
| **6** | **Sponsored Category Placement** | Category page sponsored slot (disclosed) | 25K+ WAU | $10–100K / qtr per vendor |
| **7** | **Demand‑Data API** | Vendor pulls "what users are asking for that no agent fulfills" | 100K+ goal queries/mo | $10–50K / yr per vendor |
| **8** | **Optional Execution Fee** *(vendor‑opt‑in)* | 1–2% on calls flowing through our BYO‑credentials sandbox to a vendor that has opted in. Vendor pays out of their own margin; user price never changes; capped at 2%; decoupled from ranking; vendor benefits = execution attribution analytics + sponsored eligibility + co‑marketing rights | BYO‑creds sandbox GA + 5+ paying vendors on tiers 4–6 | 1–2% per sandboxed call (vendor‑borne); typical vendor MRR $1–25K |

### 6.1 What we deliberately refuse to monetize

- ❌ User‑side margin on agent execution (user never sees a price change from anything we do — even tier‑8 is vendor‑paid)
- ❌ Float on agent execution (we never hold the money flow; vendor settles fee monthly out‑of‑band)
- ❌ Hidden vendor placement fees (destroys trust)
- ❌ Per‑agent listing fees (destroys index density and impartiality)
- ❌ Referral fees that bias ranking (destroys benchmark credibility)
- ❌ Customer data sold to vendors (destroys privacy claim)
- ❌ Pay‑to‑rank — the vendor‑neutrality firewall. Even tier‑8 execution fee is **hard‑capped at 2% and architecturally decoupled from the ranker**; vendor cannot pay a higher rate to influence position.

### 6.2 Why the optional execution fee (tier 8) is *not* a "Zapier‑style take rate"

| Dimension | Zapier‑style take rate (rejected) | Our optional execution fee (tier 8) |
|---|---|---|
| Who pays | User (margin on top of vendor price) | Vendor (out of their own margin) |
| User price impact | Higher | **Unchanged** |
| Universality | Mandatory on every execution | **Opt‑in per vendor; default off** |
| Rate | Variable, can rise to 20–30% | **Hard‑capped at 2% by contract** |
| Ranking impact | Often biased toward higher‑fee vendors | **Decoupled from ranking by architecture (P11)** |
| Vendor benefit | None (pure cost) | Attribution analytics + sponsored eligibility + co‑marketing |
| Disclosure | Often hidden | **Always disclosed in recipe metadata** |
| Comparable | Stripe / Zapier / App Store | **Amazon Associates** attribution fee |

The vendor‑side, opt‑in, capped, decoupled, vendor‑paid framing is what keeps this consistent with the trust position. Vendors opt in because the attribution and co‑marketing value is worth the 1–2% to them.

### 6.3 Indicative unit economics (Year 2 target)

**Pro tier:**
- ARPU: $30 / user / month; gross margin: 85%; payback: ~3 months

**Benchmark API:**
- ARPU: $1,500 / month average; gross margin: 70%; payback: ~6 months

**Marketplace (vendor side, blended):**
- ARPU: $5,000 / vendor / year (mix of claimed profile + verified badge + sponsored)
- Gross margin: 80% (mostly platform compute + benchmark cost)
- Payback: ~9 months on $4K acquisition cost
- LTV: $25K+ over 5 years (sticky vendor profiles)

**Enterprise registry:**
- ACV: $150K average; gross margin: 75%; sales cycle: 6–9 months

**Optional execution fee (tier 8):**
- Vendor MRR: $1–25K per opted‑in vendor (depends on sandboxed execution volume to that vendor)
- Gross margin: 95% (no incremental compute; vendor settles monthly)
- Payback: instant (no acquisition cost beyond the existing claimed‑profile relationship)
- Adoption assumption: 10% of paying vendors opt in by Year 2 end; 25% by Year 3 end

### 6.4 Why this is fundable at venture scale

The marketplace + partnership lines are *additive* to the dev‑tool subscription lines. Total ARR ramp resembles G2 (2012–2018: $0 → $50M) overlaid on Hugging Face (2019–2024: $0 → $70M+) — both built on dense, fragmented supply ecosystems. Our supply ecosystem is bigger than either.

| Year | ARR target | Headcount | Revenue mix |
|---|---|---|---|
| Y1 | $500K | 7 | 55% Pro, 30% Benchmark API, 10% early marketplace (tiers 4‑5), 5% Enterprise |
| Y2 | $5M | 14 | 30% Pro, 25% Benchmark API, 20% Marketplace (tiers 4‑6), 15% Enterprise, 5% Execution fee (tier 8), 5% Partnership royalties |
| Y3 | $20M | 28 | 22% Pro, 18% Benchmark API, 22% Marketplace, 22% Enterprise, 10% Execution fee, 6% Partnership royalties |
| Y4 | $50M | 55 | 18% Pro, 17% Benchmark API, 25% Marketplace, 22% Enterprise, 12% Execution fee, 6% Partnership royalties |
| Y5 | $100M+ | 110+ | Same mix, larger contracts, international |

---

## 7. Marketplace economics (Phase 2)

Full design in [docs/marketplace-design.md](docs/marketplace-design.md). Summary here.

### 7.1 The vendor‑neutrality firewall

This is the asset that distinguishes us from every other "agent registry" attempt. **Money buys visibility (and opt‑in execution attribution). Money never buys ranking position.** Concretely:

- **Rankings** are computed only from: cosine relevance × verification tier weight × benchmark score × freshness × cost penalty. **No vendor payment input — including the optional execution fee.**
- **Sponsored placement** lives in a clearly‑labeled "Sponsored" section, always *below* the natural #1.
- **Verified Benchmark** results are published whatever they are. A vendor cannot pay to have a poor result hidden — they can only pay for the run; the result is the result.
- **Claimed profile** lets vendors edit *their description and metadata*; never their ranking score.
- **Optional execution fee (tier 8)** is hard‑capped at 2% in contract and decoupled from the ranker by architecture — vendor cannot pay a higher rate to influence position.
- All sponsored / paid / opt‑in‑execution‑fee relationships disclosed in‑surface and machine‑readable in the API at `/disclosure`.

### 7.2 Why vendors will pay despite the firewall

| Reason | Comparable |
|---|---|
| Single neutral marketplace recognized across all LLM hosts | G2 cross‑category buyer reach |
| Verified benchmark badge unlocks enterprise procurement | UL / SOC2 / ISO badges |
| Sponsored category placement reaches high‑intent buyers at moment of decision | Capterra sponsored listings |
| Demand‑data API tells vendor what to build next | Gartner Research subscription |
| Reach without engineering investment in 7 separate catalog submissions | Marketplace consolidation value |

### 7.3 Vendor portal flow (Phase 2)

```
Vendor signup
    ↓
Claim listing (verified via DNS or email at vendor domain)
    ↓
Free claimed profile (edit, screenshots, docs links)
    ↓
Optional: pay for Verified Benchmark badge
    ↓ (we run benchmark; result published)
Optional: sponsor a category (with disclosure)
    ↓
Optional: subscribe to Demand‑Data API
    ↓
Recurring: respond to community feedback; freshen description; renew badge
```

### 7.4 The optional vendor‑opt‑in execution fee (tier 8)

Phase 2 introduces a deliberately **optional, vendor‑side, capped, decoupled** execution fee. Most "marketplace take rate" implementations destroy trust because they are user‑facing margin, mandatory, opaque, and ranking‑influencing. Ours is none of those things by construction:

| Invariant | What it means |
|---|---|
| **Opt‑in per vendor** | Vendor signs an explicit addendum to claim this fee. Default state for every vendor is OFF. |
| **Vendor‑paid, never user‑paid** | The vendor pays us 1–2% out of their own margin. The user pays the vendor's posted price, unchanged. We never sit in the user's payment flow. |
| **Hard rate cap (2%)** | Contractually capped. Vendor cannot "pay more for better placement" — the lever doesn't exist. |
| **Decoupled from ranker by architecture** | The ranker (`agents/router.py` + `workflows/scoring.py`) does not import the marketplace store. Same firewall protecting sponsored placement (P11) protects the execution fee. |
| **Disclosed in recipe metadata** | Every recipe step that touches an opted‑in vendor carries `disclosure.execution_fee_active: true`. Listed in `/disclosure` page. |
| **Triggered only via our BYO‑creds sandbox** | We must be in the runtime path to claim it. User executing recipe in their own LLM host (Claude Desktop, Cursor, n8n) without our sandbox → no fee. |
| **Vendor benefits = attribution + sponsored eligibility + co‑marketing** | Vendor opts in because they want the attribution data and co‑marketing rights — not because we forced them. |

The closest comparable is **Amazon Associates / affiliate attribution**, not Stripe / Zapier / App Store. The check on whether we ever cross into a Zapier‑style universal take rate is the 2% cap + opt‑in‑default‑off + vendor‑paid trifecta — those three together make the trust position non‑degradable.

---

## 8. Partnership ecosystem (Phase 3)

Full design in [docs/partnership-strategy.md](docs/partnership-strategy.md). Summary here.

### 8.1 Tier‑1 — founding‑priority partnerships (months 0–12)

Three partnerships covering all three distribution arms. Each one's cost of inaction is highest at this stage.

| Partner | Pain we solve | Our gain | Deal shape | Risk |
|---|---|---|---|---|
| **n8n** | Their users need agent recommendations they can't produce; native MCP integration emerging | 100K+ engaged workflow‑builder distribution; legitimacy with workflow‑tool buyers | Co‑built "Recipes for n8n"; native template export; revenue share on Pro upsells from n8n users | Low (orthogonal product) |
| **Cursor** | Their users hand‑search MCPs and tool integrations; Cursor docs can't be the trust authority across vendors | Recipe distribution into the largest native‑MCP IDE; dev mindshare; viral via Cursor's content | "Add to Cursor" buttons on every recipe; co‑published agent recommendation methodology; Cursor docs link to our `/search?host=cursor`; eventual `cursor.directory` integration | Low‑medium (their own MCP curation might extend; orthogonal because we are vendor‑neutral cross‑host) |
| **Anthropic** | MCP ecosystem is vendor‑noisy; they need trust signals; can't be vendor‑neutral themselves | Brand association; protocol authority; Claude Desktop distribution | Joint MCP quality methodology; "Tested by PlanMyAgents" badge in Claude Desktop; Anthropic‑funded benchmark cells (with disclosure) | Medium (could launch competing registry — co‑opt via methodology partnership) |

**The three together cover all three distribution arms** — workflow tools (n8n) + IDE/coding host (Cursor) + protocol authority (Anthropic) — and lock in our position before any of them in‑sources the layer.

### 8.1.1 Founding marketplace cohort — parallel workstream (not a partnership tier)

Distribution partnerships put the engine in front of users. The founding marketplace cohort puts **first paying vendors** on the engine — proving the Phase‑2 marketplace economics before public launch.

| Aspect | Detail |
|---|---|
| **Target** | 10 vendors over 12 months (Apollo, Perplexity, Hunter, Linkup, Tavily, Exa, Apify, Firecrawl, Browserbase, Resend) |
| **Selection criteria** | Already shipping production MCP/A2A; benchmark‑forward; will commit to publishing results; sells to dev/operator ICP |
| **Offer** | Founding price 50% off Verified Benchmark Y1; 3‑year price lock on all SKUs; product council seat; case study rights; co‑marketing on launch; **founding cohort preferred adopters of optional execution fee (tier 8) with 1‑year fee waiver** |
| **What they validate** | (a) Vendor‑side willingness to pay ($25K–250K marketplace ARR Y1), (b) First 3 publishable benchmark cells, (c) Optional execution fee viability (3+ opt‑ins = tier 8 works), (d) Distribution into their existing communities |
| **Why this is workstream, not partnership tier** | Different motion (sales not BD); different metric (vendor ARR not user reach); different cadence (rolling not lumpy); founder + Partnership BD lead jointly own |

### 8.2 Tier‑2 — distribution + scale (Year 1–2)

| Partner | Strategic shape | Risk |
|---|---|---|
| Cursor / Cline / Continue.dev | "Add to host" buttons; doc links to our `/search`; co‑marketing | Low |
| Zapier | Co‑marketing; recipe export Zapier format; Zapier links from agent help docs | Medium‑high competitive — manage by being the *neutral* layer above their internal recommendations |
| Google (A2A) | Joint A2A benchmark methodology; A2A‑funded benchmarks | Low |
| OpenAI | Recipe export to OpenAI Apps; benchmark for OpenAI tool quality | Medium (their own marketplace ambitions) |
| Vercel / Supabase / Replicate / Modal | Recipe deploys as their function; co‑marketing | Low |
| LangChain / LlamaIndex / CrewAI / AutoGen / smolagents | Recipe export targets their framework; they recommend us for discovery | Low |
| GitHub / Microsoft | OEM into GitHub agent suggestions | Medium‑high (natural acquirer) |

### 8.3 Tier‑3 — long‑arm strategic + standards (Year 2+)

| Partner | Why |
|---|---|
| Cloud platforms (AWS Bedrock, Azure AI Foundry, GCP Vertex) | OEM into developer experience; enterprise channel partner |
| Gartner / Forrester / IDC | Industry analyst legitimacy; would pay us for data |
| Linux Foundation AI / CNCF | Open neutrality positioning |
| MCP WG / A2A WG / OpenAPI Initiative | Standards influence |
| Auth‑for‑agents (Clerk / Auth0 / WorkOS / Stytch) | Recipe needs OAuth flows for vendor APIs |
| Observability (Datadog / Honeycomb / Helicone / Langfuse) | Telemetry handoff; "which agent broke" |
| Universities (Stanford, MIT, Berkeley AI labs) | Academic credibility for benchmark methodology; talent pipeline |

### 8.4 Partnerships we deliberately decline

| Suggested | Why we decline |
|---|---|
| Stripe / Plaid / payment vendors as adapter customers | Conflicts with "no execution" philosophy; would push us toward executor model |
| Salesforce / HubSpot direct integrations at seed | Too far from agent‑builder ICP; comes naturally via enterprise |
| Vector DB vendors (Pinecone, Weaviate) as integration partners | Different layer, no buyer overlap |

### 8.5 The partnership flywheel

```
                       USERS
                         │ goal traffic
                         ▼
                    PLANMYAGENTS
                  (engine + index)
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
  WORKFLOW HOSTS   AGENT VENDORS   PROTOCOL AUTHORITIES
  (n8n, Zapier,    (Apollo,        (Anthropic, Google)
   Claude, Cursor)  Perplexity,
                    Apify, etc.)

  Distribution     Marketplace ARR  Brand + methodology
       │                │                │
       └────────────────┴────────────────┘
                         │
                  More user trust
                  More vendor demand
                  More host integration
                         │
                         ▼
                    (compounds)
```

---

## 9. Go‑to‑market

### 9.1 Sequenced beachhead

1. **Months 0–6: Developer agent workflows.** Acquisition through HN, dev Twitter, Reddit, GitHub presence, agent‑dev Discords. Pricing: free with optional Pro. Distribution gain: **n8n partnership signed by month 6** for recipe export integration.
2. **Months 6–12: Anthropic methodology partnership announced.** First publishable benchmark cell live. First 5 vendor customers paying for claimed profile. GTM data category content expansion.
3. **Year 2: Vendor portal GA + Cursor / Cline / Continue partnerships.** Enterprise registry pilots with 3 design partners.
4. **Year 2–3: Cloud OEM (AWS / Azure / GCP).** Sponsored placement program scales. Demand‑data API enterprise customers.
5. **Year 3+: Horizontal expansion** to any category with measurable workflow outputs.

### 9.2 Acquisition channels by stage

| Stage | Primary channel | Secondary | Cost model |
|---|---|---|---|
| Developer | HN, dev Twitter, Reddit, GitHub repo, agent‑dev Discords | Tech publications, MCP/A2A community events | Content + DevRel |
| Operator | SEO ("best agents for X"), Clay/Apollo adjacent content | Partner referrals, RevOps newsletters | Content + paid SEO |
| Vendor | Direct outreach to vendor product / RevOps teams | Inbound from vendor‑neutral content; analyst recognition | BD + content |
| Enterprise | Inbound from Pro users with corporate cards | Conference (KubeCon, AI Engineer Summit, Gartner shows) | Sales |
| Workflow tool / LLM host | Direct partnership BD | Conference + product‑integration co‑marketing | BD |

### 9.3 Founding distribution motion (months 0–6)

- **Public launch:** HN front page; product‑first post ("Show HN: G2 + Hugging Face for the agent economy")
- **GitHub presence:** OSS components (curated registries, embedding utilities, scout SDK template) for credibility
- **Content cadence:** 1 deep "best agents for X" post / week; doubles as SEO and lead magnet
- **Community engagement:** Active in MCP Discord, A2A Slack, agent‑dev Twitter; ship recipes for community members publicly
- **Partner BD outreach:** n8n, Cursor, Cline, Continue, Anthropic — first conversations in month 1
- **Founding vendor cohort:** Apollo, Perplexity, Hunter, Linkup, Apify — invited to "founding partner" program with extended free claimed profile + discounted Verified Benchmark
- **Design partner program:** 5 dev teams + 3 enterprise early access cohorts with direct founder support

---

## 10. Competitive landscape

### 10.1 Direct competitors (today)

| Player | What they do | Their moat | Our edge |
|---|---|---|---|
| **Smithery** | MCP catalog with search | Index volume, head start | We rank, recipe, benchmark, run a vendor‑neutral marketplace; we are *one layer above* (we use them as input) |
| **MCP Marketplace** | MCP catalog | Vendor relationships | Same as above; protocol‑neutral (also A2A + AI‑native) |
| **Official MCP Registry** | Protocol‑official catalog | Anthropic association | We rank and benchmark; they only list. Anthropic is our Tier‑1 partner. |
| **AgentBench / AgentSearchBench / Steel.dev** | Research benchmarks | Academic rigor | We focus on commercial workflow execution + marketplace |

### 10.2 Adjacent / overlapping competitors

| Player | Overlap | Differentiation |
|---|---|---|
| **Zapier / n8n / Make** | Workflow execution | Tier‑1 partner (n8n) + Tier‑2 (Zapier); we are upstream discovery layer |
| **Clay / Apollo** | GTM data | We are one layer below; Clay uses providers we recommend |
| **Hugging Face** | Discovery hub | Different artifact (models vs agent orchestrations); HF could pivot but their DNA is artifact storage |
| **G2 / Capterra** | Vendor‑neutral marketplace | Different supply ecosystem (SaaS vs agents); they don't reach this category |
| **Cursor / Cline / Continue** | Native MCP wiring | Tier‑2 partners — they integrate our recipes |
| **LangChain / LlamaIndex / AutoGen** | Component discovery | Component‑level; we are workflow + recipe + marketplace |
| **Frontier model vendors** | Native tool curation | Vendor‑neutral, multi‑host, multi‑protocol; they are Tier‑1 partners |

### 10.3 The credible threats (and our defenses)

| Threat | Why credible | Defense |
|---|---|---|
| Anthropic launches a ranked, benchmarked MCP marketplace | Owns protocol; has distribution | Vendor‑neutral, multi‑host, multi‑protocol; A2A and AI‑native they won't include; benchmark methodology open and replicable; recipe handoff to *every* host. **Plus Anthropic is our Tier‑1 partner — co‑opt the threat.** |
| Hugging Face extends to agents | Brand, community | Agent workflows ≠ model artifacts; recipe handoff is workflow‑shaped; trust ladder built for agent risks (auth, side effects, cost) |
| Smithery raises and adds rankings + marketplace | Already has the index | Multi‑source dedupe across 17+ sources spanning canonical registries, third‑party aggregators, package registries, and community awesome‑* lists; goal decomposition + recipe handoff; vendor‑neutrality vs single‑registry capture |
| Microsoft / GitHub launches an agent registry | Distribution + IDE integration | Cross‑host neutrality; benchmark + verification not as easily replicated as a catalog |
| n8n in‑sources discovery | They could, eventually | Tier‑1 partnership locks them in; our cross‑host coverage (Cursor + Cline + Claude + Zapier + own apps) is broader than n8n's reach |

---

## 11. Moat and defensibility

### 11.1 Six compounding assets

| Asset | What compounds | Years to replicate |
|---|---|---|
| **Discovery freshness** | 17+ source scouts across 4 channel classes, daily refresh | 2–3 |
| **Verification evidence** | Trust tier ladder, MCP probe history, vendor cross‑refs | 3–5 |
| **Benchmark history** | Per‑capability runs, time‑series, methodology trust | 3–5 |
| **Refusal demand signal** | Every unmet goal → demand event → public leaderboard | Not replicable without our distribution |
| **Marketplace network effects** | Vendors register because users are here; users come because vendors are verified; both sides reinforce | Compounds with both sides |
| **Tier‑1 partnership distribution** | Each integration (n8n, Cursor, Anthropic) creates switching cost for the partner | Once locked, hard to replicate |

### 11.2 Network effects (multi‑directional)

- More users → more vendor demand to be listed → vendor revenue → more benchmark data → better recipes → more users
- More vendors registered → more credible recipes → more LLM hosts integrate → more users → more vendor registrations
- More benchmark runs → more credible rankings → more vendor‑neutral buyer trust → more enterprise pull
- More partnerships → more distribution → more users → more vendor pull → more partnership leverage

### 11.3 Switching costs (Pro + Vendor + Partner tiers)

- Saved recipes (team library)
- Private notes per agent
- Freshness alerts wired to user's workflow
- Benchmark API keys wired into vendor product backends
- **Vendor claimed profiles** (analytics, community responses, benchmark history)
- **Sponsored placement contracts** (multi‑quarter)
- **Partner integrations** (n8n templates referencing our index)
- Enterprise registry (deep integration with customer's CI/CD or LLMOps)

### 11.4 Why this is venture‑defensible

A trusted, ranked, benchmark‑rich, marketplace‑powered, partnership‑distributed discovery layer over a 10× supply ecosystem (vs models, packages, or SaaS) supports valuations in the $1–5B range based on direct precedents (Hugging Face, G2, Gartner Digital Markets). The moat compounds with index size, marketplace density, partnership lock‑in, and benchmark coverage.

---

## 12. Product roadmap (24 months)

### 12.1 Q1 (now → +3 months)

- ✅ Generic protocol adapters (MCP / A2A / OpenAPI) — done
- ✅ Verification tier ladder + qualification gate — done
- ✅ Goal decomposer + per‑capability provider partition — done
- ✅ Pre‑plan discovery + post‑goal refresh — done
- 🟡 Recipe export endpoint (`/recipe/export?format=...`)
- 🟡 Public launch — HN, dev Twitter, blog
- 🟡 Index density push — 2,000+ verified agentic candidates
- 🟡 **n8n + Cursor + Anthropic Tier‑1 BD outreach started**

### 12.2 Q2 (+3 → +6 months)

- Pro tier MVP (saved recipes, teams, freshness alerts)
- Auth (Clerk + Stripe billing)
- BYO‑credentials sandbox alpha
- First publishable benchmark cell (web_search likely)
- 5 dev team design partners
- 10K weekly active developers
- **n8n Tier‑1 partnership signed and integration live**
- **Cursor partnership LOI signed; "Add to Cursor" button on first recipes**
- **Founding marketplace cohort outreach started** (first 3 LOIs target)

### 12.3 Q3 (+6 → +9 months)

- Benchmark API beta
- 3 publishable benchmark cells
- First enterprise design partner (private registry pilot)
- GTM data category content expansion
- **Cursor Tier‑1 partnership signed and integration live**
- **First 3 founding cohort Verified Benchmarks published**
- 25K weekly active users

### 12.4 Q4 (+9 → +12 months)

- Benchmark API GA
- Enterprise registry private beta
- **Vendor portal alpha** (claimed profiles + first 5 paying vendors)
- **Anthropic methodology partnership announced**
- **Optional execution fee (tier 8) opens to founding cohort first (1‑year waiver); first 1–2 vendors opt in**
- Series A readiness gate: $50K MRR, 25K WAU, 3 enterprise pilots, 3 publishable benchmark cells, **2 of 3 Tier‑1 partner integrations live (n8n + Cursor)**, 5 paying marketplace vendors

### 12.5 Year 2

- Q1: **Vendor portal GA** (25 paying vendors). **Anthropic "Tested by PlanMyAgents" badge live in Claude Desktop.**
- Q2: **Verified Benchmark Badge program launched publicly.** Cline + Continue.dev Tier‑2 partnerships signed. **Optional execution fee (tier 8) opens to all vendors (opt‑in, default off).**
- Q3: First sponsored category placements (with disclosure). First enterprise registry pilot closed paying. **First execution‑fee vendors generating $5K–25K MRR.**
- Q4: **Demand‑Data API beta.** $5M ARR run rate. 100 paying vendors. **All 3 Tier‑1 integrations live (n8n + Cursor + Anthropic).** Series B readiness.

### 12.6 Year 3+

- Cloud OEM (AWS / Azure / GCP)
- International (EU, APAC) hosting + i18n
- Recipe marketplace (community‑contributed verified recipes, free + curated paid)
- Standards body influence (MCP WG, A2A WG implementer‑of‑record)
- Acquisition optionality (G2, Gartner, GitHub, AWS, Anthropic)

---

## 13. Financial plan

### 13.1 Use of seed funds ($4M model)

| Category | % | $ |
|---|---|---|
| Engineering (3 backend + 2 frontend + 1 ML + 1 marketplace eng) | 55% | $2.2M |
| Discovery + benchmark infrastructure | 15% | $600K |
| GTM (DevRel + partnership BD + content + design partners) | 20% | $800K |
| Ops (legal, finance, runway buffer, SOC2 prep) | 10% | $400K |

### 13.2 Headcount plan

| Role | Q1 | Q2 | Q3 | Q4 | Y2 |
|---|---|---|---|---|---|
| Founder / CEO | 1 | 1 | 1 | 1 | 1 |
| Backend engineer | 0 | 1 | 2 | 3 | 4 |
| Frontend engineer | 0 | 1 | 1 | 2 | 3 |
| ML engineer | 0 | 0 | 1 | 1 | 1 |
| Marketplace engineer | 0 | 0 | 0 | 1 | 1 |
| DevRel | 0 | 0 | 1 | 1 | 1 |
| Partnership BD | 0 | 1 | 1 | 1 | 2 |
| Sales (enterprise) | 0 | 0 | 0 | 0 | 1 |
| Ops / finance | 0.25 | 0.25 | 0.5 | 0.5 | 1 |
| **Total** | **1.25** | **4.25** | **7.5** | **10.5** | **15** |

### 13.3 Cost structure assumptions

- LLM API spend (Groq, OpenAI, Anthropic): ~$3K / mo at 10K WAU; ~$15K / mo at 100K WAU
- Embedding compute: ~$500–2K / mo
- Postgres + pgvector: ~$300–1K / mo
- Hosting: ~$500–2K / mo
- **Benchmark runs (recurring + vendor‑paid): ~$5K / mo at scale**
- Total infra at Year 2 with 50K WAU + 100 paying vendors: ~$30K / mo → $360K / yr

### 13.4 Revenue projection (conservative)

| Quarter | WAU | Pro MRR | Bench API MRR | Marketplace (4‑6) MRR | Enterprise MRR | Exec Fee (tier 8) MRR | Total MRR |
|---|---|---|---|---|---|---|---|
| Q1 | 1K | $0 | $0 | $0 | $0 | $0 | $0 |
| Q2 | 5K | $3K | $0 | $0 | $0 | $0 | $3K |
| Q3 | 15K | $12K | $5K | $0 | $0 | $0 | $17K |
| Q4 | 25K | $25K | $15K | $5K (5 vendors) | $10K (1 pilot) | $0 (waiver yr) | $55K |
| Y2 Q1 | 35K | $40K | $30K | $15K (25 vendors) | $30K | $0 (waiver yr) | $115K |
| Y2 Q2 | 45K | $55K | $45K | $35K (50 vendors) | $60K | $5K (5 opt‑ins) | $200K |
| Y2 Q3 | 55K | $70K | $60K | $60K (75 vendors) | $120K | $15K (8 opt‑ins) | $325K |
| Y2 Q4 | 65K | $90K | $80K | $90K (100 vendors) | $250K | $30K (10 opt‑ins) | **$540K** |
| Y3 Q4 | 100K | $150K | $200K | $300K (300 vendors) | $1.2M | $180K (60 opt‑ins) | **$2.03M** |

→ Year 3 ARR ~ **$24M** on conservative assumptions (the tier‑8 execution fee adds ~10% on top by Y3 end).

### 13.5 Series A target (12–15 months out)

- $50–100K MRR
- 25K+ weekly active developers
- 3+ enterprise pilots (1 closed paying)
- 3+ publishable benchmark cells
- **2 of 3 Tier‑1 partner integrations live (n8n + Cursor); Anthropic methodology announced**
- **5+ paying marketplace vendors; 1–2 founding cohort vendors opted into execution fee (waiver year)**
- Brand recognition in dev community

Target raise: $10–20M Series A at $50–100M post.

---

## 14. Team and hiring

### 14.1 Founder

**Deepraj Jha — CEO / founding engineer.**

- 11 years backend engineering (Adobe, Amazon Payments, Salesforce)
- Production systems at scale; payment + cost discipline; external API integration depth
- Vendor‑neutral by background

### 14.2 Founding hires (in order)

1. **Senior backend engineer** — discovery infra, scout fleet, store performance
2. **Partnership BD lead** — Tier‑1 partnership deals (n8n, Anthropic, founding vendors)
3. **Senior full‑stack engineer** — Pro tier UX, recipe export, dashboard polish
4. **ML engineer** — embedding pipeline, benchmark methodology, judge model tuning
5. **DevRel / community lead** — public content, dev community, design partner cohort
6. **Marketplace engineer** — vendor portal, claimed profile, sponsorship surface
7. **Sales (first enterprise)** — only after 3 inbound enterprise pilots prove pull

### 14.3 Advisor profile we are recruiting

- One MCP/A2A protocol insider (Anthropic, Google, ServiceNow, Salesforce)
- One marketplace operator (G2, Capterra, Hugging Face, Replicate, Snyk)
- One distribution / DevRel veteran (Vercel, Supabase, Replicate, Hugging Face, Hashicorp)
- One enterprise GTM operator (Gartner, G2, Snyk, Datadog)
- One ML benchmark / evaluation researcher (academic credibility)

---

## 15. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Frontier model vendor launches competing registry | Medium | High | Vendor‑neutrality, multi‑protocol, multi‑host; **co‑opt via Tier‑1 Anthropic partnership** |
| MCP/A2A adoption stalls | Low | Existential | Multi‑protocol coverage including AI‑native services |
| Discovery quality degrades | Medium | High | Continuous freshness monitoring, multi‑source dedupe, verification ladder, judge oversight |
| LLM cost explodes | Medium | Medium | Escalating client with tiered fallback; embedding caching; judge model rotation |
| Slow Pro tier conversion | Medium | Medium | Free tier itself is acquisition motor; Pro features are usage‑driven |
| Slow vendor‑side conversion | Medium | Medium | Founding vendor cohort with discounted founding pricing; case studies |
| Enterprise sales cycle longer than expected | High | Medium | Design partner program de‑risks before scaling sales |
| Vendor pushback on benchmark publication | Medium | Low | Transparent methodology, right‑of‑reply, vendors can re‑run |
| **Vendor‑neutrality firewall breach** | Low | Existential | Codified in benchmark methodology + pricing; would require explicit policy violation |
| **Tier‑1 partner deals fail to materialize** | Medium | Medium | Multiple parallel BD tracks; n8n + Anthropic are the priority but not the only candidates |
| Compliance / privacy issue | Low | High | We don't store customer credentials; minimal PII; SOC2 path from Q3 |
| Founder burnout / single point of failure | Medium | High | Co‑founder search post‑seed; advisor bench |
| Series A fails despite hitting metrics | Low | High | Default‑alive on Pro + Benchmark API + early Marketplace by month 18 with conservative spend |

---

## 16. Fundraising narrative

### 16.1 The one‑sentence pitch

> *"PlanMyAgents is the trust, recipe, and marketplace platform for the AI agent economy — G2 + Hugging Face + Gartner Digital Markets, for an ecosystem 10× the surface area of any of them."*

### 16.2 Why now (one paragraph)

MCP shipped 14 months ago; A2A shipped 9 months ago; every LLM client now has native tool use; n8n / Zapier / Make are racing to add AI nodes they don't have a discovery layer for. The supply of agents is exploding past 1,000 MCP servers, hundreds of A2A agents, and thousands of AI‑native services — but nothing ranks them, verifies them, benchmarks them, or runs a vendor‑neutral marketplace on top. Every dense supply ecosystem (packages, models, SaaS, apps) produced a discovery + marketplace layer worth billions; this ecosystem will too. We are building it before a frontier model vendor or marketplace incumbent realizes the layer is missing.

### 16.3 Why us (one paragraph)

11 years backend engineering at Adobe, Amazon Payments, and Salesforce — the right discipline mix for an index that has to be fresh, trustworthy, cost‑disciplined, and enterprise‑credible. Vendor‑neutral by background. Already shipped a working prototype with 14+ source connectors, semantic decomposition, verification tier ladder, generic protocol adapters, and 1,200+ backend tests passing.

### 16.4 Why this round (one paragraph)

$3–5M seed funds 18 months of: shipping the recipe export and reaching 25K weekly active developers, signing **Tier‑1 partnerships with n8n + Cursor + Anthropic** (workflow distribution + IDE / coding host distribution + protocol authority), running a parallel **founding marketplace cohort workstream** to land 5–10 paying vendors and the first 3 publishable Verified Benchmarks, **piloting the optional vendor‑opt‑in execution fee (tier 8)** with the founding cohort under a 1‑year waiver, closing 3 enterprise design partners, and reaching $50K MRR — the Series A gate. The seed is a capital‑efficient launch, not a multi‑year platform build; the marketplace + partnership flywheels compound from Year 1 end onward.

### 16.5 Why this investor profile

We are looking for one or two leads with:

- Infrastructure / developer tools investing track record (Vercel, Supabase, Hugging Face, Replicate, Hashicorp, Datadog, etc.)
- **Marketplace investing experience (G2, Capterra, Etsy, Faire, etc.)**
- Comfort with usage‑driven seed motion + marketplace flywheel thesis
- Belief that benchmark + trust + neutrality can be a moat (not connector count)
- Willingness to introduce us to **n8n, Cursor, Anthropic, Cline, Continue.dev, Google (A2A), AWS Bedrock, GCP Vertex, and Tier‑1 vendor agent companies (Apollo, Perplexity, Hunter, Linkup, Tavily, Exa, Apify)** for ecosystem partnerships

---

## 17. Appendix: glossary and FAQ

### Glossary

- **MCP (Model Context Protocol)** — open standard from Anthropic for LLM tool integration; primary agent supply we index
- **A2A (Agent‑to‑Agent)** — open protocol for agent interop; secondary supply
- **AI‑native service** — API service with agent‑shaped interfaces (Perplexity, Linkup, Exa, Apify, etc.); tertiary supply
- **Capability** — atomic, namable thing an agent can do (`web_search`, `payment_authorization`, etc.); 26 in routable catalog plus open‑ended labels
- **Trust ladder** — five‑tier evidence model: `capability_verified` > `registered_in_directory` > `known_provider` > `community_listed` > `unverified`
- **Recipe** — machine‑and‑human‑readable workflow artifact the user executes in their own LLM host
- **Goal decomposer** — LLM that turns user goal into ordered sub‑tasks with capability slugs
- **Discovery gap event** — recorded signal whenever a goal sub‑task has no qualified agent; fuels public gap leaderboard
- **BYO‑credentials sandbox** — optional execution surface where user pastes credentials in browser session; we invoke MCP/A2A/OpenAPI on their behalf; credentials never persisted server‑side
- **Vendor portal** *(Phase 2)* — vendor self‑serve surface for claimed profile, verified benchmark, sponsored placement, lead routing, demand‑data API
- **Verified Benchmark Badge** *(Phase 2)* — extended benchmark run (≥250 samples) result published whatever it is; vendor pays for the run, not the result
- **Vendor‑neutrality firewall** — money buys *visibility* (sponsored placement, claimed profile), never *ranking position*; codified in pricing + methodology
- **Tier‑1 partnership** *(Phase 3)* — founding‑priority partner (n8n, Anthropic, founding vendors) where the cost of inaction is highest

### FAQ

**Q: Why not just be Zapier for AI agents?**
A: Zapier has 5,000+ hand‑coded connectors and a $5B valuation. The arms race is over. Trust + benchmark + recipe + marketplace is unfilled and has $25B+ comparable valuations (Gartner). And Zapier is our **Tier‑2 partner**, not our competitor.

**Q: Why won't Anthropic or OpenAI just do this?**
A: They might curate within their own LLM host. Vendor‑neutral, multi‑protocol, multi‑host benchmarking with recipe handoff across every host is a different product. Their incentive is single‑host capture; ours is to be the meta‑layer. **Anthropic is our Tier‑1 partner.** Co‑opt the threat.

**Q: Won't Hugging Face extend to agents?**
A: They could. HF Spaces is the closest extension and has been stalled. HF's DNA is artifact storage; agent workflows are a different shape, with different benchmarking methodology, different trust signals, and a recipe handoff that crosses LLM hosts. If they extend, they'll do MCP only — we still own A2A + AI‑native + cross‑host.

**Q: Won't Smithery just add ranking and become a marketplace?**
A: They could try. Multi‑source dedupe across 14+ sources is hard to replicate (they're 1 source). Goal decomposition + recipe handoff is meaningfully proprietary. Vendor‑neutrality is a *positioning* choice that, once compromised, can't be uncompromised. Our window to be *the* trusted marketplace is now.

**Q: How do you measure "best agent" objectively?**
A: Documented benchmark methodology per capability (`docs/benchmark-methodology.md`): success rate against known good answers, p95 latency, cost per query, recall@K for search, capability‑specific quality measures. Methodology is public so vendors can dispute and re‑submit.

**Q: What if vendors won't be benchmarked?**
A: We benchmark from public endpoints with documented inputs; vendors are notified before publication; we offer right‑of‑reply and re‑run on request. Vendors who refuse get "no benchmark" rather than a fabricated score. Our **Verified Benchmark Badge** is opt‑in *and* the result is published whatever it is — no hiding poor results.

**Q: How does the marketplace stay vendor‑neutral if vendors pay you?**
A: Money buys *visibility* (sponsored placement, claimed profile features, lead routing) and optionally *execution attribution* (tier 8). Money never buys *ranking position*. This firewall is codified in pricing + methodology + product surface. Tier 8 (the opt‑in execution fee) is hard‑capped at 2% and architecturally decoupled from the ranker — vendor cannot pay a higher rate to influence position. G2 has held this line for 14 years; it's the asset.

**Q: How is the optional execution fee different from a Zapier‑style take rate?**
A: Five concrete differences. (1) **Vendor pays, not user** — vendor settles out of their margin; user price never changes. (2) **Opt‑in per vendor, default off** — no vendor pays unless they signed the addendum. (3) **Hard‑capped at 2%** by contract — vendor cannot escalate. (4) **Decoupled from ranker by architecture** — the ranking module does not import the marketplace store; CI lints enforce this. (5) **Only applies when our BYO‑creds sandbox is in the runtime path** — a user running the same recipe in Claude Desktop, Cursor, or n8n directly triggers no fee. Comparable is Amazon Associates attribution, not Stripe / Zapier / App Store.

**Q: How big can this really get?**
A: Hugging Face is $4.5B post on models alone. G2 is ~$2B+ on SaaS. Gartner is $25B mcap on enterprise IT advisory. The agent supply ecosystem is 10× more granular and fast‑moving. A trusted discovery + marketplace + partnership platform here is plausibly a $5–20B outcome.

**Q: Why open this now vs after a closed launch?**
A: The supply is mid‑explosion. Every quarter delays the index density flywheel and the marketplace network effect. The first credible discovery + marketplace layer captures category‑defining brand value.

**Q: What's the acquisition path?**
A: G2 (extending into agents), Gartner Digital Markets (extending category coverage), GitHub / Microsoft (extending Copilot Workspace), AWS (extending Bedrock developer experience), Anthropic (folding into MCP), Google (folding into A2A). We optimize for product‑independence; acquisition optionality is a byproduct.
