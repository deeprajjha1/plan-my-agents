"""Event-driven GitHub discovery source.

Polls ``api.github.com/search/repositories`` with the ``pushed:>YYYY-MM-DD``
qualifier to surface repos that were updated *recently* and match agent /
MCP / A2A keywords. This is the GitHub half of the event-driven discovery
firehose — it complements ``GitHubCodeSearchSource`` (capability-query
driven) by catching new launches by recency rather than by content.

Why this source matters:

* Most new MCPs / A2A agents / agent frameworks are pushed to GitHub
  before they are announced anywhere else. This catches them within
  hours of the push, not weeks later when an aggregator notices.
* Free with a `GITHUB_TOKEN` (5,000 req/hr unauthenticated → 1 req/min,
  and unauthenticated quickly hits 403; with a token, plenty of headroom).
* Repo metadata (name, description, owner, topics, stars, language)
  arrives in the response, so we don't need a follow-up fetch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib import error, parse, request

from planmyagents_api.discovery.capability_index import infer_capabilities_for_source
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate

DEFAULT_BASE_URL = "https://api.github.com/search/repositories"

USER_AGENT = (
    "agent-manager-discovery/0.1 "
    "(+https://github.com/deepraj-jha/agent-manager; first-party Tier-1 source)"
)

# Each entry produces one search query. We deliberately keep the queries
# narrow (specific terms + topics) so we don't drown in unrelated AI repos.
DEFAULT_QUERIES: tuple[str, ...] = (
    "mcp in:description",
    '"model context protocol" in:description',
    "a2a agent in:description",
    'topic:mcp',
    'topic:mcp-server',
    'topic:a2a',
    'topic:a2a-agent',
    'topic:agent-framework',
)


@dataclass(frozen=True)
class GitHubRecentlyPushedSource:
    """Pulls recently-pushed agent/MCP/A2A repos via GitHub repo search."""

    token: str = ""
    base_url: str = DEFAULT_BASE_URL
    source_id: str = "github_recently_pushed"
    queries: tuple[str, ...] = DEFAULT_QUERIES
    pushed_within_days: int = 30
    per_query_limit: int = 25
    timeout_seconds: float = 10.0
    goal_hash: str = ""
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)
    # Filter out toy repos: the GitHub firehose has many forks and 0-star
    # demos. min_stars=2 keeps real launches without being too aggressive.
    min_stars: int = 2

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        if not self.token:
            # Unauthenticated GitHub search is essentially unusable
            # (rate-limited at <1 req/min and 403s frequently). We honestly
            # no-op here; the env-check tells the user how to get a token.
            return []

        cutoff = (
            datetime.now(UTC) - timedelta(days=self.pushed_within_days)
        ).strftime("%Y-%m-%d")

        seen_repos: set[str] = set()
        candidates: list[DiscoveryCandidate] = []
        for query in self.queries:
            full_query = f"{query} pushed:>{cutoff}"
            payload = self._fetch_search(full_query)
            if not isinstance(payload, dict):
                continue
            for item in payload.get("items", []) or []:
                if not isinstance(item, dict):
                    continue
                full_name = str(item.get("full_name") or "").lower()
                if not full_name or full_name in seen_repos:
                    continue
                if int(item.get("stargazers_count") or 0) < self.min_stars:
                    continue
                seen_repos.add(full_name)
                normalized = self._normalize(item)
                if normalized is None:
                    continue
                if capabilities and not normalized.supports_any(capabilities):
                    continue
                candidates.append(normalized)
        return candidates

    def _fetch_search(self, query: str) -> Any:
        params = {
            "q": query,
            "sort": "updated",
            "order": "desc",
            "per_page": str(self.per_query_limit),
        }
        url = f"{self.base_url}?{parse.urlencode(params)}"
        return _fetch_json(
            url,
            token=self.token,
            timeout_seconds=self.timeout_seconds,
        )

    def _normalize(self, item: dict[str, Any]) -> DiscoveryCandidate | None:
        full_name = str(item.get("full_name") or "").strip()
        if not full_name:
            return None
        owner_payload = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        owner = str(owner_payload.get("login") or full_name.split("/", 1)[0]).strip()
        name = full_name.split("/", 1)[-1]
        description = str(item.get("description") or "").strip()
        topics_payload = item.get("topics") if isinstance(item.get("topics"), list) else []
        topics = [str(t) for t in topics_payload if isinstance(t, str)]
        html_url = str(item.get("html_url") or f"https://github.com/{full_name}").strip()

        provider_type = _infer_provider_type(topics=topics, description=description, name=name)
        capabilities = _infer_capabilities(
            text=" ".join([name, description, " ".join(topics)]),
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        raw = {
            "id": full_name.replace("/", "--"),
            "display_name": name,
            "vendor": owner,
            "vendor_url": html_url,
            "provider_type": provider_type,
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            "docs": {"setup_url": html_url, "auth_method": ""},
            "evidence_url": html_url,
        }
        try:
            return normalize_candidate(
                raw,
                source=self.source_id,
                requested_capabilities=sorted(capabilities),
                goal_hash=self.goal_hash,
            )
        except (CandidateNormalizationError, TypeError, ValueError):
            return None


def _infer_provider_type(*, topics: list[str], description: str, name: str) -> str:
    """Classify the repo's protocol from topics + free text. Defaults to
    `ai_agent` because 'unknown' is not a valid provider_type and most
    agent framework repos that don't self-identify are still
    agent-shaped."""

    haystack = f"{' '.join(topics)} {description} {name}".lower()
    if "mcp" in haystack or "model context protocol" in haystack:
        return "mcp_server"
    if "a2a" in haystack or "agent-to-agent" in haystack or "agent.json" in haystack:
        return "a2a_agent"
    if "openapi" in haystack or "swagger" in haystack:
        return "api_provider"
    return "ai_agent"


def _infer_capabilities(*, text: str, extra: dict[str, set[str]]) -> set[str]:
    # Catch-all `semantic_search` preserved so we don't drop
    # agent-shaped GitHub repos whose READMEs happen not to mention any
    # specific capability keyword. A repo named `acme/customer-bot` is
    # still meaningful demand signal even if its tags don't trip a match.
    return infer_capabilities_for_source(
        text=text, extra=extra, fallback="semantic_search"
    )


def _fetch_json(url: str, *, token: str, timeout_seconds: float) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
    }
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return None
