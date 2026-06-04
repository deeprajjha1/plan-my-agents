"""Unit tests for the post-decomposition label reconciler.

Covers:

* The happy path (LLM returns a partial-match payload, caller gets a
  ``ReconciliationResult`` with the right shape).
* Confidence floor coercion (model says 0.5 → match becomes None).
* Hallucinated catalog ids (model invents an id, reconciler discards
  the match).
* Missing rows (model addresses 1 of 2 coined ids, the second gets a
  synthetic "no match" verdict — output stays exhaustive).
* LLM unavailability (caller gets ``status="unavailable"`` and
  unmodified coined ids; never raises).
* ``apply_matches_to_decomposition`` correctly rewrites sub-task
  slugs and recomputes the catalog_reused / new_capabilities
  partitions.
* Prompt contract — no domain bias enumerated.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.llm.escalating_client import (
    EscalationMetadata,
    NoLlmTierAvailableError,
)
from planmyagents_api.planner.goal_decomposer import (
    DecomposedSubTask,
    GoalDecomposition,
)
from planmyagents_api.planner.label_reconciler import (
    DEFAULT_MIN_CONFIDENCE,
    apply_matches_to_decomposition,
    reconcile_labels,
)


class _FakeChatClient:
    """Returns a pre-baked JSON payload and records calls for
    prompt-contract assertions."""

    def __init__(self, payload: dict | str) -> None:
        if isinstance(payload, dict):
            self._content = json.dumps(payload)
        else:
            self._content = payload
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self._content


class _FailingChatClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def complete(self, _messages: list[dict[str, str]]) -> str:
        raise self._exc


def _make_decomposition(
    *,
    sub_tasks: list[DecomposedSubTask],
    catalog_reused: list[str],
    new_caps: list[str],
) -> GoalDecomposition:
    return GoalDecomposition(
        intent_summary="test intent",
        sub_tasks=sub_tasks,
        confidence=0.8,
        catalog_reused_capabilities=catalog_reused,
        new_capabilities=new_caps,
        raw_response="",
    )


def _sub_task(cap_id: str, *, description: str = "") -> DecomposedSubTask:
    return DecomposedSubTask(
        description=description or f"do {cap_id}",
        user_facing_step=f"step {cap_id}",
        search_query=f"{cap_id} api",
        acceptance_criteria=f"agent does {cap_id}",
        suggested_capability_id=cap_id,
    )


class ReconcileLabelsHappyPathTests(unittest.TestCase):
    def test_returns_match_for_high_confidence_verdict(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("email_dispatch", description="send bulk email")],
            catalog_reused=[],
            new_caps=["email_dispatch"],
        )
        client = _FakeChatClient(
            {
                "matches": [
                    {
                        "coined_id": "email_dispatch",
                        "matched_to": "email_sending",
                        "confidence": 0.92,
                        "reason": "both describe sending mail to recipients.",
                    }
                ]
            }
        )

        result = reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["email_sending", "browser_automation"],
            client=client,
        )

        self.assertEqual(result.status, "applied")
        self.assertEqual(len(result.matches), 1)
        match = result.matches[0]
        self.assertEqual(match.coined_id, "email_dispatch")
        self.assertEqual(match.matched_to, "email_sending")
        self.assertGreater(match.confidence, 0.9)
        self.assertEqual(result.matched_pairs, {"email_dispatch": "email_sending"})

    def test_returns_no_match_when_model_says_no_match(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("ferry_logistics")],
            catalog_reused=[],
            new_caps=["ferry_logistics"],
        )
        client = _FakeChatClient(
            {
                "matches": [
                    {
                        "coined_id": "ferry_logistics",
                        "matched_to": None,
                        "confidence": 0.95,
                        "reason": "no equivalent in catalog.",
                    }
                ]
            }
        )

        result = reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["email_sending", "browser_automation"],
            client=client,
        )

        self.assertEqual(result.status, "applied")
        self.assertEqual(result.matches[0].matched_to, None)
        self.assertEqual(result.matched_pairs, {})


class ReconcileLabelsCoercionTests(unittest.TestCase):
    def test_low_confidence_match_is_coerced_to_none(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("email_dispatch")],
            catalog_reused=[],
            new_caps=["email_dispatch"],
        )
        client = _FakeChatClient(
            {
                "matches": [
                    {
                        "coined_id": "email_dispatch",
                        "matched_to": "email_sending",
                        "confidence": 0.4,  # below default floor 0.7
                        "reason": "weak match.",
                    }
                ]
            }
        )

        result = reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["email_sending"],
            client=client,
        )
        self.assertEqual(result.matches[0].matched_to, None)
        self.assertEqual(result.matched_pairs, {})

    def test_hallucinated_catalog_id_is_discarded(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("email_dispatch")],
            catalog_reused=[],
            new_caps=["email_dispatch"],
        )
        client = _FakeChatClient(
            {
                "matches": [
                    {
                        "coined_id": "email_dispatch",
                        # Not in the catalog we passed → must be discarded.
                        "matched_to": "magic_email_sender_9000",
                        "confidence": 0.99,
                        "reason": "made up.",
                    }
                ]
            }
        )

        result = reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["email_sending", "browser_automation"],
            client=client,
        )
        self.assertIsNone(result.matches[0].matched_to)

    def test_min_confidence_override_changes_floor(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("email_dispatch")],
            catalog_reused=[],
            new_caps=["email_dispatch"],
        )
        client = _FakeChatClient(
            {
                "matches": [
                    {
                        "coined_id": "email_dispatch",
                        "matched_to": "email_sending",
                        "confidence": 0.55,
                        "reason": "moderate.",
                    }
                ]
            }
        )

        # With a permissive floor (0.5) the match survives.
        result = reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["email_sending"],
            client=client,
            min_confidence=0.5,
        )
        self.assertEqual(result.matches[0].matched_to, "email_sending")

    def test_missing_match_row_is_backfilled_as_no_match(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("email_dispatch"), _sub_task("ferry_logistics")],
            catalog_reused=[],
            new_caps=["email_dispatch", "ferry_logistics"],
        )
        # Model only addresses one of two coined ids.
        client = _FakeChatClient(
            {
                "matches": [
                    {
                        "coined_id": "email_dispatch",
                        "matched_to": "email_sending",
                        "confidence": 0.9,
                        "reason": "match.",
                    }
                ]
            }
        )

        result = reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["email_sending"],
            client=client,
        )
        # Both coined ids must appear in the verdict list.
        coined = {m.coined_id for m in result.matches}
        self.assertEqual(coined, {"email_dispatch", "ferry_logistics"})
        # The unaddressed one is a synthetic "no match".
        unaddressed = next(m for m in result.matches if m.coined_id == "ferry_logistics")
        self.assertIsNone(unaddressed.matched_to)
        self.assertEqual(unaddressed.confidence, 0.0)


class ReconcileLabelsSkippedAndUnavailableTests(unittest.TestCase):
    def test_skipped_when_no_new_capabilities(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("email_sending")],
            catalog_reused=["email_sending"],
            new_caps=[],
        )
        client = _FakeChatClient({"matches": []})

        result = reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["email_sending"],
            client=client,
        )
        self.assertEqual(result.status, "skipped_no_new_capabilities")
        self.assertEqual(client.calls, [])  # LLM never invoked

    def test_skipped_when_catalog_is_empty(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("brand_new_thing")],
            catalog_reused=[],
            new_caps=["brand_new_thing"],
        )
        client = _FakeChatClient({"matches": []})

        result = reconcile_labels(
            decomposition=decomposition,
            catalog_ids=[],
            client=client,
        )
        self.assertEqual(result.status, "skipped_no_catalog")
        self.assertEqual(client.calls, [])  # nothing to match against
        self.assertEqual(result.matched_pairs, {})

    def test_unavailable_when_llm_raises(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("brand_new_thing")],
            catalog_reused=[],
            new_caps=["brand_new_thing"],
        )
        metadata = EscalationMetadata(
            primary_label="qwen-test",
            primary_attempted=True,
            primary_error="connection refused",
        )
        client = _FailingChatClient(NoLlmTierAvailableError(metadata))

        result = reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["email_sending"],
            client=client,
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.matched_pairs, {})
        # Coined ids preserved exhaustively so the caller can still
        # iterate them for persistence.
        self.assertEqual(
            [m.coined_id for m in result.matches], ["brand_new_thing"]
        )
        self.assertIn("connection refused", result.reason)

    def test_unavailable_on_malformed_llm_response(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("brand_new_thing")],
            catalog_reused=[],
            new_caps=["brand_new_thing"],
        )
        client = _FakeChatClient("nope no json here")

        result = reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["email_sending"],
            client=client,
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.matched_pairs, {})


class PromptContractTests(unittest.TestCase):
    def test_prompt_does_not_enumerate_domain_specific_patterns(self) -> None:
        """The same anti-bias rule that drove the decomposer applies
        here. Enumerated families bias the model toward over-matching
        their members; this test pins the prompt to the unbiased shape.
        """
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("anything")],
            catalog_reused=[],
            new_caps=["anything"],
        )
        client = _FakeChatClient({"matches": []})
        reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["something_else"],
            client=client,
        )
        self.assertEqual(len(client.calls), 1)
        system_message = client.calls[0][0]["content"].lower()
        forbidden_substrings = [
            "compliance",
            "cross-border",
            "cross border",
            "commerce",
            "single malt",
            "liquor",
            "fleet",
        ]
        for needle in forbidden_substrings:
            self.assertNotIn(
                needle,
                system_message,
                msg=(
                    f"Reconciler system prompt must not enumerate "
                    f"the domain-specific term {needle!r} — that biases "
                    f"the model toward over-matching that family."
                ),
            )


class ApplyMatchesToDecompositionTests(unittest.TestCase):
    def test_rewrites_matched_sub_tasks_and_recomputes_partitions(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[
                _sub_task("web_search"),  # already in catalog → reused
                _sub_task("email_dispatch"),  # coined → will be matched
                _sub_task("ferry_logistics"),  # coined → will stay new
            ],
            catalog_reused=["web_search"],
            new_caps=["email_dispatch", "ferry_logistics"],
        )
        rewritten = apply_matches_to_decomposition(
            decomposition,
            matches={"email_dispatch": "email_sending"},
            catalog_ids=["web_search", "email_sending"],
        )
        slugs = [s.suggested_capability_id for s in rewritten.sub_tasks]
        self.assertEqual(slugs, ["web_search", "email_sending", "ferry_logistics"])
        self.assertEqual(
            rewritten.catalog_reused_capabilities, ["web_search", "email_sending"]
        )
        self.assertEqual(rewritten.new_capabilities, ["ferry_logistics"])
        # Other fields preserved.
        self.assertEqual(rewritten.intent_summary, decomposition.intent_summary)
        self.assertEqual(rewritten.confidence, decomposition.confidence)

    def test_returns_input_unchanged_when_no_matches(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("brand_new")],
            catalog_reused=[],
            new_caps=["brand_new"],
        )
        result = apply_matches_to_decomposition(decomposition, matches={})
        self.assertIs(result, decomposition)

    def test_self_referential_match_is_a_noop(self) -> None:
        # Reconciler shouldn't but might emit ``email_dispatch ->
        # email_dispatch`` (the coined id WAS itself); handle without
        # corruption.
        decomposition = _make_decomposition(
            sub_tasks=[_sub_task("email_dispatch")],
            catalog_reused=[],
            new_caps=["email_dispatch"],
        )
        result = apply_matches_to_decomposition(
            decomposition,
            matches={"email_dispatch": "email_dispatch"},
            catalog_ids=[],
        )
        self.assertEqual(
            result.sub_tasks[0].suggested_capability_id, "email_dispatch"
        )


class CoinedDescriptionsAreSentTests(unittest.TestCase):
    def test_user_payload_contains_coined_id_descriptions(self) -> None:
        decomposition = _make_decomposition(
            sub_tasks=[
                _sub_task("email_dispatch", description="send bulk email to many"),
            ],
            catalog_reused=[],
            new_caps=["email_dispatch"],
        )
        client = _FakeChatClient({"matches": []})
        reconcile_labels(
            decomposition=decomposition,
            catalog_ids=["email_sending"],
            client=client,
        )
        user_payload = json.loads(client.calls[0][1]["content"])
        self.assertIn("coined_ids", user_payload)
        first = user_payload["coined_ids"][0]
        self.assertEqual(first["id"], "email_dispatch")
        self.assertEqual(first["description"], "send bulk email to many")
        self.assertIn("email_sending", user_payload["existing_catalog_ids"])


class DefaultMinConfidenceIsConservativeTests(unittest.TestCase):
    def test_default_floor_is_at_least_07(self) -> None:
        # The "false matches are worse than false misses" design
        # decision pins the floor; if someone later lowers it below
        # 0.7 they should know they're crossing this line.
        self.assertGreaterEqual(DEFAULT_MIN_CONFIDENCE, 0.7)


if __name__ == "__main__":
    unittest.main()
