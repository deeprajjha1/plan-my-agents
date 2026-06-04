"""Promote benchmarked, adapter-ready discovery candidates into providers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from planmyagents_api.benchmark.store import benchmark_store_for_path
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.store import discovery_store_for_path

PROMOTION_EVIDENCE_STATUSES = {"capability_verified", "known_provider"}
PROMOTED_LIFECYCLES = {"configured", "promoted"}
GENERIC_PROTOCOL_ADAPTERS = {
    "a2a_agent": "planmyagents_api.agents.protocol:GenericA2AAdapter",
    "mcp_server": "planmyagents_api.agents.protocol:GenericMcpAdapter",
    "ai_agent": "planmyagents_api.agents.protocol:GenericAiAgentAdapter",
    "api_provider": "planmyagents_api.agents.protocol:GenericOpenApiAdapter",
}


def merge_promoted_agents_from_store(
    registry: dict[str, Any], store_url: str, *, benchmark_store_url: str = ""
) -> dict[str, Any]:
    """Return registry plus DB-backed promoted providers."""

    candidates = discovery_store_for_path(store_url).load()
    if benchmark_store_url:
        candidates = apply_benchmark_statuses(candidates, benchmark_store_url)
    return merge_promoted_candidates(registry, candidates)


def apply_benchmark_statuses(
    candidates: list[DiscoveryCandidate], benchmark_store_url: str
) -> list[DiscoveryCandidate]:
    statuses = benchmark_store_for_path(benchmark_store_url).latest_statuses()
    updated = []
    for candidate in candidates:
        capability_statuses = [
            statuses.get((candidate.id, capability.id), candidate.benchmark_status)
            for capability in candidate.capabilities
        ]
        if capability_statuses and all(status == "passed" for status in capability_statuses):
            benchmark_status = "passed"
        elif any(status == "failed" for status in capability_statuses):
            benchmark_status = "failed"
        else:
            benchmark_status = candidate.benchmark_status
        updated.append(DiscoveryCandidate(**{**candidate.__dict__, "benchmark_status": benchmark_status}))
    return updated


def merge_promoted_candidates(
    registry: dict[str, Any], candidates: list[DiscoveryCandidate]
) -> dict[str, Any]:
    """Merge promotion-ready candidates into the executable registry shape."""

    merged = {
        **registry,
        "capabilities": sorted(
            set(registry.get("capabilities", []))
            | {
                capability.id
                for candidate in candidates
                if is_promotion_ready(candidate)
                for capability in candidate.capabilities
            }
        ),
        "agents": list(registry.get("agents", [])),
        "updated_at": datetime.now(UTC).date().isoformat(),
    }
    existing_ids = {str(agent.get("id")) for agent in merged["agents"]}
    for candidate in sorted(candidates, key=lambda item: (item.discovery_priority, item.id)):
        if candidate.id in existing_ids or not is_promotion_ready(candidate):
            continue
        merged["agents"].append(candidate_to_agent(candidate))
        existing_ids.add(candidate.id)
    return merged


def is_promotion_ready(candidate: DiscoveryCandidate) -> bool:
    """Return true only for explicitly reviewed, benchmarked, adapter-backed candidates."""

    return (
        candidate.lifecycle_status in PROMOTED_LIFECYCLES
        and candidate.route_status == "ready_for_promotion"
        and not candidate.will_fail
        and candidate.verification_status in PROMOTION_EVIDENCE_STATUSES
        and candidate.benchmark_status == "passed"
        and bool(adapter_module_for(candidate))
        and bool(_api_base_url(candidate))
        and len(candidate.required_env_vars) <= 1
    )


def candidate_to_agent(candidate: DiscoveryCandidate) -> dict[str, Any]:
    """Convert a promotion-ready discovery candidate to an active provider record."""

    return {
        "id": candidate.id,
        "display_name": candidate.display_name,
        "vendor": candidate.vendor,
        "vendor_url": candidate.vendor_url,
        "provider_type": candidate.provider_type,
        "runtime_mode": _runtime_mode(candidate),
        "capabilities": [
            {
                "id": capability.id,
                "tier": "specialty",
                "endpoint": _endpoint(candidate),
                "unit_cost_usd": 0.0,
                "pricing_model": "per_call_estimated",
            }
            for capability in candidate.capabilities
        ],
        "api_base_url": _api_base_url(candidate),
        "auth": _auth(candidate),
        "rate_limit": {"rpm": 30, "concurrent": 2},
        "quality_notes": (
            "Generated from a promotion-ready discovery candidate. Keep benchmark "
            "and adapter metadata in the discovery store as source of truth."
        ),
        "gotchas": [
            "Generated providers are routable only when their adapter module imports successfully.",
            "Re-run benchmarks after endpoint, auth, or capability changes.",
        ],
        "is_active": True,
        "requires_benchmark_gate": True,
        "benchmark_status": candidate.benchmark_status,
        "adapter_module": adapter_module_for(candidate),
        "source_discovery": {
            "source": candidate.source,
            "evidence_url": candidate.evidence_url,
            "first_seen_at": candidate.first_seen_at,
            "last_seen_at": candidate.last_seen_at,
        },
    }


def _api_base_url(candidate: DiscoveryCandidate) -> str:
    return candidate.evidence_url or candidate.vendor_url


def adapter_module_for(candidate: DiscoveryCandidate) -> str:
    return candidate.adapter_module or GENERIC_PROTOCOL_ADAPTERS.get(candidate.provider_type, "")


def _runtime_mode(candidate: DiscoveryCandidate) -> str:
    adapter_module = adapter_module_for(candidate)
    if adapter_module in GENERIC_PROTOCOL_ADAPTERS.values():
        return "protocol_beta"
    return "production"


def _auth(candidate: DiscoveryCandidate) -> dict[str, str]:
    if not candidate.required_env_vars:
        return {"type": "none"}
    return {"type": "bearer", "env_var": candidate.required_env_vars[0]}


def _endpoint(candidate: DiscoveryCandidate) -> str:
    endpoints = {
        "a2a_agent": "A2A agent card",
        "mcp_server": "MCP tool call",
        "ai_agent": "Agent invocation",
    }
    return endpoints.get(candidate.provider_type, "Provider call")
