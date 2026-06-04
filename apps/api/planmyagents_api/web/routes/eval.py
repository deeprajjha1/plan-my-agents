"""Eval-methodology route (read-only).

Surfaces the Eval_Framework's *shape* so the public trust page can
explain how a leaderboard number is produced — and why most cells are
verification-only today. This is the first UI window onto the eval work
that previously lived entirely in CLI / cron jobs.

Honesty by construction
-----------------------
Every value returned here is read from code that already governs real
behaviour:

* the tier ladder from :class:`eval.models.EvalTier` (+ ``_TIER_ORDER``),
* the protocol-maturity registry from
  :func:`eval.protocols.default_registry`,
* the source taxonomy from
  :data:`eval.models.REAL_EVAL_SOURCES` /
  :data:`eval.models.NON_REAL_EVAL_SOURCES`,
* the live credibility distribution from the same leaderboard index the
  ``/leaderboards`` page renders.

So the page cannot advertise a tier, protocol, or source the framework
does not actually implement, and the credibility counts move with the
real index rather than a hand-maintained slide. There is no new store
and no new persisted state — this endpoint is a pure projection.
"""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter

from planmyagents_api._config import benchmark_store_url, discovery_store_url
from planmyagents_api.benchmark.store import benchmark_store_for_path
from planmyagents_api.discovery.store import discovery_store_for_path
from planmyagents_api.eval.decomposition import (
    SCORING_METHOD_DECOMPOSITION,
    SCORING_METHOD_EXACT,
    SCORING_METHOD_RUBRIC,
)
from planmyagents_api.eval.models import (
    NON_REAL_EVAL_SOURCES,
    REAL_EVAL_SOURCES,
    EvalTier,
    tier_rank,
)
from planmyagents_api.eval.protocols import default_registry
from planmyagents_api.web.models import (
    CredibilityDistributionEntry,
    EvalMethodologyResponse,
    EvalProtocolModel,
    EvalSourceModel,
    EvalTierModel,
)

router = APIRouter()

# Tier → public copy. The credibility-band mapping mirrors the docstring on
# ``EvalTier``; keep the two in sync if a tier is ever re-banded.
_TIER_COPY: dict[str, tuple[str, str, str]] = {
    EvalTier.STATIC_VERIFICATION.value: (
        "Static verification",
        "Cheapest rung. We confirm the agent exists, its descriptor/card is "
        "reachable, and its claimed identity checks out — but we do not invoke "
        "any capability. A failed check stops the ladder here.",
        "synthetic_only",
    ),
    EvalTier.FUNCTIONAL_SMOKE.value: (
        "Functional smoke",
        "A real invocation ran against resolved ground truth, but the sample "
        "is too small to compare vendors. Proves the capability runs; not yet "
        "a benchmark.",
        "smoke_test",
    ),
    EvalTier.SCORED_BENCHMARK.value: (
        "Scored benchmark",
        "Enough real runs against curated cases to score the capability and "
        "rank vendors against each other. This is the rung that earns a "
        "publishable leaderboard.",
        "developing",
    ),
    EvalTier.CONTINUOUS_REEVAL.value: (
        "Continuous re-eval",
        "Keeps a scored cell fresh by re-running on a schedule so a stale "
        "number never masquerades as current.",
        "publishable",
    ),
}

_SOURCE_COPY: dict[str, str] = {
    "exact_match": (
        "Output compared field-by-field against deterministic ground truth. "
        "Counts as a real run."
    ),
    "judge": (
        "Open-ended output graded by an LLM judge against a versioned rubric. "
        "Counts as a real run."
    ),
    "verification_only": (
        "Existence/identity confirmed but no capability was invoked. Never "
        "carries a quality score."
    ),
    "dry_run": "Planned invocation that was not executed (no live provider call).",
    "gated": "Invocation withheld by the eval coordinator (gate off or safety).",
    "refused": "Structured refusal — e.g. a protocol with no executable surface yet.",
    "synthetic": "Scored against a mock adapter. Tests our scoring code, not the vendor.",
    "mock": "Mock adapter output. Not real-task performance.",
    "fixture": "Fixed test fixture. Not real-task performance.",
    "stub": "Stub adapter output. Not real-task performance.",
}

_PROTOCOL_NOTE_FALLBACK = (
    "Recognised protocol. See the per-invoker maturity for whether a live "
    "invocation is possible today."
)


@router.get(
    "/eval/methodology",
    response_model=EvalMethodologyResponse,
    tags=["eval"],
)
def eval_methodology() -> EvalMethodologyResponse:
    """Self-describing methodology + live credibility distribution.

    Backs the public ``/trust`` page. Cheap and deterministic apart from
    one read of the discovery + benchmark stores for the live credibility
    histogram (the same data ``/leaderboards`` already loads).
    """

    tiers = sorted(
        (
            EvalTierModel(
                id=tier.value,
                rank=tier_rank(tier),
                label=_TIER_COPY[tier.value][0],
                description=_TIER_COPY[tier.value][1],
                credibility_band=_TIER_COPY[tier.value][2],
            )
            for tier in EvalTier
        ),
        key=lambda model: model.rank,
    )

    registry = default_registry()
    protocols = [
        EvalProtocolModel(
            protocol=name,
            maturity=invoker.maturity.value,
            can_invoke=invoker.maturity.value == "executable",
            note=getattr(invoker, "reason", "") or _PROTOCOL_NOTE_FALLBACK,
        )
        for name, invoker in sorted(registry.invokers.items())
    ]

    all_sources = sorted(REAL_EVAL_SOURCES | NON_REAL_EVAL_SOURCES | {"synthetic", "mock"})
    sources = [
        EvalSourceModel(
            source=src,
            is_real=src in REAL_EVAL_SOURCES,
            description=_SOURCE_COPY.get(src, "Framework-produced ranking source."),
        )
        for src in all_sources
    ]

    distribution, total_caps, real_caps = _live_credibility_distribution()

    return EvalMethodologyResponse(
        tiers=tiers,
        protocols=protocols,
        sources=sources,
        scoring_methods=[
            SCORING_METHOD_EXACT,
            SCORING_METHOD_DECOMPOSITION,
            SCORING_METHOD_RUBRIC,
        ],
        real_eval_sources=sorted(REAL_EVAL_SOURCES),
        credibility_distribution=distribution,
        total_capabilities=total_caps,
        real_run_capabilities=real_caps,
        spec_reference=".kiro/specs/agent-eval-framework/",
    )


def _live_credibility_distribution() -> tuple[
    list[CredibilityDistributionEntry], int, int
]:
    """Histogram the current per-capability credibility statuses.

    Reuses the exact same leaderboard-index builder the ``/leaderboards``
    page uses so the page can never disagree with the leaderboard about how
    many cells are publishable vs synthetic_only. Degrades to an empty
    distribution (rather than 500-ing) if the stores are unreachable — the
    methodology copy is still useful without the live histogram.
    """

    try:
        from planmyagents_api.web.app import _leaderboard_index_entries

        candidates = discovery_store_for_path(discovery_store_url()).load()
        rankings = list(
            benchmark_store_for_path(benchmark_store_url()).load_rankings()
        )
        entries = _leaderboard_index_entries(candidates=candidates, rankings=rankings)
    except Exception:  # noqa: BLE001 — histogram is auxiliary, never load-bearing
        return [], 0, 0

    counter: Counter[str] = Counter(entry.credibility.status for entry in entries)
    distribution = [
        CredibilityDistributionEntry(status=status, count=count)
        for status, count in sorted(counter.items())
    ]
    real_caps = sum(1 for entry in entries if entry.has_real_adapter_runs)
    return distribution, len(entries), real_caps
