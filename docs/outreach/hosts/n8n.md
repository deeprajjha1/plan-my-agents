---
audience: host
target: n8n
capability: recipe-export-host
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/goal
last_validated: 2026-05-19
---

# n8n — recipe export → marketplace integration

## Subject

`PlanMyAgents → n8n: recipe-export integration + marketplace placement`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent supply across MCP, A2A, and AI-native services. Our planner takes a natural-language goal, decomposes it into capabilities, routes through tested + benchmarked providers when one exists, and refuses honestly when nothing is ready. The whole catalogue is browseable at https://planmyagents.dev/partners.

Reaching out specifically about n8n: our recipe export already emits **n8n-importable workflow JSON** for every routed plan. Today the user copy-pastes that JSON into their n8n instance; the obvious next step is a first-class integration where:

1. A "PlanMyAgents" node appears in the n8n marketplace, takes a goal as input, and emits the routed sub-workflow inline.
2. Every recipe we export carries an `n8n-version` field so the workflow is forward-compatible across n8n versions.

What this unlocks for n8n: every n8n user becomes a potential PlanMyAgents user the moment they hit a "I need to discover the right vendor for X" wall. From your side it's a routing layer on top of the vendors n8n users already use (Apollo, Firecrawl, Resend, …) — we're explicitly not competing with the n8n execution engine, we're feeding it.

What we will NOT ask n8n for:
- An NDA before the first conversation. Pitch and methodology are public.
- Logo placement before there is a real partnership.
- Exclusivity — our recipe export also emits Claude Desktop config, Cursor MCP config, and a bash CLI script. n8n is one of four targets today.

The ask is a 30-min scoping call with whoever owns the marketplace + integrations roadmap on your side. We can demo the goal → recipe → n8n-import flow live in 5 minutes, then spend 25 on what a real integration looks like and whether it's a fit for n8n's 2026 marketplace plans.

Next Tue / Wed / Thu (your timezone)? Happy to send a Calendly link or three concrete slots.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — try it yourself: take any vendor-discovery goal through https://planmyagents.dev/goal and click "Export → n8n". The JSON drops straight into a fresh n8n instance.
