# Sprint Quality - Trust Contract Recovery Plan

> **Date:** 2026-05-17  
> **Owner:** single developer + reviewer model  
> **Status:** proposed execution plan  
> **Why this sprint exists:** the product keeps failing on the same core loop: discover candidates, recommend the wrong thing, refuse honestly in one surface, then export misleading artifacts in another. This sprint is a deliberate stop-the-line quality sprint before adding T2/T3/T4 features.

---

## 0. Executive Diagnosis

The codebase has enough pieces to be valuable, but the pieces do not share one hard product contract.

The intended contract is:

> A user-facing recommendation or executable recipe step may only contain a provider that is **judged relevant to the goal**, **qualified by trust policy**, and **routable/exportable for the target host**. Everything else is a candidate, gap, fallback, or TODO.

The current implementation violates this in multiple places:

- Discovery candidates can bypass the relevance judge if they were found inline during live scout dispatch.
- Recipe export treats "candidate exists" as "recommendation exists," even when `/goal` says the plan is unsupported.
- Export renderers produce artifacts that look runnable but are not valid for the target product (`n8n_yaml`, Claude Desktop `echo` stubs).
- The UI renders download/save affordances for refused plans without clear "not runnable" gating.
- Tests assert output is parseable or non-empty, not that the target host would accept it or that the product contract is honest.
- Docs promise stronger invariants than the code enforces: HLD says unqualified candidates may appear in search, but **never in recipes**.

This sprint should not be measured by feature count. It should be measured by whether a basic user flow becomes trustworthy:

1. A gift query must not shortlist an interview research tool.
2. A refused plan must not export runnable-looking workflows.
3. A recipe file must be valid for its named target.
4. The same state must mean the same thing across backend, frontend, docs, and tests.

---

## 1. Non-Negotiable Quality Invariants

### QI-1: One Provider State Model

Every provider-like object crossing the product boundary must be classified into exactly one of these states:

| State | Meaning | Allowed user surface |
|---|---|---|
| `discovered_candidate` | Found by a source/scout, capability inferred, not judged for this goal | Search/index/debug only |
| `judged_relevant_candidate` | CandidateJudge accepted it for this specific goal/capability | "Candidates found" sections only |
| `qualified_candidate` | Relevant plus meets trust policy (`provider_type`, `verification_status`, freshness) | Ranked candidate list |
| `routable_provider` | Qualified plus adapter/protocol/client configuration exists, benchmark/promotion gates pass | Recommendation + executable recipe |
| `exportable_provider` | Routable or target-host handoff is valid for the specific export format | Host-specific recipe artifact |
| `gap` | No exportable provider for this step | TODO/gap block only |

Any code path that collapses these states into `recommendation != None` is a bug.

### QI-2: Fail Closed for User-Facing Recommendations

If the judge, store, LLM, route gate, or exportability check cannot evaluate a candidate, the candidate must not be shown as recommended.

Allowed fail-open surfaces:

- Debug drawer
- Discovery details
- Search with explicit "unverified / not routable" badge
- Open opportunity / demand signal

Forbidden fail-open surfaces:

- `workflow_options[].steps[].best_match`
- recipe `RecommendedProvider`
- n8n/Cursor/Claude Desktop executable config
- "Save recipe" as if the plan is usable

### QI-3: Refusal Is A Product Output, Not A Partial Success

When `/goal` returns `status: unsupported`:

- It may still include discovery candidates.
- It may still include human alternatives and research backstop.
- It may still include markdown runbook that explains the gaps.
- It must not export runnable-looking host workflows.
- It must not count steps as "covered by recommendation."
- It must not create Claude/Cursor/n8n config entries for unroutable providers.

### QI-4: Host Export Names Must Be True

If a button says "n8n workflow," the file must import into n8n.

If a button says "Claude Desktop config," every entry under `mcpServers` must be a valid Claude Desktop MCP server entry.

If an entry cannot be represented for that host, it belongs in metadata/gaps, not in the runnable section.

### QI-5: Tests Must Exercise Product Contracts, Not Renderer Shapes

Quality gates must include golden product scenarios:

- Gift shopping query must reject Conveo/interviewing tools.
- "Qualitative research platform" must not satisfy "gift research for child."
- Refused plans produce gap-only artifacts.
- n8n export is JSON and schema-valid enough to import.
- Claude Desktop export contains no `echo` placeholder servers.
- Frontend does not show host download buttons when a plan has no exportable steps.

---

## 2. Audit Findings

### 2.1 Discovery And Candidate Quality

Current strengths to reuse:

- `apps/api/planmyagents_api/discovery/sources/*` gives broad source coverage.
- `ScoutDispatcher` already handles parallelism, timeouts, token-gated scouts, dedupe, and logging.
- `CandidateJudge` has a good prompt contract and unit tests for omitted candidate fail-close behavior.
- `discovery/gaps.py` already has the language and structure for qualified vs rejected candidates.
- `DiscoveryCandidate` already carries `route_status`, `will_fail`, `will_fail_reasons`, `verification_status`, `benchmark_status`, `adapter_module`.

Problems:

- `official_mcp_registry.py`, `smithery.py`, and `mcp_marketplace.py` infer capabilities from broad text using embedding similarity. That is fine for recall, but unsafe for recommendation without a hard judge gate.
- `general_research` is too broad. "Qualitative research platform" and "gift research" share embedding signal but are operationally different.
- `CandidateJudge` is applied only to candidates loaded from the persistent store. Inline live-scout candidates can pass through unjudged.
- `_apply_candidate_judge` currently preserves untracked candidates by design:

```python
filtered = [
    r for r in agentic_results
    if r.get("provider_id") in accepted_ids
    or r.get("provider_id") not in judged_ids
]
```

This is exactly the failure mode that let Conveo appear in the gift query.

Required fixes:

- Build `DiscoveryCandidate` objects for inline results and judge them in the same batch as persisted candidates.
- Remove untracked passthrough for recommendation surfaces.
- Keep untracked candidates only in `discovery.unjudged_candidates` or debug metadata.
- Add per-capability judge counts:
  - `candidate_count`
  - `judged_count`
  - `accepted_count`
  - `rejected_count`
  - `unjudged_count`
  - `blocked_from_recommendations_count`
- Add a hard warning if `unjudged_count > 0`, but do not show those as recommended.

### 2.2 Planning, Routing, And Refusal Semantics

Current strengths to reuse:

- `ProviderRouter` already distinguishes configured/routable vs not configured/no adapter.
- Benchmark baseline firewall tests are good and should stay.
- `discovery/gaps.py` already has `candidate_found_but_not_routable` language.
- `will_fail` and `will_fail_reasons` are already present in discovery models.

Problems:

- The backend can say `status: unsupported` while still attaching discovered candidates to `discovery.candidates`.
- Recipe context builder consumes `discovery.agentic_results`, `discovery.candidates`, and `discovery.candidates_by_capability` without enforcing `will_fail == false`.
- `RecommendedProvider` does not carry enough state to decide whether it is actually recommendation-safe:
  - no `route_status`
  - no `will_fail`
  - no `will_fail_reasons`
  - no target-host exportability
  - no `judge_status`
  - no `accepted_by_judge`

Required fixes:

- Introduce an explicit backend type/function:
  - `is_recommendation_safe(candidate, capability, goal_context) -> bool`
  - `is_recipe_step_exportable(provider, format_id) -> ExportabilityDecision`
- Recommendation-safe must require:
  - candidate was judged for this goal OR came from a pre-verified curated routable registry entry
  - judge accepted candidate for this capability
  - `will_fail is False`
  - `route_status == "ready_for_promotion"` or equivalent approved state
  - `benchmark_status == "passed"` where the capability requires benchmark gate
  - adapter/protocol/client information exists for the handoff mode
- Refused plans should set all recipe step recommendations to `None` unless a step has a truly exportable provider.
- Split discovery payload into:
  - `recommendations`
  - `candidates`
  - `blocked_candidates`
  - `apis_without_agents`
  - `debug`

### 2.3 Recipe Export

Current strengths to reuse:

- `planner/recipe_export/__init__.py` has a useful format-agnostic `RecipeContext`.
- Markdown and CLI renderers already have gap/TODO paths.
- `goal_cache` is useful and should stay.
- Frontend already consumes `plan.recipes` as format entries.

Problems:

- `n8n_yaml` is a false promise. n8n imports JSON workflow files, not YAML.
- n8n renderer produces no stable node IDs and uses an outdated/generic HTTP Request shape.
- n8n renderer falls back to `https://example.com/<capability>` for missing API bases, creating runnable-looking nonsense.
- Claude Desktop renderer emits `command: "echo"` placeholder servers for unknown install commands.
- Claude Desktop renderer only supports local stdio (`command`/`args`), not remote MCP server `url`.
- `RecipeContext.covered_step_count` currently counts any recommendation, not any exportable provider.
- `missing_steps` and `non_mcp_steps` do not explain repeated provider usage or unexportable providers clearly.
- `_planmyagents.generated_by` and labels include Unicode punctuation; keep docs fine, but generated machine config should be ASCII-friendly unless needed.

Required fixes:

- Replace `n8n_yaml` with `n8n_json`.
  - Preserve backwards compatibility temporarily by returning a `410 Gone` or alias warning for `n8n_yaml`.
  - Update docs, frontend labels, tests, and partner API planned names.
- Implement target-specific exportability:
  - `claude_desktop_json`: only local stdio MCP entries with known command/args, or remote MCP entries if client config supports `url`.
  - `cursor_mcp_json` / `cursor_prompt`: only valid Cursor MCP config entries; prompt can include gaps.
  - `n8n_json`: valid n8n workflow JSON with IDs, nodes, connections, metadata.
  - `markdown`: can always render gaps and candidates, but must mark status clearly.
  - `cli`: only include executable steps when command/curl is real; otherwise TODO comments only.
- Remove `echo` placeholders from `mcpServers`.
- Move all unresolved entries into `_planmyagents.gaps` or `_planmyagents.unexportable_steps`.
- Add `coverage` metadata:
  - `step_count`
  - `exportable_step_count`
  - `recommended_step_count`
  - `gap_count`
  - `target_format`
  - `status: "runnable" | "partial" | "gap_only"`
- For unsupported plans:
  - host-specific exports should either be disabled at `/goal` or render gap-only artifacts with clear warnings.

### 2.4 Frontend Product Surfaces

Current strengths to reuse:

- `OutcomeCard` already distinguishes refused vs executable modes.
- `MissingCapabilities`, `ResearchBackstop`, and `HumanAlternatives` are useful for honest refusal UX.
- `tags.ts` has routable tag language.
- `RecipeDownloadButtons` is a clean component and can be adapted.
- Clerk optional wrapper keeps dev mode placeholder-safe.

Problems:

- `RecipeDownloadButtons` renders whenever `goal_id` and `recipes` exist, regardless of plan status/exportability.
- The copy says "Download recipe" and "run it in your own environment," even on refused gap-only plans.
- The n8n hint says "Import into n8n via Import from File," but the file is YAML and invalid.
- Save recipe can persist a refused/misleading plan without a quality warning.
- The UI can show "Cannot complete this goal yet" and then a download card that looks actionable.
- Account dashboard downloads saved recipes without surfacing whether the recipe is runnable, partial, stale, or gap-only.

Required fixes:

- Add recipe status to API response:
  - `runnable`
  - `partial`
  - `gap_only`
  - `unavailable`
- Change frontend behavior:
  - If `gap_only`, show "Download diagnostic runbook" only for markdown.
  - Hide n8n/Cursor/Claude/CLI buttons unless exportable steps exist.
  - On refused plans, show a "Why no runnable recipe?" explanation.
  - Save button copy should become "Save diagnostic plan" for refused plans.
- Update `FORMAT_HINTS`:
  - `n8n_json`: "Import JSON workflow into n8n."
  - remove `n8n_yaml`.
- Add frontend type fields for recipe coverage/status.
- Add browser smoke tests for:
  - refused gift query shows no host workflow buttons
  - executable synthetic plan shows expected buttons
  - account saved recipe shows status badge

### 2.5 Pro Tier, Clerk, Stripe, Saved Recipes

Current strengths to reuse:

- `marketplace_store` separation is directionally right.
- Clerk backend dependencies are placeholder-safe.
- Stripe client/webhook code has tests and avoids a large SDK dependency.
- Saved recipes are a useful Pro primitive.

Problems:

- The implemented `marketplace_store` is a Pro-user subset, not the marketplace vendor schema promised in docs.
- Saved recipes currently save plan payloads without recipe quality status.
- Plan cache TTL and saved recipes interact awkwardly: saved recipes can outlive the 7-day cache, but download URLs still imply cache-backed re-rendering.
- Auth/billing are now mixed into `web/app.py`, which is already too large.
- Frontend Clerk dependencies forced a Next 16 upgrade; web lint is now broken/stale (`next lint` + `eslint-config-next@15`).

Required fixes:

- Persist `recipe_status`, `coverage`, and `plan_status` with saved recipes.
- Saved recipe downloads should render from saved `recipe_json`, not only cache lookup.
- Split API routers out of `web/app.py`:
  - `web/routes/goal.py`
  - `web/routes/recipe.py`
  - `web/routes/billing.py`
  - `web/routes/account.py`
  - `web/routes/discovery.py`
- Fix web lint:
  - move to ESLint flat config compatible with Next 16
  - or temporarily replace `npm run lint` with `eslint .` configured explicitly
  - update `eslint-config-next` to match Next 16 when available

### 2.6 Marketplace, Partner API, And Promised Features

Promised but not implemented or only partially implemented:

- Vendor portal alpha (`apps/vendor-web/`)
- Vendor signup / RBAC / audit
- Claim verification via DNS + email
- Profile editor + moderation overlay
- Verified Benchmark request + right-of-reply flow
- Sponsored placement renderer and `/disclosure`
- Demand-Data API
- Partner gateway + `/partner/v1/search`
- Partner gateway + `/partner/v1/recipe`
- Add-to-Cursor button / host-native handoff
- BYO-credentials sandbox UI and `/sandbox/execute`
- Vendor-neutrality firewall CI lint beyond benchmark baseline tests

Quality position:

Do not start these until Q0/Q1 of this sprint is done. Adding marketplace/partner surfaces on top of a broken recommendation contract will multiply trust failures.

What to reuse later:

- `marketplace_store` patterns for SQL/in-memory/SQLite backends.
- `billing` and `auth` modules for Pro user and future vendor auth.
- benchmark runner/store/scoring for Verified Benchmark.
- `discovery_gaps` and demand recorder for Demand-Data.
- `RecipeContext` concept after it is made exportability-aware.
- `SandboxRunner` docstring as a contract seed, but not the placeholder implementation.

What to defer:

- Sponsored placement CRUD.
- Execution fee.
- Partner revenue share.
- Vendor portal app.
- Full AWS deployment automation.

### 2.7 Tests, CI, And Local Quality

Current strengths to reuse:

- Backend unittest suite is broad.
- Ruff/compile/json validation exist.
- Scout dispatcher tests are strong.
- CandidateJudge core tests are strong.
- Benchmark baseline firewall tests are good.

Problems:

- Tests validate renderer syntax, not target-host compatibility.
- No golden product scenario tests for common user goals.
- No test locks "unjudged live candidates must not become recommendations."
- No test locks "unsupported plan has zero exportable host workflow steps."
- No frontend tests or browser smoke tests are wired into `make quality`.
- Web lint script is stale/broken under Next 16.
- `make quality` does not include `web-typecheck` or `web-build`.
- `discovery_run_events.jsonl` under `data/` appears in searches and can become noisy/generated state.

Required fixes:

- Add `make product-quality`:
  - backend tests
  - contract tests
  - web typecheck
  - web build
  - recipe fixture validation
- Add `make web-quality`:
  - `npm run typecheck`
  - `npm run build`
  - fixed lint once configured
- Add tests:
  - `test_goal_quality_contract.py`
  - `test_recipe_export_contract.py`
  - `test_frontend_recipe_contract` or Playwright/browser smoke if tooling is added
  - `test_host_export_schemas.py`
- Add fixtures:
  - gift query with Conveo-like candidate
  - unsupported plan with `will_fail=true` candidates
  - valid stdio MCP server
  - valid remote MCP server
  - valid API provider
  - non-exportable API-only provider

---

## 3. Sprint Goal

**Make PlanMyAgents honest and deterministic for the core product loop: goal -> discovery -> judged recommendations -> refusal -> valid recipe export.**

By the end of this sprint:

- No unjudged live candidate can appear as a recommendation.
- No unroutable provider can appear as a runnable recipe step.
- No refused plan can export runnable-looking host workflows.
- n8n export is renamed/fixed to valid JSON.
- Claude/Cursor exports contain only real MCP config entries.
- Frontend copy and button gating match backend state.
- Quality tests catch the Conveo/gift-query class of bug permanently.

---

## 4. Workstreams

### Q0 - Stop The Bleeding: Recommendation Contract

| ID | Task | Files | Acceptance |
|---|---|---|---|
| Q0-1 | Define `ProviderSurfaceState` / `RecommendationSafety` model | `apps/api/planmyagents_api/discovery/`, `web/models.py` | One shared function classifies candidate state; no ad-hoc `recommendation != None` checks in export path |
| Q0-2 | Fix `_apply_candidate_judge` fail-open passthrough | `apps/api/planmyagents_api/web/app.py`, new tests | Inline live-scout candidates are judged or blocked; `untracked_passthrough` no longer enters recommendations |
| Q0-3 | Convert inline result dicts into `DiscoveryCandidate` for judging | `web/app.py` or helper module | Conveo-like inline candidate is rejected by synthetic judge fixture |
| Q0-4 | Add judge coverage metadata | API discovery payload | Response exposes judged/unjudged/blocked counts per capability |
| Q0-5 | Add hard product test for gift query class | `apps/api/tests/test_goal_quality_contract.py` | Candidate with description "analyze interviews" never appears for gift-shopping goal |

Implementation notes:

- Prefer extracting logic out of `web/app.py` into `discovery/recommendation_gate.py`.
- Do not call LLM in unit tests; use scripted `CandidateJudge`/client or monkeypatch gate result.
- Preserve raw candidates in debug/search payloads so discovery breadth is not lost.

### Q1 - Refusal And Recipe Coverage Contract

| ID | Task | Files | Acceptance |
|---|---|---|---|
| Q1-1 | Extend `RecommendedProvider` with routing/exportability fields | `planner/recipe_export/__init__.py` | Carries `route_status`, `will_fail`, `will_fail_reasons`, `judge_status`, `accepted_by_judge` |
| Q1-2 | Filter recipe recommendations through safety gate | `build_recipe_context` | `will_fail=true` candidates become gap steps |
| Q1-3 | Add `RecipeCoverage` metadata | recipe context + API response | `step_count`, `recommended_step_count`, `exportable_step_count`, `gap_count`, `status` |
| Q1-4 | Fix unsupported plan behavior | goal route + export route | Unsupported plan cannot claim "4 covered by recommendation" |
| Q1-5 | Tests for refused plan exports | `test_recipe_export_contract.py` | Markdown is gap runbook; host workflows are disabled or gap-only with no executable entries |

Acceptance scenarios:

- Given a plan with 4 sub-tasks and 4 `will_fail=true` candidates:
  - `covered_step_count == 0`
  - markdown says "No recommended provider yet" for all steps
  - CLI contains no `curl`/`npx` executable step for those providers
  - Claude JSON has empty `mcpServers`
  - n8n JSON has no executable HTTP nodes, or export is unavailable

### Q2 - Host Export Correctness

| ID | Task | Files | Acceptance |
|---|---|---|---|
| Q2-1 | Replace `n8n_yaml` with `n8n_json` | `planner/recipe_export/`, frontend, tests, docs | Downloaded n8n file is JSON and has valid workflow shape |
| Q2-2 | Implement n8n node IDs and current HTTP node shape | new `n8n_json.py` | Fixture validates `nodes[].id`, `nodes[].name`, `nodes[].type`, `connections` |
| Q2-3 | Remove `echo` stubs from Claude/Cursor configs | `claude_desktop_json.py`, `cursor_prompt.py` | Unknown install command goes to `_planmyagents.unexportable_steps`, not `mcpServers` |
| Q2-4 | Support remote MCP config when target host allows it | renderer metadata + candidate metadata | Remote streamable-http candidates emit `url` only where supported |
| Q2-5 | Add host schema fixture tests | `test_host_export_schemas.py` | Claude/Cursor/n8n JSON parse and meet minimal schema |

Decisions needed before implementation:

- Whether to keep `n8n_yaml` as a deprecated alias for one sprint.
- Whether Claude Desktop in the target version supports remote MCP `url`; if uncertain, mark remote MCP as unexportable for Claude and export for Cursor only if confirmed.
- Whether `cursor_prompt` should be split into `cursor_mcp_json` and `cursor_prompt`. Current file mixes config and prompt template.

Recommended decision:

- Rename to `n8n_json`.
- Keep `n8n_yaml` endpoint returning HTTP 410 with message: "n8n exports are now JSON; use format=n8n_json."
- Split Cursor later; for this sprint, keep `cursor_prompt` but make its config section real-only.

### Q3 - Frontend Honest UX

| ID | Task | Files | Acceptance |
|---|---|---|---|
| Q3-1 | Add recipe status/coverage types | `apps/web/src/lib/api.ts` | TS models include `recipe_status` and coverage |
| Q3-2 | Gate download buttons by exportability | `RecipeDownloadButtons.tsx`, `goal/page.tsx` | Refused gap-only plans show only markdown diagnostic download |
| Q3-3 | Fix format hints and labels | `RecipeDownloadButtons.tsx` | `n8n_json`, no `n8n_yaml` copy |
| Q3-4 | Change Save Recipe copy by status | `SaveRecipeButton.tsx`, `SaveRecipeForm.tsx` | Refused plan says "Save diagnostic plan," not "Save this recipe" |
| Q3-5 | Show coverage/status in account saved recipe list | `AccountDashboard.tsx` | Saved recipes show runnable/partial/gap-only badge |

Acceptance scenarios:

- User sees "Cannot complete this goal yet" and does not see host-specific "run this workflow" CTAs.
- User can still download a markdown diagnostic/runbook explaining what is missing.
- If a recipe is partial, UI names it partial and lists which steps are gaps.

### Q4 - Quality Harness And Golden Scenarios

| ID | Task | Files | Acceptance |
|---|---|---|---|
| Q4-1 | Add golden scenario fixtures | `apps/api/tests/fixtures/quality/` | Gift query, email verification, web search, unsupported query |
| Q4-2 | Add backend contract tests | `test_goal_quality_contract.py` | Locks judge/routing/refusal invariants |
| Q4-3 | Add export contract tests | `test_recipe_export_contract.py`, `test_host_export_schemas.py` | Host exports validated beyond parseability |
| Q4-4 | Add web build/typecheck to quality | `Makefile` | `make product-quality` includes backend + web checks |
| Q4-5 | Fix Next 16 lint config or remove broken lint script from quality docs | `apps/web/package.json`, ESLint config | `npm run lint` works or is replaced with explicit supported command |

Minimum golden scenarios:

1. `gift_for_3yo`:
   - Conveo-like candidate rejected.
   - Forage-like unverified/unroutable candidate not exportable.
   - Refused plan has markdown-only diagnostic.
2. `verify_email_known_provider`:
   - synthetic routable provider produces CLI/Claude/Cursor entries.
3. `unsupported_no_candidates`:
   - no recommendations, no host exports, demand event recorded.
4. `remote_mcp_known_url`:
   - remote MCP is represented only for supported hosts.
5. `api_provider_fallback`:
   - API provider appears as fallback, not as agentic recipe unless explicitly approved.

### Q5 - Codebase Cleanup And Modularization

| ID | Task | Files | Acceptance |
|---|---|---|---|
| Q5-1 | Split `web/app.py` by route groups | `apps/api/planmyagents_api/web/routes/*` | Goal/recipe/billing/discovery/account routes separated; imports cleaner |
| Q5-2 | Extract recommendation gate from app route | `discovery/recommendation_gate.py` | Unit-tested independently |
| Q5-3 | Extract recipe exportability decisions | `planner/recipe_export/exportability.py` | Renderer code does not duplicate safety checks |
| Q5-4 | Move generated/event data out of tracked searches if appropriate | `data/discovery_run_events.jsonl`, `.gitignore` | Generated logs do not pollute audits |
| Q5-5 | Update docs to match actual format names and quality gates | `docs/HLD.md`, `docs/LLD.md`, `docs/operations.md`, `sprint-16th-may.md` | No doc claims `n8n_yaml` importability |

Deletion/rename candidates:

- Delete or deprecate `planner/recipe_export/n8n_yaml.py`.
- Delete `echo` placeholder behavior in Claude/Cursor renderers.
- Delete fail-open `untracked_passthrough` from recommendation path.
- Delete stale `next lint` script if it cannot be made compatible immediately; replace with a working lint command.
- Delete any docs claiming recipe host exports are runnable when only placeholders exist.
- Rename "covered" to "candidate_covered" only if it truly means candidate, otherwise replace with `exportable_step_count`.

### Q6 - Promised Feature Reprioritization

Pause until Q0-Q4 pass:

- T2 Add-to-Cursor buttons
- BYO sandbox UI
- Vendor portal alpha
- Claim verification
- Partner API skeleton
- Sponsored placements
- Demand-Data subscription API
- AWS deployment hardening

Then resume in this order:

1. Add-to-Cursor button only after `cursor_prompt` emits valid config.
2. BYO sandbox UI only after recipe steps carry exportability and credential requirements safely.
3. Partner `/recipe` only after host exports are valid.
4. Vendor portal only after recommendation/ranking firewall is test-backed.
5. Sponsored/disclosure only after ranker import firewall and UI disclosure tests exist.

### Q7 - Observability And Operator Feedback

| ID | Task | Files | Acceptance |
|---|---|---|---|
| Q7-1 | Add request-level quality summary log | goal route | Logs judged/blocked/exportable counts |
| Q7-2 | Add debug field for blocked recommendations | API response | Engineer drawer can show why a candidate was blocked |
| Q7-3 | Add metrics hooks | future ops | Counts for `unjudged_blocked`, `export_gap_only`, `invalid_export_prevented` |
| Q7-4 | Add local audit script | `scripts/audit_product_contract.py` | Runs golden fixtures and prints pass/fail summary |

---

## 5. Reuse / Delete / Implement Inventory

### Reuse

| Area | Reuse | Why |
|---|---|---|
| Discovery scouts | `discovery/sources/*`, `ScoutDispatcher` | Broad recall is valuable when gated correctly |
| CandidateJudge | `discovery/candidate_judge.py` | Good concept; issue is integration fail-open |
| Discovery gaps | `discovery/gaps.py` | Already models blockers and qualification |
| Promotion/readiness | `discovery/readiness.py`, `will_fail` fields | Correct raw ingredients for recommendation gate |
| Benchmark baseline firewall | `test_benchmark_baseline_firewall.py`, `test_provider_router.py` | Strong boundary tests |
| Goal cache | `planner/recipe_export/goal_cache.py` | Useful for recipe downloads; supplement for saved recipes |
| Markdown renderer | `recipe_export/markdown.py` | Best universal fallback; make it more honest |
| CLI renderer | `recipe_export/cli.py` | Useful only after executable-step filtering |
| Clerk/Stripe modules | `auth/`, `billing/` | Keep but route-split out of monolithic app |
| Marketplace store pattern | `marketplace_store/store.py` | Good backend pattern for future vendor/partner stores |
| Research/human fallback UI | `ResearchBackstop`, `HumanAlternatives` | Good refusal UX |

### Delete Or Deprecate

| Item | Action | Reason |
|---|---|---|
| `n8n_yaml` format | Deprecate/replace with `n8n_json` | n8n imports JSON, not YAML |
| Claude/Cursor `echo` stubs | Delete | Invalid MCP server entries; creates false failures |
| Recommendation untracked passthrough | Delete from user-facing path | Lets riskiest live candidates bypass judge |
| `https://example.com/<capability>` executable fallback | Delete from host exports | Looks runnable but is fake |
| "covered by recommendation" for unroutable candidates | Rename/remove | Misleading coverage metric |
| Broken `next lint` script | Fix or replace | Current script is not a usable quality gate |
| Docs saying YAML import is valid | Update | False product promise |

### Implement

| Item | Purpose |
|---|---|
| `discovery/recommendation_gate.py` | Single source of truth for recommendation-safe candidates |
| `planner/recipe_export/exportability.py` | Single source of truth for per-format exportability |
| `n8n_json.py` | Valid n8n workflow export |
| Recipe `coverage/status` metadata | Align backend/frontend/account/downloads |
| Inline candidate judge conversion | Judge live scout candidates |
| Product-quality fixture tests | Lock core user scenarios |
| Web quality target | Prevent another untested dev/build breakage |
| Route modules | Reduce `web/app.py` blast radius |

---

## 6. Definition Of Done

This sprint is done only when all are true:

1. `make test` passes.
2. `make compile` passes.
3. `make lint` passes.
4. `make web-typecheck` passes.
5. `make web-build` passes.
6. New `make product-quality` passes.
7. Gift query golden test rejects Conveo-like candidate.
8. Unsupported plan golden test exports no runnable host workflow.
9. n8n export is JSON and passes fixture schema checks.
10. Claude/Cursor exports contain no placeholder `echo` MCP servers.
11. Frontend refused-plan page shows no host-specific workflow download buttons.
12. Docs no longer claim `n8n_yaml` importability.
13. Saved recipes persist and display recipe status/coverage.
14. Reviewer can run one local browser smoke test and see consistent UI state.

---

## 7. Suggested Execution Order

### Day 1: Contract And Failing Tests

- Add failing tests for Conveo/gift and unsupported export.
- Add failing tests for `n8n_yaml` deprecation and no `echo` stubs.
- Add recommendation gate skeleton.
- Do not fix UI yet.

### Day 2: Backend Gate

- Fix `_apply_candidate_judge`.
- Convert inline candidates for judge.
- Filter recipe context by `will_fail`/route status.
- Add coverage metadata.

### Day 3: Exporters

- Replace `n8n_yaml` with `n8n_json`.
- Remove fake URLs and `echo` stubs.
- Add host exportability decisions.
- Update recipe endpoint response/headers.

### Day 4: Frontend And Saved Recipes

- Gate `RecipeDownloadButtons`.
- Update copy and format labels.
- Add recipe status to account dashboard and save flow.
- Fix web lint/typecheck/build configuration.

### Day 5: Cleanup, Docs, Smoke

- Route/module cleanup where low-risk.
- Update docs.
- Add product-quality make target.
- Run browser smoke test.
- Re-run the gift query and compare against the screenshot failure.

---

## 8. Explicit Non-Goals For This Sprint

- Do not add vendor portal screens.
- Do not add partner API endpoints.
- Do not implement sponsored placement.
- Do not add execution fee.
- Do not broaden discovery sources.
- Do not tune embeddings as the primary fix.
- Do not add more "general research" backstops until recommendation gating is fixed.
- Do not deploy to AWS until quality gates are green locally.

---

## 9. Open Decisions For Reviewer

1. Should unsupported plans expose only markdown downloads, or should they also expose host-specific gap-only files?
   - Recommendation: markdown only for now.
2. Should `registered_in_directory` remain qualified for candidate display?
   - Recommendation: yes for search/candidates, no for recipe without route/exportability.
3. Should `general_research` be split?
   - Recommendation: yes after Q0; introduce `web_research`, `market_research`, `qualitative_research`, and keep `general_research` internal-only or fallback-only.
4. Should remote MCP `url` be supported in Claude Desktop exports?
   - Recommendation: verify against current Claude Desktop config docs before emitting. If uncertain, mark remote MCP unexportable for Claude.
5. Should `n8n_yaml` remain as a compatibility alias?
   - Recommendation: return `410 Gone` with migration hint; do not silently serve wrong format.

---

## 10. The Honest Product Bar

The product does not need to find a runnable agent for every goal yet.

It does need to be honest every time:

- "We found candidates, but they are not runnable yet."
- "This looks relevant, but has not been verified."
- "This provider is relevant, but cannot be exported to n8n."
- "This is a diagnostic runbook, not an executable workflow."
- "No, Conveo is not a gift-shopping agent."

That honesty is the platform. Everything else, including marketplace monetization and partnerships, depends on it.
