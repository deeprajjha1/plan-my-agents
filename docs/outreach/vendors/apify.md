---
audience: vendor
target: Apify
capability: web_scraping
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/agents/firecrawl
last_validated: 2026-05-19
---

# Apify — web_scraping (second-vendor cell next to Firecrawl)

## Subject

`Apify as a second web_scraping cell on PlanMyAgents (next to Firecrawl)`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. Today our routable shortlist for `web_scraping` is Firecrawl (response-fixture cell shipping today — see https://planmyagents.dev/agents/firecrawl) and Browserbase (scoped for the headless-browser subset). We want to add Apify as the **second-vendor cell** so the router can compare a structured-actors-style scrape against Firecrawl's single-URL fetch on the same 5-case suite.

Why Apify specifically: the actor ecosystem means a goal like *"scrape every TechCrunch front-page story from the last 7 days"* routes to a fundamentally different shape than Firecrawl's single-URL `scrape`, and the benchmark needs to grade both shapes side-by-side rather than pretending one vendor wins all of `web_scraping`.

The ask: a sandbox API key scoped to our benchmark cron + 30 min to walk through the 5-case suite, agree which actors to call for each case, and confirm the structured-output + cost-per-page rubric matches what your team would consider fair. Resulting rows land on `/agents/apify` and update on every cron tick.

What we will NOT ask Apify for:
- API keys for customer workflows. We do not execute on behalf of users — the key is scoped to our benchmark cron and only runs the deterministic test queries in the public suite. Where Apify supports a test/preview run, we'll use that exclusively.
- An NDA before the first conversation. Pitch and methodology are public.
- Logo placement before there is a real partnership.

15 min next Tue / Wed / Thu (your timezone)? Happy to send a Calendly link or three concrete slots.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — having Apify + Firecrawl + Browserbase as three distinct cells (rather than one "web_scraping" cell with a vendor pick) is the whole point of the benchmark. Different goal shapes → different vendors.
