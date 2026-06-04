# Persona-fit guide — which draft to send to whom

The drafts in `vendors/`, `hosts/`, and `design-partners/` are not
interchangeable. Sending a vendor draft to a design partner (or vice versa)
sounds like spam and burns a relationship you can't easily un-burn.

This guide is a 60-second decision tree the founder uses **before** copying
any draft.

## Step 1 — Identify the prospect's role

| Their job title contains | Audience | Folder to start from |
|---|---|---|
| Product / Marketing / DevRel / RevOps **at one of the 10 named vendors** (Apollo, Hunter, Perplexity, Linkup, Tavily, Exa, Apify, Firecrawl, Browserbase, Resend) | **vendor** | `vendors/{vendor-slug}.md` |
| BD / Partnerships / Integrations **at n8n, Cursor, or Anthropic** | **host** | `hosts/{host-slug}.md` |
| Anyone else — individual contributor, indie hacker, RevOps at a non-vendor company, MCP enthusiast, etc. | **design-partner** | `design-partners/template.md` + one of the three persona hooks |

If the prospect's job title is **at a vendor we haven't named yet** (e.g. Clay,
Smartlead, Browser AI), do NOT use a vendor draft. Use the design-partner
template — they're a person, not yet a partnership target.

## Step 2 — Pick the right specific draft

### Vendor — use the matching `vendors/{slug}.md` directly

- The drafts in `vendors/` are 1:1 with the deck. Don't mix them. Apollo's
  draft references our `contact_enrichment` cell — sending it to Hunter
  (whose draft references `email_verification`) confuses both of them.
- If the vendor has two plausible capability buckets (e.g. Firecrawl could
  arguably ship a `pdf_extraction` cell as well), pick the cell the deck
  already names and reference the other only in the PS.

### Host — use the matching `hosts/{slug}.md` directly

- All three host drafts pitch a recipe-export integration, but the *shape* of
  the integration is different per host (n8n marketplace node ≠ Cursor
  command-palette plug ≠ Claude Desktop reranker). Don't paste one into the
  other.

### Design partner — pick exactly one of the three persona hooks

Open `design-partners/template.md` and pick the hook variant that fits:

| Persona signal | Hook variant | Why |
|---|---|---|
| You found them in an MCP community, on a Claude Desktop thread, or they contributed to an Awesome-MCP list | **A — MCP power user** | They already understand the picker-trust problem; the hook lands in 1 sentence. |
| You found them via a public n8n workflow, a Cursor blog post, or a non-trivial GitHub repo wired to MCP | **B — workflow builder** | They will judge us on whether the recipe export *actually* drops into their host without manual edits. The hook addresses that head-on. |
| You found them via a RevOps community (Pavilion / RevGenius / r/RevOps), a job post, or LinkedIn Sales Nav | **C — RevOps / Growth eng** | They care about per-row cost and explaining cost variance more than about agent routing. The hook leads with cost. |

If the prospect fits two variants, pick the one where the recipe export
matters more to their daily workflow. If you can't pick, default to **A** —
it's the only one where the prospect already cares about agent quality, so
the recipe-export framing isn't the load-bearing claim.

## Step 3 — Personalize one sentence

This is where the templates go from "AI-generated cold spam" to "real human
took 60 seconds with my profile". Choices in order of preference:

1. **A specific public artefact they made** — a tweet, a blog post, a
   repo, a workflow, a Pavilion thread. Quote it. ("Saw your post about X
   last week — that's exactly the gap PlanMyAgents tries to close.")
2. **A specific company signal** — a job post they shared, a launch they
   were involved in, a product their team owns. Reference it. ("Saw
   {company} is hiring for a RevOps Manager; the workflow that role would
   own is exactly the one PlanMyAgents tries to make trustable.")
3. **A specific mutual connection or community** — name them, name the
   community. ("Saw your reply in the n8n community Slack last Tuesday.")

If you can't find any of the above in 60 seconds, **skip the prospect**. The
template-only version always loses.

## Anti-patterns

- **Don't mix vendor + host + design-partner asks in one email.** Each draft
  has exactly one ask. If you find yourself wanting to ask for two things in
  one email, send two emails (separated by a few days).
- **Don't claim a partnership we don't have.** Hunter is "in the registry as
  capability_verified"; that's true. Hunter is not "our launch partner";
  that's not true (yet).
- **Don't drop the "what we won't ask you for" block from a vendor or host
  draft.** It is the single highest-leverage paragraph in those drafts. It
  pre-empts the reflexive objection ("oh, they want free API keys") and
  earns a reply.
- **Don't soften the refusal-with-reasons claim for design partners.** They
  *should* run a goal that gets refused; that's the most useful 30 minutes
  of their week from our side.

## Tracking

Use the spreadsheet schema in `docs/cold-email-template.md` §6. Add one
column: `outreach_audience` (vendor / host / design-partner). Reply rates
diverge wildly between the three; tracking them mixed is misleading.

## When in doubt

When you genuinely can't tell which audience a prospect is, default to
**design-partner**. The cost of treating a vendor like a design partner is
"the email is too generic and gets ignored". The cost of treating a design
partner like a vendor is "the email assumes a partnership budget they don't
have and offends them". The first is recoverable. The second is not.
