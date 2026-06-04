---
audience: vendor
target: Apollo
capability: contact_enrichment
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/categories
  - https://planmyagents.dev/demand
last_validated: 2026-05-19
---

# Apollo — contact_enrichment cell

## Subject

`Apollo as a benchmarked cell in PlanMyAgents (contact_enrichment)`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. We are vendor-neutral by construction — every routing decision is backed by a benchmark row in a public Postgres store, and refusals land in a public demand signal at https://planmyagents.dev/demand.

I'm reaching out because `contact_enrichment` is consistently in the top 5 capabilities our planner refuses on today (you can see the live demand counts at the URL above), and Apollo's B2B identity graph is the cheapest, highest-coverage way to wrap that cell. We want to add Apollo as a benchmarked cell — `find_email_by_company_and_role` + `enrich_person_by_email` — alongside our existing Razorpay / Firecrawl / Resend cells (see https://planmyagents.dev/partners for the full grid).

What I'd need from your side is a sandbox API key scoped to our benchmark cron, plus 30 min to walk through the 5-case suite and confirm the scoring rubric matches what your team would consider a fair score. The resulting rows would land on `/agents/apollo` within 24h and update on every cron tick.

What we will NOT ask Apollo for:
- API keys for customer workflows. We do not execute on behalf of users — the key is scoped to our benchmark cron and only touches your sandbox / test endpoints if you support them.
- An NDA before the first conversation. The pitch and the benchmark methodology are on the public deck and at https://planmyagents.dev/partners.
- Logo placement before there is a real partnership.

15 min next Tue / Wed / Thu afternoon (your timezone)? Happy to send a Calendly link, or three concrete slots if that's easier.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — the full deck and partner grid live at https://planmyagents.dev/partners. If `contact_enrichment` is the wrong place to start, I'd also love your read on `email_verification` (we already have Hunter on the shortlist) and `web_search`.
