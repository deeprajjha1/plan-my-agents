---
audience: host
target: Anthropic
capability: recipe-export-host
live_urls:
  - https://planmyagents.dev/partners
  - https://planmyagents.dev/goal
last_validated: 2026-05-19
---

# Anthropic — Claude Desktop reranking layer over MCP

## Subject

`PlanMyAgents → Claude Desktop: benchmarked reranking over discovered MCPs`

## Body

Hi {first_name},

PlanMyAgents is a discovery + benchmark + ranking layer for the AI-agent supply across MCP, A2A, and AI-native services. Our planner takes a natural-language goal, decomposes it into capabilities, routes through tested + benchmarked providers when one exists, and refuses honestly when nothing is ready. The whole catalogue is browseable at https://planmyagents.dev/partners.

Reaching out specifically about Claude Desktop: today Claude Desktop already surfaces MCP servers to the user, but there's no trust signal on top — a user who adds three "Postgres MCP" servers has no way to know which one will actually return correct results, and Claude itself has no way to prefer one over the other beyond "the first one in the list". A reranking + benchmark layer that, at MCP-server-selection time, says *"use these three in this order, with these confidence scores from the public benchmark store"* is exactly the unsolved trust gap on top of MCP.

Concretely: our recipe export already emits **Claude Desktop MCP config** for every routed plan. The integration we want to scope is the reverse — Claude Desktop calls our `/route` endpoint at MCP-selection time, gets back a ranked list with benchmark scores + capability tags, and surfaces that ranking in the picker UI.

What this unlocks for Anthropic: the MCP ecosystem grows fastest if users trust the picker, and the picker is more trustworthy if it's backed by a public benchmark grid rather than alphabetical / install-order sorting.

What we will NOT ask Anthropic for:
- An NDA before the first conversation. Pitch and methodology are public.
- Logo placement before there is a real partnership.
- Exclusivity — our recipe export also emits n8n workflow JSON, Cursor MCP config, and a bash CLI script. Claude Desktop is one of four targets today.

The ask is a 30-min scoping call with whoever owns the Claude Desktop MCP roadmap on your side. We can demo the goal → benchmark-ranked MCP picker flow live in 5 minutes, then spend 25 on what a real reranking integration looks like.

Next Tue / Wed / Thu (your timezone)? Happy to send a Calendly link or three concrete slots.

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners

PS — try it yourself: take any goal that needs an MCP server through https://planmyagents.dev/goal and click "Export → Claude Desktop config". The block drops straight into the Claude Desktop MCP config file.
