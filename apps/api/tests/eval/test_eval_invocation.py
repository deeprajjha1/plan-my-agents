from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.protocol import ENABLE_PROTOCOL_EXECUTION_ENV
from planmyagents_api.benchmark.models import ProviderRequest
from planmyagents_api.cost.cost_cap import CostCapPolicy
from planmyagents_api.discovery.models import (
    CandidateCapability,
    CandidateTool,
    DiscoveryCandidate,
)
from planmyagents_api.eval.invocation import (
    Executable,
    HostRateLimiter,
    InvocationResolver,
    Refused,
    idempotency_key,
)
from planmyagents_api.eval.models import EvalRunMode
from planmyagents_api.eval.safety import SafetyClassifier


def _mcp_candidate() -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id="mcp-1",
        display_name="MCP One",
        vendor="Vendor",
        vendor_url="https://mcp.example.com/rpc",
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
        tools=[CandidateTool(name="scrape", input_schema={})],
    )


def _a2a_candidate() -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id="a2a-1",
        display_name="A2A One",
        vendor="Vendor",
        vendor_url="https://a2a.example.com",
        provider_type="a2a_agent",
        capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
    )


def _resolver(**kwargs) -> InvocationResolver:
    return InvocationResolver(
        cost_cap=CostCapPolicy(enabled=True, per_goal_usd=1.0, daily_usd=10.0, ledger=None),
        safety=SafetyClassifier(),
        **kwargs,
    )


class InvocationResolverTest(unittest.TestCase):
    def test_gate_off_returns_gated(self) -> None:
        with mock.patch.dict(os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: ""}, clear=False):
            decision = _resolver().resolve(
                candidate=_mcp_candidate(),
                capability="web_scraping",
                run_mode=EvalRunMode.SANDBOX,
            )
        self.assertIsInstance(decision, Refused)
        self.assertEqual(decision.source, "gated")

    def test_gate_on_mcp_executable(self) -> None:
        with mock.patch.dict(os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False):
            decision = _resolver().resolve(
                candidate=_mcp_candidate(),
                capability="web_scraping",
                run_mode=EvalRunMode.SANDBOX,
            )
        self.assertIsInstance(decision, Executable)
        self.assertEqual(decision.protocol, "mcp")

    def test_a2a_refused_even_with_gate_on(self) -> None:
        with mock.patch.dict(os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False):
            decision = _resolver().resolve(
                candidate=_a2a_candidate(),
                capability="web_scraping",
                run_mode=EvalRunMode.SANDBOX,
            )
        self.assertIsInstance(decision, Refused)
        self.assertEqual(decision.source, "refused")
        self.assertEqual(decision.protocol, "a2a")

    def test_safety_blocked_unclassified_sandbox(self) -> None:
        # An unclassified capability permits only dry_run; sandbox is refused.
        with mock.patch.dict(os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False):
            cand = _mcp_candidate()
            cand.capabilities[0] = CandidateCapability(id="mystery_cap", confidence=0.9)
            decision = _resolver().resolve(
                candidate=cand,
                capability="mystery_cap",
                run_mode=EvalRunMode.SANDBOX,
            )
        self.assertIsInstance(decision, Refused)
        self.assertEqual(decision.source, "refused")

    def test_byo_live_without_sandbox_refused(self) -> None:
        with mock.patch.dict(os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False):
            decision = _resolver(sandbox_runner_wired=False).resolve(
                candidate=_mcp_candidate(),
                capability="web_scraping",
                run_mode=EvalRunMode.LIVE,
                requires_byo_credentials=True,
            )
        self.assertIsInstance(decision, Refused)
        self.assertIn("sandbox_unavailable", decision.reason)

    def test_idempotency_key_is_secret_free_function(self) -> None:
        key = idempotency_key(provider_id="mcp-1", capability="web_scraping", case_id="c1")
        self.assertEqual(key, "mcp-1:web_scraping:c1")

    def test_cost_cap_refusal_on_execute(self) -> None:
        # A zero-allowance cap must refuse the call inside the decorated
        # adapter and return a structured cost-cap result (no live call).
        from planmyagents_api.eval.invocation import _DecoratedAdapter

        class _FakeInner:
            provider_id = "mcp-1"
            capabilities = ["web_scraping"]
            base_url = "https://mcp.example.com/rpc"
            executed = False

            async def estimate_cost(self, request):
                return 0.5  # non-zero so the zero cap trips

            async def execute(self, request):  # pragma: no cover - must NOT run
                self.executed = True
                raise AssertionError("execute must not be called when cap refuses")

            async def health_check(self):
                return True

        inner = _FakeInner()
        decorated = _DecoratedAdapter(
            inner=inner,
            cost_cap=CostCapPolicy(enabled=True, per_goal_usd=0.0, daily_usd=0.0, ledger=None),
            rate_limiter=HostRateLimiter(min_interval_s=0.0),
            latency_budget_s=5.0,
        )
        request = ProviderRequest(
            capability="web_scraping",
            inputs={},
            idempotency_key="mcp-1:web_scraping:c1",
        )
        response = asyncio.run(decorated.execute(request))
        self.assertFalse(response.succeeded)
        self.assertIn("cost_cap_exceeded", response.error)
        self.assertFalse(inner.executed)

    def test_rate_limiter_no_op_when_zero_interval(self) -> None:
        limiter = HostRateLimiter(min_interval_s=0.0)
        # Should not sleep or raise.
        limiter.acquire("example.com")
        limiter.acquire("example.com")


if __name__ == "__main__":
    unittest.main()
