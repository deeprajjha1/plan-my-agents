"""Vendor RSS event-driven discovery source.

Polls a list of RSS / Atom feeds (vendor blogs, community announcements)
and emits :class:`DiscoveryCandidate` records for entries whose title or
description mentions agents / MCP / A2A / tooling.

Why this source matters:

* Major vendor agent launches (Anthropic, OpenAI, AWS, Google, Cohere,
  Mistral, etc.) almost always come with a blog post the same day, and
  most vendors publish a stable RSS feed.
* Free, no API key, very stable (RSS has been around since 1999).
* The curated seed list is version-controlled (see
  :data:`packages/discovery/sources/vendor_rss_feeds.json`) so additions
  go through review.

Parsing model:

* Stdlib ``xml.etree.ElementTree`` handles both RSS 2.0 (``<rss><channel>``)
  and Atom (``<feed>``). We don't take a dependency on ``feedparser`` to
  keep the no-extra-dependency posture.
* We only emit candidates for entries with at least one keyword match
  AND at least one inferred capability — keeps the index high-signal.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib import error, parse, request
from xml.etree import ElementTree as ET

from planmyagents_api.discovery.agent_classifier import (
    ChatClient,
    classify_candidate,
)
from planmyagents_api.discovery.capability_index import infer_capabilities_for_source
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate

USER_AGENT = (
    "agent-manager-discovery/0.1 "
    "(+https://github.com/deepraj-jha/agent-manager; first-party Tier-1 source)"
)

DEFAULT_KEYWORDS = (
    "mcp",
    "model context protocol",
    "a2a",
    "agent-to-agent",
    "ai agent",
    "llm agent",
    "agent framework",
    "agent platform",
    "tool use",
    "function calling",
    "openapi",
    "agent.json",
    "autonomous agent",
)

# RSS 2.0 + Atom both use the same handful of element names. We try each in
# order and pick the first non-empty value.
_RSS_ITEM_TITLE_PATHS = (".//{*}title", "title")
_RSS_ITEM_LINK_PATHS = (".//{*}link", "link")
_RSS_ITEM_DESCRIPTION_PATHS = (
    ".//{*}description",
    "description",
    ".//{http://www.w3.org/2005/Atom}summary",
    ".//{http://www.w3.org/2005/Atom}content",
)

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
class VendorRssSource:
    """Polls vendor RSS/Atom feeds for agent-related announcements."""

    feed_urls: list[str]
    source_id: str = "vendor_rss"
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS
    max_entries_per_feed: int = 30
    # Per-feed timeout. The dispatcher's per-scout budget is the outer
    # bound; this is the per-HTTP-call bound so one slow vendor can't
    # consume the whole budget by itself.
    timeout_seconds: float = 4.0
    # Parallel feed fetches. 13 feeds × ~2s each = ~26s sequential. At 8
    # workers parallel it's ~5s, comfortably inside the dispatcher's
    # 6s budget.
    max_concurrent_feed_fetches: int = 8
    goal_hash: str = ""
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)
    # Optional LLM arbiter for ambiguous items. When None, the
    # classifier rejects uncertain items (conservative). Inject from
    # the scout dispatcher so production paths can reuse the planner's
    # Qwen client without each source re-instantiating its own.
    llm_classifier_client: ChatClient | None = None

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        regex = _keyword_regex(self.keywords)
        candidates: list[DiscoveryCandidate] = []
        seen: set[str] = set()
        # Fan out feed fetches in parallel; only the keyword/normalize
        # work happens single-threaded. Each fetch is HTTP-bound and the
        # GIL is released during socket reads.
        executor = ThreadPoolExecutor(
            max_workers=max(1, self.max_concurrent_feed_fetches)
        )
        try:
            entries_per_feed = list(
                executor.map(self._fetch_and_parse_feed, self.feed_urls)
            )
        finally:
            executor.shutdown(wait=False)
        for feed_url, entries in zip(self.feed_urls, entries_per_feed, strict=True):
            for entry in entries[: self.max_entries_per_feed]:
                title = (entry.get("title") or "").strip()
                description = (entry.get("description") or "").strip()
                link = (entry.get("link") or "").strip()
                if not title or not link:
                    continue
                haystack = f"{title}\n{description}"
                if not regex.search(haystack):
                    continue
                normalized = self._normalize(
                    feed_url=feed_url,
                    title=title,
                    description=description,
                    link=link,
                )
                if normalized is None:
                    continue
                if normalized.id in seen:
                    continue
                if capabilities and not normalized.supports_any(capabilities):
                    continue
                seen.add(normalized.id)
                candidates.append(normalized)
        return candidates

    def _fetch_and_parse_feed(self, url: str) -> list[dict[str, str]]:
        body = _fetch_text(url, timeout_seconds=self.timeout_seconds)
        if not body:
            return []
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return []
        # RSS 2.0 → <rss><channel><item>...; Atom → <feed><entry>...
        items = root.findall(".//{*}item") or root.findall(".//{*}entry")
        parsed: list[dict[str, str]] = []
        for item in items:
            parsed.append(
                {
                    "title": _first_text(item, _RSS_ITEM_TITLE_PATHS),
                    "link": _first_link(item),
                    "description": _first_text(item, _RSS_ITEM_DESCRIPTION_PATHS),
                }
            )
        return parsed

    def _normalize(
        self, *, feed_url: str, title: str, description: str, link: str
    ) -> DiscoveryCandidate | None:
        capabilities = _infer_capabilities(
            text=f"{title} {description}",
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        # Smart filter: drop forum posts, opinion pieces, etc. before
        # we ever write them to the index. The classifier is cheap
        # (heuristic-first), and only escalates to LLM when ambiguous.
        verdict = classify_candidate(
            title=title,
            url=link,
            description=description,
            llm_client=self.llm_classifier_client,
        )
        if verdict.verdict != "agent":
            return None

        vendor = _vendor_from_url(link) or _vendor_from_url(feed_url) or "unknown-via-rss"
        slug = _slug_for_link(link)

        raw = {
            "id": f"rss-{slug}",
            "display_name": title[:200],
            "vendor": vendor,
            "vendor_url": link,
            "provider_type": "ai_agent",
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            "docs": {"setup_url": link, "auth_method": ""},
            "evidence_url": link,
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


def _first_text(element: ET.Element, paths: tuple[str, ...]) -> str:
    for path in paths:
        node = element.find(path)
        if node is not None and (node.text or "").strip():
            return _strip_html(node.text or "")
    return ""


def _first_link(element: ET.Element) -> str:
    """Atom uses <link href="..."/> while RSS uses <link>...</link>."""
    for path in _RSS_ITEM_LINK_PATHS:
        node = element.find(path)
        if node is None:
            continue
        href = node.attrib.get("href")
        if href:
            return href.strip()
        if node.text:
            return node.text.strip()
    return ""


def _strip_html(text: str) -> str:
    """Crude HTML strip for descriptions. Good enough for keyword matching;
    we don't need fidelity, and avoiding a BeautifulSoup dependency keeps
    the prototype lean."""
    return re.sub(r"<[^>]+>", " ", text)


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


def _slug_for_link(url: str) -> str:
    """Stable id derived from URL — collapses non-alphanumerics so re-fetches
    of the same blog post merge instead of duplicating."""
    try:
        parsed = parse.urlparse(url)
    except ValueError:
        return re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:120]
    base = f"{parsed.netloc}-{parsed.path}".lower()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:120]


def _infer_capabilities(*, text: str, extra: dict[str, set[str]]) -> set[str]:
    # Catch-all `semantic_search` is preserved because legitimate vendor
    # launches ("Anthropic releases MCP server") often don't mention a
    # specific capability vertical in the headline, and we still want
    # them in the index for review. The `classify_candidate()` call
    # upstream in `_normalize()` is what blocks forum threads / opinion
    # pieces now, so the catch-all can't be abused.
    return infer_capabilities_for_source(
        text=text, extra=extra, fallback="semantic_search"
    )


def _fetch_text(url: str, *, timeout_seconds: float) -> str:
    headers = {
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        "User-Agent": USER_AGENT,
    }
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return response.read().decode("utf-8", errors="replace")
    except (OSError, TimeoutError, error.URLError):
        return ""
