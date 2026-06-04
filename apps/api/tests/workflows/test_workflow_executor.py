from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.mock import MockContactEnricher, MockEmailVerifier
from planmyagents_api.agents.router import RouteDecision
from planmyagents_api.planner.goal import GoalPlan, PlannedSubTask
from planmyagents_api.workflows.executor import WorkflowExecutor


class FakeRouter:
    def __init__(self, providers: dict[str, object]) -> None:
        self.providers = providers

    def route(self, capability: str) -> RouteDecision:
        provider_id = {
            "contact_enrichment": "mock-contact-enricher",
            "email_verification": "mock-email-verifier",
        }.get(capability)
        if provider_id and provider_id in self.providers:
            return RouteDecision(
                capability=capability,
                provider_id=provider_id,
                reason=f"selected {provider_id}",
                candidates=[],
            )
        return RouteDecision(
            capability=capability,
            provider_id=None,
            reason=f"no provider for {capability}",
            candidates=[],
        )

    def provider_for(self, provider_id: str):
        return self.providers[provider_id]


class WorkflowExecutorTest(unittest.TestCase):
    def test_executes_and_stitches_multistep_workflow(self) -> None:
        plan = GoalPlan(
            status="executable",
            summary="Enrich and verify a contact.",
            sub_tasks=[
                PlannedSubTask(
                    capability="contact_enrichment",
                    description="Find CTO contact",
                    inputs={
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "company_domain": "acme.com",
                        "expected_title": "CTO",
                    },
                ),
                PlannedSubTask(
                    capability="email_verification",
                    description="Verify Jane's email",
                    inputs={"email": "jane.doe@acme.com"},
                ),
            ],
        )
        executor = WorkflowExecutor(
            router=FakeRouter(
                {
                    "mock-contact-enricher": MockContactEnricher(),
                    "mock-email-verifier": MockEmailVerifier(),
                }
            )
        )

        execution = asyncio.run(executor.execute(plan, workflow_id="test-workflow"))

        self.assertEqual("succeeded", execution.status)
        self.assertEqual(2, len(execution.sub_task_results))
        self.assertEqual(2, len(execution.records))
        self.assertGreater(execution.total_cost_usd, 0)
        self.assertGreater(execution.average_confidence, 0)
        self.assertEqual("mock-contact-enricher", execution.records[0]["provider_id"])

    def test_refuses_when_any_subtask_is_not_routable(self) -> None:
        plan = GoalPlan(
            status="executable",
            summary="Find companies and enrich contacts.",
            sub_tasks=[
                PlannedSubTask(
                    capability="semantic_search",
                    description="Find EU SaaS companies",
                    inputs={"query": "EU SaaS companies"},
                ),
                PlannedSubTask(
                    capability="contact_enrichment",
                    description="Find CTO contact",
                    inputs={"first_name": "Jane", "last_name": "Doe", "company_domain": "acme.com"},
                ),
            ],
        )
        executor = WorkflowExecutor(
            router=FakeRouter({"mock-contact-enricher": MockContactEnricher()})
        )

        execution = asyncio.run(executor.execute(plan))

        self.assertEqual("unsupported", execution.status)
        self.assertIn("no provider for semantic_search", execution.refusal_reasons)
        self.assertFalse(execution.executed)


if __name__ == "__main__":
    unittest.main()
