"""LLM-driven query expansion for the scout dispatcher.

When the planner produces a sub-task like "kyc_aml_check for retail buyers
in UAE", a single search query rarely surfaces the right agent. Different
sources prefer different phrasings:

* APIs.guru indexes by domain + product name → "Onfido Verify" hits.
* Hacker News matches discussion phrasing → "Show HN: KYC API for…".
* GitHub code search responds best to topic terms → "topic:kyc-api".

The `LlmQueryExpander` asks the planner LLM (Qwen / Groq /
BrainstormChatClient) to rewrite the sub-task into one query per scout,
then returns a `dict[scout_id -> query_string]` that the dispatcher
passes via its `per_scout_query_overrides` parameter.

If the LLM is unavailable we fall back to a deterministic rule-based
expander — that's enough to keep the system functional in
local-development / no-API-key setups, while still producing
source-appropriate queries.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

LOGGER = logging.getLogger(__name__)


class ChatClient(Protocol):
    """Same shape as `BrainstormChatClient` so we can plug in any of
    Qwen / Groq / a stub."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class ExpandedQueries:
    """Result of one expansion call.

    `per_scout_queries` keys are scout ids. Values are natural-language
    descriptions optimized for that scout's underlying source. If a key
    is missing for a scout, the dispatcher falls back to the original
    sub-task text.
    """

    per_scout_queries: dict[str, str]
    used_llm: bool
    raw_response: str = ""
    fallback_reason: str | None = None


# Keys we send to the LLM and expect back. Adding a new scout? Add its id
# here so the LLM is asked to produce a query for it.
DEFAULT_SCOUT_IDS: tuple[str, ...] = (
    "official_mcp_registry",
    "apis_guru",
    "hacker_news_agent_watch",
    "vendor_rss",
    "github_code_search",
    "github_recently_pushed",
)


_SYSTEM_PROMPT = (
    "You are a research assistant routing one CAPABILITY (not a specific "
    "user request) to several agent-discovery sources. The downstream "
    "judge filters results against the user's actual goal — your job is "
    "to MAXIMISE RECALL at the capability level so the judge has "
    "candidates to filter.\n\n"
    "Hard rules:\n"
    "1. Produce queries about the GENERIC CAPABILITY ONLY. Do NOT include "
    "domain nouns, brand names, places, products, or specifics taken "
    "from the sub-task. The sub-task is provided only as context for "
    "you to understand which capability is meant — never copy its "
    "specific words into the query.\n"
    "2. Each query is 3-8 words, source-appropriate phrasing.\n"
    "3. Do NOT invent vendor names.\n"
    "4. Output strict JSON: a single object whose keys are the requested "
    "source ids and values are the phrases. No prose, no markdown, no "
    "commentary.\n\n"
    "Example of WRONG behaviour (do not do this):\n"
    "  Capability: place_search, Sub-task: 'find me a bookstore in Paris'\n"
    "  Wrong query: 'Paris bookstore finder API' "
    "(domain-specific — bookstore and Paris are sub-task nouns)\n"
    "  Right query: 'place search API' or 'business locator API' "
    "(capability-broad — judge filters for 'bookstore' downstream)"
)

_USER_PROMPT_TEMPLATE = (
    "Capability: {capability}\n"
    "Sub-task (CONTEXT ONLY — do not copy nouns from this): {task_text}\n"
    "Constraints (may be empty): {constraints}\n"
    "Sources to produce queries for: {scout_ids}\n\n"
    "Per-source phrasing reminders (still capability-broad, never "
    "domain-specific):\n"
    "* official_mcp_registry → MCP server names + capability nouns\n"
    "* apis_guru → API/spec category that publishes OpenAPI specs\n"
    "* hacker_news_agent_watch → 'Show HN' style launch phrasing for "
    "the capability\n"
    "* vendor_rss → vendor blog announcement phrasing for the capability\n"
    "* github_code_search → topic / filename / language keywords for the "
    "capability\n"
    "* github_recently_pushed → capability noun + agent/MCP/A2A keyword\n\n"
    "Return JSON only."
)


def expand_for_scouts(
    *,
    capability: str,
    task_text: str,
    constraints: Iterable[str] = (),
    chat_client: ChatClient | None = None,
    scout_ids: Iterable[str] = DEFAULT_SCOUT_IDS,
) -> ExpandedQueries:
    """Expand one capability gap into per-scout query strings.

    Args:
        capability: The capability id the planner says is missing
            (e.g., "kyc_aml_check"). Required even though it can be
            empty — empty means "no specific capability, free-form
            search" which is still meaningful.
        task_text: The planner sub-task description (e.g., "Verify the
            buyer's identity per UAE KYC regulations").
        constraints: Optional extra constraints (region, license,
            authentication preference). Forwarded verbatim to the LLM.
        chat_client: Optional Qwen / Groq client. If `None`, the
            deterministic rule-based fallback is used.
        scout_ids: Override the default fleet (useful for tests).

    Returns:
        ExpandedQueries with one query per scout. Always returns a
        result (never raises) — falls back to rule-based on any error.
    """

    scout_ids = tuple(scout_ids)
    if not chat_client:
        return _rule_based_expand(
            capability=capability,
            task_text=task_text,
            constraints=tuple(constraints),
            scout_ids=scout_ids,
            fallback_reason="no_chat_client",
        )

    constraints_text = ", ".join(c for c in constraints if c) or "(none)"
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        capability=capability or "(none)",
        task_text=task_text or "(none)",
        constraints=constraints_text,
        scout_ids=", ".join(scout_ids),
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw = ""
    try:
        raw = chat_client.complete(messages)
    except Exception as exc:  # noqa: BLE001 — chat clients can fail for many reasons
        LOGGER.info("query_expansion: chat_client failed (%s)", exc)
        return _rule_based_expand(
            capability=capability,
            task_text=task_text,
            constraints=tuple(constraints),
            scout_ids=scout_ids,
            fallback_reason=f"chat_client_error:{type(exc).__name__}",
        )

    parsed = _parse_json_object(raw)
    if not parsed:
        return _rule_based_expand(
            capability=capability,
            task_text=task_text,
            constraints=tuple(constraints),
            scout_ids=scout_ids,
            fallback_reason="invalid_llm_json",
        )

    cleaned = _clean_per_scout_queries(parsed, scout_ids=scout_ids)
    if not cleaned:
        return _rule_based_expand(
            capability=capability,
            task_text=task_text,
            constraints=tuple(constraints),
            scout_ids=scout_ids,
            fallback_reason="empty_llm_payload",
        )

    return ExpandedQueries(
        per_scout_queries=cleaned, used_llm=True, raw_response=raw
    )


def _parse_json_object(raw: str) -> dict | None:
    """Extract the first top-level JSON object from a chat completion.
    Models sometimes wrap JSON in ```json fences or prepend chatter; we
    locate the first ``{ ... }`` block and parse it."""

    if not raw:
        return None
    text = raw.strip()
    # Strip common fenced-code wrappings.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Find the first matching brace block.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _clean_per_scout_queries(
    payload: dict, *, scout_ids: tuple[str, ...]
) -> dict[str, str]:
    """Filter `payload` down to the requested scout ids; coerce values
    to short strings; drop empties."""

    out: dict[str, str] = {}
    for scout_id in scout_ids:
        value = payload.get(scout_id)
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if 1 <= len(cleaned) <= 200:
            out[scout_id] = cleaned
    return out


def _rule_based_expand(
    *,
    capability: str,
    task_text: str,
    constraints: tuple[str, ...],
    scout_ids: tuple[str, ...],
    fallback_reason: str,
) -> ExpandedQueries:
    """Deterministic per-scout query phrasings used when the LLM is
    unavailable. Each phrasing biases the underlying source toward
    its strengths (see comments in `_USER_PROMPT_TEMPLATE`)."""

    capability_phrase = capability.replace("_", " ") if capability else ""
    task_phrase = (task_text or "").strip()
    base = " ".join(p for p in [capability_phrase, task_phrase] if p) or task_phrase
    constraints_phrase = " ".join(constraints).strip()
    suffix = f" {constraints_phrase}" if constraints_phrase else ""

    by_scout: dict[str, str] = {}
    for scout_id in scout_ids:
        if scout_id == "official_mcp_registry":
            by_scout[scout_id] = (capability_phrase or base) + " mcp server" + suffix
        elif scout_id == "apis_guru":
            by_scout[scout_id] = (capability_phrase or base) + " api" + suffix
        elif scout_id == "hacker_news_agent_watch":
            by_scout[scout_id] = (
                f"Show HN {capability_phrase or base}".strip() + suffix
            )
        elif scout_id == "vendor_rss":
            by_scout[scout_id] = (capability_phrase or base) + " announcement" + suffix
        elif scout_id == "github_code_search":
            by_scout[scout_id] = (
                f"topic {capability_phrase} OR {task_phrase}".strip() + suffix
            )
        elif scout_id == "github_recently_pushed":
            by_scout[scout_id] = (capability_phrase or base) + " agent" + suffix
        else:
            by_scout[scout_id] = base + suffix

    return ExpandedQueries(
        per_scout_queries=by_scout,
        used_llm=False,
        fallback_reason=fallback_reason,
    )
