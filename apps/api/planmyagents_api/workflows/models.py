"""Workflow execution domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from planmyagents_api.benchmark.models import ProviderResponse, ScoreResult

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class WorkflowSubTaskResult:
    """Execution result for one planned sub-task."""

    ordinal: int
    capability: str
    description: str
    provider_id: str | None
    route_decision: JsonDict
    response: ProviderResponse | None = None
    score: ScoreResult | None = None
    refusal_reason: str | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 1:
            raise ValueError("workflow sub-task ordinal must be positive")
        if not self.capability.strip():
            raise ValueError("workflow sub-task capability is required")
        if not self.description.strip():
            raise ValueError("workflow sub-task description is required")

    @property
    def succeeded(self) -> bool:
        return bool(
            self.response and self.response.succeeded and self.score and self.score.succeeded
        )

    def to_json(self) -> JsonDict:
        return {
            "ordinal": self.ordinal,
            "capability": self.capability,
            "description": self.description,
            "provider_id": self.provider_id,
            "route_decision": self.route_decision,
            "succeeded": self.succeeded,
            "refusal_reason": self.refusal_reason,
            "response": _response_json(self.response),
            "score": _score_json(self.score),
        }


@dataclass(frozen=True)
class WorkflowExecution:
    """Structured output for an end-to-end workflow execution."""

    status: str
    summary: str
    sub_task_results: list[WorkflowSubTaskResult] = field(default_factory=list)
    records: list[JsonDict] = field(default_factory=list)
    sources: list[JsonDict] = field(default_factory=list)
    refusal_reasons: list[str] = field(default_factory=list)
    total_cost_usd: float = 0.0
    average_confidence: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "partial", "unsupported", "failed"}:
            raise ValueError(f"unsupported workflow execution status: {self.status}")
        if not self.summary.strip():
            raise ValueError("workflow execution summary is required")
        if self.total_cost_usd < 0:
            raise ValueError("workflow execution cost cannot be negative")
        if not 0.0 <= self.average_confidence <= 1.0:
            raise ValueError("workflow execution confidence must be between 0 and 1")

    @property
    def executed(self) -> bool:
        return self.status in {"succeeded", "partial"}

    def to_json(self) -> JsonDict:
        return {
            "status": self.status,
            "summary": self.summary,
            "executed": self.executed,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "average_confidence": round(self.average_confidence, 4),
            "records": self.records,
            "sources": self.sources,
            "refusal_reasons": self.refusal_reasons,
            "sub_task_results": [result.to_json() for result in self.sub_task_results],
            "created_at": self.created_at,
        }


def _response_json(response: ProviderResponse | None) -> JsonDict | None:
    if response is None:
        return None
    return {
        "succeeded": response.succeeded,
        "output": response.output,
        "cost_usd": response.cost_usd,
        "latency_ms": response.latency_ms,
        "error": response.error,
        "raw_response": response.raw_response,
    }


def _score_json(score: ScoreResult | None) -> JsonDict | None:
    if score is None:
        return None
    return {
        "quality_score": score.quality_score,
        "succeeded": score.succeeded,
        "reason": score.reason,
        "field_scores": [
            {
                "field": item.field,
                "weight": item.weight,
                "score": item.score,
                "reason": item.reason,
            }
            for item in score.field_scores
        ],
    }
