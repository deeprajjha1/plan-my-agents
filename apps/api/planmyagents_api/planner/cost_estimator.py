"""Cost preview for a planned goal.

Pure function over a `GoalPlan` and the existing `agent_rankings` rows. Picks
the cheapest *credible* provider per sub-task capability (credible meaning
the ranking row carries real-adapter data with non-zero sample size; we don't
let mock/synthetic adapter prices drive the estimate).

The output is informational, not a contract: it tells a user "if we routed
each sub-task to the best-priced provider with real benchmark data, we'd
spend roughly $X." It is **not** what the workflow executor will actually
spend — that is decided by the router at execute time, which currently picks
on composite score, not on price alone.

Returned shape::

    {
        "total_estimated_usd": 0.0123,
        "per_sub_task": [
            {
                "capability": "email_verification",
                "provider_id": "hunter",
                "avg_cost_usd": 0.0123,
                "sample_size": 50,
                "source": "real_adapter",
                "basis": "real_adapter",
            },
            {
                "capability": "web_scraping",
                "provider_id": null,
                "avg_cost_usd": null,
                "sample_size": 0,
                "source": null,
                "basis": "no_credible_pricing",
            }
        ],
        "missing_capabilities": ["web_scraping"],
        "credibility_notes": [
            "1 of 2 sub-tasks lack real-adapter pricing; estimate is partial."
        ],
    }
"""

from __future__ import annotations

from typing import Any

from planmyagents_api.planner.goal import GoalPlan

REAL_SOURCES = {"real_adapter", "live", "vendor", "production"}


def estimate_plan_cost(
    *,
    plan: GoalPlan,
    rankings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute a per-sub-task and total cost estimate.

    Returns a JSON-friendly dict; never raises. Sub-tasks without credible
    pricing are recorded with ``avg_cost_usd: None`` and counted in
    ``missing_capabilities`` so the UI can disclose partial coverage instead
    of silently summing zeros.
    """

    by_capability: dict[str, list[dict[str, Any]]] = {}
    for row in rankings:
        capability = str(row.get("capability") or "")
        if capability:
            by_capability.setdefault(capability, []).append(row)

    per_sub_task: list[dict[str, Any]] = []
    total = 0.0
    counted = 0
    missing: list[str] = []

    for sub_task in plan.sub_tasks:
        capability = sub_task.capability
        rows = by_capability.get(capability, [])
        best = _cheapest_credible(rows)
        if best is None:
            per_sub_task.append(
                {
                    "capability": capability,
                    "provider_id": None,
                    "avg_cost_usd": None,
                    "sample_size": 0,
                    "source": None,
                    "basis": "no_credible_pricing",
                }
            )
            missing.append(capability)
            continue

        avg_cost = float(best.get("avg_cost_usd") or 0.0)
        per_sub_task.append(
            {
                "capability": capability,
                "provider_id": str(best.get("provider_id") or ""),
                "avg_cost_usd": avg_cost,
                "sample_size": int(best.get("sample_size") or 0),
                "source": str(best.get("source") or ""),
                "basis": "real_adapter",
            }
        )
        total += avg_cost
        counted += 1

    notes: list[str] = []
    if missing:
        notes.append(
            f"{len(missing)} of {len(plan.sub_tasks)} sub-task(s) lack real-adapter "
            "pricing; estimate is partial."
        )
    if counted == 0 and plan.sub_tasks:
        notes.append(
            "No sub-task has real-adapter pricing data; the total below is $0 by "
            "default, not a meaningful estimate."
        )

    return {
        "total_estimated_usd": round(total, 6),
        "covered_sub_task_count": counted,
        "total_sub_task_count": len(plan.sub_tasks),
        "per_sub_task": per_sub_task,
        "missing_capabilities": sorted(set(missing)),
        "credibility_notes": notes,
    }


def _cheapest_credible(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    credible = [
        row
        for row in rows
        if str(row.get("source") or "").lower() in REAL_SOURCES
        and int(row.get("sample_size") or 0) > 0
    ]
    if not credible:
        return None
    return min(
        credible,
        key=lambda row: (
            float(row.get("avg_cost_usd") or 0.0),
            -float(row.get("composite_score") or 0.0),
            str(row.get("provider_id") or ""),
        ),
    )
