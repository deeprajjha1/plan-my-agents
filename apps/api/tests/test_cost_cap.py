"""Tests for :class:`CostCapPolicy` (per-goal + daily cap enforcement)."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse
from planmyagents_api.cost.cost_cap import (
    DEFAULT_DAILY_USD,
    DEFAULT_PER_GOAL_USD,
    CostCapPolicy,
    cost_cap_policy_from_env,
)
from planmyagents_api.cost.spend_ledger import (
    JsonSpendLedger,
    SpendEvent,
)


@dataclass
class _FakeAdapter:
    """Minimal stand-in for ProviderAdapter used in tests."""

    provider_id: str = "fake-payment"
    capabilities: list[str] | None = None
    estimate: float = 0.10
    response: ProviderResponse | None = None
    estimate_calls: int = 0
    execute_calls: int = 0

    def __post_init__(self) -> None:
        if self.capabilities is None:
            self.capabilities = ["payment_authorization"]
        if self.response is None:
            self.response = ProviderResponse(
                succeeded=True, output={"ok": True}, cost_usd=self.estimate, latency_ms=10
            )

    async def estimate_cost(self, request: ProviderRequest) -> float:  # noqa: ARG002
        self.estimate_calls += 1
        return self.estimate

    async def execute(self, request: ProviderRequest) -> ProviderResponse:  # noqa: ARG002
        self.execute_calls += 1
        return self.response  # type: ignore[return-value]


def _request(capability: str = "payment_authorization") -> ProviderRequest:
    return ProviderRequest(capability=capability, inputs={"k": "v"}, idempotency_key="t1")


class CostCapInitValidationTests(unittest.TestCase):
    def test_negative_per_goal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CostCapPolicy(per_goal_usd=-0.01, daily_usd=10.0)

    def test_negative_daily_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CostCapPolicy(per_goal_usd=0.50, daily_usd=-0.01)

    def test_disabled_policy_skips_baseline_read(self) -> None:
        # Pass a ledger that would explode if read; disabled policy
        # must NOT touch it.
        class BoomLedger:
            def spend_today_usd(self) -> float:
                raise RuntimeError("must not be called")

        policy = CostCapPolicy(
            ledger=BoomLedger(), per_goal_usd=0.5, daily_usd=10.0, enabled=False
        )
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.day_spent_usd, 0.0)


class CostCapCheckSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tempdir.name) / "spend.jsonl"
        self.ledger = JsonSpendLedger(self.ledger_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_under_per_goal_cap_passes(self) -> None:
        policy = CostCapPolicy(
            ledger=self.ledger, per_goal_usd=0.50, daily_usd=20.0
        )
        verdict = policy.check(
            capability="x", provider_id="p", projected_cost_usd=0.10
        )
        self.assertIsNone(verdict)

    def test_per_goal_cap_exceeded_returns_refusal(self) -> None:
        policy = CostCapPolicy(
            ledger=self.ledger, per_goal_usd=0.10, daily_usd=20.0
        )
        verdict = policy.check(
            capability="x", provider_id="p", projected_cost_usd=0.20
        )
        self.assertIsNotNone(verdict)
        assert verdict is not None
        self.assertEqual(verdict.scope, "per_goal")
        payload = verdict.to_payload()
        self.assertEqual(payload["kind"], "cost_cap_exceeded")
        self.assertEqual(payload["scope"], "per_goal")
        self.assertEqual(payload["limit_usd"], 0.10)

    def test_per_goal_cap_consumed_incrementally(self) -> None:
        policy = CostCapPolicy(
            ledger=self.ledger, per_goal_usd=0.30, daily_usd=20.0
        )
        # First $0.10 fits.
        self.assertIsNone(
            policy.check(capability="x", provider_id="p", projected_cost_usd=0.10)
        )
        policy.charge(capability="x", provider_id="p", cost_usd=0.10)
        # Second $0.10 still fits (total $0.20 ≤ $0.30).
        self.assertIsNone(
            policy.check(capability="x", provider_id="p", projected_cost_usd=0.10)
        )
        policy.charge(capability="x", provider_id="p", cost_usd=0.10)
        # Third $0.15 would push to $0.35 — refused.
        verdict = policy.check(
            capability="x", provider_id="p", projected_cost_usd=0.15
        )
        self.assertIsNotNone(verdict)
        assert verdict is not None
        self.assertEqual(verdict.scope, "per_goal")

    def test_daily_cap_exceeded_after_baseline(self) -> None:
        # Pre-populate the ledger with $19.95 spent earlier today.
        for _ in range(199):
            self.ledger.append(
                [SpendEvent(goal_hash="prior", capability="x", provider_id="p", cost_usd=0.10)]
            )
        # 199 * 0.10 = 19.90; one more 0.05 leaves $19.95.
        self.ledger.append(
            [SpendEvent(goal_hash="prior", capability="x", provider_id="p", cost_usd=0.05)]
        )

        policy = CostCapPolicy(
            ledger=self.ledger, per_goal_usd=10.0, daily_usd=20.0
        )
        # $0.04 would push us to $19.99 — still under.
        self.assertIsNone(
            policy.check(capability="x", provider_id="p", projected_cost_usd=0.04)
        )
        policy.charge(capability="x", provider_id="p", cost_usd=0.04)
        # The next $0.10 would push us to $20.09 — over the daily cap.
        verdict = policy.check(
            capability="x", provider_id="p", projected_cost_usd=0.10
        )
        self.assertIsNotNone(verdict)
        assert verdict is not None
        self.assertEqual(verdict.scope, "daily")

    def test_disabled_policy_lets_everything_through(self) -> None:
        policy = CostCapPolicy(
            ledger=self.ledger, per_goal_usd=0.0, daily_usd=0.0, enabled=False
        )
        # Even projecting $1000 is fine when disabled.
        self.assertIsNone(
            policy.check(capability="x", provider_id="p", projected_cost_usd=1000.0)
        )
        # charge is also a no-op when disabled.
        policy.charge(capability="x", provider_id="p", cost_usd=1000.0)
        self.assertEqual(policy.goal_spent_usd, 0.0)
        self.assertEqual(self.ledger.load_all(), [])

    def test_negative_projection_treated_as_zero(self) -> None:
        policy = CostCapPolicy(
            ledger=self.ledger, per_goal_usd=0.0, daily_usd=20.0
        )
        # per_goal=0 + projection clamped to 0 → no exceedance.
        self.assertIsNone(
            policy.check(capability="x", provider_id="p", projected_cost_usd=-1.0)
        )

    def test_charge_writes_to_ledger(self) -> None:
        policy = CostCapPolicy(
            ledger=self.ledger,
            per_goal_usd=1.0,
            daily_usd=20.0,
            goal_hash="goal-abc",
        )
        policy.charge(
            capability="email_send",
            provider_id="sendgrid",
            cost_usd=0.0001,
            idempotency_key="wf:1:k",
        )
        events = self.ledger.load_all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].provider_id, "sendgrid")
        self.assertEqual(events[0].goal_hash, "goal-abc")
        self.assertEqual(events[0].idempotency_key, "wf:1:k")


class CostCapGatedExecuteTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_call_executes_and_charges(self) -> None:
        ledger = JsonSpendLedger(Path(tempfile.mkdtemp()) / "spend.jsonl")
        policy = CostCapPolicy(
            ledger=ledger, per_goal_usd=1.0, daily_usd=10.0, goal_hash="g"
        )
        adapter = _FakeAdapter(estimate=0.10)
        response, refusal = await policy.gated_execute(adapter, _request())
        self.assertIsNone(refusal)
        self.assertIsNotNone(response)
        self.assertEqual(adapter.execute_calls, 1)
        self.assertEqual(adapter.estimate_calls, 1)
        self.assertAlmostEqual(policy.goal_spent_usd, 0.10)
        self.assertEqual(len(ledger.load_all()), 1)

    async def test_refusal_does_not_execute(self) -> None:
        ledger = JsonSpendLedger(Path(tempfile.mkdtemp()) / "spend.jsonl")
        policy = CostCapPolicy(
            ledger=ledger, per_goal_usd=0.05, daily_usd=10.0
        )
        adapter = _FakeAdapter(estimate=0.10)
        response, refusal = await policy.gated_execute(adapter, _request())
        self.assertIsNone(response)
        self.assertIsNotNone(refusal)
        # Critically: execute MUST NOT have been called when the cap blocked.
        self.assertEqual(adapter.execute_calls, 0)
        # And no spend was recorded.
        self.assertEqual(policy.goal_spent_usd, 0.0)
        self.assertEqual(len(ledger.load_all()), 0)

    async def test_estimate_failure_falls_back_to_unit_cost(self) -> None:
        ledger = JsonSpendLedger(Path(tempfile.mkdtemp()) / "spend.jsonl")
        policy = CostCapPolicy(
            ledger=ledger, per_goal_usd=0.50, daily_usd=10.0
        )

        class BoomEstimateAdapter(_FakeAdapter):
            unit_cost_usd: float = 0.05

            async def estimate_cost(self, request: ProviderRequest) -> float:  # noqa: ARG002
                raise RuntimeError("estimator down")

        adapter = BoomEstimateAdapter()
        response, refusal = await policy.gated_execute(adapter, _request())
        # Falls back to unit_cost_usd ($0.05), still under the cap.
        self.assertIsNone(refusal)
        self.assertIsNotNone(response)


class CostCapEnvFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preserve = {
            k: os.environ.get(k)
            for k in (
                "PLANMYAGENTS_COST_CAP_ENABLED",
                "PLANMYAGENTS_COST_CAP_PER_GOAL_USD",
                "PLANMYAGENTS_COST_CAP_DAILY_USD",
                "PLANMYAGENTS_SPEND_LEDGER_PATH",
            )
        }
        for k in self.preserve:
            os.environ.pop(k, None)
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["PLANMYAGENTS_SPEND_LEDGER_PATH"] = str(
            Path(self.tempdir.name) / "spend.jsonl"
        )

    def tearDown(self) -> None:
        for k, v in self.preserve.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tempdir.cleanup()

    def test_defaults_used_when_env_unset(self) -> None:
        os.environ.pop("PLANMYAGENTS_COST_CAP_PER_GOAL_USD", None)
        os.environ.pop("PLANMYAGENTS_COST_CAP_DAILY_USD", None)
        policy = cost_cap_policy_from_env(goal_hash="g")
        self.assertEqual(policy.per_goal_limit_usd, DEFAULT_PER_GOAL_USD)
        self.assertEqual(policy.daily_limit_usd, DEFAULT_DAILY_USD)
        self.assertTrue(policy.enabled)

    def test_env_overrides_apply(self) -> None:
        os.environ["PLANMYAGENTS_COST_CAP_PER_GOAL_USD"] = "1.25"
        os.environ["PLANMYAGENTS_COST_CAP_DAILY_USD"] = "75.00"
        policy = cost_cap_policy_from_env()
        self.assertAlmostEqual(policy.per_goal_limit_usd, 1.25)
        self.assertAlmostEqual(policy.daily_limit_usd, 75.0)

    def test_disabled_via_env(self) -> None:
        os.environ["PLANMYAGENTS_COST_CAP_ENABLED"] = "false"
        policy = cost_cap_policy_from_env()
        self.assertFalse(policy.enabled)

    def test_bogus_env_value_falls_back_to_default(self) -> None:
        os.environ["PLANMYAGENTS_COST_CAP_PER_GOAL_USD"] = "not-a-number"
        policy = cost_cap_policy_from_env()
        self.assertEqual(policy.per_goal_limit_usd, DEFAULT_PER_GOAL_USD)


if __name__ == "__main__":
    unittest.main()
