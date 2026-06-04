"""Hacker News event-driven discovery source.

Polls the public Firebase API at ``hacker-news.firebaseio.com`` (free, no
auth, very stable since 2014) and emits :class:`DiscoveryCandidate` records
for stories whose title or text mentions agents/MCP/A2A/LLM tooling.

Why this source matters:

* Agents and MCP servers are routinely launched via Show HN posts. This
  catches them within hours of the announcement.
* Zero cost, no API key.
* Each emitted candidate carries the HN story URL as its evidence URL so
  the team can manually verify before promotion.

Event-driven model: this is one of three "where do agents get announced"
firehoses in the discovery design (see sprint.md → Real-Time Per-Subtask
Discovery). The other two are GitHub recently-pushed and vendor RSS.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib import error, parse, request

from planmyagents_api.discovery.agent_classifier import (
    ChatClient,
    classify_candidate,
)
from planmyagents_api.discovery.capability_index import infer_capabilities_for_source
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate

DEFAULT_BASE_URL = "https://hacker-news.firebaseio.com/v0"

# Conservative keyword set. We deliberately don't match "AI" alone (too noisy
# — most HN front-page stories mention AI nowadays). We require explicit
# agent / protocol / tool-use signals.
DEFAULT_KEYWORDS = (
    "mcp",
    "model context protocol",
    "a2a agent",
    "agent-to-agent",
    "ai agent",
    "llm agent",
    "autonomous agent",
    "agent framework",
    "agent platform",
    "tool use",
    "tool-use",
    "function calling",
    "openapi spec",
    "agent.json",
    "agent card",
)

USER_AGENT = (
    "agent-manager-discovery/0.1 "
    "(+https://github.com/deepraj-jha/agent-manager; first-party Tier-1 source)"
)

# Compiled once. We anchor on word boundaries to avoid e.g. "tag" matching
# "agent". Case-insensitive at match time.
_KEYWORD_RE_CACHE: dict[tuple[str, ...], re.Pattern[str]] = {}


def _keyword_regex(keywords: tuple[str, ...]) -> re.Pattern[str]:
    cached = _KEYWORD_RE_CACHE.get(keywords)
    if cached is not None:
        return cached
    pattern = "|".join(rf"\b{re.escape(k)}\b" for k in keywords)
    compiled = re.compile(pattern, re.IGNORECASE)
    _KEYWORD_RE_CACHE[keywords] = compiled
    return compiled


@dataclass(frozen=True)
class HackerNewsAgentWatcherSource:
    """Pulls recent HN stories matching agent/MCP/A2A keywords."""

    base_url: str = DEFAULT_BASE_URL
    source_id: str = "hacker_news_agent_watch"
    # Which firehose to scan. Order matters only for deterministic dedupe:
    #   - topstories: HN's main ranked list (rotates ~hourly; ~500 ids)
    #   - newstories: just posted (rotates fast; ~500 ids)
    #   - beststories: curated past-few-days highly-upvoted (~200 ids)
    # Including all three catches both fresh launches and curated wins.
    firehoses: tuple[str, ...] = ("topstories", "newstories", "beststories")
    # Cap how many story IDs to inspect per firehose. Each item is one
    # ~0.7s HTTP call but they fan out via `max_concurrent_fetches`, so
    # 100 × 3 = 300 items finishes in ~13s on a 16-thread pool.
    # Empirically: at 60 we miss real agent posts that exist in positions
    # 60-100. At 100 we catch them.
    max_items_per_firehose: int = 100
    # Parallelism for the per-item fetch fan-out. HN's Firebase API scales
    # comfortably up to dozens of concurrent reads (it's just a CDN-fronted
    # JSON blob store), so 16 is a safe sweet spot — brings 120 items from
    # ~85s sequential to ~6s parallel.
    max_concurrent_fetches: int = 16
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS
    timeout_seconds: float = 8.0
    goal_hash: str = ""
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)
    # Minimum story score (HN upvotes). Filters out single-upvote posts that
    # are usually personal blog posts that didn't reach an audience.
    min_score: int = 2
    # Optional LLM arbiter. Falls back to "reject ambiguous" when None.
    llm_classifier_client: ChatClient | None = None

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        regex = _keyword_regex(self.keywords)
        # Collect story IDs from each firehose, dedupe across them, then
        # fetch each item once in parallel.
        merged_ids: list[int] = []
        seen_ids: set[int] = set()
        for firehose in self.firehoses:
            for story_id in self._fetch_firehose_ids(firehose)[
                : self.max_items_per_firehose
            ]:
                if story_id in seen_ids:
                    continue
                seen_ids.add(story_id)
                merged_ids.append(story_id)

        items = self._fetch_items_parallel(merged_ids)
        candidates: list[DiscoveryCandidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "story":
                continue
            if int(item.get("score") or 0) < self.min_score:
                continue
            if not _matches_keywords(item, regex):
                continue
            normalized = self._normalize(item)
            if normalized is None:
                continue
            if capabilities and not normalized.supports_any(capabilities):
                continue
            candidates.append(normalized)
        return candidates

    def _fetch_items_parallel(self, story_ids: list[int]) -> list[Any]:
        if not story_ids:
            return []
        # ThreadPoolExecutor over urllib is fine here; the workload is
        # purely I/O-bound (no GIL pressure) and we want to avoid the
        # dependency footprint of asyncio + aiohttp for a lazy poller.
        with ThreadPoolExecutor(
            max_workers=max(1, self.max_concurrent_fetches)
        ) as executor:
            return list(executor.map(self._fetch_item, story_ids))

    def _fetch_firehose_ids(self, firehose: str) -> list[int]:
        url = f"{self.base_url}/{firehose}.json"
        payload = _fetch_json(url, timeout_seconds=self.timeout_seconds)
        if not isinstance(payload, list):
            return []
        return [int(value) for value in payload if isinstance(value, int)]

    def _fetch_item(self, story_id: int) -> Any:
        url = f"{self.base_url}/item/{story_id}.json"
        return _fetch_json(url, timeout_seconds=self.timeout_seconds)

    def _normalize(self, item: dict[str, Any]) -> DiscoveryCandidate | None:
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "").strip()
        url = str(item.get("url") or "").strip()
        story_id = int(item.get("id") or 0)
        if not title:
            return None

        # Prefer the linked article URL as the evidence URL; fall back to the
        # HN discussion thread itself when the post has no link.
        hn_thread = f"https://news.ycombinator.com/item?id={story_id}"
        evidence_url = url or hn_thread

        capabilities = _infer_capabilities(
            text=" ".join([title, text]),
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        # Smart filter: an HN post titled "I built X" is a personal post,
        # not a product. An HN thread URL (when the story has no upstream
        # `url`) is just discussion. The classifier rejects both. Pass
        # the upstream `url` when present so the heuristic checks the real
        # vendor URL, not the HN thread.
        verdict = classify_candidate(
            title=title,
            url=url or hn_thread,
            description=text,
            llm_client=self.llm_classifier_client,
        )
        if verdict.verdict != "agent":
            return None

        vendor = _vendor_from_url(url) or "unknown-via-hn"

        raw = {
            # Use the HN id so re-fetching the same story merges, not
            # duplicates. HN ids are monotonic and globally unique.
            "id": f"hn-{story_id}",
            "display_name": title,
            "vendor": vendor,
            "vendor_url": url or hn_thread,
            "provider_type": "ai_agent",
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            "docs": {
                "setup_url": url or hn_thread,
                "auth_method": "",
            },
            "evidence_url": evidence_url,
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


def _matches_keywords(item: dict[str, Any], regex: re.Pattern[str]) -> bool:
    title = str(item.get("title") or "")
    text = str(item.get("text") or "")
    return bool(regex.search(f"{title}\n{text}"))


def _infer_capabilities(*, text: str, extra: dict[str, set[str]]) -> set[str]:
    # Catch-all `semantic_search` is preserved as a fallback because
    # legitimate HN launch posts often don't mention a specific
    # capability vertical in the title (e.g. "Show HN: I built X" is a
    # common pattern). The `classify_candidate()` call upstream in
    # `_normalize()` is what filters Ask-HN/personal/opinion posts now,
    # so the catch-all can't be abused the way it could with the old
    # synonym-dict-based classifier.
    return infer_capabilities_for_source(
        text=text, extra=extra, fallback="semantic_search"
    )


def _vendor_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        netloc = parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _fetch_json(url: str, *, timeout_seconds: float) -> Any:
    req = request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return None
