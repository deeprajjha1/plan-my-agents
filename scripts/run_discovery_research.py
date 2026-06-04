#!/usr/bin/env python3
"""Run a local broad discovery research refresh.

The job combines curated discovery manifests with optional live GitHub and URL
research. Live findings remain non-routable and unverified until reviewed,
adapted, and benchmarked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api._env import load_dotenv_once  # noqa: E402

# Load `.env` so live-research keys (BRAVE_SEARCH_API_KEY, TAVILY_API_KEY,
# GITHUB_TOKEN, etc.) are picked up whether the script is launched via `make
# discovery-refresh` or directly via `python scripts/run_discovery_research.py`.
load_dotenv_once()

from planmyagents_api.discovery.service import search_candidates  # noqa: E402
from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource  # noqa: E402
from planmyagents_api.discovery.sources.ai_directory import AiAgentDirectorySource  # noqa: E402
from planmyagents_api.discovery.sources.mcp import McpCatalogSource  # noqa: E402
from planmyagents_api.discovery.sources.research import GitHubResearchSource, UrlResearchSource  # noqa: E402
from planmyagents_api.discovery.sources.static import StaticDiscoverySource  # noqa: E402
from planmyagents_api.discovery.sources.web_doc import WebDocDiscoverySource  # noqa: E402

BROAD_CAPABILITIES = {
    "booking_execution",
    "company_data_lookup",
    "contact_enrichment",
    "email_verification",
    "fare_comparison",
    "lodging_comparison",
    "lodging_search",
    "payment_authorization",
    "semantic_search",
    "shipping_quote",
    "travel_search",
    "web_scraping",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run broad local discovery research.")
    parser.add_argument(
        "--store",
        default=os.getenv(
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "discovery-store.sqlite"),
        ),
        help="Discovery store path (.sqlite/.db or JSON) or Postgres URL.",
    )
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--query", default="broad MCP A2A AI-agent provider discovery refresh")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--no-static", action="store_true")
    parser.add_argument("--no-github-live", action="store_true")
    parser.add_argument("--github-token", default=os.getenv("GITHUB_TOKEN", ""))
    parser.add_argument(
        "--research-url",
        action="append",
        default=[],
        help="Public URL to inspect as discovery evidence. Repeatable.",
    )
    parser.add_argument("--stale-after-days", type=int, default=30)
    args = parser.parse_args()

    capabilities = {item.strip() for item in args.capability if item.strip()} or BROAD_CAPABILITIES
    sources = []
    if not args.no_static:
        sources.append(StaticDiscoverySource())
    sources.extend(
        [
            McpCatalogSource([str(ROOT / "packages/discovery/sources/curated_mcp_catalog.json")]),
            A2AAgentCardSource([str(ROOT / "packages/discovery/sources/curated_a2a_cards.json")]),
            AiAgentDirectorySource([str(ROOT / "packages/discovery/sources/curated_ai_agents.json")]),
            WebDocDiscoverySource([str(ROOT / "packages/discovery/sources/curated_web_docs.json")]),
        ]
    )
    if not args.no_github_live:
        sources.append(GitHubResearchSource(token=args.github_token))
    if args.research_url:
        sources.append(UrlResearchSource(args.research_url))

    payload = search_candidates(
        capabilities=capabilities,
        task_description=args.query,
        sources=sources,
        store_path=args.store,
        limit=args.limit,
        persist=True,
        load_store=False,
        include_stale=True,
        stale_after_days=args.stale_after_days,
    )
    print(
        json.dumps(
            {
                "store": str(args.store),
                "live_github_research": not args.no_github_live,
                "research_urls": args.research_url,
                "searched_capabilities": payload["searched_capabilities"],
                "total_candidates": payload["total_candidates"],
                "returned_results": len(payload["results"]),
                "provider_types": sorted({item["provider_type"] for item in payload["results"]}),
                "unverified_results": sum(
                    1 for item in payload["results"] if item["verification_status"] == "unverified"
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
