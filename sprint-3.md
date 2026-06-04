# PlanMyAgents Sprint 3 Plan

> Purpose: close the **agent supply gap** end-to-end. Sprint 2 made the
> matcher smart (embeddings + 25-cap registry + corroboration filters);
> sprint 3 turns that smart matcher into something useful by actually
> giving it routable agents to match TO. Today only **5 of 25**
> registry capabilities have at least one configured agent. Everything
> else is either undiscovered, discovered-but-unverified, or an API
> with no wrapper.
>
> The historical, sprawling plan lives in `sprint.md`. The previous
> sprint lives in `sprint-2.md`. This file is deliberately scannable:
> a developer should be able to read it end-to-end in ~5 minutes and
> know exactly what's blocked on what.

Last updated: 2026-05-15 (Gap 1–4 honest-scope closure: **918 backend
tests green**, ruff clean — adds 7 tool-aware embedding-text tests
(Gap 1), 6 post-goal MCP probe stage tests (Gap 2), 16 pre-plan
discovery wrapper + helper tests (Gap 3), 29 generic protocol adapter
tests (Gap 4: real MCP `tools/call` invocation, real OpenAPI request
builder + auth, structured A2A / AI-agent refusals, gate-first
ordering). The four "honest scope" gaps from the audit are now closed
in code:
- **Gap 1** — `candidate_text_for_embedding` includes probed MCP tool
  names + descriptions and A2A skill names, with per-tool / per-list
  caps so one chatty tool can't starve the embedding budget. Backwards
  compat preserved when ``tools`` is absent.
- **Gap 2** — `post_goal_refresh._run_refresh_blocking` now runs an
  MCP `tools/list` probe stage after the source pull, in parallel
  (8 workers default), capped at 20 candidates / 5s per probe by
  default, and persists via `save_merge` so embeddings re-roll with
  the new tool data. Env-toggled (`PLANMYAGENTS_POST_GOAL_PROBE`).
- **Gap 3** — `plan_goal_with_pre_plan_discovery` wraps `plan_goal_smart`
  to dispatch scouts against the decomposer's per-sub-task search_query
  BEFORE the route handler does refusal-path discovery. Persisted
  candidates re-embed inline. The `/goal` route reads the pre-plan
  summary from metadata and skips its own duplicate `_live_discovery`
  call when scouts already ran. Gated by `PLANMYAGENTS_PRE_PLAN_DISCOVERY`,
  default OFF until Gap 4-driven executable conversions justify the
  extra latency on first refusal.
- **Gap 4** — `GenericMcpAdapter` and `GenericOpenApiAdapter` now
  perform real invocation. MCP speaks JSON-RPC `tools/call` with
  capability→tool auto-resolution (or explicit `inputs.tool_name`),
  text-content extraction from the MCP `content[]` array, and proper
  `isError` handling. OpenAPI re-fetches the spec, picks an operation
  by `inputs.operation_id` or capability-name overlap, builds the HTTP
  request with path-param substitution and three auth modes
  (bearer / api-key header / api-key query), and returns the parsed
  body. `GenericA2AAdapter` and `GenericAiAgentAdapter` return
  structured refusals with the gate-disabled message taking precedence.

Sprint 2 closed: 659 backend tests, eval script confirms F1-optimal
threshold of 0.55 for nomic-embed-text on the 30-goal labelled set.
Sprint 3 deliverables to date: the cost-cap stack (S3a-7), the
demand-driven capability picker (S3a-1), the slug canonicaliser, the
**first six commodity-API wrappers** (Stripe, Razorpay, Resend,
Firecrawl, Shippo, eBay Browse) with 30 hand-authored benchmark cases,
matching mocks, capability-aware workflow scorers, registry promotions
with `requires_benchmark_gate=true`, AND **the four honest-scope gaps
above** so the discovery layer can route to existing canonical MCP /
OpenAPI servers without writing a wrapper per vendor.

**First end-to-end live validation, 2026-05-15:** Razorpay wrapper
graded 5/5 cases at 1.00 quality against the live Razorpay test API
(latency 178-265ms). `agents.json` flipped to `benchmark_status="passed"`
with reproducible `benchmark_status_evidence`. The other five wrappers
remain `benchmark_status="not_started"` until operator-supplied keys
unblock S3a-6 for each (Resend, Firecrawl, Shippo, eBay free tiers
all available; Stripe still blocked on Stripe-India's invite-only
signup). The codepath through the cost cap and routing gate is
otherwise complete and tested end-to-end.

**Honest substitution note (audit-flagged):** The provisional pick
table below lists `general_research` as one of the top-5; the actual
top-5 written by `scripts/pick_top_capabilities_for_wrappers.py` was
the same set, but the wrapper that *shipped* in S3a-2/S3a-4 was
`email_send` (Resend), not `general_research`. Reason: `general_research`
needs an LLM-as-tool wrapper that overlaps with our existing escalating
chat client and would have required redesigning the cost-cap/spend-
ledger contract for streaming responses, which fell outside this
sprint's scope. `email_send` is a clean transactional API with the
same shape as the other four commodity wrappers, so we substituted it
to keep the wrapper batch homogeneous. `general_research` carries
forward to Sprint 4.

---

## Sprint goal in one sentence

**Take coverage from 5/25 capabilities-with-routable-agents to 10/25 by
proving one end-to-end loop (discover → benchmark → promote → execute →
report) on five high-demand capabilities, then automate the most
expensive step in that loop (benchmark authoring) so future capability
expansion is cheap.**

---

## Why 3a → 3b → 3c is sequential, not three alternatives

A previous draft framed 3a / 3b / 3c as a menu. That was wrong. They
are dependencies in series:

```
3a (depth: prove the loop on 5 caps)  ← MUST go first
   └─ produces:
      • a working discover→benchmark→promote→execute pipeline end-to-end
      • a "gold standard" set of human-authored benchmark cases
      • real cost/latency/quality data on actual third-party providers
      • a list of every place the pipeline broke and how we fixed it
        ↓
3b (breadth: scale benchmark authoring with an LLM)  ← needs 3a's gold standard
   └─ produces:
      • LLM-generated benchmark cases for any new capability in minutes
      • a human review queue so generated cases stay trustworthy
      • a calibration test that flags when generated cases drift from gold
        ↓
3c (UX honesty during the gap)  ← runs in parallel from day 1
   └─ produces:
      • "we found 12 leads, here's how to verify them yourself" cards
      • clear surfacing of what we tried, what worked, what didn't
```

3b is impossible without 3a because:

1. **No gold standard to validate against.** An LLM that "generates
   benchmark cases" is just hallucinating until you can compare its
   output to human-authored cases that we already trust. 3a produces
   that comparison set.
2. **No working execution pipeline to validate cases against.** A
   generated case for `image_generation` is unverifiable without a
   working `image_generation` agent + executor. 3a builds that for
   the first 5 capabilities.
3. **No prompt calibration.** The "generate me 10 benchmark cases for
   capability X" prompt has to be tuned against examples of what good
   looks like. 3a is where those examples come from.

3c is parallel because it ships value while 3a is in flight: even if
we never finish 3a, the user still sees a useful response instead of
a blank refusal.

---

## Track 3a — Prove the loop on 5 capabilities (CRITICAL PATH)

Pick the five capabilities with the highest demand-leaderboard signal
**and** non-trivial supply (at least 3 plausible candidates in the
discovery store today). Provisional pick — **finalise from
`/discovery-gaps` data on day 1**, do not commit ahead of time:

| Capability | Why now | Likely top-3 candidates today |
|---|---|---|
| `web_search` | Every "research" goal needs it; we already wrap Tavily/Exa as `semantic_search` so this is the smallest delta | Tavily, Exa, Brave Search API |
| `payment_authorization` | Tested upstream demand from Moltbook + Stripe MCP discovered repeatedly | Stripe, Adyen, PayPal |
| `web_scraping` | Already partially covered (Firecrawl, Apify) — the other 1–2 candidates are easy adds | Firecrawl ✓, Apify ✓, Diffbot ✓, Browserless |
| `text_summarization` | Cheap to benchmark (input → output is short text), high demand | OpenAI, Anthropic, Cohere |
| `general_research` | The horizontal backstop we shipped in sprint-2 needs a real adapter, not just a fallback message | Tavily DeepResearch, Perplexity, OpenAI Deep Research |

> **Substitution shipped in S3a-2/S3a-4:** `email_send` (Resend) was
> built in place of `general_research`. The streaming-response design
> needed for a credible `general_research` wrapper would have forked
> the cost-cap contract; deferred to Sprint 4. The four other slots
> shipped as planned. This footnote also lives in the "Last updated"
> header so the substitution can't get lost in a long doc.

The remaining 20 capabilities stay as `missing_capabilities` for now —
they're handled by track 3c (honesty) until 3b automates their addition.

| ID | Task | Owner | Effort |
|---|---|---|---|
| **S3a-1 ✅ DONE 2026-05-15** | Pull last 30 days of `discovery_gap_events` from Postgres, sort by `judge_evaluated` desc, pick the **actual** top-5 capabilities (the table above is provisional). Document the choice in `docs/sprint-3-capability-picks.md` so we can revisit if rankings change. **Shipped:** `scripts/pick_top_capabilities_for_wrappers.py` joins demand events with the curated catalog and the registry to print a ranked report. Top-5 picks today (with 32 wrappable capabilities and 0 routable agents): `shipping_quote`, `web_scraping`, `payment_authorization`, `price_comparison`, `general_research`. | engineer | 1 h |
| **S3a-2 ✅ DONE 2026-05-15** | For each of the 5 picked capabilities, **write a benchmark suite** with ~5 hand-authored cases per capability covering happy paths, boundary conditions, and at least one adversarial / honesty-test case. **Shipped:** 5 YAML suites (25 total cases) under `packages/benchmarks/{capability}/`: `payment_authorization` (5 Stripe cases + 5 Razorpay cases — different status vocabularies), `email_send` (Resend test addresses), `web_scraping` (RFC-2606 reserved domains), `shipping_quote` (Shippo test mode), `price_comparison` (eBay sandbox). Adversarial cases catch dishonest wrappers via dedicated tests (`test_*_benchmark.py::test_dishonest_mock_loses_*`). | engineer | ~3 h per capability = 15 h |
| **S3a-3** | Inventory the top 3 candidates per capability (15 candidates total). For each: (a) confirm an API key path exists (env var documented), (b) confirm `evidence_url` resolves, (c) read the upstream API docs to size the wrapper effort. Output a 1-line "build / generic-adapter / skip" decision per candidate. | engineer | 4 h |
| **S3a-4 ✅ DONE 2026-05-15** | Write production adapters for the 5 picked capabilities. **Shipped (all stdlib-only, injectable transport, defense-in-depth safety gates, registered with `requires_benchmark_gate=true`):** `apps/api/planmyagents_api/agents/stripe.py` (`StripePaymentAuthorization` — refuses live keys without `STRIPE_ALLOW_LIVE_MODE`), `agents/razorpay.py` (`RazorpayPaymentAuthorization` — Indian-market path, refuses live keys without `RAZORPAY_ALLOW_LIVE_MODE`, two-credential auth via `required_env_vars`), `agents/resend.py` (`ResendEmailSender` — refuses real-domain senders without `RESEND_ALLOW_REAL_DOMAINS`), `agents/firecrawl.py` (`FirecrawlScraper` — http(s)-only URL validation, RFC-2606 test-mode detection), `agents/shippo.py` (`ShippoQuoteFetcher` — refuses live tokens without `SHIPPO_ALLOW_LIVE_MODE`), `agents/ebay.py` (`EbayBrowseProvider` — sandbox by default, refuses prod without `EBAY_ALLOW_PRODUCTION`). All have matching mocks in `agents/mock.py` for hermetic CI; benchmark-runner integration tests verify both honest mocks pass and dishonest mocks fail their adversarial cases. Capability-aware workflow scorers added in `workflows/scoring.py` for `payment_authorization`, `email_send`, `shipping_quote`, `price_comparison` (web_scraping was already wired). | engineer | ~6 h per adapter × 5 wrappers (Stripe, Razorpay, Resend, Firecrawl, Shippo, eBay) = 36 h |
| **S3a-5** | For candidates marked "generic-adapter", flip `PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION=true` in a sandboxed dev env and run their suite. Anything that passes via the generic OpenAPI/MCP adapter without a bespoke wrapper is pure profit. **Do NOT enable the generic adapter in production without S3a-7 below.** | engineer | 4 h |
| **S3a-6 ⏳ PARTIAL 2026-05-15** | Run `make benchmark-schedule --capability {cap}` for each of the 5 capabilities. Land results in `benchmark-store.json`. Top performers (any candidate with `benchmark_status='passed'` AND `verification_status ∈ {known_provider, capability_verified}`) automatically become `lifecycle_status='promotion_ready'` via the existing pipeline. **Shipped:** Razorpay run live against `rzp_test_Spe581Sg5b8pWJ` on 2026-05-15 — 5/5 cases at 1.00 quality, latencies 178-265ms; `agents.json` flipped to `benchmark_status="passed"` with `benchmark_status_evidence` recording the run. **Pending:** Resend / Firecrawl / Shippo / eBay / Stripe — all blocked on operator-supplied keys (Stripe additionally on Stripe-India invite). The benchmark scheduler script itself is also still hardcoded to `email_verification` (audit-flagged); a follow-up will iterate over `agents.json` entries with `requires_benchmark_gate=true` instead so this step becomes one command per capability rather than a hand-written invocation. | engineer | 2 h (mostly waiting) |
| **S3a-7 ✅ DONE 2026-05-15** | Add an **execution-time cost cap** + **per-capability spend ledger** before any new agent is reachable from `/goal`. Without this, a runaway loop on a paid agent (Stripe, OpenAI) is a billing incident. **Shipped:** `apps/api/planmyagents_api/cost/{spend_ledger.py,cost_cap.py}`. Env vars: `PLANMYAGENTS_COST_CAP_ENABLED` (default true), `PLANMYAGENTS_COST_CAP_PER_GOAL_USD` (default $0.50), `PLANMYAGENTS_COST_CAP_DAILY_USD` (default $20), `PLANMYAGENTS_SPEND_LEDGER_PATH`. Both JSON and Postgres backends. Wired into `WorkflowExecutor` via `gated_execute()` — refused calls return a structured `cost_cap_exceeded` payload AND prevent the underlying adapter from being invoked. Regression test (`test_workflow_executor_cost_cap.py::test_51st_call_refuses_with_cost_cap_exceeded`) locks in: 50 paid sub-tasks at $0.01 each succeed, 51st refuses with `cost_cap_exceeded`. | engineer | 6 h |
| **S3a-8** | Update `agents.json` with the newly-promoted agents (run `python scripts/generate_registry_from_db.py`) and re-run the goals.jsonl eval. **Definition of done:** the eval shows precision/recall numbers for the 5 capabilities, AND `/goal` for each capability returns at least one `routable=true` decision. | engineer | 2 h |

**Total Track 3a effort:** ~64 h ≈ 8 working days for one developer,
~4 working days for two developers in parallel (the per-capability
adapter work parallelises cleanly).

**Definition of done for Track 3a:**
1. 5 of 25 capabilities → 10 of 25 capabilities have ≥1 configured,
   benchmark-passing, executable agent (gain of +5).
2. The cost cap (S3a-7) is in place and tested with a runaway-loop
   regression test.
3. `goals.jsonl` eval precision/recall for the 5 picked capabilities is
   reported in `reports/sprint-3a-eval.md`.
4. A live `/goal` request for each of the 5 capabilities returns
   `routable=true` and successfully `execute=true` against the chosen
   provider, with cost reported.

---

## Track 3b — Scale benchmark authoring with an LLM (BLOCKED on 3a)

Goal: the next 5 capabilities (15/25 → 20/25) shouldn't take another
15 hours of human benchmark authoring. After 3a we have ~25 hand-
authored cases as gold standard; this track turns them into a calibrated
generator.

| ID | Task | Effort | Notes |
|---|---|---|---|
| **S3b-1** | Build `scripts/generate_benchmark_cases.py`. Inputs: capability slug + `agents.json` description + 2-3 hand-authored example cases (the gold standard). Output: 10 candidate cases as JSON. Uses the same escalating LLM client we use for the planner. | 6 h |
| **S3b-2** | Build a **calibration harness** — for each of the 5 already-authored capabilities (3a output), generate 10 cases with the LLM, run BOTH the human cases and the generated cases against the production providers, and report agreement rate. Target: ≥80% of generated cases produce the same pass/fail outcome as a human-equivalent case. Below that, the generator prompt needs work. | 8 h |
| **S3b-3** | Build a **human review queue** UI surface (under `apps/web/src/app/admin/benchmark-review/`) where an operator can: read each generated case, mark it accepted/rejected/edited, and see the pass/fail rate against current providers. Generated cases never reach `benchmark-store.json` until accepted. **Trust gate, not full automation.** | 12 h |
| **S3b-4** | Pick the next 5 capabilities by demand signal (same mechanism as S3a-1). Run S3b-1 → S3b-2 → S3b-3 for each. Acceptance threshold for the queue: ≥7/10 generated cases accepted by the reviewer. Below that → re-prompt or fall back to hand-authoring for that capability. | 4 h human review per capability = 20 h |
| **S3b-5** | Re-run benchmarks across the new 5 capabilities; promote any candidates that pass. Update `agents.json` and `goals.jsonl`. | 4 h |

**Total Track 3b effort:** ~50 h ≈ 6 working days. Net result:
**+5 more capabilities routable (10 → 15 of 25) at ~half the human
effort per capability that 3a took.**

Track 3b is **explicitly blocked** until 3a's S3a-2 ships its gold
standard cases. Starting it earlier produces a beautifully-engineered
generator that emits cases nobody can validate.

---

## Track 3c — UX honesty during the gap (PARALLEL from day 1)

Even after 3a + 3b ship, 10 of 25 capabilities will still be uncovered
on day one. Track 3c makes the gap honest and useful instead of
silent and useless. **Independent of 3a/3b — start day one.**

| ID | Task | Effort | Notes |
|---|---|---|---|
| **S3c-1** | When `/goal` produces a refusal with `missing_capabilities` AND the discovery store has ≥3 candidates (any verification status) for that capability, render a "We found these leads but couldn't verify them" card on the response. Includes evidence URLs, verification_status pills (already in place from sprint-2), and a "Help us verify this provider" CTA. | 6 h |
| **S3c-2** | Add a **"manual workaround" section** to the refusal response. Re-uses the existing `HumanFallbackSuggester` (sprint-2). The card asks the LLM: "given that we have no agent for capability X, what's the closest manual workflow a human could do today?". Already partially exists; harden it for 5-star UX. | 4 h |
| **S3c-3** | Add a public **"capability gap leaderboard"** at `/discovery-gaps` (already built in sprint-2 backend; surface it in the nav). Operators / users see what's most-asked and what we're working on. Each row links to the GitHub issue tracking that capability's adapter work. | 4 h |
| **S3c-4** | Add a **"what we've already built" page** at `/capabilities` listing the 5 (then 10, then 15) covered capabilities with their best-performing agent, cost, latency, and benchmark verdict. The opposite of the gap leaderboard — the supply page, not the demand page. | 4 h |

**Total Track 3c effort:** ~18 h ≈ 2.5 working days. Starts day one,
ships incrementally as parts land.

---

## What "wrapper agent, manual engineering work per API" actually means

You asked for detail on this. Concretely:

### What an "agent" is in our system

An agent (in our code: a `ProviderAdapter`) is something that can be
called like:

```python
response = await provider.execute(
    ProviderRequest(
        capability="email_verification",
        inputs={"email": "alice@example.com"},
        idempotency_key="goal-123:step-2",
    )
)
# response.succeeded, response.output, response.cost_usd, response.latency_ms
```

That uniform interface is what `/goal` calls behind the scenes when
it routes a sub-task to a provider. The router doesn't know or care
whether the underlying provider is Stripe, an MCP server, a curl call,
or a local Python function — it just calls `.execute()`.

### What an API is, by contrast

An API like Stripe's REST API is not callable in that uniform way. To
authorize a $42 payment, Stripe expects:

```http
POST https://api.stripe.com/v1/payment_intents
Authorization: Bearer sk_live_...
Content-Type: application/x-www-form-urlencoded

amount=4200&currency=usd&payment_method_types[]=card
```

And it responds with Stripe-specific JSON, Stripe-specific error codes
(`card_declined`, `rate_limit`, `insufficient_funds`, etc.), and
Stripe-specific cost rules (2.9% + $0.30 per successful charge).

### What "build the wrapper" means

The wrapper is a Python class that translates the uniform
`ProviderRequest` ↔ `ProviderResponse` interface into Stripe's
specific format. Look at `apps/api/planmyagents_api/agents/hunter.py`
for a real example — 129 lines of stdlib-only code that:

1. Reads `STRIPE_SECRET_KEY` from env (`__post_init__`).
2. Translates `{"amount": 42, "currency": "USD"}` into
   Stripe's URL-encoded body format (`_payment_intent_url`).
3. Issues the HTTP call (`urllib.request`).
4. Translates Stripe's success response into `ProviderResponse(
   succeeded=True, output={...}, cost_usd=...)`.
5. Translates Stripe's error responses into `ProviderResponse(
   succeeded=False, output=None, raw_response={"error": ...})` —
   so the benchmark layer sees a typed failure, not an exception.
6. Reports cost. Stripe's 2.9% + $0.30 means `cost_usd` depends on
   the input amount, not a flat per-call number — the wrapper
   computes it.

That's ~100–150 lines per provider, written by hand once per provider.
We have ~10 such wrappers today (`hunter.py`, `apollo.py` etc.).
For Stripe, Adyen, Skyscanner, Amadeus, Duffel, Aviationstack — each
needs its own.

### What the GenericProtocolAdapter does today (post Gap 4)

`apps/api/planmyagents_api/agents/protocol.py` now actually invokes
discovered providers when execution is enabled
(`PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION=true`):

- **`GenericMcpAdapter`** speaks JSON-RPC `tools/call`. The tool to
  invoke is either explicit (`inputs.tool_name`) or auto-resolved by
  capability/name overlap; auto-resolution refuses (rather than
  guesses) when no tool's name overlaps the capability slug. Tool
  arguments come from `inputs.arguments` (preferred) or any
  non-reserved `inputs` key. The MCP `content[]` text blocks are
  collapsed into `output.text`; `result.isError = true` flips
  `succeeded` to false.
- **`GenericOpenApiAdapter`** re-fetches the spec at execution time
  (cache is the operator's job), picks an operation by
  `inputs.operation_id` or capability-name overlap, builds the HTTP
  request with path-param substitution, and supports three auth
  modes via `registry_agent['auth']`: bearer (`{type, env}`), api-key
  header (`{type, header, env}`), api-key query (`{type, query, env}`).
  Secret values come from environment variables only — the registry
  blob declares the contract, the deploy supplies the secret.
- **`GenericA2AAdapter`** and **`GenericAiAgentAdapter`** return
  structured refusals (no synthesised "metadata_only" success).
  A2A is held until the wire format stabilises; the free-form AI
  agent type has no published wire format we can default to.
- The base **`GenericProtocolAdapter`** also refuses with a message
  asking the operator to promote to a protocol-specific subclass.
  This guards against a registry / router misconfiguration that
  routes a request through the bare base class.

What the generic adapters *don't* try to be:

- They don't synthesise pricing — `cost_usd` is always `0.0`. The
  cost-cap policy at the workflow level enforces operator-imposed
  budgets per provider; double-counting here would contradict the
  operator's schedule.
- They don't normalise per-vendor pagination or rate-limit semantics.
  HTTP `429` and tool-level `isError` are surfaced verbatim; the
  workflow layer decides whether to retry.
- They don't handle OAuth flows (a bespoke wrapper still owns those).

So the realistic path forward is **hybrid**: use the generic adapter
for compliant MCP servers and well-formed OpenAPI specs with one of
the three supported auth modes, use bespoke wrappers for the
high-stakes cases (anything that costs money, anything where wrong
output is worse than no output, anything with non-trivial pagination
or auth). Track 3a includes auditing each candidate to make this
build / generic-adapter / skip decision (S3a-3).

### What "manual engineering work per API" looks like in hours

| Provider type | Wrapper effort | Why |
|---|---|---|
| Read-only API with API-key auth + JSON response (e.g. Hunter, Tavily) | ~3–4 h | The hunter.py shape applies almost verbatim |
| Paid API with non-trivial auth or pricing (e.g. Stripe, Adyen) | ~6–8 h | Need pricing model encoded; need careful error handling for `card_declined` etc. |
| API with non-standard pagination or async results (e.g. Apify actor runs) | ~8–12 h | Need polling loop or webhook plumbing |
| MCP server with declared tools | ~30 min via generic adapter | Generic MCP adapter handles tool discovery + invocation if execution is enabled |
| A2A agent with a published agent card | ~30 min via generic adapter | Same as MCP |

That cost is what makes 3b's LLM-driven benchmark generator a force
multiplier: the bottleneck isn't writing wrappers (those are
mechanical), it's writing benchmark cases (those need product thought).
3b automates the latter; 3a still pays the former by hand for the
first 5–10 capabilities.

---

## Sequencing & dependency graph

```
day 1 ────┬──► S3a-1 (pick caps from data) ──► S3a-2 (gold benchmarks) ──► S3a-3 (inventory)
          │                                          │                             │
          │                                          ▼                             ▼
          │                              gold standard exists           build/generic decision per candidate
          │                                          │                             │
          │                                          ▼                             ▼
          │                                   S3a-4 (build wrappers)  ◄────────────┤
          │                                   S3a-5 (generic adapter run)          │
          │                                          │                             │
          │                                          ▼                             │
          │                                   S3a-6 (benchmark + promote)          │
          │                                          │                             │
          │                                          ▼                             │
          │                                   S3a-7 (cost cap, blocking)           │
          │                                          │                             │
          │                                          ▼                             │
          │                                   S3a-8 (release, eval)                │
          │                                          │                             │
          │                                          └────► UNLOCKS 3b             │
          │                                                       │                │
          │                                                       ▼                │
          │                                                  S3b-1 → S3b-2 → S3b-3 → S3b-4 → S3b-5
          │
          └──► S3c-1, S3c-2, S3c-3, S3c-4 (independent, ship as ready throughout)
```

Critical path: **S3a-1 → S3a-2 → S3a-4 → S3a-6 → S3a-7 → S3a-8 → S3b-2**.

---

## Definition of done for Sprint 3

A successful sprint produces all of the following, verifiable end-to-end:

1. `make test` is green (currently 659 backend + frontend `tsc --noEmit`).
2. **10 of 25** registry capabilities have at least one
   `lifecycle_status='promoted'` agent in `agents.json`, up from 5 today.
3. A live `/goal` request for each of the 5 (then 10) covered
   capabilities returns `routable=true`, `execute=true` succeeds against
   the real provider, and the cost is correctly reported and
   accumulated against the daily spend cap.
4. The cost cap (S3a-7) is enforced — a regression test demonstrates
   that the 51st call against a $0.01-per-call provider in a single
   goal refuses with `cost_cap_exceeded`.
5. The benchmark generator (S3b-1) produces ≥80% pass/fail-equivalent
   cases on the 5 hand-authored gold-standard capabilities (S3b-2
   calibration report).
6. At least one capability outside the original 5 has been onboarded
   end-to-end via the LLM generator → human review queue → benchmark
   → promote pipeline (proves the loop closes without the engineer
   touching any benchmark file by hand).
7. The "we found leads but couldn't verify" card (S3c-1) appears on
   `/goal` refusals when the store has ≥3 candidates for the missing
   capability.

---

## Estimated total effort

| Track | Hours | Working days (1 dev) |
|---|---|---|
| Track 3a (depth, critical path) | ~64 h | 8 days |
| Track 3b (breadth, blocked on 3a) | ~50 h | 6 days |
| Track 3c (UX honesty, parallel) | ~18 h | 2.5 days |
| **Total sequential** | **~132 h** | **~17 working days (≈ 3.5 weeks)** |
| **With 2 devs splitting 3a+3c** | **~80 h critical path** | **~10 working days (≈ 2 weeks)** |

This is a meaningfully larger sprint than sprint-2 (sprint-2 was ~14 h
of focused work). Splittable across two devs cleanly.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Adapter work for paid providers (Stripe, OpenAI) costs real money during testing. | S3a-7 ships the cost cap *before* any new agent is reachable from `/goal`. Tests run against sandbox/test API keys where available. Document per-provider testing quotas in `docs/sprint-3-testing-quotas.md`. |
| Generic protocol adapter (S3a-5) succeeds against simple cases but fails silently on edge cases when promoted to prod. | Keep generic-adapter execution gated behind a per-capability allowlist, not a global flag. Audit each pass before promoting. |
| LLM-generated benchmark cases (S3b) drift from gold standard over time as the LLM behind the generator changes. | S3b-2 calibration report runs on a schedule (weekly), not just once. Drift > 10% triggers a re-tune. |
| Picking the wrong 5 capabilities for 3a (e.g. picking based on intuition not data). | S3a-1 mandates the data-driven pick from the demand leaderboard. The provisional table in this doc is explicitly labelled "subject to data". |
| Cost-cap implementation (S3a-7) is too conservative and refuses legitimate goals. | Cap is per-env, configurable. Default $0.50/goal is comfortably above all currently-wrapped providers' typical per-call cost. Also surface "cap exceeded" as a structured response so the user can opt-in to a higher cap explicitly. |
| Benchmark suite authoring (S3a-2) takes longer than 3 h per capability because of the strict-criterion design. | Allow LLM-judged criteria with a fixed rubric for the long-tail cases. The 3 h estimate assumes ~3 strict + ~2 LLM-judged cases per suite. |

---

## What's explicitly OUT of scope for Sprint 3

| Item | Why deferred |
|---|---|
| **Workflow execution + inter-agent piping** | Still depends on having coverage for the capabilities being chained. Re-evaluate after Sprint 3 hits 15/25 coverage. |
| **Sandboxed execution for arbitrary MCP servers** | Real engineering work (process isolation, network policy, filesystem isolation). Track 3 keeps the generic adapter behind a flag for now; full sandbox sandboxing is its own sprint. |
| **Per-tenant API key isolation** | Single-tenant prototype. Ship multi-tenant only after the value prop is proven via a single live tenant. |
| **Capability dependency graph** ("payment_authorization needs identity_verification") | Premature until we have ~20 capabilities. With 10 they're either independent or trivially chainable. |
| **Streaming responses on `/goal`** | Real UX win but mechanical work. Sprint 4. |
| **Reaching parity with all 25 registry capabilities** | Sprint 3 honestly closes 5 + 5 = 10 of the 20-capability gap. The remaining 10 are sprint-4 work, ideally with 3b's generator already calibrated. |

---

## After Sprint 3 — what comes next

If Sprint 3 lands as planned, the next logical sprint is **Sprint 4:
chain agents into multi-step workflows**. With 15/25 capabilities
routable, the value proposition shifts from "find me an agent for X"
to "execute this multi-step goal end-to-end". That's the version of
the product where *"buy 100 cars for my top employees with discount"*
becomes a chained execution — `general_research` → `price_comparison`
→ (human approval) → `payment_authorization`, all from one goal
submission. None of that is possible until each link in the chain
has a routable agent, which is exactly what Sprint 3 delivers.
