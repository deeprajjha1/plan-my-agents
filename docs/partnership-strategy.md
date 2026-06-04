# PlanMyAgents — Partnership Strategy (Phase 3)

> **Version:** 1.0 (May 2026)
> **Scope:** Tier 1 / 2 / 3 partnership map, deal shapes, BD sequencing, revenue share models, risk register, and the Partner API surface that enables OEM integration.
> **Audience:** Founder, BD lead, advisors, hiring managers for the partnership BD role.
> **Phase:** Tier‑1 partnerships start in months 0–12. Partner API GA in Year 2.

---

## Table of contents

1. [Why partnerships are the Phase 3 moat](#1-why-partnerships-are-the-phase-3-moat)
2. [Partnership taxonomy](#2-partnership-taxonomy)
3. [Tier 1 — founding priority (months 0–12)](#3-tier-1--founding-priority-months-0-12)
4. [Tier 2 — distribution + scale (Year 1–2)](#4-tier-2--distribution--scale-year-1-2)
5. [Tier 3 — long‑arm strategic + standards (Year 2+)](#5-tier-3--long-arm-strategic--standards-year-2)
6. [Partnerships we decline](#6-partnerships-we-decline)
7. [Partner API design](#7-partner-api-design)
8. [Deal shapes and revenue share](#8-deal-shapes-and-revenue-share)
9. [BD sequencing and operational playbook](#9-bd-sequencing-and-operational-playbook)
10. [Risk register](#10-risk-register)
11. [Success metrics](#11-success-metrics)

---

## 1. Why partnerships are the Phase 3 moat

The Phase 1 product (Plan + Discover + Recipe) builds the engine. Phase 2 (Marketplace) monetizes the supply side. **Phase 3 (Partnerships) distributes the engine through every host that touches an agent** — locking in compounding distribution that competitors can't replicate by shipping a better catalog.

The pattern is well‑established:

| Comparable | Engine | Distribution partnerships |
|---|---|---|
| Hugging Face | Model hub | Embedded in every LLM training pipeline; Transformers library is the de‑facto standard |
| npm | Package registry | Embedded in every Node.js build; bundled with Node |
| G2 | SaaS reviews | Embedded as "compare on G2" buttons across SaaS vendor sites |
| Steam | Game store | Embedded as the default launcher for nearly every PC game |
| App Store | App distribution | Mandatory channel on iOS — the OS itself is the partnership |

For PlanMyAgents, "every host that touches an agent" means:

- **Workflow tools** that wire agents together (n8n, Zapier, Make)
- **LLM hosts** that consume MCP / A2A (Claude Desktop, Cursor, Cline, Continue, ChatGPT, Gemini)
- **Cloud platforms** that serve developers (AWS Bedrock, Azure AI Foundry, GCP Vertex)
- **Protocol authorities** (Anthropic for MCP, Google for A2A)
- **Standards bodies** (MCP WG, A2A WG, OpenAPI Initiative)

Each partnership creates:
- **Distribution** — their users discover us by default
- **Switching cost** — once integrated, replacing us means re‑integrating
- **Trust transfer** — their endorsement of our methodology legitimizes us

---

## 2. Partnership taxonomy

We classify partnerships into three tiers based on **cost of inaction** and **timing urgency**.

### 2.1 Tier 1 — founding priority

Partnerships where the cost of *not* having them is highest. These are the deals we go after in months 0–12. Each Tier‑1 partnership unlocks one arm of the **distribution** flywheel:

| Arm | Tier‑1 partner | Why |
|---|---|---|
| **Workflow distribution** | n8n | 100K+ engaged workflow builders; orthogonal product (they execute, we recommend); native MCP integration emerging |
| **IDE / coding host distribution** | Cursor | Largest LLM‑host with native MCP wiring; concentrated dev mindshare; recipe handoff is one click ("Add to Cursor") |
| **Protocol authority** | Anthropic | MCP protocol owner; their endorsement legitimizes our methodology with every MCP buyer; Claude Desktop distribution |

**Note on founding marketplace cohort:** the first 10 paying vendors (Apollo, Perplexity, Hunter, Linkup, etc.) are a *parallel workstream*, not a Tier‑1 partnership tier. Different motion (sales not BD), different metric (vendor ARR not user reach). See [§3.4 below](#34-founding-marketplace-cohort-parallel-workstream-not-a-partnership-tier).

### 2.2 Tier 2 — distribution + scale

Partnerships that scale Phase 1 distribution and pull Phase 2/3 forward. Started in Year 1, signed in Year 1–2.

### 2.3 Tier 3 — long‑arm strategic + standards

Partnerships that build long‑term defensibility (enterprise channel, standards influence, academic credibility). Year 2+.

### 2.4 Anti‑partnerships

Categories we deliberately decline. See §6.

---

## 3. Tier 1 — founding priority (months 0–12)

### 3.1 n8n

| Aspect | Detail |
|---|---|
| **Their pain** | n8n users want to know which AI agents / MCPs to use; n8n can't be the discovery authority for tools they don't ship |
| **Our pain solved** | Distribution to 100K+ engaged workflow builders without paying for ads |
| **Deal shape** | Co‑built "Recipes for n8n" surface; native n8n template export from our `/recipe` endpoint; bidirectional referrals (n8n recommends us for discovery; we recommend n8n for execution) |
| **Revenue share** | 10–20% of Pro tier MRR from users acquired via n8n co‑marketing channels (tracked via referral codes) |
| **Risk** | Low — orthogonal product; they execute, we recommend; no direct competition |
| **Timing** | BD outreach: Q1. Target signature: Q2. First integration live: Q3. |
| **Owner** | Founder + Partnership BD hire (Q2) |
| **Success metric** | 5K WAU coming from n8n channels by Q4 |

**Why n8n first:** open‑source DNA aligned with ours; faster decision‑making than Zapier; international (EU) user base diversifies geo; active community we can co‑market into.

### 3.2 Cursor

| Aspect | Detail |
|---|---|
| **Their pain** | Their users hand‑search MCPs and tool integrations; Cursor docs can't be the trust authority across all vendors; `cursor.directory` (community catalog) lacks ranking + benchmark + freshness |
| **Our pain solved** | Recipe distribution into the largest native‑MCP IDE; dev mindshare is concentrated here; viral via Cursor's content marketing |
| **Deal shape** | (a) "Add to Cursor" buttons on every recipe we generate (one‑click install into Cursor MCP config); (b) Co‑published agent recommendation methodology; (c) Cursor docs link to our `/search?host=cursor`; (d) `cursor.directory` integration (we feed ranked candidates back); (e) co‑marketing in Cursor newsletter and at Cursor‑hosted dev events |
| **Revenue share** | Co‑marketing free; per‑user royalty on Pro tier from Cursor‑referred users (tracked via referral codes) |
| **Risk** | Low‑medium — their own MCP curation might extend; defense is vendor‑neutrality and cross‑host coverage (we work in Claude Desktop, Cline, Continue, n8n too) |
| **Timing** | BD outreach: Q1. Target LOI: Q2. First "Add to Cursor" button live on recipes: Q2. Full partnership announcement + `cursor.directory` integration: Q3. |
| **Owner** | Founder + Partnership BD hire (Q2) |
| **Success metric** | "Add to Cursor" button used 10K+ times by Q4; 5K WAU referred from Cursor channels by Q4 |

**Why Cursor over Cline / Continue first:** largest user base of the three; most developer mindshare; brand association strongest; revenue per Pro upsell is highest. Cline and Continue follow as Tier‑2.

### 3.3 Anthropic

| Aspect | Detail |
|---|---|
| **Their pain** | MCP ecosystem is vendor‑noisy; Claude users hand‑search MCPs; Anthropic can't be the vendor‑neutral trust authority (single‑vendor by definition) |
| **Our pain solved** | Brand association with the protocol owner; Claude Desktop distribution; methodology credibility |
| **Deal shape** | (a) Joint MCP quality methodology (we publish; they endorse); (b) "Tested by PlanMyAgents" badge displayed in Claude Desktop's MCP sidebar; (c) Anthropic‑funded benchmark cells (with disclosure); (d) co‑announce at Anthropic developer events |
| **Revenue share** | None directly; trust transfer is the value. Optional: Anthropic pays for benchmark runs in categories they care about (clearly disclosed) |
| **Risk** | **Medium** — Anthropic could launch a competing curated registry; defense is to be vendor‑neutral and multi‑host so we're useful to *all* LLM hosts, not just Claude. **Tier‑1 partnership is the co‑opt move.** |
| **Timing** | BD outreach: Q1. Target methodology partnership: Q4. "Tested by PlanMyAgents" badge in Claude Desktop: Year 2 Q1. |
| **Owner** | Founder personally — this requires founder‑level relationship |
| **Success metric** | Public co‑announcement at Anthropic event; visible badge in Claude Desktop |

**Why Anthropic stays Tier‑1:** they own MCP; the supply we index is densest in their ecosystem; their dev community is most aligned with our ICP. Even though signature is later than n8n/Cursor, the BD relationship building must start in Q1.

### 3.4 Founding marketplace cohort — parallel workstream (not a partnership tier)

The first 10 paying vendors are a *workstream*, not a Tier‑1 partnership tier. Different motion, different metric, different owner. Captured here for completeness because the cohort is critical to Phase‑2 validation.

| Aspect | Detail |
|---|---|
| **Workstream type** | Sales motion (not BD); founding cohort program in vendor portal |
| **Target** | 10 vendors over months 6–12 |
| **Their pain** | Need vendor‑neutral validation to win enterprise deals; need single neutral marketplace across all LLM hosts |
| **Our pain solved** | First marketplace ARR; first publishable benchmark cells; case studies for next 100 vendors; **execution fee (tier 8) viability data** |
| **Offer** | (a) Founding price 50% off Verified Benchmark for Y1; (b) Lock‑in pricing for 3 years on all SKUs; (c) Product council seat; (d) Case study rights; (e) Co‑marketing on launch; **(f) 12‑month waiver on optional execution fee (tier 8) if they opt in** |
| **Revenue** | $25K–250K marketplace ARR Year 1 across cohort; execution‑fee MRR begins Year 2 Q2 after waiver expires |
| **Risk** | Low — founding price is high enough margin to break even on operations |
| **Timing** | Outreach starts: Q2. First 3 signed: Q3. All 10 by Q4. First 3 Verified Benchmarks published: Q3. First execution‑fee opt‑ins: Q4. |
| **Owner** | Founder + Partnership BD hire (joint owners) |
| **Success metric (Y1)** | 10 founding vendors signed; 3 Verified Benchmark badges published; 3 published case studies; 1–2 execution‑fee opt‑ins (under waiver) |

**Vendor shortlist (in order):**
1. **Apollo** — GTM data; large dev audience; benchmark‑forward culture
2. **Perplexity** — web search MCP; high brand value; willing publisher of methodology
3. **Hunter** — email enrichment; SMB market; founding‑pricing aligned
4. **Linkup** — web search; growing fast; needs validation
5. **Tavily** — AI‑native search; benchmark‑interested
6. **Exa** — semantic search; researcher‑facing
7. **Apify** — web scraping; mature vendor; would benefit from neutral validation
8. **Firecrawl** — crawling; OSS‑first; aligned values
9. **Browserbase** — browser automation; ambitious; growing
10. **Resend** — transactional email; clean MCP shipping

---

## 4. Tier 2 — distribution + scale (Year 1–2)

### 4.1 LLM host integrations

| Partner | Deal shape | Risk |
|---|---|---|
| **Cline** | "Add to Cline" button on recipes; co‑marketing in OSS dev community; their open‑source DNA aligns with our methodology transparency | Low |
| **Continue.dev** | "Add to Continue" button on recipes; their VC backing (Sequoia) gives us warm intro paths | Low |
| **OpenAI (Apps)** | Recipe export to OpenAI Apps; benchmark for OpenAI tool‑call quality | Medium — OpenAI has marketplace ambitions but multi‑host buyer reach makes us complementary |
| **Google (A2A)** | Joint A2A benchmark methodology; A2A‑funded benchmarks; A2A conference presence | Low |
| **Bolt.new / v0 / Replit (agent‑coding platforms)** | Recipe export targeting their app generation; co‑marketing | Low |

### 4.2 Workflow host integrations

| Partner | Deal shape | Risk |
|---|---|---|
| **Zapier** | Co‑marketing "Zapier + PlanMyAgents — find any agent, run anything"; recipe export Zapier format; help docs link to our `/search` | **Medium‑high competitive** — manage by being the neutral layer above their internal recommendations |
| **Make.com** | Same as Zapier; smaller surface area, faster to integrate | Medium |
| **Pipedream** | Same; dev‑focused audience aligns with our ICP | Low |

### 4.3 Vendor framework integrations

| Partner | Deal shape | Risk |
|---|---|---|
| **LangChain / LlamaIndex** | Recipe export targets their framework; they list us in agent‑discovery docs | Low |
| **CrewAI / AutoGen / smolagents** | Recipe targeting their orchestration; co‑marketing in agent‑builder community | Low |

### 4.4 Hosting / infra co‑marketing

| Partner | Deal shape | Risk |
|---|---|---|
| **Vercel** | Recipe deploys as Vercel function; co‑marketing in Vercel content | Low |
| **Supabase** | Recipe state persistence in Supabase; co‑marketing | Low |
| **Replicate** | Cross‑promotion in their model/agent index | Low |
| **Modal** | Recipe execution on Modal compute; case studies | Low |

### 4.5 Strategic enterprise partners (early Y2)

| Partner | Deal shape | Risk |
|---|---|---|
| **GitHub / Microsoft (Copilot Workspace + GitHub Marketplace)** | OEM our index into GitHub agent suggestions; partnership with GitHub Marketplace | Medium‑high — they're a natural acquirer; OEM deal could be the path to acquisition |
| **HashiCorp / Datadog / Snyk** | Reference partnerships; case studies | Low |

---

## 5. Tier 3 — long‑arm strategic + standards (Year 2+)

### 5.1 Cloud platform OEM (Y2 Q3+)

| Partner | Deal shape | Why year 2 |
|---|---|---|
| **AWS Bedrock** | OEM our index into Bedrock's developer experience; channel partner | Enterprise procurement; need Y1 traction first |
| **Azure AI Foundry** | OEM index; co‑sell to enterprise | Same |
| **GCP Vertex AI** | OEM index; co‑sell to enterprise | Same |

### 5.2 Industry analyst

| Partner | Deal shape |
|---|---|
| **Gartner Digital Markets** | Sell our demand data; potential acquisition path |
| **Forrester** | Research subscription | reference partner |
| **IDC** | Industry research partner; would pay for category data |

### 5.3 Open neutrality (Year 2+)

| Partner | Why |
|---|---|
| **Linux Foundation AI / CNCF** | Positions us as the open‑source‑adjacent neutral player; potential OSS contribution path |

### 5.4 Standards bodies

| Body | Role we play |
|---|---|
| **MCP Working Group** | Implementer‑of‑record for "trusted discovery" patterns |
| **A2A Working Group** | Same for A2A |
| **OpenAPI Initiative** | Same for OpenAPI‑described agents |

### 5.5 Adjacent infrastructure

| Partner | Why |
|---|---|
| **Auth‑for‑agents (Clerk / Auth0 / WorkOS / Stytch)** | Recipe needs OAuth flows for vendor APIs; we partner for the auth layer, never build it |
| **Observability (Datadog / Honeycomb / Helicone / Langfuse)** | Their customers want agent observability; we surface "which agent broke" |
| **Vector DB vendors (Pinecone / Weaviate / Qdrant)** | Different layer; co‑marketing only |
| **Agent payment rails (x402 / AP2 / ACP)** | If agent‑to‑agent payments become real, recipes need payment layer; partner, don't build |

### 5.6 Academic

| Partner | Why |
|---|---|
| **Stanford / MIT / Berkeley AI labs** | Academic credibility for benchmark methodology; co‑authored papers; talent pipeline; advisor bench |

---

## 6. Partnerships we decline

Categories we explicitly do not pursue, with rationale.

| Suggested | Why we decline |
|---|---|
| Stripe / Plaid / payment vendors as adapter customers | Would push us toward executor model; conflicts with "no execution" philosophy |
| Salesforce / HubSpot direct integrations at seed | Too far from agent‑builder ICP; comes naturally via enterprise channel later |
| Slack / Microsoft Teams as distribution partner | Comes for free if we win developer mindshare; not strategic to pursue directly |
| Discord / Slack agent integrations | Adjacent product surface; we're not a chat app |
| Paid PR placement in non‑aligned publications | Brand‑mismatched; dev community sees through it |
| White‑label of our index for crypto‑agent projects | Reputation risk; crypto‑adjacent vendors are noisy |
| Acquisition by anyone before Series B | Premature; we want optionality at $200M+ valuation, not $20M |

---

## 7. Partner API design

The Partner API (Phase 3) is the technical surface that makes partnerships scale beyond manual integrations.

### 7.1 Design principles

1. **Versioned and stable.** `/partner/v1/*` contracts are stable; v2 is additive only.
2. **Per‑partner authentication and quota.** Each partner has API key, scope, rate limit, usage telemetry.
3. **Same data, packaged for the partner's use case.** Partners don't need `/goal`'s full richness; they need targeted shapes.
4. **Callback‑friendly.** `/partner/v1/feedback` ingests execution results back into our benchmark history.
5. **OEM SDK.** Tier‑1 partners get TypeScript + Python wrappers.

### 7.2 Endpoint surface

| Endpoint | Use case | Tier‑1 example |
|---|---|---|
| `GET /partner/v1/search?q=...&host={partner}` | Embed discovery search in partner UI | n8n "find an MCP" node |
| `POST /partner/v1/recipe` | Goal → recipe in partner's preferred format | Cursor recipe template; n8n workflow |
| `GET /partner/v1/recommend?capability=...` | One‑shot best‑agent recommendation | Claude Desktop "suggested MCPs" |
| `GET /partner/v1/benchmark/{capability}` | Vendor‑neutral comparison widget | Anthropic embedded benchmark display |
| `POST /partner/v1/feedback` | Execution result callback (success / failure / latency) | Any host running recipes |
| `GET /partner/v1/demand?capability=...` | Demand signal data (Demand‑Data API) | n8n showcasing "what to build next" |

### 7.3 Authentication

```http
POST /partner/v1/recipe HTTP/1.1
Authorization: Bearer pma_partner_v1_n8n_...
X-Partner-ID: n8n
X-Partner-Version: 1.0.4
Content-Type: application/json

{
  "goal": "find 100 EU CTOs and verify their emails",
  "output_format": "n8n_json",
  "user_session_id": "anon_abc123",
  "user_consent_lead_routing": false
}
```

Per‑partner API keys, scoped by:
- Endpoint allowlist (n8n gets `/recipe` + `/search`; Anthropic gets `/benchmark`)
- Rate limit (per‑partner QPS quota)
- Usage telemetry (billed if applicable)

### 7.4 SDK shape (Tier‑1 partners)

**TypeScript:**
```typescript
import { PlanMyAgentsClient } from '@planmyagents/sdk';

const client = new PlanMyAgentsClient({
  apiKey: process.env.PMA_API_KEY,
  partnerId: 'n8n',
});

const recipe = await client.recipe({
  goal: 'find 100 EU CTOs',
  outputFormat: 'n8n_json',
});
```

**Python:**
```python
from planmyagents import PlanMyAgentsClient

client = PlanMyAgentsClient(
    api_key=os.environ['PMA_API_KEY'],
    partner_id='n8n',
)

recipe = await client.recipe(
    goal='find 100 EU CTOs',
    output_format='n8n_json',
)
```

### 7.5 Feedback callback contract

```http
POST /partner/v1/feedback HTTP/1.1
Authorization: Bearer pma_partner_v1_n8n_...
Content-Type: application/json

{
  "recipe_id": "rcp_abc123",
  "step_id": "step_2",
  "capability": "contact_enrichment",
  "provider_id": "hunter-mcp",
  "result": {
    "status": "success",
    "latency_ms": 1240,
    "cost_usd": 0.05,
    "user_satisfaction": 5
  }
}
```

This is how Partner API generates real‑world benchmark data — partners hand us feedback, we feed it into `benchmark_runs`, the ranker improves, partners benefit.

---

## 8. Deal shapes and revenue share

### 8.1 Standard deal templates

| Template | Use case | Terms |
|---|---|---|
| **Co‑marketing (free)** | Tier‑2 LLM hosts (Cursor, Cline, Continue) | Mutual link / button; no money flows; joint content; 1‑year initial term |
| **Royalty (referral)** | Tier‑1 workflow hosts (n8n) | 10–20% of Pro tier MRR for users acquired via referral code; 2‑year term; renewable |
| **OEM (enterprise)** | Tier‑3 cloud platforms (AWS, Azure, GCP) | Quarterly minimum + per‑seat / per‑query above; 3‑year term; sales co‑sell agreement |
| **Methodology endorsement (free)** | Tier‑1 protocol authority (Anthropic) | No money flows; brand association; jointly published methodology; perpetual (with termination right) |
| **Vendor‑funded benchmark** | Tier‑1 founding vendor cohort | Vendor pays per benchmark cycle; founding cohort 50% off Y1; lock‑in pricing 3 years |
| **API consumption (per‑query)** | Tier‑2+ partners using Partner API at scale | Free up to 1K queries/mo; $0.01 per query above; volume discounts |

### 8.2 What stays free across all partnerships

- Discovery search (free for users always; free for partners up to volume tier)
- Public gap leaderboard (free always)
- Public benchmark display (free; deeper data is paid)
- Methodology docs (free; reproducible)

### 8.3 What never bends

- Vendor‑neutrality firewall (P11) is non‑negotiable regardless of partner pressure
- Sponsored placement disclosure (P11)
- Benchmark publication commitment (P11)
- BYO credentials (P4)
- Refusal to write proprietary connectors (P5)

If a partnership demands compromising any of these, we walk away — even if the deal would be valuable in dollar terms.

---

## 9. BD sequencing and operational playbook

### 9.1 Months 0–3 (pre‑seed → seed close)

- Founder personally manages BD for all three Tier‑1 partners
- Outreach to n8n + Cursor + Anthropic in parallel (initial contact)
- Outreach to founding marketplace cohort (Apollo, Perplexity, Hunter, Linkup) starts at month 3
- No partnership signatures yet — relationship building phase

### 9.2 Months 3–6 (seed → Q2)

- Hire Partnership BD lead (Q2 hire)
- n8n technical integration discussion begins; target signature Q2
- **Cursor "Add to Cursor" prototype shipped (Q2 launch alongside recipe export endpoint)**
- Anthropic methodology partnership shape defined; relationship building continues
- First 3 founding marketplace cohort vendors sign LOIs

### 9.3 Months 6–9 (Q3)

- n8n integration prototype live (private alpha)
- **Cursor Tier‑1 partnership signed; "Add to Cursor" buttons live on every recipe; `cursor.directory` integration**
- First 3 founding cohort Verified Benchmark badges live
- Cline + Continue.dev outreach begins (Tier‑2)
- Anthropic methodology partnership advancing toward announcement

### 9.4 Months 9–12 (Q4)

- n8n partnership signed and integration live (public)
- Cursor partnership integration fully GA + co‑marketing campaign
- **Anthropic methodology partnership announced**
- 5+ paying founding marketplace cohort vendors
- **First 1–2 founding cohort vendors opt into optional execution fee (tier 8) under 12‑month waiver**

### 9.5 Year 2

- "Tested by PlanMyAgents" badge live in Claude Desktop (Q1)
- Cline + Continue.dev Tier‑2 partnerships signed
- Partner API GA (Q2)
- Optional execution fee opens publicly to all vendors (Q2)
- First cloud platform deal (Q3+)
- GitHub conversations (Q3+)
- Standards body involvement begins

### 9.6 Operational playbook

For every partnership opportunity:

1. **Strategic fit assessment** — does this serve one of the three flywheel arms?
2. **Tier classification** — Tier 1 / 2 / 3 or decline?
3. **Founder vs BD owner** — founder for Tier 1 + protocol authorities; BD lead for others
4. **Deal template** — pick from §8.1 standards; custom only for material exceptions
5. **Vendor‑neutrality review** — does this require any compromise of P11? If yes, escalate or decline
6. **Legal review** — TOS / contract review before signature
7. **Public announcement** — coordinated co‑marketing on signature
8. **Quarterly review** — partnership health metric (joint pipeline / activations / NPS) reviewed each quarter

---

## 10. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **n8n in‑sources discovery** | Medium | High | Lock in Tier‑1 partnership early; expand our cross‑host coverage (Cursor + Cline + Claude) so n8n needs us for breadth even if they build narrow |
| **Anthropic launches competing registry** | Medium | High | Be vendor‑neutral, multi‑host; co‑opt via methodology partnership; A2A and AI‑native coverage they won't include |
| **OpenAI builds proprietary tool registry inside Apps** | High | Medium | Multi‑host strategy means we still serve Claude / Cursor / Cline; OpenAI users on adjacent hosts still need us |
| **Cursor / Cline / Continue stays neutral and won't pick a discovery partner** | Medium | Medium | We're the only credible vendor‑neutral choice; if they don't pick us they pick nothing — we win passively |
| **Zapier launches their own MCP/agent discovery** | High | Medium | Their connector arms race makes them slow; we're upstream layer they don't compete with; possible Tier‑2 co‑marketing instead of competition |
| **GitHub / Microsoft acquires a competitor** | Low | High | We stay independent until $200M+ valuation; partnership inroads via GitHub Marketplace |
| **Cloud platform OEM deals slip past Y2** | Medium | Medium | Not on Series A critical path; Y3 unlock is fine |
| **Partnership BD hire underperforms** | Medium | Medium | Founder maintains Tier‑1 ownership; BD focuses on Tier‑2 |
| **A partnership demands compromise of vendor‑neutrality** | Medium | **Existential** | Walk away. The firewall (P11) is the moat; no partnership is worth crossing it. |
| **A partner integrates us, then drops us a year later** | Low | Medium | Multi‑partner strategy; no single partner is critical; switching cost protects us |

---

## 11. Success metrics

### 11.1 Year 1 partnership scorecard

| Metric | Target | Current |
|---|---|---|
| Tier‑1 partnerships signed | 3 (n8n + Cursor + Anthropic methodology partnership) | 0 |
| Tier‑1 partnership integrations live | 2 (n8n + Cursor) | 0 |
| Founding marketplace cohort signed | 10 | 0 |
| Founding cohort Verified Benchmarks published | 3 | 0 |
| Founding cohort execution‑fee opt‑ins (under waiver) | 1–2 | 0 |
| Partner API beta sign‑ups | 5 (Cline + Continue + 3 other) | 0 |
| Co‑marketing events held | 3 | 0 |
| Joint blog posts published | 6 | 0 |
| Inbound partnership requests | 10+ | 0 |

### 11.2 Year 2 partnership scorecard

| Metric | Target |
|---|---|
| Tier‑1 partnerships live | 3 |
| Tier‑2 partnerships signed | 8+ |
| Partner API live partners | 10+ |
| Cloud platform OEM LOIs | 1+ |
| Standards body involvement | MCP WG observer minimum |
| Partnership revenue (royalties + OEM) | $200K+ MRR |

### 11.3 Leading indicators

- **For distribution partnerships:** monthly users referred from each partner channel
- **For methodology partnerships:** citations of our methodology in vendor / host docs
- **For OEM partnerships:** partner‑integrated queries / month and per‑partner SLO compliance
- **For founding vendor partnerships:** vendor renewal rate + vendor‑driven referrals to other vendors

---

## 12. Cross‑references

- **Why partnerships are the moat**: [BUSINESS_PLAN.md §8](../BUSINESS_PLAN.md#8-partnership-ecosystem-phase-3)
- **Architectural fit**: [ARCHITECTURE.md §8](ARCHITECTURE.md#8-partnership-platform-phase-3)
- **Partner API LLD**: [LLD.md §2.6](LLD.md#26-partner-api-contracts-phase-3)
- **Marketplace (vendor side)**: [marketplace-design.md](marketplace-design.md)
- **Pitch deck partnership slide**: [PITCH_DECK.md](../PITCH_DECK.md) — "Tier‑1 partnerships we go after first"
