"""Provider registry loading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RegistryLoadError(ValueError):
    """Raised when the provider registry is malformed."""


def load_registry(path: Path) -> dict[str, Any]:
    """Load and minimally validate the provider registry JSON."""

    if not path.exists():
        raise RegistryLoadError(f"registry file does not exist: {path}")

    payload = json.loads(path.read_text())
    _validate_registry(payload)
    return payload


def providers_for_capability(registry: dict[str, Any], capability: str) -> list[dict[str, Any]]:
    """Return active providers that support a capability."""

    providers = []
    for agent in registry["agents"]:
        if not agent.get("is_active", True):
            continue
        if any(item["id"] == capability for item in agent.get("capabilities", [])):
            providers.append(agent)
    return providers


def discovered_providers_for_capability(
    registry: dict[str, Any], capability: str
) -> list[dict[str, Any]]:
    """Return non-routable discovered provider candidates for a capability."""

    providers = []
    for agent in registry.get("discovered_agents", []):
        if any(item["id"] == capability for item in agent.get("capabilities", [])):
            providers.append(agent)
    return providers


def gated_benchmark_capabilities(registry: dict[str, Any]) -> list[str]:
    """Capabilities that have at least one active agent with `requires_benchmark_gate=true`.

    Used by the benchmark scheduler so cron jobs cover exactly the cells the
    registry says must be benchmark-gated before a route_status flip — instead
    of historically being hardcoded to `email_verification`.
    """

    capabilities: set[str] = set()
    for agent in registry.get("agents", []):
        if not agent.get("is_active", True):
            continue
        if not bool(agent.get("requires_benchmark_gate", False)):
            continue
        for cap in agent.get("capabilities", []):
            cap_id = cap.get("id") if isinstance(cap, dict) else cap
            if cap_id:
                capabilities.add(cap_id)
    return sorted(capabilities)


def _validate_registry(payload: dict[str, Any]) -> None:
    required = ["version", "updated_at", "capabilities", "agents"]
    missing = [key for key in required if key not in payload]
    if missing:
        raise RegistryLoadError(f"registry missing required keys: {missing}")

    if not isinstance(payload["capabilities"], list) or not payload["capabilities"]:
        raise RegistryLoadError("registry `capabilities` must be a non-empty list")

    if not isinstance(payload["agents"], list) or not payload["agents"]:
        raise RegistryLoadError("registry `agents` must be a non-empty list")

    capability_set = set(payload["capabilities"])
    seen_ids: set[str] = set()
    for agent in payload["agents"]:
        for key in ["id", "display_name", "vendor", "capabilities", "api_base_url", "auth"]:
            if key not in agent:
                raise RegistryLoadError(f"agent missing required key `{key}`: {agent}")
        if agent["id"] in seen_ids:
            raise RegistryLoadError(f"duplicate agent id: {agent['id']}")
        seen_ids.add(agent["id"])

        for cap in agent["capabilities"]:
            if cap["id"] not in capability_set:
                raise RegistryLoadError(f"unknown capability {cap['id']} on agent {agent['id']}")
            if float(cap["unit_cost_usd"]) < 0:
                raise RegistryLoadError(f"negative unit cost on agent {agent['id']}")

    for candidate in payload.get("discovered_agents", []):
        for key in [
            "id",
            "display_name",
            "vendor",
            "provider_type",
            "capabilities",
            "lifecycle_status",
            "route_status",
            "will_fail",
            "will_fail_reasons",
            "required_env_vars",
        ]:
            if key not in candidate:
                raise RegistryLoadError(
                    f"discovered agent missing required key `{key}`: {candidate}"
                )
        if candidate["id"] in seen_ids:
            raise RegistryLoadError(
                f"discovered agent duplicates active agent id: {candidate['id']}"
            )
        seen_ids.add(candidate["id"])
