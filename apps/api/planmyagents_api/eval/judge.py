"""LLM-as-judge scoring for soft / open-ended outputs.

Reliability + bias controls (R4):

* **Identity-blind.** The material handed to the judge model contains only
  the rubric, the case inputs, and the provider's OUTPUT — never the
  provider id, display name, or vendor. Provider identity cannot bias the
  score.
* **Explicit rubric.** Every score is against stated criteria, not an
  unstated preference.
* **Auditable basis.** The judge model id + rubric version are recorded on
  the result.
* **Fail-closed.** If the judge LLM is unavailable, the cell is marked
  non-eval-able for that run with a ``judge_unavailable`` reason — never a
  fabricated score.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from planmyagents_api.benchmark.models import FieldScore, ProviderResponse, ScoreResult, TestCase
from planmyagents_api.eval.ground_truth import Rubric
from planmyagents_api.llm.escalating_client import (
    EscalatingChatClient,
    NoLlmTierAvailableError,
)


class JudgeUnavailableError(RuntimeError):
    """Raised when no LLM tier can produce a judge verdict for this run."""


@dataclass(frozen=True)
class JudgeVerdict:
    quality_score: float
    justification: str
    judge_model_id: str
    rubric_version: str


_SYSTEM_PROMPT = """
You are an impartial evaluation judge for an AI-agent benchmark.

You are given:
- a rubric: the explicit criteria a response must satisfy,
- the case inputs the agent was given,
- the agent's OUTPUT.

You are NOT told which vendor or product produced the output. Judge ONLY the
output against the rubric. Do not reward or penalise based on style, brand, or
guesses about the provider.

Return JSON only, no markdown:
{
  "score": 0.0-1.0,
  "justification": "<one or two sentences tied to the rubric criteria>"
}

Scoring guidance:
- 1.0 = fully satisfies every rubric criterion.
- 0.5 = satisfies some criteria, fails others.
- 0.0 = satisfies none / wrong / empty.
""".strip()


@dataclass
class EvalJudge:
    chat_client: EscalatingChatClient

    def score(
        self, *, rubric: Rubric, response_output: dict, case_inputs: dict
    ) -> JudgeVerdict:
        """Score a single response against ``rubric``. Identity-blind."""

        messages = self._build_messages(
            rubric=rubric, response_output=response_output, case_inputs=case_inputs
        )
        try:
            raw = self.chat_client.complete(messages)
        except NoLlmTierAvailableError as exc:
            raise JudgeUnavailableError(
                f"judge_unavailable: no LLM tier could score the rubric: {exc}"
            ) from exc

        score, justification = _parse_verdict(raw)
        model_id = getattr(self.chat_client, "primary_label", "") or "unknown-judge"
        return JudgeVerdict(
            quality_score=score,
            justification=justification,
            judge_model_id=model_id,
            rubric_version=rubric.resolved_version(),
        )

    def score_test_case(
        self, test_case: TestCase, response: ProviderResponse
    ) -> ScoreResult:
        """Adapter for ``benchmark.scoring.score_response``'s judge hook.

        Builds an ad-hoc rubric from the case's ``expected.rubric_criteria``
        (or a generic criterion) and returns a ``ScoreResult`` shaped exactly
        like the exact-match path so downstream aggregation is identical.
        """

        criteria = test_case.expected.get("rubric_criteria")
        if not isinstance(criteria, list) or not criteria:
            criteria = ["The output correctly and completely satisfies the request."]
        rubric = Rubric(
            capability=test_case.capability,
            criteria=[str(c) for c in criteria],
            version=str(test_case.expected.get("rubric_version") or ""),
        )
        verdict = self.score(
            rubric=rubric,
            response_output=response.output or {},
            case_inputs=test_case.inputs,
        )
        return ScoreResult(
            quality_score=verdict.quality_score,
            succeeded=verdict.quality_score > 0,
            field_scores=[
                FieldScore(
                    field="rubric",
                    weight=1.0,
                    score=verdict.quality_score,
                    reason=verdict.justification,
                )
            ],
            reason="judge",
        )

    def _build_messages(
        self, *, rubric: Rubric, response_output: dict, case_inputs: dict
    ) -> list[dict[str, str]]:
        # Identity-blind payload: rubric + inputs + output ONLY. No provider
        # id / display name / vendor is ever included.
        user_payload = {
            "rubric": {
                "criteria": list(rubric.criteria),
                "scale": [rubric.scale_min, rubric.scale_max],
            },
            "case_inputs": case_inputs,
            "agent_output": response_output,
        }
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, sort_keys=True)},
        ]


def _parse_verdict(raw: str) -> tuple[float, str]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    score = 0.0
    justification = ""
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            try:
                score = float(parsed.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            justification = str(parsed.get("justification") or "")
    # Clamp to [0, 1] (R4.1).
    score = max(0.0, min(1.0, score))
    return score, justification
