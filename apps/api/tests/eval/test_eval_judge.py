from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.models import ProviderResponse, TestCase
from planmyagents_api.eval.ground_truth import Rubric
from planmyagents_api.eval.judge import EvalJudge, JudgeUnavailableError
from planmyagents_api.llm.escalating_client import (
    EscalationMetadata,
    NoLlmTierAvailableError,
)


class _FakeClient:
    """Captures the messages it was asked to complete + returns a canned body."""

    primary_label = "fake-judge-model"

    def __init__(self, body: str = '{"score": 0.75, "justification": "ok"}', *, raise_no_tier=False):
        self.body = body
        self.raise_no_tier = raise_no_tier
        self.last_messages: list[dict[str, str]] | None = None

    def complete(self, messages):
        self.last_messages = messages
        if self.raise_no_tier:
            raise NoLlmTierAvailableError(EscalationMetadata())
        return self.body


def _rubric() -> Rubric:
    return Rubric(capability="summarization", criteria=["faithful", "concise"])


class EvalJudgeTest(unittest.TestCase):
    def test_returns_clamped_score_and_basis(self) -> None:
        client = _FakeClient('{"score": 1.4, "justification": "great"}')
        judge = EvalJudge(chat_client=client)
        verdict = judge.score(
            rubric=_rubric(), response_output={"text": "x"}, case_inputs={"doc": "y"}
        )
        self.assertEqual(verdict.quality_score, 1.0)  # clamped to [0,1]
        self.assertEqual(verdict.judge_model_id, "fake-judge-model")
        self.assertTrue(verdict.rubric_version.startswith("rubric:summarization:"))

    def test_payload_is_identity_blind(self) -> None:
        client = _FakeClient()
        judge = EvalJudge(chat_client=client)
        judge.score(
            rubric=_rubric(),
            response_output={"text": "the agent output"},
            case_inputs={"doc": "input"},
        )
        # The identity-blind guarantee is about the DATA payload (user
        # message), not the system-prompt wording. Assert the user message
        # carries only rubric + inputs + output, with no provider identity.
        user_msg = next(m["content"] for m in client.last_messages if m["role"] == "user")
        self.assertNotIn("provider_id", user_msg)
        self.assertNotIn("display_name", user_msg)
        self.assertNotIn("vendor", user_msg)
        self.assertIn("rubric", user_msg)
        self.assertIn("agent_output", user_msg)

    def test_judge_unavailable_raises_typed_error(self) -> None:
        client = _FakeClient(raise_no_tier=True)
        judge = EvalJudge(chat_client=client)
        with self.assertRaises(JudgeUnavailableError):
            judge.score(
                rubric=_rubric(), response_output={"text": "x"}, case_inputs={}
            )

    def test_score_test_case_returns_scoreresult_shape(self) -> None:
        client = _FakeClient('{"score": 0.6, "justification": "partial"}')
        judge = EvalJudge(chat_client=client)
        case = TestCase(
            id="c1",
            capability="summarization",
            difficulty="easy",
            inputs={"doc": "input"},
            expected={"rubric_version": "rubric:summarization:v1", "rubric_criteria": ["faithful"]},
        )
        response = ProviderResponse(
            succeeded=True, output={"text": "summary"}, cost_usd=0.0, latency_ms=5
        )
        result = judge.score_test_case(case, response)
        self.assertEqual(result.quality_score, 0.6)
        self.assertEqual(result.reason, "judge")
        self.assertTrue(result.succeeded)

    def test_malformed_judge_body_scores_zero(self) -> None:
        client = _FakeClient("not json at all")
        judge = EvalJudge(chat_client=client)
        verdict = judge.score(rubric=_rubric(), response_output={}, case_inputs={})
        self.assertEqual(verdict.quality_score, 0.0)


if __name__ == "__main__":
    unittest.main()
