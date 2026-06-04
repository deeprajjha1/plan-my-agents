# Requirements Document

## Introduction

PlanMyAgents is an Agent Discovery Index plus a benchmarked routing layer. The honest-scope audit (`docs/honest-scope-audit.md`) established that the platform's central claim — that it produces real, persisted quality scores for publicly-discovered agents — is currently unbuilt. The only adapters that produce real quality numbers are hand-coded wrappers around commodity APIs (Razorpay, Resend, Firecrawl, and similar) that are deliberately firewalled out of routing, plus mock adapters. As a direct consequence, the credibility classifier (`apps/api/planmyagents_api/benchmark/credibility.py`) reports every public leaderboard as `synthetic_only`.

The Agent Evaluation Framework (the "Eval_Framework") closes that gap. The Eval_Framework defines a single, general mechanism for evaluating any agent the platform discovers — `mcp_server`, `a2a_agent`, and `ai_agent` provider types — and feeding honest, provenance-bearing results into the existing `benchmark_runs` and `agent_rankings` stores so the existing credibility classifier can grade them. The Eval_Framework is explicitly honest about what is executable today (MCP and OpenAPI surfaces have wire formats; A2A and free-form `ai_agent` return structured refusals in `agents/protocol.py`) and degrades gracefully to a verification-only result for any provider type that has no invocation surface, rather than fabricating a quality score.

This document specifies the behavioral requirements for the Eval_Framework. Implementation detail (module layout, function signatures) is deferred to the design phase. Requirements are written so the framework is general enough to apply to future discovered agents broadly, while staying truthful about the difference between "verified" (a cheap probe) and "benchmarked" (a scored comparison against ground truth).

## Glossary

- **Eval_Framework**: THE system specified by this document. It coordinates tier selection, ground-truth resolution, safe invocation, scoring, persistence, and provenance for the evaluation of a discovered agent.
- **Eval_Scheduler**: THE component of the Eval_Framework that drives evaluation runs against selected discovery candidates and persists results. It extends the role of the existing `benchmark/scheduler.py` rather than replacing it.
- **Verification_Prober**: THE component that performs cheap static verification of a provider (for example, an MCP `tools/list` probe) without scoring output quality.
- **Ground_Truth_Manager**: THE component that resolves, stores, curates, and versions the ground truth used to score a capability.
- **Eval_Judge**: THE component that produces a quality score for soft or open-ended outputs by applying an LLM-as-judge evaluation against a rubric.
- **Safety_Classifier**: THE component that classifies a capability as side-effecting or read-only and assigns a safety class before any live evaluation is permitted.
- **Case_Generator**: THE component that defines or auto-generates benchmark cases for a capability that has no hand-authored YAML cases.
- **Provenance_Recorder**: THE component that attaches reproducibility metadata (case set, fixtures, timestamp, agent version, tier, run mode) to every persisted eval result.
- **Capability_Descriptor**: THE machine-readable self-description a discovered agent publishes about what it can do — for example, an MCP `tools/list` schema, an A2A Agent Card `skills` array, an OpenAPI operation set, or an ANP capability document. The Capability_Descriptor is the input the Eval_Framework reads to derive what to test.
- **Descriptor_Reader**: THE component that parses a Capability_Descriptor for a given protocol into a normalized internal shape (a list of invokable operations with names, input schemas, and human-readable descriptions) that the Case_Generator and invocation layer consume.
- **Protocol_Invoker**: THE pluggable component that knows how to invoke one specific agent protocol (for example, MCP, A2A, ACP, ANP, or OpenAPI). Each Protocol_Invoker declares a protocol maturity status and either performs a live invocation or returns a structured refusal.
- **Protocol_Invoker_Registry**: THE registry that maps a discovered candidate's protocol to its Protocol_Invoker, so support for a new protocol is added by registering a new invoker rather than by modifying the coordinator.
- **Protocol maturity status**: A per-protocol label that states whether the Eval_Framework can invoke that protocol today: `executable` (a stable wire format exists and is implemented), `refusal_only` (no stable invocation surface yet; verification-only), or `planned` (recognized but not yet implemented).
- **Tool-use decomposition**: The industry-standard four-step decomposition of a tool/agent call used for diagnostic scoring — decide-to-call, select-operation, build-arguments, integrate-result — scored per step rather than only as a single pass/fail outcome.
- **Trajectory**: The ordered sequence of steps (operation selections, arguments, intermediate outputs) an agent produced during an eval case, retained so a failure can be located at its step rather than reported only as an outcome.
- **Credibility_Classifier**: THE existing classifier in `apps/api/planmyagents_api/benchmark/credibility.py` that grades a per-capability leaderboard. The Eval_Framework feeds it; the Eval_Framework does not bypass or replace it.
- **Eval cell**: A single (provider_id, capability) pair under evaluation. An eval cell maps directly to the `(provider_id, capability)` key used by `agent_rankings`.
- **Eval tier**: A named rung in the tiered evaluation ladder. The ordered tiers are: `static_verification`, `functional_smoke`, `scored_benchmark`, and `continuous_reeval`.
- **Ground truth**: The authoritative expected outputs or acceptance criteria for a capability against which a provider response is scored. Ground truth is either an exact-match expected value, a structured rubric evaluated by the Eval_Judge, or absent (in which case the capability is non-eval-able).
- **Eval-able capability**: A capability for which the Eval_Framework can obtain ground truth (exact-match or rubric-based) AND for which an invocation surface exists. Only eval-able capabilities can receive a `scored_benchmark` quality score.
- **Non-eval-able capability**: A capability for which the Eval_Framework cannot obtain ground truth, OR for which no invocation surface exists. A non-eval-able capability receives a verification-only result and is never assigned a fabricated quality score.
- **Side-effecting capability**: A capability whose execution mutates external state (for example, sending email, moving money, creating or deleting records). Contrast with a read-only capability whose execution returns information without mutating external state.
- **Synthetic run**: An eval run whose result carries a `source` value that the Credibility_Classifier treats as non-real (currently `synthetic`, `mock`, `fixture`, or `stub`, per `credibility._is_real_run`). A synthetic run scores the platform's own code, not the vendor.
- **Real run**: An eval run whose result carries a `source` value the Credibility_Classifier treats as real AND whose sample size is greater than zero.
- **Run mode**: The execution mode of an eval run: `dry_run` (no live call), `sandbox` (live call against a synthetic or test fixture endpoint), or `live` (live call against the provider's production surface).
- **Safety class**: The Safety_Classifier verdict for a capability: `read_only`, `side_effecting`, or `unclassified`.
- **Protocol execution gate**: The environment flag `PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION` (default off) that gates all generic protocol adapter execution in `agents/protocol.py`.

## Requirements

### Requirement 1: Universal applicability across provider types

**User Story:** As a platform engineer, I want one evaluation framework that applies to every agentic provider type the platform discovers, so that newly discovered agents become eval-able without bespoke per-capability code.

#### Acceptance Criteria

1. THE Eval_Framework SHALL accept discovery candidates of provider type `mcp_server`, `a2a_agent`, and `ai_agent` as evaluation inputs.
2. WHEN the Eval_Framework receives a candidate whose provider type has an executable invocation surface, THE Eval_Framework SHALL select an eval tier of at least `functional_smoke`.
3. IF a candidate's provider type has no invocation surface available, THEN THE Eval_Framework SHALL assign a verification-only result and SHALL set the result quality score to absent.
4. WHEN the Eval_Framework evaluates an `a2a_agent` or `ai_agent` candidate for which `agents/protocol.py` returns a structured refusal, THE Eval_Framework SHALL record the structured refusal as a verification-only result and SHALL set the result `source` to a synthetic value.
5. THE Eval_Framework SHALL record the evaluated provider type on every eval result.
6. WHERE a provider type gains an executable invocation surface in the future, THE Eval_Framework SHALL evaluate that provider type through the same tier ladder without requiring a parallel evaluation system.
7. IF a candidate is not an agentic provider type as defined in `discovery/constants.py` (`mcp_server`, `a2a_agent`, `ai_agent`), THEN THE Eval_Framework SHALL reject the candidate as an evaluation input and SHALL return a descriptive error.

### Requirement 2: Graceful degradation to verification-only

**User Story:** As a credibility-conscious operator, I want providers with no invocation surface to receive an honest verification-only result, so that the platform never publishes a fabricated quality score.

#### Acceptance Criteria

1. WHEN the Verification_Prober confirms that a provider exists and advertises a tool or skill, THE Eval_Framework SHALL record a verification-only result that marks the provider as verified for existence.
2. THE Eval_Framework SHALL represent a verification-only result with an absent quality score rather than a numeric quality score of zero, so that "not scored" is distinguishable from "scored zero".
3. IF the Eval_Framework cannot obtain a quality score for a capability, THEN THE Eval_Framework SHALL mark the capability as non-eval-able and SHALL record the reason for non-eval-ability.
4. THE Eval_Framework SHALL preserve the distinction between a `static_verification` result and a `scored_benchmark` result in every persisted record and report.
5. WHEN a verification-only result is persisted, THE Eval_Framework SHALL set the result `source` to a synthetic value so the Credibility_Classifier does not count the result as a real run.

### Requirement 3: Ground-truth resolution for arbitrary capabilities

**User Story:** As a platform engineer, I want the framework to obtain ground truth for capabilities that have no hand-authored YAML, so that the long tail of discovered capabilities can be scored instead of only the six capabilities that exist today.

#### Acceptance Criteria

1. WHEN hand-authored exact-match ground truth exists for a capability, THE Ground_Truth_Manager SHALL use the exact-match ground truth for scoring.
2. WHERE a capability produces soft or open-ended outputs, THE Ground_Truth_Manager SHALL provide a rubric-based ground truth evaluated by the Eval_Judge.
3. IF the Ground_Truth_Manager cannot resolve exact-match ground truth and cannot construct a rubric for a capability, THEN THE Eval_Framework SHALL mark the capability as non-eval-able and SHALL record the reason.
4. THE Ground_Truth_Manager SHALL record, for each scored eval result, which ground-truth source was used (exact-match or rubric-based).
5. THE Ground_Truth_Manager SHALL assign a version identifier to each ground-truth definition.
6. WHEN a ground-truth definition changes, THE Ground_Truth_Manager SHALL increment the ground-truth version identifier.

### Requirement 4: LLM-as-judge reliability and bias controls

**User Story:** As a credibility-conscious operator, I want the LLM-as-judge path to be reliability-checked and bias-controlled, so that judge-derived scores are trustworthy enough to publish.

#### Acceptance Criteria

1. WHEN the Eval_Judge scores a response against a rubric, THE Eval_Judge SHALL return a numeric quality score between 0.0 and 1.0 inclusive and a textual justification.
2. THE Eval_Judge SHALL evaluate each response against an explicit rubric rather than against an unstated preference.
3. WHILE scoring a single response, THE Eval_Judge SHALL exclude the provider identity from the material presented to the judge model so that provider identity does not bias the score.
4. WHEN the Eval_Judge produces a quality score, THE Eval_Framework SHALL record the judge model identifier and the rubric version on the eval result.
5. IF the Eval_Judge is unavailable for a capability that requires rubric-based scoring, THEN THE Eval_Framework SHALL mark the affected eval cell as non-eval-able for that run and SHALL record the judge-unavailable reason.
6. WHERE a capability is evaluated by the Eval_Judge, THE Eval_Framework SHALL set the eval result `source` to a value distinct from exact-match scoring so the judge-derived basis is auditable.
7. THE Eval_Framework SHALL classify a capability as non-eval-able when its outputs are neither exact-match scorable nor rubric-scorable.

### Requirement 5: Side-effect safety classification

**User Story:** As a safety owner, I want destructive capabilities to be classified before any live evaluation, so that an eval run never sends an email, moves money, or mutates external state without an explicit safety decision.

#### Acceptance Criteria

1. THE Safety_Classifier SHALL assign a safety class of `read_only`, `side_effecting`, or `unclassified` to every capability before evaluation.
2. IF a capability has a safety class of `unclassified`, THEN THE Eval_Framework SHALL refuse any `live` or `sandbox` run for that capability and SHALL permit only a `dry_run`.
3. IF a capability has a safety class of `side_effecting` and lacks an explicit safety approval record, THEN THE Eval_Framework SHALL refuse any `live` run for that capability.
4. WHEN the Eval_Framework evaluates a `side_effecting` capability, THE Eval_Framework SHALL use a synthetic test fixture (for example, an `@resend.dev` recipient address) rather than a real destination.
5. THE Eval_Framework SHALL provide a `dry_run` mode that performs no live provider call.
6. THE Eval_Framework SHALL provide a `sandbox` mode that directs side-effecting calls to synthetic or test fixture endpoints only.
7. WHEN the Eval_Framework issues a provider call during an eval run, THE Eval_Framework SHALL include an idempotency key on the request.
8. THE Eval_Framework SHALL record the run mode and the capability safety class on every eval result.

### Requirement 6: Tiered evaluation ladder

**User Story:** As a platform engineer, I want a cheapest-first evaluation ladder mapped onto existing concepts, so that the framework spends expensive scored runs only on providers that pass cheaper checks.

#### Acceptance Criteria

1. THE Eval_Framework SHALL define the ordered eval tiers `static_verification`, `functional_smoke`, `scored_benchmark`, and `continuous_reeval`.
2. WHEN the Eval_Framework evaluates a candidate, THE Eval_Framework SHALL attempt tiers in cheapest-first order beginning with `static_verification`.
3. IF a candidate fails the `static_verification` tier, THEN THE Eval_Framework SHALL stop before the `functional_smoke` tier and SHALL record the failed tier.
4. THE `static_verification` tier SHALL reference the existing `verification_status` ladder and SHALL set the verification outcome using the canonical statuses defined in `discovery/normalizer.py`.
5. WHEN a candidate passes the `functional_smoke` tier and the capability is eval-able, THE Eval_Framework SHALL proceed to the `scored_benchmark` tier.
6. THE `continuous_reeval` tier SHALL re-run a previously scored eval cell on a freshness schedule consistent with the `max_run_age_days` threshold used by the Credibility_Classifier.
7. THE Eval_Framework SHALL record the highest eval tier reached on every eval result.

### Requirement 7: Honesty and anti-overclaim invariants

**User Story:** As the platform owner bound by the honesty hard rules, I want the framework to never let a surface claim more than the eval data warrants, so that the platform stays compliant with AGENTS.md Hard Rules #1 and #3.

#### Acceptance Criteria

1. THE Eval_Framework SHALL label every synthetic run with a `source` value the Credibility_Classifier treats as non-real.
2. THE Eval_Framework SHALL persist eval results in a form the Credibility_Classifier can grade without modification to the classifier's thresholds.
3. THE Eval_Framework SHALL NOT gate the planner on eval results; the planner SHALL continue to refuse or route independent of eval outcomes.
4. WHEN the Eval_Framework lacks a benchmark for a capability, THE Eval_Framework SHALL allow the planner to continue treating the capability as existing.
5. IF an eval cell has only synthetic runs, THEN THE Eval_Framework SHALL ensure the persisted result causes the Credibility_Classifier to report `synthetic_only` for that cell.
6. THE Eval_Framework SHALL NOT assign a `scored_benchmark` quality score to a non-eval-able capability.
7. THE Eval_Framework SHALL record a real run only when the eval result is derived from a live or sandbox provider invocation against resolved ground truth.

### Requirement 8: Persistence and integration with existing stores

**User Story:** As a platform engineer, I want eval results to flow into the existing benchmark stores, so that the leaderboard surfaces and the credibility report consume eval data through the paths that already exist.

#### Acceptance Criteria

1. WHEN the Eval_Framework completes a scored eval run, THE Eval_Framework SHALL persist the run as a `BenchmarkRun` record in the `benchmark_runs` store.
2. WHEN the Eval_Framework aggregates runs for an eval cell, THE Eval_Framework SHALL persist an `AgentRanking` record in the `agent_rankings` store keyed by `(provider_id, capability)`.
3. THE Eval_Framework SHALL set the `source` field on each persisted ranking so the Credibility_Classifier can distinguish real runs from synthetic runs.
4. THE Eval_Framework SHALL operate through the existing `benchmark/scheduler.py` rather than introducing a parallel scheduler.
5. WHEN the Protocol execution gate is enabled and a discovered MCP candidate is selected, THE Eval_Scheduler SHALL evaluate the candidate instead of skipping it as `protocol_beta`.
6. THE Eval_Framework SHALL keep benchmark baselines in `benchmark/baselines/` out of the routing path and SHALL NOT introduce any routing dependency on baseline adapters.
7. THE Eval_Framework SHALL read agentic candidates through the `RoutingDiscoveryStore` facade and SHALL NOT read agentic candidates with a provider-type filter that mixes `apis_without_agents` rows.

### Requirement 9: Capability-agnostic test-case model

**User Story:** As a platform engineer, I want benchmark cases to be definable and auto-generatable for a newly discovered capability, so that evaluation is not limited to capabilities with hand-authored YAML.

#### Acceptance Criteria

1. WHEN a capability has no hand-authored benchmark cases, THE Case_Generator SHALL produce candidate benchmark cases for that capability.
2. THE Case_Generator SHALL produce each case in a structure compatible with the existing `TestCase` model (`id`, `capability`, `difficulty`, `inputs`, `expected`).
3. WHERE a generated case has not been curated, THE Eval_Framework SHALL mark the case as uncurated and SHALL exclude the uncurated case from any published `scored_benchmark` result.
4. THE Case_Generator SHALL assign a version identifier to each generated case set.
5. WHEN a curated case set changes, THE Eval_Framework SHALL increment the case set version identifier.
6. THE Eval_Framework SHALL record the case set version used on every scored eval result.

### Requirement 10: Determinism, reproducibility, and provenance

**User Story:** As an auditor, I want every published score to be reproducible and to carry provenance, so that any number on a surface can be traced to the cases, fixtures, agent version, and time that produced it.

#### Acceptance Criteria

1. WHEN the Eval_Framework persists a scored eval result, THE Provenance_Recorder SHALL attach the case set version, the ground-truth version, the run mode, the evaluated agent version, the eval tier, and the run timestamp.
2. THE Provenance_Recorder SHALL record the fixture identifiers used for any side-effecting capability evaluation.
3. WHEN two eval runs use the same case set version, the same ground-truth version, the same agent version, and the same fixtures, THE Eval_Framework SHALL produce the same exact-match scores for deterministic capabilities.
4. WHERE a capability is scored by the Eval_Judge, THE Provenance_Recorder SHALL record the judge model identifier and the rubric version so the score basis is auditable even though judge output may vary.
5. THE Eval_Framework SHALL record the evaluated agent version on every eval result so a score is attributable to a specific provider version.

### Requirement 11: Cost, latency, and rate-limit budgets

**User Story:** As an operator, I want enforced cost and latency budgets and rate limiting for an eval run, so that evaluating discovered agents cannot drain the budget or overload third-party endpoints.

#### Acceptance Criteria

1. THE Eval_Framework SHALL enforce a per-eval-run cost cap using the existing cost cap policy in `cost/cost_cap.py`.
2. IF a projected eval call would exceed the configured per-eval-run cost cap, THEN THE Eval_Framework SHALL refuse the call and SHALL record a structured cost-cap refusal.
3. THE Eval_Framework SHALL enforce a per-call latency budget and SHALL record a timeout result when a provider call exceeds the budget.
4. WHILE evaluating a third-party agent, THE Eval_Framework SHALL apply a rate limit to outbound calls to that agent.
5. WHEN an eval run begins, THE Eval_Framework SHALL record the configured cost cap and latency budget on the run provenance.

### Requirement 12: Failure isolation across a batch

**User Story:** As an operator, I want one failing agent to never crash an eval batch, so that a scheduled run records and continues exactly as the existing scheduler does.

#### Acceptance Criteria

1. WHEN one candidate raises an error during evaluation, THE Eval_Scheduler SHALL record the error for that candidate and SHALL continue evaluating the remaining candidates.
2. IF a provider call fails or times out, THEN THE Eval_Framework SHALL convert the failure into a structured eval result rather than raising an unhandled exception.
3. WHEN an eval batch completes, THE Eval_Scheduler SHALL produce a per-candidate summary that includes the outcome status for every selected candidate.
4. THE Eval_Scheduler SHALL include candidates with a status of `error` in the batch summary without discarding successful results from other candidates.

### Requirement 13: Credential and security handling

**User Story:** As a security owner, I want the framework to never persist credentials and to use bring-your-own credentials only, so that evaluating real agents does not leak secrets.

#### Acceptance Criteria

1. THE Eval_Framework SHALL resolve provider credentials from operator-supplied environment variables or a per-request credential input only.
2. THE Eval_Framework SHALL NOT persist credential values to any store, log, ledger, error message, or idempotency key.
3. WHEN the Eval_Framework records a cost or spend event for an eval call, THE Eval_Framework SHALL store the provider identifier and capability and SHALL NOT store the credential value.
4. WHERE bring-your-own credential execution is required, THE Eval_Framework SHALL route the call through the sandbox runner contract defined in `agents/sandbox_runner.py`.
5. IF the sandbox runner is not yet wired, THEN THE Eval_Framework SHALL refuse bring-your-own credential live execution and SHALL record the sandbox-unavailable reason.

### Requirement 14: Protocol execution gating

**User Story:** As a safety owner, I want all generic protocol execution to remain gated, so that an accidental deploy cannot trigger live third-party calls.

#### Acceptance Criteria

1. WHILE the Protocol execution gate is disabled, THE Eval_Framework SHALL NOT issue live generic protocol adapter calls and SHALL record a gated result for any candidate that requires protocol execution.
2. WHEN the Protocol execution gate is enabled, THE Eval_Framework SHALL permit generic protocol adapter execution only for capabilities whose safety class permits the requested run mode.
3. THE Eval_Framework SHALL default the Protocol execution gate to disabled when the environment flag `PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION` is unset.
4. IF the Protocol execution gate is disabled, THEN THE Eval_Framework SHALL still allow `dry_run` and `static_verification` outcomes that require no live call.

### Requirement 15: Capability-descriptor-driven test generation

**User Story:** As a platform engineer, I want the framework to read a discovered agent's own capability descriptor and derive what to test from it, so that evaluation reflects what the agent actually advertises rather than only a hand-mapped capability slug.

#### Acceptance Criteria

1. WHEN a discovered candidate publishes a Capability_Descriptor, THE Descriptor_Reader SHALL parse it into a normalized list of invokable operations, each with an operation name, an input schema where available, and a human-readable description where available.
2. THE Descriptor_Reader SHALL support parsing an MCP `tools/list` schema, an A2A Agent Card `skills` array, and an OpenAPI operation set into the same normalized shape.
3. WHEN the Case_Generator builds cases for a capability, THE Case_Generator SHALL derive case inputs from the normalized operation input schema produced by the Descriptor_Reader.
4. IF a candidate publishes no Capability_Descriptor and none can be probed, THEN THE Eval_Framework SHALL mark the candidate non-eval-able for `scored_benchmark` and SHALL record the missing-descriptor reason.
5. THE Eval_Framework SHALL record which Capability_Descriptor source (for example, `mcp_tools_list`, `a2a_agent_card`, `openapi`) was read on every eval result derived from a descriptor.
6. WHERE a Capability_Descriptor advertises an operation that does not map to any known registry capability, THE Eval_Framework SHALL record the unmapped operation so the discovery layer can reconcile it, and SHALL NOT fabricate a capability for it.

### Requirement 16: Pluggable multi-protocol invocation

**User Story:** As a platform engineer, I want protocol invocation to be pluggable with an honest per-protocol maturity status, so that the framework covers MCP today and extends to A2A, ACP, and ANP as their wire formats stabilize without a redesign.

#### Acceptance Criteria

1. THE Eval_Framework SHALL resolve a candidate's Protocol_Invoker through the Protocol_Invoker_Registry rather than through protocol-specific branches in the coordinator.
2. THE Protocol_Invoker_Registry SHALL recognize the protocols MCP, A2A, ACP, ANP, and OpenAPI and SHALL assign each a protocol maturity status of `executable`, `refusal_only`, or `planned`.
3. WHEN a candidate's protocol has a maturity status of `executable`, THE Eval_Framework SHALL invoke the provider through the corresponding Protocol_Invoker.
4. IF a candidate's protocol has a maturity status of `refusal_only` or `planned`, THEN THE Eval_Framework SHALL record a verification-only result with a synthetic `source` and SHALL NOT fabricate a quality score.
5. THE Eval_Framework SHALL record the protocol and the protocol maturity status on every eval result.
6. WHERE a new protocol gains a stable invocation surface, THE Eval_Framework SHALL support it by registering a new Protocol_Invoker without modifying the coordinator, the scheduler, or the scoring path.
7. THE Eval_Framework SHALL default the A2A, ACP, and ANP protocols to a maturity status that is not `executable` until an explicit invoker implementation and its tests exist.

### Requirement 17: Standards-aligned scoring

**User Story:** As a credibility-conscious operator, I want scoring to follow the evaluation decomposition the field has converged on, so that scores are diagnostic and defensible against external methodology scrutiny.

#### Acceptance Criteria

1. WHEN the Eval_Framework scores an eval case that involves a tool or operation call, THE Eval_Framework SHALL produce per-step scores for the Tool-use decomposition steps: decide-to-call, select-operation, build-arguments, and integrate-result.
2. THE Eval_Framework SHALL record both an outcome result and a per-step breakdown for every scored eval case so a failure can be attributed to a specific step.
3. THE Eval_Framework SHALL retain the Trajectory for a scored eval case so the failing step is locatable.
4. WHERE an exact-match field comparison is sufficient for a capability, THE Eval_Framework SHALL preserve the existing exact-match scoring behavior and SHALL treat the decomposition breakdown as additive metadata rather than replacing the exact-match score.
5. THE Eval_Framework SHALL keep the composite quality score it persists to `agent_rankings` on the same 0.0-to-1.0 scale the Credibility_Classifier already consumes, so standards-aligned scoring does not require any change to the classifier thresholds.
6. THE Eval_Framework SHALL record the scoring-method identifier (for example, `exact_match`, `tool_use_decomposition`, or `rubric_judge`) on every scored eval result.
