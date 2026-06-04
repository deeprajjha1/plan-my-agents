---
audience: vendor
target: Hunter
capability: email_verification
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/categories
last_validated: 2026-05-19
---

# Hunter — email_verification (upgrade from synthetic-baseline)

## Subject

`Hunter as a routable cell on PlanMyAgents (currently synthetic)`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. Hunter is already in our static registry as a `capability_verified` provider for `email_verification` — you can see the `email_verification` taxonomy (and Hunter's row) at https://planmyagents.dev/categories. The cell is currently scored against a **synthetic-baseline** adapter, which means the row exists but the deck has to caveat "synthetic" next to your name.

I'd like to upgrade that cell to a true live run. Concretely: we already have the 5-case `email_verification` benchmark suite written; all we'd need from your side is a sandbox API key scoped to our benchmark cron. The resulting Hunter rows would land in our public Postgres store with the `live` provenance tag (alongside Razorpay live + Resend / Firecrawl response-fixture — see https://planmyagents.dev/partners for the current grid), and a dedicated `/agents/hunter` page lights up the moment the first live run lands.

Why this matters for Hunter specifically: today the deck calls Hunter the **reference vendor** for `email_verification`. We'd rather that be a live-grader claim than a synthetic one.

What we will NOT ask Hunter for:
- API keys for customer workflows. We do not execute on behalf of users — the key is scoped to our benchmark cron and only hits Hunter's `email-verifier` endpoint in deterministic test mode.
- An NDA before the first conversation. The methodology and the test cases are on the public site.
- Logo placement before there is a real partnership.

15 min next Tue / Wed / Thu (your timezone)? I'll send a Calendly link in reply, or three concrete slots if that's easier.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — the 5-case suite is small enough that the upgrade is a one-evening change on our side; the only blocker is a sandbox key.
