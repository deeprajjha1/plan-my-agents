"""Standards-aligned tool-use decomposition scoring (additive).

The agent-eval field has converged on a four-step decomposition for tool/agent
calls: decide-to-call, select-operation, build-arguments, integrate-result.
This module produces a per-step breakdown and retains a trajectory so a
failure can be located at its step, WITHOUT changing the composite 0.0-1.0
quality scale the credibility classifier consumes.

It is additive metadata: ``score_response`` still returns its existing
``ScoreResult``; the decomposition is computed alongside and attached to the
run's provenance/metadata. For exact-match cases the composite quality is
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from planmyagents_api.benchmark.models import ProviderResponse, ScoreResult, TestCase

# The four canonical steps, in order.
DECIDE_TO_CALL = "decide_to_call"
SELECT_OPERATION = "select_operation"
BUILD_ARGUMENTS = "build_arguments"
INTEGRATE_RESULT = "integrate_result"

SCORING_METHOD_EXACT = "exact_match"
SCORING_METHOD_DECOMPOSITION = "tool_use_decomposition"
SCORING_METHOD_RUBRIC = "rubric_judge"


@dataclass(frozen=True)
class StepScore:
    step: str
    score: float            # 0.0-1.0
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {"step": self.step, "score": round(self.score, 4), "reason": self.reason}


@dataclass(frozen=True)
class DecompositionResult:
    steps: list[StepScore]
    scoring_method: str
    trajectory: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "scoring_method": self.scoring_method,
            "steps": [step.to_json() for step in self.steps],
            "trajectory": list(self.trajectory),
        }


def decompose(
    *,
    test_case: TestCase,
    response: ProviderResponse,
    score: ScoreResult,
    scoring_method: str = SCORING_METHOD_EXACT,
) -> DecompositionResult:
    """Derive a per-step breakdown from a single scored response.

    This is a best-effort reconstruction from the response envelope (we don't
    have the provider's internal reasoning), but it is diagnostic: it cleanly
    separates "the call never happened / errored" from "the call happened but
    the result was wrong".

    Step semantics:
    * decide_to_call    — did the provider attempt the call at all?
    * select_operation  — did it resolve a tool/operation (not a transport
                          or routing refusal)?
    * build_arguments   — did the request reach the provider without a
                          client-side build/validation refusal?
    * integrate_result  — did the returned output match ground truth (the
                          existing score quality)?
    """

    output = response.output or {}
    raw = response.raw_response or {}
    refused = bool(raw.get("refused"))
    gated = bool(raw.get("execution_gated"))
    transport_error = isinstance(response.error, str) and response.error.startswith(
        ("transport:", "openapi spec fetch failed")
    )

    # decide_to_call: a gated/disabled response means we never decided to
    # call the provider for real.
    decided = 0.0 if gated else 1.0

    # select_operation: a structured refusal that couldn't resolve a tool /
    # operation fails this step.
    selected = 0.0 if (refused or gated) else 1.0
    select_reason = (
        "refused/gated before operation resolution"
        if (refused or gated)
        else "operation resolved"
    )

    # build_arguments: transport-level failures indicate the request was built
    # and sent (so building succeeded) but the wire failed → arguments built,
    # integrate fails. A build refusal lives in `error` with "build failed".
    build_failed = isinstance(response.error, str) and "build failed" in response.error
    built = 0.0 if (build_failed or refused or gated) else 1.0
    build_reason = "request built" if built else "request not built (refused/gated/build error)"

    # integrate_result: the actual quality of the returned output vs ground
    # truth. For a transport error there is no result to integrate.
    integrate = 0.0 if (transport_error or not response.succeeded) else score.quality_score
    integrate_reason = (
        "transport/availability failure"
        if transport_error
        else (score.reason or "scored")
    )

    steps = [
        StepScore(DECIDE_TO_CALL, decided, "gated" if gated else "attempted"),
        StepScore(SELECT_OPERATION, selected, select_reason),
        StepScore(BUILD_ARGUMENTS, built, build_reason),
        StepScore(INTEGRATE_RESULT, float(integrate), integrate_reason),
    ]

    trajectory = [
        {"step": SELECT_OPERATION, "tool_name": output.get("tool_name") or output.get("operation_id")},
        {"step": INTEGRATE_RESULT, "succeeded": response.succeeded, "error": response.error},
    ]

    return DecompositionResult(
        steps=steps,
        scoring_method=scoring_method,
        trajectory=trajectory,
    )
