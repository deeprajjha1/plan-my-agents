"""Core benchmark domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class TestCase:
    """A single benchmark case loaded from YAML."""

    id: str
    capability: str
    difficulty: str
    inputs: JsonDict
    expected: JsonDict
    notes: str = ""
    created_at: str | None = None
    created_by: str | None = None


@dataclass(frozen=True)
class ProviderRequest:
    """Normalized request passed to a provider adapter."""

    capability: str
    inputs: JsonDict
    idempotency_key: str


@dataclass(frozen=True)
class ProviderResponse:
    """Normalized response returned by every provider adapter."""

    succeeded: bool
    output: JsonDict | None
    cost_usd: float
    latency_ms: int
    error: str | None = None
    raw_response: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class FieldScore:
    """Score for one expected field."""

    field: str
    weight: float
    score: float
    reason: str


@dataclass(frozen=True)
class ScoreResult:
    """Final score for one provider response against one test case."""

    quality_score: float
    succeeded: bool
    field_scores: list[FieldScore]
    reason: str


@dataclass(frozen=True)
class BenchmarkRun:
    """A scored provider execution against a test case."""

    test_case_id: str
    provider_id: str
    capability: str
    difficulty: str
    response: ProviderResponse
    score: ScoreResult
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json(self) -> JsonDict:
        """Return a JSON-serializable representation."""

        return {
            "test_case_id": self.test_case_id,
            "provider_id": self.provider_id,
            "capability": self.capability,
            "difficulty": self.difficulty,
            "response": {
                "succeeded": self.response.succeeded,
                "output": self.response.output,
                "cost_usd": self.response.cost_usd,
                "latency_ms": self.response.latency_ms,
                "error": self.response.error,
                "raw_response": self.response.raw_response,
            },
            "score": {
                "quality_score": self.score.quality_score,
                "succeeded": self.score.succeeded,
                "reason": self.score.reason,
                "field_scores": [
                    {
                        "field": item.field,
                        "weight": item.weight,
                        "score": item.score,
                        "reason": item.reason,
                    }
                    for item in self.score.field_scores
                ],
            },
            "created_at": self.created_at,
        }
