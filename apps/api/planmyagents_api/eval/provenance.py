"""Provenance recording for eval results.

Builds an :class:`EvalProvenance` from a candidate plus the run context and
attaches it to a ``BenchmarkRun.raw_response["_eval"]`` block. Carries the
case-set / ground-truth / agent versions, protocol + maturity, descriptor
source, scoring method, budgets, and fixtures so any persisted score is
reproducible and auditable.

NEVER carries a credential value — fixture ids are destination identifiers,
not secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from planmyagents_api.benchmark.models import BenchmarkRun
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.eval.models import EvalProvenance, EvalRunMode, EvalTier


def resolve_agent_version(candidate: DiscoveryCandidate) -> str:
    """Best-effort provider version so a score attributes to a specific build.

    Looks at common version carriers in candidate metadata (MCP registry
    version, npm version, generic ``version``), defaulting to ``"unknown"``.
    """

    metadata = candidate.metadata or {}
    for key in ("version", "mcp_version", "registry_version", "npm_version", "latest_version"):
        value = metadata.get(key)
        if value:
            return str(value)
    return "unknown"


@dataclass
class ProvenanceRecorder:
    """Builds eval provenance and attaches it to benchmark runs."""

    def build(
        self,
        *,
        candidate: DiscoveryCandidate,
        eval_tier: EvalTier,
        run_mode: EvalRunMode,
        safety_class: str,
        protocol: str = "",
        protocol_maturity: str = "",
        descriptor_source: str = "",
        scoring_method: str = "",
        case_set_version: str = "",
        ground_truth_version: str = "",
        ground_truth_kind: str = "",
        judge_model_id: str = "",
        rubric_version: str = "",
        cost_cap_usd: float = 0.0,
        latency_budget_s: float = 0.0,
        fixture_ids: list[str] | None = None,
    ) -> EvalProvenance:
        return EvalProvenance(
            eval_tier=eval_tier.value,
            run_mode=run_mode.value,
            safety_class=safety_class,
            provider_type=candidate.provider_type,
            agent_version=resolve_agent_version(candidate),
            case_set_version=case_set_version,
            ground_truth_version=ground_truth_version,
            ground_truth_kind=ground_truth_kind,
            protocol=protocol,
            protocol_maturity=protocol_maturity,
            descriptor_source=descriptor_source,
            scoring_method=scoring_method,
            judge_model_id=judge_model_id,
            rubric_version=rubric_version,
            cost_cap_usd=cost_cap_usd,
            latency_budget_s=latency_budget_s,
            fixture_ids=list(fixture_ids or []),
            run_timestamp=datetime.now(UTC).isoformat(),
        )

    def attach(self, run: BenchmarkRun, provenance: EvalProvenance) -> BenchmarkRun:
        """Return a copy of ``run`` with provenance under ``raw_response._eval``.

        ``BenchmarkRun`` is frozen, so we rebuild it. The provenance lives
        inside ``response.raw_response`` because that is what the benchmark
        store persists verbatim (JSONB ``output``/``run_json``).
        """

        from dataclasses import replace

        raw = dict(run.response.raw_response or {})
        raw["_eval"] = provenance.to_json()
        new_response = replace(run.response, raw_response=raw)
        return replace(run, response=new_response)
