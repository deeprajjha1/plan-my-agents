---
audience: vendor
target: Firecrawl
capability: web_scraping
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/agents/firecrawl
last_validated: 2026-05-19
---

# Firecrawl — flip the existing cell from fixture to live

## Subject

`Firecrawl: flipping our existing benchmark cell from fixture to live`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. Firecrawl is already on the deck as **one of three routable cells today** (see https://planmyagents.dev/partners) — concretely we ship a Firecrawl wrapper that runs against the 5-case `web_scraping` benchmark suite on every cron tick. The agent page is at https://planmyagents.dev/agents/firecrawl.

The catch: the cell ships today against our `MockFirecrawlScraper` (RFC-2606 reserved test domains only — `example.com`, `test.invalid`, etc.) tagged with `output._provenance = "response_fixture_pending_live_key"`. Everything else is real — the registry, the wrapper shape, the grader, the discovery_candidates row, the `/agents/firecrawl` page. The only missing piece is a `FIRECRAWL_API_KEY` in our `.env` so the next cron tick can land a true live row next to the fixture row.

The ask: a sandbox API key scoped to our benchmark cron + 15 min to walk through the 5-case suite, confirm the test URLs we'll hit live (we already gate non-reserved domains behind `FIRECRAWL_ALLOW_REAL_DOMAINS`), and confirm the scoring rubric. The fixture rows fall out of our 30-day routable window naturally as the live rows accumulate.

What we will NOT ask Firecrawl for:
- API keys for customer workflows. We do not execute on behalf of users — the key lives only on our benchmark cron VM and never touches customer-facing routing paths (we have an AST-based firewall audit script + a CI workflow that fails the build if a baseline import leaks into customer code).
- An NDA before the first conversation. Pitch and methodology are public.
- Logo placement before there is a real partnership.

15 min next Tue / Wed / Thu (your timezone)? Happy to send a Calendly link or three concrete slots.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — the wrapper code is already committed at `apps/api/planmyagents_api/benchmark/baselines/firecrawl.py` (the same one that runs against the mock today). Flipping fixture → live is purely a credentials change on our side, and on Firecrawl's side it's "issue a sandbox key".
