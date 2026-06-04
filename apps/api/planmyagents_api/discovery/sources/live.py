"""Bounded live discovery connectors.

These connectors use search APIs or configured live directories to find candidate
evidence. They only emit unverified, non-routable candidates.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib import error, parse, request

from planmyagents_api.discovery.capability_index import get_default_capability_index
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate
from planmyagents_api.discovery.query import expand_discovery_query

SearchTransport = Callable[[str, str, str, int, float], list[dict[str, str]]]
FetchText = Callable[[str, float], str]


@dataclass(frozen=True)
class LiveWebSearchSource:
    """Search Brave/Tavily-style web APIs and infer non-routable candidates."""

    provider: str
    api_key: str
    source_id: str = "live_web_search"
    max_results: int = 8
    timeout_seconds: float = 8.0
    goal_hash: str = ""
    search_transport: SearchTransport | None = None

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        if not self.api_key:
            return []
        expanded = expand_discovery_query(capabilities=capabilities, task_description=task_description)
        candidates: list[DiscoveryCandidate] = []
        seen: set[str] = set()
        for query in _live_queries(
            capabilities=expanded.all_capabilities,
            task_description=expanded.normalized_description,
            family="agent provider mcp a2a openapi",
        ):
            for result in (self.search_transport or _search_api)(
                self.provider,
                self.api_key,
                query,
                self.max_results,
                self.timeout_seconds,
            ):
                candidate = _candidate_from_search_result(
                    result=result,
                    capabilities=expanded.all_capabilities,
                    task_description=expanded.normalized_description,
                    source_id=self.source_id,
                    goal_hash=self.goal_hash,
                )
                if candidate and candidate.id not in seen:
                    seen.add(candidate.id)
                    candidates.append(candidate)
        return candidates


@dataclass(frozen=True)
class GitHubCodeSearchSource:
    """Search GitHub code for MCP servers, A2A cards, and OpenAPI specs."""

    token: str = ""
    source_id: str = "github_code_search"
    max_results: int = 8
    timeout_seconds: float = 8.0
    goal_hash: str = ""
    search_transport: SearchTransport | None = None

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        if not self.token:
            return []
        expanded = expand_discovery_query(capabilities=capabilities, task_description=task_description)
        candidates: list[DiscoveryCandidate] = []
        seen: set[str] = set()
        for query in _live_queries(
            capabilities=expanded.all_capabilities,
            task_description=expanded.normalized_description,
            family="filename:json mcp OR a2a OR openapi",
        ):
            for item in (self.search_transport or _github_code_search)(
                "github_code",
                self.token,
                query,
                self.max_results,
                self.timeout_seconds,
            ):
                candidate = _candidate_from_search_result(
                    result=item,
                    capabilities=expanded.all_capabilities,
                    task_description=expanded.normalized_description,
                    source_id=self.source_id,
                    goal_hash=self.goal_hash,
                )
                if candidate and candidate.id not in seen:
                    seen.add(candidate.id)
                    candidates.append(candidate)
        return candidates


@dataclass(frozen=True)
class LiveMcpRegistrySource:
    api_key: str
    provider: str = "brave"
    source_id: str = "live_mcp_registry"
    max_results: int = 8
    timeout_seconds: float = 8.0
    goal_hash: str = ""
    search_transport: SearchTransport | None = None

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        return _search_family(
            provider=self.provider,
            api_key=self.api_key,
            family="mcp server registry model context protocol",
            source_id=self.source_id,
            provider_type="mcp_server",
            capabilities=capabilities,
            task_description=task_description,
            max_results=self.max_results,
            timeout_seconds=self.timeout_seconds,
            goal_hash=self.goal_hash,
            search_transport=self.search_transport,
        )


@dataclass(frozen=True)
class LiveA2AAgentCardSource:
    api_key: str
    provider: str = "brave"
    source_id: str = "live_a2a_agent_cards"
    max_results: int = 8
    timeout_seconds: float = 8.0
    goal_hash: str = ""
    search_transport: SearchTransport | None = None

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        return _search_family(
            provider=self.provider,
            api_key=self.api_key,
            family="a2a agent card agent.json",
            source_id=self.source_id,
            provider_type="a2a_agent",
            capabilities=capabilities,
            task_description=task_description,
            max_results=self.max_results,
            timeout_seconds=self.timeout_seconds,
            goal_hash=self.goal_hash,
            search_transport=self.search_transport,
        )


@dataclass(frozen=True)
class LiveOpenApiSpecSource:
    api_key: str
    provider: str = "brave"
    source_id: str = "live_openapi_specs"
    max_results: int = 8
    timeout_seconds: float = 8.0
    goal_hash: str = ""
    search_transport: SearchTransport | None = None

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        return _search_family(
            provider=self.provider,
            api_key=self.api_key,
            family="openapi swagger api docs",
            source_id=self.source_id,
            provider_type="api_provider",
            capabilities=capabilities,
            task_description=task_description,
            max_results=self.max_results,
            timeout_seconds=self.timeout_seconds,
            goal_hash=self.goal_hash,
            search_transport=self.search_transport,
        )


@dataclass(frozen=True)
class LiveVendorDocsSource:
    api_key: str
    provider: str = "brave"
    source_id: str = "live_vendor_docs"
    max_results: int = 8
    timeout_seconds: float = 8.0
    goal_hash: str = ""
    search_transport: SearchTransport | None = None

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        return _search_family(
            provider=self.provider,
            api_key=self.api_key,
            family="api documentation vendor docs developer platform",
            source_id=self.source_id,
            provider_type="api_provider",
            capabilities=capabilities,
            task_description=task_description,
            max_results=self.max_results,
            timeout_seconds=self.timeout_seconds,
            goal_hash=self.goal_hash,
            search_transport=self.search_transport,
        )


@dataclass(frozen=True)
class LiveAgentMarketplaceSource:
    api_key: str
    provider: str = "brave"
    source_id: str = "live_agent_marketplaces"
    max_results: int = 8
    timeout_seconds: float = 8.0
    goal_hash: str = ""
    search_transport: SearchTransport | None = None

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        return _search_family(
            provider=self.provider,
            api_key=self.api_key,
            family="ai agent marketplace agent directory tools",
            source_id=self.source_id,
            provider_type="ai_agent",
            capabilities=capabilities,
            task_description=task_description,
            max_results=self.max_results,
            timeout_seconds=self.timeout_seconds,
            goal_hash=self.goal_hash,
            search_transport=self.search_transport,
        )


@dataclass(frozen=True)
class LiveUrlDirectorySource:
    """Fetch configured live directories/specs and infer candidate families."""

    urls: list[str]
    provider_type: str
    source_id: str
    timeout_seconds: float = 8.0
    goal_hash: str = ""
    fetch_text: FetchText | None = None

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        expanded = expand_discovery_query(capabilities=capabilities, task_description=task_description)
        candidates: list[DiscoveryCandidate] = []
        for url in self.urls:
            text = (self.fetch_text or _fetch_text)(url, self.timeout_seconds)
            if not text:
                continue
            candidate = _candidate_from_text(
                url=url,
                text=text,
                capabilities=expanded.all_capabilities,
                task_description=expanded.normalized_description,
                source_id=self.source_id,
                provider_type=self.provider_type,
                goal_hash=self.goal_hash,
            )
            if candidate:
                candidates.append(candidate)
        return candidates


def _search_family(
    *,
    provider: str,
    api_key: str,
    family: str,
    source_id: str,
    provider_type: str,
    capabilities: set[str],
    task_description: str,
    max_results: int,
    timeout_seconds: float,
    goal_hash: str,
    search_transport: SearchTransport | None,
) -> list[DiscoveryCandidate]:
    if not api_key:
        return []
    expanded = expand_discovery_query(capabilities=capabilities, task_description=task_description)
    candidates: list[DiscoveryCandidate] = []
    seen: set[str] = set()
    for query in _live_queries(
        capabilities=expanded.all_capabilities,
        task_description=expanded.normalized_description,
        family=family,
    ):
        for result in (search_transport or _search_api)(
            provider,
            api_key,
            query,
            max_results,
            timeout_seconds,
        ):
            candidate = _candidate_from_search_result(
                result=result,
                capabilities=expanded.all_capabilities,
                task_description=expanded.normalized_description,
                source_id=source_id,
                goal_hash=goal_hash,
                provider_type=provider_type,
            )
            if candidate and candidate.id not in seen:
                seen.add(candidate.id)
                candidates.append(candidate)
    return candidates


def _search_api(
    provider: str, api_key: str, query: str, max_results: int, timeout_seconds: float
) -> list[dict[str, str]]:
    if provider == "brave":
        return _brave_search(api_key, query, max_results, timeout_seconds)
    if provider == "tavily":
        return _tavily_search(api_key, query, max_results, timeout_seconds)
    return []


def _brave_search(
    api_key: str, query: str, max_results: int, timeout_seconds: float
) -> list[dict[str, str]]:
    url = "https://api.search.brave.com/res/v1/web/search?" + parse.urlencode(
        {"q": query, "count": max_results}
    )
    req = request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "PlanMyAgentsDiscovery/0.1",
            "X-Subscription-Token": api_key,
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return []
    results = ((payload.get("web") or {}).get("results") or []) if isinstance(payload, dict) else []
    return [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("description") or ""),
        }
        for item in results
        if isinstance(item, dict)
    ]


def _tavily_search(
    api_key: str, query: str, max_results: int, timeout_seconds: float
) -> list[dict[str, str]]:
    body = json.dumps({"api_key": api_key, "query": query, "max_results": max_results}).encode()
    req = request.Request(
        "https://api.tavily.com/search",
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "PlanMyAgentsDiscovery/0.1",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return []
    results = payload.get("results", []) if isinstance(payload, dict) else []
    return [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("content") or ""),
        }
        for item in results
        if isinstance(item, dict)
    ]


def _github_code_search(
    _provider: str, token: str, query: str, max_results: int, timeout_seconds: float
) -> list[dict[str, str]]:
    url = "https://api.github.com/search/code?" + parse.urlencode(
        {"q": query, "per_page": max_results}
    )
    req = request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PlanMyAgentsDiscovery/0.1",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return []
    items = payload.get("items", []) if isinstance(payload, dict) else []
    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        repo = item.get("repository") if isinstance(item.get("repository"), dict) else {}
        html_url = str(item.get("html_url") or repo.get("html_url") or "")
        name = str(repo.get("full_name") or item.get("name") or "")
        results.append(
            {
                "title": name,
                "url": html_url,
                "snippet": f"{item.get('path', '')} {repo.get('description', '')}",
            }
        )
    return results


def _fetch_text(url: str, timeout_seconds: float) -> str:
    req = request.Request(
        url,
        headers={
            "Accept": "text/plain,text/html,application/json",
            "User-Agent": "PlanMyAgentsDiscovery/0.1",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return response.read(200_000).decode("utf-8", errors="ignore")
    except (OSError, TimeoutError, error.URLError):
        return ""


def _candidate_from_search_result(
    *,
    result: dict[str, str],
    capabilities: set[str],
    task_description: str,
    source_id: str,
    goal_hash: str,
    provider_type: str = "",
) -> DiscoveryCandidate | None:
    url = result.get("url", "")
    text = f"{result.get('title', '')} {result.get('snippet', '')} {url}"
    return _candidate_from_text(
        url=url,
        text=text,
        capabilities=capabilities,
        task_description=task_description,
        source_id=source_id,
        provider_type=provider_type or _provider_type(text),
        goal_hash=goal_hash,
        title=result.get("title", ""),
    )


def _candidate_from_text(
    *,
    url: str,
    text: str,
    capabilities: set[str],
    task_description: str,
    source_id: str,
    provider_type: str,
    goal_hash: str,
    title: str = "",
) -> DiscoveryCandidate | None:
    title = title or _title(text) or parse.urlparse(url).netloc or url
    inferred_capabilities = _capabilities_from_text(text, capabilities, task_description)
    if not inferred_capabilities:
        return None
    raw = {
        "id": title,
        "display_name": title,
        "vendor": parse.urlparse(url).netloc or title,
        "vendor_url": url,
        "provider_type": provider_type,
        "verification_status": "unverified",
        "evidence_url": url,
        "capabilities": [
            {
                "id": capability,
                "confidence": 0.42,
                "notes": "Capability inferred from bounded live discovery evidence; requires review.",
            }
            for capability in sorted(inferred_capabilities)
        ],
    }
    try:
        return normalize_candidate(raw, source=source_id, goal_hash=goal_hash)
    except (CandidateNormalizationError, TypeError, ValueError):
        return None


def _live_queries(capabilities: set[str], task_description: str, family: str) -> list[str]:
    terms = " ".join(sorted(capabilities)) if capabilities else " ".join(_terms(task_description)[:8])
    roots = [
        f"{terms} {family}",
        f"{task_description} {family}",
    ]
    return [query.strip() for query in roots if query.strip()]


def _provider_type(text: str) -> str:
    lowered = text.lower()
    if "mcp" in lowered or "model context protocol" in lowered:
        return "mcp_server"
    if "a2a" in lowered or "agent card" in lowered:
        return "a2a_agent"
    if "agent" in lowered:
        return "ai_agent"
    return "api_provider"


def _capabilities_from_text(
    text: str, requested_capabilities: set[str], task_description: str
) -> set[str]:
    """Map a live search hit's text to registry capability ids.

    Uses ``CapabilityIndex.infer_from_text`` (embedding-similarity over
    the live registry) instead of the deleted ``CAPABILITY_SYNONYMS``
    keyword table. Also passes-through any of the explicitly requested
    capabilities under either of two conditions:

    1. The slug appears verbatim in the hit text (covers the common case
       where a vendor page literally mentions the capability id, e.g.
       "MCP server for travel_search and fare comparison").
    2. The slug's tokenised form (underscores → spaces, then split into
       words) shares any term with the hit's tokenised form (covers
       "travel search agents" → "travel_search").

    Both rules are needed because the tokeniser's character class
    intentionally keeps underscored slugs as single tokens for capability
    matching elsewhere; that's wrong here, where we want to recognise
    multi-word phrasing of a slug.
    """

    combined_text = f"{text} {task_description}".strip()
    matches: set[str] = set(
        get_default_capability_index().infer_from_text(combined_text)
    )

    lowered_text = combined_text.lower()
    text_terms = {_stem(term) for term in _terms(combined_text)}
    for capability in requested_capabilities:
        if not capability:
            continue
        if capability.lower() in lowered_text:
            matches.add(capability)
            continue
        capability_terms = {
            _stem(term) for term in _terms(capability.replace("_", " "))
        }
        if not capability_terms or text_terms & capability_terms:
            matches.add(capability)
    return matches


def _title(text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    return re.sub(r"\s+", " ", first_line).strip()[:120]


def _terms(value: str) -> list[str]:
    return [
        _stem(term)
        for term in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", value.lower())
        if len(term) > 2
    ]


def _stem(value: str) -> str:
    if value.endswith("ing") and len(value) > 5:
        return value[:-3]
    if value.endswith("ies") and len(value) > 5:
        return f"{value[:-3]}y"
    if value.endswith("s") and len(value) > 4:
        return value[:-1]
    return value
