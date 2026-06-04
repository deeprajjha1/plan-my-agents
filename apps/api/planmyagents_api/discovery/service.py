"""Discovery service used by planner/router integration."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from planmyagents_api.discovery.constants import INTERNAL_MISSING_CAPABILITIES
from planmyagents_api.discovery.embeddings import Embedder, EmbedderError, embedder_from_env
from planmyagents_api.discovery.index import DiscoveryIndex
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, candidate_from_registry
from planmyagents_api.discovery.query import expand_discovery_query
from planmyagents_api.discovery.readiness import promotion_readiness
from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource
from planmyagents_api.discovery.sources.ai_directory import AiAgentDirectorySource
from planmyagents_api.discovery.sources.apis_guru import DEFAULT_LIST_URL as APIS_GURU_DEFAULT_URL
from planmyagents_api.discovery.sources.apis_guru import ApisGuruSource
from planmyagents_api.discovery.sources.base import DiscoverySource
from planmyagents_api.discovery.sources.github_recently_pushed import GitHubRecentlyPushedSource
from planmyagents_api.discovery.sources.hacker_news import HackerNewsAgentWatcherSource
from planmyagents_api.discovery.sources.json_source import JsonDiscoverySource
from planmyagents_api.discovery.sources.live import (
    GitHubCodeSearchSource,
    LiveA2AAgentCardSource,
    LiveAgentMarketplaceSource,
    LiveMcpRegistrySource,
    LiveOpenApiSpecSource,
    LiveUrlDirectorySource,
    LiveVendorDocsSource,
    LiveWebSearchSource,
)
from planmyagents_api.discovery.sources.mcp import McpCatalogSource
from planmyagents_api.discovery.sources.official_mcp_registry import (
    DEFAULT_REGISTRY_URL as OFFICIAL_MCP_REGISTRY_DEFAULT_URL,
)
from planmyagents_api.discovery.sources.official_mcp_registry import OfficialMcpRegistrySource
from planmyagents_api.discovery.sources.research import GitHubResearchSource, UrlResearchSource
from planmyagents_api.discovery.sources.static import StaticDiscoverySource
from planmyagents_api.discovery.sources.vendor_rss import VendorRssSource
from planmyagents_api.discovery.sources.web_doc import WebDocDiscoverySource
from planmyagents_api.discovery.store import PostgresDiscoveryStore, discovery_store_for_path
from planmyagents_api.planner.goal import GoalPlan
from planmyagents_api.registry.loader import load_registry


def build_discovery_index(
    *,
    capabilities: set[str],
    task_description: str,
    sources: list[DiscoverySource] | None = None,
    store_path: Path | str | None = None,
    load_store: bool = True,
    run_logger: Any = None,
    trigger: str = "batch",
) -> DiscoveryIndex:
    """Build an in-memory discovery index for a search request.

    If `run_logger` is None we resolve a default `DiscoveryRunLogger`
    from environment configuration (controlled by
    `PLANMYAGENTS_RUN_LOG_ENABLED` and `PLANMYAGENTS_RUN_LOG_STORE_PATH`).
    Pass `run_logger=False`-equivalent (a no-op logger) to suppress
    logging entirely; the helper accepts `None` only as a sentinel for
    "use default."
    """

    from planmyagents_api.discovery.run_log import DiscoveryRunLogger

    if run_logger is None:
        run_logger = DiscoveryRunLogger.default()
    expanded = expand_discovery_query(capabilities=capabilities, task_description=task_description)
    index = DiscoveryIndex()
    if store_path and load_store:
        index.ingest(discovery_store_for_path(store_path).load())
    index.ingest_sources(
        sources or default_discovery_sources(),
        capabilities=expanded.all_capabilities,
        task_description=expanded.normalized_description,
        run_logger=run_logger,
        trigger=trigger,
    )
    return index


def search_candidates(
    *,
    capabilities: set[str],
    task_description: str,
    sources: list[DiscoverySource] | None = None,
    store_path: Path | str | None = None,
    limit: int = 20,
    persist: bool = True,
    load_store: bool = True,
    include_stale: bool = True,
    stale_after_days: int = 30,
) -> dict[str, Any]:
    """Search discovered candidates and optionally persist the index."""

    expanded = expand_discovery_query(capabilities=capabilities, task_description=task_description)
    index = build_discovery_index(
        capabilities=capabilities,
        task_description=task_description,
        sources=sources,
        store_path=store_path,
        load_store=load_store,
    )
    if store_path and persist:
        discovery_store_for_path(store_path).save(index.all_candidates())

    results = index.search(
        capabilities=expanded.all_capabilities,
        task_description=expanded.normalized_description,
        limit=limit,
        include_stale=include_stale,
        stale_after_days=stale_after_days,
    )
    serialized_results = []
    for result in results:
        payload = result.to_json(stale_after_days=stale_after_days)
        payload["promotion_readiness"] = promotion_readiness(result.candidate)
        serialized_results.append(payload)

    return {
        "query": task_description,
        "normalized_query": expanded.normalized_description,
        "normalizations": expanded.normalizations,
        "requested_capabilities": sorted(capabilities),
        "inferred_capabilities": sorted(expanded.inferred_capabilities),
        "searched_capabilities": sorted(expanded.all_capabilities),
        "total_candidates": len(index.all_candidates()),
        "freshness_policy": {
            "include_stale": include_stale,
            "stale_after_days": stale_after_days,
        },
        "results": serialized_results,
    }


# Tool/skill text caps. We deliberately bound *per-tool* and *number-of-tools*
# rather than relying on the embedder's terminal truncation, because the
# truncation point falls in document order and we'd rather drop the 33rd
# tool than the first half of the second tool. With these limits the worst
# case for an MCP server with 32 tools is roughly:
#
#   3 lines of header (display_name + vendor + provider_type)
#   + ~10 capability lines
#   + 32 × (≤96 + ≤512 + ≤8) ≈ 19 KB
#
# which fits comfortably inside MAX_INPUT_CHARS_OPENAI (30 KB) and is
# truncated tail-first by MAX_INPUT_CHARS_OLLAMA (4 KB).
_MAX_TOOL_NAME_CHARS = 96
_MAX_TOOL_DESCRIPTION_CHARS = 512
_MAX_TOOLS_EMBEDDED = 32
# Cap a single capability note the same way so a chatty discoverer can't
# starve later capabilities or the tool block. 512 chars is enough for
# 4–6 sentences of justification text.
_MAX_CAPABILITY_NOTE_CHARS = 512


def candidate_text_for_embedding(candidate_payload: dict[str, Any]) -> str:
    """Compose the text we feed an embedder for a discovery candidate.

    Field order is significant: identifying fields go first, then capability
    declarations, then the *tool surface* (MCP tools / A2A skills) probed
    from the live server, then evidence.

    Why include tools / skills:
    - The same MCP server can be tagged ``general_research`` or
      ``web_scraping`` at the catalogue level but expose a tool literally
      named ``send_email``. Without the tool text in the embedding, a
      semantic query for "send transactional email" never reaches that
      server. The probed tool list is the highest-signal text we have for
      what the server can actually *do*.
    - We embed tool ``name + description`` (and ``input_schema.title``
      where present) but not the full schema. Schemas are JSON-ish noise
      that wastes the limited embedding window without improving recall.

    Per-element / per-list caps are enforced here rather than relying on
    the embedder's terminal truncation, so trimming happens in document
    order at well-known boundaries (drop the 33rd tool, never split tool
    #2 in half).
    """

    parts: list[str] = [
        str(candidate_payload.get("display_name") or ""),
        str(candidate_payload.get("vendor") or ""),
        str(candidate_payload.get("provider_type") or ""),
    ]
    capabilities = candidate_payload.get("capabilities") or []
    if isinstance(capabilities, list):
        for capability in capabilities:
            if isinstance(capability, dict):
                parts.append(str(capability.get("id") or ""))
                note = str(capability.get("notes") or "")
                if note:
                    parts.append(note[:_MAX_CAPABILITY_NOTE_CHARS])
            elif isinstance(capability, str):
                parts.append(capability)

    parts.extend(_tool_surface_parts(candidate_payload))

    parts.append(str(candidate_payload.get("evidence_url") or ""))
    return " ".join(part for part in parts if part).strip()


def _tool_surface_parts(candidate_payload: dict[str, Any]) -> list[str]:
    """Return the text fragments contributed by probed tools and A2A skills.

    Both ``tools`` (MCP-style) and ``skills`` (A2A-style) live on the same
    candidate today; they're interchangeable from the embedding's point of
    view — both describe a callable surface in natural language. We merge
    them, cap the count, and emit ``name`` + truncated ``description`` per
    entry. ``input_schema.title`` is included when present because it is
    often the only place a tool exposes a human-readable parameter label
    (e.g. "Recipient email address") that helps the embedder bridge from
    the user's wording to the tool's wording.

    Defensive parsing: missing fields, non-dict entries, and non-string
    name/description values are silently skipped — discovery payloads are
    union-shaped across many sources and the embedder must never crash
    on a malformed catalogue row.
    """

    tools_raw = candidate_payload.get("tools") or []
    skills_raw = candidate_payload.get("skills") or []
    callable_entries: list[dict[str, Any]] = []
    for collection in (tools_raw, skills_raw):
        if not isinstance(collection, list):
            continue
        for entry in collection:
            if isinstance(entry, dict):
                callable_entries.append(entry)
            if len(callable_entries) >= _MAX_TOOLS_EMBEDDED:
                break
        if len(callable_entries) >= _MAX_TOOLS_EMBEDDED:
            break

    parts: list[str] = []
    for entry in callable_entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        parts.append(name[:_MAX_TOOL_NAME_CHARS])
        description = str(entry.get("description") or "").strip()
        if description:
            parts.append(description[:_MAX_TOOL_DESCRIPTION_CHARS])
        # `inputSchema` (MCP) or `input_schema` (our normalised form) —
        # we already accept both shapes elsewhere in the pipeline so the
        # embedding text honours both too. Only the `title` field is
        # interesting; properties/required are noise without context.
        schema = (
            entry.get("input_schema")
            or entry.get("inputSchema")
            or {}
        )
        if isinstance(schema, dict):
            title = str(schema.get("title") or "").strip()
            if title:
                parts.append(title[:_MAX_TOOL_NAME_CHARS])
    return parts


def search_candidates_with_embeddings(
    *,
    store_url: str,
    query: str,
    capability: str | None = None,
    provider_type: str | None = None,
    limit: int = 20,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    """Embedding-aware candidate search against a Postgres discovery store.

    Falls back to in-memory text search when the store is not Postgres or when
    the configured embedder is unavailable, so this is a safe single entry
    point for callers (FastAPI, CLI) that want to use embeddings *if available*.
    """

    embedder = embedder or embedder_from_env()
    is_postgres = store_url.startswith(("postgresql://", "postgres://"))
    store = discovery_store_for_path(store_url)

    # `discovery_store_for_path` returns a `RoutingDiscoveryStore` facade
    # that wraps the actual backend (Postgres / SQLite / JSON) so the
    # public store contract can route api_provider rows to a separate
    # table. The pgvector cosine search lives only on the underlying
    # PostgresDiscoveryStore, so we have to unwrap the routing facade
    # before doing the isinstance check — without this unwrap the check
    # always fails and we silently degrade to in-memory text search,
    # which is exactly the bug the audit flagged.
    backend_store = getattr(store, "agentic_store", store)

    if is_postgres and isinstance(backend_store, PostgresDiscoveryStore):
        try:
            vector = embedder.encode(query)
        except EmbedderError:
            vector = []
        if vector:
            rows = backend_store.search_by_embedding(
                vector=vector,
                capability=capability,
                provider_type=provider_type,
                limit=limit,
            )
            results = []
            for row in rows:
                try:
                    candidate = candidate_from_registry(row["candidate"])
                except (CandidateNormalizationError, TypeError, ValueError):
                    continue
                payload = candidate.to_public_summary()
                payload["embedding_similarity"] = round(row["similarity"], 4)
                payload["promotion_readiness"] = promotion_readiness(candidate)
                results.append(payload)
            if results:
                return {
                    "query": query,
                    "store": store_url,
                    "backend": "postgres+pgvector",
                    "embedder": embedder.name,
                    "capability": capability,
                    "provider_type": provider_type,
                    "results": results,
                }

    # Fallback: load the store and run the in-memory text/capability search.
    candidates = store.load()
    index = DiscoveryIndex()
    index.ingest(candidates)
    capabilities = {capability} if capability else set()
    text_results = index.search(
        capabilities=capabilities,
        task_description=query,
        limit=limit,
    )
    results = []
    for result in text_results:
        if provider_type and result.candidate.provider_type != provider_type:
            continue
        payload = result.to_json()
        payload["promotion_readiness"] = promotion_readiness(result.candidate)
        results.append(payload)
    return {
        "query": query,
        "store": store_url,
        "backend": "in_memory_text",
        "embedder": embedder.name,
        "capability": capability,
        "provider_type": provider_type,
        "results": results,
    }


def enrich_registry_from_unsupported_plan(
    *,
    goal: str,
    plan: GoalPlan,
    registry_path: Path,
    sources: list[DiscoverySource] | None = None,
    persist_registry: bool = False,
) -> dict[str, Any] | None:
    """Find non-routable discovered providers for a failed plan's missing capabilities."""

    missing = sorted(set(plan.missing_capabilities) - INTERNAL_MISSING_CAPABILITIES)
    if not missing:
        return None

    registry = load_registry(registry_path)
    existing_ids = {agent["id"] for agent in registry.get("agents", [])}
    discovered = registry.setdefault("discovered_agents", [])
    expanded = expand_discovery_query(capabilities=set(missing), task_description=goal)

    index = DiscoveryIndex()
    index.ingest([candidate_from_registry(agent) for agent in discovered])
    index.ingest_sources(
        sources
        or default_discovery_sources(
            goal_hash=_hash_goal(goal), static_source_id="unsupported_goal_pipeline"
        ),
        capabilities=expanded.all_capabilities,
        task_description=expanded.normalized_description,
    )

    matched_results = index.search(
        capabilities=expanded.all_capabilities,
        task_description=expanded.normalized_description,
        limit=100,
    )
    matched_candidates = [
        result.candidate for result in matched_results if result.candidate.id not in existing_ids
    ]
    existing_discovered_ids = {agent["id"] for agent in discovered}

    added = sorted(
        {
            candidate.id
            for candidate in matched_candidates
            if candidate.id not in existing_discovered_ids
        }
    )
    updated = sorted(
        {
            candidate.id
            for candidate in matched_candidates
            if candidate.id in existing_discovered_ids
        }
    )

    if persist_registry and (added or updated):
        registry["discovered_agents"] = [
            candidate.to_registry_json()
            for candidate in index.all_candidates()
            if candidate.id not in existing_ids
        ]
        registry_path.write_text(json.dumps(registry, indent=2) + "\n")

    return {
        "status": "enriched" if persist_registry and (added or updated) else "discovered",
        "persisted_to_registry": persist_registry,
        "source_coverage": _source_coverage(missing=missing, goal=goal),
        "missing_capabilities": missing,
        "searched_capabilities": sorted(expanded.all_capabilities),
        "normalizations": expanded.normalizations,
        "added_candidates": added,
        "updated_candidates": updated,
        "will_fail": True,
        "will_fail_reasons": [
            "Discovered candidates are not routable until API keys, adapters, and benchmarks are complete.",
            "Discovery ranks MCP servers, A2A agents, and AI agents before plain API providers.",
        ],
        "candidates": [result.to_json() for result in matched_results],
    }


def _hash_goal(goal: str) -> str:
    return hashlib.sha256(goal.strip().encode("utf-8")).hexdigest()


def _source_coverage(*, missing: list[str], goal: str) -> dict[str, Any]:
    capability_query = " ".join(missing)
    return {
        "mode": "configured_sources_only",
        "live_web_search": "not_implemented",
        "note": (
            "The local demo searches configured manifests and the local discovery store. "
            "It searches configured manifests and the local discovery store by default. "
            "Live GitHub/URL research is available through the research job or explicit CLI flags."
        ),
        "suggested_live_search_queries": [
            f"MCP server {capability_query}",
            f"A2A agent card {capability_query}",
            f"AI agent {capability_query}",
            f"GitHub MCP A2A {' '.join(goal.lower().split()[:8])}",
        ],
    }


def default_discovery_sources(
    *, goal_hash: str = "", static_source_id: str = "static_seed"
) -> list[DiscoverySource]:
    """Build default sources from environment configuration."""

    sources: list[DiscoverySource] = [
        StaticDiscoverySource(source_id=static_source_id, goal_hash=goal_hash)
    ]
    json_locations = _split_locations(os.getenv("PLANMYAGENTS_DISCOVERY_JSON_SOURCES", ""))
    mcp_locations = _split_locations(os.getenv("PLANMYAGENTS_DISCOVERY_MCP_CATALOGS", ""))
    a2a_locations = _split_locations(os.getenv("PLANMYAGENTS_DISCOVERY_A2A_CARDS", ""))
    ai_locations = _split_locations(os.getenv("PLANMYAGENTS_DISCOVERY_AI_DIRECTORIES", ""))
    web_doc_locations = _split_locations(os.getenv("PLANMYAGENTS_DISCOVERY_WEB_DOCS", ""))
    research_urls = _split_locations(os.getenv("PLANMYAGENTS_DISCOVERY_RESEARCH_URLS", ""))
    mcp_live_urls = _split_locations(os.getenv("PLANMYAGENTS_DISCOVERY_LIVE_MCP_URLS", ""))
    a2a_live_urls = _split_locations(os.getenv("PLANMYAGENTS_DISCOVERY_LIVE_A2A_URLS", ""))
    openapi_live_urls = _split_locations(
        os.getenv("PLANMYAGENTS_DISCOVERY_LIVE_OPENAPI_URLS", "")
    )
    vendor_doc_live_urls = _split_locations(
        os.getenv("PLANMYAGENTS_DISCOVERY_LIVE_VENDOR_DOC_URLS", "")
    )
    marketplace_live_urls = _split_locations(
        os.getenv("PLANMYAGENTS_DISCOVERY_LIVE_MARKETPLACE_URLS", "")
    )
    github_research_enabled = os.getenv("PLANMYAGENTS_DISCOVERY_GITHUB_RESEARCH", "").lower() in {
        "1",
        "true",
        "yes",
    }
    github_code_enabled = _env_enabled("PLANMYAGENTS_DISCOVERY_GITHUB_CODE_SEARCH")
    brave_key = os.getenv("BRAVE_SEARCH_API_KEY", "")
    tavily_key = os.getenv("TAVILY_API_KEY", "")
    live_search_provider = os.getenv("PLANMYAGENTS_DISCOVERY_LIVE_SEARCH_PROVIDER", "brave").lower()
    live_search_key = tavily_key if live_search_provider == "tavily" else brave_key

    if json_locations:
        sources.append(JsonDiscoverySource(json_locations, goal_hash=goal_hash))
    if mcp_locations:
        sources.append(McpCatalogSource(mcp_locations, goal_hash=goal_hash))
    if a2a_locations:
        sources.append(A2AAgentCardSource(a2a_locations, goal_hash=goal_hash))
    if ai_locations:
        sources.append(AiAgentDirectorySource(ai_locations, goal_hash=goal_hash))
    if web_doc_locations:
        sources.append(WebDocDiscoverySource(web_doc_locations, goal_hash=goal_hash))
    if github_research_enabled:
        sources.append(
            GitHubResearchSource(
                token=os.getenv("GITHUB_TOKEN", ""),
                goal_hash=goal_hash,
            )
        )
    if _env_enabled("PLANMYAGENTS_DISCOVERY_BRAVE_SEARCH"):
        sources.append(
            LiveWebSearchSource(
                provider="brave",
                api_key=brave_key,
                source_id="brave_search",
                goal_hash=goal_hash,
            )
        )
    if _env_enabled("PLANMYAGENTS_DISCOVERY_TAVILY_SEARCH"):
        sources.append(
            LiveWebSearchSource(
                provider="tavily",
                api_key=tavily_key,
                source_id="tavily_search",
                goal_hash=goal_hash,
            )
        )
    if github_code_enabled:
        sources.append(
            GitHubCodeSearchSource(token=os.getenv("GITHUB_TOKEN", ""), goal_hash=goal_hash)
        )
    if _env_enabled("PLANMYAGENTS_DISCOVERY_LIVE_MCP_REGISTRIES"):
        sources.append(
            LiveMcpRegistrySource(
                provider=live_search_provider,
                api_key=live_search_key,
                goal_hash=goal_hash,
            )
        )
    if _env_enabled("PLANMYAGENTS_DISCOVERY_LIVE_A2A_CARDS"):
        sources.append(
            LiveA2AAgentCardSource(
                provider=live_search_provider,
                api_key=live_search_key,
                goal_hash=goal_hash,
            )
        )
    if _env_enabled("PLANMYAGENTS_DISCOVERY_LIVE_OPENAPI_SPECS"):
        sources.append(
            LiveOpenApiSpecSource(
                provider=live_search_provider,
                api_key=live_search_key,
                goal_hash=goal_hash,
            )
        )
    if _env_enabled("PLANMYAGENTS_DISCOVERY_LIVE_VENDOR_DOCS"):
        sources.append(
            LiveVendorDocsSource(
                provider=live_search_provider,
                api_key=live_search_key,
                goal_hash=goal_hash,
            )
        )
    if _env_enabled("PLANMYAGENTS_DISCOVERY_LIVE_AGENT_MARKETPLACES"):
        sources.append(
            LiveAgentMarketplaceSource(
                provider=live_search_provider,
                api_key=live_search_key,
                goal_hash=goal_hash,
            )
        )
    if mcp_live_urls:
        sources.append(
            LiveUrlDirectorySource(
                mcp_live_urls,
                provider_type="mcp_server",
                source_id="live_mcp_url_directory",
                goal_hash=goal_hash,
            )
        )
    if a2a_live_urls:
        sources.append(
            LiveUrlDirectorySource(
                a2a_live_urls,
                provider_type="a2a_agent",
                source_id="live_a2a_url_directory",
                goal_hash=goal_hash,
            )
        )
    if openapi_live_urls:
        sources.append(
            LiveUrlDirectorySource(
                openapi_live_urls,
                provider_type="api_provider",
                source_id="live_openapi_url_directory",
                goal_hash=goal_hash,
            )
        )
    if vendor_doc_live_urls:
        sources.append(
            LiveUrlDirectorySource(
                vendor_doc_live_urls,
                provider_type="api_provider",
                source_id="live_vendor_doc_url_directory",
                goal_hash=goal_hash,
            )
        )
    if marketplace_live_urls:
        sources.append(
            LiveUrlDirectorySource(
                marketplace_live_urls,
                provider_type="ai_agent",
                source_id="live_marketplace_url_directory",
                goal_hash=goal_hash,
            )
        )
    if research_urls:
        sources.append(UrlResearchSource(research_urls, goal_hash=goal_hash))

    # Tier-1 first-party sources. Default-ON because they are unauthenticated
    # and free; opt out by setting PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY=false
    # or PLANMYAGENTS_DISCOVERY_APIS_GURU=false (e.g. for offline tests).
    if _env_disabled_default_true("PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY"):
        sources.append(
            OfficialMcpRegistrySource(
                base_url=os.getenv(
                    "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY_URL",
                    OFFICIAL_MCP_REGISTRY_DEFAULT_URL,
                ),
                goal_hash=goal_hash,
            )
        )
    if _env_disabled_default_true("PLANMYAGENTS_DISCOVERY_APIS_GURU"):
        sources.append(
            ApisGuruSource(
                list_url=os.getenv(
                    "PLANMYAGENTS_DISCOVERY_APIS_GURU_URL",
                    APIS_GURU_DEFAULT_URL,
                ),
                goal_hash=goal_hash,
            )
        )

    # Event-driven Tier-1 sources. Default-ON (no key for HN/RSS, GitHub
    # source no-ops without a token).
    if _env_disabled_default_true("PLANMYAGENTS_DISCOVERY_HACKER_NEWS"):
        sources.append(HackerNewsAgentWatcherSource(goal_hash=goal_hash))
    if _env_disabled_default_true("PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED"):
        sources.append(
            GitHubRecentlyPushedSource(
                token=os.getenv("GITHUB_TOKEN", ""),
                goal_hash=goal_hash,
            )
        )
    if _env_disabled_default_true("PLANMYAGENTS_DISCOVERY_VENDOR_RSS"):
        feeds = _load_curated_rss_feeds()
        if feeds:
            sources.append(VendorRssSource(feed_urls=feeds, goal_hash=goal_hash))

    return sources


def _load_curated_rss_feeds() -> list[str]:
    """Load the version-controlled vendor RSS seed list. Returns an empty
    list (and no source is added) if the file is missing or malformed —
    the caller already guards on that."""

    here = Path(__file__).resolve()
    # The seed file lives at packages/discovery/sources/vendor_rss_feeds.json
    # at the repo root. Walk up until we find the `packages` sibling.
    for parent in [here, *here.parents][:7]:
        candidate = parent / "packages" / "discovery" / "sources" / "vendor_rss_feeds.json"
        if candidate.exists():
            try:
                payload = json.loads(candidate.read_text())
            except (OSError, json.JSONDecodeError):
                return []
            entries = payload.get("feeds") if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                return []
            return [
                str(entry["url"])
                for entry in entries
                if isinstance(entry, dict) and entry.get("url")
            ]
    return []


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


def _env_disabled_default_true(name: str) -> bool:
    """Inverse of _env_enabled: true unless explicitly set to false/0/no.

    Used for Tier-1 sources that we want enabled by default since they are
    unauthenticated and free.
    """

    return os.getenv(name, "").lower() not in {"0", "false", "no", "off"}


def _split_locations(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
