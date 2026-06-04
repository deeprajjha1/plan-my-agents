"""Backward-compatible import path for registry discovery enrichment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from planmyagents_api.discovery.service import enrich_registry_from_unsupported_plan as _enrich
from planmyagents_api.discovery.sources.base import DiscoverySource
from planmyagents_api.planner.goal import GoalPlan


def enrich_registry_from_unsupported_plan(
    *,
    goal: str,
    plan: GoalPlan,
    registry_path: Path,
    sources: list[DiscoverySource] | None = None,
    persist_registry: bool = False,
) -> dict[str, Any] | None:
    """Find non-routable discovered providers for a failed plan's missing capabilities."""

    return _enrich(
        goal=goal,
        plan=plan,
        registry_path=registry_path,
        sources=sources,
        persist_registry=persist_registry,
    )
