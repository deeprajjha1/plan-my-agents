---
audience: vendor
target: Resend
capability: email_send
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/agents/resend-emails
last_validated: 2026-05-19
---

# Resend — flip the existing cell from fixture to live

## Subject

`Resend: flipping our existing benchmark cell from fixture to live`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. Resend is already on the deck as **one of three routable cells today** (see https://planmyagents.dev/partners) — concretely we ship a Resend wrapper that runs against the 5-case `email_send` benchmark suite on every cron tick. The agent page is at https://planmyagents.dev/agents/resend-emails.

The catch: the cell ships today against our `MockResendEmailSender` using `onboarding@resend.dev` as the from address (your documented test sender) tagged with `output._provenance = "response_fixture_pending_live_key"`. Everything else is real — the registry, the wrapper shape, the grader, the discovery_candidates row, the `/agents/resend-emails` page. The only missing piece is a `RESEND_API_KEY` in our `.env` so the next cron tick can land a true live row next to the fixture row.

The ask: a sandbox API key scoped to our benchmark cron + 15 min to walk through the 5-case suite (all sends are gated to `onboarding@resend.dev` unless `RESEND_ALLOW_REAL_DOMAINS=1`), and confirm the `format_check(email_id)` scoring rubric matches what your team would consider fair. The fixture rows fall out of our 30-day routable window naturally as live rows accumulate.

What we will NOT ask Resend for:
- API keys for customer workflows. We do not execute on behalf of users — the key lives only on our benchmark cron VM and never touches customer-facing routing paths (we have an AST-based firewall audit script + a CI workflow that fails the build if a baseline import leaks into customer code).
- An NDA before the first conversation. Pitch and methodology are public.
- Logo placement before there is a real partnership.

15 min next Tue / Wed / Thu (your timezone)? Happy to send a Calendly link or three concrete slots.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — the wrapper code is already committed at `apps/api/planmyagents_api/benchmark/baselines/resend.py` (the same one that runs against the mock today). Flipping fixture → live is purely a credentials change on our side, and on Resend's side it's "issue a sandbox key".
