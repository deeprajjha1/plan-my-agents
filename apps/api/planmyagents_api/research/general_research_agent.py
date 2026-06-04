"""First-party general-purpose web-research agent.

Background
----------
PlanMyAgents's discovery layer is biased toward *specialist* agents:
"if you need to verify an email, route to Hunter; if you need to
locate stores, route to a store-locator MCP server". That bias is
correct most of the time, but it leaves a real product hole: when
the goal is something like "find liquor stores in southern India and
compare prices on single malts", no MCP server in any registry has
indexed that vertical, the candidate judge correctly refuses every
quasi-match, and the user sees an honest-but-cold "we cannot satisfy
this".

This module is the **last-resort backstop** for that case. It is
NOT a specialist for any one capability — it is a general web-
research agent that takes a sub-task description, runs a real web
search, and asks a hosted LLM to synthesize a cited answer. Quality
is by definition lower than a domain specialist would offer, but it
is dramatically better than refusing the request outright.

Design choices and the reasoning behind them
--------------------------------------------
* **Brave Search as the default backend.** It already has a free
  tier (2,000 queries/month) and the API key slot already exists in
  ``.env`` (``BRAVE_SEARCH_API_KEY``), so wiring it adds zero
  ops cost. The contract is small enough that a Tavily fallback is
  one method swap if we ever want it.

* **Silent skip when no key is configured.** Mirrors the Smithery
  scout's pattern: ``available()`` returns False when the key is
  missing, the caller checks first, and the backstop is treated as
  "not deployed in this environment" rather than crashing the
  refusal path. Dev environments without a Brave key still get a
  clean refusal payload with the human-fallback card.

* **Bounded result set.** We cap at ``DEFAULT_MAX_RESULTS`` (8)
  search hits per call. More hits don't help the LLM synthesize a
  better answer and they balloon the prompt token cost. The cap is
  a constant rather than env-tunable because changing it changes
  the *quality* trade-off, not the *cost* trade-off, and we want
  every environment to behave the same way at this slice's
  maturity.

* **Structured citations contract.** The LLM is asked to return a
  ``summary`` plus a list of ``citations`` where each citation maps
  back to a search-result URL. The validator enforces that every
  cited URL is one we actually fetched; hallucinated URLs are
  silently dropped. This is the same anti-hallucination posture as
  the human-fallback suggester — better to under-cite than to ship
  a fabricated source.

* **Best-effort, never blocks /goal.** Every public method swallows
  unexpected exceptions and returns a structured error result. The
  refusal path treats "research backstop unavailable" the same as
  "research backstop disabled" — the user sees the rest of the
  refusal payload exactly as they would without this module.

* **Opt-out via env var.** ``PLANMYAGENTS_GENERAL_RESEARCH_AGENT``
  toggles the module off completely. Tests use this for a
  deterministic "no backstop" pipeline; operators use it to measure
  end-to-end latency without the backstop's contribution.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from planmyagents_api.llm.escalating_client import (
    NoLlmTierAvailableError,
    build_default_escalating_client,
)
from planmyagents_api.planner.local_qwen import ChatClient, LocalQwenPlannerError

LOGGER = logging.getLogger(__name__)

# The capability slug PlanMyAgents uses to advertise this agent
# internally. Surfaces in the UI badge and on the leaderboard so the
# operator can see how often the backstop fires versus a specialist.
GENERAL_RESEARCH_CAPABILITY_ID = "general_web_research"

# How many web-search hits we forward to the LLM. Eight is the sweet
# spot empirically — five misses too many follow-up answers, twelve
# pads the prompt without obvious synthesis quality gains. See module
# docstring for the rationale on it being a constant rather than
# env-tunable.
DEFAULT_MAX_RESULTS = 8

# Defensive timeout for the upstream search HTTP call. Brave's API
# usually responds in well under a second; an 8s ceiling means a
# stalled upstream cannot pin a /goal request for longer than the
# user's patience allows.
DEFAULT_SEARCH_TIMEOUT_SECONDS = 8.0

BRAVE_API_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
BRAVE_API_KEY_ENV = "BRAVE_SEARCH_API_KEY"
ENABLE_ENV = "PLANMYAGENTS_GENERAL_RESEARCH_AGENT"


# A search transport returns a normalised list of search hits. Tests
# inject a fake transport so the module can be exercised without any
# network access.
SearchTransport = Callable[[str, int, float], list[dict[str, Any]]]


class GeneralResearchAgentError(RuntimeError):
    """Raised internally when the LLM returns an unparseable payload.

    Always caught at the public method boundary so callers never see
    the exception — they get a structured error result instead.
    """


@dataclass(frozen=True)
class ResearchSource:
    """One citable web result the agent considered.

    ``url`` is the original Brave-result URL (validated for
    ``http(s)://`` scheme). ``title`` and ``snippet`` are the
    upstream-supplied display fields, truncated for safety so a
    misbehaving search engine cannot blow up the response payload.
    """

    url: str
    title: str
    snippet: str

    def to_json(self) -> dict[str, Any]:
        return {"url": self.url, "title": self.title, "snippet": self.snippet}


@dataclass(frozen=True)
class ResearchResult:
    """Aggregate result of one ``research`` call.

    ``status`` discriminates between:

    * ``"applied"`` — search ran, LLM ran, answer is ready.
    * ``"empty_search"`` — search ran but returned zero usable
      results (no public information indexed for this query). The
      ``summary`` will be empty.
    * ``"unavailable"`` — search transport or LLM tier failed; the
      ``reason`` carries the error text.
    * ``"disabled"`` — the env var is off; module short-circuited.
    * ``"missing_credentials"`` — neither the API key nor a transport
      was provided. Treated as "not deployed in this environment".
    """

    status: str
    summary: str = ""
    sources: list[ResearchSource] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "sources": [s.to_json() for s in self.sources],
            "citations": list(self.citations),
            "elapsed_ms": self.elapsed_ms,
            "reason": self.reason,
        }


def general_research_agent_enabled() -> bool:
    """Honour ``PLANMYAGENTS_GENERAL_RESEARCH_AGENT`` (default on).

    Same opt-out semantics as the human-fallback suggester and label
    reconciler — set to ``off``/``0``/``false``/``no`` to short-
    circuit before any external service is touched.
    """

    return os.getenv(ENABLE_ENV, "on").lower() not in {
        "0",
        "false",
        "off",
        "none",
        "disabled",
        "no",
    }


@dataclass(frozen=True)
class GeneralResearchAgent:
    """Last-resort general web-research agent.

    Construct with the default settings (reads everything from env)
    or inject ``api_key`` / ``transport`` / ``client`` for tests. The
    agent is a frozen dataclass so two requests can share one
    instance without locking concerns.
    """

    api_key: str | None = None
    transport: SearchTransport | None = None
    client: ChatClient | None = None
    timeout_seconds: float = DEFAULT_SEARCH_TIMEOUT_SECONDS
    max_results: int = DEFAULT_MAX_RESULTS

    @property
    def capability_id(self) -> str:
        return GENERAL_RESEARCH_CAPABILITY_ID

    def available(self) -> bool:
        """Return True iff the agent is fully usable in this process.

        False when the env-var is off, when no API key is present and
        no transport is injected, or when the LLM tier is configured
        to be skipped entirely. Caller should always check
        ``available()`` before invoking ``research`` — invoking when
        unavailable returns a ``status="missing_credentials"`` or
        ``"disabled"`` result rather than raising.
        """

        if not general_research_agent_enabled():
            return False
        if self.transport is not None:
            return True
        return bool(self._resolved_api_key())

    def research(
        self,
        *,
        sub_task_description: str,
        goal: str = "",
    ) -> ResearchResult:
        """Run one web-search + LLM-synthesis pass.

        Args:
            sub_task_description: The free-text describing what the
                user actually wants this step to do. Used as the web
                search query AND as the synthesis prompt context.
                The decomposer's ``user_facing_step`` is the
                expected input shape; raw capability slugs work too
                but produce noticeably worse search hits.
            goal: Optional full-goal context. Passed through to the
                LLM so the synthesis can frame the answer in the
                user's broader intent ("you are buying single malt
                in Bangalore" vs "you are running a price audit").

        Returns:
            :class:`ResearchResult`. Never raises — every failure
            path returns a structured payload the caller can render.
        """

        cleaned_query = (sub_task_description or "").strip()
        if not cleaned_query:
            return ResearchResult(
                status="missing_query",
                reason="Sub-task description was empty.",
            )
        if not general_research_agent_enabled():
            return ResearchResult(status="disabled")
        if self.transport is None and not self._resolved_api_key():
            return ResearchResult(
                status="missing_credentials",
                reason=f"{BRAVE_API_KEY_ENV} is not configured.",
            )

        start = time.perf_counter()
        try:
            hits = self._search(cleaned_query)
        except _SearchUnavailable as exc:
            return ResearchResult(
                status="unavailable",
                reason=f"search backend failed: {exc}",
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )

        sources = [
            self._normalise_hit(hit) for hit in hits[: self.max_results]
        ]
        sources = [src for src in sources if src is not None]

        if not sources:
            return ResearchResult(
                status="empty_search",
                reason="No usable results returned from the search backend.",
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )

        try:
            summary, citations = self._synthesise(
                goal=(goal or "").strip(),
                query=cleaned_query,
                sources=sources,
            )
        except (LocalQwenPlannerError, NoLlmTierAvailableError) as exc:
            LOGGER.info(
                "general_research_agent: LLM unavailable for query %r: %s",
                cleaned_query,
                exc,
            )
            return ResearchResult(
                status="unavailable",
                reason=f"LLM tier failed: {exc}",
                sources=sources,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
        except GeneralResearchAgentError as exc:
            LOGGER.info(
                "general_research_agent: synthesis parse failed: %s",
                exc,
            )
            return ResearchResult(
                status="unavailable",
                reason=str(exc),
                sources=sources,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )

        return ResearchResult(
            status="applied",
            summary=summary,
            sources=sources,
            citations=citations,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )

    # --- Internals -------------------------------------------------

    def _resolved_api_key(self) -> str:
        return (self.api_key or os.getenv(BRAVE_API_KEY_ENV) or "").strip()

    def _search(self, query: str) -> list[dict[str, Any]]:
        """Dispatch to the injected transport or the default Brave
        client. Wraps both in a uniform exception so the caller has
        only one error type to handle."""

        transport = self.transport or _default_brave_transport(self._resolved_api_key())
        try:
            return list(
                transport(query, self.max_results, self.timeout_seconds) or []
            )
        except _SearchUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — wrap arbitrary upstream errors
            raise _SearchUnavailable(str(exc)) from exc

    @staticmethod
    def _normalise_hit(hit: dict[str, Any]) -> ResearchSource | None:
        """Validate one Brave-shape hit. Drops any without an http(s)
        URL with a non-empty host (same defensive posture as the
        human-fallback suggester's URL validator)."""

        url = _validate_url(hit.get("url"))
        if not url:
            return None
        title = _truncate(_strip(hit.get("title")), max_chars=180)
        snippet = _truncate(_strip(hit.get("snippet")), max_chars=400)
        return ResearchSource(url=url, title=title, snippet=snippet)

    def _synthesise(
        self,
        *,
        goal: str,
        query: str,
        sources: list[ResearchSource],
    ) -> tuple[str, list[str]]:
        chat_client = self.client or build_default_escalating_client()
        messages = _build_messages(goal=goal, query=query, sources=sources)
        raw = chat_client.complete(messages)
        return _parse_response(raw, allowed_urls={s.url for s in sources})


# ---------------------------------------------------------------------------
# Brave Search default transport
# ---------------------------------------------------------------------------


class _SearchUnavailable(RuntimeError):
    """Internal-only signal that the upstream search call failed.

    Caller in :class:`GeneralResearchAgent` translates this into a
    user-facing ``status="unavailable"`` result. Module-private so the
    public surface stays a single error-free dataclass.
    """


def _default_brave_transport(api_key: str) -> SearchTransport:
    """Build the production Brave-Search transport bound to a key.

    Returned closure keeps the API key out of method signatures so
    a misuse like accidentally logging the transport doesn't leak the
    secret.
    """

    def _transport(
        query: str,
        max_results: int,
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        if not api_key:
            raise _SearchUnavailable(f"{BRAVE_API_KEY_ENV} not configured")
        params = urllib_parse.urlencode(
            {
                "q": query,
                "count": max(1, min(max_results, 20)),
                # Restrict to web hits — Brave can return discussions,
                # videos, news; for synthesis we only want the canonical
                # web result list.
                "result_filter": "web",
                "safesearch": "moderate",
            }
        )
        url = f"{BRAVE_API_ENDPOINT}?{params}"
        req = urllib_request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "PlanMyAgentsResearch/0.1",
                "X-Subscription-Token": api_key,
            },
        )
        try:
            with urllib_request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
                body = response.read().decode("utf-8", errors="replace")
        except (urllib_error.URLError, TimeoutError, OSError) as exc:
            raise _SearchUnavailable(str(exc)) from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise _SearchUnavailable(f"non-JSON response: {exc}") from exc
        web_block = (payload.get("web") or {}).get("results") or []
        out: list[dict[str, Any]] = []
        for item in web_block:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "url": item.get("url"),
                    "title": item.get("title"),
                    # Brave returns ``description`` but our normalised
                    # shape calls it ``snippet`` to mirror what most
                    # other search APIs use.
                    "snippet": item.get("description") or item.get("snippet"),
                }
            )
        return out

    return _transport


# ---------------------------------------------------------------------------
# LLM synthesis
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """
You are PlanMyAgents's "general research agent".

Context
-------
A user has a goal that no specialist AI agent in the system can
satisfy. Rather than refusing, PlanMyAgents has run a web search
and is asking you to synthesise a useful answer from the results.

Your job is to produce:

1. A concise ``summary`` (4-8 short sentences max) that answers the
   user's question as best you can from the provided sources.
2. A list of ``citations`` — the URLs you actually used to support
   the summary. Every citation MUST be one of the URLs you were
   given. Do not invent URLs.

Hard rules
----------
1. Use ONLY information that is supported by the provided sources.
   If the sources don't actually cover the question, say so plainly
   in the summary and return an empty ``citations`` array.
2. Do not include URLs in the summary text itself — keep them in
   the ``citations`` array. The UI renders citations separately.
3. Do not pad the summary with hedges or filler. Be direct, like a
   smart colleague who skimmed the same pages and is summarising
   what they saw.
4. The user's original full-goal context is provided so you can
   tailor the framing — e.g. a regional goal should be answered
   with regional emphasis when the sources support it.

Output strict JSON. No markdown. No prose. No commentary.

Schema:
{
  "summary": "<4-8 sentence answer>",
  "citations": ["<url from sources>", "<url from sources>"]
}
""".strip()


def _build_messages(
    *,
    goal: str,
    query: str,
    sources: list[ResearchSource],
) -> list[dict[str, str]]:
    """Build the chat-message pair sent to the LLM for synthesis."""

    payload = {
        "user_goal": goal,
        "research_query": query,
        "sources": [
            {
                "url": s.url,
                "title": s.title,
                "snippet": s.snippet,
            }
            for s in sources
        ],
    }
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, indent=2, sort_keys=True)},
    ]


def _parse_response(
    raw: str, *, allowed_urls: set[str]
) -> tuple[str, list[str]]:
    """Parse the LLM's strict-JSON synthesis response.

    Validates:

    * ``summary`` is a non-empty string. Empty / missing summaries
      raise :class:`GeneralResearchAgentError`; the caller treats
      that as "synthesis failed" and returns a structured error.
    * Every citation URL is present in ``allowed_urls`` (the set we
      actually fetched). Hallucinated URLs are silently dropped —
      same anti-hallucination posture as the human-fallback
      suggester's URL validator.
    * Summary is truncated defensively at 2,000 chars even though
      the prompt asks for 4-8 sentences. Prevents a misbehaving
      model from blowing up the /goal response payload.
    """

    payload = _parse_json_object(raw)
    if not payload:
        raise GeneralResearchAgentError(
            "Research agent did not return a parseable JSON object."
        )
    summary_raw = payload.get("summary")
    if not isinstance(summary_raw, str) or not summary_raw.strip():
        raise GeneralResearchAgentError(
            "Research agent returned an empty summary."
        )
    summary = _truncate(summary_raw.strip(), max_chars=2000)
    citations_raw = payload.get("citations")
    citations: list[str] = []
    if isinstance(citations_raw, list):
        for item in citations_raw:
            if not isinstance(item, str):
                continue
            candidate = item.strip()
            if candidate in allowed_urls and candidate not in citations:
                citations.append(candidate)
    return summary, citations


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Tolerant JSON-object extraction — same shape as the suggester."""

    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _strip(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _truncate(value: str, *, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "\u2026"


def _validate_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    try:
        parsed = urllib_parse.urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    return candidate
