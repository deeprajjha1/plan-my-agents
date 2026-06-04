"""Pre-plan discovery: dispatch scouts before the planner sees a final catalog.

Why this module exists (Gap 3 in the honest-scope audit)
--------------------------------------------------------
The historical ``/goal`` flow runs the constrained planner against the
*cached* discovery store, refuses if no executable plan is possible,
THEN dispatches scouts in the refusal handler. That ordering means the
planner is forever one cycle behind the live ecosystem: a request whose
true match was published to Smithery five minutes ago can't be planned
against until *after* the refusal kicks off the scouts.

The pre-plan stage runs the goal decomposer first, dispatches scouts
against each sub-task's LLM-written ``search_query`` in parallel,
persists what they find via ``save_merge`` (which re-embeds inline
with Gap 1's tool-aware text), and lets the planner re-evaluate against
a freshly-warmed catalog. The actual re-planning happens in the
``plan_goal_with_pre_plan_discovery`` wrapper in
:mod:`planmyagents_api.web.planning`; this module owns just the scout
dispatch + persistence half so it can be tested in isolation and reused
by other entry points (e.g. CLI evaluation, the demo script).

Design constraints
------------------
* **Best-effort.** This stage MUST NOT raise into the planner. A scout
  failure, a persistence failure, or a missing decomposer output all
  degrade to "stage produced no new candidates" and let the existing
  refusal-path discovery do its thing.
* **Bounded scope.** We dispatch at most ``_MAX_PRE_PLAN_CAPABILITIES``
  capabilities per request (default 3) so the worst-case latency is
  capped at one ``ScoutDispatcher`` global budget regardless of how
  many sub-tasks the decomposer emits.
* **Reuses the existing fleet.** Same ``default_scouts()``,
  ``ScoutDispatcher``, ``expand_for_scouts``, and ``save_merge`` the
  refusal path uses — there is exactly one definition of "what counts
  as a scout dispatch" in the codebase, and Gap 3 doesn't fork it.
* **Self-describing return value.** The summary dict carries enough
  state for the wrapper to decide whether to re-plan AND for the
  ``/goal`` route to know it can skip a redundant
  ``_live_discovery`` call (the dispatch ran once already).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from planmyagents_api.discovery.models import DiscoveryCandidate

LOGGER = logging.getLogger(__name__)

# Hard cap on capabilities probed in the pre-plan stage. Each capability
# costs one ``ScoutDispatcher.dispatch`` (~28s global budget worst case),
# so 3 keeps the whole pre-plan stage under the 90s ceiling we promise
# the user-visible /goal latency. Operators tuning for higher recall can
# raise it via ``PLANMYAGENTS_PRE_PLAN_MAX_CAPABILITIES``.
_MAX_PRE_PLAN_CAPABILITIES_DEFAULT = 3


def pre_plan_discovery_enabled() -> bool:
    """``PLANMYAGENTS_PRE_PLAN_DISCOVERY=true`` opts the wrapper in.

    Default OFF in this codebase: until the GenericProtocolAdapter
    execution path lands (Gap 4), pre-plan discovery primarily warms
    the cache for the next request rather than converting refusals
    into executable plans on the current request. Operators who care
    about cache warmth and richer gap reports more than they care
    about the +5–35s latency on first refusal turn it on; tests opt
    out via the package-level conftest in ``tests/__init__.py``.

    Once the GenericProtocolAdapter executor lands and discovered MCP /
    A2A / OpenAPI providers can satisfy a plan immediately, this
    default flips to ON.
    """

    import os

    raw = (os.getenv("PLANMYAGENTS_PRE_PLAN_DISCOVERY") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def max_pre_plan_capabilities() -> int:
    """Resolve the per-request capability cap.

    Falls back to :data:`_MAX_PRE_PLAN_CAPABILITIES_DEFAULT` on any
    parse error, and treats <=0 as "use default" so a typo'd env var
    can't accidentally disable the stage."""

    import os

    raw = os.getenv("PLANMYAGENTS_PRE_PLAN_MAX_CAPABILITIES")
    if not raw or not raw.strip():
        return _MAX_PRE_PLAN_CAPABILITIES_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return _MAX_PRE_PLAN_CAPABILITIES_DEFAULT
    return value if value > 0 else _MAX_PRE_PLAN_CAPABILITIES_DEFAULT


def run_pre_plan_discovery(
    *,
    goal: str,
    decomposed_sub_tasks: list[dict[str, Any]],
    store_url: str,
    max_capabilities: int | None = None,
) -> dict[str, Any]:
    """Dispatch scouts for the decomposer's sub-tasks and persist results.

    Args:
        goal: The user goal text. Used as a fallback ``task_description``
            for any sub-task that didn't include its own ``search_query``.
        decomposed_sub_tasks: The list-of-dicts shape emitted by
            :func:`planmyagents_api.planner.goal_decomposer.decompose_goal`
            and threaded through ``_apply_decomposer``. Each entry MUST
            carry at minimum a ``suggested_capability_id``; ``search_query``
            and ``acceptance_criteria`` are used opportunistically.
        store_url: URL of the discovery store to ``save_merge`` into.
        max_capabilities: Optional override for the per-request cap.
            Defaults to :func:`max_pre_plan_capabilities`.

    Returns:
        A status dict consumable by the wrapper:

        * ``status`` — one of ``"ran"``, ``"skipped_no_sub_tasks"``,
          ``"skipped_no_capabilities"``, ``"persistence_failed"``.
        * ``capabilities_dispatched`` — list of capability ids touched.
        * ``persisted_count`` — number of new candidates the
          ``save_merge`` accepted (may be 0 when scouts ran but found
          nothing or everything was already indexed).
        * ``new_candidate_capabilities`` — set of capability ids that
          gained at least one fresh candidate (decision input for the
          plan-retry loop).
        * ``elapsed_ms`` — wall-clock for the whole stage.

        On any internal failure the status downgrades to
        ``"persistence_failed"`` (or ``"error"`` for unexpected
        exceptions) and the function still returns — the wrapper treats
        this as "no enrichment happened" and moves on.
    """

    started = time.monotonic()
    if not decomposed_sub_tasks:
        return {
            "status": "skipped_no_sub_tasks",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    capabilities, search_queries = _select_capabilities_and_queries(
        decomposed_sub_tasks=decomposed_sub_tasks,
        cap=(max_capabilities if max_capabilities is not None else max_pre_plan_capabilities()),
    )
    if not capabilities:
        return {
            "status": "skipped_no_capabilities",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    # Lazy imports keep this module cheap to import for tests that just
    # want to call ``pre_plan_discovery_enabled`` and never touch the
    # scout dispatch.
    from planmyagents_api.discovery.query_expansion import expand_for_scouts
    from planmyagents_api.discovery.scouts import ScoutDispatcher, default_scouts
    from planmyagents_api.discovery.store import discovery_store_for_path

    dispatcher = ScoutDispatcher(default_scouts())
    chat_client = _maybe_chat_client_for_query_expansion()

    new_candidates: list[DiscoveryCandidate] = []
    seen_ids: set[str] = set()
    new_candidates_by_capability: dict[str, list[str]] = {}
    per_capability_summary: list[dict[str, Any]] = []

    for capability in capabilities:
        scout_task_text = search_queries.get(capability) or goal
        try:
            expansion = expand_for_scouts(
                capability=capability,
                task_text=scout_task_text,
                chat_client=chat_client,
            )
            result = dispatcher.dispatch(
                capability=capability,
                task_description=scout_task_text,
                per_scout_query_overrides=expansion.per_scout_queries,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            LOGGER.warning(
                "pre-plan discovery: dispatch failed for %s: %s: %s",
                capability,
                type(exc).__name__,
                exc,
            )
            per_capability_summary.append(
                {
                    "capability": capability,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        per_capability_new_ids: list[str] = []
        for candidate in result.merged_candidates:
            if candidate.id in seen_ids:
                continue
            seen_ids.add(candidate.id)
            new_candidates.append(candidate)
            per_capability_new_ids.append(candidate.id)

        if per_capability_new_ids:
            new_candidates_by_capability[capability] = per_capability_new_ids

        per_capability_summary.append(
            {
                "capability": capability,
                "status": "ok",
                "scout_task_text": scout_task_text,
                "scout_task_text_source": (
                    "goal_decomposer"
                    if capability in search_queries and search_queries[capability]
                    else "raw_goal"
                ),
                "candidates_returned": len(result.merged_candidates),
                "candidates_newly_seen_in_request": len(per_capability_new_ids),
                "dispatch": result.to_summary(),
            }
        )

    persisted_count = 0
    persistence_status = "ok"
    persistence_error: str | None = None
    if new_candidates:
        try:
            discovery_store_for_path(store_url).save_merge(new_candidates)
            persisted_count = len(new_candidates)
        except Exception as exc:  # noqa: BLE001 — best-effort
            persistence_status = "failed"
            persistence_error = f"{type(exc).__name__}: {exc}"
            LOGGER.warning(
                "pre-plan discovery: save_merge failed: %s",
                persistence_error,
            )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    LOGGER.info(
        "pre-plan discovery complete in %dms: caps=%d new_candidates=%d persisted=%d",
        elapsed_ms,
        len(capabilities),
        len(new_candidates),
        persisted_count,
    )

    status = "ran" if persistence_status == "ok" else "persistence_failed"
    return {
        "status": status,
        "capabilities_dispatched": list(capabilities),
        "persisted_count": persisted_count,
        "new_candidate_capabilities": sorted(new_candidates_by_capability.keys()),
        "new_candidates_by_capability": new_candidates_by_capability,
        "per_capability": per_capability_summary,
        "persistence_error": persistence_error,
        "elapsed_ms": elapsed_ms,
    }


def _select_capabilities_and_queries(
    *,
    decomposed_sub_tasks: list[dict[str, Any]],
    cap: int,
) -> tuple[list[str], dict[str, str]]:
    """Pick at most ``cap`` capability ids from the decomposer output,
    preserving the decomposer's ordering (which reflects sub-task
    sequence in the workflow).

    Returns ``(capabilities, search_queries_by_capability)`` where the
    capability list is deduplicated AND order-preserving — a sub-task
    that re-uses an earlier slug doesn't consume a slot.

    Capability ids are sourced from ``suggested_capability_id`` (the
    field the decomposer commits to per sub-task). Sub-tasks missing
    that field are skipped silently — they were noise, not a planner
    decision.
    """

    capabilities: list[str] = []
    seen: set[str] = set()
    search_queries: dict[str, str] = {}
    for sub_task in decomposed_sub_tasks:
        if not isinstance(sub_task, dict):
            continue
        capability = str(sub_task.get("suggested_capability_id") or "").strip()
        if not capability or capability in seen:
            continue
        capabilities.append(capability)
        seen.add(capability)
        query = str(sub_task.get("search_query") or "").strip()
        if query:
            search_queries[capability] = query
        if len(capabilities) >= cap:
            break
    return capabilities, search_queries


def _maybe_chat_client_for_query_expansion():
    """Best-effort chat client for the LLM query expander.

    Mirrors the behaviour of ``app._maybe_chat_client_for_query_expansion``
    — returns the escalating client when configured, ``None`` when no
    tier is available. The expander degrades to a deterministic rule-
    based fallback when the client is ``None``, so callers don't need to
    handle the no-client case explicitly.
    """

    import os

    if os.getenv("PLANMYAGENTS_QUERY_EXPANDER_LLM", "true").lower() in {
        "0",
        "false",
        "no",
    }:
        return None
    try:
        from planmyagents_api.llm.escalating_client import (
            build_default_escalating_client,
        )

        return build_default_escalating_client()
    except Exception:  # noqa: BLE001 — never block discovery on classifier setup
        return None
