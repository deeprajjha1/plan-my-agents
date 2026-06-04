"""Invocation resolution: gate, safety, budgets, rate limiting, idempotency.

Bridges a candidate + run mode to either an *executable adapter* (wrapped in a
cost-capped, latency-bounded, rate-limited, idempotency-keyed decorator) or a
structured *refusal*. Protocol selection goes through the
``ProtocolInvokerRegistry`` — there are no protocol-specific branches here.

Honesty / safety invariants enforced here:
* Protocol execution gate off  → Refused(source="gated").              (R14.1)
* Safety class forbids run mode → Refused(source="refused").           (R5.2/5.3)
* Non-executable protocol       → Refused(source="refused").           (R16.4)
* BYO-creds live + sandbox stub → Refused(source="refused").           (R13.5)
* Cost cap would be exceeded    → structured cost-cap refusal result.  (R11.2)
* Latency budget exceeded       → structured timeout result.           (R11.3)
* Idempotency key is f(provider_id, capability, case_id) — never a secret.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from planmyagents_api.agents.protocol import (
    ENABLE_PROTOCOL_EXECUTION_ENV,
    GenericProtocolAdapter,
)
from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse
from planmyagents_api.cost.cost_cap import CostCapPolicy
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.eval.models import EvalRunMode
from planmyagents_api.eval.protocols import (
    ProtocolInvokerRegistry,
    ProtocolMaturity,
    default_registry,
)
from planmyagents_api.eval.safety import SafetyClass, SafetyClassifier


def protocol_execution_enabled() -> bool:
    """Mirror ``agents.protocol._protocol_execution_enabled`` (default off)."""

    return os.getenv(ENABLE_PROTOCOL_EXECUTION_ENV, "").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Executable:
    """A ready-to-run, fully-decorated adapter for an executable protocol."""

    adapter: GenericProtocolAdapter
    protocol: str
    protocol_maturity: str


@dataclass(frozen=True)
class Refused:
    """A structured refusal: no live call will be made."""

    reason: str
    source: str            # gated | refused
    protocol: str = ""
    protocol_maturity: str = ""


InvocationDecision = Executable | Refused


@dataclass
class HostRateLimiter:
    """Minimal token-bucket-ish limiter: min interval between calls per host."""

    min_interval_s: float = 0.0
    _last_call: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire(self, host: str) -> None:
        if self.min_interval_s <= 0:
            return
        with self._lock:
            now = time.monotonic()
            last = self._last_call.get(host, 0.0)
            wait = self.min_interval_s - (now - last)
            if wait > 0:
                time.sleep(wait)
            self._last_call[host] = time.monotonic()


@dataclass
class _DecoratedAdapter:
    """Wraps a generic-protocol adapter with budgets + rate limit + idempotency.

    Implements the ``ProviderAdapter`` contract (estimate_cost / execute /
    health_check) so it drops straight into ``BenchmarkRunner``.
    """

    inner: GenericProtocolAdapter
    cost_cap: CostCapPolicy
    rate_limiter: HostRateLimiter
    latency_budget_s: float

    @property
    def provider_id(self) -> str:
        return self.inner.provider_id

    @property
    def capabilities(self) -> list[str]:
        return self.inner.capabilities

    async def estimate_cost(self, request: ProviderRequest) -> float:
        return await self.inner.estimate_cost(request)

    async def health_check(self) -> bool:
        return await self.inner.health_check()

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        # Cost cap: project + refuse before any call (R11.2).
        capability = str(getattr(request, "capability", "") or "")
        projected = 0.0
        try:
            projected = float(await self.inner.estimate_cost(request) or 0.0)
        except Exception:  # noqa: BLE001 - a broken estimator must not disable the cap
            projected = 0.0
        refusal = self.cost_cap.check(
            capability=capability,
            provider_id=self.provider_id,
            projected_cost_usd=projected,
        )
        if refusal is not None:
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=0,
                error=refusal.to_refusal_string(),
                raw_response={"cost_cap": refusal.to_payload()},
            )

        # Rate limit per host.
        host = _host_of(self.inner)
        self.rate_limiter.acquire(host)

        # Execute with a latency budget (R11.3). The generic adapters already
        # use a per-call HTTP timeout; we add an outer wall-clock guard so a
        # hung adapter still surfaces a structured timeout result.
        started = time.monotonic()
        try:
            response = await _with_timeout(self.inner.execute(request), self.latency_budget_s)
        except TimeoutError:
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=f"latency_budget_exceeded:{self.latency_budget_s}s",
                raw_response={"timeout": True},
            )

        # Charge actual cost so the daily cap reflects reality.
        actual = float(getattr(response, "cost_usd", 0.0) or 0.0)
        self.cost_cap.charge(
            capability=capability,
            provider_id=self.provider_id,
            cost_usd=actual,
            idempotency_key=str(getattr(request, "idempotency_key", "") or ""),
        )
        return response


async def _with_timeout(coro, budget_s: float):
    import asyncio

    if budget_s <= 0:
        return await coro
    return await asyncio.wait_for(coro, timeout=budget_s)


def _host_of(adapter: GenericProtocolAdapter) -> str:
    from urllib.parse import urlparse

    url = getattr(adapter, "base_url", "") or ""
    try:
        return urlparse(url).netloc or "unknown"
    except ValueError:
        return "unknown"


def idempotency_key(*, provider_id: str, capability: str, case_id: str) -> str:
    """Idempotency key is a pure function of non-secret identifiers (R13.3)."""

    return f"{provider_id}:{capability}:{case_id}"


@dataclass
class InvocationResolver:
    cost_cap: CostCapPolicy
    safety: SafetyClassifier
    registry: ProtocolInvokerRegistry = field(default_factory=default_registry)
    rate_limiter: HostRateLimiter = field(default_factory=HostRateLimiter)
    latency_budget_s: float = 12.0
    sandbox_runner_wired: bool = False  # agents/sandbox_runner.py is a stub today

    def resolve(
        self,
        *,
        candidate: DiscoveryCandidate,
        capability: str,
        run_mode: EvalRunMode,
        requires_byo_credentials: bool = False,
    ) -> InvocationDecision:
        invoker = self.registry.for_candidate(candidate)
        protocol = self.registry.protocol_for_candidate(candidate)
        maturity = invoker.maturity.value if invoker else ProtocolMaturity.PLANNED.value

        # 0. dry_run never makes a live call, regardless of gate/safety (R5.5).
        if run_mode == EvalRunMode.DRY_RUN:
            return Refused(
                reason="dry_run: no live provider call is made.",
                source="dry_run",
                protocol=protocol,
                protocol_maturity=maturity,
            )

        # 1. Protocol gate dominates everything (R14.1).
        if not protocol_execution_enabled():
            return Refused(
                reason=(
                    "Protocol execution gate is disabled "
                    f"({ENABLE_PROTOCOL_EXECUTION_ENV} unset). No live call."
                ),
                source="gated",
                protocol=protocol,
                protocol_maturity=maturity,
            )

        # 2. Non-executable protocol → verification-only upstream; refuse here.
        if invoker is None or invoker.maturity != ProtocolMaturity.EXECUTABLE:
            return Refused(
                reason=(
                    f"protocol '{protocol}' is not executable "
                    f"(maturity={maturity})."
                ),
                source="refused",
                protocol=protocol,
                protocol_maturity=maturity,
            )

        # 3. Safety gate: the requested run mode must be permitted (R5.2/5.3).
        if not self.safety.is_permitted(capability, run_mode):
            return Refused(
                reason=(
                    f"safety_class={self.safety.classify(capability).value} "
                    f"forbids run_mode={run_mode.value} for '{capability}'."
                ),
                source="refused",
                protocol=protocol,
                protocol_maturity=maturity,
            )

        # 4. BYO-credential live execution requires the sandbox runner (R13.5).
        if requires_byo_credentials and run_mode == EvalRunMode.LIVE and not self.sandbox_runner_wired:
            return Refused(
                reason=(
                    "sandbox_unavailable: BYO-credential live execution "
                    "requires the sandbox runner (not yet wired)."
                ),
                source="refused",
                protocol=protocol,
                protocol_maturity=maturity,
            )

        # 5. Build the decorated, budget-enforced adapter.
        adapter = invoker.build_adapter(candidate)
        decorated = _DecoratedAdapter(
            inner=adapter,
            cost_cap=self.cost_cap,
            rate_limiter=self.rate_limiter,
            latency_budget_s=self.latency_budget_s,
        )
        return Executable(
            adapter=decorated,  # type: ignore[arg-type]
            protocol=protocol,
            protocol_maturity=maturity,
        )

    def safety_class(self, capability: str) -> SafetyClass:
        return self.safety.classify(capability)
