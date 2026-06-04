"""Per-(provider, capability) ranking aggregates for the discovery leaderboard.

Rankings are computed from `BenchmarkRun` records and persisted in the benchmark
store. They are read by the category browser to show success rate, quality,
latency, cost, and a composite score per candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from planmyagents_api.benchmark.models import BenchmarkRun

# Composite weighting. Tuned for a leaderboard view:
# - reliability matters most (success rate)
# - then output quality
# - cost is a tiebreaker only (small influence so a free unreliable provider
#   does not outrank a slightly more expensive reliable one)
COMPOSITE_WEIGHTS = {
    "success_rate": 0.55,
    "quality": 0.35,
    "cost_efficiency": 0.10,
}

# Anchors for cost normalization. Anything above MAX_COST_USD scores 0 on the
# cost dimension; anything at or below MIN_COST_USD scores 1.
MIN_COST_USD = 0.0
MAX_COST_USD = 1.0


@dataclass(frozen=True)
class AgentRanking:
    """Aggregated benchmark performance for one (provider, capability) pair."""

    provider_id: str
    capability: str
    sample_size: int
    success_rate: float
    avg_quality_score: float
    p50_latency_ms: int
    p95_latency_ms: int
    avg_cost_usd: float
    composite_score: float
    last_run_at: str
    benchmark_status: str
    source: str = "synthetic"
    rank: int = 0
    weights: dict[str, float] = field(default_factory=lambda: dict(COMPOSITE_WEIGHTS))
    # Optional eval-framework provenance (case-set/ground-truth/agent
    # versions, protocol + maturity, scoring method, fixtures). Defaults to
    # an empty dict so every existing constructor, JSON round-trip, and test
    # stays valid and the credibility classifier (which ignores it) is
    # unaffected. Populated only by the Eval_Framework.
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "capability": self.capability,
            "sample_size": self.sample_size,
            "success_rate": round(self.success_rate, 4),
            "avg_quality_score": round(self.avg_quality_score, 4),
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "avg_cost_usd": round(self.avg_cost_usd, 6),
            "composite_score": round(self.composite_score, 4),
            "last_run_at": self.last_run_at,
            "benchmark_status": self.benchmark_status,
            "source": self.source,
            "rank": self.rank,
            "weights": self.weights,
            "provenance": dict(self.provenance),
        }


def compute_rankings(
    runs: list[BenchmarkRun],
    *,
    weights: dict[str, float] | None = None,
    source: str = "synthetic",
    provenance: dict[str, Any] | None = None,
) -> list[AgentRanking]:
    """Aggregate benchmark runs into per-(provider, capability) rankings.

    ``provenance`` (optional) is attached verbatim to every produced ranking.
    The Eval_Framework uses it to carry case-set / ground-truth / protocol
    metadata; default callers leave it ``None`` and get an empty dict.
    """

    if not runs:
        return []
    grouped: dict[tuple[str, str], list[BenchmarkRun]] = {}
    for run in runs:
        grouped.setdefault((run.provider_id, run.capability), []).append(run)

    rankings: list[AgentRanking] = []
    for (provider_id, capability), capability_runs in grouped.items():
        rankings.append(
            _ranking_from_runs(
                provider_id=provider_id,
                capability=capability,
                runs=capability_runs,
                weights=weights or COMPOSITE_WEIGHTS,
                source=source,
                provenance=provenance or {},
            )
        )
    rankings.sort(key=lambda item: (item.capability, -item.composite_score, item.provider_id))
    by_capability: dict[str, int] = {}
    ranked: list[AgentRanking] = []
    for ranking in rankings:
        by_capability[ranking.capability] = by_capability.get(ranking.capability, 0) + 1
        ranked.append(_with_rank(ranking, by_capability[ranking.capability]))
    return ranked


def benchmark_status_from_ranking(ranking: AgentRanking) -> str:
    """Map a ranking to the candidate.benchmark_status enum used for promotion."""

    if ranking.sample_size == 0:
        return "not_started"
    if ranking.success_rate >= 0.95 and ranking.avg_quality_score >= 0.8:
        return "passed"
    return "failed"


def _ranking_from_runs(
    *,
    provider_id: str,
    capability: str,
    runs: list[BenchmarkRun],
    weights: dict[str, float],
    source: str,
    provenance: dict[str, Any] | None = None,
) -> AgentRanking:
    sample_size = len(runs)
    successes = sum(1 for run in runs if run.score.succeeded)
    success_rate = successes / sample_size if sample_size else 0.0
    avg_quality = (
        sum(run.score.quality_score for run in runs) / sample_size if sample_size else 0.0
    )
    avg_cost = (
        sum(run.response.cost_usd for run in runs) / sample_size if sample_size else 0.0
    )
    latencies = sorted(run.response.latency_ms for run in runs)
    cost_efficiency = max(
        0.0,
        min(
            1.0,
            (MAX_COST_USD - avg_cost) / (MAX_COST_USD - MIN_COST_USD)
            if MAX_COST_USD > MIN_COST_USD
            else 0.0,
        ),
    )
    composite = (
        weights.get("success_rate", 0.0) * success_rate
        + weights.get("quality", 0.0) * avg_quality
        + weights.get("cost_efficiency", 0.0) * cost_efficiency
    )
    last_run = max((run.created_at for run in runs), default=datetime.now(UTC).isoformat())
    pre_status = AgentRanking(
        provider_id=provider_id,
        capability=capability,
        sample_size=sample_size,
        success_rate=success_rate,
        avg_quality_score=avg_quality,
        p50_latency_ms=_percentile(latencies, 0.5),
        p95_latency_ms=_percentile(latencies, 0.95),
        avg_cost_usd=avg_cost,
        composite_score=composite,
        last_run_at=last_run,
        benchmark_status="not_started",
        source=source,
        weights=dict(weights),
        provenance=dict(provenance or {}),
    )
    return AgentRanking(
        **{
            **pre_status.__dict__,
            "benchmark_status": benchmark_status_from_ranking(pre_status),
        }
    )


def _with_rank(ranking: AgentRanking, rank: int) -> AgentRanking:
    return AgentRanking(**{**ranking.__dict__, "rank": rank})


def _percentile(latencies: list[int], pct: float) -> int:
    if not latencies:
        return 0
    if len(latencies) == 1:
        return int(latencies[0])
    idx = max(0, min(len(latencies) - 1, round(pct * (len(latencies) - 1))))
    return int(latencies[idx])
