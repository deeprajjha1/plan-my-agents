"""Benchmark report generation."""

from __future__ import annotations

from collections import Counter
from statistics import mean, median
from typing import Any

from planmyagents_api.benchmark.models import BenchmarkRun


def summarize_runs(runs: list[BenchmarkRun] | list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary metrics for benchmark runs."""

    if not runs:
        return {
            "total": 0,
            "success_rate": 0.0,
            "average_quality": 0.0,
            "average_cost_usd": 0.0,
            "p50_latency_ms": 0,
            "difficulty_counts": {},
        }

    normalized = [_to_dict(run) for run in runs]
    total = len(normalized)
    succeeded = sum(1 for run in normalized if run["score"]["succeeded"])
    qualities = [float(run["score"]["quality_score"]) for run in normalized]
    costs = [float(run["response"]["cost_usd"]) for run in normalized]
    latencies = [int(run["response"]["latency_ms"]) for run in normalized]
    difficulty_counts = Counter(str(run["difficulty"]) for run in normalized)

    return {
        "total": total,
        "success_rate": round(succeeded / total, 4),
        "average_quality": round(mean(qualities), 4),
        "average_cost_usd": round(mean(costs), 6),
        "p50_latency_ms": int(median(latencies)),
        "difficulty_counts": dict(sorted(difficulty_counts.items())),
    }


def render_markdown_report(
    runs: list[BenchmarkRun] | list[dict[str, Any]],
    *,
    title: str = "PlanMyAgents Benchmark Report",
) -> str:
    """Render a human-readable markdown report."""

    normalized = [_to_dict(run) for run in runs]
    summary = summarize_runs(normalized)
    provider = normalized[0]["provider_id"] if normalized else "n/a"
    capability = normalized[0]["capability"] if normalized else "n/a"

    lines = [
        f"# {title}",
        "",
        f"- **Provider:** `{provider}`",
        f"- **Capability:** `{capability}`",
        f"- **Cases:** {summary['total']}",
        f"- **Success rate:** {summary['success_rate']:.0%}",
        f"- **Average quality:** {summary['average_quality']:.2f}",
        f"- **Average cost:** ${summary['average_cost_usd']:.4f}",
        f"- **p50 latency:** {summary['p50_latency_ms']}ms",
        "",
        "## Results",
        "",
        "| Case | Difficulty | Quality | Succeeded | Cost | Latency | Reason |",
        "|---|---|---:|---:|---:|---:|---|",
    ]

    for run in normalized:
        score = run["score"]
        response = run["response"]
        lines.append(
            "| {case} | {difficulty} | {quality:.2f} | {succeeded} | ${cost:.4f} | {latency}ms | {reason} |".format(
                case=run["test_case_id"],
                difficulty=run["difficulty"],
                quality=float(score["quality_score"]),
                succeeded="yes" if score["succeeded"] else "no",
                cost=float(response["cost_usd"]),
                latency=int(response["latency_ms"]),
                reason=str(score["reason"]).replace("|", "\\|"),
            )
        )

    lines.append("")
    return "\n".join(lines)


def _to_dict(run: BenchmarkRun | dict[str, Any]) -> dict[str, Any]:
    if isinstance(run, BenchmarkRun):
        return run.to_json()
    return run
