"""Product-level quality contract tests for /goal recommendation filtering.

These tests focus on the handoff between live discovery results and the
retrieval-time CandidateJudge. The core invariant is that candidates found
inline during a request must not bypass judgment and become recommendations.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.candidate_judge import _short_description  # noqa: E402
from planmyagents_api.discovery.models import (  # noqa: E402
    CandidateCapability,
    DiscoveryCandidate,
)
from planmyagents_api.llm.escalating_client import (  # noqa: E402
    EscalationMetadata,
    NoLlmTierAvailableError,
)
from planmyagents_api.web import app as app_module  # noqa: E402


def _candidate(candidate_id: str, *, capability: str = "general_research") -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=candidate_id,
        display_name=candidate_id.replace("-", " ").title(),
        vendor=candidate_id.split("-", 1)[0],
        vendor_url=f"https://example.com/{candidate_id}",
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id=capability, confidence=0.7)],
        verification_status="registered_in_directory",
        source="test",
    )


class _FakeStore:
    def __init__(self, candidates: list[DiscoveryCandidate]) -> None:
        self._candidates = candidates

    def load(self) -> list[DiscoveryCandidate]:
        return list(self._candidates)


class _FakeJudgeResult:
    def __init__(self, *, accepted: list[DiscoveryCandidate], rejected: list[DiscoveryCandidate]) -> None:
        self.accepted = accepted
        self.rejected = rejected

    def to_summary(self) -> dict[str, object]:
        return {
            "accepted": len(self.accepted),
            "rejected": len(self.rejected),
            "min_confidence": 0.5,
            "batches": 1,
        }


class _AcceptOnlyStoredGoodJudge:
    seen_candidate_ids: list[str] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def judge(
        self,
        *,
        goal: str,
        required_capabilities: list[str],
        candidates: list[DiscoveryCandidate],
        acceptance_criteria_by_capability: dict[str, str] | None = None,
    ) -> _FakeJudgeResult:
        del goal, required_capabilities, acceptance_criteria_by_capability
        type(self).seen_candidate_ids = [candidate.id for candidate in candidates]
        accepted = [candidate for candidate in candidates if candidate.id == "stored-good"]
        rejected = [candidate for candidate in candidates if candidate.id != "stored-good"]
        return _FakeJudgeResult(accepted=accepted, rejected=rejected)


class GoalRecommendationQualityContractTest(unittest.TestCase):
    def setUp(self) -> None:
        _AcceptOnlyStoredGoodJudge.seen_candidate_ids = []

    def test_judge_disabled_blocks_recommendations(self) -> None:
        candidate = _candidate("gift-helper")

        with patch.object(app_module, "is_judge_enabled", return_value=False):
            filtered, metadata = app_module._apply_candidate_judge(
                goal="find gifts for a toddler",
                required_capabilities=["general_research"],
                agentic_results=[candidate.to_public_summary()],
                store_url="memory://test",
            )

        self.assertEqual(filtered, [])
        self.assertEqual(metadata["status"], "disabled")
        self.assertEqual(metadata["accepted"], 0)
        self.assertEqual(metadata["rejected"], 1)
        self.assertEqual(metadata["untracked_passthrough"], 0)

    def test_store_unavailable_blocks_recommendations(self) -> None:
        candidate = _candidate("gift-helper")

        with (
            patch.object(app_module, "is_judge_enabled", return_value=True),
            patch(
                "planmyagents_api.discovery.store.discovery_store_for_path",
                side_effect=RuntimeError("store down"),
            ),
        ):
            filtered, metadata = app_module._apply_candidate_judge(
                goal="find gifts for a toddler",
                required_capabilities=["general_research"],
                agentic_results=[candidate.to_public_summary()],
                store_url="memory://test",
            )

        self.assertEqual(filtered, [])
        self.assertEqual(metadata["status"], "store_unavailable")
        self.assertEqual(metadata["accepted"], 0)
        self.assertEqual(metadata["rejected"], 1)
        self.assertEqual(metadata["untracked_passthrough"], 0)

    def test_llm_unavailable_blocks_recommendations(self) -> None:
        candidate = _candidate("gift-helper")
        error = NoLlmTierAvailableError(
            EscalationMetadata(primary_label="qwen", fallback_label="openai"),
            "all tiers down",
        )

        with (
            patch(
                "planmyagents_api.discovery.store.discovery_store_for_path",
                return_value=_FakeStore([candidate]),
            ),
            patch.object(app_module, "is_judge_enabled", return_value=True),
            patch.object(
                app_module,
                "build_default_escalating_client",
                side_effect=error,
            ),
        ):
            filtered, metadata = app_module._apply_candidate_judge(
                goal="find gifts for a toddler",
                required_capabilities=["general_research"],
                agentic_results=[candidate.to_public_summary()],
                store_url="memory://test",
            )

        self.assertEqual(filtered, [])
        self.assertEqual(metadata["status"], "unavailable")
        self.assertEqual(metadata["accepted"], 0)
        self.assertEqual(metadata["rejected"], 1)
        self.assertEqual(metadata["primary_label"], "qwen")
        self.assertEqual(metadata["fallback_label"], "openai")
        self.assertEqual(metadata["untracked_passthrough"], 0)

    def test_inline_live_candidates_are_judged_not_passed_through(self) -> None:
        stored_good = _candidate("stored-good")
        inline_interviewer_ai = {
            "provider_id": "ai-conveo-conveo",
            "display_name": "Conveo",
            "provider_type": "mcp_server",
            "capabilities": ["general_research"],
            "verification_status": "registered_in_directory",
            "evidence_url": "https://app.conveo.ai/api/mcp",
            "metadata": {
                "description": (
                    "Qualitative research platform. Design studies, analyze "
                    "interviews, and generate insights."
                )
            },
            "source_ids": ["official_mcp_registry"],
        }

        with (
            patch("planmyagents_api.discovery.store.discovery_store_for_path", return_value=_FakeStore([stored_good])),
            patch.object(app_module, "is_judge_enabled", return_value=True),
            patch.object(app_module, "build_default_escalating_client", return_value=object()),
            patch.object(app_module, "CandidateJudge", _AcceptOnlyStoredGoodJudge),
        ):
            filtered, metadata = app_module._apply_candidate_judge(
                goal="find the best place to buy gift for a 3 year old baby girl",
                required_capabilities=["general_research"],
                agentic_results=[
                    stored_good.to_public_summary(),
                    inline_interviewer_ai,
                ],
                store_url="memory://test",
                acceptance_criteria_by_capability={
                    "general_research": (
                        "The agent provides a list of gift ideas suitable for "
                        "3-year-old girls."
                    )
                },
            )

        self.assertEqual([item["provider_id"] for item in filtered], ["stored-good"])
        self.assertEqual(
            sorted(_AcceptOnlyStoredGoodJudge.seen_candidate_ids),
            ["ai-conveo-conveo", "stored-good"],
        )
        self.assertEqual(metadata["accepted"], 1)
        self.assertEqual(metadata["rejected"], 1)
        self.assertEqual(metadata["untracked_passthrough"], 0)
        self.assertEqual(metadata["blocked_untracked"], 1)

    def test_inline_candidate_description_is_available_to_judge(self) -> None:
        candidate = app_module._candidate_from_agentic_result(
            {
                "provider_id": "ai-conveo-conveo",
                "display_name": "Conveo",
                "provider_type": "mcp_server",
                "capabilities": ["general_research"],
                "metadata": {
                    "description": (
                        "Qualitative research platform. Design studies, "
                        "analyze interviews, and generate insights."
                    )
                },
            },
            requested_capabilities=["general_research"],
        )

        self.assertIn("analyze interviews", _short_description(candidate))


if __name__ == "__main__":
    unittest.main()
