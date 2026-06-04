# Shared outreach template (structure all drafts follow)

Every draft in `vendors/`, `hosts/`, and `design-partners/` follows the same
9-element structure. Keeping the structure stable across drafts is what makes
the URL validator + the personalization checklist work. **Don't ship a draft
that drops one of these elements without a reason in the draft's frontmatter.**

```
1. Subject line                  — names vendor + capability, ≤ 60 chars
2. One-sentence opener           — what we are, in one breath
3. Specific reason for *them*    — why this vendor / host / persona
4. Live-URL anchor               — one production URL that proves the reason
5. The ask                       — concrete, time-bound, one sentence
6. What we won't ask for         — keys / NDA / exclusivity (vendors + hosts)
7. CTA                           — 2–3 concrete slots or Calendly link
8. Sign-off                      — founder name + ≤ 2 credibility anchors
9. PS                            — optional, links to deck or partners page
```

## The text scaffold

Copy-paste this into a new draft and fill in the bracketed fields. The
structure intentionally fits in one screen.

```
---
audience: <vendor | host | design-partner>
target: <Apollo | n8n | persona-claude-desktop-user | ...>
capability: <contact_enrichment | recipe-export-host | ...>
live_urls:
  - https://planmyagents.dev/<path-1>
  - https://planmyagents.dev/<path-2>
last_validated: <YYYY-MM-DD>
---

Subject: <≤ 60 chars, names target + capability>

Hi {first_name},

<One-sentence opener: what we are. e.g. "PlanMyAgents is a
discovery + benchmark layer for the AI-agent supply across MCP,
A2A, and AI-native services.">

<Specific reason for *them* — one sentence. References a *real*
deck claim and a *real* vendor / host / persona attribute.>

<Live-URL anchor — one sentence that names the URL and what it
proves. e.g. "You can see the current benchmark grid at
https://planmyagents.dev/agents/firecrawl — Firecrawl is one of
the three cells we have routable today.">

<The ask — one sentence, concrete, time-bound. e.g. "All we'd
need is a sandbox API key scoped to our benchmark cron for the
5-case web_scraping suite — the resulting rows would land on the
public agent page within 24h.">

<Vendors + hosts only: "What we won't ask you for" block.>

What we won't ask you for:
- API keys for customer workflows. We don't execute on behalf of
  users — the key is scoped to our benchmark cron and only hits
  test-mode endpoints where you support them.
- An NDA before the first conversation. The pitch is on the
  public deck (https://planmyagents.dev/partners).
- Logo placement before there is a real partnership.

15 min on <day-A> at <time-window> or <day-B> at <time-window>?
Calendly: <link>

Thanks,
{founder_name}
<credibility anchor 1, e.g. "ex-Adobe / Amazon Payments / Salesforce">
<credibility anchor 2, e.g. "github.com/{handle}">

PS — public partners page at https://planmyagents.dev/partners
lists the three asks (design partners, vendor intros, host BD)
and the live evidence behind each. Happy to discuss any of them.
```

## Why this structure

- **Subject line names the target + capability** → highest deliverability and
  highest reply rate. Vendors get hundreds of vague "would love to partner"
  cold emails per week; ours is filterable.
- **Opener + reason + URL anchor** answers the only three questions a
  recipient has in the first 5 seconds: what is this, why is it for me, and
  is it real.
- **"What we won't ask for"** block kills the most common reflexive
  objections (free API keys, NDAs, exclusivity).
- **Concrete slots beat "let me know when works"** by ~3× reply rate.
- **Sign-off with credibility anchors** is short but lets the recipient verify
  the founder is a real builder in 10 seconds.

## When to deviate

You can drop element #6 (the "what we won't ask for" block) for
design-partner drafts, since the asks for design partners are different (they
*do* run the recipe with their own credentials, on their own host, by design).

Don't drop any other element without leaving a comment in the draft's
frontmatter explaining why.
