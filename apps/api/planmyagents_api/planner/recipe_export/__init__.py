"""Recipe export — convert a planned goal into a downloadable artifact.

Sprint 4 T1-A. The recipe is the keystone product output under the
16-May-2026 product spec: PlanMyAgents plans + ranks, the user
executes in their own environment using a recipe that drops into
Claude Desktop / Cursor / n8n / their CLI of choice.

Architecture
------------

A recipe goes through three layers:

1. **Plan dict** — output of ``/goal`` (``plan_payload``). Shape is
   whatever the planner produces today; renderers MUST tolerate
   missing fields gracefully (e.g. ``discovery`` absent, sub-tasks
   with no recommended provider).
2. **RecipeContext** — a normalized, format-agnostic representation
   built from the plan dict by :func:`build_recipe_context`. This is
   the only shape the renderers see, which means new formats can be
   added without revisiting the plan dict shape, and the plan dict
   can evolve (more fields, richer discovery payload) without
   breaking any renderer.
3. **Bytes + content_type** — each renderer in
   ``planmyagents_api.planner.recipe_export.<format>`` produces a
   ``(bytes, content_type, filename_suffix)`` tuple. The endpoint
   wraps it in an HTTP response.

Supported formats (see ``RECIPE_FORMATS``)
------------------------------------------

* ``claude_desktop_json`` — drops into ``~/Library/Application
  Support/Claude/claude_desktop_config.json``'s ``mcpServers`` map.
* ``n8n_json`` — n8n workflow JSON; one node per exportable API sub-task.
* ``cursor_prompt`` — Cursor MCP config block for
  ``.cursor/mcp.json`` plus a system-prompt template.
* ``markdown`` — human-readable runbook (the universal fallback).
* ``cli`` — bash script with ``npx`` / ``curl`` invocations the
  user can run end-to-end.

Firewall invariant
------------------

Renderers MUST NOT import anything from
``planmyagents_api.benchmark.baselines`` or
``planmyagents_api.agents.router``. A recipe is a description of
what the user should do in their environment, not a recommendation
backed by our own routing decisions. CI lint (Sprint 4 T5-1)
enforces this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Recipe context — what every renderer sees.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecommendedProvider:
    """One concrete provider recommendation for a sub-task.

    None of these fields are required at runtime — every renderer
    must tolerate the empty case (no provider found) and emit a
    "describe what the user must find for themselves" stub. The
    recipe layer is honest about gaps, never papers over them.
    """

    provider_id: str
    display_name: str
    provider_type: str  # mcp_server | a2a_agent | openapi | ai_agent | api_provider
    docs_url: str = ""
    install_command: str = ""  # e.g. "npx -y @apify/actors-mcp-server"
    api_base_url: str = ""
    required_env_vars: tuple[str, ...] = field(default_factory=tuple)
    verification_status: str = "unverified"
    benchmark_status: str = "not_started"
    route_status: str = ""
    will_fail: bool = False
    will_fail_reasons: tuple[str, ...] = field(default_factory=tuple)
    judge_status: str = ""
    accepted_by_judge: bool | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RecommendedProvider:
        return cls(
            provider_id=str(payload.get("id") or payload.get("provider_id") or ""),
            display_name=str(payload.get("display_name") or ""),
            provider_type=str(payload.get("provider_type") or "api_provider"),
            docs_url=str(payload.get("docs_url") or payload.get("vendor_url") or ""),
            install_command=str(payload.get("install_command") or ""),
            api_base_url=str(payload.get("api_base_url") or ""),
            required_env_vars=tuple(payload.get("required_env_vars") or ()),
            verification_status=str(payload.get("verification_status") or "unverified"),
            benchmark_status=str(payload.get("benchmark_status") or "not_started"),
            route_status=str(payload.get("route_status") or ""),
            will_fail=bool(payload.get("will_fail", False)),
            will_fail_reasons=tuple(payload.get("will_fail_reasons") or ()),
            judge_status=str(payload.get("judge_status") or ""),
            accepted_by_judge=_optional_bool(payload.get("accepted_by_judge")),
        )

    @property
    def is_exportable(self) -> bool:
        if self.will_fail or self.route_status == "will_fail":
            return False
        if self.accepted_by_judge is False or self.judge_status == "rejected":
            return False
        if self.provider_type == "mcp_server":
            return bool(self.install_command.strip())
        if self.provider_type in {"openapi", "api_provider", "payment_provider"}:
            return bool(self.api_base_url.strip())
        return False


@dataclass(frozen=True)
class RecipeStep:
    """One sub-task plus its recommended provider (if any)."""

    ordinal: int
    capability: str
    description: str
    inputs: dict[str, Any] = field(default_factory=dict)
    recommendation: RecommendedProvider | None = None
    alternates: tuple[RecommendedProvider, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RecipeCoverage:
    """Step-level honesty metadata for recipe generation."""

    step_count: int
    recommended_step_count: int
    exportable_step_count: int
    gap_count: int
    status: str

    def to_dict(self) -> dict[str, int | str]:
        return {
            "step_count": self.step_count,
            "recommended_step_count": self.recommended_step_count,
            "exportable_step_count": self.exportable_step_count,
            "gap_count": self.gap_count,
            "status": self.status,
        }


@dataclass(frozen=True)
class RecipeContext:
    """Normalized recipe context — input to every renderer."""

    goal_id: str
    goal_text: str
    steps: tuple[RecipeStep, ...]
    workflow_option_index: int = 0
    planner_tier: str = ""
    cost_estimate_usd: float | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def covered_step_count(self) -> int:
        return self.coverage.recommended_step_count

    @property
    def coverage(self) -> RecipeCoverage:
        recommended_step_count = sum(
            1 for step in self.steps if step.recommendation is not None
        )
        exportable_step_count = sum(
            1
            for step in self.steps
            if step.recommendation is not None and step.recommendation.is_exportable
        )
        gap_count = self.step_count - exportable_step_count
        if self.step_count == 0:
            status = "empty"
        elif exportable_step_count == 0:
            status = "gap_only"
        elif gap_count:
            status = "partial"
        else:
            status = "complete"
        return RecipeCoverage(
            step_count=self.step_count,
            recommended_step_count=recommended_step_count,
            exportable_step_count=exportable_step_count,
            gap_count=gap_count,
            status=status,
        )


# ---------------------------------------------------------------------------
# Conversion from a /goal plan dict to a RecipeContext.
# ---------------------------------------------------------------------------


def build_recipe_context(
    *,
    goal_id: str,
    goal_text: str,
    plan_payload: dict[str, Any],
    workflow_option_index: int = 0,
) -> RecipeContext:
    """Convert a ``/goal`` plan_payload into a renderer-ready context.

    Robust to partial plans:
    * missing ``sub_tasks`` -> empty steps
    * missing ``discovery`` -> every step has ``recommendation=None``
    * sub-task with no candidate match -> step renders as a gap

    The ``workflow_option_index`` is reserved for future use when
    ``/goal`` returns multiple ranked workflow options; today there
    is one workflow per goal so the index is always 0. We keep it
    in the API so frontend code doesn't have to change later.
    """
    sub_tasks = plan_payload.get("sub_tasks") or []
    discovery = plan_payload.get("discovery") or {}
    candidates_by_capability = _index_candidates_by_capability(discovery)

    steps: list[RecipeStep] = []
    for ordinal, sub_task in enumerate(sub_tasks, start=1):
        if not isinstance(sub_task, dict):
            continue
        capability = str(sub_task.get("capability") or "").strip()
        if not capability:
            continue
        description = str(sub_task.get("description") or capability).strip()
        inputs = sub_task.get("inputs") if isinstance(sub_task.get("inputs"), dict) else {}
        recs = candidates_by_capability.get(capability, [])
        rec = recs[0] if recs else None
        alternates = tuple(recs[1:4])  # cap at top 3 alternates
        steps.append(
            RecipeStep(
                ordinal=ordinal,
                capability=capability,
                description=description,
                inputs=inputs,
                recommendation=rec,
                alternates=alternates,
            )
        )

    planner_meta = (
        plan_payload.get("planner", {})
        if isinstance(plan_payload.get("planner"), dict)
        else {}
    )
    tier_used = (
        planner_meta.get("llm_quality", {}).get("planner", {}).get("tier_used", "")
        if isinstance(planner_meta.get("llm_quality"), dict)
        else ""
    )
    cost_estimate = plan_payload.get("cost_estimate") or {}
    cost_total = (
        cost_estimate.get("total_usd") if isinstance(cost_estimate, dict) else None
    )

    notes = []
    if plan_payload.get("status") == "unsupported":
        notes.append(
            "This plan was refused by the engine — every sub-task lacks a "
            "qualified routable provider. The recipe below describes what you "
            "must find yourself; we have not validated any provider for it."
        )
    if steps and sum(1 for step in steps if step.recommendation) < len(steps):
        notes.append(
            "Some sub-tasks have no recommended provider — they appear in the "
            "recipe as TODO blocks. Discover one yourself or check back later "
            "as discovery refreshes."
        )
    if steps and any(
        step.recommendation is not None and not step.recommendation.is_exportable
        for step in steps
    ):
        notes.append(
            "Some recommended providers are not exportable to host config yet — "
            "they are shown as references, not runnable workflow steps."
        )

    return RecipeContext(
        goal_id=goal_id,
        goal_text=goal_text,
        steps=tuple(steps),
        workflow_option_index=workflow_option_index,
        planner_tier=str(tier_used or ""),
        cost_estimate_usd=float(cost_total) if cost_total is not None else None,
        notes=tuple(notes),
    )


def _index_candidates_by_capability(
    discovery: dict[str, Any],
) -> dict[str, list[RecommendedProvider]]:
    """Build {capability_id: [RecommendedProvider ranked]} from a discovery block.

    The discovery block coming out of ``/goal`` can take several
    shapes depending on which path produced it (refusal vs success).
    We tolerate all of them: any list of candidate-shaped dicts at
    the top level, under ``agentic_results``, or under
    ``candidates_by_capability``.
    """
    by_capability: dict[str, list[RecommendedProvider]] = {}

    def absorb(candidates: list[dict[str, Any]]) -> None:
        for raw in candidates:
            if not isinstance(raw, dict):
                continue
            if _blocks_recipe_recommendation(raw):
                continue
            provider = RecommendedProvider.from_dict(raw)
            for capability in raw.get("capabilities") or []:
                if isinstance(capability, dict):
                    capability_id = str(capability.get("id") or "").strip()
                elif isinstance(capability, str):
                    capability_id = capability.strip()
                else:
                    continue
                if not capability_id:
                    continue
                by_capability.setdefault(capability_id, []).append(provider)

    if isinstance(discovery.get("agentic_results"), list):
        absorb(discovery["agentic_results"])
    if isinstance(discovery.get("candidates"), list):
        absorb(discovery["candidates"])
    if isinstance(discovery.get("candidates_by_capability"), dict):
        for items in discovery["candidates_by_capability"].values():
            if isinstance(items, list):
                absorb(items)
    return by_capability


def _blocks_recipe_recommendation(candidate: dict[str, Any]) -> bool:
    """Return True when a candidate is explicitly not runnable today.

    Older synthetic fixtures did not carry route fields, so absence is
    treated as unknown rather than blocked. Explicit `will_fail=true` or
    `route_status=will_fail` is enough to keep it out of executable-looking
    recipe recommendations.
    """

    if candidate.get("will_fail") is True:
        return True
    if str(candidate.get("route_status") or "").strip() == "will_fail":
        return True
    if _optional_bool(candidate.get("accepted_by_judge")) is False:
        return True
    return str(candidate.get("judge_status") or "").strip() == "rejected"


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


# ---------------------------------------------------------------------------
# Format registry + dispatcher.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderedRecipe:
    """Output of a renderer — bytes + HTTP metadata."""

    body: bytes
    content_type: str
    filename_suffix: str  # e.g. ".json" or ".yaml"


# Format id -> human label; the renderer module path is derived
# (``planmyagents_api.planner.recipe_export.<format_id>.render``).
RECIPE_FORMATS: dict[str, str] = {
    "claude_desktop_json": "Claude Desktop config",
    "n8n_json": "n8n workflow JSON",
    "cursor_prompt": "Cursor MCP config",
    "markdown": "Human-readable runbook",
    "cli": "Bash CLI script",
}


def render_recipe(context: RecipeContext, format_id: str) -> RenderedRecipe:
    """Render ``context`` in ``format_id``; raise ``ValueError`` if unknown."""

    if format_id not in RECIPE_FORMATS:
        raise ValueError(
            f"Unknown recipe format: {format_id!r}. "
            f"Supported: {sorted(RECIPE_FORMATS)}"
        )
    # Late import so each format module is independent and adding a
    # new one doesn't require touching this file beyond the dict.
    from importlib import import_module

    module = import_module(
        f"planmyagents_api.planner.recipe_export.{format_id}"
    )
    return module.render(context)  # type: ignore[no-any-return]
