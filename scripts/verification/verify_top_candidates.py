"""Verify a batch of discovered candidates (sprint-pitch-align P2-3).

Picks the most promising candidates currently sitting in
``discovery_candidates`` (anything with ``verification_status`` in
``known_provider`` or ``registered_in_directory`` — these have the
strongest a-priori signal) and runs the existing
:func:`planmyagents_api.discovery.verification.verify_candidate`
against each. Persists every result as a row in
``verification_records`` so the agent detail page, the homepage Live
Evidence strip, and the deck's "evidence-first" claim all have real
data to render.

Designed to be safe and cheap:

* Only fetches ``evidence_url`` (or ``vendor_url`` fallback) — no
  side-effects against the real provider API.
* 8s timeout per fetch (the verifier default).
* Concurrency = 1 (sequential) so we stay polite on shared hosts
  like GitHub; flip ``--concurrency`` if you have key rotation.
* No-op when ``--limit`` is 0.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.models import DiscoveryCandidate  # noqa: E402
from planmyagents_api.discovery.store import PostgresDiscoveryStore  # noqa: E402
from planmyagents_api.discovery.verification import verify_candidate  # noqa: E402
from planmyagents_api.discovery.verification_store import (  # noqa: E402
    PostgresVerificationStore,
    VerificationRecord,
)

DEFAULT_DSN = "postgresql://planmyagents:planmyagents@localhost:55433/planmyagents"


def _resolve_dsn(arg: str | None) -> str:
    return (
        arg
        or os.environ.get("PLANMYAGENTS_VERIFICATION_STORE_URL")
        or os.environ.get("PLANMYAGENTS_DISCOVERY_STORE_URL")
        or DEFAULT_DSN
    )


_PREFERRED_TIERS = ("known_provider", "registered_in_directory")


def _fetch_candidates(dsn: str, limit: int) -> list[DiscoveryCandidate]:
    """Pull the top-`limit` candidates with the strongest a-priori signal.

    Ranking inside Python (cheap — load is already paginated server-side
    via the store's WHERE filter and we cap by ``limit`` after sorting):

    1. known_provider (already directory-recognised) → most likely to verify
    2. registered_in_directory (in MCP Registry / Smithery / etc.)
    Within each tier we prefer those with a non-empty evidence/vendor URL
    so the fetch doesn't waste time on candidates we can't verify.
    """
    store = PostgresDiscoveryStore(dsn)
    all_candidates = store.load()

    def _rank(candidate: DiscoveryCandidate) -> tuple[int, int, str]:
        try:
            tier_idx = _PREFERRED_TIERS.index(candidate.verification_status)
        except ValueError:
            tier_idx = len(_PREFERRED_TIERS)
        has_evidence = bool((candidate.evidence_url or candidate.vendor_url or "").strip())
        return (tier_idx, 0 if has_evidence else 1, candidate.id)

    eligible = [
        candidate
        for candidate in all_candidates
        if candidate.verification_status in _PREFERRED_TIERS
    ]
    eligible.sort(key=_rank)
    return eligible[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="Postgres DSN; defaults to env or local.")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max number of candidates to verify in this run (default 10).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Per-fetch timeout in seconds (default 8.0).",
    )
    args = parser.parse_args()

    if args.limit <= 0:
        print("[verify] limit=0 — nothing to do")
        return 0

    dsn = _resolve_dsn(args.dsn)
    candidates = _fetch_candidates(dsn, args.limit)
    if not candidates:
        print(f"[verify] no candidates found in {dsn} (status in known_provider|registered_in_directory)")
        return 0

    store = PostgresVerificationStore(dsn)
    records: list[VerificationRecord] = []
    started = time.time()

    for i, candidate in enumerate(candidates, start=1):
        evidence_url = candidate.evidence_url or candidate.vendor_url or "(none)"
        print(f"[verify] {i}/{len(candidates)} {candidate.id} -> {evidence_url}")
        result = verify_candidate(candidate, timeout_seconds=args.timeout)
        records.append(VerificationRecord.from_result(result))
        marker = "OK" if result.status != "unverified" else "BLOCKED"
        print(f"           status={result.status:25s} {marker} "
              f"blockers={result.blockers[:2]} verified_caps={result.verified_capabilities[:3]}")

    if records:
        store.append(records)

    elapsed = time.time() - started
    summary = {}
    for record in records:
        summary[record.status] = summary.get(record.status, 0) + 1

    print("---- verification summary ----")
    print(f"verified  {len(records)} candidates in {elapsed:.1f}s")
    for status, count in sorted(summary.items()):
        print(f"  {status:30s} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
