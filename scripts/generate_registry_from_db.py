#!/usr/bin/env python3
"""Generate a provider registry by merging promotion-ready DB candidates."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.registry.loader import load_registry  # noqa: E402
from planmyagents_api.registry.promotions import merge_promoted_agents_from_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge promotion-ready discovery DB candidates into an agents.json registry."
    )
    parser.add_argument(
        "--store",
        default=os.getenv("PLANMYAGENTS_DISCOVERY_STORE_URL", ""),
        help="Discovery store URL/path. Use Postgres for the production-shaped flow.",
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=ROOT / "packages" / "registry" / "agents.json",
        help="Base promoted-provider registry.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write merged registry to this path. Defaults to stdout.",
    )
    args = parser.parse_args()

    if not args.store:
        parser.error("--store or PLANMYAGENTS_DISCOVERY_STORE_URL is required")

    base = load_registry(args.base)
    merged = merge_promoted_agents_from_store(base, args.store)
    encoded = json.dumps(merged, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
