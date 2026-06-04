---
audience: vendor
target: Perplexity
capability: web_search
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/demand
last_validated: 2026-05-19
---

# Perplexity — web_search (top-of-demand cell)

## Subject

`Perplexity as the web_search reference cell on PlanMyAgents`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. Every routing decision is backed by a benchmark row in a public Postgres store, and refusals land in a public demand signal at https://planmyagents.dev/demand.

`web_search` is consistently in the top 3 capabilities our planner refuses on today. We want to add Perplexity as the **reference** web_search cell — concretely, your `sonar` API exposes a deterministic citation contract that is much easier to grade than the open-web alternatives, which is why we want to anchor the cell on Perplexity rather than on a wrapped Google / Bing call.

The ask: a sandbox API key scoped to our benchmark cron + 30 min to walk through the 5-case suite and confirm the citation-quality + answer-grounding rubric matches what your team would consider a fair score. The resulting rows would land on `/agents/perplexity-sonar` alongside our existing Razorpay / Resend / Firecrawl cells (full grid at https://planmyagents.dev/partners) and update on every cron tick.

What we will NOT ask Perplexity for:
- API keys for customer workflows. We do not execute on behalf of users — the key is scoped to our benchmark cron and only runs the deterministic test queries in the public suite.
- An NDA before the first conversation. Pitch and methodology are public.
- Logo placement before there is a real partnership.

15 min next Tue / Wed / Thu (your timezone)? Happy to send a Calendly link or three concrete slots.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — we're also scoping Linkup and Tavily as parallel web_search cells (different baselines, different rubrics). If `web_search` is the wrong starting point, your read on `semantic_search` would also be very welcome.
