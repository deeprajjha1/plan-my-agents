"""EvalFramework coordinator.

Single entry point per eval cell. Owns the cheapest-first tier ladder, the
degradation rules, and assembling an :class:`EvalResult`. Composes the
SafetyClassifier, VerificationProber, DescriptorReader, GroundTruthManager,
ProtocolInvokerRegistry (via InvocationResolver), BenchmarkRunner + scoring,
the EvalJudge, and the ProvenanceRecorder.

Contract: ``evaluate_cell`` ALWAYS returns an ``EvalResult`` — no exception
from a provider call, judge call, cost-cap refusal, or timeout escapes
(Property 8). Honesty Properties 1, 2, 4, 6, 11 are enforced by construction.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from planmyagents_api.benchmark.models import BenchmarkRun, ProviderRequest
from planmyagents_api.benchmark.rankings import AgentRanking, compute_rankings
from planmyagents_api.benchmark.scoring import score_response
from planmyagents_api.cost.cost_cap import CostCapPolicy
from planmyagents_api.discovery.constants import AGENTIC_PROVIDER_TYPES
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.eval.case_generator import filter_curated_for_scored
from planmyagents_api.eval.decomposition import (
    SCORING_METHOD_EXACT,
    SCORING_METHOD_RUBRIC,
    decompose,
)
from planmyagents_api.eval.descriptor import DescriptorReader, schema_for_capability
from planmyagents_api.eval.ground_truth import (
    GroundTruthKind,
    GroundTruthManager,
)
from planmyagents_api.eval.invocation import Executable, InvocationResolver, Refused, idempotency_key
from planmyagents_api.eval.judge import EvalJudge
from planmyagents_api.eval.models import (
    EvalResult,
    EvalRunMode,
    EvalTier,
)
from planmyagents_api.eval.protocols import ProtocolMaturity
from planmyagents_api.eval.provenance import ProvenanceRecorder
from planmyagents_api.eval.safety import SafetyClassifier
from planmyagents_api.eval.verification import VerificationProber

LOGGER = logging.getLogger(__name__)

# Below this real sample count a cell is treated as functional_smoke even if
# it scored; scored_benchmark needs the curated case set to be large enough.
# The credibility classifier still has the final say on the published band;
# this only governs which tier label the result carries.
_SCORED_MIN_SAMPLES = 30


@dataclass
class EvalFramework:
    safety: SafetyClassifier
    prober: VerificationProber
    descriptor_reader: DescriptorReader
    ground_truth: GroundTruthManager
    invocation: InvocationResolver
    provenance: ProvenanceRecorder
    benchmarks_dir: Path
    judge: EvalJudge | None = None
    cases_per_capability: int = 30
    _recorder_cls: object = field(default=None, repr=False)

    def evaluate_cell(
        self,
        *,
        candidate: DiscoveryCandidate,
        capability: str,
        run_mode: EvalRunMode = EvalRunMode.DRY_RUN,
        requires_byo_credentials: bool = False,
    ) -> EvalResult:
        """Evaluate one (candidate, capability) cell through the tier ladder."""

        try:
            return self._evaluate(
                candidate=candidate,
                capability=capability,
                run_mode=run_mode,
                requires_byo_credentials=requires_byo_credentials,
            )
        except Exception as exc:  # noqa: BLE001 — contract: always return a result
            LOGGER.warning(
                "eval: unexpected error for %s/%s: %s: %s",
                candidate.id,
                capability,
                type(exc).__name__,
                exc,
            )
            return self._verification_only(
                candidate=candidate,
                capability=capability,
                run_mode=EvalRunMode.DRY_RUN,
                source="refused",
                reason=f"eval_error:{type(exc).__name__}:{exc}",
                tier=EvalTier.STATIC_VERIFICATION,
                error=str(exc),
            )

    # -- internals ----------------------------------------------------------

    def _evaluate(
        self,
        *,
        candidate: DiscoveryCandidate,
        capability: str,
        run_mode: EvalRunMode,
        requires_byo_credentials: bool,
    ) -> EvalResult:
        # R1.7: reject non-agentic provider types.
        if candidate.provider_type not in AGENTIC_PROVIDER_TYPES:
            return EvalResult(
                provider_id=candidate.id,
                capability=capability,
                provider_type=candidate.provider_type,
                tier_reached=EvalTier.STATIC_VERIFICATION,
                run_mode=run_mode,
                safety_class=self.safety.classify(capability).value,
                source="refused",
                eval_able=False,
                reason=(
                    f"provider_type '{candidate.provider_type}' is not agentic; "
                    f"eval inputs must be one of {sorted(AGENTIC_PROVIDER_TYPES)}."
                ),
                error="non_agentic_provider_type",
            )

        safety_class = self.safety.classify(capability)
        protocol = self.invocation.registry.protocol_for_candidate(candidate)
        maturity = self.invocation.registry.maturity(protocol)

        # Tier 1: static_verification (cheapest). A failed verification stops
        # the ladder (R6.3).
        outcome = self.prober.probe(candidate)
        if not outcome.passed:
            return self._verification_only(
                candidate=candidate,
                capability=capability,
                run_mode=run_mode,
                source="verification_only",
                reason=(
                    f"static_verification did not pass "
                    f"(status={outcome.verification_status}); "
                    f"blockers={outcome.blockers}"
                ),
                tier=EvalTier.STATIC_VERIFICATION,
                verification_record=outcome.record,
                protocol=protocol,
                protocol_maturity=maturity.value,
            )

        # Non-executable protocol → verification-only (R16.4 / Property 11).
        if maturity != ProtocolMaturity.EXECUTABLE:
            return self._verification_only(
                candidate=candidate,
                capability=capability,
                run_mode=run_mode,
                source="verification_only",
                reason=(
                    f"protocol '{protocol}' maturity={maturity.value}; "
                    "verification-only until an executable invoker ships."
                ),
                tier=EvalTier.STATIC_VERIFICATION,
                verification_record=outcome.record,
                protocol=protocol,
                protocol_maturity=maturity.value,
            )

        # Read the agent's capability descriptor (drives case generation).
        descriptor = self.descriptor_reader.read(candidate)
        descriptor_source = descriptor.source_kind if descriptor else ""
        tool_schema = (
            schema_for_capability(descriptor, capability) if descriptor else None
        )

        # Resolve ground truth (exact-match / rubric / none).
        gt = self.ground_truth.resolve(capability, tool_schema=tool_schema)
        if gt.kind == GroundTruthKind.NONE:
            return self._verification_only(
                candidate=candidate,
                capability=capability,
                run_mode=run_mode,
                source="verification_only",
                reason=f"non_eval_able: {gt.reason_if_none}",
                tier=EvalTier.FUNCTIONAL_SMOKE,
                verification_record=outcome.record,
                protocol=protocol,
                protocol_maturity=maturity.value,
                descriptor_source=descriptor_source,
            )

        if gt.kind == GroundTruthKind.RUBRIC and self.judge is None:
            return self._verification_only(
                candidate=candidate,
                capability=capability,
                run_mode=run_mode,
                source="verification_only",
                reason="judge_unavailable: rubric ground truth requires a judge",
                tier=EvalTier.FUNCTIONAL_SMOKE,
                verification_record=outcome.record,
                protocol=protocol,
                protocol_maturity=maturity.value,
                descriptor_source=descriptor_source,
            )

        # Resolve invocation (gate + safety + budgets). Refusal → verification.
        decision = self.invocation.resolve(
            candidate=candidate,
            capability=capability,
            run_mode=run_mode,
            requires_byo_credentials=requires_byo_credentials,
        )
        if isinstance(decision, Refused):
            return self._verification_only(
                candidate=candidate,
                capability=capability,
                run_mode=run_mode,
                source=decision.source,           # "gated" | "refused"
                reason=decision.reason,
                tier=EvalTier.STATIC_VERIFICATION,
                verification_record=outcome.record,
                protocol=protocol,
                protocol_maturity=maturity.value,
                descriptor_source=descriptor_source,
            )

        assert isinstance(decision, Executable)

        # Run the curated cases against the live/sandbox adapter and score.
        scoring_method = (
            SCORING_METHOD_RUBRIC
            if gt.kind == GroundTruthKind.RUBRIC
            else SCORING_METHOD_EXACT
        )
        cases = list(gt.cases)
        runs = self._run_cases(
            adapter=decision.adapter,
            candidate=candidate,
            capability=capability,
            cases=cases,
        )
        if not runs:
            return self._verification_only(
                candidate=candidate,
                capability=capability,
                run_mode=run_mode,
                source="verification_only",
                reason="no benchmark runs produced (no cases or all skipped)",
                tier=EvalTier.FUNCTIONAL_SMOKE,
                verification_record=outcome.record,
                protocol=protocol,
                protocol_maturity=maturity.value,
                descriptor_source=descriptor_source,
            )

        sample_size = len(runs)
        ranking_source = "judge" if gt.kind == GroundTruthKind.RUBRIC else "exact_match"
        # Tier label: scored_benchmark only when the sample is large enough;
        # otherwise it's a functional_smoke real run.
        tier = (
            EvalTier.SCORED_BENCHMARK
            if sample_size >= _SCORED_MIN_SAMPLES
            else EvalTier.FUNCTIONAL_SMOKE
        )

        prov = self.provenance.build(
            candidate=candidate,
            eval_tier=tier,
            run_mode=run_mode,
            safety_class=safety_class.value,
            protocol=protocol,
            protocol_maturity=maturity.value,
            descriptor_source=descriptor_source,
            scoring_method=scoring_method,
            case_set_version=gt.version,
            ground_truth_version=gt.version,
            ground_truth_kind=gt.kind.value,
            judge_model_id=self._judge_model_id() if gt.kind == GroundTruthKind.RUBRIC else "",
            rubric_version=gt.rubric.resolved_version() if gt.rubric else "",
            cost_cap_usd=self.invocation.cost_cap.per_goal_limit_usd,
            latency_budget_s=self.invocation.latency_budget_s,
            fixture_ids=list((self.safety.fixture_for(capability) or {}).values()),
        )

        runs_with_prov = [self.provenance.attach(run, prov) for run in runs]
        rankings = compute_rankings(
            runs_with_prov, source=ranking_source, provenance=prov.to_json()
        )
        ranking: AgentRanking | None = rankings[0] if rankings else None
        quality = ranking.avg_quality_score if ranking else 0.0

        return EvalResult(
            provider_id=candidate.id,
            capability=capability,
            provider_type=candidate.provider_type,
            tier_reached=tier,
            run_mode=run_mode,
            safety_class=safety_class.value,
            source=ranking_source,
            eval_able=True,
            quality_score=quality,
            sample_size=sample_size,
            reason="scored",
            provenance=prov,
            runs=runs_with_prov,
            ranking=ranking,
            verification_record=outcome.record,
        )

    def _judge_model_id(self) -> str:
        """Best-effort judge model id for provenance.

        Reads ``judge.chat_client.primary_label`` when present, but never
        crashes if a caller injects a judge without that attribute (e.g. a
        test fake or a custom judge). Provenance is informational; a missing
        label must not fail the eval.
        """

        if self.judge is None:
            return ""
        client = getattr(self.judge, "chat_client", None)
        return str(getattr(client, "primary_label", "") or "")

    def _run_cases(
        self,
        *,
        adapter,
        candidate: DiscoveryCandidate,
        capability: str,
        cases: list,
    ) -> list[BenchmarkRun]:
        runs: list[BenchmarkRun] = []
        limited = cases[: self.cases_per_capability]
        for case in limited:
            request = ProviderRequest(
                capability=capability,
                inputs=dict(case.inputs),
                idempotency_key=idempotency_key(
                    provider_id=candidate.id, capability=capability, case_id=case.id
                ),
            )
            try:
                response = asyncio.run(adapter.execute(request))
            except Exception as exc:  # noqa: BLE001 — convert to a failed run
                from planmyagents_api.benchmark.models import ProviderResponse

                response = ProviderResponse(
                    succeeded=False,
                    output=None,
                    cost_usd=0.0,
                    latency_ms=0,
                    error=f"{type(exc).__name__}: {exc}",
                )
            score = score_response(case, response, judge=self.judge)
            run = BenchmarkRun(
                test_case_id=case.id,
                provider_id=candidate.id,
                capability=capability,
                difficulty=case.difficulty,
                response=response,
                score=score,
            )
            # Attach the decomposition breakdown as additive metadata.
            decomposition = decompose(
                test_case=case,
                response=response,
                score=score,
                scoring_method=(
                    SCORING_METHOD_RUBRIC
                    if case.expected.get("rubric_version")
                    else SCORING_METHOD_EXACT
                ),
            )
            from dataclasses import replace

            raw = dict(run.response.raw_response or {})
            raw["_decomposition"] = decomposition.to_json()
            run = replace(run, response=replace(run.response, raw_response=raw))
            runs.append(run)
        return runs

    def _verification_only(
        self,
        *,
        candidate: DiscoveryCandidate,
        capability: str,
        run_mode: EvalRunMode,
        source: str,
        reason: str,
        tier: EvalTier,
        verification_record: dict | None = None,
        protocol: str = "",
        protocol_maturity: str = "",
        descriptor_source: str = "",
        error: str = "",
    ) -> EvalResult:
        safety_class = self.safety.classify(capability)
        prov = self.provenance.build(
            candidate=candidate,
            eval_tier=tier,
            run_mode=run_mode,
            safety_class=safety_class.value,
            protocol=protocol,
            protocol_maturity=protocol_maturity,
            descriptor_source=descriptor_source,
        )
        return EvalResult(
            provider_id=candidate.id,
            capability=capability,
            provider_type=candidate.provider_type,
            tier_reached=tier,
            run_mode=run_mode,
            safety_class=safety_class.value,
            source=source,
            eval_able=False,
            quality_score=None,
            sample_size=0,
            reason=reason,
            provenance=prov,
            verification_record=verification_record,
            error=error,
        )


def build_default_framework(
    *,
    benchmarks_dir: Path,
    cost_cap: CostCapPolicy | None = None,
    judge: EvalJudge | None = None,
    enable_live_probe: bool = False,
) -> EvalFramework:
    """Wire a framework from defaults. Used by the scheduler + CLI."""

    safety = SafetyClassifier()
    cap = cost_cap or CostCapPolicy(enabled=True, ledger=None)
    return EvalFramework(
        safety=safety,
        prober=VerificationProber(enable_live_probe=enable_live_probe),
        descriptor_reader=DescriptorReader(),
        ground_truth=GroundTruthManager(benchmarks_dir=benchmarks_dir),
        invocation=InvocationResolver(cost_cap=cap, safety=safety),
        provenance=ProvenanceRecorder(),
        benchmarks_dir=benchmarks_dir,
        judge=judge,
    )


# Keep the unused-import guard honest: filter_curated_for_scored is part of the
# public scored-isolation contract and re-exported for the scheduler.
__all__ = ["EvalFramework", "build_default_framework", "filter_curated_for_scored"]
