# PlanMyAgents Implementation Plan

> Goal: turn the revised business thesis into an execution-grade build plan with phases, gates, acceptance criteria, and explicit non-goals.

This plan assumes PlanMyAgents is built as a **benchmark-driven routing layer for measurable workflows**, not as a universal public-agent marketplace on day one.

---

## 0. What Can Be Implemented Now

The following parts are implementable immediately:

| Component | Build now? | Why |
|---|---:|---|
| Agent Discovery Index | Yes | Core product asset; required before scalable routing |
| Provider registry | Yes | Active/routable subset of the discovery index |
| Benchmark test-case format | Yes | Independent of customers; creates early asset |
| Manual benchmark runner | Yes | Proves provider variance before full product |
| Provider adapters for 3-5 tools | Later | Build only after discovery finds credible candidates for the chosen workflow |
| Task planner prompt | Yes | Start with local Qwen via Ollama, constrained by the registry, and evaluate against synthetic tasks |
| Structured output/stitcher | Yes | Core product experience |
| Cost estimator | Yes | Simple but important differentiation |
| Public benchmark page | Yes, basic | Distribution asset |
| Full payment flow | Later | Do after workflows show user pull |
| x402/AP2 support | Later | Not needed for beachhead |
| Universal agent crawler | Start narrow now | Use source connectors, not broad crawling, to index MCP/A2A/AI-agent/API candidates |
| Enterprise SSO/audit/on-prem | Much later | Scope trap |

The most important first implementation is **not the full app**. It is:

> An Agent Discovery Index + benchmark harness + small routable registry that proves PlanMyAgents can find candidate agents/providers, gate unsafe ones, and route verified ones.

---

## 1. Phase Overview

| Phase | Duration | Objective | Gate |
|---|---:|---|---|
| Phase -1 | 1-2 weeks | Customer + workflow validation | 3 design partners or stop |
| Phase 0 | 2-3 days | Repo/app scaffolding | local dev runs |
| Phase 1 | 1 week | Agent Discovery Index v0 | source connectors + normalized candidates + workflow-option ranking |
| Phase 2 | 1 week | Benchmark foundation | 50-100 test cases + discovered/routable subset tested |
| Phase 3 | 1-2 weeks | Verified provider adapters | 3-5 discovered candidates callable via common interface |
| Phase 4 | 1 week | Planner + cost estimator | task -> candidate search -> plan -> estimate/refusal |
| Phase 5 | 1-2 weeks | Execution engine | plan -> provider calls -> structured output |
| Phase 6 | 1 week | Public benchmark/discovery page | candidates + scores visible |
| Phase 7 | 1 week | Minimal UI + auth | user can submit and download results |
| Phase 8 | 2-4 weeks | Design partner pilots | 10+ real workflows executed |
| Phase 9 | ongoing | Production hardening | readiness for seed/fundraise |

Total: **8-12 weeks** to credible v0 if scope is controlled.

---

## 2. Phase -1: Validation Before Code

### Objective
Confirm that users care about provider quality, cost control, and repeatable workflow execution enough to test PlanMyAgents.

### Work
- Send 100-200 discovery emails.
- Run 10-15 discovery calls.
- Talk specifically to:
  - RevOps leads
  - sales ops managers
  - GTM engineers
  - Clay/Apollo power users
  - data-driven agencies
- Ask where Clay/Apollo/ZoomInfo workflows still fail.
- Ask for screenshots of current workflows.
- Ask whether they would test a benchmark-driven router.

### Deliverables
- Call notes for 10+ interviews.
- List of repeated pain phrases.
- At least 3 design partners.
- One chosen workflow for v0.

### Acceptance Criteria
Proceed only if:

- 3+ people agree to test a prototype.
- 2+ people mention the same provider-quality/cost/transparency pain.
- At least one person says they would pay for a concierge/manual run.

### Kill Criteria
Stop or change beachhead if:

- Clay users are satisfied.
- Buyers only care about UI convenience, not provider quality.
- Nobody repeats the workflow weekly/monthly.
- Nobody will share example data or workflow screenshots.

---

## 3. Phase 0: Repo and App Scaffold

### Objective
Create the codebase structure needed for fast iteration.

### Work
Create:

```text
apps/
  api/                 FastAPI backend
  web/                 Next.js frontend
packages/
  registry/            provider registry
  benchmarks/          YAML test cases
scripts/
  seed_agents.py
  run_benchmarks.py
```

### Backend Setup
- Python 3.12
- FastAPI
- Pydantic v2
- SQLAlchemy or SQLModel
- Alembic migrations
- Postgres locally via Docker
- Ruff + pytest

### Frontend Setup
- Next.js
- Tailwind
- shadcn/ui
- basic landing page
- placeholder dashboard

### Acceptance Criteria
- `make dev` starts backend and frontend.
- `/healthz` endpoint returns `ok`.
- `agents.json` validates against schema.
- CI validates JSON and runs tests.

---

## 4. Phase 1: Agent Discovery Index v0

### Objective
Build the first real product asset: a searchable, normalized index of candidate
agents/providers for user subtasks.

This phase replaces the earlier instinct to add more one-off API adapters. The
router cannot scale to lakhs of agents if the system cannot first discover,
normalize, dedupe, and search candidates.

### Sources
Start with source connectors in this order:

1. curated seed catalog
2. MCP server registries/catalogs
3. A2A Agent Cards and agent directories
4. AI-native service directories
5. GitHub repositories exposing MCP/A2A/tool servers
6. OpenAPI catalogs and vendor docs
7. plain API providers as fallback

### Work
- Add `apps/api/planmyagents_api/discovery/` package.
- Define `DiscoverySource` protocol.
- Implement static/curated source connector first.
- Add MCP catalog connector.
- Add A2A Agent Card connector.
- Add web/doc search connector.
- Normalize candidates into a common schema.
- Generate dedupe keys.
- Add synonym and typo-tolerant query expansion.
- Store candidates in local SQLite/Postgres or JSON for v0.
- Add query API: search by capability + task description.
- Add daily upkeep script: `scripts/run_discovery_upkeep.py`.
- Keep ad hoc demo discovery non-persistent so unsupported prompts do not grow
  the canonical registry.
- Rank end-to-end workflow options and show rejected fallback candidates.
- Build planner fallback capability inference from discovered candidate metadata
  rather than hardcoding one branch per domain.

### Deliverables
- discovery source connectors
- candidate normalizer
- dedupe logic
- searchable local discovery index
- docs for promotion lifecycle
- tests for normalization, dedupe, and search

### Acceptance Criteria
- Can ingest at least 50 candidate agents/providers from multiple source types.
- Can search candidates for a missing capability.
- Can distinguish `mcp_server`, `a2a_agent`, `ai_agent`, and API fallback.
- Every candidate defaults to `will_fail: true`.
- No discovered candidate is routable until promoted.
- `agents.json` does not grow for every demo query.
- Qualified workflow options can be restricted to MCP servers, A2A agents, and
  AI agents while APIs remain visible as rejected fallbacks.

### Current Prototype Caveat

The current implementation has the discovery package shape plus configured
file/URL ingestion for JSON candidate lists, MCP catalogs, A2A Agent Cards,
AI-agent directories, and web-doc manifests. It also has opt-in live GitHub/URL
research sources and JSON/SQLite local discovery stores. The curated manifests
currently ingest ~70 deduped non-routable candidates across multiple source types.
Blocked runtime requests now trigger live discovery for planned or missing
capabilities and persist non-routable findings to the discovery store for the
first three-month learning window. Runtime discovery still does not crawl the
whole internet, use search-engine discovery, or query unconfigured live
registries. The demo now ranks end-to-end workflow options, restricts qualified
candidates to MCP/A2A/AI-agent provider types, reports rejected API/payment
fallbacks, and enforces booking/payment compatibility.
Hotel/lodging prompts infer `lodging_search`, `lodging_comparison`,
`booking_execution`, and `payment_authorization` through the source-derived
capability catalog; debug output includes source coverage metadata so users can
see whether live research ran.

The next implementation phase should keep `agents.json` as a promoted-provider
registry, add benchmark scheduling for promotion candidates, and move local
SQLite persistence toward the Docker/Neon Postgres + pgvector schema. The
discovery store interface now accepts Postgres URLs, so scripts and demo runtime
can write candidates to Postgres while provider routing continues to bootstrap
from `agents.json`. Promotion-ready DB candidates can now be merged into routing
or generated into a registry artifact, but only after verified evidence, adapter
metadata, passed benchmarks, and `ready_for_promotion` state are present. Generic
protocol adapters provide scale for MCP/A2A/OpenAPI/API/AI-agent candidates, but
they are `protocol_beta` and blocked from production routing by default.
Generalization eval fixtures should catch ranking/refusal regressions across task
families without encoding those task families as product branches.

---

## 5. Phase 2: Benchmark Foundation

### Objective
Build the first asset that can exist before the product: a credible benchmark suite.

### Start Smaller Than The Original Plan
Do **not** start with 250 test cases. Start with:

| Capability | Initial cases |
|---|---:|
| email verification | 20 |
| contact enrichment | 20 |
| web scraping | 20 |
| semantic search | 20 |
| company data lookup | 20 |

Total: **100 cases**.

### Work
- Create YAML test-case schema.
- Create benchmark loader.
- Create scoring functions for:
  - exact match
  - accepted aliases
  - regex match
  - null/unknown expected
  - LLM-as-judge for softer outputs
- Create CLI:

```bash
planmyagents-bench run --capability email_verification --provider hunter
planmyagents-bench report --input .planmyagents_runs/latest.json
```

### Deliverables
- `packages/benchmarks/*/*.yaml`
- `scripts/run_benchmarks.py`
- first local benchmark report in markdown

### Acceptance Criteria
- Can run 20 cases against one mock provider.
- Can store results in Postgres.
- Can compute success rate, average score, latency, and cost.

---

## 6. Phase 3: Verified Provider Adapters

### Objective
Promote a small subset of discovered candidates into real executable providers.

### Rule

Do not add adapters because a vendor is familiar. Add adapters only when the
discovery index and benchmark plan show that the provider is relevant to the
chosen workflow.

APIs such as Hunter, Apollo, Firecrawl, Tavily, and Exa are acceptable fallback
providers, but MCP/A2A/AI-agent candidates should be considered first.

### Common Interface
Each adapter implements:

```python
class ProviderAdapter(Protocol):
    provider_id: str
    capabilities: list[str]

    async def estimate_cost(self, request: ProviderRequest) -> CostEstimate: ...
    async def execute(self, request: ProviderRequest) -> ProviderResponse: ...
    async def health_check(self) -> HealthStatus: ...
```

### Work
- Implement one adapter at a time.
- Create a sandbox test for each.
- Add redaction before logging.
- Record:
  - input hash
  - provider ID
  - latency
  - cost estimate
  - status
  - normalized output

### Acceptance Criteria
- 3-5 discovered candidates are promoted into executable providers.
- Each provider can be benchmarked.
- Failures are normalized.
- No raw secrets or API keys ever logged.

Current constraint: local utilities must not be promoted as product providers.
Executable adapters should represent real configured providers or callable
agent/protocol endpoints. If none are available for a task, PlanMyAgents should
refuse honestly instead of substituting a local heuristic. Test doubles must be
marked with `runtime_mode: local`, `test`, `fixture`, or `mock`; the router blocks
them unless `PLANMYAGENTS_ALLOW_DEV_PROVIDERS=true` is explicitly set.

---

## 7. Phase 4: Planner and Cost Estimator

### Objective
Convert a plain-English workflow request into a constrained execution plan.

### Work
Implement:

- `POST /api/plans`
- input: user prompt + optional cost ceiling
- output:
  - sub-task list
  - chosen provider per sub-task
  - cost estimate
  - refusal reasons if unsupported
  - confidence

### Key Design Rule
The planner must only choose from registered capabilities. It must not invent capabilities.

### Initial LLM Choice
Use a local Qwen model through Ollama for the first implementation. This keeps
planning cheap, private, and easy to iterate while the registry and benchmark
loop are still immature. The production model can later be swapped behind the
same planner interface, but the first call in local development should be:

```bash
PLANMYAGENTS_PLANNER=local_qwen PLANMYAGENTS_QWEN_MODEL=qwen3.6:27b make api
```

(Works for both `make api` and the legacy `make demo` stdlib UI.)

The local model is not trusted blindly. Its JSON output must be validated before
execution, and any sub-task using a capability outside `agents.json` is refused.

### Unsupported-Task Discovery Loop

When a task is refused because required capabilities are not available in the
active registry, the product should still learn from it:

1. Capture the missing capabilities and a hash of the user goal, not raw PII.
2. Run provider discovery in priority order: MCP server registries, A2A Agent
   Cards, AI-agent directories, then plain API/vendor docs only as fallback.
3. Use web search and OpenAPI/vendor docs to fill metadata, but never to make a
   fresh provider immediately executable.
4. Persist matching providers to the discovery store: agentic types
   (`mcp_server` / `a2a_agent` / `ai_agent`) into `discovery_candidates`,
   plain `api_provider` / `payment_provider` into `apis_without_agents`.
   The `RoutingDiscoveryStore` facade partitions by `provider_type` at
   save time. Both carry `will_fail: true` and required env vars.
5. Include `required_env_vars` for API-key blockers.
6. Do not route to the candidate until adapter implementation, benchmarks,
   configuration, and smoke tests are complete.

This means a failed flight-booking request can enrich the registry with Duffel,
Amadeus, or Stripe payment candidates for future onboarding while still refusing
the current execution honestly. Those API providers are fallback candidates:
MCP/A2A/AI-agent providers should be preferred whenever suitable ones are found.

### Daily Registry Upkeep

Run a daily scheduled discovery job that:

- revisits unresolved missing capabilities from failed tasks
- searches MCP registries, A2A Agent Cards, AI-agent catalogs, and vendor docs
- updates candidate metadata, auth requirements, pricing, rate limits, and docs links
- re-runs benchmarks for active providers
- flags candidates that appear better than the current provider mix for review

This daily job is how PlanMyAgents stays current with newly available agents and
MCP servers without trusting unreviewed web results at runtime.

### Example Output

```json
{
  "status": "plan_ready",
  "estimated_cost_usd": 12.75,
  "sub_tasks": [
    {
      "capability": "semantic_search",
      "provider": "tavily",
      "description": "Find EU HR tech SaaS companies"
    },
    {
      "capability": "contact_enrichment",
      "provider": "apollo",
      "description": "Find CTO contacts"
    },
    {
      "capability": "email_verification",
      "provider": "hunter",
      "description": "Verify work emails"
    }
  ]
}
```

### Acceptance Criteria
- 20 sample prompts produce valid plans.
- Unsupported prompts produce refusal with reasons.
- Plans stay within declared cost ceiling.
- Planner output is validated with Pydantic before execution.

### Prototype Findings So Far

- Local Qwen through Ollama is suitable for early planner iteration, but its JSON
  output must remain validated before execution.
- Provider selection must not be exposed in the user UI; it belongs in the
  router.
- Unsupported tasks should start discovery enrichment and add non-routable
  candidates with `will_fail: true`.
- Discovery must prefer MCP/A2A/AI-agent providers and use APIs only as fallback.
- A discovered provider is not a usable provider until credentials, adapter,
  benchmarks, and smoke tests are complete.
- Current discovery supports configured source manifests and curated 64-candidate
  ingestion; broad crawling, freshness metadata, and promotion readiness remain
  the next milestones.
- Unsupported responses now include a first per-capability gap report; production
  still needs a fuller user-facing explanation separate from raw JSON:
  normalized intent, per-capability blockers, discovered candidates, and required
  promotion steps.

---

## 8. Phase 5: Execution Engine

### Objective
Execute approved plans reliably and return structured results.

### Work
Implement:

- `POST /api/tasks`
- `GET /api/tasks/{id}`
- async execution with Inngest or Temporal
- retry per provider
- cost-ceiling enforcement
- partial-result support
- normalized final output

### Implemented v0 Slice

The current codebase includes a local Phase 4 slice:

- `WorkflowExecutor` executes a validated `GoalPlan` through configured providers.
- Runtime scoring estimates confidence from live provider responses.
- The stitcher returns structured records, sources, total cost, average confidence,
  and refusal reasons.
- Planner outputs with unresolved placeholders such as `from_semantic_search_results`
  are refused until dependency-aware execution is implemented.
- The demo UI uses this workflow path instead of a one-off benchmark loop.
- Tests cover successful multi-step execution and refusal when any sub-task is
  not routable.

Remaining production work:

- Persist task/workflow state in Postgres.
- Add async workflow orchestration with Inngest or Temporal.
- Add dependency-aware parallel execution.
- Record every provider call as benchmark/routing feedback.
- Add CSV/JSON export from the web UI.

### Execution Semantics
- Independent sub-tasks run in parallel.
- Dependent sub-tasks wait for upstream output.
- Provider failures do not always fail the full task.
- If cost ceiling is reached, remaining sub-tasks are skipped and explained.
- Every provider call writes a benchmark row.

### Acceptance Criteria
- Can execute one workflow end-to-end from API.
- Can handle provider failure and return partial output.
- Output includes:
  - sources
  - confidence
  - provider used
  - actual cost
  - refusal/partial-failure reasons

---

## 9. Phase 6: Public Benchmark / Discovery Leaderboard

### Objective
Turn internal benchmark results into a public trust and distribution asset.

### Work
Create:

- `/leaderboard`
- `/leaderboard/email-verification`
- `/leaderboard/contact-enrichment`
- provider detail pages

### What To Show
- provider
- capability
- sample size
- success rate
- average score
- p95 latency
- estimated cost
- last updated

### What Not To Show Initially
- raw test inputs involving PII
- customer task data
- exact private workflow prompts
- vendor-paid placements

### Acceptance Criteria
- leaderboard renders from DB data
- scores are reproducible locally
- methodology page links to `docs/benchmark-methodology.md`
- at least 3 providers ranked in one category

---

## 10. Phase 7: Minimal UI

### Objective
Make the product usable by design partners without founder handholding.

### Pages

| Page | Purpose |
|---|---|
| `/` | landing page |
| `/app/new-task` | submit prompt and cost ceiling |
| `/app/tasks/{id}/plan` | review plan and approve |
| `/app/tasks/{id}` | progress + output |
| `/leaderboard` | public benchmark |

### Non-goals
- no advanced dashboard
- no team permissions
- no admin console
- no custom workflow builder
- no payment complexity if manual invoicing is enough for pilots

### Acceptance Criteria
- a design partner can run a task without your help
- result downloadable as CSV and JSON
- errors are understandable
- task history is visible
- unsupported tasks render as structured product explanations, not raw internal
  planner/discovery JSON
- internal trace details remain available behind a debug panel or log view

---

## 11. Phase 8: Design Partner Pilots

### Objective
Run real workflows and prove repeated value.

### Work
For each design partner:

1. import their sample task
2. run planner
3. show cost estimate
4. execute
5. review output together
6. compare against their current process
7. ask whether they would run it again next week

### Metrics

| Metric | Target |
|---|---:|
| real workflows executed | 10+ |
| repeated workflow users | 3+ |
| useful output rate | 70%+ |
| cost estimate accuracy | within 25% |
| provider failure recovery | graceful |
| user says "I would pay" | 2+ |

### Acceptance Criteria
Proceed to public launch only if:

- at least 2 users repeat a workflow
- output quality beats their current method in some dimension
- users care about confidence/source/cost breakdown

---

## 12. Phase 9: Production Hardening

### Objective
Make the system credible enough for public launch and investor diligence.

### Work
- add rate limiting
- add Sentry
- add PostHog
- add audit logs
- add provider health checks
- add background benchmark schedule
- add privacy policy
- add terms of service
- add sub-processor list
- add data deletion endpoint

### Acceptance Criteria
- no raw API keys logged
- no raw PII in benchmark public output
- provider failures visible in admin logs
- cost overruns impossible without explicit approval
- benchmark recomputation works on schedule

---

## 13. Suggested Build Order

If building solo, implement in this order. Items 1-13 now exist as a local
prototype slice; items 14+ are next.

1. Discovery candidate schema and lifecycle states
2. Static discovery source connector
3. MCP catalog connector
4. A2A Agent Card connector
5. Candidate normalizer
6. Deduplication
7. Synonym and typo-tolerant query expansion
8. Search/query API over discovered candidates
9. AI-agent directory connector
10. Web/doc manifest connector
11. Daily discovery/upkeep script
12. Structured unsupported-response renderer
13. Freshness metadata, stale filtering, and promotion-readiness reporting
14. SQLite discovery candidate store and local research scheduler
15. Benchmark YAML schema expansion
16. Benchmark runner and report generator
17. Planner prompt connected to discovery search
18. Promote 3-5 candidates into executable providers
19. Workflow executor and stitcher
20. Discovery/benchmark leaderboard
21. Minimal UI
22. Payment/manual invoicing
23. Production hardening

Do not start with frontend polish.

---

## 14. First Code Milestone

The next real code milestone should be:

> From CLI, search a user subtask against multiple discovery sources, normalize
> 50+ candidate agents/providers, dedupe them, and return ranked non-routable
> candidates with `will_fail` reasons.

Command:

```bash
python3 scripts/run_discovery.py --query "find agents that can enrich B2B contacts"
python3 scripts/search_discovery.py --capability contact_enrichment --task "find CTO emails"
```

Output:

```text
Candidates: 50
Source types: mcp_server, a2a_agent, ai_agent, api_provider
Deduped entities: 37
Top matches:
- provider_type=mcp_server, capability=contact_enrichment, will_fail=true, reason=not_benchmarked
- provider_type=ai_agent, capability=contact_enrichment, will_fail=true, reason=requires_credentials
- provider_type=api_provider, capability=contact_enrichment, will_fail=true, reason=adapter_missing
```

This proves the discovery/index loop before the full execution product exists.

---

## 15. Fundraise-Readiness Checklist

Before raising, aim for:

- [ ] Agent Discovery Index v0 with 500+ normalized candidates
- [ ] MCP/A2A/AI-agent sources ingested, not only API vendors
- [ ] search API can map user subtasks to candidate agents/providers
- [ ] 100+ benchmark cases
- [ ] 5+ providers benchmarked
- [ ] public leaderboard live
- [ ] 10+ real workflows executed
- [ ] 3+ repeated users/design partners
- [ ] clear Clay objection response
- [ ] one public benchmark report
- [ ] one paid pilot or credible LOI
- [ ] architecture diagram
- [ ] privacy/compliance plan

If fewer than half are true, keep building/validating before fundraising.

---

## 16. Decision Tree

### If benchmark shows strong provider variance
Proceed with routing product.

### If benchmark shows weak variance
Benchmark is not enough. Either:
- change capability category, or
- focus on workflow automation, not provider ranking.

### If users like benchmark but not execution
Build Benchmark API / report product first.

### If users like execution but not benchmark
Position as workflow automation, but accept higher Clay competition.

### If users do not care
Kill the beachhead and pick a different measurable workflow category.

---

## 17. Final Implementation Principle

PlanMyAgents should be built from the inside out:

1. discovery/index truth
2. benchmark truth
3. gated promotion into routable providers
4. routing logic
5. workflow execution
6. UI
7. payments
8. platform expansion

If the discovery index is weak, routing cannot scale. If benchmark truth is weak,
the company cannot be trusted. Build both before expanding adapters and UI.
