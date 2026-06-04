"""Registry-driven provider routing for executable capabilities.

The user should never select a provider in the product UI. The router owns that.
For v0, routing is intentionally conservative:

- routing is driven by the provider registry, not hardcoded providers;
- only real configured providers are considered for product execution;
- if no configured provider exists, the product refuses with explicit setup reasons.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from planmyagents_api.agents.base import ProviderAdapter
from planmyagents_api.registry.loader import (
    discovered_providers_for_capability,
    load_registry,
    providers_for_capability,
)
from planmyagents_api.registry.promotions import merge_promoted_agents_from_store

AdapterFactory = Callable[[], ProviderAdapter]
DEV_RUNTIME_MODES = {
    "dev",
    "development",
    "experimental",
    "fixture",
    "local",
    "mock",
    "protocol-beta",
    "protocol_beta",
    "test",
}
ALLOW_DEV_PROVIDERS_ENV = "PLANMYAGENTS_ALLOW_DEV_PROVIDERS"
PROMOTED_PROVIDER_STORE_ENV = "PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL"
BENCHMARK_STORE_ENV = "PLANMYAGENTS_BENCHMARK_STORE_URL"
LOAD_PROMOTED_PROVIDERS_ENV = "PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB"

# Hand-written vendor adapters are NEVER reachable from the customer
# routing path under the 16-May-2026 product spec — they live under
# planmyagents_api.benchmark.baselines and are benchmark-only.
# Only the generic-protocol adapter family (planmyagents_api.agents.protocol)
# and the mock adapters (planmyagents_api.agents.mock, dev-only) may
# appear here, and only when the registry entry's adapter_module field
# resolves to them. Tests can inject additional factories via the
# `adapter_factories` constructor argument on `ProviderRouter`.
ADAPTER_FACTORIES: dict[str, AdapterFactory] = {}


@dataclass(frozen=True)
class RouteDecision:
    capability: str
    provider_id: str | None
    reason: str
    candidates: list[dict[str, Any]]

    @property
    def routable(self) -> bool:
        return self.provider_id is not None


class ProviderRouter:
    """Selects a configured provider for a capability.

    The router intentionally excludes benchmark baselines
    (``is_benchmark_baseline=true`` in the registry) from any
    customer-facing routing decision — they exist only as scoring
    reference points for the benchmark runner. Tests that need
    a non-protocol adapter to validate a routing path can inject a
    factory dictionary via the ``adapter_factories`` constructor
    argument; production callers must leave it empty so the firewall
    invariant holds.
    """

    def __init__(
        self,
        registry_path: Path | None = None,
        *,
        adapter_factories: dict[str, AdapterFactory] | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[4]
        self.registry_path = registry_path or root / "packages" / "registry" / "agents.json"
        self.registry = load_registry(self.registry_path)
        self.adapter_factories: dict[str, AdapterFactory] = dict(ADAPTER_FACTORIES)
        if adapter_factories:
            self.adapter_factories.update(adapter_factories)
        if _load_promoted_providers_from_db():
            store_url = os.getenv(PROMOTED_PROVIDER_STORE_ENV) or os.getenv(
                "PLANMYAGENTS_DISCOVERY_STORE_URL", ""
            )
            if store_url:
                self.registry = merge_promoted_agents_from_store(
                    self.registry,
                    store_url,
                    benchmark_store_url=os.getenv(BENCHMARK_STORE_ENV, ""),
                )

    def known_capabilities(self) -> set[str]:
        return {str(capability) for capability in self.registry["capabilities"]}

    def route(self, capability: str) -> RouteDecision:
        all_candidates = providers_for_capability(self.registry, capability)
        # Firewall: benchmark baselines are NEVER user-routable.
        # Benchmark runners reach them via planmyagents_api.benchmark.baselines
        # directly, never through this router.
        candidates = [agent for agent in all_candidates if not _is_benchmark_baseline(agent)]
        configured = [agent for agent in candidates if _is_configured(agent)]
        executable = [
            agent
            for agent in configured
            if self._adapter_factory_for(agent)
            and _passes_route_gate(agent)
            and _is_production_allowed(agent)
        ]

        if executable:
            best = sorted(executable, key=lambda agent: _routing_sort_key(agent, capability))[0]
            return RouteDecision(
                capability=capability,
                provider_id=best["id"],
                reason=(
                    f"Selected `{best['id']}` from {len(executable)} executable configured provider(s) "
                    f"for `{capability}`."
                ),
                candidates=[_candidate_summary(agent) for agent in candidates],
            )

        if configured:
            configured_ids = ", ".join(sorted(str(agent["id"]) for agent in configured))
            blocked_ids = ", ".join(
                sorted(
                    str(agent["id"])
                    for agent in configured
                    if self._adapter_factory_for(agent) and not _passes_route_gate(agent)
                )
            )
            dev_only_ids = ", ".join(
                sorted(
                    str(agent["id"])
                    for agent in configured
                    if self._adapter_factory_for(agent) and not _is_production_allowed(agent)
                )
            )
            gate_reason = (
                f" Benchmark gate is not passed for: {blocked_ids}."
                if blocked_ids
                else ""
            )
            dev_reason = (
                f" Dev/test/local providers are disabled for production routing: {dev_only_ids}."
                if dev_only_ids
                else ""
            )
            return RouteDecision(
                capability=capability,
                provider_id=None,
                reason=(
                    f"`{capability}` has configured provider(s), but no executable adapter is "
                    f"available in this runtime yet: {configured_ids}.{gate_reason}{dev_reason}"
                ),
                candidates=[_candidate_summary(agent) for agent in candidates],
            )

        if candidates:
            required_env = sorted(
                {
                    str(agent.get("auth", {}).get("env_var"))
                    for agent in candidates
                    if agent.get("auth", {}).get("env_var")
                }
            )
            return RouteDecision(
                capability=capability,
                provider_id=None,
                reason=(
                    f"`{capability}` is supported by {len(candidates)} provider(s), but none "
                    f"are configured. Configure one of: {', '.join(required_env) or 'provider API key'}."
                ),
                candidates=[_candidate_summary(agent) for agent in candidates],
            )

        discovered = discovered_providers_for_capability(self.registry, capability)
        if discovered:
            return RouteDecision(
                capability=capability,
                provider_id=None,
                reason=(
                    f"`{capability}` has discovered provider candidate(s), but they will fail "
                    "until API keys, adapters, and benchmarks are complete."
                ),
                candidates=[_discovered_candidate_summary(agent) for agent in discovered],
            )

        return RouteDecision(
            capability=capability,
            provider_id=None,
            reason=f"No provider in the registry supports capability `{capability}`.",
            candidates=[],
        )

    def provider_for(self, provider_id: str):
        agent = next(
            (item for item in self.registry.get("agents", []) if item.get("id") == provider_id),
            None,
        )
        if not agent:
            raise NotImplementedError(f"Provider `{provider_id}` is not in the active registry.")
        if _is_benchmark_baseline(agent):
            raise PermissionError(
                f"Provider `{provider_id}` is a benchmark-only baseline and is not user-routable."
            )
        blocked_reason = self._execution_blocker(agent)
        if blocked_reason:
            raise PermissionError(f"Provider `{provider_id}` is not executable: {blocked_reason}")
        factory = self.adapter_factories.get(provider_id) or self._adapter_factory_for(agent)
        if not factory:
            raise NotImplementedError(
                f"Provider `{provider_id}` is configured in the registry, but its executable "
                "adapter is not implemented yet."
            )
        return factory()

    def _adapter_factory_for(self, agent: dict[str, Any] | None) -> AdapterFactory | None:
        if not agent:
            return None
        if _is_benchmark_baseline(agent):
            return None
        provider_id = str(agent.get("id", ""))
        if provider_id in self.adapter_factories:
            return self.adapter_factories[provider_id]
        return _adapter_factory_from_module(agent)

    def _execution_blocker(self, agent: dict[str, Any]) -> str:
        if not _is_configured(agent):
            env_var = str(agent.get("auth", {}).get("env_var") or "provider credentials")
            return f"missing configuration for {env_var}"
        if not _passes_route_gate(agent):
            return f"benchmark gate is {agent.get('benchmark_status', 'not passed')}"
        if not _is_production_allowed(agent):
            return "dev/test/local/protocol-beta providers are disabled for production routing"
        if not self._adapter_factory_for(agent):
            return "executable adapter is not implemented"
        return ""


def _is_configured(agent: dict[str, Any]) -> bool:
    if not bool(agent.get("is_active", True)):
        return False
    if str(agent.get("auth", {}).get("type", "")).lower() == "none":
        return True
    # Multi-credential providers (Razorpay needs KEY_ID + SECRET,
    # PayPal needs CLIENT_ID + SECRET, AWS-style needs ACCESS_KEY +
    # SECRET) declare every required env var via ``required_env_vars``
    # in addition to the canonical ``auth.env_var`` for routing /
    # error messages. ALL listed env vars must be set for the agent
    # to be considered configured. Single-credential providers can
    # still set just ``auth.env_var`` (backward compatible).
    required = agent.get("required_env_vars") or []
    if isinstance(required, list) and required:
        return all(bool(os.getenv(str(name))) for name in required)
    env_var = agent.get("auth", {}).get("env_var")
    return bool(env_var and os.getenv(str(env_var)))


def _passes_route_gate(agent: dict[str, Any]) -> bool:
    if not bool(agent.get("requires_benchmark_gate", False)):
        return True
    return str(agent.get("benchmark_status", "")).lower() == "passed"


def _is_production_allowed(agent: dict[str, Any]) -> bool:
    if not _is_dev_only_provider(agent):
        return True
    return os.getenv(ALLOW_DEV_PROVIDERS_ENV, "").lower() in {"1", "true", "yes"}


def _is_benchmark_baseline(agent: dict[str, Any]) -> bool:
    """Firewall: hand-written benchmark baselines are never user-routable.

    Marked in the registry with ``"is_benchmark_baseline": true``.
    See ``apps/api/planmyagents_api/benchmark/baselines/__init__.py``.
    """
    return bool(agent.get("is_benchmark_baseline", False))


def _is_dev_only_provider(agent: dict[str, Any]) -> bool:
    runtime_mode = str(agent.get("runtime_mode", "production")).lower()
    if runtime_mode in DEV_RUNTIME_MODES:
        return True
    provider_id = str(agent.get("id", "")).lower()
    if provider_id.startswith(("dev-", "fixture-", "local-", "mock-", "test-")):
        return True
    vendor = str(agent.get("vendor", "")).lower()
    return "planmyagents local" in vendor or "test fixture" in vendor


def _load_promoted_providers_from_db() -> bool:
    return os.getenv(LOAD_PROMOTED_PROVIDERS_ENV, "").lower() in {"1", "true", "yes"}


def _adapter_factory_from_module(agent: dict[str, Any]) -> AdapterFactory | None:
    """Resolve an adapter factory from the registry ``adapter_module`` field.

    The ``adapter_module`` field MUST point to a generic-protocol or
    mock adapter — benchmark baselines live under
    ``planmyagents_api.benchmark.baselines.*`` and must never be
    referenced from this field. The firewall is enforced by the
    caller via :func:`_is_benchmark_baseline`.
    """
    adapter_module = str(agent.get("adapter_module") or "").strip()
    if not adapter_module:
        return None
    try:
        module_name, class_name = adapter_module.split(":", maxsplit=1)
        adapter_class = getattr(import_module(module_name), class_name)
    except (ImportError, AttributeError, ValueError):
        return None
    return lambda: _instantiate_adapter(adapter_class, agent)


def _instantiate_adapter(adapter_class, agent: dict[str, Any]) -> ProviderAdapter:
    try:
        return adapter_class(registry_agent=agent)
    except TypeError:
        return adapter_class()


def _routing_sort_key(agent: dict[str, Any], capability: str) -> tuple[int, float, str]:
    tier_rank = {"primary": 0, "secondary": 1, "specialty": 2, "tertiary": 3}
    matching = [item for item in agent.get("capabilities", []) if item.get("id") == capability]
    best_tier = min((tier_rank.get(str(item.get("tier")), 9) for item in matching), default=9)
    lowest_cost = min((float(item.get("unit_cost_usd", 9999)) for item in matching), default=9999.0)
    return (best_tier, lowest_cost, str(agent["id"]))


def _candidate_summary(agent: dict[str, Any]) -> dict[str, Any]:
    env_var = agent.get("auth", {}).get("env_var")
    # Benchmark baselines never have a user-facing executable adapter,
    # regardless of what adapter_module the registry entry carries.
    if _is_benchmark_baseline(agent):
        has_adapter = False
    else:
        provider_id = str(agent.get("id", ""))
        has_adapter = provider_id in ADAPTER_FACTORIES or bool(
            _adapter_factory_from_module(agent)
        )
    return {
        "provider_id": agent["id"],
        "display_name": agent.get("display_name"),
        "configured": _is_configured(agent),
        "has_executable_adapter": has_adapter,
        "benchmark_status": agent.get("benchmark_status", "not_required"),
        "requires_benchmark_gate": bool(agent.get("requires_benchmark_gate", False)),
        "runtime_mode": agent.get("runtime_mode", "production"),
        "dev_provider_blocked": _is_dev_only_provider(agent) and not _is_production_allowed(agent),
        "required_env_var": env_var,
        "is_benchmark_baseline": _is_benchmark_baseline(agent),
        "capabilities": [
            {
                "id": item.get("id"),
                "tier": item.get("tier"),
                "unit_cost_usd": item.get("unit_cost_usd"),
            }
            for item in agent.get("capabilities", [])
        ],
    }


def _discovered_candidate_summary(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_id": agent["id"],
        "display_name": agent.get("display_name"),
        "provider_type": agent.get("provider_type", "api_provider"),
        "configured": False,
        "has_executable_adapter": False,
        "lifecycle_status": agent.get("lifecycle_status"),
        "will_fail": bool(agent.get("will_fail", True)),
        "will_fail_reasons": agent.get("will_fail_reasons", []),
        "required_env_vars": agent.get("required_env_vars", []),
        "capabilities": [
            {
                "id": item.get("id"),
                "confidence": item.get("confidence"),
            }
            for item in agent.get("capabilities", [])
        ],
    }
