#!/usr/bin/env python3
"""Ingest a submitted Agent Card URL (any domain) into the discovery index.

User/vendor-submitted registration path — NOT a crawler. Resolves the card,
runs existence/claim verification (reusing verify_candidate), and indexes the
agent at an honest trust tier. A2A quality attestation is blocked-on-invocation
and is NOT performed here.

Usage:
    PYTHONPATH=apps/api python scripts/discovery/run_card_ingestion.py \
        --card-url https://policycheck.tools/.well-known/agent.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.card_ingestion import CardIngestionService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest a submitted Agent Card URL into the discovery index."
    )
    parser.add_argument(
        "--card-url",
        required=True,
        help="https URL of the agent card (e.g. https://domain/.well-known/agent.json).",
    )
    parser.add_argument(
        "--discovery-store",
        default=os.getenv(
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "discovery-store.sqlite"),
        ),
        help="Discovery store path or Postgres URL.",
    )
    parser.add_argument(
        "--verification-store",
        default=os.getenv(
            "PLANMYAGENTS_VERIFICATION_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "verification-store.json"),
        ),
        help="Verification store path or Postgres URL.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-fetch timeout in seconds (default 10).",
    )
    args = parser.parse_args()

    service = CardIngestionService(
        discovery_store_url=args.discovery_store,
        verification_store_url=args.verification_store,
        timeout_seconds=args.timeout,
    )
    result = service.ingest(args.card_url)
    print(json.dumps({"card_url": args.card_url, **result.to_json()}, indent=2))
    # Exit 0 even on a failed resolution — the run is idempotent and a bad URL
    # is a normal, reported outcome, not a crash.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
