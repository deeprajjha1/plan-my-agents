#!/usr/bin/env python3
"""Probe reachable MCP servers in the discovery store for their ``tools/list``.

This script reads the configured discovery store, runs the
``McpToolProbeEnricher`` against any MCP candidate with an HTTP(S) vendor URL,
and persists the enriched candidates back to the store. Network failures are
swallowed per-candidate so a flaky upstream cannot poison the run.

Usage:

    PYTHONPATH=apps/api python scripts/run_mcp_tool_probe.py \\
        --store .planmyagents_runs/discovery-store.sqlite

Use ``--limit`` to cap how many candidates we touch in one run, useful when
wiring this into a recurring upkeep loop.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.discovery.enrichers.mcp_tools import McpToolProbeEnricher  # noqa: E402
from planmyagents_api.discovery.store import discovery_store_for_path  # noqa: E402

LOGGER = logging.getLogger("mcp_tool_probe")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        default=os.getenv(
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "discovery-store.sqlite"),
        ),
        help="Discovery store path or Postgres URL.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of MCP candidates to probe (0 = no limit).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="HTTP timeout per probe in seconds.",
    )
    parser.add_argument(
        "--reprobe-populated",
        action="store_true",
        help="Re-probe MCP candidates that already have tools populated.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    store = discovery_store_for_path(args.store)
    candidates = store.load()
    if not candidates:
        LOGGER.info("no candidates in store at %s", args.store)
        return 0

    targets = [c for c in candidates if c.provider_type == "mcp_server"]
    if not args.reprobe_populated:
        targets = [c for c in targets if not c.tools]
    if args.limit > 0:
        targets = targets[: args.limit]
    if not targets:
        LOGGER.info("nothing to probe (0 mcp candidates without tools)")
        return 0
    LOGGER.info("probing %d mcp candidates", len(targets))

    enricher = McpToolProbeEnricher(
        timeout_seconds=args.timeout,
        skip_when_already_populated=not args.reprobe_populated,
    )
    enriched = enricher.enrich(targets)

    enriched_by_id = {c.id: c for c in enriched}
    rebuilt = [enriched_by_id.get(c.id, c) for c in candidates]
    store.save(rebuilt)

    populated = sum(1 for c in enriched if c.tools)
    LOGGER.info("probe complete: %d/%d mcp candidates now have tools", populated, len(targets))
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
