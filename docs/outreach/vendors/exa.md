---
audience: vendor
target: Exa
capability: semantic_search
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/categories
last_validated: 2026-05-19
---

# Exa — semantic_search (embedding-based cell, distinct from web_search)

## Subject

`Exa as the semantic_search reference cell on PlanMyAgents`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. Every routing decision is backed by a benchmark row in a public Postgres store, and our discovery categories (https://planmyagents.dev/categories) already separate `web_search` (Tavily-style keyword + freshness) from `semantic_search` (Exa-style embedding-based retrieval) because the grader rubrics are genuinely different shapes.

We want to add Exa as the **reference** `semantic_search` cell. The reason it's worth its own cell, separate from `web_search`: Exa's `find_similar` + `search` API surfaces results no keyword-search API can find (analogy-driven, "give me more papers like this one" queries), which means our planner can route the right capability to the right vendor instead of forcing every search through one API.

The ask: a sandbox API key scoped to our benchmark cron + 30 min to walk through the 5-case `semantic_search` suite and confirm the embedding-quality + freshness rubric matches what your team would consider fair. Resulting rows land on `/agents/exa` alongside our existing Razorpay / Resend / Firecrawl cells (full grid at https://planmyagents.dev/partners) and update on every cron tick.

What we will NOT ask Exa for:
- API keys for customer workflows. We do not execute on behalf of users — the key is scoped to our benchmark cron and only runs the deterministic test queries in the public suite.
- An NDA before the first conversation. Pitch and methodology are public.
- Logo placement before there is a real partnership.

15 min next Tue / Wed / Thu (your timezone)? Happy to send a Calendly link or three concrete slots.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — separating `semantic_search` from `web_search` is the call where the deck gets the most pushback. Your read on whether that's the right cleavage (and on how Exa would want the cell scoped) would be very welcome.
