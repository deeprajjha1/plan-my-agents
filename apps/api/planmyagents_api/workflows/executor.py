"""Workflow execution engine for planned goals."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from planmyagents_api.agents.base import ProviderAdapter
from planmyagents_api.agents.router import ProviderRouter, RouteDecision
from planmyagents_api.benchmark.models import ProviderRequest
from planmyagents_api.cost.cost_cap import CostCapPolicy, cost_cap_policy_from_env
from planmyagents_api.planner.goal import GoalPlan
from planmyagents_api.workflows.models import WorkflowExecution, WorkflowSubTaskResult
from planmyagents_api.workflows.scoring import score_workflow_response
from planmyagents_api.workflows.stitcher import stitch_workflow_results


class WorkflowRouter(Protocol):
    def route(self, capability: str) -> RouteDecision:
        """Return routing decision for a capability."""

    def provider_for(self, provider_id: str) -> ProviderAdapter:
        """Return executable provider adapter."""


@dataclass
class WorkflowExecutor:
    """Execute a planned workflow through configured providers.

    The executor enforces a cost cap *around* every paid provider
    call. By default the cap is built from env vars
    (``PLANMYAGENTS_COST_CAP_*``) — see
    :func:`planmyagents_api.cost.cost_cap.cost_cap_policy_from_env`.
    Tests can inject a custom :class:`CostCapPolicy` (including a
    disabled one) via the ``cost_cap_factory`` argument so they
    don't depend on env state.

    On a cost-cap refusal the sub-task result records:
      * ``refusal_reason`` — compact ``cost_cap_exceeded:...`` string,
      * ``route_decision["cost_cap"]`` — the structured payload
        from :class:`CostCapExceeded`.
    Subsequent sub-tasks are still attempted (so the operator
    sees the full picture of what would have been blocked, not
    just the first refusal). The cap state is *shared* across
    them, so once the budget is consumed every subsequent paid
    call refuses too.
    """

    router: WorkflowRouter
    cost_cap_factory: object = field(
        default=cost_cap_policy_from_env, repr=False, compare=False
    )

    async def execute(self, plan: GoalPlan, *, workflow_id: str = "workflow") -> WorkflowExecution:
        if not plan.executable:
            return WorkflowExecution(
                status="unsupported",
                summary=plan.summary,
                refusal_reasons=plan.refusal_reasons,
            )

        route_decisions = [self.router.route(sub_task.capability) for sub_task in plan.sub_tasks]
        route_errors = [decision.reason for decision in route_decisions if not decision.routable]
        if route_errors:
            return WorkflowExecution(
                status="unsupported",
                summary="The plan is understood, but production execution is not configured for every sub-task.",
                sub_task_results=[
                    WorkflowSubTaskResult(
                        ordinal=index,
                        capability=sub_task.capability,
                        description=sub_task.description,
                        provider_id=decision.provider_id,
                        route_decision=_route_decision_json(decision),
                        refusal_reason=None if decision.routable else decision.reason,
                    )
                    for index, (sub_task, decision) in enumerate(
                        zip(plan.sub_tasks, route_decisions, strict=True),
                        start=1,
                    )
                ],
                refusal_reasons=route_errors,
            )

        # Cost cap is shared across all sub-tasks of this goal so the
        # per-goal limit covers the whole plan, not each call in
        # isolation. The factory is called once per execute() so the
        # daily-baseline read happens at the start of the plan, not
        # before route resolution.
        cost_cap = self._build_cost_cap(workflow_id=workflow_id)

        results: list[WorkflowSubTaskResult] = []
        ordinal = 1
        for sub_task, decision in zip(plan.sub_tasks, route_decisions, strict=True):
            provider = self.router.provider_for(str(decision.provider_id))
            resolved_inputs = _resolve_inputs(sub_task.inputs, results)
            if not resolved_inputs:
                results.append(
                    WorkflowSubTaskResult(
                        ordinal=ordinal,
                        capability=sub_task.capability,
                        description=sub_task.description,
                        provider_id=provider.provider_id,
                        route_decision=_route_decision_json(decision),
                        refusal_reason="dependency_output_missing",
                    )
                )
                ordinal += 1
                continue

            for input_item in resolved_inputs:
                request = ProviderRequest(
                    capability=sub_task.capability,
                    inputs=input_item,
                    idempotency_key=_idempotency_key(workflow_id, ordinal, input_item),
                )
                response, refusal = await cost_cap.gated_execute(provider, request)
                if refusal is not None:
                    # Cost cap blocked this call. We record a
                    # refusal-shaped sub-task result so the
                    # workflow stitcher still sees a complete row
                    # for this ordinal (no silent omissions).
                    route_payload = _route_decision_json(decision)
                    route_payload["cost_cap"] = refusal.to_payload()
                    results.append(
                        WorkflowSubTaskResult(
                            ordinal=ordinal,
                            capability=sub_task.capability,
                            description=sub_task.description,
                            provider_id=provider.provider_id,
                            route_decision=route_payload,
                            refusal_reason=refusal.to_refusal_string(),
                        )
                    )
                    ordinal += 1
                    continue
                # gated_execute guarantees response is non-None
                # when refusal is None, but the typing-strict path
                # mirrors the assertion explicitly.
                assert response is not None
                score = score_workflow_response(sub_task.capability, response)
                results.append(
                    WorkflowSubTaskResult(
                        ordinal=ordinal,
                        capability=sub_task.capability,
                        description=sub_task.description,
                        provider_id=provider.provider_id,
                        route_decision=_route_decision_json(decision),
                        response=response,
                        score=score,
                        refusal_reason=None if score.succeeded else score.reason,
                    )
                )
                ordinal += 1

        return stitch_workflow_results(results)

    def _build_cost_cap(self, *, workflow_id: str) -> CostCapPolicy:
        """Resolve the cost-cap policy for this execution.

        The factory may be:
          * a callable (default), called with ``goal_hash=<workflow_id>``
            so the per-goal cap is attributed to this request,
          * a pre-built :class:`CostCapPolicy` (used by tests that
            want to inspect ledger writes after execute completes).
        """

        factory = self.cost_cap_factory
        if isinstance(factory, CostCapPolicy):
            return factory
        if callable(factory):
            try:
                policy = factory(goal_hash=workflow_id)
            except TypeError:
                # Older callers may not accept goal_hash; allow
                # zero-arg factories for back-compat.
                policy = factory()
            if isinstance(policy, CostCapPolicy):
                return policy
        # Fall back to env-built defaults rather than crashing the
        # workflow because someone passed a bad factory.
        return cost_cap_policy_from_env(goal_hash=workflow_id)


def default_workflow_executor() -> WorkflowExecutor:
    return WorkflowExecutor(router=ProviderRouter())


def _route_decision_json(decision: RouteDecision) -> dict:
    return {
        "capability": decision.capability,
        "provider_id": decision.provider_id,
        "reason": decision.reason,
        "candidates": decision.candidates,
    }


def _idempotency_key(workflow_id: str, ordinal: int, inputs: dict) -> str:
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    return f"{workflow_id}:{ordinal}:{encoded}"


def _resolve_inputs(inputs: dict, previous_results: list[WorkflowSubTaskResult]) -> list[dict]:
    fan_out_key = ""
    fan_out_values: list[object] = []
    resolved: dict = {}

    for key, value in inputs.items():
        if isinstance(value, dict) and isinstance(value.get("from_previous"), dict):
            ref = value["from_previous"]
            extracted = _extract_previous_output(
                previous_results,
                capability=str(ref.get("capability", "")),
                path=str(ref.get("path", "")),
            )
            if bool(ref.get("fan_out", False)):
                fan_out_key = key
                fan_out_values = extracted if isinstance(extracted, list) else [extracted]
                continue
            resolved[key] = extracted
        else:
            resolved[key] = value

    if fan_out_key:
        return [{**resolved, fan_out_key: item} for item in fan_out_values if item]
    return [resolved]


def _extract_previous_output(
    previous_results: list[WorkflowSubTaskResult], *, capability: str, path: str
) -> object | None:
    for result in reversed(previous_results):
        if result.capability != capability or not result.response:
            continue
        current: object = result.response.output
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                current = current[int(part)] if int(part) < len(current) else None
            else:
                return None
        return current
    return None
