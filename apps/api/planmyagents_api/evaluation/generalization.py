"""Generalization probes for discovery/ranking behavior.

The scenarios are examples used to catch regressions. They must not become
product routing rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from planmyagents_api.discovery.service import search_candidates
from planmyagents_api.discovery.sources.base import DiscoverySource


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    passed: bool
    failures: list[str]
    top_provider_ids: list[str]
    result_count: int

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "failures": self.failures,
            "top_provider_ids": self.top_provider_ids,
            "result_count": self.result_count,
        }


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    scenarios = payload.get("scenarios", []) if isinstance(payload, dict) else []
    return [scenario for scenario in scenarios if isinstance(scenario, dict)]


def run_scenarios(
    *,
    scenarios: list[dict[str, Any]],
    sources: list[DiscoverySource],
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    results = [
        run_scenario(scenario=scenario, sources=sources, store_path=store_path)
        for scenario in scenarios
    ]
    return {
        "passed": all(result.passed for result in results),
        "scenario_count": len(results),
        "failed_count": sum(1 for result in results if not result.passed),
        "results": [result.to_json() for result in results],
    }


def run_scenario(
    *,
    scenario: dict[str, Any],
    sources: list[DiscoverySource],
    store_path: str | Path | None = None,
) -> ScenarioResult:
    scenario_id = str(scenario.get("id") or "unnamed")
    top_n = int(scenario.get("top_n") or 10)
    payload = search_candidates(
        capabilities={str(item) for item in scenario.get("capabilities", []) if str(item)},
        task_description=str(scenario.get("goal") or ""),
        sources=sources,
        store_path=store_path,
        limit=max(top_n, 20),
        persist=False,
        load_store=bool(store_path),
    )
    top_provider_ids = [
        str(item.get("provider_id")) for item in payload.get("results", [])[:top_n]
    ]
    all_provider_ids = [str(item.get("provider_id")) for item in payload.get("results", [])]
    failures: list[str] = []

    if bool(scenario.get("expect_no_results", False)) and all_provider_ids:
        failures.append(f"expected no results, got {all_provider_ids[:top_n]}")

    include_any = {str(item) for item in scenario.get("top_must_include_any", []) if str(item)}
    if include_any and not (set(top_provider_ids) & include_any):
        failures.append(f"top results missing any of {sorted(include_any)}")

    excluded = {str(item) for item in scenario.get("top_must_exclude", []) if str(item)}
    unexpected = sorted(set(top_provider_ids) & excluded)
    if unexpected:
        failures.append(f"top results included excluded providers: {unexpected}")

    return ScenarioResult(
        scenario_id=scenario_id,
        passed=not failures,
        failures=failures,
        top_provider_ids=top_provider_ids,
        result_count=len(all_provider_ids),
    )
