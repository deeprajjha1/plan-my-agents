---
audience: vendor
target: Tavily
capability: web_search
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/demand
last_validated: 2026-05-19
---

# Tavily — web_search (second live cell after Razorpay)

## Subject

`Tavily as the second live web_search cell on PlanMyAgents`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. Today we have **three routable cells** in the public benchmark store (Razorpay live + Resend / Firecrawl response-fixture — full grid at https://planmyagents.dev/partners), and `web_search` is the most-requested capability we still can't route on. Public demand counts live at https://planmyagents.dev/demand.

We want to add Tavily as a benchmarked `web_search` cell — concretely, your `tavily-search` API is one of the two baselines on our shortlist for the AI-grounding use case (alongside Perplexity's `sonar`), and the answer-grounding contract you expose is exactly the shape our existing 5-case grader was written against.

The ask: a sandbox API key scoped to our benchmark cron + 30 min to walk through the 5-case suite and confirm the answer-grounding + citation rubric matches what your team would consider a fair score. Resulting rows land on `/agents/tavily-search` and update on every cron tick.

What we will NOT ask Tavily for:
- API keys for customer workflows. We do not execute on behalf of users — the key is scoped to our benchmark cron and only runs the deterministic test queries in the public suite.
- An NDA before the first conversation. Pitch and methodology are public.
- Logo placement before there is a real partnership.

15 min next Tue / Wed / Thu (your timezone)? Happy to send a Calendly link or three concrete slots.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — being the second live cell (after Razorpay) is genuinely useful for both sides: the deck stops saying "Razorpay live + two response-fixture" and starts saying "Tavily + Razorpay live", which is a different conversation with every customer.
