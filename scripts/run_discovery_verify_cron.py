"""Discovery verification cron (sprint-pitch-align P3-2).

Keeps the evidence ladder honest by re-verifying every recently-verified
candidate on a recurring cadence:

* `known_provider` candidates → re-verify every **7 days**
* `registered_in_directory` candidates → re-verify every **30 days**
* `capability_verified` candidates → re-verify every **30 days**
  (these are the strongest tier, so we still touch them monthly)

After each verification we persist a row to `verification_records`. If the
fresh verification *worsens* the status (or returns `unverified`), the
candidate's current `verification_status` in `discovery_candidates` gets
demoted one tier — but only after the previous successful verification is
older than `2 * verification_interval` for that tier, i.e. it has had ample
time to recover. This avoids flapping a transient HTTP 503 into a perceived
trust regression.

Designed to be safe in a cron:

* Per-fetch timeout (default 8s) inherited from the verifier.
* Concurrency=1 so we stay polite on shared hosts.
* Exit code 0 even when nothing was due (idempotent re-runs are fine).
* Exit code != 0 only when the DB is unreachable or schema is missing.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.models import DiscoveryCandidate  # noqa: E402
from planmyagents_api.discovery.store import PostgresDiscoveryStore  # noqa: E402
from planmyagents_api.discovery.verification import verify_candidate  # noqa: E402
from planmyagents_api.discovery.verification_store import (  # noqa: E402
    PostgresVerificationStore,
    VerificationRecord,
)

DEFAULT_DSN = "postgresql://planmyagents:planmyagents@localhost:55433/planmyagents"

# Tier → re-verify cadence (days). Two-week stale window for known_provider,
# two-month stale window for the other two tiers. Numbers match the sprint
# plan's "weekly known_provider, monthly registered_in_directory" goal and
# extend symmetrically to capability_verified so the very top tier doesn't
# drift either.
TIER_INTERVAL_DAYS: dict[str, int] = {
    "known_provider": 7,
    "registered_in_directory": 30,
    "capability_verified": 30,
}

# Tier → next-lower tier for stale-demotion. Falls off the ladder into
# `community_listed` (a softer non-evidence tier we already track).
TIER_DEMOTION: dict[str, str] = {
    "capability_verified": "known_provider",
    "known_provider": "registered_in_directory",
    "registered_in_directory": "community_listed",
}

ELIGIBLE_TIERS: tuple[str, ...] = tuple(TIER_INTERVAL_DAYS.keys())


def _resolve_dsn(arg: str | None) -> str:
    return (
        arg
        or os.environ.get("PLANMYAGENTS_VERIFICATION_STORE_URL")
        or os.environ.get("PLANMYAGENTS_DISCOVERY_STORE_URL")
        or DEFAULT_DSN
    )


def _is_due(latest_created_at: str | None, tier: str, *, now: datetime) -> bool:
    """Return True when the candidate has never been verified, or the last
    verification is older than the tier's cadence."""

    if not latest_created_at:
        return True
    try:
        when = datetime.fromisoformat(latest_created_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    cadence = timedelta(days=TIER_INTERVAL_DAYS[tier])
    return (now - when) > cadence


def _is_stale_for_demotion(
    latest_created_at: str | None, tier: str, *, now: datetime
) -> bool:
    """Stale when no successful verification exists within `2 * cadence`.

    The doubled window is the recovery buffer: if a candidate was last
    verified successfully 10 days ago at `known_provider` (7-day cadence),
    the next cron run that returns `unverified` will _not_ immediately
    demote — we wait until day 14 before stale-demoting. This stops a
    flaky upstream from flapping a real evidence tier.
    """

    if not latest_created_at:
        return True
    try:
        when = datetime.fromisoformat(latest_created_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (now - when) > timedelta(days=2 * TIER_INTERVAL_DAYS[tier])


def _due_candidates(
    discovery: PostgresDiscoveryStore,
    verification: PostgresVerificationStore,
    *,
    now: datetime,
) -> list[tuple[DiscoveryCandidate, str | None]]:
    """Load every candidate at an eligible tier and pair it with the
    `created_at` of its latest verification record (None if never verified)."""

    out: list[tuple[DiscoveryCandidate, str | None]] = []
    for candidate in discovery.load():
        if candidate.verification_status not in ELIGIBLE_TIERS:
            continue
        latest = verification.latest_for(candidate.id)
        latest_created_at = str(latest.get("created_at")) if latest else None
        if _is_due(latest_created_at, candidate.verification_status, now=now):
            out.append((candidate, latest_created_at))
    return out


def _demote_if_stale(
    discovery: PostgresDiscoveryStore,
    candidate: DiscoveryCandidate,
    *,
    fresh_status: str,
    last_good_at: str | None,
    now: datetime,
) -> str | None:
    """Apply the stale-demotion rule. Returns the new tier, or None when no
    demotion happened (either because the verification still succeeded at
    the same/higher tier, or the recovery window is not yet exhausted)."""

    tier = candidate.verification_status
    if fresh_status == tier or fresh_status in ELIGIBLE_TIERS:
        return None
    if not _is_stale_for_demotion(last_good_at, tier, now=now):
        return None
    target = TIER_DEMOTION.get(tier)
    if not target or target == tier:
        return None
    updated: list = []
    for c in discovery.load():
        if c.id == candidate.id and dataclasses.is_dataclass(c):
            updated.append(dataclasses.replace(c, verification_status=target))
        elif c.id == candidate.id:
            # Test stubs may pass non-dataclass candidates; mutate in place
            # and re-emit. Dataclasses can't be mutated when frozen, so the
            # `is_dataclass` branch above handles them via `replace`.
            c.verification_status = target  # type: ignore[attr-defined]
            updated.append(c)
        else:
            updated.append(c)
    discovery.save(updated)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="Postgres DSN; defaults to env or local.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Per-fetch timeout in seconds (default 8.0).",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=int(os.environ.get("PLANMYAGENTS_VERIFY_CRON_MAX", "50")),
        help=(
            "Safety cap so a runaway cron doesn't hammer upstream hosts. "
            "Default 50 candidates per invocation."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the due list and print the plan; do not fetch.",
    )
    args = parser.parse_args()

    dsn = _resolve_dsn(args.dsn)
    discovery = PostgresDiscoveryStore(dsn)
    verification = PostgresVerificationStore(dsn)

    now = datetime.now(UTC)
    due = _due_candidates(discovery, verification, now=now)
    due = due[: max(args.max_candidates, 0)]

    if not due:
        print(json.dumps({"due": 0, "message": "no candidates due this run"}))
        return 0

    if args.dry_run:
        print(
            json.dumps(
                {
                    "due": len(due),
                    "candidates": [
                        {
                            "id": c.id,
                            "tier": c.verification_status,
                            "last_verified_at": last,
                        }
                        for c, last in due
                    ],
                },
                indent=2,
            )
        )
        return 0

    records: list[VerificationRecord] = []
    demotions: list[dict[str, str]] = []
    started = time.time()

    for i, (candidate, last_verified_at) in enumerate(due, start=1):
        evidence_url = candidate.evidence_url or candidate.vendor_url or "(none)"
        print(
            f"[verify-cron] {i}/{len(due)} {candidate.id} "
            f"tier={candidate.verification_status} last={last_verified_at} "
            f"-> {evidence_url}"
        )
        result = verify_candidate(candidate, timeout_seconds=args.timeout)
        records.append(VerificationRecord.from_result(result))
        marker = "OK" if result.status in ELIGIBLE_TIERS else "DEGRADED"
        print(
            f"             status={result.status:25s} {marker} "
            f"blockers={result.blockers[:2]}"
        )
        demoted_to = _demote_if_stale(
            discovery,
            candidate,
            fresh_status=result.status,
            last_good_at=last_verified_at,
            now=now,
        )
        if demoted_to:
            demotions.append(
                {
                    "candidate_id": candidate.id,
                    "from_tier": candidate.verification_status,
                    "to_tier": demoted_to,
                    "reason": "stale_verification_window_exceeded",
                }
            )
            print(
                f"             demoted {candidate.id}: "
                f"{candidate.verification_status} -> {demoted_to}"
            )

    if records:
        verification.append(records)

    summary: dict[str, int] = {}
    for record in records:
        summary[record.status] = summary.get(record.status, 0) + 1

    payload = {
        "due": len(due),
        "verified": len(records),
        "demoted": len(demotions),
        "demotions": demotions,
        "status_breakdown": summary,
        "elapsed_seconds": round(time.time() - started, 2),
    }
    print("---- verify-cron summary ----")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
