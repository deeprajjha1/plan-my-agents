#!/usr/bin/env python3
"""Verify discovery candidates and persist verification metadata."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.discovery.store import discovery_store_for_path  # noqa: E402
from planmyagents_api.discovery.verification import candidate_with_verification, verify_candidate  # noqa: E402
from planmyagents_api.discovery.verification_store import (  # noqa: E402
    VerificationRecord,
    verification_store_for_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify discovery candidate evidence.")
    parser.add_argument(
        "--store",
        default=os.getenv("PLANMYAGENTS_DISCOVERY_STORE_URL", ""),
        help="Discovery store URL/path.",
    )
    parser.add_argument(
        "--verification-store",
        default=os.getenv(
            "PLANMYAGENTS_VERIFICATION_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "verification-store.json"),
        ),
        help="Optional verification history store path or Postgres URL.",
    )
    parser.add_argument("--candidate-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.store:
        parser.error("--store or PLANMYAGENTS_DISCOVERY_STORE_URL is required")

    store = discovery_store_for_path(args.store)
    verification_store = (
        verification_store_for_path(args.verification_store)
        if args.verification_store
        else None
    )
    candidates = store.load()
    wanted = {item for item in args.candidate_id if item}
    selected = [
        candidate
        for candidate in candidates
        if not wanted or candidate.id in wanted
    ][: args.limit]
    results = []
    records: list[VerificationRecord] = []
    updates = {candidate.id: candidate for candidate in candidates}
    for candidate in selected:
        result = verify_candidate(candidate)
        results.append(result.to_json())
        updates[candidate.id] = candidate_with_verification(candidate, result)
        records.append(VerificationRecord.from_result(result))

    if not args.dry_run:
        store.save(list(updates.values()))
        if verification_store and records:
            verification_store.append(records)

    print(
        json.dumps(
            {
                "store": args.store,
                "verification_store": args.verification_store if verification_store else None,
                "checked": len(selected),
                "verified": sum(1 for result in results if result["status"] != "unverified"),
                "saved": not args.dry_run,
                "results": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
