"""Tests for the LLM-driven candidate judge.

These tests pin the contract that the judge:
* Drops candidates the LLM marks irrelevant
* Drops candidates with confidence below the floor
* Fail-closes when the LLM omits a candidate from its verdicts
* Drops verdicts for ids the LLM hallucinated
* Refuses (raises) when the first batch fails on every tier
* Continues after a non-first-batch tier failure (fail-closed for the
  remainder)
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass, field

from planmyagents_api.discovery.candidate_judge import (
    DEFAULT_MIN_CONFIDENCE,
    CandidateJudge,
)
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.llm.escalating_client import (
    EscalatingChatClient,
    NoLlmTierAvailableError,
)


def _make_candidate(
    candidate_id: str,
    *,
    display_name: str | None = None,
    capabilities: list[str] | None = None,
    description: str = "",
    source: str = "official_mcp_registry",
) -> DiscoveryCandidate:
    capabilities = capabilities or ["fare_comparison"]
    return DiscoveryCandidate(
        id=candidate_id,
        display_name=display_name or candidate_id,
        vendor=candidate_id.split("/", 1)[0] if "/" in candidate_id else candidate_id,
        vendor_url=f"https://example.com/{candidate_id}",
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id=c, confidence=0.5, notes=description) for c in capabilities],
        source=source,
    )


@dataclass
class ScriptedClient:
    """Returns a queue of pre-baked completions so tests can simulate
    primary tier producing specific JSON, then optionally raising."""

    responses: list[str] = field(default_factory=list)
    raises_after: int | None = None
    calls: list[list[dict[str, str]]] = field(default_factory=list)
    model: str = "scripted-model"

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if self.raises_after is not None and len(self.calls) > self.raises_after:
            raise RuntimeError(f"scripted_failure_after_{self.raises_after}_calls")
        if not self.responses:
            raise RuntimeError("no_more_scripted_responses")
        return self.responses.pop(0)


def _judge_payload(verdicts: list[dict]) -> str:
    return json.dumps({"verdicts": verdicts})


class CandidateJudgeAcceptanceTests(unittest.TestCase):
    def test_accepts_relevant_high_confidence_candidates(self) -> None:
        cands = [
            _make_candidate("vendor/onfido-kyc", capabilities=["kyc_aml_check"]),
            _make_candidate("vendor/junk-school", capabilities=["fare_comparison"]),
        ]
        primary = ScriptedClient(
            responses=[
                _judge_payload(
                    [
                        {
                            "candidate_id": "vendor/onfido-kyc",
                            "relevant": True,
                            "confidence": 0.9,
                            "capabilities_served": ["kyc_aml_check"],
                            "reason": "Onfido provides KYC verification.",
                        },
                        {
                            "candidate_id": "vendor/junk-school",
                            "relevant": False,
                            "confidence": 0.95,
                            "capabilities_served": [],
                            "reason": "School project, unrelated to the goal.",
                        },
                    ]
                )
            ]
        )
        client = EscalatingChatClient(primary=primary, primary_label="qwen")
        judge = CandidateJudge(chat_client=client)

        result = judge.judge(
            goal="Verify a buyer's identity for a UAE transaction",
            required_capabilities=["kyc_aml_check"],
            candidates=cands,
        )

        self.assertEqual([c.id for c in result.accepted], ["vendor/onfido-kyc"])
        self.assertEqual([c.id for c in result.rejected], ["vendor/junk-school"])
        self.assertEqual(result.metadata.tier_used, "primary")
        self.assertEqual(result.batch_count, 1)

    def test_drops_candidates_below_confidence_floor(self) -> None:
        cands = [
            _make_candidate("vendor/maybe-kyc", capabilities=["kyc_aml_check"]),
        ]
        primary = ScriptedClient(
            responses=[
                _judge_payload(
                    [
                        {
                            "candidate_id": "vendor/maybe-kyc",
                            "relevant": True,
                            "confidence": 0.3,
                            "capabilities_served": ["kyc_aml_check"],
                            "reason": "Description is ambiguous, might be KYC.",
                        }
                    ]
                )
            ]
        )
        client = EscalatingChatClient(primary=primary)
        judge = CandidateJudge(chat_client=client, min_confidence=DEFAULT_MIN_CONFIDENCE)

        result = judge.judge(
            goal="Verify identity",
            required_capabilities=["kyc_aml_check"],
            candidates=cands,
        )

        self.assertEqual(result.accepted, [])
        self.assertEqual([c.id for c in result.rejected], ["vendor/maybe-kyc"])

    def test_fail_closes_when_llm_omits_candidate(self) -> None:
        cands = [
            _make_candidate("vendor/known", capabilities=["kyc_aml_check"]),
            _make_candidate("vendor/forgotten", capabilities=["kyc_aml_check"]),
        ]
        primary = ScriptedClient(
            responses=[
                _judge_payload(
                    [
                        {
                            "candidate_id": "vendor/known",
                            "relevant": True,
                            "confidence": 0.9,
                            "capabilities_served": ["kyc_aml_check"],
                            "reason": "Clearly KYC.",
                        }
                    ]
                )
            ]
        )
        client = EscalatingChatClient(primary=primary)
        judge = CandidateJudge(chat_client=client)

        result = judge.judge(
            goal="Verify identity",
            required_capabilities=["kyc_aml_check"],
            candidates=cands,
        )

        self.assertEqual([c.id for c in result.accepted], ["vendor/known"])
        self.assertEqual([c.id for c in result.rejected], ["vendor/forgotten"])
        self.assertIn(
            "did not return a verdict",
            result.verdicts["vendor/forgotten"].reason,
        )

    def test_drops_hallucinated_candidate_ids(self) -> None:
        cands = [_make_candidate("vendor/real")]
        primary = ScriptedClient(
            responses=[
                _judge_payload(
                    [
                        {
                            "candidate_id": "vendor/real",
                            "relevant": True,
                            "confidence": 0.9,
                            "capabilities_served": ["fare_comparison"],
                            "reason": "ok",
                        },
                        {
                            "candidate_id": "vendor/imaginary",
                            "relevant": True,
                            "confidence": 0.99,
                            "capabilities_served": ["fare_comparison"],
                            "reason": "model invented this id",
                        },
                    ]
                )
            ]
        )
        client = EscalatingChatClient(primary=primary)
        judge = CandidateJudge(chat_client=client)

        result = judge.judge(
            goal="goal",
            required_capabilities=["fare_comparison"],
            candidates=cands,
        )

        self.assertEqual([c.id for c in result.accepted], ["vendor/real"])
        self.assertNotIn("vendor/imaginary", result.verdicts)


class CandidateJudgeFailureTests(unittest.TestCase):
    def test_first_batch_failure_raises_no_llm_tier_available(self) -> None:
        cands = [_make_candidate("vendor/x")]
        primary = ScriptedClient(raises_after=0)  # raises on first call
        client = EscalatingChatClient(primary=primary)
        judge = CandidateJudge(chat_client=client)

        with self.assertRaises(NoLlmTierAvailableError):
            judge.judge(
                goal="goal",
                required_capabilities=["fare_comparison"],
                candidates=cands,
            )

    def test_empty_candidate_list_returns_empty_result_without_llm_call(self) -> None:
        primary = ScriptedClient()
        client = EscalatingChatClient(primary=primary)
        judge = CandidateJudge(chat_client=client)

        result = judge.judge(
            goal="goal",
            required_capabilities=["x"],
            candidates=[],
        )

        self.assertEqual(result.accepted, [])
        self.assertEqual(result.rejected, [])
        self.assertEqual(result.batch_count, 0)
        self.assertEqual(len(primary.calls), 0)

    def test_batched_run_continues_after_non_first_batch_failure(self) -> None:
        cands = [_make_candidate(f"vendor/c{i}") for i in range(8)]
        first_payload = _judge_payload(
            [
                {
                    "candidate_id": cand.id,
                    "relevant": True,
                    "confidence": 0.9,
                    "capabilities_served": ["fare_comparison"],
                    "reason": "ok",
                }
                for cand in cands[:4]
            ]
        )
        primary = ScriptedClient(responses=[first_payload], raises_after=1)
        client = EscalatingChatClient(primary=primary)
        judge = CandidateJudge(chat_client=client, batch_size=4)

        result = judge.judge(
            goal="goal",
            required_capabilities=["fare_comparison"],
            candidates=cands,
        )

        # First batch accepted
        self.assertEqual(
            [c.id for c in result.accepted],
            [cand.id for cand in cands[:4]],
        )
        # Second batch fail-closed (rejected with "no verdict")
        self.assertEqual(
            sorted(c.id for c in result.rejected),
            sorted(cand.id for cand in cands[4:]),
        )
        self.assertEqual(result.batch_count, 2)

    def test_quality_check_escalates_on_unparseable_payload(self) -> None:
        cands = [_make_candidate("vendor/c")]
        primary = ScriptedClient(responses=["not json at all"])
        fallback = ScriptedClient(
            responses=[
                _judge_payload(
                    [
                        {
                            "candidate_id": "vendor/c",
                            "relevant": True,
                            "confidence": 0.9,
                            "capabilities_served": ["fare_comparison"],
                            "reason": "fallback handled it",
                        }
                    ]
                )
            ]
        )
        client = EscalatingChatClient(primary=primary, fallback=fallback)
        judge = CandidateJudge(chat_client=client)

        result = judge.judge(
            goal="goal",
            required_capabilities=["fare_comparison"],
            candidates=cands,
        )

        self.assertEqual([c.id for c in result.accepted], ["vendor/c"])
        self.assertEqual(result.metadata.tier_used, "fallback")
        self.assertTrue(result.metadata.quality_check_triggered)


class CandidateJudgePromptShapeTests(unittest.TestCase):
    """Sprint 2 (S2-NEW-3): the per-candidate prompt view must include
    ``verification_status`` and a whitelisted ``provenance`` object so
    the judge can apply the documented confidence cap and use
    upstream signals as tiebreakers.
    """

    def test_verification_status_and_provenance_passed_to_judge(self) -> None:
        cand = DiscoveryCandidate(
            id="provenance-test",
            display_name="Provenance Test",
            vendor="Test",
            vendor_url="https://example.com",
            provider_type="mcp_server",
            capabilities=[CandidateCapability(id="email_verification", confidence=0.7)],
            source="smithery",
            verification_status="community_listed",
            metadata={
                "smithery_use_count": 1234,
                "smithery_is_deployed": True,
                "marketplace_github_stars": 42,
                # Should NOT appear in provenance view (not whitelisted).
                "smithery_created_at": "2026-01-01",
                "moltbook_avatar_url": "https://x/y",
            },
        )

        client = ScriptedClient(
            responses=[
                _judge_payload(
                    [
                        {
                            "candidate_id": "provenance-test",
                            "relevant": True,
                            "confidence": 0.9,
                            "capabilities_served": ["email_verification"],
                            "reason": "ok",
                        }
                    ]
                )
            ]
        )
        judge = CandidateJudge(
            chat_client=EscalatingChatClient(primary=client, primary_label="t"),
            min_confidence=DEFAULT_MIN_CONFIDENCE,
        )
        judge.judge(
            goal="Verify these emails",
            required_capabilities=["email_verification"],
            candidates=[cand],
        )

        # The payload sent to the LLM is the user message of the
        # first scripted call. Decode it and inspect the candidate
        # view shape directly.
        user_payload = json.loads(client.calls[0][1]["content"])
        cand_view = user_payload["candidates"][0]
        self.assertEqual(cand_view["verification_status"], "community_listed")
        self.assertEqual(
            cand_view["provenance"],
            {
                "smithery_use_count": 1234,
                "smithery_is_deployed": True,
                "marketplace_github_stars": 42,
            },
        )

    def test_provenance_is_empty_object_when_metadata_empty(self) -> None:
        cand = DiscoveryCandidate(
            id="bare",
            display_name="Bare",
            vendor="Bare",
            vendor_url="https://example.com",
            provider_type="mcp_server",
            capabilities=[CandidateCapability(id="email_verification", confidence=0.5)],
            source="test",
            # No metadata supplied.
        )
        client = ScriptedClient(
            responses=[
                _judge_payload(
                    [
                        {
                            "candidate_id": "bare",
                            "relevant": True,
                            "confidence": 0.9,
                            "capabilities_served": ["email_verification"],
                            "reason": "ok",
                        }
                    ]
                )
            ]
        )
        judge = CandidateJudge(
            chat_client=EscalatingChatClient(primary=client, primary_label="t"),
            min_confidence=DEFAULT_MIN_CONFIDENCE,
        )
        judge.judge(
            goal="Verify emails",
            required_capabilities=["email_verification"],
            candidates=[cand],
        )
        user_payload = json.loads(client.calls[0][1]["content"])
        self.assertEqual(user_payload["candidates"][0]["provenance"], {})


if __name__ == "__main__":
    unittest.main()
