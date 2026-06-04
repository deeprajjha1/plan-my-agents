"""Promotion-readiness checks for discovered candidates."""

from __future__ import annotations

from planmyagents_api.discovery.models import DiscoveryCandidate


def promotion_readiness(candidate: DiscoveryCandidate) -> dict:
    """Return a conservative promotion-readiness report for a candidate."""

    blockers: list[str] = []
    required_steps: list[str] = []

    if candidate.required_env_vars:
        blockers.append("credentials_required")
        required_steps.append("Configure credentials or BYOK setup.")

    if not candidate.adapter_module:
        blockers.append("adapter_missing")
        required_steps.append("Implement and review provider adapter or protocol client.")

    if candidate.benchmark_status != "passed":
        blockers.append("benchmark_not_passed")
        required_steps.append("Add benchmark cases and pass benchmark gate.")

    if candidate.will_fail or candidate.route_status != "ready_for_promotion":
        blockers.append("not_routable")
        required_steps.append("Complete promotion review before runtime routing.")

    return {
        "ready_for_promotion": not blockers,
        "blockers": sorted(set(blockers)),
        "required_steps": sorted(set(required_steps)),
    }
