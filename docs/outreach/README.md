# Outreach drafts

> **Audience:** the founder (you). These drafts are ready-to-send cold emails
> aimed at three different supply-side audiences. The customer-side cold
> sequence lives in `docs/cold-email-template.md` — do **not** confuse the two.

## What lives where

| Folder | Audience | Goal of the email |
|---|---|---|
| `vendors/` | Product / RevOps / DevRel at the 10 capability vendors named on the deck (Apollo, Hunter, Perplexity, Linkup, Tavily, Exa, Apify, Firecrawl, Browserbase, Resend) | Get the vendor to send PlanMyAgents a sandbox API key so the 5-case benchmark cell can flip from *response-fixture* / *not-yet-routable* to a **true live row** in the public benchmark store. |
| `hosts/` | BD / partnerships at the three host targets (n8n, Cursor, Anthropic) | Get a 30-min intro call to scope a recipe-export integration (n8n marketplace, Cursor command-palette plug, Claude Desktop reranker). |
| `design-partners/` | Individual devs / operators on Claude Desktop, Cursor, or n8n | Get the recipient to take one goal through `/goal`, run the recipe on their own host with their own credentials, and book the 30-min onboarding call in `docs/design-partner-onboarding.md`. |

## How to use a draft

1. Open the draft for the target.
2. Replace every `{angle_bracket}` placeholder. There are typically only **two**: `{first_name}` and `{reason_you_can_reach_them}` (warm intro, post you saw, mutual connection, etc).
3. **Run the URL validator** (`make outreach-validate-urls`) to confirm every link the email cites still returns 200 from the live site. URLs change; the validator catches it.
4. Send from a sending domain that is **not** `planmyagents.dev` (see `docs/cold-email-template.md` §5 for the warmup playbook).
5. Log the send in the tracking sheet (`docs/cold-email-template.md` §6 has the schema).

## Anchoring rule

Every draft cites at least one **live URL on the production site** that proves the claim being made. The URL list is:

- `https://planmyagents.dev/` — homepage live-evidence strip
- `https://planmyagents.dev/demand` — public top-capabilities demand signal
- `https://planmyagents.dev/partners` — this page is the destination CTA for most replies
- `https://planmyagents.dev/categories` — discovery categories index
- `https://planmyagents.dev/agents/{firecrawl,resend-emails}` — specific agent detail pages for the two routable cells whose live discovery_candidates row has been seeded. Hunter is intentionally missing from this list — until a real vendor sandbox key lands, `/agents/hunter` 404s and the drafts cite `/categories` instead.
- `https://planmyagents.dev/goal` — the planner endpoint

If you change the production URL of any of these, **update every draft in this folder in the same PR.** The `scripts/validate_outreach_urls.py` guard catches drift but only after the fact.

## Anti-patterns (don't ship a draft that does any of these)

- **No vague subject lines.** Each draft has a specific subject that names the vendor + capability.
- **No "do you want to grab time"** without offering 2–3 concrete slots or a Calendly link.
- **No ask for free API keys disguised as a partnership.** Be explicit: we want a sandbox/free-tier key for benchmarking, the benchmark output is public on `/agents/{vendor}`, and either side can pull out at any time.
- **No claim of customers we don't have.** Today the deck says "solo founder, 0 paying customers, ~3 active benchmark cells". Drafts must say the same.
- **No fake mutual connections.** If we don't know someone, we don't have a warm intro. Use a hiring-signal or RFP-style opener instead.

## What we will not ask vendors for

Every vendor draft repeats this list verbatim (mirrors the `/partners` page):

1. **No API keys for customer workflows.** PlanMyAgents does not execute customer workflows or hold customer credentials. The keys we ask for are scoped to our benchmark cron, run from our infra, and only touch RFC-2606 test-mode endpoints when the vendor supports them.
2. **No NDA before the first conversation.** The pitch is on the public deck.
3. **No logo placement before there is a real partnership.** Vendor-neutral positioning is more valuable to us than a partner-logo grid.

## Quality bar before sending

Each draft is shipped through this checklist:

- [ ] Subject ≤ 60 characters, names the vendor + capability.
- [ ] Body ≤ 150 words.
- [ ] One specific live URL is cited.
- [ ] One specific reason this vendor (not a generic "we'd love to partner").
- [ ] One CTA with concrete slots or a Calendly link.
- [ ] "What we won't ask you" block at the bottom (vendors + hosts only).
- [ ] Signature names the founder + one credibility anchor.
- [ ] `make outreach-validate-urls` passes.

## Deployment state

> ⚠ **Don't send a draft before `planmyagents.dev` resolves.**
>
> Every draft cites `https://planmyagents.dev/...`. As of `last_validated`
> on the most recent draft, that domain does **not** resolve publicly — the
> site exists only on a local dev box. The two-step gating before any draft
> goes out:
>
> 1. The production deploy lands (the domain resolves with a 200 on `/`).
> 2. `make outreach-validate-urls` (which defaults to the production base)
>    passes with zero failures.
>
> While the site is dev-only, sanity-check drafts with
> `make outreach-validate-urls-local` against `http://127.0.0.1:3000`.
> That target rewrites every `planmyagents.dev` URL to the local base so
> the validator still catches typos, dead routes, and unseeded agent IDs.
