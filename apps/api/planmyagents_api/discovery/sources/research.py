"""Live research discovery sources.

These sources are intentionally conservative. They collect candidate evidence
from public GitHub search and explicitly supplied URLs, then mark candidates as
unverified until a later promotion/benchmark step reviews them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from planmyagents_api.discovery.capability_index import get_default_capability_index
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate
from planmyagents_api.discovery.query import expand_discovery_query


@dataclass(frozen=True)
class GitHubResearchSource:
    """Search public GitHub repositories for MCP/A2A/agent candidates."""

    source_id: str = "github_research"
    max_results: int = 10
    timeout_seconds: float = 10.0
    token: str = ""
    goal_hash: str = ""

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        expanded = expand_discovery_query(capabilities=capabilities, task_description=task_description)
        candidates: list[DiscoveryCandidate] = []
        seen_ids: set[str] = set()
        for query in _research_queries(expanded.all_capabilities, expanded.normalized_description):
            for item in _github_search(
                query=query,
                max_results=self.max_results,
                timeout_seconds=self.timeout_seconds,
                token=self.token,
            ):
                candidate = _candidate_from_github_item(
                    item=item,
                    capabilities=expanded.all_capabilities,
                    task_description=expanded.normalized_description,
                    source_id=self.source_id,
                    goal_hash=self.goal_hash,
                )
                if candidate:
                    if candidate.id in seen_ids:
                        continue
                    seen_ids.add(candidate.id)
                    candidates.append(candidate)
        return candidates


@dataclass(frozen=True)
class UrlResearchSource:
    """Infer candidates from explicitly supplied public URLs."""

    urls: list[str]
    source_id: str = "url_research"
    timeout_seconds: float = 10.0
    goal_hash: str = ""

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        expanded = expand_discovery_query(capabilities=capabilities, task_description=task_description)
        candidates: list[DiscoveryCandidate] = []
        for url in self.urls:
            text = _fetch_text(url, timeout_seconds=self.timeout_seconds)
            if not text:
                continue
            candidate = _candidate_from_text(
                url=url,
                text=text,
                capabilities=expanded.all_capabilities,
                task_description=expanded.normalized_description,
                source_id=self.source_id,
                goal_hash=self.goal_hash,
            )
            if candidate:
                candidates.append(candidate)
        return candidates


def _github_search(
    *, query: str, max_results: int, timeout_seconds: float, token: str
) -> list[dict[str, Any]]:
    url = "https://api.github.com/search/repositories?" + parse.urlencode(
        {"q": query, "sort": "updated", "order": "desc", "per_page": max_results}
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "PlanMyAgentsDiscovery/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return []
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _fetch_text(url: str, *, timeout_seconds: float) -> str:
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


def _research_queries(capabilities: set[str], task_description: str) -> list[str]:
    terms = _terms(task_description)
    capability_terms = " ".join(sorted(capabilities)) if capabilities else " ".join(sorted(terms))
    query_roots = [
        f"{capability_terms} mcp server",
        f"{capability_terms} a2a agent",
        f"{capability_terms} agent card",
        f"{capability_terms} ai agent",
    ]
    return [query for query in query_roots if query.strip()]


def _candidate_from_github_item(
    *,
    item: dict[str, Any],
    capabilities: set[str],
    task_description: str,
    source_id: str,
    goal_hash: str,
) -> DiscoveryCandidate | None:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("name", "full_name", "description", "html_url", "topics")
    )
    html_url = str(item.get("html_url") or "")
    raw = _raw_candidate(
        candidate_id=str(item.get("full_name") or item.get("name") or ""),
        display_name=str(item.get("full_name") or item.get("name") or ""),
        vendor=str((item.get("owner") or {}).get("login") or item.get("full_name") or ""),
        vendor_url=html_url,
        text=text,
        capabilities=capabilities,
        task_description=task_description,
        evidence_url=html_url,
    )
    if not raw:
        return None
    return _normalize_raw_candidate(raw, source_id=source_id, goal_hash=goal_hash)


def _candidate_from_text(
    *,
    url: str,
    text: str,
    capabilities: set[str],
    task_description: str,
    source_id: str,
    goal_hash: str,
) -> DiscoveryCandidate | None:
    title = _title(text) or parse.urlparse(url).netloc or url
    raw = _raw_candidate(
        candidate_id=title,
        display_name=title,
        vendor=parse.urlparse(url).netloc or title,
        vendor_url=url,
        text=text,
        capabilities=capabilities,
        task_description=task_description,
        evidence_url=url,
    )
    if not raw:
        return None
    return _normalize_raw_candidate(raw, source_id=source_id, goal_hash=goal_hash)


def _raw_candidate(
    *,
    candidate_id: str,
    display_name: str,
    vendor: str,
    vendor_url: str,
    text: str,
    capabilities: set[str],
    task_description: str,
    evidence_url: str,
) -> dict[str, Any] | None:
    provider_type = _provider_type(text)
    inferred_capabilities = _capabilities_from_text(text, capabilities, task_description)
    if not inferred_capabilities:
        return None
    return {
        "id": candidate_id,
        "display_name": display_name,
        "vendor": vendor or display_name,
        "vendor_url": vendor_url,
        "provider_type": provider_type,
        "verification_status": "unverified",
        "evidence_url": evidence_url,
        "capabilities": [
            {
                "id": capability,
                "confidence": 0.45,
                "notes": "Capability inferred from live research evidence; requires review.",
            }
            for capability in sorted(inferred_capabilities)
        ],
    }


def _normalize_raw_candidate(
    raw: dict[str, Any], *, source_id: str, goal_hash: str
) -> DiscoveryCandidate | None:
    try:
        return normalize_candidate(raw, source=source_id, goal_hash=goal_hash)
    except (CandidateNormalizationError, TypeError, ValueError):
        return None


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
    """Map a GitHub research hit's text to registry capability ids.

    Mirror of ``live._capabilities_from_text`` — embedding-similarity
    over the live registry, plus a pass-through for any explicitly
    requested capability under either of two conditions:

    1. The slug appears verbatim in the hit text.
    2. The slug's tokenised form shares any term with the hit's text.

    See the live.py docstring for why both rules are needed.
    """

    combined_text = f"{text} {task_description}".strip()
    matches: set[str] = set(
        get_default_capability_index().infer_from_text(combined_text)
    )

    lowered_text = combined_text.lower()
    text_terms = _terms(combined_text)
    for capability in requested_capabilities:
        if not capability:
            continue
        if capability.lower() in lowered_text:
            matches.add(capability)
            continue
        capability_terms = _terms(capability.replace("_", " "))
        if not capability_terms or text_terms & capability_terms:
            matches.add(capability)
    return matches


def _title(text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _terms(value: str) -> set[str]:
    return {
        _stem(term)
        for term in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", value.lower())
        if len(term) > 2
    }


def _stem(value: str) -> str:
    if value.endswith("ing") and len(value) > 5:
        return value[:-3]
    if value.endswith("ies") and len(value) > 5:
        return f"{value[:-3]}y"
    if value.endswith("s") and len(value) > 4:
        return value[:-1]
    return value
