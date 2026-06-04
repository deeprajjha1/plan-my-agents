"""Post-decomposition LLM pass that reconciles freshly-coined labels.

Background
----------
The goal decomposer (:mod:`planmyagents_api.planner.goal_decomposer`) is
intentionally allowed to coin a brand-new ``suggested_capability_id``
whenever nothing in the catalog hint genuinely matches a sub-task.
That freedom is what fixed the old bias problem (the old intent
mapper rejected anything outside its closed catalog), but it
introduces a *new* problem: two requests for semantically equivalent
work may coin two *different* labels for the same capability.

Concrete example. Request A's goal mentions "send personalised
invitation emails" and the decomposer coins ``email_sending``.
Request B's goal says "blast out a launch announcement to my list"
and the decomposer coins ``bulk_email_dispatch``. Without
reconciliation the leaderboards and demand surface treat these as
two unrelated capabilities, the catalog hint never grows in a
useful direction, and the same scout dispatch runs twice for what
is operationally one thing.

Approach
--------
After ``decompose_goal`` returns, this module runs *one* batched LLM
call that asks: "for each newly-coined id below, does it semantically
match any of the existing catalog ids?". The LLM returns either a
match (with a confidence score we floor at a configurable threshold)
or a deliberate "no, this is genuinely new". The caller then either:

* **Match**: rewrites the sub-task's ``suggested_capability_id`` to
  the matched existing id. The aggregate ``new_capabilities`` for the
  decomposition shrinks accordingly. This is the highest-value path
  — every match merges two demand buckets that would otherwise stay
  fragmented.
* **No match**: persists the coined id to the
  :mod:`capability_label_store`, complete with the decomposer's own
  description for later reference. The next request's catalog hint
  will already contain it, so a follow-up "send invitation emails"
  goal sees ``email_sending`` in the hint and the decomposer reuses
  it instead of coining a third synonym.

Design choices and the reasoning behind them
--------------------------------------------
* **One batched LLM call, not one per coined id.** A single
  decomposition can coin five or six new ids; doing one
  reconciliation call per id would 5-6× the latency cost of slice 2
  with no quality improvement. The batched prompt fits well within
  the smallest hosted model's context: the catalog ids are short
  snake_case slugs and the coined-id list is bounded by
  ``MAX_SUB_TASKS`` from the decomposer (8).

* **Confidence floor (default 0.7).** False matches are worse than
  false misses: a false match silently merges two distinct
  capabilities into one demand bucket and we never notice; a false
  miss just keeps two adjacent labels in the vocabulary that ops
  can later decide to merge by hand. The floor errs toward the
  cheaper failure mode.

* **Unbiased prompt.** The same anti-bias principle that drives the
  decomposer applies here: we do NOT enumerate "look for these
  patterns of equivalence" (e.g. "*_sending equals *_dispatch"). The
  prompt frames the task as semantic equivalence judgement against
  free text and lets the model reason. Any enumerated hint biases
  toward over-matching whatever family is named.

* **Best-effort, never blocks ``/goal``.** If the reconciler LLM is
  unavailable or returns malformed JSON, we fall back to "no
  matches, persist everything as new". The user's request flow is
  identical to "reconciler disabled". A loud INFO log records the
  fallback so operators can spot a degraded LLM tier without the
  user ever seeing it.

* **Reconciliation is opt-out via env var.** Same shape as the
  decomposer flag — production should leave it on; tests and
  debugging can switch it off to get a deterministic "no
  reconciliation" pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from planmyagents_api.llm.escalating_client import (
    NoLlmTierAvailableError,
    build_default_escalating_client,
)
from planmyagents_api.planner.goal_decomposer import (
    DecomposedSubTask,
    GoalDecomposition,
)
from planmyagents_api.planner.local_qwen import ChatClient, LocalQwenPlannerError

LOGGER = logging.getLogger(__name__)

DEFAULT_MIN_CONFIDENCE = 0.7
# Hard cap on how many catalog ids we serialise into the user
# prompt. The catalog can grow into the thousands in production but
# the reconciler only needs a representative sample — anything that
# the decomposer has already used historically is a strong candidate
# match. We cap defensively at 256 ids to keep the prompt under a
# small hosted model's friendly token budget. The catalog is sorted
# alphabetically in the prompt so the cap is deterministic across
# requests. (When the catalog is below the cap, all ids are sent.)
MAX_CATALOG_IDS_IN_PROMPT = 256

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class LabelReconciliationError(RuntimeError):
    """Raised internally when the reconciler LLM returns an
    unparseable payload. Callers should catch this and fall back to
    "no matches" rather than failing the request."""


@dataclass(frozen=True)
class LabelMatch:
    """One reconciliation verdict for a coined id.

    ``matched_to`` is None when the LLM judged the coined id to be
    genuinely new (or when its match was below the confidence floor
    after coercion). ``confidence`` is the model-reported confidence
    in the verdict; the caller has already applied the floor by the
    time the match list is returned, so callers can trust the
    verdicts as-is.
    """

    coined_id: str
    matched_to: str | None
    confidence: float
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "coined_id": self.coined_id,
            "matched_to": self.matched_to,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReconciliationResult:
    """Aggregate result of one reconciliation pass.

    ``matches`` carries the LLM's verdict for every coined id that was
    considered. ``status`` is one of:

    * ``"applied"`` — reconciliation ran and produced verdicts.
    * ``"skipped_no_new_capabilities"`` — decomposition coined nothing
      new; reconciliation was a no-op.
    * ``"skipped_no_catalog"`` — catalog hint was empty; nothing to
      match against. Coined ids are returned unmodified.
    * ``"unavailable"`` — LLM tier failed; coined ids are returned
      unmodified. ``reason`` carries the error text.
    * ``"disabled"`` — env-var off; reconciliation skipped entirely.
    """

    status: str
    matches: list[LabelMatch] = field(default_factory=list)
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "min_confidence": self.min_confidence,
            "matches": [m.to_json() for m in self.matches],
            "reason": self.reason,
        }

    @property
    def matched_pairs(self) -> dict[str, str]:
        """Convenience: ``{coined_id: matched_existing_id}`` for every
        match the reconciler accepted. Coined ids without a match are
        absent from this dict."""

        return {m.coined_id: m.matched_to for m in self.matches if m.matched_to}


def reconcile_labels(
    *,
    decomposition: GoalDecomposition,
    catalog_ids: list[str],
    client: ChatClient | None = None,
    min_confidence: float | None = None,
) -> ReconciliationResult:
    """Run the post-decomposition reconciliation pass.

    Args:
        decomposition: The decomposer output. Only its
            ``new_capabilities`` are reconciled; existing-catalog
            reuses are passed through unchanged.
        catalog_ids: The set of capability ids to match against.
            Typically the same catalog hint passed to the
            decomposer, **plus** any labels already persisted by
            earlier requests (UNION semantics — the caller is
            responsible for the union).
        client: Optional chat client for tests. Defaults to the
            escalating client (Groq primary, Qwen fallback).
        min_confidence: Floor below which a model-reported match is
            converted to "no match". Defaults to env var
            ``PLANMYAGENTS_LABEL_RECONCILER_MIN_CONFIDENCE`` or
            :data:`DEFAULT_MIN_CONFIDENCE`.

    Returns:
        :class:`ReconciliationResult`. Never raises — LLM failures
        are reported as ``status="unavailable"`` so the caller can
        proceed with "no matches" and the user's request is never
        blocked on this opportunistic optimisation.
    """

    floor = _resolve_min_confidence(min_confidence)
    coined_ids = _unique_coined_ids(decomposition)
    if not coined_ids:
        return ReconciliationResult(
            status="skipped_no_new_capabilities", min_confidence=floor
        )

    catalog_for_prompt = _prepare_catalog_for_prompt(catalog_ids)
    if not catalog_for_prompt:
        return ReconciliationResult(
            status="skipped_no_catalog",
            min_confidence=floor,
            matches=[
                LabelMatch(coined_id=cid, matched_to=None, confidence=0.0)
                for cid in coined_ids
            ],
        )

    chat_client = client or build_default_escalating_client()
    coined_descriptions = _coined_descriptions(decomposition)
    try:
        raw = chat_client.complete(
            _build_messages(coined_ids, coined_descriptions, catalog_for_prompt)
        )
    except (LocalQwenPlannerError, NoLlmTierAvailableError) as exc:
        LOGGER.info(
            "label_reconciler: LLM unavailable; treating all coined ids as new (%s)",
            exc,
        )
        return ReconciliationResult(
            status="unavailable",
            min_confidence=floor,
            matches=[
                LabelMatch(coined_id=cid, matched_to=None, confidence=0.0)
                for cid in coined_ids
            ],
            reason=str(exc),
        )

    try:
        parsed = _parse_response(
            raw,
            coined_ids=coined_ids,
            catalog_set=set(catalog_for_prompt),
            min_confidence=floor,
        )
    except LabelReconciliationError as exc:
        LOGGER.info(
            "label_reconciler: malformed LLM response; falling back to no-matches (%s)",
            exc,
        )
        return ReconciliationResult(
            status="unavailable",
            min_confidence=floor,
            matches=[
                LabelMatch(coined_id=cid, matched_to=None, confidence=0.0)
                for cid in coined_ids
            ],
            reason=str(exc),
        )

    return ReconciliationResult(
        status="applied", matches=parsed, min_confidence=floor
    )


def apply_matches_to_decomposition(
    decomposition: GoalDecomposition,
    matches: dict[str, str],
    *,
    catalog_ids: list[str] | None = None,
) -> GoalDecomposition:
    """Return a new :class:`GoalDecomposition` with sub-task slugs
    rewritten according to ``matches``.

    ``matches`` is the convenience ``{coined_id: matched_existing_id}``
    dict from :attr:`ReconciliationResult.matched_pairs`. Sub-tasks
    whose ``suggested_capability_id`` doesn't appear in ``matches``
    are left untouched.

    The aggregate ``catalog_reused_capabilities`` and
    ``new_capabilities`` partitions are recomputed against the
    (optional) ``catalog_ids`` to reflect the post-reconciliation
    state — the caller typically passes the *same* catalog union it
    used for reconciliation so the partitions are accurate.

    The decomposer's ``GoalDecomposition`` is frozen, so this
    function constructs and returns a fresh instance rather than
    mutating in place. Downstream consumers (scouts, judge,
    recorders) see only the rewritten output and don't need to know
    that reconciliation happened.
    """

    if not matches:
        return decomposition

    rewritten_sub_tasks = [
        _rewrite_sub_task(sub_task, matches) for sub_task in decomposition.sub_tasks
    ]

    catalog_set = set(catalog_ids or []) | set(matches.values())
    new_capabilities: list[str] = []
    catalog_reused_capabilities: list[str] = []
    seen: set[str] = set()
    for sub_task in rewritten_sub_tasks:
        cid = sub_task.suggested_capability_id
        if cid in seen:
            continue
        seen.add(cid)
        if cid in catalog_set:
            catalog_reused_capabilities.append(cid)
        else:
            new_capabilities.append(cid)

    return GoalDecomposition(
        intent_summary=decomposition.intent_summary,
        sub_tasks=rewritten_sub_tasks,
        confidence=decomposition.confidence,
        catalog_reused_capabilities=catalog_reused_capabilities,
        new_capabilities=new_capabilities,
        raw_response=decomposition.raw_response,
    )


def reconciler_enabled() -> bool:
    """Honour ``PLANMYAGENTS_LABEL_RECONCILER`` (default ``on``).

    Setting it to ``off`` / ``0`` / ``false`` skips reconciliation
    entirely and decomposed sub-tasks flow through unchanged. Provided
    for tests that want a deterministic "no reconciliation" pipeline
    without having to stub out the LLM client.
    """

    return os.getenv("PLANMYAGENTS_LABEL_RECONCILER", "on").lower() not in {
        "0",
        "false",
        "off",
        "none",
        "disabled",
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_min_confidence(override: float | None) -> float:
    if override is not None:
        return _clamp_confidence(override)
    raw = os.getenv("PLANMYAGENTS_LABEL_RECONCILER_MIN_CONFIDENCE")
    if raw is None:
        return DEFAULT_MIN_CONFIDENCE
    try:
        return _clamp_confidence(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_MIN_CONFIDENCE


def _clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, value))


def _unique_coined_ids(decomposition: GoalDecomposition) -> list[str]:
    """Return the deduplicated list of newly-coined capability ids in
    declaration order. Reuses the partition the decomposer already
    computed so we never re-classify an id the decomposer matched
    against the original catalog hint."""

    seen: set[str] = set()
    ordered: list[str] = []
    for cid in decomposition.new_capabilities:
        cleaned = (cid or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _coined_descriptions(decomposition: GoalDecomposition) -> dict[str, str]:
    """Map each coined id to the *first* sub-task's description that
    used it. Two sub-tasks legitimately sharing a coined id is rare
    (the decomposer usually deduplicates), but if it happens the
    first sub-task's description is the canonical one — same way the
    label store records the first-coining context."""

    out: dict[str, str] = {}
    coined = set(decomposition.new_capabilities)
    for sub_task in decomposition.sub_tasks:
        cid = sub_task.suggested_capability_id
        if cid in coined and cid not in out:
            out[cid] = sub_task.description
    return out


def _prepare_catalog_for_prompt(catalog_ids: list[str]) -> list[str]:
    """Normalise, dedupe, sort, and cap the catalog ids passed to the
    LLM. Sorting ensures the prompt is deterministic across requests
    so the LLM's serialised reasoning trace is comparable when
    debugging. The cap keeps the prompt small for hosted models with
    tight rate limits."""

    cleaned = sorted(
        {(cid or "").strip().lower() for cid in catalog_ids if (cid or "").strip()}
    )
    if len(cleaned) > MAX_CATALOG_IDS_IN_PROMPT:
        cleaned = cleaned[:MAX_CATALOG_IDS_IN_PROMPT]
    return cleaned


_SYSTEM_PROMPT = """
You are PlanMyAgents's label reconciler.

A separate decomposer has just broken a user's goal into sub-tasks
and may have coined a brand-new snake_case capability id for some of
those sub-tasks. The system already has a catalog of capability ids
from previous requests. Your job is to decide, for each coined id,
whether it is semantically equivalent to one of the existing
catalog ids — in which case the system should reuse the existing id
for better aggregation — or whether it is genuinely new and should
be persisted as-is.

Rules:

1. For each coined id you are given, return exactly one verdict.
2. ``matched_to`` MUST be either one of the catalog ids verbatim, or
   null. Do not invent ids that are not in the catalog. Do not
   normalise ids in any other way.
3. Only return a non-null match when the two labels would describe
   the *same kind of capability* in the *same operational sense*.
   "send_email" and "verify_email_deliverability" are NOT a match
   even though both involve email. "send_email" and "email_dispatch"
   ARE a match.
4. ``confidence`` is your own assessment 0.0-1.0. Use 0.9+ only when
   the equivalence is unambiguous. Use 0.7-0.9 when there is a
   plausible argument for distinguishing them. Use < 0.7 when you
   are uncertain — uncertainty becomes "no match" downstream.
5. ``reason`` is one short sentence justifying the verdict. Plain
   English. No JSON-inside-JSON, no quoted string prefixes.

Output strict JSON. No markdown. No prose. No commentary.

Schema:
{
  "matches": [
    {
      "coined_id": "<one of the input coined ids>",
      "matched_to": "<an existing catalog id> or null",
      "confidence": 0.0-1.0,
      "reason": "<short sentence>"
    }
  ]
}
""".strip()


def _build_messages(
    coined_ids: list[str],
    coined_descriptions: dict[str, str],
    catalog_ids: list[str],
) -> list[dict[str, str]]:
    """Build the chat-message pair sent to the LLM.

    The user payload is a JSON object so the model has a clean
    structured input to reason about. Each coined id ships with its
    decomposer-written description so the model can judge semantic
    equivalence on *meaning*, not just slug-overlap.
    """

    payload: dict[str, Any] = {
        "coined_ids": [
            {
                "id": cid,
                "description": coined_descriptions.get(cid, ""),
            }
            for cid in coined_ids
        ],
        "existing_catalog_ids": catalog_ids,
    }
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, indent=2, sort_keys=True)},
    ]


def _parse_response(
    raw: str,
    *,
    coined_ids: list[str],
    catalog_set: set[str],
    min_confidence: float,
) -> list[LabelMatch]:
    """Parse the LLM response into a stable list of matches.

    Tolerant of common formatting drift (markdown fencing, prose
    prefix/suffix). Hard-validates:

    * Every coined id is covered. Missing entries get a synthetic
      "no match, confidence 0.0" verdict so downstream code can
      treat the result as exhaustive.
    * Each ``matched_to`` is either ``None`` or an id present in
      ``catalog_set``. Hallucinated ids are silently coerced to
      ``None`` (the safer outcome — we'd rather under-match than
      attach to an id that doesn't exist).
    * ``confidence`` below ``min_confidence`` is also coerced to
      ``matched_to=None``. This is the floor that prevents low-
      confidence matches from silently merging two distinct
      capabilities.
    """

    payload = _parse_json_object(raw)
    if not payload:
        raise LabelReconciliationError(
            "Reconciler did not return a parseable JSON object."
        )
    raw_matches = payload.get("matches")
    if not isinstance(raw_matches, list):
        raise LabelReconciliationError(
            "Reconciler payload missing or non-list 'matches'."
        )

    by_id: dict[str, LabelMatch] = {}
    for entry in raw_matches:
        if not isinstance(entry, dict):
            continue
        coined_id = (entry.get("coined_id") or "").strip()
        if not coined_id or coined_id not in coined_ids:
            # Ignore entries that don't refer to a coined id we
            # actually asked about. The model can hallucinate extra
            # rows; we throw them away rather than letting them
            # corrupt the result.
            continue
        confidence = _coerce_confidence(entry.get("confidence"))
        matched_raw = entry.get("matched_to")
        matched_to: str | None = None
        if isinstance(matched_raw, str):
            candidate = matched_raw.strip().lower()
            if candidate and candidate in catalog_set and _SNAKE_CASE.match(candidate):
                matched_to = candidate
        if matched_to is not None and confidence < min_confidence:
            matched_to = None
        reason = (entry.get("reason") or "").strip()
        # Last write wins if the model duplicates a coined_id row
        # — we have no principled way to merge two competing
        # verdicts and the model rarely does this.
        by_id[coined_id] = LabelMatch(
            coined_id=coined_id,
            matched_to=matched_to,
            confidence=confidence,
            reason=reason,
        )

    # Backfill any coined id the model forgot to address.
    for cid in coined_ids:
        by_id.setdefault(
            cid,
            LabelMatch(
                coined_id=cid,
                matched_to=None,
                confidence=0.0,
                reason="not addressed by reconciler",
            ),
        )

    return [by_id[cid] for cid in coined_ids]


def _coerce_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return _clamp_confidence(parsed)


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Same tolerant extraction as the decomposer — strip optional
    markdown fences, locate the first balanced ``{ ... }`` block.

    Kept as a private helper rather than imported from
    ``goal_decomposer`` because both modules want to evolve their
    parsers independently (e.g. the reconciler may eventually accept
    a JSON array at top level for compactness; the decomposer's
    schema is fixed). Sharing the helper would create false
    coupling.
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


def _rewrite_sub_task(
    sub_task: DecomposedSubTask, matches: dict[str, str]
) -> DecomposedSubTask:
    """Rebuild a sub-task with its slug rewritten when the
    reconciler matched it to an existing catalog id; otherwise
    return the sub-task unchanged."""

    new_id = matches.get(sub_task.suggested_capability_id)
    if not new_id or new_id == sub_task.suggested_capability_id:
        return sub_task
    return DecomposedSubTask(
        description=sub_task.description,
        user_facing_step=sub_task.user_facing_step,
        search_query=sub_task.search_query,
        acceptance_criteria=sub_task.acceptance_criteria,
        suggested_capability_id=new_id,
    )
