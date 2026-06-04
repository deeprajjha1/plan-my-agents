#!/usr/bin/env python3
"""Run generic discovery/ranking behavior probes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource  # noqa: E402
from planmyagents_api.discovery.sources.ai_directory import AiAgentDirectorySource  # noqa: E402
from planmyagents_api.discovery.sources.mcp import McpCatalogSource  # noqa: E402
from planmyagents_api.discovery.sources.static import StaticDiscoverySource  # noqa: E402
from planmyagents_api.discovery.sources.web_doc import WebDocDiscoverySource  # noqa: E402
from planmyagents_api.evaluation.generalization import load_scenarios, run_scenarios  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run generalization probes for discovery/ranking behavior."
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=ROOT / "packages" / "evals" / "generalization" / "scenarios.json",
    )
    parser.add_argument(
        "--store",
        help="Optional discovery store to include. Omit for deterministic curated-source evals.",
    )
    args = parser.parse_args()

    payload = run_scenarios(
        scenarios=load_scenarios(args.scenarios),
        sources=_curated_sources(),
        store_path=args.store,
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 1


def _curated_sources():
    return [
        StaticDiscoverySource(),
        McpCatalogSource([str(ROOT / "packages/discovery/sources/curated_mcp_catalog.json")]),
        A2AAgentCardSource([str(ROOT / "packages/discovery/sources/curated_a2a_cards.json")]),
        AiAgentDirectorySource([str(ROOT / "packages/discovery/sources/curated_ai_agents.json")]),
        WebDocDiscoverySource([str(ROOT / "packages/discovery/sources/curated_web_docs.json")]),
    ]


if __name__ == "__main__":
    raise SystemExit(main())
