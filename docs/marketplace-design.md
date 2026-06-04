# PlanMyAgents — Marketplace Design (Phase 2)

> **Version:** 1.0 (May 2026)
> **Scope:** Vendor‑side product mechanics, vendor portal flow, claimed profile, Verified Benchmark certification, sponsored placement, lead routing, demand‑data API, and the vendor‑neutrality firewall that protects them all.
> **Audience:** Product leads, vendor‑portal engineers, BD lead, founder.
> **Phase:** This document describes Phase 2 — launches in Year 1 Q4 alpha, GA in Year 2 Q1. See [BUSINESS_PLAN.md](../BUSINESS_PLAN.md) §7 for the business model and [ARCHITECTURE.md](ARCHITECTURE.md) §7 for the architectural fit.

---

## Table of contents

1. [Why the marketplace exists](#1-why-the-marketplace-exists)
2. [The vendor‑neutrality firewall (P11)](#2-the-vendor-neutrality-firewall-p11)
3. [Vendor portal — surfaces and flows](#3-vendor-portal--surfaces-and-flows)
4. [Claimed profile](#4-claimed-profile)
5. [Verified Benchmark badge](#5-verified-benchmark-badge)
6. [Sponsored category placement](#6-sponsored-category-placement)
7. [Lead routing](#7-lead-routing)
8. [Demand‑Data API](#8-demand-data-api)
9. [**Optional execution fee (tier 8)**](#9-optional-execution-fee-tier-8)
10. [Pricing model](#10-pricing-model)
11. [Disclosure rules — what the user always sees](#11-disclosure-rules--what-the-user-always-sees)
12. [Vendor onboarding journey](#12-vendor-onboarding-journey)
13. [Founding vendor cohort program](#13-founding-vendor-cohort-program)
14. [Operational and legal](#14-operational-and-legal)
15. [Risks and what would break the marketplace](#15-risks-and-what-would-break-the-marketplace)

---

## 1. Why the marketplace exists

The Phase 1 product (Plan + Discover + Recipe) is **demand‑side only** — it serves users searching for agents. Vendors are passive subjects of the index. This has two problems at scale:

| Problem | Why it matters |
|---|---|
| Vendors have no neutral place to be discovered | They list themselves in 7 separate catalogs; no single recognized destination |
| Vendors have no way to prove their agent works | Self‑published "benchmarks" don't move enterprise buyers; need third‑party validation |
| Vendors have no way to reach high‑intent buyers at moment of decision | Random vendor lists; no targeting; CPL is brutal |
| Vendors have no way to size demand for new capabilities | Survey + guesswork |

Phase 2 turns the index into a **two‑sided marketplace** that solves all four problems for vendors *without compromising the user‑side trust position*.

The economic comparable is **G2 (~$2B+) + Capterra (now part of $25B Gartner Digital Markets) + Hugging Face Hub ($4.5B)**. Each is a vendor‑neutral marketplace where vendors pay for visibility (claimed profile, sponsored, lead routing) but never for ranking position.

---

## 2. The vendor‑neutrality firewall (P11)

This is the most important section of this document. **The firewall is the moat.** Once compromised, it cannot be uncompromised. Every product decision, every pricing choice, every internal incentive structure must defer to it.

### 2.1 The rule

> **Money buys *visibility* — and optionally *execution attribution* (tier 8, capped at 2%, vendor‑opt‑in, vendor‑paid, decoupled from ranker). Money never buys *ranking position*.**

### 2.2 What this means concretely

| Vendor can pay for | Vendor cannot pay for |
|---|---|
| Claimed profile (edit description, add screenshots, respond to community) | Higher ranking position in `/categories/{cluster}` or `/search` |
| Verified Benchmark Badge (extended testing; result published whatever it is) | Hiding poor benchmark results |
| Sponsored category placement (clearly disclosed, separate section, never above natural #1) | Removing or downgrading competitors |
| Lead routing notifications (when user opts in) | Forcing user contact without consent |
| Demand‑Data API access | Privileged demand‑data not available to others at the same tier |
| **Optional execution fee** *(tier 8, opt‑in)* — vendor opts into 1–2% on calls flowing through our BYO‑creds sandbox to their endpoint, in exchange for execution attribution analytics + sponsored eligibility + co‑marketing rights | **Higher execution‑fee rate to climb the ranking** (rate is hard‑capped at 2% and decoupled from ranker — the lever doesn't exist) |

### 2.3 How the firewall is enforced architecturally

```python
# ranker.py — there is NO vendor-payment input to this function. Ever.
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
        # ⛔ NO `paid` / `sponsored` / `vendor_relationship` input.
        # ⛔ A linter rule asserts this function does not import sponsorship modules.
        scored.append(RankedCandidate(candidate=c, score=score, explanation=...))
    return sorted(scored, key=lambda r: -r.score)
```

```python
# sponsored_placement.py — separate concern, runs AFTER ranking
def render_with_sponsored_section(
    natural_ranking: list[RankedCandidate],
    sponsored_pool: list[SponsoredPlacement],
    capability: str,
) -> CategoryView:
    return CategoryView(
        sponsored=[sponsored_pool[0]] if sponsored_pool else [],  # max 1 per category
        natural=natural_ranking,  # natural #1 is always the natural #1
        disclosure_note="Sponsored placements are paid promotions. Rankings are vendor-neutral.",
    )
```

### 2.4 How the firewall is enforced operationally

| Mechanism | Frequency |
|---|---|
| CI lint asserts `ranker.py` does not import sponsorship modules | Every PR |
| UI rendering test asserts `Sponsored` badge is present whenever `is_sponsored=true` | Every PR |
| Manual quarterly audit of "any sponsored placement above natural #1" (must be zero) | Quarterly |
| External annual audit of methodology + firewall (Year 2 SOC2 scope) | Annual |
| Public disclosure page (`/disclosure`) listing every active sponsorship | Always live |
| Sponsorship CRUD writes to `vendor_audit_events` (append‑only) | Every change |

### 2.5 The "P0 incident" treatment

A firewall breach is treated as a P0 production incident on par with a customer credential leak:
1. Immediate revert of the offending code / placement
2. Public postmortem within 14 days
3. External review (Year 2+: invited auditor reviews root cause + remediation)
4. Founder personally on the call if it's serious

This treatment is documented because **the threat of taking it seriously is itself part of the moat**. Investors, vendors, buyers, and partners must believe we mean P11.

---

## 3. Vendor portal — surfaces and flows

### 3.1 Vendor portal subdomain

`vendor.planmyagents.com` — deployed separately from the user‑facing app for blast‑radius isolation (vendor‑side bugs cannot take down the user‑facing index).

### 3.2 Top‑level vendor surfaces

| Path | Purpose |
|---|---|
| `/signup` | Vendor account creation (Clerk auth + 2FA required) |
| `/claim/{candidate_id}` | Initiate ownership claim for a listing |
| `/dashboard` | Overview: claimed agents, benchmark status, active sponsorships, lead inbox, demand‑data summary |
| `/agents/{candidate_id}/profile` | Edit claimed profile (description, screenshots, docs links, response to community) |
| `/agents/{candidate_id}/benchmark` | Request / view Verified Benchmark; right‑of‑reply on disputes |
| `/sponsor/new` | Create sponsored placement (category + duration + budget) |
| `/leads` | View qualified leads (only if user opts in) |
| `/demand-data` | Browse demand signal data (subscriber tier) |
| `/billing` | Stripe portal handoff |
| `/audit` | Vendor's own audit log (every CRUD action they performed) |

### 3.3 Vendor account hierarchy

```
Vendor (organization)
  ├─ Owner (1+)
  ├─ Editor (0+)        — can edit profile, request benchmark
  ├─ Billing (0+)       — can manage subscriptions and invoices
  └─ Read-only (0+)     — can view dashboard, leads, demand data
```

RBAC enforced server‑side; every action audit‑logged.

---

## 4. Claimed profile

### 4.1 The claim flow

```
Vendor: "I work for Apollo. I want to claim apollo-mcp."
        │
        ▼
[ Choose verification method ]
        │
        ├─ DNS TXT record (preferred, claim_verified_dns)
        │     vendor adds `planmyagents-verify=<token>` TXT record to apollo.io
        │     we poll every 5 min for 14 days; on success → verified
        │
        └─ Email at vendor domain (claim_verified_email)
              we send code to deepraj@apollo.io; vendor pastes code → verified
        │
        ▼
[ Verified ] → claimed_profile row created, vendor can edit
```

### 4.2 What the vendor can edit

| Field | Editable | Notes |
|---|---|---|
| `display_name` | Yes | Subject to community reporting if misleading |
| `description` | Yes | Markdown, 4000 chars max, profanity filter |
| `vendor_url` | Yes | Must resolve under their verified domain |
| `docs_url` | Yes | One link, must resolve under their verified domain |
| `setup_url` | Yes | Same constraint |
| `screenshots` | Yes | Up to 5; image moderation pipeline |
| `categories_claimed` | Yes | Subject to acceptance gate — they claim, we validate via probe |
| `verification_status` | **No** | Earned via probe / ladder, not vendor‑settable |
| `ranking_position` | **No** | Computed only by `ranker.py` (P11) |
| `benchmark_score` | **No** | Earned via benchmark runs |
| `is_sponsored` | **No** | Set only by sponsored‑placement engine |
| Community responses | Yes | Vendor can respond to user feedback inline |

### 4.3 Display rules

- Claimed profile gets a "Claimed" badge in the UI (`/agents/{id}`)
- Unclaimed listings show "Claim this listing" CTA to anyone (clicking → routes to `/claim/{id}` flow)
- Vendor edits are diffed against scout data; if vendor over‑claims capabilities, judge rejects them and ladder reverts

### 4.4 Why this matters to the vendor

- Single neutral profile recognized across all LLM hosts (Claude / Cursor / Cline / Continue)
- Vendor controls their own description (today, scouts pull stale data)
- Vendor can respond to community feedback (G2 review‑response feature)
- Pre‑populates the recipe text shown to users

### 4.5 Pricing

- **Free tier:** basic claim + edit (G2 freemium model)
- **Pro tier ($500–2,000 / year):** analytics dashboard, response notifications, branded card style, priority support

---

## 5. Verified Benchmark badge

### 5.1 What it is

A vendor pays for an **extended benchmark run** (≥250 samples) of their agent against a specific capability. Methodology is documented in [benchmark-methodology.md](benchmark-methodology.md). **The result is published whatever it is** — that is what makes the badge credible.

### 5.2 The flow

```
Vendor requests benchmark for `apollo-mcp` against `company_data_lookup`
        │
        ▼
[ Methodology agreement ] — vendor reviews methodology, signs commitment to publication
        │
        ▼
[ Invoice / payment ] — $5K–50K depending on capability complexity
        │
        ▼
[ Scheduled run ] — benchmark runner executes ≥250 samples
        │
        ▼
[ Results computed ] — success_rate, p95_latency, cost_per_query, recall@K
        │
        ▼
[ 7-day right-of-reply ] — vendor sees results, can dispute methodology
        │   (if dispute → external arbitration; re-run if methodology error confirmed)
        │
        ▼
[ Published ] — results live on `/agents/{id}` and `/benchmark/{capability}`
                badge added: "PlanMyAgents Verified Benchmark · 2026-Q2"
```

### 5.3 What happens if results are bad

- **Vendor cannot refuse publication.** That's the deal.
- If vendor refuses anyway → badge voided; vendor's `claim_status` drops to `unclaimed` for 90 days; public note explaining
- This is the credibility moat. Underwriters Laboratories established this model in 1894; we follow it

### 5.4 Right‑of‑reply

If the vendor disputes a result on methodology grounds:
1. Vendor submits dispute with specific methodology objection
2. External arbitrator (advisor or contracted analyst) reviews
3. If methodology error confirmed → we re‑run + republish
4. If methodology valid → original result stands; vendor can publicly respond inline on `/agents/{id}`

### 5.5 Refresh cadence

- Benchmark cycle: 90 days (results expire after that)
- Vendor can request re‑run anytime for an additional fee
- Major version changes from vendor trigger optional re‑run

### 5.6 Pricing tiers

| Capability complexity | Sample budget | Price |
|---|---|---|
| Simple (single API call, deterministic eval) | 250 samples | $5K |
| Medium (multi‑step, LLM‑eval involved) | 500 samples | $15K |
| Complex (workflow chain, golden dataset comparison) | 1000+ samples | $50K |

---

## 6. Sponsored category placement

### 6.1 What it is

A vendor can pay to appear in a dedicated "Sponsored" section on category pages. **The sponsored section is always clearly disclosed and is structurally separate from the natural ranking.**

### 6.2 Display rules (architectural)

```
/categories/web_search
  ┌──────────────────────────────────────────┐
  │  ⭐ Sponsored                            │
  │  ┌────────────────────────────────────┐  │
  │  │  Perplexity MCP (sponsored)        │  │   ← max 1 slot per category
  │  │  Disclosure: Sponsored placement   │  │
  │  └────────────────────────────────────┘  │
  ├──────────────────────────────────────────┤
  │  Rankings                                │
  │  ┌────────────────────────────────────┐  │
  │  │  #1  Linkup MCP (natural #1)       │  │
  │  ├────────────────────────────────────┤  │
  │  │  #2  Tavily MCP                    │  │
  │  ├────────────────────────────────────┤  │
  │  │  #3  Exa MCP                       │  │
  │  └────────────────────────────────────┘  │
  └──────────────────────────────────────────┘
```

**The natural #1 is always the natural #1.** A sponsored placement above it is a P0 incident.

### 6.3 What the vendor pays for

- One sponsored slot per category for the duration of the booking
- Quarterly bookings ($10K–100K depending on category demand)
- Clearly disclosed badge "Sponsored" always visible
- Position above the natural ranking section but below the disclosure header

### 6.4 Sponsorship CRUD audit

Every sponsorship change writes to `vendor_audit_events`:
- Who created the sponsorship
- When it started / ended
- What invoice it's tied to
- Who approved it (internal CRM)
- Public disclosure URL (always live)

### 6.5 What we do NOT sell

- Sponsorship for ranking position (P11)
- Hidden / undisclosed placement
- Sponsorship that would push the sponsored vendor *above* their natural rank
- Sponsorship in cells where their `qualification_gate` says they shouldn't even appear
- Sponsorship that hides competitors (we don't blacklist anyone for money)

### 6.6 Pricing tiers

| Category demand tier | Quarterly price | Max sponsors per category |
|---|---|---|
| Tier S (web_search, payment_authorization) | $50K–100K | 1 |
| Tier A (contact_enrichment, email_send) | $25K–50K | 1 |
| Tier B (long‑tail capabilities) | $10K–25K | 1 |

We deliberately cap at 1 sponsor per category. Multiple sponsors → race to the bottom + dilutes trust.

---

## 7. Lead routing

### 7.1 What it is

When a user's `/goal` request matches a vendor's capabilities **and** the user opts in to "let vendors of recommended agents contact me," the matched vendor optionally gets notified.

### 7.2 The flow

```
User: "Find me an MCP for X"
        │
        ▼
[ User receives recipe ] — sees toggle: "Let recommended vendors reach out (opt-in)"
        │
        ▼
(if user opts in)
        │
        ▼
[ Lead created ] — anonymized to vendor unless user shares email
        │
        ▼
[ Subscribed vendor notified ] — only if vendor has active lead-routing subscription
        │
        ▼
[ Vendor responds via PlanMyAgents inbox (or directly if email shared) ]
```

### 7.3 Privacy guarantees

- User opt‑in is **explicit** (default off)
- User can revoke at any time
- Vendor sees minimal info unless user shares more
- Anonymized contact through PlanMyAgents inbox by default
- User can mark a lead "spam" → reduces vendor's lead allocation

### 7.4 Pricing

- $10–100 per qualified lead, billed monthly
- Qualification rules public; vendors see "why this lead was qualified"
- Volume tiers for high‑demand categories

---

## 8. Demand‑Data API

### 8.1 What it is

Programmatic access to **anonymized, aggregated demand signal data** — capabilities users are searching for, where current supply is thin, where unmet goals are growing.

### 8.2 What's in the data

| Field | Example | Privacy |
|---|---|---|
| `capability` | `vat_validation` | Public |
| `demand_count_30d` | 142 | Aggregated only |
| `current_best_alternative` | `null` or `vat-validator-mcp` | Public |
| `api_providers_available` | `["vies.ec.europa.eu"]` | Public |
| `geo_distribution` | `{US: 40, EU: 50, APAC: 10}` (only at K≥100) | Aggregated |
| `host_distribution` | `{claude_desktop: 30, cursor: 25, n8n: 15, ...}` | Aggregated |
| `goal_excerpts` | NEVER provided | Never exposed |

### 8.3 Use cases

- Vendor identifies a category to build for ("142 demand events for vat_validation, no qualified provider")
- Vendor sizes adjacent market ("contact_enrichment has 3K monthly searches; here's host distribution")
- Vendor prioritizes integrations ("60% of our category's demand comes from n8n users; we should ship n8n integration")

### 8.4 Pricing

| Tier | Coverage | Price |
|---|---|---|
| Starter | 5 capability scopes | $10K / year |
| Pro | 25 capability scopes | $25K / year |
| Enterprise | All capabilities + custom segments | $50K / year |

### 8.5 Privacy and ethics

- Aggregation thresholds enforced (no segment exposed at K < 100)
- No individual goal text ever exposed
- No user identity, organization, or IP ever exposed
- Vendors agree to TOS prohibiting re‑identification attempts
- Year 2: external privacy audit before GA

---

## 9. Optional execution fee (tier 8)

A vendor‑opt‑in, vendor‑paid, hard‑capped attribution fee on calls flowing through our BYO‑credentials sandbox to that vendor. The 9th revenue line, introduced in Phase 2 once the BYO‑creds sandbox is GA and 5+ vendors are paying on tiers 4–6.

### 9.1 What it is — and what it is not

| Dimension | Choice | Why |
|---|---|---|
| Who initiates | Vendor opts in (signs addendum) | Default off for every vendor |
| Who pays | Vendor pays us, out of their own margin | User price never changes |
| User sees | Disclosure note in recipe metadata (`disclosure.execution_fee_active: true`) | Transparency without price impact |
| Rate range | 1–2% (vendor chooses within range) | Hard‑capped at 2% in DB constraint, service guard, and contract |
| Trigger condition | A sandboxed call (`GenericProtocolAdapter`) routes to an opted‑in vendor | User running the same recipe in Claude Desktop / Cursor / n8n directly triggers no fee |
| Ranking impact | **Zero** | Ranker does not import marketplace store; CI lint enforces; firewall audit verifies |
| Vendor benefit | Execution attribution analytics + sponsored eligibility + co‑marketing rights + founding‑cohort lock‑in | Vendor opts in because it's worth it for them, not because we forced it |
| Cooling‑off after opt‑out | 90 days before re‑opt‑in | Prevents on/off gaming |
| Closest comparable | Amazon Associates attribution fee | Not Stripe / Zapier / App Store take rate |

### 9.2 Why we introduced it (and why we said "no" for so long)

Original Phase‑2 spec excluded all per‑execution fees on the principle that any take rate compromises BYO‑credentials trust. Three subsequent observations changed that:

1. **Vendors actively asked for an attribution model.** Founding cohort interviews showed vendors *want* to know which recipe drove which call — and are willing to pay for it.
2. **Tier 6 (Sponsored Placement) needs an eligibility gate.** Without skin in the game, every vendor would request sponsored placement. The execution fee acts as a soft eligibility signal.
3. **The trust position holds — if the design is right.** Five invariants (opt‑in, default off, vendor‑paid, capped at 2%, decoupled from ranker) preserve every property of the original "no execution take rate" position while opening a vendor‑side revenue line.

The design is the answer. The fee is not a Zapier‑style universal margin; it is an Amazon‑Associates‑style attribution fee that vendors opt into for tangible benefits.

### 9.3 Vendor opt‑in flow

```
Vendor: "I want to opt in to the execution fee"
      │
      ▼
[ Sign rate-cap addendum (v1.YYYY-MM) ] — codifies the 2% hard cap
      │
      ▼
[ Choose rate (1.0% – 2.0%) ] — vendor picks within bounds
      │
      ▼
[ Activation ] — subscription row created; rate effective immediately
      │
      ▼
[ Runtime ] — every sandboxed call to this vendor's endpoint emits a charge row
      │
      ▼
[ Monthly close (1st of next month, 02:00 UTC) ] — invoice generated via Stripe
      │
      ▼
[ Optional opt-out anytime ] — 90-day cooling-off period before next opt-in
```

### 9.4 Invariants enforced architecturally

| Invariant | Where enforced |
|---|---|
| Rate ≤ 2% | DB CHECK constraint + service layer + charger guard + firewall audit |
| One active subscription per vendor | DB unique index + service guard |
| Cooling‑off 90 days after opt‑out | Service guard |
| User price never changes | Adapter does not modify `result.user_price` |
| User response never delayed by charge attempt | `asyncio.create_task` + try/except in adapter |
| Cap breach attempt is P0 | `firewall_alert.fire` on any rate > 2% at write time |
| Disclosure always present | Recipe step metadata always carries `execution_fee_active` |
| Decoupled from ranker | CI lint asserts `ranker.py` does not import marketplace modules |

### 9.5 Founding cohort 1‑year fee waiver

Founding cohort vendors who opt into the execution fee in Year 1 get a **12‑month waiver** — their fee accrues at 0% during the waiver. After the waiver, their negotiated rate (1.0–2.0%) kicks in. Rationale: validates the model + accumulates execution‑attribution data without burning founding goodwill on a vendor‑side bill in Year 1.

### 9.6 What the user sees

In the recipe response:
```json
{
  "step_id": "step_2",
  "capability": "company_data_lookup",
  "provider_id": "apollo-mcp",
  "disclosure": {
    "execution_fee_active": true,
    "execution_fee_note": "Apollo has opted into a 1.5% vendor-paid attribution fee on calls routed through PlanMyAgents' BYO-credentials sandbox. Your price to Apollo is unchanged. See /disclosure."
  }
}
```

If the user executes the same recipe in their own Claude Desktop / Cursor / n8n environment (without our sandbox), no fee applies and no disclosure note appears (because there's nothing to disclose).

### 9.7 Why this *strengthens* (not weakens) the moat

- **Adds a 9th revenue line** without compromising the trust position (because of the design invariants)
- **Improves benchmark data quality** — sandboxed calls + opt‑in vendors = highest‑signal benchmark data
- **Creates a soft eligibility gate** for sponsored placement (vendor showing skin in the game)
- **Differentiates from a "Zapier connector" model** — we're attribution, not margin
- **Aligns vendor incentive with ours** — when their recipe gets traction, we both benefit

---

## 10. Pricing model

### 10.1 Vendor‑side revenue ladder

| SKU | Free | Pro | Premium | Enterprise |
|---|---|---|---|---|
| Claimed profile | ✅ basic | ✅ + analytics | ✅ + branded styling | ✅ + multi‑listing manager |
| Verified Benchmark | — | $5K / cycle | $15K / cycle | $50K / cycle |
| Sponsored category placement | — | — | $10–50K / qtr | $50–100K / qtr |
| Lead routing | — | $10 / lead | $25 / lead | Volume tier |
| Demand‑Data API | — | — | $25K / yr | $50K / yr + custom |
| **Optional execution fee (tier 8)** | — | ✅ 1–2% opt‑in (vendor‑chosen rate within range; 1‑yr waiver for founding cohort) | Same | Same + dedicated attribution dashboards |

Most vendors enter at Free → Pro after first benchmark; large vendors enter at Enterprise directly. Execution fee opt‑in typically follows after vendor has been on Pro/Premium for ≥ 1 quarter.

### 10.2 Founding vendor cohort discount

First 10 vendors get:
- 50% off Verified Benchmark for Y1
- Lock‑in pricing for 3 years
- Product‑council seat (input on roadmap)
- Case study rights
- Direct founder access
- **12‑month waiver on optional execution fee (tier 8) if they opt in**

### 10.3 What we will NOT do

- **User‑side margin on agent execution** — user never pays more because of us. Tier 8 is vendor‑paid only.
- **Hold user payment flow** — we never sit between user and vendor. Vendor settles fee out of band, monthly.
- Sponsorship discounts in exchange for "exclusive vendor" rights (would create lock‑in we don't want)
- Refunds on bad benchmark results (vendor knew the deal: result is published whatever it is)
- "Pay extra to skip benchmark methodology" (would gut the credibility)
- **Allow execution‑fee rate > 2%** — hard cap by DB + service + contract; even if a vendor asks, we say no

---

## 11. Disclosure rules — what the user always sees

| Surface | Disclosure required |
|---|---|
| Sponsored placement | "Sponsored" badge always visible; disclosure note in category header |
| Verified Benchmark badge | "PlanMyAgents Verified Benchmark · YYYY‑Q#" with methodology link |
| Claimed profile | "Claimed by vendor" badge |
| Lead routing | User must explicitly opt‑in per request; default off |
| Demand‑Data API customer | Aggregated only; users never see "this data was sent to vendor X" |
| Founding vendor cohort | Listed publicly on `/founding-vendors` page (not hidden) |
| **Optional execution fee (tier 8)** | **`disclosure.execution_fee_active: true` in recipe step metadata when a sandboxed call routes to an opted‑in vendor; full opted‑in vendor list + rates published on `/disclosure`** |

A live disclosure page (`/disclosure`) lists every active sponsorship, every Verified Benchmark badge holder, every founding vendor, **and every execution‑fee opt‑in vendor with their rate** — the *complete* paid‑relationship inventory. Updated daily.

---

## 12. Vendor onboarding journey

### 12.1 First 30 days

| Day | What vendor does | What we do |
|---|---|---|
| 0 | Signs up at `vendor.planmyagents.com` | Send welcome + verify email |
| 1 | Claims first listing | DNS or email verification flow |
| 2–3 | DNS / email verification completes | Send "welcome to verified vendors" + offer onboarding call |
| 7 | Edits profile (description, screenshots, docs links) | Profile diff review (auto + lightweight manual for first edit) |
| 14 | Requests Verified Benchmark | Schedule run; send methodology agreement |
| 21 | Receives benchmark results | 7‑day right‑of‑reply window |
| 28 | Benchmark published | Badge live; vendor announcement co‑marketing |
| 30+ | Optional: sponsorship, lead routing, demand‑data subscription | Upsell flow via vendor dashboard |

### 12.2 KPI for vendor onboarding (Year 1)

- Claim verification rate: > 80% of vendors who start the flow complete it
- Time to first edit: < 7 days median
- First benchmark request: > 30% of claimed vendors within 90 days
- Vendor NPS at 90 days: > 40

---

## 13. Founding vendor cohort program

### 13.1 Why

The first 5–10 vendors who pay legitimize the marketplace. They become case studies, product council members, and reference customers for the next 100 vendors. Their experience determines whether the marketplace flywheel starts spinning. **They're also the first cohort that pilots the optional execution fee (tier 8) under a 12‑month waiver**, giving us attribution data without burning founding goodwill on a vendor‑side bill in Year 1.

### 13.2 Who we target

| Criteria | Why |
|---|---|
| Already shipping production MCP / A2A / AI‑native service | Has something to benchmark |
| Sells to developers or operators (not pure consumer) | Aligns with our ICP |
| Believes in vendor‑neutral validation (not playing platform games) | Won't push for ranking favors |
| Will commit to publishing benchmark results | Aligns with P11 |
| Influential in their category (Apollo, Hunter, Perplexity, Linkup, Tavily, Exa, Apify) | Validates our brand |

### 13.3 What they get

- 50% off Verified Benchmark for Year 1
- Lock‑in pricing for 3 years on all SKUs
- Founding badge on profile
- Product council seat (quarterly)
- Direct founder access
- Case study rights (we publish, they get co‑marketing)
- Annual founding vendor summit (Year 2+)
- **12‑month waiver on optional execution fee (tier 8) if they opt in**

### 13.4 What we get

- Marketplace validation (5–10 paying vendors before public marketplace launch)
- First 3 Verified Benchmark badges (revenue + credibility)
- Case studies for the next 100 vendors
- Roadmap signal from the people who use it most
- Distribution into their existing communities

---

## 14. Operational and legal

### 14.1 Legal / contractual

- Vendor TOS includes:
  - Acceptance of P11 (no pay‑for‑ranking)
  - Commitment to honor benchmark publication
  - Right of PlanMyAgents to display their badge / placement publicly
  - Anti‑re‑identification clause for Demand‑Data API
  - Termination rights for both parties with 30‑day notice
- **Optional execution fee addendum** (separate signature required to opt into tier 8):
  - Codifies 2% rate cap
  - Vendor pays out of own margin (not user‑facing)
  - 90‑day cooling‑off period after opt‑out
  - Disclosure requirement (`/disclosure` page + recipe metadata)
  - Termination rights with 30‑day notice
  - Addendum version is dated; vendor must sign current version
- Anti‑scraping protection on user data (Demand‑Data API specifically)
- Annual vendor‑neutrality audit (Year 2+ for external; Year 1 internal); audit explicitly checks rate‑cap enforcement and `ranker.py` import isolation

### 14.2 Billing

- Stripe handles payments (we never store card data)
- Invoiced annual for ≥ $5K commitments; monthly for smaller
- **Execution fee invoiced monthly via Stripe** with $10 minimum (smaller amounts roll over)
- Suspension after 30 days overdue; deletion after 90

### 14.3 Support

- Year 1: Founder + ops as first support tier
- Year 2: Dedicated vendor success rep (1 FTE per 200 paying vendors)
- All vendor tickets audit‑logged
- Quarterly NPS survey

### 14.4 Compliance

- GDPR for EU vendors (data residency option in Y2)
- SOC 2 Type 2 prep starts Year 1.5
- Annual external firewall audit Year 2 — explicitly covers execution‑fee rate‑cap enforcement and ranker isolation

---

## 15. Risks and what would break the marketplace

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **A vendor's poor benchmark gets leaked early** | Medium | Medium | Strict 7‑day right‑of‑reply window enforced before publication; legal review of vendor TOS |
| **Sponsored placement displayed without disclosure** | Low | **Existential** | CI lint + UI test + quarterly audit; treated as P0 |
| **Suspected pay‑for‑ranking by internal sales team** | Low | **Existential** | Sales has no access to ranker; quarterly audit of all sponsorship invoices vs ranking changes |
| **Founding vendor disputes benchmark result publicly** | Medium | Medium | Right‑of‑reply + external arbitration; transparent process maintains trust even in dispute |
| **Slow vendor side conversion** | Medium | Medium | Founding cohort discount; case studies; vendor success investment |
| **Marketplace cannibalizes free tier** | Low | Low | Free tier remains best‑in‑class; vendor pays for *visibility*, free users get same *ranking* |
| **Vendor over‑claims capabilities and our judge missed it** | Medium | Low | Judge re‑run quarterly; community reporting tool |
| **Lead routing privacy breach** | Low | High | Default off; explicit opt‑in per request; anonymized contact through inbox |
| **Demand‑Data API customer attempts re‑identification** | Low | High | Aggregation thresholds; TOS; legal teeth |
| **A competitor launches a pay‑for‑ranking marketplace and wins on optics** | Medium | Low | They win short‑term; we keep the trust position; long‑term the firewall is the moat |
| **Execution fee perceived externally as a "Zapier take rate" despite the design invariants** | Medium | Medium | Public messaging discipline: always lead with "vendor‑opt‑in, vendor‑paid, capped at 2%, decoupled from ranking" framing; comparable is Amazon Associates not Stripe; FAQ in BUSINESS_PLAN.md §17; disclosure page transparent |
| **Internal pressure to raise the 2% cap "just for one big vendor"** | Medium | **Existential** | The 2% cap is a board‑level invariant. Lifting it requires a P0 review and external advisor signoff. DB CHECK constraint forces a schema migration which is publicly visible. |
| **Execution fee charge fires but user response is delayed** | Low | Medium | Adapter hook is `asyncio.create_task` + try/except; charger has cached subscription lookup; fail‑open if marketplace store unreachable |
| **Vendor exploits opt‑in/opt‑out gaming** (opt out during slow months, in during high months) | Medium | Low | 90‑day cooling‑off period after opt‑out; vendor cannot re‑enter immediately |
| **Vendor disputes a charge after seeing the invoice** | Medium | Low | Charges are calculated from audit‑logged sandboxed calls; vendor can audit; right‑of‑reply within 30 days; written‑off if dispute upheld |

---

## 16. Cross‑references

- **Business model**: [BUSINESS_PLAN.md §7](../BUSINESS_PLAN.md#7-marketplace-economics-phase-2)
- **Architectural fit**: [ARCHITECTURE.md §7](ARCHITECTURE.md#7-marketplace-mechanics-phase-2)
- **HLD data model**: [HLD.md](HLD.md) — Vendor / ClaimedProfile / BenchmarkCertification / SponsoredPlacement entities
- **LLD schema**: [LLD.md §3.10](LLD.md#310-marketplace-tables-phase-2) — DDL for marketplace tables
- **Partnership integration**: [partnership-strategy.md](partnership-strategy.md)
- **Benchmark methodology**: [benchmark-methodology.md](benchmark-methodology.md)
