---
audience: vendor
target: Linkup
capability: web_search
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/demand
last_validated: 2026-05-19
---

# Linkup — web_search (deterministic citation cell)

## Subject

`Linkup as a benchmarked web_search cell on PlanMyAgents`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. Every routing decision is backed by a benchmark row in a public Postgres store, and refusals land in a public demand signal at https://planmyagents.dev/demand.

`web_search` is consistently in the top 3 capabilities our planner refuses on today. We want to add Linkup as a benchmarked cell specifically because your deterministic citation contract makes the grader's job tractable — most search APIs return a free-form answer that's hard to score reproducibly, but Linkup's `(snippet, source_url, source_title)` triples are graded directly by our existing rubric without any LLM-judge ambiguity.

The ask: a sandbox API key scoped to our benchmark cron + 30 min to walk through the 5-case suite and confirm the citation-quality + freshness rubric matches what your team would consider fair. The resulting rows would land on `/agents/linkup` alongside our existing Razorpay / Resend / Firecrawl cells (full grid at https://planmyagents.dev/partners) and update on every cron tick.

What we will NOT ask Linkup for:
- API keys for customer workflows. We do not execute on behalf of users — the key is scoped to our benchmark cron and only runs the deterministic test queries in the public suite.
- An NDA before the first conversation. Pitch and methodology are public.
- Logo placement before there is a real partnership.

15 min next Tue / Wed / Thu (your timezone)? Happy to send a Calendly link or three concrete slots.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — we're also scoping Perplexity and Tavily as parallel web_search cells. The whole point of three cells is to let the router pick the right one per goal type (deterministic citations, deep-research, fresh-news), not to pick a "winner".
