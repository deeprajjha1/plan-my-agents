"""Stitch provider responses into user-facing workflow output."""

from __future__ import annotations

from statistics import mean
from typing import Any

from planmyagents_api.workflows.models import WorkflowExecution, WorkflowSubTaskResult


def stitch_workflow_results(results: list[WorkflowSubTaskResult]) -> WorkflowExecution:
    """Create a structured workflow artifact from sub-task results."""

    successful = [result for result in results if result.succeeded and result.response]
    total_cost = sum(float(result.response.cost_usd) for result in results if result.response)
    confidences = [float(result.score.quality_score) for result in successful if result.score]
    average_confidence = round(mean(confidences), 4) if confidences else 0.0
    records = [_record_from_result(result) for result in successful]
    sources = [_source_from_result(result) for result in successful]
    failed = [result for result in results if not result.succeeded]

    if successful and not failed:
        status = "succeeded"
        summary = f"Executed {len(successful)} sub-task(s) and produced {len(records)} structured record(s)."
    elif successful:
        status = "partial"
        summary = f"Executed {len(successful)} sub-task(s), but {len(failed)} sub-task(s) failed or were refused."
    else:
        status = "failed"
        summary = "No sub-tasks produced usable output."

    return WorkflowExecution(
        status=status,
        summary=summary,
        sub_task_results=results,
        records=records,
        sources=sources,
        refusal_reasons=[result.refusal_reason for result in failed if result.refusal_reason],
        total_cost_usd=total_cost,
        average_confidence=average_confidence,
    )


def _record_from_result(result: WorkflowSubTaskResult) -> dict[str, Any]:
    output = result.response.output if result.response and result.response.output else {}
    return {
        "capability": result.capability,
        "provider_id": result.provider_id,
        "confidence": result.score.quality_score if result.score else 0.0,
        "data": output,
    }


def _source_from_result(result: WorkflowSubTaskResult) -> dict[str, Any]:
    output = result.response.output if result.response and result.response.output else {}
    return {
        "capability": result.capability,
        "provider_id": result.provider_id,
        "source_url": _source_url(output),
        "latency_ms": result.response.latency_ms if result.response else None,
        "cost_usd": result.response.cost_usd if result.response else 0.0,
    }


def _source_url(output: dict[str, Any]) -> str | None:
    for key in ["source_url", "linkedin_url", "url"]:
        if output.get(key):
            return str(output[key])
    return None
