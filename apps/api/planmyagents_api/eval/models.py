"""Shared data models for the Eval_Framework.

This module is deliberately dependency-light (stdlib + benchmark/discovery
dataclasses only) so it can be imported anywhere — including by the
credibility classifier's deny-list and by tests — without pulling in network
or LLM code.

The ``source`` taxonomy here is the contract between the framework and the
credibility classifier (`benchmark/credibility.py::_is_real_run`). The
classifier counts a ranking as a *real run* only when its ``source`` is in
:data:`REAL_EVAL_SOURCES`. Everything the framework can emit that is NOT a
live/sandbox invocation against resolved ground truth lands in
:data:`NON_REAL_EVAL_SOURCES`, so honesty is enforced structurally rather
than by remembering to label things correctly at each call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from planmyagents_api.benchmark.models import BenchmarkRun
    from planmyagents_api.benchmark.rankings import AgentRanking


# --- source taxonomy --------------------------------------------------------
#
# These string constants are the single source of truth for "what does the
# credibility classifier treat as real?". The classifier imports
# NON_REAL_EVAL_SOURCES so the two never drift.

#: Sources derived from a real (live/sandbox) invocation against resolved
#: ground truth. Only these count toward a credible, publishable leaderboard.
REAL_EVAL_SOURCES: frozenset[str] = frozenset({"exact_match", "judge"})

#: Framework-produced sources the credibility classifier MUST treat as
#: non-real. Widening this set can only ever make fewer rankings count as
#: real (the monotonicity / no-overclaim property).
NON_REAL_EVAL_SOURCES: frozenset[str] = frozenset(
    {"verification_only", "dry_run", "gated", "refused"}
)


class EvalRunMode(StrEnum):
    """Execution mode for an eval run."""

    DRY_RUN = "dry_run"      # no live provider call is made
    SANDBOX = "sandbox"      # live call against a synthetic / test-fixture endpoint
    LIVE = "live"            # live call against the provider's production surface


class EvalTier(StrEnum):
    """The ordered, cheapest-first evaluation ladder.

    The tiers map one-to-one onto the credibility classifier's bands:
    ``static_verification`` -> ``synthetic_only``, ``functional_smoke`` ->
    ``smoke_test``, ``scored_benchmark`` -> ``developing``/``publishable``,
    ``continuous_reeval`` keeps a scored cell fresh.
    """

    STATIC_VERIFICATION = "static_verification"
    FUNCTIONAL_SMOKE = "functional_smoke"
    SCORED_BENCHMARK = "scored_benchmark"
    CONTINUOUS_REEVAL = "continuous_reeval"


# Tier ordering for "cheapest-first" comparisons. Lower == cheaper / earlier.
_TIER_ORDER: dict[str, int] = {
    EvalTier.STATIC_VERIFICATION.value: 0,
    EvalTier.FUNCTIONAL_SMOKE.value: 1,
    EvalTier.SCORED_BENCHMARK.value: 2,
    EvalTier.CONTINUOUS_REEVAL.value: 3,
}


def tier_rank(tier: EvalTier) -> int:
    """Return the cheapest-first ordinal for ``tier`` (lower == cheaper)."""

    return _TIER_ORDER[tier.value]


@dataclass(frozen=True)
class EvalProvenance:
    """Reproducibility metadata attached to every persisted eval result.

    Stored on ``BenchmarkRun.raw_response["_eval"]`` and mirrored onto the
    (new, optional) ``AgentRanking.provenance`` field. Every field is a
    plain JSON-serialisable scalar/list so it round-trips through both the
    JSON and Postgres benchmark stores.

    NOTE: this NEVER carries a credential value (R13). Fixture ids are
    destination identifiers (e.g. ``"resend.dev"``), not secrets.
    """

    eval_tier: str
    run_mode: str
    safety_class: str
    provider_type: str
    agent_version: str = "unknown"
    case_set_version: str = ""
    ground_truth_version: str = ""
    ground_truth_kind: str = ""          # exact_match | rubric | none
    protocol: str = ""                   # mcp | openapi | a2a | acp | anp
    protocol_maturity: str = ""          # executable | refusal_only | planned
    descriptor_source: str = ""          # mcp_tools_list | a2a_agent_card | openapi
    scoring_method: str = ""             # exact_match | tool_use_decomposition | rubric_judge
    judge_model_id: str = ""
    rubric_version: str = ""
    cost_cap_usd: float = 0.0
    latency_budget_s: float = 0.0
    fixture_ids: list[str] = field(default_factory=list)
    run_timestamp: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "eval_tier": self.eval_tier,
            "run_mode": self.run_mode,
            "safety_class": self.safety_class,
            "provider_type": self.provider_type,
            "agent_version": self.agent_version,
            "case_set_version": self.case_set_version,
            "ground_truth_version": self.ground_truth_version,
            "ground_truth_kind": self.ground_truth_kind,
            "protocol": self.protocol,
            "protocol_maturity": self.protocol_maturity,
            "descriptor_source": self.descriptor_source,
            "scoring_method": self.scoring_method,
            "judge_model_id": self.judge_model_id,
            "rubric_version": self.rubric_version,
            "cost_cap_usd": round(self.cost_cap_usd, 6),
            "latency_budget_s": self.latency_budget_s,
            "fixture_ids": list(self.fixture_ids),
            "run_timestamp": self.run_timestamp,
        }


@dataclass(frozen=True)
class EvalResult:
    """The outcome of evaluating one (provider_id, capability) eval cell.

    ``quality_score`` is ``None`` for any verification-only or non-eval-able
    outcome so "not scored" is distinguishable from "scored zero" (R2.2).
    A numeric ``quality_score`` implies a real invocation against resolved
    ground truth.
    """

    provider_id: str
    capability: str
    provider_type: str
    tier_reached: EvalTier
    run_mode: EvalRunMode
    safety_class: str
    source: str
    eval_able: bool
    quality_score: float | None = None
    sample_size: int = 0
    reason: str = ""
    provenance: EvalProvenance | None = None
    runs: list[BenchmarkRun] = field(default_factory=list)
    ranking: AgentRanking | None = None
    verification_record: dict[str, Any] | None = None
    error: str = ""

    def __post_init__(self) -> None:
        # Honesty Property 1: a verification-only / non-eval-able result must
        # never carry a numeric quality score. We enforce it at construction
        # so no caller can accidentally violate it.
        verification_only = (
            self.tier_reached == EvalTier.STATIC_VERIFICATION or not self.eval_able
        )
        if verification_only and self.quality_score is not None:
            raise ValueError(
                "verification-only / non-eval-able EvalResult must have "
                "quality_score=None (no fabricated scores)"
            )
        # Property 2/7.1: a numeric score may only ride on a real source.
        if self.quality_score is not None and self.source not in REAL_EVAL_SOURCES:
            raise ValueError(
                f"quality_score requires a real source {sorted(REAL_EVAL_SOURCES)}; "
                f"got source={self.source!r}"
            )

    @property
    def is_real_run(self) -> bool:
        return self.source in REAL_EVAL_SOURCES and self.sample_size > 0

    def to_json(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "capability": self.capability,
            "provider_type": self.provider_type,
            "tier_reached": self.tier_reached.value,
            "run_mode": self.run_mode.value,
            "safety_class": self.safety_class,
            "source": self.source,
            "eval_able": self.eval_able,
            "quality_score": self.quality_score,
            "sample_size": self.sample_size,
            "reason": self.reason,
            "is_real_run": self.is_real_run,
            "provenance": self.provenance.to_json() if self.provenance else None,
            "ranking": self.ranking.to_json() if self.ranking is not None else None,
            "verification_record": self.verification_record,
            "error": self.error,
        }
