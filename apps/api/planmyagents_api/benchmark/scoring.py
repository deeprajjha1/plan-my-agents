"""Scoring rules for benchmark provider responses."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from planmyagents_api.benchmark.models import FieldScore, ProviderResponse, ScoreResult, TestCase

UNKNOWN_MARKERS = {"unknown", "not_found", "not found", "no_match", "no match", "none", "null"}


def score_response(test_case: TestCase, response: ProviderResponse) -> ScoreResult:
    """Score a provider response against a benchmark test case."""

    if not response.succeeded:
        return ScoreResult(
            quality_score=0.0,
            succeeded=False,
            field_scores=[],
            reason=response.error or "provider_response_failed",
        )

    actual = response.output or {}
    expected = test_case.expected

    if expected.get("must_indicate_unknown") is True:
        return _score_unknown_expected(test_case, actual)

    field_scores: list[FieldScore] = []
    total_weight = 0.0
    weighted_score = 0.0

    for field_name, spec in expected.items():
        if not isinstance(spec, Mapping) or field_name in {"notes"}:
            continue

        weight = float(spec.get("weight", 1.0))
        score, reason = _score_field(actual, field_name, spec)
        field_scores.append(FieldScore(field=field_name, weight=weight, score=score, reason=reason))
        total_weight += weight
        weighted_score += weight * score

    if not field_scores:
        return ScoreResult(
            quality_score=0.0,
            succeeded=False,
            field_scores=[],
            reason="no_scorable_expected_fields",
        )

    quality = round(weighted_score / total_weight, 4) if total_weight else 0.0
    return ScoreResult(
        quality_score=quality,
        succeeded=quality > 0,
        field_scores=field_scores,
        reason="scored",
    )


def _score_unknown_expected(test_case: TestCase, actual: dict[str, Any]) -> ScoreResult:
    serialized = str(actual).lower()
    forbidden = [str(item).lower() for item in test_case.expected.get("forbidden_outputs", [])]
    found_forbidden = [item for item in forbidden if item and item in serialized]

    if found_forbidden:
        return ScoreResult(
            quality_score=0.0,
            succeeded=False,
            field_scores=[
                FieldScore(
                    field="must_indicate_unknown",
                    weight=1.0,
                    score=0.0,
                    reason=f"hallucinated_forbidden_output:{found_forbidden[0]}",
                )
            ],
            reason="hallucinated_when_unknown_expected",
        )

    if _looks_unknown(actual):
        return ScoreResult(
            quality_score=1.0,
            succeeded=True,
            field_scores=[
                FieldScore(
                    field="must_indicate_unknown",
                    weight=1.0,
                    score=1.0,
                    reason="correctly_indicated_unknown",
                )
            ],
            reason="unknown_correct",
        )

    return ScoreResult(
        quality_score=0.0,
        succeeded=False,
        field_scores=[
            FieldScore(
                field="must_indicate_unknown",
                weight=1.0,
                score=0.0,
                reason="returned_non_empty_result_when_unknown_expected",
            )
        ],
        reason="unknown_expected_but_non_empty_result",
    )


def _score_field(
    actual: dict[str, Any], field_name: str, spec: Mapping[str, Any]
) -> tuple[float, str]:
    actual_value = _get_path(actual, field_name)

    if "accept" in spec:
        accepted = spec["accept"]
        if not isinstance(accepted, list):
            accepted = [accepted]
        if _matches_accepted(actual_value, accepted):
            return 1.0, "accepted_value_match"
        return 0.0, f"expected_one_of:{accepted};actual:{actual_value!r}"

    if "equals" in spec:
        expected_value = spec["equals"]
        if _normalize(actual_value) == _normalize(expected_value):
            return 1.0, "equals_match"
        return 0.0, f"expected:{expected_value!r};actual:{actual_value!r}"

    if "format_check" in spec:
        pattern = str(spec["format_check"])
        if actual_value is not None and re.match(pattern, str(actual_value), re.IGNORECASE):
            return 1.0, "regex_match"
        return 0.0, f"regex_no_match:{pattern};actual:{actual_value!r}"

    if "contains" in spec:
        needle = str(spec["contains"]).lower()
        haystack = str(actual_value or "").lower()
        if needle in haystack:
            return 1.0, "contains_match"
        return 0.0, f"missing_substring:{needle}"

    if "min_score" in spec:
        try:
            if float(actual_value) >= float(spec["min_score"]):
                return 1.0, "min_score_pass"
            return 0.0, f"below_min_score:{actual_value}"
        except (TypeError, ValueError):
            return 0.0, f"not_numeric:{actual_value!r}"

    return 0.0, "unsupported_expected_spec"


def _matches_accepted(actual_value: Any, accepted: list[Any]) -> bool:
    actual_norm = _normalize(actual_value)
    for item in accepted:
        if actual_norm == _normalize(item):
            return True
    return False


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    if text.startswith("https://"):
        text = text.removeprefix("https://")
    if text.startswith("http://"):
        text = text.removeprefix("http://")
    return text.rstrip("/")


def _get_path(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _looks_unknown(actual: dict[str, Any]) -> bool:
    if not actual:
        return True
    status = str(actual.get("status", "")).strip().lower()
    if status in UNKNOWN_MARKERS:
        return True
    if actual.get("found") is False:
        return True
    if actual.get("result") is None and set(actual.keys()).issubset(
        {"result", "status", "confidence"}
    ):
        return True
    return False
