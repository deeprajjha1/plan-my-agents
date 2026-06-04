# Requirements Document

## Introduction

The Eval_Framework can score open-ended, judgment-heavy capabilities (e.g. "analyse seller return policies") using an LLM-as-judge against a rubric — that path is built and tested (`eval/judge.py`, the rubric ground-truth path, the `return_policy_analysis` worked example). But a judge-derived score is only as trustworthy as the judge. An LLM judge can be inconsistent (same input, different score across runs), biased (favoring verbose or familiar-sounding output), or systematically miscalibrated (consistently too lenient or too harsh versus a human).

Today nothing validates the judge. The honesty audit (`docs/honest-scope-audit.md`) calls this out: a judge-scored leaderboard could read as authoritative while resting on an unvalidated scorer. This is the credibility gap that makes "we benchmark agents with an LLM judge" land as a claim rather than a proof.

The Judge_Calibration feature closes that gap. It introduces a **calibration gate**: before any capability's leaderboard may be promoted to the `publishable` credibility band on the strength of judge-derived scores, the judge must demonstrate sufficient agreement with a human-labeled reference set, and sufficient self-consistency. A capability whose judge fails calibration is capped below `publishable` (it may still be `developing`/`smoke_test`), and the reason is recorded.

This feature reuses existing components — the `EvalJudge`, the `Rubric` + rubric-shaped reference cases, the credibility classifier, and the benchmark stores — and adds a calibration store + a calibration gate consulted by the credibility classifier for judge-scored capabilities. It must not weaken the existing exact-match path (which needs no judge calibration) and must not let any surface present a judge-scored capability as `publishable` until its judge is calibrated.

## Glossary

- **Judge_Calibration**: THE system specified by this document. It measures and gates the reliability of the Eval_Judge per capability.
- **Eval_Judge**: THE existing LLM-as-judge in `eval/judge.py` that scores a response against a rubric.
- **Calibration_Set**: A set of (case input, agent output, human score) triples for a capability — the human-labeled reference against which the judge is measured. Each item carries a human score on the same 0.0–1.0 scale the judge uses.
- **Human_Label**: A score assigned by a human reviewer to a specific (case, output) pair, on the rubric's 0.0–1.0 scale.
- **Agreement_Metric**: A quantitative measure of how closely the judge's scores match the human scores across the Calibration_Set (e.g. mean absolute error and a correlation/agreement coefficient).
- **Consistency_Metric**: A measure of the judge's self-agreement when scoring the same item multiple times (e.g. score variance across repeated runs).
- **Calibration_Verdict**: THE per-capability outcome: `calibrated`, `uncalibrated`, or `not_assessed`, with the metrics and thresholds that produced it.
- **Calibration_Gate**: The rule the Credibility_Classifier consults for judge-scored capabilities: a capability may be `publishable` only if its Calibration_Verdict is `calibrated`.
- **Credibility_Classifier**: THE existing classifier in `benchmark/credibility.py`. It is extended to consult the Calibration_Gate for judge-scored capabilities; its existing thresholds for exact-match capabilities are unchanged.
- **Judge-scored capability**: A capability whose persisted rankings carry `source == "judge"`.
- **Calibration_Store**: Persistence for Calibration_Sets and Calibration_Verdicts, mirroring the existing store patterns (JSON / Postgres factory).

## Requirements

### Requirement 1: Human-labeled calibration set

**User Story:** As a credibility owner, I want a per-capability human-labeled reference set, so that the judge can be measured against human judgment rather than trusted blindly.

#### Acceptance Criteria

1. THE Judge_Calibration SHALL represent a Calibration_Set as a list of items, each with a case identifier, the case input, the agent output, and a Human_Label on the 0.0–1.0 scale.
2. THE Judge_Calibration SHALL persist Calibration_Sets through a Calibration_Store that mirrors the existing JSON / Postgres store factory pattern.
3. THE Judge_Calibration SHALL record, for each Calibration_Set, the capability, the rubric version it was labeled against, and a content-hash version of the set.
4. WHEN a Calibration_Set's items change, THE Judge_Calibration SHALL change the set's version identifier.
5. IF a capability has no Calibration_Set, THEN THE Judge_Calibration SHALL report its Calibration_Verdict as `not_assessed`.

### Requirement 2: Agreement and consistency measurement

**User Story:** As a credibility owner, I want the judge measured for both agreement with humans and self-consistency, so that a calibrated verdict reflects accuracy and stability.

#### Acceptance Criteria

1. WHEN Judge_Calibration assesses a capability, THE Judge_Calibration SHALL run the Eval_Judge over every item in the Calibration_Set and compute an Agreement_Metric against the Human_Labels.
2. THE Judge_Calibration SHALL compute the Agreement_Metric as at least a mean absolute error between judge scores and Human_Labels on the 0.0–1.0 scale.
3. THE Judge_Calibration SHALL compute a Consistency_Metric by scoring at least one Calibration_Set item multiple times and measuring score dispersion.
4. THE Judge_Calibration SHALL record the judge model identifier and rubric version used during assessment.
5. IF the Eval_Judge is unavailable during assessment, THEN THE Judge_Calibration SHALL report `not_assessed` and SHALL record the judge-unavailable reason rather than reporting `calibrated`.
6. THE Judge_Calibration SHALL NOT present the Human_Labels to the Eval_Judge during assessment, so the judge cannot trivially echo them.

### Requirement 3: Calibration verdict and thresholds

**User Story:** As a credibility owner, I want a conservative, configurable calibration verdict, so that only judges that demonstrably agree with humans are trusted for publication.

#### Acceptance Criteria

1. THE Judge_Calibration SHALL produce a Calibration_Verdict of `calibrated`, `uncalibrated`, or `not_assessed` per capability.
2. THE Judge_Calibration SHALL report `calibrated` only when the Agreement_Metric meets the configured agreement threshold AND the Consistency_Metric meets the configured consistency threshold AND the Calibration_Set size meets a configured minimum.
3. THE Judge_Calibration SHALL default the thresholds to conservative values and SHALL allow overrides via environment variables, mirroring the credibility classifier's threshold pattern.
4. THE Judge_Calibration SHALL record, on every Calibration_Verdict, the metric values and the thresholds in effect, so a verdict is auditable.
5. WHEN a verdict is not `calibrated`, THE Judge_Calibration SHALL record a human-readable reason and the unblockers required to reach `calibrated`.
6. THE Judge_Calibration SHALL assign a version-stamped timestamp to each Calibration_Verdict so staleness can be reasoned about.

### Requirement 4: Calibration gate in the credibility classifier

**User Story:** As the platform owner bound by the honesty hard rules, I want judge-scored leaderboards gated on calibration, so that an uncalibrated judge can never produce a `publishable` claim.

#### Acceptance Criteria

1. WHEN the Credibility_Classifier classifies a judge-scored capability, THE Credibility_Classifier SHALL consult the Calibration_Verdict for that capability.
2. IF a judge-scored capability's Calibration_Verdict is not `calibrated`, THEN THE Credibility_Classifier SHALL cap that capability's band below `publishable`.
3. THE Calibration_Gate SHALL apply only to judge-scored capabilities and SHALL NOT change the classification of exact-match-scored capabilities.
4. THE Judge_Calibration SHALL NOT modify the Credibility_Classifier's existing numeric thresholds (real-provider count, sample size, run age).
5. WHEN a judge-scored capability is capped by the Calibration_Gate, THE Credibility_Classifier SHALL record the calibration reason in the verdict's reasons.
6. WHERE a capability mixes judge-scored and exact-match-scored providers, THE Calibration_Gate SHALL apply to the judge-scored basis only and SHALL NOT penalize the exact-match basis.

### Requirement 5: Reporting and honesty integration

**User Story:** As an operator, I want calibration status surfaced in the credibility report, so that the trust state of every judge-scored capability is visible.

#### Acceptance Criteria

1. THE Judge_Calibration SHALL expose each capability's Calibration_Verdict in a form the existing credibility report can include.
2. WHEN the credibility report is generated, THE report SHALL show the Calibration_Verdict for every judge-scored capability.
3. THE Judge_Calibration SHALL NOT allow a surface to present a judge-scored capability as `publishable` while its Calibration_Verdict is not `calibrated`.
4. THE Judge_Calibration SHALL keep the exact-match credibility path unchanged in the report.

### Requirement 6: Determinism, isolation, and safety

**User Story:** As a platform engineer, I want calibration assessment to be reproducible and isolated, so that it can run in CI and a single failure cannot corrupt the gate.

#### Acceptance Criteria

1. WHEN calibration is assessed with an injected deterministic judge, THE Judge_Calibration SHALL produce a deterministic Calibration_Verdict.
2. IF assessing one capability raises an error, THEN THE Judge_Calibration SHALL record that capability as `not_assessed` and SHALL continue assessing the remaining capabilities.
3. THE Judge_Calibration SHALL run assessment without making any live provider call (it scores already-captured agent outputs in the Calibration_Set, not live invocations).
4. THE Judge_Calibration SHALL NOT persist any credential value.
5. THE Judge_Calibration SHALL reuse the existing `EvalJudge`, `Rubric`, credibility classifier, and store-factory patterns, and SHALL NOT reimplement judging, rubric handling, or credibility banding.
