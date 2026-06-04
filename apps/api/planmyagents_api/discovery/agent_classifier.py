"""Two-layer classifier for "is this candidate actually a runnable agent?"

The vendor_rss + hacker_news sources have always emitted `provider_type:
ai_agent` for every keyword-matching RSS/HN item. That polluted the index
with reddit discussions, opinion pieces, and corporate blog posts that
were never agents in the first place. This module is the cheap, smart
filter that decides whether an emitted candidate gets to stay.

Two layers, in order:

1. **Heuristic layer (free, instant).** Decides on URL hostname + title
   shape. Catches the obvious 80%:
   - Reddit / HN-thread / Twitter / LinkedIn / dev.to / lobste.rs URLs
     are discussion venues, never products.
   - First-person / question / "looking for" titles are user posts, not
     vendor announcements.
   - "Introducing X" / "Announcing X" / "Release vX.Y" / "Now available"
     titles are launch announcements.
   Heuristic verdicts are deterministic and testable.

2. **LLM arbiter (only for the genuinely ambiguous).** For titles where
   the heuristic abstains (vendor blog post that's neither a clear
   launch nor a clear opinion piece), one structured JSON call to the
   local Qwen client. Cached by URL.

Why both:
- Pure heuristic: can't tell "Anthropic announces Claude Opus 4.7" (real
  product) from "Anthropic publishes economic impact report" (corporate
  blog) — both might start with "Announcing".
- Pure LLM: too slow (N RSS items × N HN items × Ollama latency) and
  uses up Ollama capacity that should be reserved for planning.

Fallback: when the LLM is unavailable AND the heuristic abstains, we
reject. Better to under-collect than to pollute.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

# URL hostnames where every item is a discussion / opinion / aggregator,
# never a vendor's product page. Matches the host or any subdomain.
_DISCUSSION_HOSTS: frozenset[str] = frozenset(
    {
        "reddit.com",
        "old.reddit.com",
        "new.reddit.com",
        "x.com",
        "twitter.com",
        "linkedin.com",
        "lobste.rs",
        "dev.to",
        "hashnode.dev",
        "substack.com",
        "medium.com",
        "ycombinator.com",
        "lesswrong.com",
    }
)

# Pure HN thread URL pattern. When an HN item's `url` field is empty the
# scout falls back to the HN thread URL itself — that's never a product.
_HN_THREAD_RE = re.compile(r"^https?://news\.ycombinator\.com/item\?id=\d+")

# Title prefixes that are almost certainly user-generated posts /
# questions / opinion pieces, not vendor announcements. Conservative —
# only the clearest signals.
_USER_POST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(i|my|we)\s+(use|tried|built|made|wonder|think|asked)\b", re.IGNORECASE),
    re.compile(r"^(how|why|what|when|where|which|who)\s+(do|does|is|are|should|can|would|did)\b", re.IGNORECASE),
    re.compile(r"^(looking for|is there|anyone|any one|the more i|the less i|ask hn|show hn:.*\?)\b", re.IGNORECASE),
    re.compile(r"\?\s*$"),  # ends in a question mark
    re.compile(r"\b(opinion|hot take|rant|thoughts on|review of|advice on)\b", re.IGNORECASE),
)

# Title patterns for clear launch announcements. If any match AND the
# title/description mentions an agent-related noun, accept.
_LAUNCH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(introducing|announcing|launching|presenting)\b", re.IGNORECASE),
    # "Show HN:" is the canonical HN launch prefix when followed by a
    # product description (not a question). The "?$" pattern in
    # _USER_POST_PATTERNS already rejects "Show HN: ...?" style
    # questions, so by the time we reach this pattern, "Show HN:" is
    # signalling a launch.
    re.compile(r"^show hn:\s", re.IGNORECASE),
    # "Vendor X launches Y" / "X releases Y" / "X ships Y" / "X open-sources Y".
    # Note: "release" → "releasing" drops the trailing 'e', so we match
    # the bare stem + the inflected forms separately rather than relying
    # on simple suffix appending.
    re.compile(
        r"\b("
        r"launch(es|ed|ing)?"
        r"|releas(e|es|ed|ing)"
        r"|ship(s|ped|ping)?"
        r"|open[- ]sourc(e|es|ed|ing)"
        r")\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(now available|generally available|now live|now open)\b", re.IGNORECASE),
    re.compile(r"\b(release|releases|released)\s+v?\d+(\.\d+)*\b", re.IGNORECASE),
    re.compile(r"\b(v?\d+\.\d+(\.\d+)?)\s+(released|launched|out|available)\b", re.IGNORECASE),
    re.compile(r"\b(mcp server|a2a agent|agent card|agent.json)\b", re.IGNORECASE),
)

# Final heuristic guard: every agent claim must mention an
# agent/MCP/A2A-style noun somewhere in title+description. Stops
# corporate posts that have "introducing" but are unrelated.
_AGENT_NOUN_RE = re.compile(
    r"\b(agent|agents|mcp|model context protocol|a2a|tool[- ]use|"
    r"function[- ]call|llm[- ]agent|ai[- ]native|autonomous|"
    r"copilot|assistant)\b",
    re.IGNORECASE,
)


ClassificationVerdict = Literal["agent", "not_agent", "uncertain"]
ClassificationSource = Literal["heuristic", "llm", "fallback"]


@dataclass(frozen=True)
class ClassificationResult:
    """Outcome of running a candidate through the classifier."""

    verdict: ClassificationVerdict
    confidence: float  # 0..1
    reason: str
    source: ClassificationSource


class ChatClient(Protocol):
    """Subset of the planner's `ChatClient` so the classifier doesn't have
    a hard import on `planmyagents_api.planner`. Any client returning a JSON
    string from `complete()` works."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


# ---------------------------------------------------------------------------
# Layer 1: heuristic
# ---------------------------------------------------------------------------


def classify_heuristic(*, title: str, url: str, description: str = "") -> ClassificationResult:
    """Cheap, deterministic verdict based on URL + title shape.

    Returns `verdict == "uncertain"` when the heuristic doesn't have a
    strong signal — callers should escalate those to the LLM arbiter.
    """

    clean_title = (title or "").strip()
    clean_url = (url or "").strip()

    if not clean_title or not clean_url:
        return ClassificationResult(
            verdict="not_agent",
            confidence=0.95,
            reason="missing title or url",
            source="heuristic",
        )

    host = _hostname(clean_url)

    # Pure HN thread URL with no upstream link.
    if _HN_THREAD_RE.match(clean_url):
        return ClassificationResult(
            verdict="not_agent",
            confidence=0.97,
            reason="hn thread itself is not a product",
            source="heuristic",
        )

    # Discussion-venue hostnames are never products.
    if _host_matches(host, _DISCUSSION_HOSTS):
        return ClassificationResult(
            verdict="not_agent",
            confidence=0.95,
            reason=f"discussion venue: {host}",
            source="heuristic",
        )

    # User-generated post titles.
    for pattern in _USER_POST_PATTERNS:
        if pattern.search(clean_title):
            return ClassificationResult(
                verdict="not_agent",
                confidence=0.9,
                reason=f"user-post title pattern: /{pattern.pattern}/",
                source="heuristic",
            )

    # Clear launch announcement, AND has an agent-related noun somewhere.
    haystack = f"{clean_title}\n{description}"
    for pattern in _LAUNCH_PATTERNS:
        if pattern.search(clean_title):
            if _AGENT_NOUN_RE.search(haystack):
                return ClassificationResult(
                    verdict="agent",
                    confidence=0.85,
                    reason="launch verb + agent noun present",
                    source="heuristic",
                )
            # Launch verb but no agent noun — likely corporate blog. Send
            # to LLM to be safe; don't auto-accept.
            return ClassificationResult(
                verdict="uncertain",
                confidence=0.4,
                reason="launch verb but no agent noun — needs llm",
                source="heuristic",
            )

    # No strong signal either way. Defer.
    return ClassificationResult(
        verdict="uncertain",
        confidence=0.0,
        reason="no strong heuristic signal",
        source="heuristic",
    )


def _hostname(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower()


def _host_matches(host: str, denylist: frozenset[str]) -> bool:
    """Match host or any subdomain. `r.reddit.com` matches `reddit.com`."""

    if not host:
        return False
    if host in denylist:
        return True
    for denied in denylist:
        if host.endswith("." + denied):
            return True
    return False


# ---------------------------------------------------------------------------
# Layer 2: LLM arbiter
# ---------------------------------------------------------------------------


_LLM_SYSTEM_PROMPT = """
You are a strict classifier. Given an article's title, URL, and
description, decide if it is announcing or describing a *runnable* AI
agent, MCP server, A2A endpoint, or agent platform that another program
could call or install.

Reply ONLY with JSON: {"is_agent": bool, "confidence": float (0..1),
"reason": "short phrase"}.

Reject (is_agent=false) when the item is:
- a forum / Reddit / Hacker News discussion thread
- a user's opinion piece or personal experience post
- a tutorial about how to use someone else's agent
- a corporate blog about the company, hiring, fundraising, or strategy
- a research paper without an associated runnable product
- a comparison / review / "best of" listicle

Accept (is_agent=true) only when the item clearly describes a specific
agent / MCP server / A2A agent / agent framework that exists as a
product (open source repo, hosted service, SDK, package).
""".strip()


def classify_with_llm(
    *,
    title: str,
    url: str,
    description: str,
    client: ChatClient,
) -> ClassificationResult:
    """Ask the LLM to arbitrate an ambiguous case.

    Returns a `not_agent` verdict (with source="fallback") if the LLM
    returns malformed JSON or any other error — strict failure semantics
    mean the index doesn't get polluted when the LLM is wobbly.
    """

    user_payload = json.dumps(
        {
            "title": title[:300],
            "url": url[:500],
            "description": description[:1000],
        },
        ensure_ascii=False,
    )
    try:
        raw = client.complete(
            [
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_payload},
            ]
        )
    except Exception as exc:  # noqa: BLE001 — strict failure path
        return ClassificationResult(
            verdict="not_agent",
            confidence=0.5,
            reason=f"llm error: {type(exc).__name__}",
            source="fallback",
        )

    parsed = _parse_llm_json(raw)
    if parsed is None:
        return ClassificationResult(
            verdict="not_agent",
            confidence=0.5,
            reason="llm returned non-json or wrong shape",
            source="fallback",
        )

    is_agent = bool(parsed.get("is_agent"))
    confidence = _clamp_confidence(parsed.get("confidence"))
    reason = str(parsed.get("reason") or "").strip()[:240] or (
        "llm verdict (no reason)"
    )
    return ClassificationResult(
        verdict="agent" if is_agent else "not_agent",
        confidence=confidence,
        reason=reason,
        source="llm",
    )


def _parse_llm_json(raw: str) -> dict[str, Any] | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if "is_agent" not in parsed:
        return None
    return parsed


def _clamp_confidence(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.5
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


# ---------------------------------------------------------------------------
# Composition: the public entry point
# ---------------------------------------------------------------------------


def classify_candidate(
    *,
    title: str,
    url: str,
    description: str = "",
    llm_client: ChatClient | None = None,
) -> ClassificationResult:
    """Top-level classifier. Run heuristic first; only invoke LLM for the
    `uncertain` band, and only if a client was supplied.

    When no LLM client is configured and the heuristic abstains, the
    result is `not_agent` with source="fallback" — preserves the
    invariant that ambiguous items don't sneak into the index.
    """

    verdict = classify_heuristic(title=title, url=url, description=description)
    if verdict.verdict != "uncertain":
        return verdict

    if llm_client is None:
        return ClassificationResult(
            verdict="not_agent",
            confidence=0.5,
            reason="uncertain + no llm configured",
            source="fallback",
        )

    return classify_with_llm(
        title=title, url=url, description=description, client=llm_client
    )


def cache_key(url: str) -> str:
    """Stable hash for caching LLM verdicts by URL. Use sha256 so the
    cache key is collision-free across very large indices."""

    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()
