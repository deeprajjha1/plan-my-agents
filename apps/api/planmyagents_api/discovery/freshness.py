"""Freshness and staleness helpers for discovered candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class FreshnessStatus:
    """Computed freshness status for a candidate."""

    first_seen_at: str
    last_seen_at: str
    age_days: int | None
    stale_after_days: int
    is_stale: bool
    reason: str

    def to_json(self) -> dict:
        return {
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "age_days": self.age_days,
            "stale_after_days": self.stale_after_days,
            "is_stale": self.is_stale,
            "reason": self.reason,
        }


def freshness_status(
    *, first_seen_at: str, last_seen_at: str, stale_after_days: int
) -> FreshnessStatus:
    """Compute freshness from ISO dates."""

    parsed_last_seen = _parse_date(last_seen_at)
    if parsed_last_seen is None:
        return FreshnessStatus(
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
            age_days=None,
            stale_after_days=stale_after_days,
            is_stale=True,
            reason="Candidate has invalid or missing last_seen_at metadata.",
        )

    age_days = (date.today() - parsed_last_seen).days
    is_stale = age_days > stale_after_days
    return FreshnessStatus(
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at,
        age_days=age_days,
        stale_after_days=stale_after_days,
        is_stale=is_stale,
        reason=(
            f"Candidate has not been refreshed for {age_days} day(s)."
            if is_stale
            else "Candidate is within the freshness window."
        ),
    )


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
