#!/usr/bin/env python3
"""Run Agent Discovery Index v0 locally.

This script searches configured discovery sources and prints non-routable
candidates. It does not execute providers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.discovery.service import default_discovery_sources, search_candidates  # noqa: E402
from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource  # noqa: E402
from planmyagents_api.discovery.sources.ai_directory import AiAgentDirectorySource  # noqa: E402
from planmyagents_api.discovery.sources.json_source import JsonDiscoverySource  # noqa: E402
from planmyagents_api.discovery.sources.mcp import McpCatalogSource  # noqa: E402
from planmyagents_api.discovery.sources.research import GitHubResearchSource, UrlResearchSource  # noqa: E402
from planmyagents_api.discovery.sources.static import StaticDiscoverySource  # noqa: E402
from planmyagents_api.discovery.sources.web_doc import WebDocDiscoverySource  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Search discovered agent/provider candidates.")
    parser.add_argument(
        "--capability", action="append", default=[], help="Capability to search for. Repeatable."
    )
    parser.add_argument("--query", default="", help="Natural-language task description.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--source-json",
        action="append",
        default=[],
        help="JSON candidate source file/URL. Repeatable.",
    )
    parser.add_argument(
        "--mcp-catalog", action="append", default=[], help="MCP catalog JSON file/URL. Repeatable."
    )
    parser.add_argument(
        "--a2a-card", action="append", default=[], help="A2A Agent Card JSON file/URL. Repeatable."
    )
    parser.add_argument(
        "--ai-directory",
        action="append",
        default=[],
        help="AI-agent directory JSON file/URL. Repeatable.",
    )
    parser.add_argument(
        "--web-docs",
        action="append",
        default=[],
        help="Web/docs manifest JSON file/URL. Repeatable.",
    )
    parser.add_argument(
        "--github-live",
        action="store_true",
        help="Search public GitHub repositories live for candidate MCP/A2A/AI agents.",
    )
    parser.add_argument(
        "--github-token",
        default="",
        help="Optional GitHub token for higher unauthenticated search limits.",
    )
    parser.add_argument(
        "--research-url",
        action="append",
        default=[],
        help="Public URL to inspect as live discovery evidence. Repeatable.",
    )
    parser.add_argument(
        "--store",
        help="Optional discovery store path (.sqlite/.db or JSON) or Postgres URL for persistent candidates.",
    )
    parser.add_argument(
        "--no-static", action="store_true", help="Disable the built-in static seed source."
    )
    parser.add_argument(
        "--no-persist", action="store_true", help="Do not persist search candidates to --store."
    )
    parser.add_argument(
        "--exclude-stale", action="store_true", help="Exclude stale candidates from results."
    )
    parser.add_argument(
        "--stale-after-days", type=int, default=30, help="Days before a candidate is stale."
    )
    args = parser.parse_args()

    capabilities = {str(item).strip() for item in args.capability if str(item).strip()}
    sources = _sources_from_args(args)
    store = args.store or os.getenv("PLANMYAGENTS_DISCOVERY_STORE_URL") or None
    payload = search_candidates(
        capabilities=capabilities,
        task_description=args.query,
        sources=sources,
        store_path=store,
        limit=args.limit,
        persist=not args.no_persist,
        include_stale=not args.exclude_stale,
        stale_after_days=args.stale_after_days,
    )
    print(json.dumps(payload, indent=2))
    return 0


def _sources_from_args(args: argparse.Namespace):
    explicit_sources = (
        args.source_json
        or args.mcp_catalog
        or args.a2a_card
        or args.ai_directory
        or args.web_docs
        or args.github_live
        or args.research_url
        or args.no_static
    )
    if not explicit_sources:
        return default_discovery_sources()

    sources = []
    if not args.no_static:
        sources.append(StaticDiscoverySource())
    if args.source_json:
        sources.append(JsonDiscoverySource(args.source_json))
    if args.mcp_catalog:
        sources.append(McpCatalogSource(args.mcp_catalog))
    if args.a2a_card:
        sources.append(A2AAgentCardSource(args.a2a_card))
    if args.ai_directory:
        sources.append(AiAgentDirectorySource(args.ai_directory))
    if args.web_docs:
        sources.append(WebDocDiscoverySource(args.web_docs))
    if args.github_live:
        sources.append(GitHubResearchSource(token=args.github_token))
    if args.research_url:
        sources.append(UrlResearchSource(args.research_url))
    return sources


if __name__ == "__main__":
    raise SystemExit(main())
