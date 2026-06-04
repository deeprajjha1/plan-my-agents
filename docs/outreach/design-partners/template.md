---
audience: design-partner
target: generic
capability: planner-recipe-export
live_urls:
  - https://planmyagents.dev/
  - https://planmyagents.dev/goal
  - https://planmyagents.dev/partners
last_validated: 2026-05-19
---

# Generic design-partner outreach (replace persona-specific bits inline)

This is the **template** every design-partner outreach starts from. Three
persona-specific intros follow underneath — see `_persona-fit-guide.md` in
this folder for which intro to use for which prospect.

The structural rule: opener + persona-specific hook + URL anchor + recipe-export
nudge + 30-min ask. Body should be ≤ 150 words and never feel auto-generated.

## Subject

`PlanMyAgents — 15-min for a refusal-with-reasons + a recipe you'd actually run?`

## Body — generic skeleton

```
Hi {first_name},

PlanMyAgents is a discovery + benchmark layer for the AI-agent
supply across MCP, A2A, and AI-native services. The planner takes
a natural-language goal, decomposes it into capabilities, routes
through tested + benchmarked providers when one exists, and
refuses honestly when nothing is ready. Live homepage strip with
the current counts: https://planmyagents.dev/

<persona-specific hook — see the three variants below.>

The ask: pick one real goal you'd want to automate, send it through
https://planmyagents.dev/goal, and tell me where the output (routed
plan OR refusal-with-reasons OR exported recipe) breaks for your
workflow. 30 min on a call, recorded if you're OK with that, so the
specific friction lands in our public demand signal.

What we will NOT ask you for:
- API keys for anything we run. You execute the recipe on your own
  host (Claude Desktop / Cursor / n8n / bash) with your own creds.
  We never see them.
- An NDA. The whole pitch is on https://planmyagents.dev/partners.
- A signed commitment to switch tools. The 30-min is research,
  not a sales call.

Next Tue / Wed / Thu (your timezone)? Calendly: <link>

Thanks,
Deepraj Jha
ex-Salesforce / Amazon Payments / Adobe — 11 yrs backend
https://planmyagents.dev/partners
```

---

## Persona-specific hook variants

Pick exactly one of the three and paste it in place of the
`<persona-specific hook>` line above. Don't mix them.

### Variant A — Claude Desktop / MCP power user

> Hook: I'm reaching out because you've been active in the MCP
> community (e.g. {a specific server you contributed / a thread
> you replied on}). The interesting unsolved problem in MCP today
> isn't "are there enough servers?" — it's "which of these three
> Postgres MCPs should I actually trust?" PlanMyAgents is what
> goes on top of that picker.

### Variant B — n8n workflow builder / Cursor power user

> Hook: I'm reaching out because you've built non-trivial n8n
> workflows / Cursor MCP setups already (e.g. {a specific workflow
> you posted / a public repo you maintain}). The recipe export
> emits {n8n-importable JSON | Cursor MCP config} directly — so
> the entire planner output is one paste away from running in the
> host you already use. I'm curious where that flow breaks for the
> goals *you'd* actually run.

### Variant C — RevOps / Sales Ops / Growth engineer

> Hook: Most of the people I'm talking to in this round are
> running enrichment / outbound / web-scraping workflows where the
> hard part isn't "pick a vendor" — it's "pick the right vendor
> per row and explain to the boss why the cost went up". Goals
> like *"enrich these 200 leads with verified emails, only if the
> per-row cost stays under 8¢"* are exactly what our planner +
> refusal-with-reasons is built for. Refusal-with-reasons matters
> more than the routed answer when you're spending real money.

---

## Sequence (4 touches, 11 days)

Same sequence as `docs/cold-email-template.md` §4. The variant above is
Email 1 / Day 0. Use the bump / value-add / break-up templates from there for
the follow-ups; they're already audience-tested for cold sends.

## Anchoring rule

Each design-partner draft cites at least two live URLs:

1. **Homepage** (`https://planmyagents.dev/`) — proves the live evidence strip
   shows real Postgres counts.
2. **`/goal`** (`https://planmyagents.dev/goal`) — proves the planner runs
   without a login.

If you cite anything else (e.g. a specific agent page), add it to the
frontmatter `live_urls` block and re-run `make outreach-validate-urls`.
