#!/usr/bin/env python3
"""Refresh the local Agent Discovery Index store from configured sources."""

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

# Pick up `.env` so this script behaves the same whether invoked via Make or
# directly. Live-research keys (BRAVE_SEARCH_API_KEY, TAVILY_API_KEY, etc.)
# are read by the underlying discovery sources — no key, no source.
load_dotenv_once()

from planmyagents_api.discovery.service import (  # noqa: E402
    _load_curated_rss_feeds,
    search_candidates,
)
from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource  # noqa: E402
from planmyagents_api.discovery.sources.ai_directory import AiAgentDirectorySource  # noqa: E402
from planmyagents_api.discovery.sources.apis_guru import ApisGuruSource  # noqa: E402
from planmyagents_api.discovery.sources.github_recently_pushed import (  # noqa: E402
    GitHubRecentlyPushedSource,
)
from planmyagents_api.discovery.sources.hacker_news import (  # noqa: E402
    HackerNewsAgentWatcherSource,
)
from planmyagents_api.discovery.sources.mcp import McpCatalogSource  # noqa: E402
from planmyagents_api.discovery.sources.official_mcp_registry import (  # noqa: E402
    OfficialMcpRegistrySource,
)
from planmyagents_api.discovery.sources.research import GitHubResearchSource, UrlResearchSource  # noqa: E402
from planmyagents_api.discovery.sources.static import StaticDiscoverySource  # noqa: E402
from planmyagents_api.discovery.sources.vendor_rss import VendorRssSource  # noqa: E402
from planmyagents_api.discovery.sources.web_doc import WebDocDiscoverySource  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh local discovery candidates into a persistent store."
    )
    parser.add_argument(
        "--store",
        default=os.getenv(
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "discovery-store.sqlite"),
        ),
        help="Discovery store path (.sqlite/.db or JSON) or Postgres URL.",
    )
    parser.add_argument(
        "--mcp-catalog",
        action="append",
        default=[str(ROOT / "packages/discovery/sources/curated_mcp_catalog.json")],
    )
    parser.add_argument(
        "--a2a-card",
        action="append",
        default=[str(ROOT / "packages/discovery/sources/curated_a2a_cards.json")],
    )
    parser.add_argument(
        "--ai-directory",
        action="append",
        default=[str(ROOT / "packages/discovery/sources/curated_ai_agents.json")],
    )
    parser.add_argument(
        "--web-docs",
        action="append",
        default=[str(ROOT / "packages/discovery/sources/curated_web_docs.json")],
    )
    parser.add_argument(
        "--capability", action="append", default=[], help="Capability to refresh. Repeatable."
    )
    parser.add_argument(
        "--github-live",
        action="store_true",
        help="Search public GitHub repositories live while refreshing the store.",
    )
    parser.add_argument("--github-token", default="", help="Optional GitHub token for live search.")
    parser.add_argument(
        "--research-url",
        action="append",
        default=[],
        help="Public URL to inspect as discovery evidence. Repeatable.",
    )
    parser.add_argument("--no-static", action="store_true")
    parser.add_argument(
        "--no-official-mcp-registry",
        action="store_true",
        help="Skip the canonical MCP server registry "
        "(registry.modelcontextprotocol.io). Default: enabled.",
    )
    parser.add_argument(
        "--no-apis-guru",
        action="store_true",
        help="Skip the APIs.guru OpenAPI directory "
        "(api.apis.guru/v2/list.json). Default: enabled.",
    )
    parser.add_argument(
        "--no-hacker-news",
        action="store_true",
        help="Skip the Hacker News agent/MCP watcher. Default: enabled.",
    )
    parser.add_argument(
        "--no-github-recently-pushed",
        action="store_true",
        help="Skip the GitHub recently-pushed agent repo watcher "
        "(no-ops without GITHUB_TOKEN). Default: enabled.",
    )
    parser.add_argument(
        "--no-vendor-rss",
        action="store_true",
        help="Skip the curated vendor RSS feed watcher. Default: enabled.",
    )
    parser.add_argument("--stale-after-days", type=int, default=30)
    args = parser.parse_args()

    sources = []
    if not args.no_static:
        sources.append(StaticDiscoverySource())
    sources.extend(
        [
            McpCatalogSource(args.mcp_catalog),
            A2AAgentCardSource(args.a2a_card),
            AiAgentDirectorySource(args.ai_directory),
            WebDocDiscoverySource(args.web_docs),
        ]
    )
    if not args.no_official_mcp_registry:
        sources.append(OfficialMcpRegistrySource())
    if not args.no_apis_guru:
        sources.append(ApisGuruSource())
    if not args.no_hacker_news:
        sources.append(HackerNewsAgentWatcherSource())
    if not args.no_github_recently_pushed:
        sources.append(
            GitHubRecentlyPushedSource(token=os.getenv("GITHUB_TOKEN", ""))
        )
    if not args.no_vendor_rss:
        feeds = _load_curated_rss_feeds()
        if feeds:
            sources.append(VendorRssSource(feed_urls=feeds))
    if args.github_live:
        sources.append(GitHubResearchSource(token=args.github_token))
    if args.research_url:
        sources.append(UrlResearchSource(args.research_url))
    capabilities = set(args.capability)
    task_description = (
        "refresh local discovery store from configured agent/provider sources"
        if not capabilities
        else f"refresh local discovery store for {', '.join(sorted(capabilities))}"
    )
    payload = search_candidates(
        capabilities=capabilities,
        task_description=task_description,
        sources=sources,
        store_path=args.store,
        limit=200,
        persist=True,
        load_store=False,
        include_stale=True,
        stale_after_days=args.stale_after_days,
    )
    print(
        json.dumps(
            {
                "store": str(args.store),
                "searched_capabilities": payload["searched_capabilities"],
                "total_candidates": payload["total_candidates"],
                "returned_results": len(payload["results"]),
                "provider_types": sorted({item["provider_type"] for item in payload["results"]}),
                "stale_candidates": sum(
                    1 for item in payload["results"] if item["freshness"]["is_stale"]
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
