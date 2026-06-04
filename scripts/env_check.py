#!/usr/bin/env python3
"""Print which env vars are configured for the agent-manager runtime.

Reads `.env` via the same loader the FastAPI app uses, then groups the result
by purpose (storage, planner, live discovery) so you can see at a glance:

* what is wired up,
* what is missing,
* and what each missing var unlocks.

Usage:

    PYTHONPATH=apps/api .venv/bin/python scripts/env_check.py

Exits 0 always. Never prints actual secret values.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api._env import load_dotenv_once  # noqa: E402

# Single source of truth for every env var the runtime cares about. Keep this
# in sync with `.env.example` so the help text stays accurate.
#
# Two tiers for discovery sources:
#   TIER 1 = first-party (we own the loop). GitHub + public catalog JSON.
#   TIER 2 = third-party search APIs (Tavily/Brave). Scaffolding, opt-in.
GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Storage (required for everything)",
        [
            (
                "PLANMYAGENTS_DISCOVERY_STORE_URL",
                "required",
                "Where the Agent Discovery Index lives (Postgres or sqlite path).",
            ),
            (
                "PLANMYAGENTS_BENCHMARK_STORE_URL",
                "required",
                "Where benchmark runs and rankings are persisted.",
            ),
            (
                "PLANMYAGENTS_VERIFICATION_STORE_URL",
                "required",
                "Where candidate verification results are persisted.",
            ),
        ],
    ),
    (
        "Planner / intent mapper (local, free)",
        [
            (
                "PLANMYAGENTS_PLANNER",
                "optional",
                "Planner backend: `escalating` (default; N-tier client; "
                "degrades cleanly to local Qwen when GROQ_API_KEY is unset), "
                "`local_qwen` (single-tier Qwen), `groq` (single-model Groq, "
                "requires GROQ_API_KEY), or `rules` (debug-only regex).",
            ),
            (
                "PLANMYAGENTS_QWEN_MODEL",
                "optional",
                "Ollama model tag for the local planner.",
            ),
            (
                "GROQ_API_KEY",
                "optional",
                "Required only if PLANMYAGENTS_PLANNER=groq. Free tier exists.",
            ),
        ],
    ),
    (
        "TIER 1 — first-party discovery (we own the loop)",
        [
            (
                "GITHUB_TOKEN",
                "recommended",
                "Free PAT (no scopes needed) → unlocks GitHub code search, "
                "today the most important first-party source since most "
                "MCPs/A2A agents/OpenAPI manifests live in GitHub repos. "
                "5k req/hour. https://github.com/settings/tokens",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_GITHUB_CODE_SEARCH",
                "recommended",
                "Set to `true` once GITHUB_TOKEN is set.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
                "default-on",
                "Canonical MCP server registry "
                "(registry.modelcontextprotocol.io). No key needed. ON unless "
                "set to `false`. Verified live: ~60-200 servers per refresh.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_APIS_GURU",
                "default-on",
                "APIs.guru OpenAPI directory (api.apis.guru/v2/list.json). "
                "No key needed, CC0 license. ON unless set to `false`. "
                "Verified live: ~2,500 providers, ~300-500 candidates after "
                "capability filtering.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
                "default-on",
                "Event-driven HN watcher (top + new stories matching "
                "agent/MCP/A2A keywords). No key needed. ON unless set to "
                "`false`. Catches launches within hours.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
                "default-on",
                "Event-driven RSS watcher across 13 curated vendor blogs + "
                "subreddits + HN search firehose (see "
                "packages/discovery/sources/vendor_rss_feeds.json). No key. "
                "ON unless set to `false`.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
                "default-on (needs GITHUB_TOKEN)",
                "Event-driven GitHub repo watcher "
                "(/search/repositories?pushed:>X). No-ops without "
                "GITHUB_TOKEN. ON unless set to `false`.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_JSON_SOURCES",
                "optional",
                "Extra comma-separated public catalog JSON URLs to append.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_MCP_CATALOGS",
                "optional",
                "Extra MCP server catalog URLs (the canonical one is wired by "
                "default — see PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY).",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_A2A_CARDS",
                "optional",
                "Comma-separated A2A agent card directory URLs.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_AI_DIRECTORIES",
                "optional",
                "Comma-separated AI agent directory URLs.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_WEB_DOCS",
                "optional",
                "Comma-separated vendor doc URLs we crawl ourselves.",
            ),
        ],
    ),
    (
        "TIER 2 — third-party search APIs (scaffolding, opt-in only)",
        [
            (
                "TAVILY_API_KEY",
                "optional",
                "Tavily general web search. Free tier 1k/month. "
                "Useful only as a fallback for the long tail of vendor blogs "
                "Tier 1 doesn't cover yet. https://tavily.com",
            ),
            (
                "BRAVE_SEARCH_API_KEY",
                "optional",
                "Brave general web search. Free tier 2k/month. "
                "Same role as Tavily — fallback only. https://brave.com/search/api/",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_TAVILY_SEARCH",
                "optional",
                "Set to `true` once TAVILY_API_KEY is set. OFF by default.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_BRAVE_SEARCH",
                "optional",
                "Set to `true` once BRAVE_SEARCH_API_KEY is set. OFF by default.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_LIVE_MCP_REGISTRIES",
                "optional",
                "WARNING: actually a Brave/Tavily query for 'MCP server', "
                "not a real registry crawl. OFF by default until Tier 1 covers it.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_LIVE_A2A_CARDS",
                "optional",
                "WARNING: actually a Brave/Tavily query, not a real A2A crawl. "
                "OFF by default — GitHub code search (Tier 1) covers this better.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_LIVE_OPENAPI_SPECS",
                "optional",
                "WARNING: actually a Brave/Tavily query. OFF by default — "
                "wire PLANMYAGENTS_DISCOVERY_JSON_SOURCES with apis.guru instead.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_LIVE_VENDOR_DOCS",
                "optional",
                "WARNING: actually a Brave/Tavily query. OFF by default — "
                "wire PLANMYAGENTS_DISCOVERY_WEB_DOCS with curated URLs instead.",
            ),
            (
                "PLANMYAGENTS_DISCOVERY_LIVE_AGENT_MARKETPLACES",
                "optional",
                "WARNING: actually a Brave/Tavily query. OFF by default.",
            ),
        ],
    ),
    (
        "TIER 2 — paid alternatives",
        [
            (
                "EXA_API_KEY",
                "optional",
                "Neural search. ~$10 starter. https://exa.ai",
            ),
        ],
    ),
    (
        "Real benchmark adapters (only needed for credible leaderboards)",
        [
            (
                "HUNTER_API_KEY",
                "optional",
                "Hunter.io adapter for `email_verification`. ~$49/mo paid plan "
                "for full features; free tier exists with limits.",
            ),
        ],
    ),
]

STATUS_ICON = {
    "set": "[set]",
    "unset": "[ ! ]",
}


def main() -> int:
    loaded = load_dotenv_once()
    if loaded:
        print(f"loaded .env from: {loaded}")
    else:
        print("no .env file found — using shell environment only")
    print()

    total_set = 0
    total_unset_recommended = 0

    for group_name, vars_in_group in GROUPS:
        print(f"## {group_name}")
        for env_var, requirement, help_text in vars_in_group:
            present = bool(os.getenv(env_var))
            status = STATUS_ICON["set"] if present else STATUS_ICON["unset"]
            tag = f"({requirement})"
            print(f"  {status} {env_var} {tag}")
            if not present:
                # Indent the help text so it lines up under the var name.
                for line in _wrap(help_text, width=78, indent="        "):
                    print(line)
            if present:
                total_set += 1
            elif requirement in {"required", "recommended"}:
                total_unset_recommended += 1
        print()

    print("---")
    print(f"summary: {total_set} env vars set; "
          f"{total_unset_recommended} required/recommended still missing.")
    if total_unset_recommended:
        print(
            "tip: edit `.env`, paste the keys, then re-run "
            "`make env-check` (or `make growth-pass` to grow the index)."
        )
    return 0


def _wrap(text: str, *, width: int, indent: str) -> list[str]:
    """Tiny word-wrap so we don't pull in textwrap-style multi-line config."""
    words = text.split()
    lines: list[str] = []
    current = indent
    for word in words:
        if len(current) + len(word) + 1 > width and current.strip():
            lines.append(current.rstrip())
            current = indent + word
        else:
            current = (current + " " + word) if current.strip() else (indent + word)
    if current.strip():
        lines.append(current.rstrip())
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
