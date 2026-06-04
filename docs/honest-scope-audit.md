# PlanMyAgents — Honest Scope Audit (2026-06-03)

> Author: engineering audit pass. Purpose: reconcile what the pitch/README
> claim with what the code actually does, grounded in file/line evidence, so
> fundraising and roadmap decisions are made on reality, not on the deck.
>
> This document is deliberately blunt. It is the internal counterpart to the
> external pitch. Where the two disagree, this document is the source of truth
> per `AGENTS.md` Hard Rule #1 ("no surface may claim more than the index data
> warrants").

---

## TL;DR

The engineering is genuinely good: disciplined separation of index vs.
surfaces, an honest-refusal architecture, a real self-grading credibility
classifier, a well-enforced "benchmark baselines are never user-routable"
firewall, ~1,231 tests, clean Ruff/typed-routes hygiene.

**But the single most important claim in the pitch — that PlanMyAgents
dynamically evaluates / benchmarks publicly-discovered agents — is not
implemented.** There is no code path that takes a discovered MCP/A2A/AI
agent and produces a real quality score for it. The only real benchmarks
that exist are hand-coded wrappers around commodity *APIs* (Razorpay,
Stripe, Resend, Firecrawl, Shippo, eBay, Hunter) — and those are
architecturally *firewalled out* of the routing/recommendation path by
design. So:

> **The things we can benchmark, we refuse to recommend.
> The things we recommend, we cannot benchmark.**

That is the gap the founder correctly suspected. Everything else in this
audit is secondary to it.

---

## 1. The dynamic-eval gap (the core finding)

### 1.1 What the pitch implies

- PITCH_DECK.md / README: "benchmark gates", "trust + benchmark evidence
  model", "3 routable benchmark cells", "discover, gate, and evaluate
  providers in live workflows", "Tested by PlanMyAgents".
- The whole "trust layer" thesis rests on: *we run discovered agents and
  publish honest performance numbers for them.*

### 1.2 What the code actually does

There are three adapter families. None of them closes the loop.

1. **Hand-coded benchmark baselines** —
   `apps/api/planmyagents_api/benchmark/baselines/{razorpay,stripe,resend,firecrawl,shippo,ebay,hunter}.py`.
   These are real, stdlib HTTP wrappers around vendor REST APIs. They are the
   *only* adapters that can produce real quality/latency/cost numbers.
   - They are all `provider_type: api_provider | payment_provider`.
   - They are marked `is_benchmark_baseline: true` in
     `packages/registry/agents.json` and are **explicitly blocked from
     user-facing routing** by `ProviderRouter` (`agents/router.py`,
     `_is_benchmark_baseline`). Per `baselines/__init__.py`: *"They MUST NOT
     be imported from any user-facing routing path."*

2. **Generic protocol adapters** —
   `apps/api/planmyagents_api/agents/protocol.py`. These *can* invoke
   discovered agentic providers, but:
   - MCP / OpenAPI execution is gated **OFF by default**
     (`PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION`, default off).
   - **A2A and ai_agent return structured refusals — not implemented**
     (`GenericA2AAdapter.execute`, `GenericAiAgentAdapter.execute`).
   - The benchmark scheduler **explicitly skips** generic protocol adapters:
     `scheduler.py._resolve_adapter` returns `source="synthetic"` and no
     adapter when `adapter_module` starts with
     `planmyagents_api.agents.protocol:` ("generic protocol_beta adapter
     cannot produce useful benchmark scores"). So discovered MCP servers are
     never scored even when execution is enabled.

3. **Mock adapters** — `agents/mock.py`, wired via
   `scheduler.MOCK_ADAPTERS_BY_CAPABILITY = {email_verification,
   contact_enrichment}`. These produce `source="synthetic"` rankings.

**Net:** the scheduler that feeds the leaderboard can only ever produce
(a) synthetic/mock rankings, or (b) rankings for hand-coded API wrappers that
the router refuses to recommend. There is no path
"discovered agent → benchmark → published score."

### 1.3 Why this is structural, not a missing config

Even with every key and a live Postgres:

- The benchmark **scoring** (`benchmark/scoring.py`) compares a provider
  response against **hand-authored YAML expected values**
  (`packages/benchmarks/<capability>/*.yaml`). There are 7 case files total.
  To benchmark an arbitrary discovered agent for an arbitrary capability you
  need ground-truth cases for that capability — which only exist for the 5–6
  capabilities someone hand-wrote. There is no mechanism to synthesize
  ground-truth for a newly discovered agent's capability. So "evaluate any
  discovered agent" cannot work for the long tail by construction.
- "Verification" (`capability_verified`) is an **MCP `tools/list` probe** —
  it proves a server *exists and advertises a tool*, not that the tool
  *works well*. The pitch conflates "verified" (probe) with "benchmarked"
  (quality). They are different things; only the cheap one is automated.

### 1.4 The smoking gun in the data

The latest credibility report
(`reports/benchmark-credibility/2026-05-30.json`) contains exactly two
capabilities — `contact_enrichment` and `email_verification` — both
`synthetic_only`, `0 publishable`. Those are **exactly** the two keys in
`MOCK_ADAPTERS_BY_CAPABILITY`.

Meaning: the only data that ever flowed through the scheduler into the
persisted rankings store (which the leaderboard and the credibility report
both read) came from **mock adapters**. The "3 routable cells"
(Razorpay live + Resend/Firecrawl fixture) advertised in the pitch were
**manual one-off `run_sync` invocations** (see the reproduce-command in
`agents.json` `benchmark_status_evidence`) that were **never persisted into
the ranking store the surfaces read**. The honest leaderboard, as actually
generated, shows two synthetic capabilities — not three routable cells.

This is a direct violation of `AGENTS.md` Hard Rule #1.

---

## 2. Discovery subsystem — real vs. claimed

### 2.1 The "17 scouts" claim

Literally true as a count: `discovery/scouts.py default_scouts()` wires 17
`Scout(...)` entries. The honest breakdown:

| Class | Count | Notes |
|---|---|---|
| Curated static/file loaders | 5 | `curated_static` contributes **0** agentic rows (all 4 entries are api/payment type → routed to `apis_without_agents`). |
| Real first-party/canonical remote crawlers | 12 | official MCP registry, apis.guru, HN, vendor RSS, github code/recent/awesome, mcp_marketplace, smithery, moltbook, glama, npm |
| Third-party web-search wrappers (Brave/Tavily/Exa) | 0 wired | `LiveWebSearchSource` etc. exist in `sources/live.py` as **dead scaffolding** — not in the fleet. Only `GitHubCodeSearchSource` is wired (first-party GitHub API). |

### 2.2 What a default deployment actually runs

- **5 scouts are token-gated** and silently skip with no key:
  `github_code_search`, `github_recently_pushed`, `github_awesome_lists`
  (`GITHUB_TOKEN`), `smithery` (`SMITHERY_API_KEY`), `moltbook`
  (`MOLTBOOK_API_KEY`). A keyless deploy runs 12 of 17.
- **7 remote scouts have no `fallback` capability** and depend on the
  embedder clearing the 0.55 threshold to emit anything. With the **default
  `DeterministicHashEmbedder`** (used whenever `PLANMYAGENTS_EMBEDDING_MODEL`
  is unset), capability inference rarely clears that threshold, and the
  normalizer **drops any candidate with zero capabilities**
  (`normalizer.py`). So those scouts emit ≈0 in a default deploy.
- Realistic keyless + no-Ollama picture: ~5 curated loaders + ~3 remote
  scouts actually producing candidates. The "~30,000 servers indexed"
  surface area requires GitHub/Smithery/Moltbook keys **and** a real
  embedder (Ollama nomic / OpenAI) configured.

### 2.3 Embeddings are not used at query time by default

- In-memory `DiscoveryIndex.search()` uses **no vectors** — pure
  capability-set overlap + substring text match + provider-type priority
  (`discovery/index.py`).
- pgvector cosine search is reached only when the store is Postgres **and**
  the backend unwraps to `PostgresDiscoveryStore` **and** the embedder
  returns a non-empty vector **and** ≥1 row comes back. `discovery_store_for_path`
  defaults to **JSON** for non-`.db` URLs, so dev/default never hits pgvector.
- Even on Postgres, the default embedder is the **hash** embedder, so
  "semantic search" is hash-bag-of-words similarity unless an operator sets
  `PLANMYAGENTS_EMBEDDING_PROVIDER=openai` or `_EMBEDDING_MODEL` (Ollama).
- Note: `capability_index.py` docstring claims nomic is "the default" — the
  actual code default with no env set is the hash embedder. Doc/code drift.

### 2.4 The relevance pipeline is LLM-hard-dependent

The aggregator scouts lean on `CandidateJudge` (`discovery/candidate_judge.py`)
as the load-bearing relevance filter, and it has **no substring fallback** —
if both LLM tiers (local Qwen, Groq) are down it raises
`NoLlmTierAvailableError` and the request refuses. Discovery quality claims
implicitly assume a working LLM at request time.

---

## 3. What is implemented well (credit where due)

- **Index vs. surface discipline.** The 4-table physical split
  (`discovery_candidates` vs `apis_without_agents` vs demand events vs run
  log), enforced by a Postgres CHECK and the `RoutingDiscoveryStore` facade,
  is clean and consistently applied.
- **The credibility classifier itself** (`benchmark/credibility.py`) is the
  best asset in the repo: a conservative, deterministic, CI-runnable
  self-grader that refuses to call a leaderboard publishable until ≥3 real
  providers × ≥30 samples × ≤30-day freshness. It is honest by construction.
  The irony is that the pitch ignores its verdict.
- **The routing firewall** (`agents/router.py`): benchmark baselines,
  dev/test/mock providers, and un-gated providers are all correctly excluded
  from production routing. The invariants are real and tested.
- **Honest-refusal path** and the `gap_only` recipe coverage framing
  (`docs/manual-test-log.md`) — the system genuinely refuses rather than
  fabricating, and the manual test log is itself an honest document that
  already flagged the deck overclaim.
- **Dedupe/merge** with a trust-tier priority ladder
  (`discovery/dedupe.py`) is thoughtful and handles the verification-status
  collision case correctly.
- **Scoring engine** for the cases that do exist: field-level weighted
  scoring plus a real hallucination check on `must_indicate_unknown`
  (`benchmark/scoring.py`) is reasonable.
- **Test/lint hygiene:** ~1,231 stdlib-unittest tests, Ruff, typed routes.

---

## 4. Bugs / weaknesses / drift (ranked)

1. **[Critical] No dynamic eval of discovered agents.** §1. The product's
   central differentiator is unbuilt.
2. **[Critical] Pitch "3 routable cells" contradicts the credibility report
   (0 publishable, 2 synthetic).** §1.4. Honesty bug per Hard Rule #1.
3. **[High] BYO-creds sandbox runner is a stub.**
   `agents/sandbox_runner.py` raises `SandboxNotYetWiredError`. The entire
   Phase-2 "optional execution fee (tier 8)" revenue line and the "collect
   benchmark data on real workflows" story depend on a path that does not
   exist yet.
4. **[High] Default deployment is far thinner than "17 scouts / 30k
   servers."** §2.2. Without keys + a real embedder, most scouts emit ~0.
5. **[Medium] Embeddings advertised as semantic search are hash-similarity
   by default and bypassed entirely on non-Postgres stores.** §2.3.
6. **[Medium] Doc/code drift:** nomic-as-default claim vs hash default;
   README "live evidence — every count above the fold is a Postgres row"
   vs the fact that no data store exists on disk and the rankings store only
   ever held mock data.
7. **[Low] LLM hard-dependency** at request time with no degraded mode means
   a planner/judge outage = blanket refusals. Acceptable as a design choice,
   but should be stated.

---

## 5. Repositioning recommendation

The current pitch sells a **"trust + benchmark layer"** whose benchmark
engine for discovered agents does not exist. Two honest paths:

### Option A — Reposition to what is real today: a *discovery + honest-refusal
index* (recommended for the next 1–2 quarters)

- Lead with the **Agent Discovery Index** + **honest refusal** + **demand
  signal** (`/open-mcp-opportunities`, `/discovery-gaps`). These are real and
  defensible.
- Demote "benchmarked routing" and "Tested by PlanMyAgents" to a **labelled
  roadmap item**, not a current capability. Keep the credibility classifier
  front-and-center as the *mechanism* that will gate it — it's a great story
  *as a commitment to honesty*, not as a claim of present coverage.
- Drop or asterisk "3 routable cells" until ≥3 real cells are actually
  persisted in the ranking store and pass the classifier as `developing`+.

### Option B — Build the missing wedge before pitching it (the higher bar)

To make the benchmark claim true for *discovered* agents you need, at minimum:
1. Wire the generic MCP adapter into the scheduler (stop skipping
   `protocol_beta`) behind the sandbox, so discovered MCP servers can be
   executed against cases.
2. A way to obtain ground-truth per capability without hand-authoring every
   case (LLM-as-judge for soft outputs is already listed as "pending" in
   sprint.md Phase 2 — this is the unlock).
3. Persist those runs into the ranking store the surfaces read, and let the
   credibility classifier gate them.
4. Then, and only then, claim "benchmarked routing."

My recommendation: **ship Option A now, fund Option B as the thing the seed
actually buys.** That is a fundable, honest story ("we own the discovery
index and the honest-refusal UX today; the benchmark trust layer is the
roadmap, and here is the classifier that keeps us honest while we build it").
It directly matches `competitor-redteam.md` §11's kill-criterion: prove
provider variance on a handful of *real* cells first.

### Docs that must move together (per `AGENTS.md`)

If repositioning is approved, update **all** of:
- `sprint.md` — milestone-level reframe (benchmark-of-discovered-agents moves
  from implied-done to explicit Phase-2 unlock).
- `BUSINESS_PLAN.md` §6/§8 — the benchmark API and execution-fee lines depend
  on capabilities that are roadmap, not current; mark them as such.
- `PITCH_DECK.md` — "current state" + "what we are / are not" tables; the
  "3 routable cells" and "Tested by PlanMyAgents" claims.
- `README.md` — the "live evidence — every count is a Postgres row" framing
  and the Status table rows that say ✅ for benchmark coverage.

---

## 6. Suggested immediate, non-destructive fixes

1. Reconcile the "3 routable cells" claim with the credibility report
   everywhere it appears (README status table, PITCH_DECK "current state").
2. Add a one-line honesty note to the leaderboard surface copy: counts are
   mock/synthetic until the classifier reports `developing`+ for a capability.
3. Fix the nomic-vs-hash default-embedder doc drift in
   `discovery/capability_index.py` and `discovery/embeddings.py`.
4. State the keyed-vs-keyless scout reality in `docs/agent-discovery-index.md`
   so "17 scouts" is never read as "17 scouts produce data in every deploy."

---

## Addendum (2026-06-04): Agent-card ingestion + A2A quality attestation status

Two related capabilities landed/were-scoped after the eval framework:

**Shipped — agent-card ingestion (`discovery/card_ingestion.py`).** A
user/vendor can submit an Agent Card URL for *any* domain (e.g.
`https://policycheck.tools/.well-known/agent.json`); the platform resolves it,
runs existence + claim verification (reusing `verify_candidate`), and indexes
the agent at an honest trust tier. This is a registration/resolution path, NOT
a discovery crawler — finding unknown agents on unknown domains remains
unsolved and out of scope. The ingested agent is capped at claim-level trust:
`benchmark_status` stays `not_started` and it is non-routable. `make card-ingest
CARD_URL=...` / `scripts/discovery/run_card_ingestion.py`.

**Blocked-on-invocation — A2A quality attestation.** We can verify an A2A
agent *exists and advertises* a capability (the card is the evidence). We
CANNOT yet attest that it performs the capability *well*, for two reasons:
(1) A2A skill invocation is not implemented (the wire format is still in flux —
`agents/protocol.py::GenericA2AAdapter` returns a structured refusal, and the
eval `ProtocolInvokerRegistry` marks A2A `refusal_only`); (2) open-ended
capabilities like "analyse return policies" need rubric ground truth.
`CardIngestionService.attest_quality(...)` returns a structured
`blocked_on_invocation` result whose `source="refused"` is in the credibility
classifier's non-real deny-list, so an ingested A2A agent can never appear as
quality-scored. When A2A invocation is wired, quality attestation routes
through the existing `EvalFramework` — not a parallel mechanism.

Honest one-liner: a self-asserted card claim is verified as *made*, never as
*true*. Existence/claim today; quality when A2A invocation ships.
