"""Per-capability leaderboard credibility classifier.

This module answers a single question that runs through the entire
trust-layer roadmap: **is the leaderboard for capability `X` good enough that
we are willing to put it in front of a buyer?**

We do not gate the *product* on this answer (the planner keeps handling any
goal, the discovery store keeps growing, refusal flows keep working). We gate
the *credibility surface* on it. A leaderboard the public sees should not
pretend to be more than it is.

Verdict ladder, ordered by strength:

* ``synthetic_only`` — every ranked provider was scored against a mock
  adapter. The page is a smoke test of our own scoring code, not a vendor
  comparison.
* ``smoke_test`` — at least one ranking has ``source != "synthetic"`` but the
  bar for cross-vendor comparison is not met (too few providers, too few
  samples, or stale runs).
* ``developing`` — meets the minimum bar for cross-vendor comparison but is
  missing one or more publishability requirements (e.g. holdout eval not run,
  per-difficulty breakdown not populated, last real run is borderline-stale).
* ``publishable`` — defensible to publish externally. We are willing to put
  the page on Hacker News and stand behind every number on it.

Thresholds are intentionally conservative; tightening them later only marks
fewer capabilities as ``publishable`` and is reversible. Lowering them would
silently weaken the entire trust pitch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from planmyagents_api.discovery.models import DiscoveryCandidate

CredibilityStatus = str  # one of: synthetic_only | smoke_test | developing | publishable

DEFAULT_MIN_REAL_PROVIDERS = 3
DEFAULT_MIN_SAMPLE_SIZE = 30
DEFAULT_MAX_RUN_AGE_DAYS = 30


@dataclass(frozen=True)
class CredibilityThresholds:
    """Knobs for the classifier.

    Defaults match the published roadmap. Override via env (see
    :func:`thresholds_from_env`) so we can tighten globally without code edits.
    """

    min_real_providers: int = DEFAULT_MIN_REAL_PROVIDERS
    min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE
    max_run_age_days: int = DEFAULT_MAX_RUN_AGE_DAYS


def thresholds_from_env() -> CredibilityThresholds:
    return CredibilityThresholds(
        min_real_providers=_int_env(
            "PLANMYAGENTS_CREDIBILITY_MIN_REAL_PROVIDERS",
            DEFAULT_MIN_REAL_PROVIDERS,
        ),
        min_sample_size=_int_env(
            "PLANMYAGENTS_CREDIBILITY_MIN_SAMPLE_SIZE",
            DEFAULT_MIN_SAMPLE_SIZE,
        ),
        max_run_age_days=_int_env(
            "PLANMYAGENTS_CREDIBILITY_MAX_RUN_AGE_DAYS",
            DEFAULT_MAX_RUN_AGE_DAYS,
        ),
    )


@dataclass(frozen=True)
class CredibilityVerdict:
    """Output of the classifier for one capability."""

    capability: str
    status: CredibilityStatus
    real_provider_count: int
    total_provider_count: int
    max_real_sample_size: int
    last_real_run_at: str
    reasons: list[str] = field(default_factory=list)
    unblockers: list[str] = field(default_factory=list)

    @property
    def is_publishable(self) -> bool:
        return self.status == "publishable"

    @property
    def is_smoke_test(self) -> bool:
        return self.status in {"synthetic_only", "smoke_test"}

    def to_json(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "status": self.status,
            "real_provider_count": self.real_provider_count,
            "total_provider_count": self.total_provider_count,
            "max_real_sample_size": self.max_real_sample_size,
            "last_real_run_at": self.last_real_run_at,
            "reasons": list(self.reasons),
            "unblockers": list(self.unblockers),
        }


def classify(
    *,
    capability: str,
    candidates: list[DiscoveryCandidate],
    rankings: list[dict[str, Any]],
    thresholds: CredibilityThresholds | None = None,
    now: datetime | None = None,
) -> CredibilityVerdict:
    """Classify a single capability's leaderboard.

    The classifier intentionally inspects only what we already persist; it
    does not ask the runtime to call any provider. That keeps the credibility
    report cheap and deterministic so it can run in CI.
    """

    cfg = thresholds or thresholds_from_env()
    moment = now or datetime.now(UTC)

    rankings_for_cap = [
        row for row in rankings if str(row.get("capability") or "") == capability
    ]
    real_rankings = [
        row for row in rankings_for_cap if _is_real_run(row)
    ]
    real_provider_ids = {str(row.get("provider_id") or "") for row in real_rankings}
    candidate_provider_ids = {candidate.id for candidate in candidates}
    candidate_provider_ids.discard("")

    last_real_run_at = max(
        (str(row.get("last_run_at") or "") for row in real_rankings),
        default="",
    )
    max_real_sample_size = max(
        (int(row.get("sample_size") or 0) for row in real_rankings),
        default=0,
    )

    reasons: list[str] = []
    unblockers: list[str] = []

    # Tier 1: synthetic_only
    if not real_rankings:
        reasons.append(
            f"All {len(rankings_for_cap)} ranking row(s) for `{capability}` "
            "use synthetic adapters. The leaderboard scores our scoring code, "
            "not the vendors."
        )
        unblockers.append(
            f"Add at least {cfg.min_real_providers} real provider adapter(s) and "
            "run them via `make benchmark-schedule`."
        )
        return CredibilityVerdict(
            capability=capability,
            status="synthetic_only",
            real_provider_count=0,
            total_provider_count=len(candidate_provider_ids),
            max_real_sample_size=0,
            last_real_run_at="",
            reasons=reasons,
            unblockers=unblockers,
        )

    # Tier 2: smoke_test — has real data but not enough to compare vendors
    if len(real_provider_ids) < cfg.min_real_providers:
        reasons.append(
            f"Only {len(real_provider_ids)} provider(s) have been benchmarked "
            f"with real adapters. Cross-vendor comparison needs at least "
            f"{cfg.min_real_providers}."
        )
        unblockers.append(
            f"Implement {cfg.min_real_providers - len(real_provider_ids)} more real "
            f"adapter(s) for `{capability}`."
        )

    if max_real_sample_size < cfg.min_sample_size:
        reasons.append(
            f"Largest real sample size is {max_real_sample_size}; we require "
            f">= {cfg.min_sample_size} for at least one provider."
        )
        unblockers.append(
            f"Add benchmark cases for `{capability}` until at least one provider "
            f"has {cfg.min_sample_size}+ runs."
        )

    age_days = _age_days(last_real_run_at, moment)
    if age_days is None:
        reasons.append("No real run timestamp available for this capability.")
        unblockers.append("Re-run the benchmark scheduler against real adapters.")
    elif age_days > cfg.max_run_age_days:
        reasons.append(
            f"Last real run was {age_days} day(s) ago; we require updates within "
            f"the last {cfg.max_run_age_days} day(s)."
        )
        unblockers.append(
            "Schedule `make benchmark-schedule` to refresh runs for this capability."
        )

    if reasons:
        # If we lack the bar for vendor comparison, label as smoke_test;
        # otherwise the only gaps are publishability extras → developing.
        below_bar = (
            len(real_provider_ids) < cfg.min_real_providers
            or max_real_sample_size < cfg.min_sample_size
        )
        status: CredibilityStatus = "smoke_test" if below_bar else "developing"
        return CredibilityVerdict(
            capability=capability,
            status=status,
            real_provider_count=len(real_provider_ids),
            total_provider_count=len(candidate_provider_ids),
            max_real_sample_size=max_real_sample_size,
            last_real_run_at=last_real_run_at,
            reasons=reasons,
            unblockers=unblockers,
        )

    return CredibilityVerdict(
        capability=capability,
        status="publishable",
        real_provider_count=len(real_provider_ids),
        total_provider_count=len(candidate_provider_ids),
        max_real_sample_size=max_real_sample_size,
        last_real_run_at=last_real_run_at,
        reasons=[
            "Meets cross-vendor comparison bar: real adapters, sample size, and "
            "freshness thresholds all satisfied."
        ],
        unblockers=[],
    )


def classify_index(
    *,
    candidates_by_capability: dict[str, list[DiscoveryCandidate]],
    rankings: list[dict[str, Any]],
    thresholds: CredibilityThresholds | None = None,
    now: datetime | None = None,
) -> dict[str, CredibilityVerdict]:
    """Classify every capability that has at least one declared candidate."""

    return {
        capability: classify(
            capability=capability,
            candidates=cands,
            rankings=rankings,
            thresholds=thresholds,
            now=now,
        )
        for capability, cands in candidates_by_capability.items()
    }


def _is_real_run(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "").strip().lower()
    if not source:
        return False
    if source in {"synthetic", "mock", "fixture", "stub"}:
        return False
    sample_size = int(row.get("sample_size") or 0)
    return sample_size > 0


def _age_days(timestamp: str, now: datetime) -> int | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta = now - parsed
    return max(delta.days, 0)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
