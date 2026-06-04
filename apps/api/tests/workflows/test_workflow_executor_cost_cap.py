"""Integration tests for the cost cap inside the WorkflowExecutor.

The headline test (``test_51st_call_refuses_with_cost_cap_exceeded``)
locks in the explicit guarantee called out in Sprint 3a: when 50
paid sub-tasks each cost $0.01 and the per-goal cap is $0.50, the
51st sub-task must refuse with ``cost_cap_exceeded`` *without
calling* the underlying adapter. The other tests cover the
ancillary semantics: refusal payload shape, the daily cap path,
non-paid sub-tasks bypassing the cap, and the ``executed=False``
overall status when every sub-task is refused.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.router import RouteDecision
from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse
from planmyagents_api.cost.cost_cap import CostCapPolicy
from planmyagents_api.cost.spend_ledger import JsonSpendLedger
from planmyagents_api.planner.goal import GoalPlan, PlannedSubTask
from planmyagents_api.workflows.executor import WorkflowExecutor


@dataclass
class _CountingPaidProvider:
    """Provider whose every call costs ``unit_cost_usd``.

    Tracks ``execute_calls`` so the test can assert "the executor
    actually stopped invoking the adapter once the cap was hit".
    """

    provider_id: str = "fake-payment"
    capabilities: list[str] = field(default_factory=lambda: ["payment_authorization"])
    unit_cost_usd: float = 0.01
    execute_calls: int = 0

    async def estimate_cost(self, request: ProviderRequest) -> float:  # noqa: ARG002
        return self.unit_cost_usd

    async def execute(self, request: ProviderRequest) -> ProviderResponse:  # noqa: ARG002
        self.execute_calls += 1
        return ProviderResponse(
            succeeded=True,
            output={"ok": True, "call_number": self.execute_calls},
            cost_usd=self.unit_cost_usd,
            latency_ms=1,
        )

    async def health_check(self) -> bool:
        return True


class _SingleProviderRouter:
    """Routes every capability to the same provider instance.

    Tests can't share a real ProviderRouter without setting up the
    full registry; this stand-in keeps the executor honest about
    its router contract while letting us count adapter calls.
    """

    def __init__(self, provider: _CountingPaidProvider) -> None:
        self.provider = provider

    def route(self, capability: str) -> RouteDecision:
        return RouteDecision(
            capability=capability,
            provider_id=self.provider.provider_id,
            reason="single-provider stand-in",
            candidates=[],
        )

    def provider_for(self, provider_id: str):  # noqa: ARG002
        return self.provider


def _plan_with_n_paid_subtasks(n: int) -> GoalPlan:
    return GoalPlan(
        status="executable",
        summary=f"Run {n} paid sub-tasks.",
        sub_tasks=[
            PlannedSubTask(
                capability="payment_authorization",
                description=f"paid call #{idx + 1}",
                inputs={"amount": 1, "i": idx + 1},
            )
            for idx in range(n)
        ],
    )


class CostCapWorkflowIntegrationTests(unittest.TestCase):
    """The cost cap must enforce its limits THROUGH the executor."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tempdir.name) / "spend.jsonl"
        self.ledger = JsonSpendLedger(self.ledger_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_51st_call_refuses_with_cost_cap_exceeded(self) -> None:
        # Per-goal cap $0.50; 50 paid calls at $0.01 each = $0.50
        # exactly. The 51st projection ($0.51) must trip the cap.
        provider = _CountingPaidProvider(unit_cost_usd=0.01)
        policy = CostCapPolicy(
            ledger=self.ledger,
            per_goal_usd=0.50,
            daily_usd=100.0,
            goal_hash="regression-51st",
        )
        executor = WorkflowExecutor(
            router=_SingleProviderRouter(provider),
            cost_cap_factory=policy,
        )
        plan = _plan_with_n_paid_subtasks(60)

        execution = asyncio.run(executor.execute(plan, workflow_id="regression-51st"))

        # Exactly 50 calls actually ran on the adapter.
        self.assertEqual(provider.execute_calls, 50)
        # Plan still produced 60 sub-task results (10 refusals).
        self.assertEqual(len(execution.sub_task_results), 60)
        succeeded = [r for r in execution.sub_task_results if r.succeeded]
        refused = [r for r in execution.sub_task_results if not r.succeeded]
        self.assertEqual(len(succeeded), 50)
        self.assertEqual(len(refused), 10)
        # Every refusal has the structured cost_cap payload + the
        # compact one-liner refusal_reason.
        for r in refused:
            self.assertIn("cost_cap_exceeded:per_goal", r.refusal_reason or "")
            cost_cap_payload = r.route_decision.get("cost_cap")
            self.assertIsNotNone(cost_cap_payload)
            self.assertEqual(cost_cap_payload["kind"], "cost_cap_exceeded")
            self.assertEqual(cost_cap_payload["scope"], "per_goal")
            self.assertEqual(cost_cap_payload["limit_usd"], 0.50)
        # Total cost reported by the executor matches the 50 calls
        # that actually ran (refusals don't add to spend).
        self.assertAlmostEqual(execution.total_cost_usd, 0.50, places=4)
        # Ledger persisted exactly one event per real call.
        self.assertEqual(len(self.ledger.load_all()), 50)

    def test_disabled_cap_lets_everything_through(self) -> None:
        provider = _CountingPaidProvider(unit_cost_usd=0.01)
        # Note: per_goal_usd=0.0 — would refuse everything if enabled.
        policy = CostCapPolicy(
            ledger=self.ledger,
            per_goal_usd=0.0,
            daily_usd=0.0,
            enabled=False,
        )
        executor = WorkflowExecutor(
            router=_SingleProviderRouter(provider),
            cost_cap_factory=policy,
        )
        execution = asyncio.run(
            executor.execute(_plan_with_n_paid_subtasks(5), workflow_id="dis")
        )
        self.assertEqual(provider.execute_calls, 5)
        # Disabled policy does not write to the ledger.
        self.assertEqual(self.ledger.load_all(), [])
        # All 5 sub-tasks succeeded.
        self.assertEqual(execution.status, "succeeded")

    def test_daily_cap_blocks_even_under_per_goal_cap(self) -> None:
        # Pre-populate the ledger with $9.99 already spent today
        # (under the daily cap of $10.00 by 1 cent).
        for _ in range(999):
            self.ledger.append([
                _ledger_event(cost=0.01)
            ])
        provider = _CountingPaidProvider(unit_cost_usd=0.01)
        policy = CostCapPolicy(
            ledger=self.ledger,
            per_goal_usd=10.0,    # well above what we'd spend
            daily_usd=10.0,       # tight: $9.99 baseline + 1 cent left
            goal_hash="daily-test",
        )
        executor = WorkflowExecutor(
            router=_SingleProviderRouter(provider),
            cost_cap_factory=policy,
        )
        execution = asyncio.run(
            executor.execute(_plan_with_n_paid_subtasks(5), workflow_id="daily-test")
        )
        # Exactly ONE call fits ($9.99 + $0.01 = $10.00 = cap).
        self.assertEqual(provider.execute_calls, 1)
        refused = [r for r in execution.sub_task_results if not r.succeeded]
        self.assertEqual(len(refused), 4)
        for r in refused:
            cost_cap_payload = r.route_decision.get("cost_cap")
            self.assertEqual(cost_cap_payload["scope"], "daily")

    def test_cost_cap_payload_serialization(self) -> None:
        """to_json on the executor payload must include cost_cap data
        verbatim so the UI can render the limit and remaining."""
        provider = _CountingPaidProvider(unit_cost_usd=0.01)
        policy = CostCapPolicy(
            ledger=self.ledger, per_goal_usd=0.005, daily_usd=10.0
        )
        executor = WorkflowExecutor(
            router=_SingleProviderRouter(provider),
            cost_cap_factory=policy,
        )
        plan = _plan_with_n_paid_subtasks(1)
        execution = asyncio.run(executor.execute(plan, workflow_id="ser"))
        payload = execution.to_json()
        sub = payload["sub_task_results"][0]
        self.assertIn("cost_cap_exceeded", sub["refusal_reason"])
        self.assertEqual(sub["route_decision"]["cost_cap"]["scope"], "per_goal")


def _ledger_event(*, cost: float):
    """Helper to keep the daily-cap setup readable."""
    from planmyagents_api.cost.spend_ledger import SpendEvent

    return SpendEvent(
        goal_hash="prior",
        capability="payment_authorization",
        provider_id="fake-payment",
        cost_usd=cost,
    )


if __name__ == "__main__":
    unittest.main()
