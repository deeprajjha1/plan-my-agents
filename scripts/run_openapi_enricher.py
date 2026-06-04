#!/usr/bin/env python3
"""Fetch OpenAPI specs for any candidate with ``openapi_url`` set.

This script reads the configured discovery store, runs the
``OpenApiEnricher`` over every candidate that has an OpenAPI URL but no tools
yet, and persists the enriched candidates back to the store. Specs that are
unreachable or non-JSON are skipped without failing the run.

Usage:

    PYTHONPATH=apps/api python scripts/run_openapi_enricher.py \\
        --store .planmyagents_runs/discovery-store.sqlite
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.discovery.enrichers.openapi import OpenApiEnricher  # noqa: E402
from planmyagents_api.discovery.store import discovery_store_for_path  # noqa: E402

LOGGER = logging.getLogger("openapi_enricher")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        default=os.getenv(
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "discovery-store.sqlite"),
        ),
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-operations", type=int, default=32)
    parser.add_argument("--reprobe-populated", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    store = discovery_store_for_path(args.store)
    candidates = store.load()
    targets = [c for c in candidates if c.openapi_url]
    if not args.reprobe_populated:
        targets = [c for c in targets if not c.tools]
    if args.limit > 0:
        targets = targets[: args.limit]
    if not targets:
        LOGGER.info("nothing to enrich (0 candidates with openapi_url awaiting tools)")
        return 0
    LOGGER.info("fetching openapi specs for %d candidates", len(targets))

    enricher = OpenApiEnricher(
        timeout_seconds=args.timeout,
        max_operations=args.max_operations,
        skip_when_already_populated=not args.reprobe_populated,
    )
    enriched = enricher.enrich(targets)

    enriched_by_id = {c.id: c for c in enriched}
    rebuilt = [enriched_by_id.get(c.id, c) for c in candidates]
    store.save(rebuilt)

    populated = sum(1 for c in enriched if c.tools)
    LOGGER.info("openapi enrichment complete: %d/%d gained tools", populated, len(targets))
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
