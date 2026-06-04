# Cold Email Templates — Days 1–14 Discovery Sprint

> Goal of this sprint: book **8–10 calls** with RevOps / Sales Ops / Growth Engineers from B2B SaaS companies $5–50M ARR. Get **3 verbal "I'd test it" commits** by Day 14 to gate the build phase.

---

## 1. Ideal Customer Profile (ICP)

| Filter | Criterion |
|---|---|
| Company type | B2B SaaS |
| Company ARR | $5M–$50M |
| Company HQ | US, UK, EU (English-language buyers) |
| Headcount | 50–500 |
| Buyer titles | RevOps Lead, VP RevOps, Head of Sales Ops, Sales Operations Manager, Growth Engineer, Marketing Operations Manager, Head of Demand Gen |
| Excluded | Enterprise Fortune 500 (different sales motion), pre-seed startups (no budget), agencies (different needs) |

**Why this ICP wins:**
- Real pain (manual enrichment is daily drudgery)
- Real budget ($30–150K/yr on data tooling)
- Reachable async (LinkedIn searchable, in known communities)
- Not too small (won't churn after 1 month) or too big (procurement nightmare)

---

## 2. Lead-sourcing playbook (where to find 100 prospects this week)

### Source 1: Apollo / Sales Navigator filters
```
Industry: Software (B2B)
Company size: 50–500 employees
Annual revenue: $5M–$50M (or proxies: Series A/B funded, 2020–2024)
HQ: US OR UK OR DE OR FR OR NL OR SE
Title (ANY): "RevOps" OR "Revenue Operations" OR "Sales Operations" OR "Sales Ops" OR "Marketing Operations" OR "Growth Engineer"
Seniority: Manager OR Director OR VP OR Head
```

Expected output: 500–2,000 matching profiles. Pick top 100 based on company recognizability.

### Source 2: Hiring signals (intent buyers)
LinkedIn job board search: companies posting for "RevOps Manager" or "Sales Operations Analyst" right now → they're feeling the pain enough to hire for it. Highest-converting cold-email targets.

### Source 3: Communities (warm-ish leads)
- **Pavilion** (RevOps community)
- **RevOps Co-op** Slack
- **RevGenius** Slack
- **Modern Sales Pros** Slack
- **r/RevOps**, **r/SalesOperations**

Lurk for 2–3 days. Note people asking enrichment / data-quality questions. DM them with the warm intro template (template #4 below).

### Source 4: Twitter/X
Search: `("enrichment" OR "data quality" OR "Apollo" OR "Clearbit") (from RevOps/sales ops bios)`
Reply to relevant pain tweets. Build context. DM after 1–2 interactions.

---

## 3. The four cold-email templates (test all four, pick winners)

### Template A — The "I'm researching" frame (Mom Test classic)

> **Subject:** Quick question — how does your team handle enrichment?

> Hi {first_name},
>
> I'm spending the next two weeks talking to RevOps leaders at SaaS companies your size to understand how the enrichment workflow actually works in practice (not the marketing version).
>
> Three questions take 15 minutes — I'm not selling anything, I'm researching:
> 1. How do you currently enrich 100+ leads with verified emails + LinkedIn?
> 2. What does it cost you per 1,000 enriched records (in $ or hours)?
> 3. What's the most annoying part of that workflow today?
>
> I'll share the aggregated findings back with everyone I talk to. Open to a quick call this week or next?
>
> Thanks,
> {your_name}
> ex-Amazon Payments / Adobe / Salesforce

**Why this works:** Mom-Test compliant (asks past behavior, not future intent). Specific. Reciprocity offer. Credibility footer without being braggy.

---

### Template B — The hiring-signal frame (highest open + reply rate)

> **Subject:** Saw the {role} job post — quick question?

> Hi {first_name},
>
> Noticed {company} is hiring a {Sales Ops / RevOps Manager}. Curious — what's the immediate fire that's driving the hire? (Genuinely asking — I'm researching how SaaS RevOps teams in the $10–50M ARR range deal with data enrichment / pipeline hygiene before I build something in this space.)
>
> If it's enrichment / lead gen / data quality related, I'd love 15 min on a call this or next week. If it's something else, no worries — and I owe you a coffee for reading this.
>
> {your_name}
> ex-Amazon Payments / Adobe / Salesforce

**Why this works:** Hyper-personalized (mentions their job post). Curious, not pushy. Buyers feel seen.

---

### Template C — The "I'm building, want your honest opinion" frame

> **Subject:** Building an AI router for enrichment workflows — would love your read

> Hi {first_name},
>
> Short version: I'm building software that takes a sentence like *"enrich these 1,000 leads with CTO emails + LinkedIn + recent product news"* and routes the work across the right specialist agents (Apollo, Firecrawl, Tavily, etc.), pays them, and returns a CSV with confidence flags and itemized cost.
>
> Before I write more code, I want to hear from 5 RevOps leaders at SaaS your size whether this would actually save you time vs. how you do it today.
>
> 15 minutes — I bring questions, you bring honest opinions, no slides. Free this week?
>
> {your_name}
> 11 yrs backend at Adobe / Amazon Payments / Salesforce

**Why this works:** Specific use case. Asks for honest critique (people love giving opinions). Time-bounded.

---

### Template D — Warm intro from a community

> **Subject:** Saw your post in {Pavilion / RevOps Co-op} about {topic}

> Hi {first_name},
>
> Saw your post yesterday about {their actual pain — quote/paraphrase}. Felt familiar — I'm building an enrichment orchestrator that addresses exactly that, and I'm trying to talk to 5 RevOps people who feel the pain to make sure I'm building the right thing.
>
> Would 15 min on Thursday or Friday work? I'll share what I'm building, you tear it apart honestly. No pitch, no slide deck.
>
> {your_name}

**Why this works:** Warm context. Quotes their words. Asks for critique not money.

---

## 4. The 4-touch sequence (over 11 days)

| Day | Touch | What to send |
|---|---|---|
| Day 0 | Email 1 | Template A or B (most-personalized version you can write) |
| Day 3 | Email 2 (bump) | One-liner: *"Hi {first_name} — bumping this in case it got buried. Genuinely a 15-min ask, no slides."* |
| Day 7 | Email 3 (value-add) | Send a useful link: *"PS — found this benchmark of email-verification accuracy across providers, thought your team might find it useful: {link}. Still curious about your workflow if you have 15 min."* |
| Day 11 | Email 4 (break-up) | One-liner: *"No worries if not the right time — closing the loop on this. If enrichment workflow ever becomes a priority, ping me. {your_name}"* |

**Sending cadence:** 30 emails/day, 5 days/week = 150/week. Expected reply rate: 8–15%. Expected call rate: 4–8%. So 100 sends → 4–8 calls.

---

## 5. Sending infrastructure

| Tool | Purpose | Approx cost |
|---|---|---|
| **Apollo.io** or **Sales Navigator** | Lead sourcing | $99–$149/mo |
| **Instantly.ai** or **Smartlead** or **Lemlist** | Cold-email sequencing + warm-up | $30–$97/mo |
| **DNS setup**: SPF + DKIM + DMARC on `planmyagents.dev` (or your domain) | Deliverability | Free, ~1 hr |
| **2 sending domains** (e.g., `try-planmyagents.dev` + `planmyagents-app.com`) with 3 mailboxes each | Avoid main domain reputation risk | $20/mo for domains |
| **Warm-up period** | 2 weeks of warmup before sending cold | (built into Instantly/Smartlead) |

**Critical:** never send cold from your main `planmyagents.dev` mailbox. Use throwaway sending domains. If reputation tanks, you don't lose your real domain.

---

## 6. Tracking spreadsheet (Google Sheet template)

| Column | Type | Example |
|---|---|---|
| `prospect_email` | text | jane@acme.com |
| `first_name` | text | Jane |
| `company` | text | Acme HR |
| `title` | text | RevOps Manager |
| `template_used` | enum | A / B / C / D |
| `personalization_note` | text | "saw job post for SDR Ops" |
| `sent_date` | date | 2026-05-05 |
| `opened` | bool | y/n |
| `replied` | bool | y/n |
| `reply_summary` | text | "interested, scheduling Tue" |
| `call_booked` | bool | y/n |
| `call_outcome` | enum | strong-yes / soft-yes / no |
| `design_partner_commit` | bool | y/n |

**Daily 5-min ritual:** at end of day, log all sends + responses. Weekly review every Sunday.

---

## 7. Common replies + how to respond

| Their reply | Your response |
|---|---|
| *"Sure, when works?"* | Reply within 1 hour with Calendly link OR 3 specific time slots. Speed matters. |
| *"Not the right person — talk to {name}"* | *"Thanks {first_name} — much appreciated. Will reach out to {name} and mention you sent me."* Then email {name} with the warm intro. |
| *"We use {Clay/Apollo/Outreach} already"* | *"Perfect — that's exactly the kind of perspective I want. I'm not asking you to switch, just to walk me through what works and what doesn't with {tool}. Still 15 min open?"* |
| *"Send me a deck"* | *"No deck — I'm at the research stage before building. Genuinely just questions for 15 min. If after that you want a deck, I'll send one."* |
| *"What's the price?"* | *"Honestly haven't priced it yet — depends what I learn from people like you. Happy to talk pricing on the call if useful."* |
| *"Sounds like {competitor}"* | *"Maybe — what do you use {competitor} for, and what's missing? That's the kind of thing I need to hear."* |
| *"We're not hiring"* (auto-reply) | Skip. Move on. |
| Hard no / unsubscribe | Remove from list. Mark `do not contact`. |

---

## 8. Personalization checklist (before sending each email)

For each prospect, in 60 seconds:
- [ ] Open their LinkedIn — do they have a recent post or job change? (Reference it.)
- [ ] Open their company website — what's their main product? (Reference it specifically.)
- [ ] Check their company's careers page — are they hiring for ops/RevOps? (Use Template B.)
- [ ] Check their tech stack on BuiltWith — do they use HubSpot, Salesforce, Outreach, etc.? (Reference it.)
- [ ] Personalize first sentence with one of the above. Keep rest of email template-stable.

**Rule:** if you can't personalize the first sentence in 60 seconds, skip the prospect.

---

## 9. Goal scoreboard (Day 14 gate)

Print this and check daily:

| Metric | Goal | Status |
|---|---|---|
| Emails sent | 200+ | __ |
| Replies | 20+ | __ |
| Calls booked | 8+ | __ |
| Calls completed | 8+ | __ |
| Verbal "I'd test it" commits | **3+** | __ |
| Specific pain phrases captured | 10+ | __ |

**If at Day 14 you have 3+ design-partner commits → start building (proceed to v0 sprint).**
**If you have 1–2 → extend sprint by 1 week.**
**If you have 0 → reassess vertical choice before writing code.**
