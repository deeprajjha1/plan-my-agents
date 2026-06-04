---
audience: vendor
target: Browserbase
capability: web_scraping
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/demand
last_validated: 2026-05-19
---

# Browserbase — web_scraping (headless-browser subset cell)

## Subject

`Browserbase as the headless-browser web_scraping cell on PlanMyAgents`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. Today our `web_scraping` shortlist is Firecrawl (single-URL fetch — shipping as a routable cell today) and Apify (structured-actors scrape — scoping). We want to add Browserbase as the **third cell**, scoped specifically to the subset where Firecrawl and Apify don't apply: login walls, JS-heavy pages, multi-step click-through flows, and consent-banner-blocked sites.

Why a distinct cell rather than rolling Browserbase into `web_scraping`: the goal *"scrape my LinkedIn search results"* and the goal *"scrape this static blog post"* should route to fundamentally different vendors, and the grader has to score them on different rubrics (session-cost-per-page vs. fetch-cost-per-page, JS-rendered DOM vs. raw HTML, etc.). One cell per shape.

The ask: a sandbox API key scoped to our benchmark cron + 30 min to walk through the 5-case suite, agree on the login-walled / JS-heavy test fixtures, and confirm the session-cost + DOM-fidelity rubric matches what your team would consider fair. Resulting rows land on `/agents/browserbase` alongside our existing Razorpay / Resend / Firecrawl cells (full grid at https://planmyagents.dev/partners) and update on every cron tick.

What we will NOT ask Browserbase for:
- API keys for customer workflows. We do not execute on behalf of users — the key is scoped to our benchmark cron and only runs the deterministic test fixtures in the public suite. We will never proxy a customer's session through Browserbase.
- An NDA before the first conversation. Pitch and methodology are public.
- Logo placement before there is a real partnership.

15 min next Tue / Wed / Thu (your timezone)? Happy to send a Calendly link or three concrete slots.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — having three cells (Firecrawl + Apify + Browserbase) under `web_scraping` is the whole point of the benchmark. The router picks per goal shape, not per vendor preference.
