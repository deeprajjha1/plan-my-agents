"""Shared planning helper used by both the FastAPI app and the local demo.

Wraps the planner with a clean, side-effect-free interface so callers do
not need to know which LLM tier produced the plan.

Architecture (post-decomposer refactor)
---------------------------------------
A ``/goal`` request flows through two LLM stages:

1. **Constrained planner** (``plan_goal_with_local_qwen``):
   Decides whether the goal is *already executable* against the
   provider registry. For example, an "email verification" goal with
   concrete email inputs becomes an executable plan with a populated
   ``sub_tasks`` list and an empty ``missing_capabilities`` list.
   This stage is rigid by design: it only produces executable plans
   for capabilities the registry actually knows how to route.

2. **Goal decomposer** (``decompose_goal``, NEW):
   Runs only when stage 1 returns ``unsupported``. The decomposer
   asks an LLM to write the *workflow a human would actually
   perform* to achieve the goal — one atomic sub-task at a time, in
   the LLM's own words, with no fixed capability vocabulary to pick
   from. The catalog of previously-seen capability ids is passed
   only as an *optional hint* so labels can be reused for cross-
   request aggregation; the LLM is explicitly free to coin new
   labels.

Both stages share the escalating LLM client so a primary-tier failure
(Groq → Qwen, or vice versa) escalates automatically. If every tier
fails the route raises :class:`PlanningUnavailableError` and the
handler is expected to return a 503 with structured remediation,
never a silent rules-fallback.

What the old design used (and why it was removed)
------------------------------------------------
The old design had two more LLM stages between (1) and what is now
(2):

* ``intent_mapper.map_goal_to_capabilities`` — picked capability ids
  from a closed catalog. Constrained the LLM to a vocabulary derived
  from already-indexed agents, which biased the system to ignore
  capabilities the world hasn't added to its index yet.
* ``intent_mapper._audit_mapping_coverage`` — re-checked the mapping
  for "missing constraints", but its prompt explicitly enumerated
  commerce/compliance/cross-boundary patterns and never mentioned
  research/outreach/comparison patterns, biasing every decomposition
  toward transactional vocabulary even for research-and-procurement
  goals.

Both modules and their biases are gone. The single decomposer in (2)
emits richer per-sub-task objects (``description``,
``user_facing_step``, ``search_query``, ``acceptance_criteria``,
``suggested_capability_id``) that the downstream scouts and judge
consume directly — no slug-only intermediate representation.

Modes (unchanged):

* ``PLANMYAGENTS_PLANNER=escalating`` (default) — escalating client.
* ``PLANMYAGENTS_PLANNER=local_qwen`` — local Qwen only.
* ``PLANMYAGENTS_PLANNER=groq`` — hosted Groq only.
* ``PLANMYAGENTS_PLANNER=rules`` — substring rules planner. Debug only;
  emits a loud warning. The decomposer still runs for unsupported
  plans even in rules mode, so the ``missing_capabilities`` list is
  semantically meaningful.

When an LLM-driven mode cannot produce a plan, this function raises
:class:`PlanningUnavailableError`. The FastAPI route handler is
expected to translate that into a 503 with a structured refusal payload
so the UI can render a clear "no LLM tier available" card instead of a
silently degraded result.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any

from planmyagents_api._config import discovery_store_url
from planmyagents_api.agents.router import ProviderRouter
from planmyagents_api.discovery.pre_plan_discovery import (
    pre_plan_discovery_enabled,
    run_pre_plan_discovery,
)
from planmyagents_api.discovery.service import default_discovery_sources
from planmyagents_api.discovery.store import discovery_store_for_path
from planmyagents_api.llm.escalating_client import (
    EscalatingChatClient,
    EscalationMetadata,
    NoLlmTierAvailableError,
    build_default_escalating_client,
)
from planmyagents_api.planner.capability_catalog import (
    CapabilityCatalog,
    build_capability_catalog,
)
from planmyagents_api.planner.capability_label_recorder import (
    load_persisted_label_ids,
    record_coined_labels,
)
from planmyagents_api.planner.goal import GoalPlan, PlannedSubTask
from planmyagents_api.planner.goal_decomposer import (
    GoalDecomposition,
    GoalDecompositionError,
    decompose_goal,
)
from planmyagents_api.planner.groq_client import DEFAULT_GROQ_MODEL, GroqChatClient, GroqChatError
from planmyagents_api.planner.label_reconciler import (
    apply_matches_to_decomposition,
    reconcile_labels,
    reconciler_enabled,
)
from planmyagents_api.planner.local_qwen import (
    DEFAULT_QWEN_MODEL,
    LocalQwenPlannerError,
    OllamaQwenClient,
    plan_goal_with_local_qwen,
)
from planmyagents_api.registry.capability_descriptions import describe_capabilities

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanningUnavailableMetadata:
    """Structured provenance for a planning failure.

    Surfaced verbatim in the API response so the UI can show "primary
    qwen failed because <X>, fallback groq failed because <Y>" rather
    than a generic 500.
    """

    reason: str
    mode: str
    primary_label: str = ""
    primary_attempted: bool = False
    primary_error: str | None = None
    fallback_label: str = ""
    fallback_attempted: bool = False
    fallback_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "mode": self.mode,
            "primary_label": self.primary_label,
            "primary_attempted": self.primary_attempted,
            "primary_error": self.primary_error,
            "fallback_label": self.fallback_label,
            "fallback_attempted": self.fallback_attempted,
            "fallback_error": self.fallback_error,
        }

    @classmethod
    def from_escalation(
        cls,
        metadata: EscalationMetadata,
        *,
        mode: str,
        reason: str,
    ) -> PlanningUnavailableMetadata:
        return cls(
            reason=reason,
            mode=mode,
            primary_label=metadata.primary_label,
            primary_attempted=metadata.primary_attempted,
            primary_error=metadata.primary_error,
            fallback_label=metadata.fallback_label,
            fallback_attempted=metadata.fallback_attempted,
            fallback_error=metadata.fallback_error,
        )


class PlanningUnavailableError(RuntimeError):
    """Raised when no LLM tier can produce a usable plan.

    The route handler should translate this to a 503 with the
    ``metadata`` payload visible to the user — never silently fall back
    to the substring rules planner.
    """

    def __init__(self, metadata: PlanningUnavailableMetadata) -> None:
        self.metadata = metadata
        super().__init__(metadata.reason)


@dataclass
class _PlannerCallResult:
    """Internal: bundles a plan with the escalation metadata that
    produced it. Used to thread llm_quality info up to the route."""

    plan: GoalPlan
    escalation: EscalationMetadata = field(default_factory=EscalationMetadata)


_LLM_DRIVEN_MODES: frozenset[str] = frozenset({"escalating", "local_qwen", "groq"})
# Default = ``escalating`` mode. Tier selection is delegated to
# :func:`build_default_escalating_client` (which respects
# ``PLANMYAGENTS_LLM_TIER_ORDER`` and ``GROQ_API_KEY``):
#
# * No ``GROQ_API_KEY`` set      -> Qwen primary, no fallback (Qwen-only,
#   identical to the old ``local_qwen`` default).
# * ``GROQ_API_KEY`` set         -> Groq primary, Qwen fallback. This
#   collapses a 7-minute Qwen-only response into ~30s end-to-end with
#   Groq's hosted Llama-class model on the hot path.
#
# Either way the two tiers are NEVER both invoked on a single
# successful request — the fallback fires only when the primary failed
# on that specific request.
_DEFAULT_PLANNER_MODE = "escalating"


def plan_goal_smart(
    goal: str,
    *,
    router: ProviderRouter | None = None,
    capability_catalog: CapabilityCatalog | None = None,
) -> tuple[GoalPlan, dict[str, Any]]:
    """Plan a goal using the configured planner mode.

    Returns ``(plan, metadata)``. ``metadata['llm_quality']`` carries
    the escalation provenance so the route can render a status pill.
    ``metadata['decomposed_sub_tasks']`` carries the rich sub-task
    objects emitted by the decomposer (only present for unsupported
    plans where the decomposer ran successfully).

    Raises :class:`PlanningUnavailableError` when an LLM-driven mode is
    selected but no tier could produce a plan. Callers should surface a
    clean refusal rather than catching this and silently degrading.
    """

    router = router or ProviderRouter()
    capability_catalog = capability_catalog or _build_default_capability_catalog(goal)
    base_metadata: dict[str, Any] = {
        "capability_catalog": {
            "mode": "source_derived",
            "capabilities": len(capability_catalog.capability_ids),
        }
    }

    planner_mode = (os.getenv("PLANMYAGENTS_PLANNER") or _DEFAULT_PLANNER_MODE).lower()

    if planner_mode == "rules":
        # Debug-only path. The constrained planner is a substring
        # matcher; downstream the decomposer still runs to produce a
        # meaningful missing_capabilities list. The loud warning is
        # surfaced so the UI can render a red banner.
        from planmyagents_api.planner.goal import plan_goal

        plan = plan_goal(
            goal,
            supported_capabilities=router.known_capabilities(),
            capability_catalog=capability_catalog,
        )
        plan, decomposer_metadata = _apply_decomposer(
            plan=plan,
            goal=goal,
            capability_catalog=capability_catalog,
            router=router,
        )
        return plan, {
            "mode": "rules",
            "llm_quality": {
                "planner": {
                    "tier_used": "none",
                    "mode": "rules",
                    "warning": (
                        "PLANMYAGENTS_PLANNER=rules is a debug-only "
                        "substring planner. Results may be unreliable."
                    ),
                }
            },
            **base_metadata,
            **decomposer_metadata,
        }

    if planner_mode == "groq":
        client = _build_groq_only_client_or_refuse(planner_mode)
        result = _run_planner(goal, router, client)
        plan, decomposer_metadata = _apply_decomposer(
            plan=result.plan,
            goal=goal,
            capability_catalog=capability_catalog,
            router=router,
        )
        return plan, {
            "mode": "groq",
            "model": os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL),
            "llm_quality": {"planner": _llm_quality_from_escalation(result.escalation, mode="groq")},
            **base_metadata,
            **decomposer_metadata,
        }

    if planner_mode == "local_qwen":
        client = OllamaQwenClient()
        result = _run_planner(goal, router, client)
        plan, decomposer_metadata = _apply_decomposer(
            plan=result.plan,
            goal=goal,
            capability_catalog=capability_catalog,
            router=router,
        )
        return plan, {
            "mode": "local_qwen",
            "model": os.getenv("PLANMYAGENTS_QWEN_MODEL", DEFAULT_QWEN_MODEL),
            "llm_quality": {
                "planner": _llm_quality_from_escalation(
                    result.escalation, mode="local_qwen"
                )
            },
            **base_metadata,
            **decomposer_metadata,
        }

    # Default and unknown modes use the escalating client.
    client = build_default_escalating_client()
    result = _run_planner(goal, router, client)
    plan, decomposer_metadata = _apply_decomposer(
        plan=result.plan,
        goal=goal,
        capability_catalog=capability_catalog,
        router=router,
    )
    return plan, {
        "mode": "escalating",
        "llm_quality": {
            "planner": _llm_quality_from_escalation(result.escalation, mode="escalating")
        },
        **base_metadata,
        **decomposer_metadata,
    }


def plan_goal_with_pre_plan_discovery(
    goal: str,
    *,
    router: ProviderRouter | None = None,
    capability_catalog: CapabilityCatalog | None = None,
) -> tuple[GoalPlan, dict[str, Any]]:
    """Drop-in wrapper around :func:`plan_goal_smart` that runs scouts
    against the goal-decomposer's per-sub-task ``search_query`` BEFORE
    the route handler does its own refusal-path discovery.

    This is the Gap-3 re-sequencing from the honest-scope audit: instead
    of letting the planner refuse and the route handler scramble after
    the fact, we run the decomposer-driven scouts in the same call as
    the planner so the discovery store is warm before the user sees
    either the executable plan or the refusal payload.

    Hot-path contract
    -----------------
    * Plans the planner declares ``executable`` are returned UNCHANGED.
      Pre-plan discovery only adds latency to *unsupported* plans, where
      the user is going to wait for refusal-path discovery anyway. The
      executable hot path stays as fast as the underlying planner.
    * Discovery is gated on
      :func:`~planmyagents_api.discovery.pre_plan_discovery.pre_plan_discovery_enabled`.
      Default OFF: until the GenericProtocolAdapter execution path
      (Gap 4) lets the executor pick up newly-discovered providers,
      pre-plan discovery primarily warms the cache for the next request
      rather than converting a refusal into an executable plan now.
      Operators tuning for cache warmth flip the env var on.
    * Discovery is best-effort. A scout fleet outage, a persistence
      failure, or a missing decomposer output all surface as a
      ``"skipped_*"`` / ``"persistence_failed"`` status in the
      returned metadata; the plan + decomposer output flow through
      unchanged. We never raise out of this wrapper for discovery
      reasons — only for the same planner-availability reasons
      :func:`plan_goal_smart` already raises.

    Returned metadata
    -----------------
    Whatever :func:`plan_goal_smart` returns, plus one extra key:

    * ``pre_plan_discovery`` — dict with the same shape as
      :func:`run_pre_plan_discovery` returns. The ``/goal`` route
      reads ``status`` to decide whether to skip its own redundant
      ``_live_discovery`` call (the dispatch already ran), and
      surfaces the ``per_capability`` summary in the response payload
      so the user can see what the system tried.
    """

    plan, metadata = plan_goal_smart(
        goal,
        router=router,
        capability_catalog=capability_catalog,
    )

    pre_plan_summary: dict[str, Any]
    if not _pre_plan_discovery_enabled_runtime():
        pre_plan_summary = {"status": "disabled"}
    elif plan.executable:
        pre_plan_summary = {"status": "skipped_executable_plan"}
    else:
        decomposed = _coerce_decomposed_sub_tasks(metadata)
        if not decomposed:
            pre_plan_summary = {"status": "skipped_no_decomposed_sub_tasks"}
        else:
            try:
                pre_plan_summary = run_pre_plan_discovery(
                    goal=goal,
                    decomposed_sub_tasks=decomposed,
                    store_url=_pre_plan_discovery_store_url(),
                )
            except Exception as exc:  # noqa: BLE001 — best-effort
                LOGGER.warning(
                    "pre-plan discovery wrapper: unexpected failure: %s: %s",
                    type(exc).__name__,
                    exc,
                )
                pre_plan_summary = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }

    metadata = {**metadata, "pre_plan_discovery": pre_plan_summary}
    return plan, metadata


def _pre_plan_discovery_enabled_runtime() -> bool:
    """Indirection over the module-level enabled check so tests can
    monkeypatch the boolean without touching environment state."""

    return pre_plan_discovery_enabled()


def _pre_plan_discovery_store_url() -> str:
    """Resolve the discovery store URL the wrapper writes scout results to.

    Delegates to the centralised :func:`planmyagents_api._config.discovery_store_url`
    so this site, ``web/app.py``, and every future consumer always agree
    on which store to talk to (Postgres in prod, JSON/SQLite in dev/CI
    when the env var is explicitly set).
    """

    return discovery_store_url()


def _coerce_decomposed_sub_tasks(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Pluck the decomposed sub-tasks out of planner metadata.

    The decomposer attaches them as ``metadata['decomposed_sub_tasks']``
    when it ran successfully. Any non-list value (missing key, ``None``,
    ``[]``, malformed shape) is treated as "no usable sub-tasks" and
    suppresses pre-plan discovery for this request — consistent with the
    "best-effort, never raise" contract.
    """

    raw = metadata.get("decomposed_sub_tasks")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _run_planner(
    goal: str, router: ProviderRouter, client: Any
) -> _PlannerCallResult:
    """Run the LLM planner. Translates tier failures into
    :class:`PlanningUnavailableError` so the route can refuse cleanly
    instead of silently falling back to substring rules."""

    mode = "escalating" if isinstance(client, EscalatingChatClient) else (
        "groq" if isinstance(client, GroqChatClient) else "local_qwen"
    )
    try:
        plan = plan_goal_with_local_qwen(goal, router.registry, client=client)
    except (LocalQwenPlannerError, GroqChatError, NoLlmTierAvailableError) as exc:
        metadata = (
            client.last_metadata
            if isinstance(client, EscalatingChatClient)
            else _ad_hoc_metadata_for(client, mode=mode, error=str(exc))
        )
        raise PlanningUnavailableError(
            PlanningUnavailableMetadata.from_escalation(
                metadata,
                mode=mode,
                reason=f"Planner LLM unavailable: {exc}",
            )
        ) from exc
    escalation = (
        client.last_metadata
        if isinstance(client, EscalatingChatClient)
        else _ad_hoc_metadata_for(client, mode=mode, error=None)
    )
    return _PlannerCallResult(plan=plan, escalation=escalation)


def _ad_hoc_metadata_for(
    client: Any, *, mode: str, error: str | None
) -> EscalationMetadata:
    """Synthesize EscalationMetadata for the legacy single-tier modes
    (``local_qwen`` / ``groq``) so the surfaced shape is uniform."""

    label = getattr(client, "model", "") or type(client).__name__
    if mode == "groq":
        return EscalationMetadata(
            tier_used="fallback" if error is None else "none",
            primary_label="",
            fallback_label=label,
            primary_attempted=False,
            fallback_attempted=True,
            fallback_error=error,
        )
    return EscalationMetadata(
        tier_used="primary" if error is None else "none",
        primary_label=label,
        fallback_label="",
        primary_attempted=True,
        primary_error=error,
        fallback_attempted=False,
    )


def _llm_quality_from_escalation(
    metadata: EscalationMetadata, *, mode: str
) -> dict[str, Any]:
    payload = metadata.to_json()
    payload["mode"] = mode
    return payload


def _build_groq_only_client_or_refuse(mode: str) -> GroqChatClient:
    if not os.getenv("GROQ_API_KEY"):
        raise PlanningUnavailableError(
            PlanningUnavailableMetadata(
                reason=(
                    "PLANMYAGENTS_PLANNER=groq selected but GROQ_API_KEY "
                    "is not set."
                ),
                mode=mode,
                fallback_label="groq",
                fallback_attempted=False,
                fallback_error="missing_api_key",
            )
        )
    try:
        return GroqChatClient()
    except (GroqChatError, ValueError, TypeError) as exc:
        raise PlanningUnavailableError(
            PlanningUnavailableMetadata(
                reason=f"Groq client could not be constructed: {exc}",
                mode=mode,
                fallback_label="groq",
                fallback_attempted=False,
                fallback_error=str(exc),
            )
        ) from exc


def _apply_decomposer(
    *,
    plan: GoalPlan,
    goal: str,
    capability_catalog: CapabilityCatalog,
    router: ProviderRouter,
) -> tuple[GoalPlan, dict[str, Any]]:
    """Run the LLM goal decomposer when the constrained plan is not executable.

    The decomposer is the SINGLE source of truth for the missing-
    capability set on unsupported plans. It also produces a list of
    rich sub-task objects (description, user_facing_step,
    search_query, acceptance_criteria, suggested_capability_id) that
    the route handler threads into the response payload so scouts,
    the judge, and the UI can all consume the same shape.

    Behaviour:

    * Executable plans (already-routable, e.g. an ``email_verification``
      goal with concrete inputs) are passed through unchanged. The
      decomposer's job is to surface what's missing for *unsupported*
      goals; an executable plan has nothing missing.
    * Unsupported plans get their ``missing_capabilities`` list
      replaced by the decomposer's ``suggested_capability_ids``. Any
      capability the decomposer suggests that the registry already
      routes is filtered out (the registry can satisfy it; we don't
      need to flag it as missing).
    * Slice 2 only: after decomposition, a small batched LLM call
      (the *label reconciler*) checks every freshly-coined
      ``suggested_capability_id`` against the catalog UNION
      (discovery store + persisted labels). High-confidence matches
      rewrite the sub-task slug to the existing id (better cross-
      request aggregation for demand and gap leaderboards).
      Genuinely-new ids are persisted to the capability label store
      so the next request's catalog hint already contains them.
      Both reconciliation and persistence are best-effort: any
      failure leaves the decomposer output unchanged and emits a
      log line. They never block the request.
    * If the decomposer is disabled via
      ``PLANMYAGENTS_GOAL_DECOMPOSER=off``, the constrained planner's
      output flows through verbatim. This is provided as an escape
      hatch for tests / debugging; production should leave it on.
    * If the decomposer's LLM tier fails, the constrained planner's
      output flows through with a status entry in the metadata so the
      UI can show "decomposer unavailable — showing planner-only
      output". We intentionally do NOT raise here: the constrained
      planner already succeeded for this request, and refusing the
      whole /goal because the decomposer couldn't run would discard
      usable work.
    """

    if not _decomposer_enabled():
        return plan, {
            "decomposer": {"status": "disabled"},
            "decomposed_sub_tasks": [],
        }
    if plan.executable:
        return plan, {
            "decomposer": {"status": "skipped_executable_plan"},
            "decomposed_sub_tasks": [],
        }

    # Surface descriptions for ONLY the router-supported capabilities.
    # The decomposer treats these as a strongly-preferred reuse pool;
    # without them the LLM was coining synonyms like ``gift_suggestion``
    # for goals the router already covers via ``web_search`` /
    # ``price_comparison`` / ``payment_authorization``, producing the
    # "0 agents discovered" symptom on shopping-style goals. See the
    # audit in ``packages/registry/capability_descriptions.json`` notes
    # for the failure mode this closes.
    supported_for_descriptions = router.known_capabilities()
    described = describe_capabilities(sorted(supported_for_descriptions))
    try:
        decomposition = decompose_goal(
            goal,
            catalog_hint=capability_catalog,
            catalog_descriptions=described,
        )
    except GoalDecompositionError as exc:
        LOGGER.warning("decomposer: unavailable (%s)", exc)
        return plan, {
            "decomposer": {
                "status": "unavailable",
                "reason": str(exc),
            },
            "decomposed_sub_tasks": [],
        }

    decomposition, reconciler_metadata = _reconcile_and_persist_labels(
        decomposition=decomposition,
        capability_catalog=capability_catalog,
        goal=goal,
    )

    supported = router.known_capabilities()
    # Embedding-based slug canonicalization (defense in depth).
    #
    # The LLM label reconciler above tries to map freshly-coined slugs
    # (``payment_processing``) onto existing labels (the registry's
    # ``payment_authorization``), but it can miss when:
    #   - the catalog hint passed to the reconciler doesn't include the
    #     registry slug verbatim, or
    #   - the reconciler is disabled / the LLM tier is down, or
    #   - the LLM judges them as semantically distinct even though
    #     downstream scouts will treat them identically.
    # Without normalization, the planner emits ``payment_processing``,
    # the scouts dispatch with ``capability=payment_processing``, and
    # APIs.guru's Adyen entries (correctly inferred as
    # ``payment_authorization``) get filtered out — producing the
    # "0 candidates" symptom we saw in the surprise-birthday-gift
    # audit even though Adyen's PaymentService was right there.
    #
    # This step uses the same embedding similarity the scouts use, so
    # by construction any slug we rewrite here will match candidates
    # that were inferred against the registry side. We only rewrite to
    # an id that's actually ``supported`` (registry has a router
    # entry); coined-only labels are left alone so they still flow
    # through as honest discovery_gap signal.
    decomposition, canonicalization_metadata = _canonicalize_unsupported_slugs(
        decomposition=decomposition,
        supported=supported,
    )

    suggested = decomposition.suggested_capability_ids
    missing = sorted({cap for cap in suggested if cap not in supported})

    # Per-capability provider-availability map for every slug the
    # decomposer suggested. ``router.known_capabilities()`` only tells
    # us "the catalog has this slug" — it does NOT tell us whether
    # any provider can actually fulfil it. Without this distinction
    # the user sees "Refused, 0 candidates" with no explanation for
    # capabilities the catalog technically lists but has no provider
    # for (the gift-goal failure mode where ``web_search`` and
    # ``store_locator`` are router-known yet provider-less).
    #
    # ``provider_status`` partitions every suggested slug into one
    # of three states the user/UI/discovery layer can act on:
    #
    # * ``executable``         — at least one configured + adapter +
    #                            gate-passing provider is ready to run
    # * ``configurable``       — provider(s) exist, adapter(s) exist,
    #                            but env_vars / gates / region rules
    #                            currently block routing (the user
    #                            can fix by configuring a key)
    # * ``no_provider``        — registry has no provider for this
    #                            slug at all (true gap; discovery
    #                            scouts must hunt for one)
    provider_status = _capability_provider_status(suggested, router=router)

    # Capabilities discovery should hunt for: router knows the slug
    # but the registry has zero providers backing it. We add these
    # to ``effective_missing`` so ``_refusal_discovery`` fires the
    # scouts and the gap layer records demand. Capabilities that
    # are merely "configurable" (provider exists, env var missing)
    # are NOT counted as missing — the user can resolve them by
    # adding a key, which is a different remediation than
    # "discovery should hunt for a new provider".
    #
    # Coined slugs (in ``missing`` because the catalog doesn't know
    # them) are reported under their own message line — we exclude
    # them from ``no_provider_caps`` to avoid double-listing the
    # same slug under two different reasons.
    no_provider_caps = sorted(
        cap for cap, status in provider_status.items()
        if status["status"] == "no_provider" and cap in supported
    )
    configurable_caps = sorted(
        cap for cap, status in provider_status.items()
        if status["status"] == "configurable"
    )
    executable_caps = sorted(
        cap for cap, status in provider_status.items()
        if status["status"] == "executable"
    )

    effective_missing = sorted(set(missing) | set(no_provider_caps))

    # Build PlannedSubTask instances from the decomposer output so the
    # UI has cards to render and the discovery layer (which collects
    # capabilities from ``plan.sub_tasks`` in addition to
    # ``plan.missing_capabilities``) fires for every decomposed slug,
    # not just the ones the catalog has never seen. Without this
    # the user sees "Refused" with an empty body for the common
    # gift-goal shape.
    planned_sub_tasks = _planned_sub_tasks_from_decomposition(decomposition)

    metadata = {
        "decomposer": {
            "status": "applied",
            "intent_summary": decomposition.intent_summary,
            "confidence": decomposition.confidence,
            "catalog_reused_capabilities": decomposition.catalog_reused_capabilities,
            "new_capabilities": decomposition.new_capabilities,
            "supported_capabilities_filtered_out": sorted(
                set(suggested) & supported
            ),
            "provider_status": provider_status,
            "executable_capabilities": executable_caps,
            "configurable_capabilities": configurable_caps,
            "no_provider_capabilities": no_provider_caps,
        },
        "decomposed_sub_tasks": [sub.to_json() for sub in decomposition.sub_tasks],
        "label_reconciler": reconciler_metadata,
        "slug_canonicalization": canonicalization_metadata,
    }

    # Honest, per-capability refusal reasoning. We name each blocker
    # by capability so an operator reading the payload knows
    # exactly what to fix:
    #
    # * "no provider"    → discovery scouts will hunt; the user
    #                      sees the goal recorded as a gap.
    # * "configurable"   → provider exists, set this env var to
    #                      unblock execution.
    # * "missing slug"   → registry doesn't know this concept; the
    #                      decomposer coined a new slug.
    #
    # Compared to the previous behaviour which collapsed everything
    # into a single generic message, this gives the UI enough
    # signal to render per-sub-task status badges instead of a
    # blanket "Refused" card.
    summary = decomposition.intent_summary or (
        "This goal decomposes into sub-tasks but at least one cannot run yet."
    )
    refusal_reasons = _per_capability_refusal_reasons(
        decomposition=decomposition,
        provider_status=provider_status,
        coined_missing=missing,
    )

    refined = GoalPlan(
        status="unsupported",
        summary=summary,
        sub_tasks=planned_sub_tasks,
        refusal_reasons=refusal_reasons,
        missing_capabilities=effective_missing,
    )
    return refined, metadata


_MAX_PROVIDERS_IN_REASON = 3
_MAX_ENV_VARS_IN_REASON = 3


def _capability_provider_status(
    capabilities: set[str],
    *,
    router: ProviderRouter,
) -> dict[str, dict[str, Any]]:
    """Build a per-capability provider-availability map.

    For each capability, calls ``router.route(cap)`` and translates
    the routing decision into a structured ``status`` the UI / gap
    layer / refusal-reason builder can consume directly.

    The returned dict keys are the capability slugs; values are
    ``{"status": ..., "provider_id": ..., "candidate_ids": [...],
    "required_env_vars": [...], "reason": ...}``. The ``status``
    values are:

    * ``executable``   — ``RouteDecision.provider_id`` is non-None;
                         a configured + adapter-backed + gate-
                         passing provider exists.
    * ``configurable`` — ``RouteDecision.provider_id`` is None but
                         ``candidates`` is non-empty; the registry
                         knows providers but every one is missing
                         configuration / failing a gate.
    * ``no_provider``  — ``candidates`` is empty; the registry has
                         no provider for this slug.

    Failures inside the router are caught and recorded as
    ``{"status": "router_error"}`` so a single bad capability never
    crashes the whole refusal-summary.
    """

    out: dict[str, dict[str, Any]] = {}
    for cap in sorted(capabilities):
        if not cap:
            continue
        try:
            decision = router.route(cap)
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("router.route(%s) failed: %s", cap, exc)
            out[cap] = {
                "status": "router_error",
                "provider_id": None,
                "candidate_ids": [],
                "required_env_vars": [],
                "reason": f"router error: {exc}",
            }
            continue

        candidates = list(decision.candidates or [])
        candidate_ids = sorted(
            {str(c.get("id")) for c in candidates if isinstance(c, dict) and c.get("id")}
        )
        required_env_vars: list[str] = []
        for c in candidates:
            if not isinstance(c, dict):
                continue
            auth = c.get("auth")
            if isinstance(auth, dict):
                env_var = auth.get("env_var")
                if isinstance(env_var, str) and env_var.strip():
                    required_env_vars.append(env_var.strip())
        required_env_vars = sorted(set(required_env_vars))

        if decision.provider_id:
            status = "executable"
        elif candidate_ids:
            status = "configurable"
        else:
            status = "no_provider"

        out[cap] = {
            "status": status,
            "provider_id": decision.provider_id,
            "candidate_ids": candidate_ids,
            "required_env_vars": required_env_vars,
            "reason": decision.reason,
        }
    return out


def _planned_sub_tasks_from_decomposition(
    decomposition: GoalDecomposition,
) -> list[PlannedSubTask]:
    """Convert decomposer output into ``PlannedSubTask`` instances.

    Surfaces the LLM-written description, search query and
    acceptance criteria via ``inputs`` so downstream consumers
    (discovery scouts, retrieval judge, UI cards) can read them
    off ``plan.sub_tasks`` instead of having to walk the
    decomposer metadata. Sub-tasks without a capability id are
    skipped — ``PlannedSubTask`` requires a non-empty capability.
    """

    out: list[PlannedSubTask] = []
    for sub in decomposition.sub_tasks:
        cap = (getattr(sub, "suggested_capability_id", "") or "").strip()
        description = (getattr(sub, "description", "") or "").strip()
        if not cap or not description:
            continue
        inputs: dict[str, Any] = {}
        for attr in ("user_facing_step", "search_query", "acceptance_criteria"):
            value = getattr(sub, attr, None)
            if isinstance(value, str) and value.strip():
                inputs[attr] = value.strip()
        try:
            out.append(
                PlannedSubTask(
                    capability=cap,
                    description=description,
                    inputs=inputs,
                )
            )
        except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
            LOGGER.warning(
                "planning: skipped malformed decomposed sub-task (cap=%s): %s",
                cap,
                exc,
            )
    return out


def _per_capability_refusal_reasons(
    *,
    decomposition: GoalDecomposition,
    provider_status: dict[str, dict[str, Any]],
    coined_missing: list[str],
) -> list[str]:
    """Build refusal reasons that name each blocker by capability.

    Output order is stable: a one-line summary first, then one
    line per category (executable / configurable / no provider /
    coined). Each line names the affected capability slugs so a
    UI that renders ``refusal_reasons`` verbatim still gives the
    user enough signal to act.
    """

    n_subtasks = len(decomposition.sub_tasks)
    reasons: list[str] = [
        f"Goal decomposed into {n_subtasks} sub-task(s)."
    ]

    executable = [c for c, s in provider_status.items() if s["status"] == "executable"]
    configurable = [
        (c, s) for c, s in provider_status.items() if s["status"] == "configurable"
    ]
    no_provider = [
        c for c, s in provider_status.items() if s["status"] == "no_provider"
    ]

    if executable:
        reasons.append(
            "Executable today: " + ", ".join(sorted(executable)) + "."
        )

    if configurable:
        for cap, status in sorted(configurable, key=lambda item: item[0]):
            env_vars = status.get("required_env_vars") or []
            providers = status.get("candidate_ids") or []
            env_hint = (
                "set " + " or ".join(env_vars[:_MAX_ENV_VARS_IN_REASON])
                if env_vars
                else "configure provider credentials"
            )
            provider_hint = (
                " (provider(s): " + ", ".join(providers[:_MAX_PROVIDERS_IN_REASON]) + ")"
                if providers
                else ""
            )
            reasons.append(
                f"Configurable: `{cap}` — {env_hint}{provider_hint}."
            )

    if no_provider:
        reasons.append(
            "No provider in registry for: "
            + ", ".join(sorted(no_provider))
            + ". Discovery scouts will hunt for matching MCP / A2A / AI agents."
        )

    if coined_missing:
        reasons.append(
            "New capability slugs (not in registry): "
            + ", ".join(sorted(coined_missing))
            + ". Discovery scouts will run with these as search hints."
        )

    return reasons


def _slug_canonicalization_enabled() -> bool:
    """Slug canonicalization is opt-in via env var.

    Production should leave it ON (the env file ships with
    ``PLANMYAGENTS_SLUG_CANONICALIZATION=true``); tests default
    OFF because pre-canonicalization tests assume planner-emitted
    slugs flow through verbatim. Tests that specifically want to
    verify canonicalization behaviour set the env var explicitly.
    """

    raw = (os.environ.get("PLANMYAGENTS_SLUG_CANONICALIZATION") or "true").strip().lower()
    return raw not in {"false", "0", "off", "no"}


def _canonicalize_unsupported_slugs(
    *,
    decomposition: GoalDecomposition,
    supported: set[str],
) -> tuple[GoalDecomposition, dict[str, Any]]:
    """Rewrite planner-emitted slugs to registry-supported equivalents
    using the discovery embedding index.

    Returns ``(rewritten_decomposition, metadata)``. Metadata always
    contains a ``status`` and a ``rewrites`` list of
    ``{from, to, similarity}`` entries (possibly empty). Any failure
    inside this helper falls back to ``status="error"`` and returns
    the decomposition unchanged — slug canonicalization is best-effort
    and must never block the request.

    Why this helper exists separately from the LLM reconciler:

    * The reconciler is goal-shaped LLM judgment; this is a deterministic
      embedding lookup. They complement each other (the embedder is
      the safety net for cases the LLM misses, and vice versa).
    * The embedder is the same one downstream scouts use to infer
      capabilities from candidate text, so normalising via match_slug
      guarantees the two halves of the pipeline use the same vocabulary.
    """

    if not _slug_canonicalization_enabled():
        return decomposition, {"status": "disabled"}

    # Lazy import: planning.py is hot-loaded by the FastAPI module and
    # we don't want a discovery-package import cycle on startup.
    try:
        from planmyagents_api.discovery.capability_index import (
            get_default_capability_index,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        return decomposition, {
            "status": "error",
            "reason": f"capability_index_import_failed: {exc}",
            "rewrites": [],
        }

    try:
        index = get_default_capability_index()
    except Exception as exc:  # noqa: BLE001 — defensive
        return decomposition, {
            "status": "error",
            "reason": f"capability_index_init_failed: {exc}",
            "rewrites": [],
        }

    rewrites: list[dict[str, Any]] = []
    rewrite_map: dict[str, str] = {}
    for sub_task in decomposition.sub_tasks:
        slug = sub_task.suggested_capability_id
        if not slug or slug in supported or slug in rewrite_map:
            continue
        try:
            matched = index.match_slug(slug)
        except Exception as exc:  # noqa: BLE001 — defensive
            LOGGER.warning(
                "slug_canonicalization: match_slug(%r) raised %s; skipping",
                slug,
                exc,
            )
            continue
        if matched and matched in supported and matched != slug:
            rewrite_map[slug] = matched
            rewrites.append({"from": slug, "to": matched})

    if not rewrite_map:
        return decomposition, {
            "status": "no_rewrites",
            "rewrites": [],
        }

    rewritten_sub_tasks = []
    for sub_task in decomposition.sub_tasks:
        target = rewrite_map.get(sub_task.suggested_capability_id)
        if target is None:
            rewritten_sub_tasks.append(sub_task)
            continue
        rewritten_sub_tasks.append(
            replace(sub_task, suggested_capability_id=target)
        )

    catalog_reused_extra = sorted(set(rewrite_map.values()))
    rewritten = replace(
        decomposition,
        sub_tasks=rewritten_sub_tasks,
        new_capabilities=[
            cap for cap in decomposition.new_capabilities
            if cap not in rewrite_map
        ],
        catalog_reused_capabilities=sorted(
            set(decomposition.catalog_reused_capabilities) | set(catalog_reused_extra)
        ),
    )

    LOGGER.info(
        "slug_canonicalization: rewrote %d planner slug(s) to registry equivalents: %s",
        len(rewrite_map),
        rewrite_map,
    )
    return rewritten, {
        "status": "applied",
        "rewrites": rewrites,
    }


def _reconcile_and_persist_labels(
    *,
    decomposition: GoalDecomposition,
    capability_catalog: CapabilityCatalog,
    goal: str,
) -> tuple[GoalDecomposition, dict[str, Any]]:
    """Run the label reconciler and UPSERT every label this request
    *uses* into the persistent label store.

    Returns ``(possibly_rewritten_decomposition, metadata_block)``.

    The reconciler matches freshly-coined ids against the catalog
    UNION (discovery-store-derived ids + already-persisted labels).
    On match, the sub-task slug is rewritten in place. The
    persistence step then UPSERTs every label this request actually
    used, with three rules:

    1. **New coin (no match)** — insert into the label store with
       its decomposer-written description and usage_count=1.
    2. **Matched to a persisted label** — UPSERT the matched-to id
       (bumps its usage_count). The decomposer's description for
       the matched-to id is preserved as it was on first coining;
       subsequent reuses do not overwrite it.
    3. **Matched to a discovery-store-derived id** — *no* UPSERT.
       Discovery-store ids are not part of the coined-label
       vocabulary; they're tags on real candidates. Adding them to
       the label store would muddy the "labels coined from real
       demand" semantic the leaderboards rely on.

    The decomposer can also reuse a label *directly* from the
    catalog hint (no reconciler call needed for those ids). When
    such a directly-reused id is itself a persisted label (i.e. was
    coined in some past request), we UPSERT it too — same rule as
    case 2: track adoption, preserve original description.

    Disabled via ``PLANMYAGENTS_LABEL_RECONCILER=off`` — useful for
    unit tests that want a deterministic "no reconciliation"
    pipeline. All failures are swallowed and reported in the
    metadata so the user's request never breaks because
    reconciliation hit a transient issue.
    """

    if not reconciler_enabled():
        return decomposition, {"status": "disabled"}

    persisted_ids_before = _safe_persisted_label_ids()
    candidate_catalog_ids = set(capability_catalog.capability_ids)
    catalog_union = sorted(candidate_catalog_ids | persisted_ids_before)

    if not decomposition.new_capabilities:
        # Nothing was newly coined this request — skip the
        # reconciler LLM round-trip but still bump usage_count for
        # any directly-reused id that is itself a persisted label
        # (see case in docstring above).
        upsert_payload = _upsert_payload_for_reused_persisted_labels(
            decomposition=decomposition,
            persisted_ids_before=persisted_ids_before,
        )
        persisted = record_coined_labels(
            coined_labels=upsert_payload,
            coined_from_goal_hash=_safe_goal_hash(goal),
        )
        return decomposition, {
            "status": "skipped_no_new_capabilities",
            "matches": [],
            "labels_persisted": persisted,
        }

    result = reconcile_labels(
        decomposition=decomposition,
        catalog_ids=catalog_union,
    )
    rewritten = apply_matches_to_decomposition(
        decomposition,
        matches=result.matched_pairs,
        catalog_ids=catalog_union,
    )

    upsert_payload = _build_upsert_payload(
        decomposition=decomposition,
        reconciler_matches=result.matches,
        candidate_catalog_ids=candidate_catalog_ids,
        persisted_ids_before=persisted_ids_before,
    )
    persisted = record_coined_labels(
        coined_labels=upsert_payload,
        coined_from_goal_hash=_safe_goal_hash(goal),
    )

    return rewritten, {
        "status": result.status,
        "min_confidence": result.min_confidence,
        "matches": [m.to_json() for m in result.matches],
        "labels_persisted": persisted,
        "reason": result.reason,
    }


def _build_upsert_payload(
    *,
    decomposition: GoalDecomposition,
    reconciler_matches: list[Any],
    candidate_catalog_ids: set[str],
    persisted_ids_before: set[str],
) -> dict[str, str]:
    """Compute ``{label_id: description}`` for everything this
    request should UPSERT into the label store.

    See :func:`_reconcile_and_persist_labels` docstring for the
    three rules; this function applies them deterministically given
    the reconciler's verdicts and the prior catalog/label sets.
    """

    descriptions_by_coined = _descriptions_by_coined_id(decomposition)
    payload: dict[str, str] = {}

    for match in reconciler_matches:
        coined_id = match.coined_id
        description = descriptions_by_coined.get(coined_id, "")
        if match.matched_to is None:
            # Rule 1: new coin → insert with description (or bump
            # usage_count if the same id was coined in some earlier
            # request and has already been persisted).
            payload.setdefault(coined_id, description)
            continue
        matched_to = match.matched_to
        # Rule 3: matched to a discovery-store-derived id that is
        # NOT itself a persisted label → don't add to label store.
        # Those ids belong to indexed candidates and the label
        # store is reserved for the coined-from-demand vocabulary.
        if matched_to in candidate_catalog_ids and matched_to not in persisted_ids_before:
            continue
        # Rule 2: matched to an already-persisted label → UPSERT
        # to bump its usage_count. Description ignored on conflict.
        payload.setdefault(matched_to, description)

    # Catalog-reused (decomposer matched directly against the hint)
    # ids that are themselves persisted labels: also UPSERT for
    # adoption tracking. We have to walk the original decomposition
    # because ``catalog_reused_capabilities`` was computed against
    # the catalog UNION before reconciliation rewriting.
    payload.update(
        _upsert_payload_for_reused_persisted_labels(
            decomposition=decomposition,
            persisted_ids_before=persisted_ids_before,
        )
    )
    return payload


def _upsert_payload_for_reused_persisted_labels(
    *,
    decomposition: GoalDecomposition,
    persisted_ids_before: set[str],
) -> dict[str, str]:
    """Identify catalog-reused ids that are already persisted labels.

    Used both on the cold path (no new coins this request) and as a
    helper for :func:`_build_upsert_payload`. Only labels that
    existed in the persisted store *before* this request are
    considered — that's what distinguishes "decomposer reused a
    persisted label" from "decomposer happened to pick a discovery-
    store-derived id".
    """

    if not persisted_ids_before:
        return {}
    out: dict[str, str] = {}
    for sub_task in decomposition.sub_tasks:
        cid = sub_task.suggested_capability_id
        if cid in persisted_ids_before and cid not in out:
            out[cid] = sub_task.description
    return out


def _descriptions_by_coined_id(decomposition: GoalDecomposition) -> dict[str, str]:
    """Map each coined id to the first sub-task's description that
    used it. Mirrors the helper inside the reconciler so the
    label store gets the same description-of-record."""

    out: dict[str, str] = {}
    coined = set(decomposition.new_capabilities)
    for sub_task in decomposition.sub_tasks:
        cid = sub_task.suggested_capability_id
        if cid in coined and cid not in out:
            out[cid] = sub_task.description
    return out


def _safe_persisted_label_ids() -> set[str]:
    """Defensive wrapper around :func:`load_persisted_label_ids` so a
    bad label store never blocks the planner. The recorder already
    swallows IOErrors, but we wrap once more here so even an
    *import-time* explosion (e.g. someone shipped a bad migration)
    doesn't take out ``/goal``.
    """

    try:
        return load_persisted_label_ids()
    except Exception as exc:  # noqa: BLE001 — defensive
        LOGGER.warning("label store unreachable for catalog union: %s", exc)
        return set()


def _safe_goal_hash(goal: str) -> str:
    """Stable opaque dedupe token for ``goal``.

    Same shape as ``discovery_gaps_store._hash_goal`` — lower-cased,
    whitespace-collapsed, sha256-truncated to 16 chars. Re-implemented
    here to avoid importing the discovery store into the planner
    (the dependency direction is planner → store, not the other way).
    """

    import hashlib

    cleaned = " ".join((goal or "").lower().split())
    if not cleaned:
        return ""
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]


def _decomposer_enabled() -> bool:
    """Honour ``PLANMYAGENTS_GOAL_DECOMPOSER`` (default ``on``).

    Setting it to ``off`` / ``0`` / ``false`` skips the decomposer
    entirely and the constrained planner's output flows through
    verbatim. Provided for tests and debugging; production should
    leave it on.
    """

    return os.getenv("PLANMYAGENTS_GOAL_DECOMPOSER", "on").lower() not in {
        "0",
        "false",
        "off",
        "none",
        "disabled",
    }


# Note: the in-file default for ``PLANMYAGENTS_DISCOVERY_STORE_URL`` used to
# live here and shadowed the one in ``web/app.py`` (which defaulted to a
# SQLite path instead of a Postgres DSN). Both sites now call
# ``planmyagents_api._config.discovery_store_url`` so the default is
# defined exactly once. See ``planmyagents_api/_config.py`` for the full
# backstory of the split-brain bug this addresses.


def _build_default_capability_catalog(goal: str) -> CapabilityCatalog:
    """Build the planner's capability catalog from cached candidates
    UNIONed with persisted labels coined by previous decompositions.

    Performance contract
    --------------------
    This function runs *before* the planner LLM call on every ``/goal``
    request. The previous implementation iterated
    :func:`default_discovery_sources` and called ``.search()`` on every
    one — including HTTP-bound sources like ``apis_guru``,
    ``hacker_news``, and ``github_code_search`` — which cost 30-60s on
    a single request before any planning happened. The catalog only
    needs the *set of known capability ids and aliases*, all of which
    are already in the cached discovery store.

    Behaviour
    ---------
    * If the cached store is reachable and non-empty, build the
      candidate-derived catalog from its candidates. This is a
      single Postgres read on the prod path — typically <100ms.
    * If the cached store is unreachable OR returns zero candidates
      (fresh install, first run), fall back to
      :func:`default_discovery_sources` with the goal text — accepting
      the latency cost only when there is literally no cache to read.
      Without this fallback a brand-new install would have an empty
      planner catalog and refuse every goal.
    * **Slice 2:** the candidate-derived catalog is then UNIONed
      with the set of capability ids previously coined by the
      decomposer and persisted by the label reconciler. This is what
      makes the vocabulary self-grow from real usage — a label coined
      one request ago becomes part of the catalog hint on the next
      request. Persisted labels are added with empty alias sets:
      they are pure vocabulary contributions, not aliases of any
      existing candidate, and the only consumer that cares about
      aliases (the legacy substring matcher in
      :mod:`infer_capabilities_from_catalog`) is no longer on the
      LLM-driven hot path.

    The ``goal`` parameter is intentionally unused on the fast path
    because the catalog is goal-agnostic. It is forwarded to the
    live-source fallback so a freshly installed system can still
    surface goal-relevant capabilities on the first request.
    """

    store_url = discovery_store_url()
    try:
        cached = discovery_store_for_path(store_url).load()
    except Exception:  # noqa: BLE001 — store unreachable, fall back to live
        cached = []

    if cached:
        catalog = build_capability_catalog(cached)
    else:
        # Fresh install / store unreachable — pay the live-source cost
        # only when there's no cached signal. This is the slow path;
        # in production it should hit only on the very first request.
        candidates = []
        for source in default_discovery_sources():
            candidates.extend(source.search(capabilities=set(), task_description=goal))
        catalog = build_capability_catalog(candidates)

    return _union_with_persisted_labels(catalog)


def _union_with_persisted_labels(catalog: CapabilityCatalog) -> CapabilityCatalog:
    """Return a new :class:`CapabilityCatalog` that includes both
    candidate-derived ids and persisted-label ids.

    Persisted labels enter with empty alias sets — they don't
    correspond to any indexed candidate yet (that's the whole point
    of the slice 2 vocabulary growth). This means the legacy
    substring matcher in :func:`infer_capabilities_from_catalog`
    will never match a goal *to* a persisted label via aliases, but
    the LLM decomposer (which uses ids only, no aliases) sees the
    full union and can reuse a coined label by id alone.

    Failures inside :func:`_safe_persisted_label_ids` already return
    an empty set, so the worst-case behaviour here is "label union
    is a no-op" — never an exception bubbling into ``/goal``.
    """

    persisted = _safe_persisted_label_ids()
    if not persisted:
        return catalog
    merged = dict(catalog.aliases_by_capability)
    for cid in persisted:
        merged.setdefault(cid, set())
    return CapabilityCatalog(aliases_by_capability=merged)
