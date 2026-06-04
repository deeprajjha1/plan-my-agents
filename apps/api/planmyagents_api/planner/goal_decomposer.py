"""LLM-driven goal decomposition.

This module replaces the older ``intent_mapper`` + ``coverage_audit``
pair. The previous design asked the LLM to *pick capability ids from a
fixed catalog*, which mechanically biased the output toward whatever
capabilities happened to be tagged on the indexed agents — and the
prompts themselves enumerated commerce/compliance/cross-boundary
patterns to look for, which biased the LLM further toward
transactional decompositions even for research/outreach goals.

The new design has one rule: **the LLM writes the workflow a human
would actually perform**, in its own words, with no fixed vocabulary
to pick from. The catalog is passed only as an *optional hint* so
labels can be reused across requests for downstream aggregation
(leaderboards, demand events). The LLM is explicitly told it may coin
new labels when nothing in the hint fits — coining is preferred over
distorting the meaning of the goal.

For each sub-task the LLM produces:

* ``description`` — one sentence describing the action.
* ``user_facing_step`` — short imperative line shown to the user as
  "what I'd do for you".
* ``search_query`` — 3-8 word phrase the scouts use to look for
  matching agents (capability-shaped, not goal-specific).
* ``acceptance_criteria`` — one sentence the downstream judge uses to
  decide whether a discovered candidate actually fits.
* ``suggested_capability_id`` — snake_case label for the capability,
  reused from the catalog hint if applicable, otherwise freshly coined.

The downstream pipeline (scouts, judge, demand recorder, discovery
gaps recorder) consumes the rich :class:`DecomposedSubTask` objects
directly — no slug-only intermediate representation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from planmyagents_api.llm.escalating_client import (
    EscalatingChatClient,
    NoLlmTierAvailableError,
    QualityVerdict,
    build_default_escalating_client,
)
from planmyagents_api.planner.capability_catalog import CapabilityCatalog
from planmyagents_api.planner.local_qwen import ChatClient, LocalQwenPlannerError

LOGGER = logging.getLogger(__name__)

DEFAULT_MIN_CONFIDENCE = 0.55
MAX_SUB_TASKS = 8
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class GoalDecompositionError(RuntimeError):
    """Raised when the decomposer cannot produce a usable result."""


@dataclass(frozen=True)
class DecomposedSubTask:
    """One atomic step in the LLM-generated workflow.

    All five fields are required and non-empty. Validation happens at
    parse time so downstream code (scouts, judge, recorders) can rely
    on every field being usable.
    """

    description: str
    user_facing_step: str
    search_query: str
    acceptance_criteria: str
    suggested_capability_id: str

    def to_json(self) -> dict[str, str]:
        return {
            "description": self.description,
            "user_facing_step": self.user_facing_step,
            "search_query": self.search_query,
            "acceptance_criteria": self.acceptance_criteria,
            "suggested_capability_id": self.suggested_capability_id,
        }


@dataclass(frozen=True)
class GoalDecomposition:
    """Aggregate result of one decomposition call.

    ``catalog_reused_capabilities`` and ``new_capabilities`` partition
    the suggested ids into "matched against the catalog hint" vs
    "freshly coined" — useful for instrumentation and for the
    leaderboard rollup that runs as a separate slice.
    """

    intent_summary: str
    sub_tasks: list[DecomposedSubTask]
    confidence: float
    catalog_reused_capabilities: list[str] = field(default_factory=list)
    new_capabilities: list[str] = field(default_factory=list)
    raw_response: str = ""

    @property
    def suggested_capability_ids(self) -> list[str]:
        """Stable, deduplicated capability ids in declaration order."""
        seen: set[str] = set()
        ordered: list[str] = []
        for sub_task in self.sub_tasks:
            if sub_task.suggested_capability_id in seen:
                continue
            seen.add(sub_task.suggested_capability_id)
            ordered.append(sub_task.suggested_capability_id)
        return ordered

    def to_json(self) -> dict[str, Any]:
        return {
            "intent_summary": self.intent_summary,
            "confidence": self.confidence,
            "sub_tasks": [sub.to_json() for sub in self.sub_tasks],
            "catalog_reused_capabilities": list(self.catalog_reused_capabilities),
            "new_capabilities": list(self.new_capabilities),
        }


def decompose_goal(
    goal: str,
    *,
    catalog_hint: CapabilityCatalog | None = None,
    catalog_descriptions: dict[str, dict[str, Any]] | None = None,
    client: ChatClient | None = None,
    min_confidence: float | None = None,
) -> GoalDecomposition:
    """Run the LLM decomposer over ``goal``.

    Args:
        goal: User goal text. Must be non-empty after stripping.
        catalog_hint: Optional capability catalog. Its capability ids
            are passed to the LLM as a hint — labels the system has
            already seen — so cross-request aggregation can reuse
            them. The LLM is explicitly free to coin new labels.
        catalog_descriptions: Optional ``{slug: {description, examples}}``
            mapping for router-supported capabilities (loaded from
            ``packages/registry/capability_descriptions.json``). When
            present, the slugs in this mapping are surfaced to the
            LLM as a **strongly-preferred reuse pool**: the prompt
            tells the LLM to reuse one of these slugs whenever it
            plausibly covers a sub-task. Slugs in ``catalog_hint`` but
            NOT in ``catalog_descriptions`` are surfaced as a softer
            secondary pool (the existing fall-back behaviour). The
            decomposer is still free to coin new slugs when nothing in
            either pool fits — coining drives the discovery-gap signal
            and must remain available. When omitted, behaviour reduces
            to the legacy flat-list catalog hint exactly.
        client: Optional chat client. Defaults to the escalating
            client (Groq primary, local Qwen fallback by default).
        min_confidence: Confidence floor below which a result is
            treated as a refusal. Defaults to
            ``PLANMYAGENTS_GOAL_DECOMPOSER_MIN_CONFIDENCE`` env var,
            or ``DEFAULT_MIN_CONFIDENCE`` if that is unset.

    Raises:
        GoalDecompositionError: when the LLM is unavailable, the
            response cannot be parsed into the strict schema, or the
            decomposition is below the confidence floor.
    """

    normalized = (goal or "").strip()
    if not normalized:
        raise GoalDecompositionError("Goal decomposer requires a non-empty goal.")

    chat_client = client or build_default_escalating_client()
    catalog_ids = sorted(catalog_hint.capability_ids) if catalog_hint else []
    described_capabilities: dict[str, dict[str, Any]] = catalog_descriptions or {}
    messages = _build_messages(
        normalized,
        catalog_ids=catalog_ids,
        described_capabilities=described_capabilities,
    )

    floor = (
        float(os.getenv("PLANMYAGENTS_GOAL_DECOMPOSER_MIN_CONFIDENCE", str(DEFAULT_MIN_CONFIDENCE)))
        if min_confidence is None
        else min_confidence
    )

    # When the chat client is an EscalatingChatClient, route through
    # ``complete_with_metadata(quality_check=...)`` so a transport-success
    # but content-bad answer (e.g. ``llama-4-scout`` returning a
    # sub-task with empty ``search_query``) escalates to the NEXT tier
    # instead of dead-ending the request.
    #
    # Without this, the decomposer's strict parser would raise
    # ``GoalDecompositionError`` on the first tier's reply and the
    # entire /goal request would fall to the rule-based fallback —
    # producing a single inferred capability rather than the 3-4 sub
    # tasks the goal actually warrants. With it, the next Groq model
    # (or Ollama) gets a chance to produce a well-formed plan.
    #
    # The quality_check is intentionally LENIENT — it only rejects
    # responses that the strict parser will definitely fail on, so a
    # well-formed but low-confidence plan still ships from the first
    # tier (rather than burning latency rotating through every tier
    # for the same imperfect answer).
    if isinstance(chat_client, EscalatingChatClient):
        try:
            result = chat_client.complete_with_metadata(
                messages,
                quality_check=lambda content: _decomposer_quality_verdict(
                    content,
                    catalog_ids=catalog_ids,
                    floor=floor,
                ),
            )
        except (LocalQwenPlannerError, NoLlmTierAvailableError) as exc:
            raise GoalDecompositionError(
                f"Goal decomposer LLM is unavailable: {exc}"
            ) from exc
        raw = result.content
    else:
        try:
            raw = chat_client.complete(messages)
        except (LocalQwenPlannerError, NoLlmTierAvailableError) as exc:
            raise GoalDecompositionError(
                f"Goal decomposer LLM is unavailable: {exc}"
            ) from exc

    payload = _parse_json_object(raw)
    decomposition = _decomposition_from_payload(
        payload,
        catalog_ids=catalog_ids,
        described_ids=list(described_capabilities),
        raw_response=raw,
    )

    if decomposition.confidence < floor:
        raise GoalDecompositionError(
            f"Goal decomposer confidence {decomposition.confidence:.2f} is below {floor:.2f}."
        )
    return decomposition


def _decomposer_quality_verdict(
    content: str,
    *,
    catalog_ids: list[str],
    floor: float,
) -> QualityVerdict:
    """Quality-check callback used by the escalating chat client.

    Returns ``ESCALATE`` if the LLM response will definitely be rejected
    by the strict parser (so the next tier gets a chance), and
    ``ACCEPT`` otherwise.

    The check is intentionally narrower than the full validation in
    :func:`_decomposition_from_payload` — we only catch the failures
    where the next tier is actually likely to do better, not every
    schema deviation. Specifically:

    * unparseable JSON                     → ESCALATE  (next tier may format better)
    * missing/empty ``sub_tasks``          → ESCALATE  (next tier may produce them)
    * any sub_task field empty/missing     → ESCALATE  (BUG-1: scout's empty search_query)
    * confidence below the configured floor → ESCALATE (next tier may be more decisive)
    * any other parse error                → ACCEPT (let the outer raise GoalDecompositionError)

    The last case is deliberate: if the response is malformed in some
    way we DON'T know how to recover from, escalating to a slower
    tier is unlikely to help — better to surface the parse error to
    the user immediately than burn 60-110s on local Qwen for the same
    bad answer.
    """

    payload = _parse_json_object(content)
    if not isinstance(payload, dict):
        return QualityVerdict.ESCALATE
    raw_sub_tasks = payload.get("sub_tasks")
    if not isinstance(raw_sub_tasks, list) or not raw_sub_tasks:
        return QualityVerdict.ESCALATE
    # Reject any sub_task with an empty/missing required field.
    # This is the exact failure mode that motivated A1: a single empty
    # ``search_query`` was killing the whole decomposition path.
    required = (
        "description",
        "user_facing_step",
        "search_query",
        "acceptance_criteria",
        "suggested_capability_id",
    )
    for raw_sub in raw_sub_tasks[:MAX_SUB_TASKS]:
        if not isinstance(raw_sub, dict):
            return QualityVerdict.ESCALATE
        for field_name in required:
            value = raw_sub.get(field_name)
            if not isinstance(value, str) or not value.strip():
                return QualityVerdict.ESCALATE
    confidence = _coerce_confidence(payload.get("confidence"))
    if confidence < floor:
        return QualityVerdict.ESCALATE
    # Note: catalog_ids is intentionally unused here. The quality check
    # only enforces parser-survival, not catalog reuse — coining new
    # ids is by design (see module docstring) and never warrants
    # rotating through more tiers.
    del catalog_ids
    return QualityVerdict.ACCEPT


# Prompt design notes
# -------------------
# This prompt deliberately does NOT enumerate any family of constraints
# (compliance, cross-border, payment, research, outreach, …). Every
# enumeration biases the LLM toward over-producing capabilities of the
# enumerated kind — see the post-mortem in ``goal_decomposer`` module
# docstring above. The prompt instead frames the task as "write the
# workflow a competent human would actually do" and lets the model
# decompose freely.
#
# The catalog hint is passed as ids only (no aliases). The model is
# told to PREFER existing labels but is explicitly free to coin new
# ones. This is the inversion of the old contract, which validated
# the LLM's output against a closed set and rejected unknown ids.
_SYSTEM_PROMPT = """
You are PlanMyAgents's goal decomposer.

A user has stated a goal in their own words. Your job is to write the
sub-tasks a competent human would actually perform to achieve that
goal end-to-end. For each sub-task, also describe what kind of agent
or tool would do it, write a short search query someone would type
into a tool registry to find such an agent, and write an acceptance
test that a downstream judge can use to filter discovered candidates.

Hard rules:

1. Decompose the goal into 1-8 sub-tasks. Each sub-task is one atomic
   action that could be delegated to ONE tool or agent. Do not bundle
   multiple distinct actions into a single sub-task.
2. Order the sub-tasks in the realistic execution order a human would
   follow.
3. For each sub-task, return exactly these fields:
   - description: one sentence describing what the sub-task does and
     what its inputs/outputs are. User-facing language.
   - user_facing_step: imperative-mood, <= 12 words, written so a
     non-technical user can read it as "what I'd do for you".
   - search_query: 3-8 words you would type into a tool registry or
     Google to find an agent that performs this kind of action.
     Capability-shaped, not goal-specific. Example: "send bulk email
     API" rather than "email Bengaluru car dealers". The downstream
     judge filters results back against the user's actual goal.
   - acceptance_criteria: one sentence telling a downstream judge
     what a discovered candidate must actually do for it to count as
     "this sub-task could use it".
   - suggested_capability_id: a snake_case label naming this kind of
     capability. 3-64 chars, lowercase, alphanumeric + underscore,
     starts with a letter. **STRONGLY prefer reusing an id from
     `catalog_hint.router_supported`** whenever any of those slugs
     plausibly covers the sub-task — those are the only slugs the
     PlanMyAgents router can actually execute end-to-end, and reusing
     them is the difference between the user getting a working
     workflow versus a refusal. Read each slug's `description` and
     `examples` and pick the one whose meaning matches. Examples of
     correct reuse: a "buy / purchase / checkout / pay for" sub-task
     reuses `payment_authorization`; a "find / research / look up /
     discover options" sub-task reuses `web_search`; a "compare prices
     / find cheapest / filter by budget" sub-task reuses
     `price_comparison`; an "arrange shipping / estimate delivery"
     sub-task reuses `shipping_quote`; a "find stores near a city"
     sub-task reuses `store_locator`. Reuse from
     `other_known_capability_ids` only as a soft fallback for
     cross-request labeling — those slugs are NOT executable. Coin a
     NEW snake_case label only when no slug in either pool plausibly
     fits the sub-task; coining is the honest discovery-gap signal but
     must not be used to invent a synonym for a slug that's already in
     the pool.
4. confidence is your overall assessment of how well your decomposition
   covers the goal, 0.0-1.0. Use < 0.55 only when the goal is too
   ambiguous to decompose.

Output strict JSON. No markdown. No prose. No commentary.

Schema:
{
  "intent_summary": "<short summary of the user's real-world intent>",
  "confidence": 0.0-1.0,
  "sub_tasks": [
    {
      "description": "...",
      "user_facing_step": "...",
      "search_query": "...",
      "acceptance_criteria": "...",
      "suggested_capability_id": "..."
    }
  ]
}
""".strip()


def _build_messages(
    goal: str,
    *,
    catalog_ids: list[str],
    described_capabilities: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Compose the messages for the decomposer LLM call.

    The user payload has up to three sections under ``catalog_hint``:

    * ``router_supported`` — the router-supported capabilities with
      one-line descriptions and 2-5 example sub-tasks each. Reusing one
      of these slugs means the request can be executed end-to-end (a
      router entry exists). The LLM is told to STRONGLY prefer these
      and to reuse one whenever it plausibly covers a sub-task. This
      is the section that closes the "decomposer coins synonyms the
      registry can't match" gap; without it the LLM was forced to
      guess from slug names alone.
    * ``other_known_capability_ids`` — slugs the system has seen via
      discovery / past requests but for which no router entry exists.
      Surfaced as a softer secondary pool: the LLM may reuse for
      cross-request aggregation, but reuse here does NOT make a goal
      executable.
    * (omitted entirely when both pools are empty)

    Slug ordering is preserved in each section so prompt output is
    reproducible across requests with the same inputs (cache-key
    friendly).
    """

    described = described_capabilities or {}
    described_ids = sorted(described)
    other_ids = sorted(set(catalog_ids) - set(described_ids))

    user_payload: dict[str, Any] = {"goal": goal}
    if described_ids or other_ids:
        catalog_hint: dict[str, Any] = {
            "explanation": (
                "These capability ids are part of the PlanMyAgents catalog. "
                "STRONGLY prefer reusing a `router_supported` id whenever it "
                "plausibly covers a sub-task — those are the only slugs the "
                "router can execute end-to-end. Treat `other_known_capability_ids` "
                "as a softer secondary pool used only for cross-request label "
                "aggregation. Coin a NEW snake_case label only when nothing in "
                "either pool fits — coining is the honest discovery-gap signal."
            ),
        }
        if described_ids:
            catalog_hint["router_supported"] = {
                slug: {
                    "description": described[slug].get("description", ""),
                    "examples": list(described[slug].get("examples", [])),
                }
                for slug in described_ids
            }
        if other_ids:
            catalog_hint["other_known_capability_ids"] = other_ids
        user_payload["catalog_hint"] = catalog_hint
    user = json.dumps(user_payload, indent=2, sort_keys=True)
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _decomposition_from_payload(
    payload: dict[str, Any] | None,
    *,
    catalog_ids: list[str],
    described_ids: list[str] | None = None,
    raw_response: str,
) -> GoalDecomposition:
    """Parse the LLM payload into a :class:`GoalDecomposition`.

    The ``catalog_reused`` / ``new_capabilities`` partition counts a
    slug as reused if it appears in **either** ``catalog_ids`` (the
    legacy flat-list catalog hint) **or** ``described_ids`` (the new
    router-supported descriptions pool). Both pools are real reuse
    signals: a slug from the descriptions file is router-executable
    AND has a description the LLM was explicitly told to prefer, so
    booking it as "new" would under-count the most useful reuse path
    on the leaderboards and discovery-gap signal.
    """

    if not payload or not isinstance(payload, dict):
        raise GoalDecompositionError(
            "Goal decomposer did not return a JSON object."
        )

    raw_sub_tasks = payload.get("sub_tasks")
    if not isinstance(raw_sub_tasks, list) or not raw_sub_tasks:
        raise GoalDecompositionError(
            "Goal decomposer returned no sub_tasks."
        )

    catalog_set = set(catalog_ids) | set(described_ids or [])
    sub_tasks: list[DecomposedSubTask] = []
    catalog_reused: list[str] = []
    new_capabilities: list[str] = []
    seen_capability_ids: set[str] = set()

    for index, raw_sub in enumerate(raw_sub_tasks[:MAX_SUB_TASKS]):
        if not isinstance(raw_sub, dict):
            raise GoalDecompositionError(
                f"sub_tasks[{index}] is not a JSON object."
            )
        sub_task = _sub_task_from_payload(raw_sub, index=index)
        sub_tasks.append(sub_task)
        if sub_task.suggested_capability_id in seen_capability_ids:
            # We allow duplicates — two sub-tasks may legitimately
            # share a capability id (e.g. two web_search calls with
            # different queries). Don't double-count in the
            # reused/new partitions.
            continue
        seen_capability_ids.add(sub_task.suggested_capability_id)
        if sub_task.suggested_capability_id in catalog_set:
            catalog_reused.append(sub_task.suggested_capability_id)
        else:
            new_capabilities.append(sub_task.suggested_capability_id)

    intent_summary = str(payload.get("intent_summary") or "").strip()
    if not intent_summary:
        intent_summary = "Decomposed user goal into sub-tasks."

    confidence = _coerce_confidence(payload.get("confidence"))

    return GoalDecomposition(
        intent_summary=intent_summary,
        sub_tasks=sub_tasks,
        confidence=confidence,
        catalog_reused_capabilities=catalog_reused,
        new_capabilities=new_capabilities,
        raw_response=raw_response,
    )


def _sub_task_from_payload(payload: dict[str, Any], *, index: int) -> DecomposedSubTask:
    description = _required_string(payload.get("description"), field="description", index=index)
    user_facing_step = _required_string(
        payload.get("user_facing_step"), field="user_facing_step", index=index
    )
    search_query = _required_string(payload.get("search_query"), field="search_query", index=index)
    acceptance_criteria = _required_string(
        payload.get("acceptance_criteria"), field="acceptance_criteria", index=index
    )
    suggested_capability_id = _required_string(
        payload.get("suggested_capability_id"),
        field="suggested_capability_id",
        index=index,
    )
    coerced_id = _coerce_capability_id(suggested_capability_id)
    if not _SNAKE_CASE.match(coerced_id):
        raise GoalDecompositionError(
            f"sub_tasks[{index}].suggested_capability_id "
            f"'{suggested_capability_id}' is not a valid snake_case id."
        )
    return DecomposedSubTask(
        description=description,
        user_facing_step=user_facing_step,
        search_query=search_query,
        acceptance_criteria=acceptance_criteria,
        suggested_capability_id=coerced_id,
    )


def _required_string(value: Any, *, field: str, index: int) -> str:
    if not isinstance(value, str):
        raise GoalDecompositionError(
            f"sub_tasks[{index}].{field} is missing or not a string."
        )
    cleaned = value.strip()
    if not cleaned:
        raise GoalDecompositionError(
            f"sub_tasks[{index}].{field} is empty."
        )
    return cleaned


def _coerce_capability_id(raw: str) -> str:
    """Normalise a capability id the LLM might have returned in
    near-snake-case (e.g. ``"Web Search"`` or ``"web-search"``).
    Lower-cases, replaces spaces and hyphens with underscores, and
    strips trailing/leading underscores. Returns the result; the
    caller is responsible for validating against the snake_case
    regex."""

    return (
        raw.strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .strip("_")
    )


def _coerce_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Tolerant extractor for the first ``{ ... }`` block in ``raw``.

    Models occasionally wrap JSON in markdown fences or prepend
    chatter; this function locates the first balanced object and
    parses it. Returns ``None`` if no object can be parsed.
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
