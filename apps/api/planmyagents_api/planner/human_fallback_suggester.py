"""LLM-driven "what can the user do manually" suggester.

Background
----------
When ``/goal`` refuses because no agent matches the missing capabilities,
today's response is an honest-but-cold ``status: unsupported``. The user
sees a red banner and has nowhere to go. The original "liquor stores in
southern India" forensic walked through this exact UX hole: the system
correctly identified that no specialist agent for that domain exists,
but it also failed to give the user the obvious next step — *here are
three websites where you can do this manually*.

This module fills the hole. After the refusal path has confirmed there
are no routable agents, we make **one** LLM call asking the model to
propose 2–4 real, public websites or services the user could use as a
manual workaround. The model is given the full goal text and the list
of capabilities the system gave up on, so it can suggest fallbacks that
match what the user actually wanted (not just the slug shape).

Design choices and the reasoning behind them
--------------------------------------------
* **One batched LLM call, not one per capability.** The user's goal is
  a single human request and the suggested alternatives should reflect
  the *whole* goal, not piecemeal per slug. A liquor-store goal
  decomposed into ``store_locator + price_comparison`` should suggest
  liquor-retailer chains (Living Liquidz, Tonique), not "Yelp" for
  store_locator and "Google Shopping" for price_comparison. The
  batched prompt gives the model the whole context.

* **Capability ids are a *hint*, not a constraint.** We pass the
  per-capability ``user_facing_step`` text from the decomposer when it
  is available, so the model can reason about *what the user wanted to
  do at each step* rather than guessing from snake_case slugs. This is
  the same anti-bias principle that drives the decomposer and the
  reconciler — let the model reason on text, not on ids.

* **Strict URL/scheme validation.** The suggester surfaces links into
  the UI; we treat anything that doesn't look like a real public
  ``http(s)`` URL as a hallucination and drop it. Better to return
  fewer alternatives than to ship a bad link the user might trust.
  We also dedupe by lowercased host so the same site doesn't show
  twice when the model emits both ``site.com`` and ``www.site.com``.

* **Best-effort, never blocks ``/goal``.** Same contract as the
  reconciler: an LLM tier outage, a malformed payload, or an
  unsupported response shape all degrade to "no alternatives" with a
  loud INFO log. The user still gets the refusal payload they would
  have gotten before this module existed.

* **Opt-out via env var.** ``PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER``
  toggles the whole module off, mirroring the decomposer/reconciler
  flag pattern. Tests use this to get a deterministic "no suggester"
  pipeline without stubbing the LLM.

What this module deliberately does NOT do
-----------------------------------------
* It does not pick "the best" alternative. The model is asked for
  2–4 candidates; the UI ranks them in declaration order (which the
  prompt asks the model to put in confidence order). Picking a single
  winner would require a second LLM pass and is not worth the latency.
* It does not curate per-capability fallback lists in code. That
  approach would require maintaining a hand-edited mapping per
  capability slug — exactly the static-lookup model the user has
  rejected throughout this project. The intelligence stays in the
  LLM at request time.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from planmyagents_api.llm.escalating_client import (
    NoLlmTierAvailableError,
    build_default_escalating_client,
)
from planmyagents_api.planner.local_qwen import ChatClient, LocalQwenPlannerError

LOGGER = logging.getLogger(__name__)

# Hard caps the prompt asks for. The lower bound is a quality target
# (a single suggestion looks weak), the upper bound is a length cap
# (four links fit comfortably in the refusal card without overwhelming).
MIN_ALTERNATIVES_REQUESTED = 2
MAX_ALTERNATIVES_REQUESTED = 4

# Defensive cap on how many we ever return regardless of what the
# model does. The schema validator already enforces this, but the
# constant lives here so the prompt and the code can't drift.
HARD_CAP_ALTERNATIVES_RETURNED = 6


class HumanFallbackSuggesterError(RuntimeError):
    """Raised internally when the LLM returns an unparseable payload.

    Callers should catch and degrade to "no alternatives" rather than
    propagating — this is an opportunistic UX improvement, not a
    request-blocking dependency.
    """


@dataclass(frozen=True)
class HumanAlternative:
    """One suggested manual workaround.

    ``url`` is a validated public ``http(s)`` URL. ``name`` is a short
    human-readable label (the model is asked for the brand name rather
    than the URL itself, since URLs are rarely the most readable label
    for a list). ``why`` is a one-sentence rationale the UI surfaces
    so the user understands *why this site fits their goal* rather
    than seeing an opaque list of links.
    """

    url: str
    name: str
    why: str

    def to_json(self) -> dict[str, Any]:
        return {"url": self.url, "name": self.name, "why": self.why}


@dataclass(frozen=True)
class HumanFallbackSuggestion:
    """Aggregate result of one suggester pass.

    ``status`` is one of:

    * ``"applied"`` — suggester ran and produced at least one valid
      alternative (after URL validation and dedup).
    * ``"empty"`` — suggester ran successfully but yielded zero
      validated alternatives (model returned hallucinated URLs, or
      the goal genuinely has no plausible web fallback).
    * ``"skipped_no_capabilities"`` — caller passed an empty
      ``missing_capabilities`` list; we have no signal to send to the
      model and would just generate generic links.
    * ``"unavailable"`` — every LLM tier failed; the ``reason`` field
      carries the exception text. Caller should render the refusal
      payload as it would pre-suggester.
    * ``"disabled"`` — env-var off; the module short-circuited.
    """

    status: str
    alternatives: list[HumanAlternative] = field(default_factory=list)
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "alternatives": [a.to_json() for a in self.alternatives],
        }


def suggester_enabled() -> bool:
    """Honour ``PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER`` (default on).

    Setting it to ``off``/``0``/``false``/``no`` skips the suggester
    entirely. Provided for tests that want a deterministic
    "no suggester" pipeline without stubbing the LLM client and for
    operators who want to validate latency impact independently.
    """

    return os.getenv("PLANMYAGENTS_HUMAN_FALLBACK_SUGGESTER", "on").lower() not in {
        "0",
        "false",
        "off",
        "none",
        "disabled",
        "no",
    }


def suggest_human_alternatives(
    *,
    goal: str,
    missing_capabilities: list[str],
    decomposed_sub_tasks: list[dict[str, Any]] | None = None,
    client: ChatClient | None = None,
) -> HumanFallbackSuggestion:
    """Run one batched LLM call to propose manual workarounds.

    Args:
        goal: The original free-text goal the user submitted. The
            suggester needs the full text (not just the slug list)
            because the brand-level fallback ("Living Liquidz" vs
            "Yelp") depends on the *domain* of the goal.
        missing_capabilities: Capability ids the system gave up on.
            Empty list → ``status="skipped_no_capabilities"``.
        decomposed_sub_tasks: Optional per-sub-task objects from the
            decomposer (``description``, ``user_facing_step``, etc.).
            When supplied, the prompt includes the human-readable
            step text per capability so the model can suggest
            alternatives that match the *intent* rather than the
            slug. When absent, the prompt falls back to slug-only.
        client: Optional chat client for tests. Defaults to the
            escalating client (Groq primary, Qwen fallback).

    Returns:
        :class:`HumanFallbackSuggestion`. Never raises — LLM failures
        and parse failures are reported via ``status="unavailable"``
        so the caller can include the refusal payload regardless.
    """

    cleaned_goal = (goal or "").strip()
    cleaned_capabilities = _clean_capabilities(missing_capabilities)
    if not cleaned_capabilities:
        return HumanFallbackSuggestion(status="skipped_no_capabilities")

    chat_client = client or build_default_escalating_client()
    prompt = _build_messages(
        goal=cleaned_goal,
        missing_capabilities=cleaned_capabilities,
        decomposed_sub_tasks=decomposed_sub_tasks or [],
    )
    try:
        raw = chat_client.complete(prompt)
    except (LocalQwenPlannerError, NoLlmTierAvailableError) as exc:
        LOGGER.info(
            "human_fallback_suggester: LLM unavailable; no alternatives surfaced (%s)",
            exc,
        )
        return HumanFallbackSuggestion(status="unavailable", reason=str(exc))

    try:
        alternatives = _parse_response(raw)
    except HumanFallbackSuggesterError as exc:
        LOGGER.info(
            "human_fallback_suggester: malformed LLM response; degrading to empty (%s)",
            exc,
        )
        return HumanFallbackSuggestion(status="unavailable", reason=str(exc))

    if not alternatives:
        # The model parsed but every URL it proposed was invalid or
        # duplicated. We surface ``"empty"`` rather than
        # ``"unavailable"`` so the UI can choose to display
        # "we tried to suggest manual workarounds but couldn't find
        # any" rather than the generic "LLM tier down" message.
        return HumanFallbackSuggestion(status="empty")

    return HumanFallbackSuggestion(status="applied", alternatives=alternatives)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _clean_capabilities(capabilities: list[str]) -> list[str]:
    """Return the deduplicated list of non-empty capability ids in
    declaration order. Lowercased to match how the rest of the system
    treats slugs."""

    seen: set[str] = set()
    ordered: list[str] = []
    for raw in capabilities or []:
        cid = (raw or "").strip().lower()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ordered.append(cid)
    return ordered


_SYSTEM_PROMPT = f"""
You are PlanMyAgents's "human fallback suggester".

Context
-------
A user submitted a goal. PlanMyAgents broke it into sub-tasks and
tried to find AI agents that can solve each one, but no agent in the
current registry can. Rather than telling the user "sorry, no agent
exists", we want to give them a graceful manual workaround: a small
list of real, public websites or services where they could
accomplish the same goal by hand.

Your job is to suggest {MIN_ALTERNATIVES_REQUESTED} to {MAX_ALTERNATIVES_REQUESTED} websites
or services that would actually let the user solve their goal
manually. You will be shown:

* the user's original free-text goal
* the list of capabilities the system tried to find an agent for
  (with optional human-readable step text when available)

Hard rules
----------
1. Suggest only **real**, **public** websites or services. Do not invent URLs.
   If you are not confident a URL exists, omit it.
2. Each ``url`` MUST be a fully qualified ``http://`` or ``https://``
   URL. No bare hostnames, no paths-without-scheme, no email
   addresses.
3. Each suggestion should be ranked by how well it actually serves
   the user's goal as a whole — first item is the best fit. Do not
   reorder alphabetically.
4. ``name`` is the human-readable brand or service name (what a
   user would recognise), not the URL. Keep it under 60 characters.
5. ``why`` is one short sentence (under 200 characters) explaining
   why this option fits this user's specific goal. Speak directly
   to the user. Do not repeat the goal text verbatim.
6. If the goal is regional (a specific country, city, or language)
   prefer regional services where they exist. A generic global
   site is acceptable when no regional one is well-known.
7. If you genuinely cannot think of any plausible website or
   service, return an empty ``alternatives`` array. An empty array
   is correct; a fabricated URL is not.

Output strict JSON. No markdown. No prose. No commentary.

Schema:
{{
  "alternatives": [
    {{
      "url": "https://example.com",
      "name": "Example",
      "why": "<one short sentence>"
    }}
  ]
}}
""".strip()


def _build_messages(
    *,
    goal: str,
    missing_capabilities: list[str],
    decomposed_sub_tasks: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build the chat-message pair sent to the LLM.

    The user payload is structured JSON so the model has a clean,
    typed view of the inputs. Per-capability ``user_facing_step``
    text is included when the decomposer ran for this request — it
    gives the model the human-readable intent ("look up cars
    available in India") which yields much better suggestions than
    the bare slug ("vehicle_lookup").
    """

    sub_task_index: dict[str, dict[str, Any]] = {}
    for entry in decomposed_sub_tasks:
        if not isinstance(entry, dict):
            continue
        cap_id = (entry.get("suggested_capability_id") or "").strip().lower()
        if cap_id and cap_id not in sub_task_index:
            sub_task_index[cap_id] = entry

    capabilities_payload: list[dict[str, Any]] = []
    for cap_id in missing_capabilities:
        sub_task = sub_task_index.get(cap_id)
        capabilities_payload.append(
            {
                "id": cap_id,
                "user_facing_step": (sub_task or {}).get("user_facing_step") or "",
                "description": (sub_task or {}).get("description") or "",
            }
        )

    payload = {
        "goal": goal,
        "missing_capabilities": capabilities_payload,
    }
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, indent=2, sort_keys=True)},
    ]


def _parse_response(raw: str) -> list[HumanAlternative]:
    """Parse the LLM response into a list of validated alternatives.

    Tolerant of common formatting drift (markdown fencing, prose
    prefix/suffix). Strict on the inner shape: every URL is
    validated for scheme + host, every alternative is deduped by
    lowercased host so the same site can't appear twice via
    ``site.com`` vs ``www.site.com``.

    Raises :class:`HumanFallbackSuggesterError` only when the
    response is structurally unparseable (no JSON object found,
    missing/non-list ``alternatives``). A response that parses but
    yields zero validated entries is returned as an empty list — the
    caller distinguishes "LLM ran but produced nothing useful" from
    "LLM tier failed" via that empty return.
    """

    payload = _parse_json_object(raw)
    if not payload:
        raise HumanFallbackSuggesterError(
            "Suggester did not return a parseable JSON object."
        )
    raw_alternatives = payload.get("alternatives")
    if not isinstance(raw_alternatives, list):
        raise HumanFallbackSuggesterError(
            "Suggester payload missing or non-list 'alternatives'."
        )

    out: list[HumanAlternative] = []
    seen_hosts: set[str] = set()
    for entry in raw_alternatives:
        if not isinstance(entry, dict):
            continue
        url = _validate_url(entry.get("url"))
        if not url:
            continue
        host_key = _host_key(url)
        if not host_key or host_key in seen_hosts:
            continue
        name = _truncate(_strip(entry.get("name")), max_chars=60)
        why = _truncate(_strip(entry.get("why")), max_chars=200)
        if not name:
            # Without a label the link looks like spam in the UI.
            # Skip rather than synthesising a label from the host.
            continue
        seen_hosts.add(host_key)
        out.append(HumanAlternative(url=url, name=name, why=why))
        if len(out) >= HARD_CAP_ALTERNATIVES_RETURNED:
            break
    return out


def _strip(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _truncate(value: str, *, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "\u2026"


def _validate_url(value: Any) -> str:
    """Return ``value`` only if it parses as an ``http(s)`` URL with
    a non-empty host. Empty / hallucinated / non-string values are
    coerced to ``""`` so the caller can use a single ``if not url``
    check."""

    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    return candidate


def _host_key(url: str) -> str:
    """Normalise a URL to its registrable host key for dedup.

    Strips ``www.`` prefix, lowercases. Two suggestions for the same
    site under different www/non-www variants merge to one — the
    *first* occurrence wins to mirror the prompt's "rank by best fit"
    contract.
    """

    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Tolerant JSON-object extraction matching the reconciler's parser.

    Strips optional markdown fences and locates the first balanced
    ``{ ... }`` block. Kept private to this module so the suggester's
    parser can evolve independently of the reconciler's.
    """

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
