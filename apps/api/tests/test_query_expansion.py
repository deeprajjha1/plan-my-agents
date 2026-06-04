"""Tests for the LLM-driven scout query expander."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.query_expansion import (  # noqa: E402
    DEFAULT_SCOUT_IDS,
    expand_for_scouts,
)


class _StubChatClient:
    def __init__(self, response: str, raise_exc: BaseException | None = None) -> None:
        self._response = response
        self._raise = raise_exc
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(list(messages))
        if self._raise:
            raise self._raise
        return self._response


class QueryExpansionTests(unittest.TestCase):
    def test_uses_llm_response_when_well_formed_json(self) -> None:
        llm_payload = (
            '{"official_mcp_registry": "kyc verification mcp",'
            ' "apis_guru": "Onfido KYC API",'
            ' "hacker_news_agent_watch": "Show HN: KYC API",'
            ' "vendor_rss": "KYC product launch",'
            ' "github_code_search": "topic:kyc-api OR identity-verification",'
            ' "github_recently_pushed": "kyc agent"}'
        )
        client = _StubChatClient(llm_payload)
        expanded = expand_for_scouts(
            capability="kyc_aml_check",
            task_text="Verify buyer identity per UAE KYC.",
            chat_client=client,
        )
        self.assertTrue(expanded.used_llm)
        self.assertIsNone(expanded.fallback_reason)
        self.assertEqual(
            expanded.per_scout_queries["github_code_search"],
            "topic:kyc-api OR identity-verification",
        )
        self.assertEqual(len(client.calls), 1)
        # The system prompt must be present and ask for strict JSON.
        system_msg = client.calls[0][0]["content"]
        self.assertIn("strict JSON", system_msg)

    def test_falls_back_to_rules_when_no_chat_client(self) -> None:
        expanded = expand_for_scouts(
            capability="email_verification",
            task_text="Validate addresses for outreach campaign",
            chat_client=None,
        )
        self.assertFalse(expanded.used_llm)
        self.assertEqual(expanded.fallback_reason, "no_chat_client")
        # Each default scout id should have a phrasing.
        for scout_id in DEFAULT_SCOUT_IDS:
            self.assertIn(scout_id, expanded.per_scout_queries)
            self.assertGreater(len(expanded.per_scout_queries[scout_id]), 0)

    def test_falls_back_when_llm_returns_invalid_json(self) -> None:
        client = _StubChatClient("I am sorry, I cannot do that")
        expanded = expand_for_scouts(
            capability="x", task_text="t", chat_client=client
        )
        self.assertFalse(expanded.used_llm)
        self.assertEqual(expanded.fallback_reason, "invalid_llm_json")

    def test_falls_back_when_llm_raises(self) -> None:
        client = _StubChatClient("", raise_exc=TimeoutError("model busy"))
        expanded = expand_for_scouts(
            capability="x", task_text="t", chat_client=client
        )
        self.assertFalse(expanded.used_llm)
        self.assertTrue(
            expanded.fallback_reason.startswith("chat_client_error")
        )

    def test_extracts_json_from_fenced_code_blocks(self) -> None:
        llm_payload = (
            "```json\n"
            '{"official_mcp_registry": "kyc mcp",'
            ' "apis_guru": "Onfido",'
            ' "hacker_news_agent_watch": "Show HN: KYC",'
            ' "vendor_rss": "KYC launch",'
            ' "github_code_search": "topic:kyc",'
            ' "github_recently_pushed": "kyc agent"}\n'
            "```"
        )
        client = _StubChatClient(llm_payload)
        expanded = expand_for_scouts(
            capability="kyc_aml_check", task_text="t", chat_client=client
        )
        self.assertTrue(expanded.used_llm)
        self.assertEqual(expanded.per_scout_queries["apis_guru"], "Onfido")

    def test_drops_unknown_scout_ids_in_llm_response(self) -> None:
        # LLM returns an extra key that doesn't correspond to any scout.
        llm_payload = (
            '{"official_mcp_registry": "kyc",'
            ' "apis_guru": "Onfido",'
            ' "hacker_news_agent_watch": "Show HN",'
            ' "vendor_rss": "launch",'
            ' "github_code_search": "topic:kyc",'
            ' "github_recently_pushed": "kyc agent",'
            ' "made_up_scout": "should be ignored"}'
        )
        client = _StubChatClient(llm_payload)
        expanded = expand_for_scouts(
            capability="x", task_text="t", chat_client=client
        )
        self.assertNotIn("made_up_scout", expanded.per_scout_queries)

    def test_ignores_oversized_query_strings(self) -> None:
        # 300-char query — too long, must be dropped.
        oversized = "x " * 200
        llm_payload = (
            '{"official_mcp_registry": "ok",'
            f' "apis_guru": "{oversized}",'
            ' "hacker_news_agent_watch": "Show HN",'
            ' "vendor_rss": "launch",'
            ' "github_code_search": "topic:kyc",'
            ' "github_recently_pushed": "kyc agent"}'
        )
        client = _StubChatClient(llm_payload)
        expanded = expand_for_scouts(
            capability="x", task_text="t", chat_client=client
        )
        self.assertNotIn("apis_guru", expanded.per_scout_queries)
        self.assertIn("official_mcp_registry", expanded.per_scout_queries)


class CapabilityBroadPromptContractTests(unittest.TestCase):
    """The system prompt MUST tell the LLM to keep queries
    capability-broad and explicitly NOT copy domain nouns from the
    sub-task. This contract is what makes the broad-search-then-judge
    pipeline work — a regression here would silently restore the
    over-specific behaviour where queries like
    ``"<sub-task domain> finder"`` get sent to scouts and return
    zero recall on agent registries.

    We assert against the prompt content directly because there's no
    cheap way to verify the LLM-side behaviour without a live model;
    the prompt content is the contract.
    """

    def _capture_system_prompt(self) -> str:
        client = _StubChatClient("{}")
        expand_for_scouts(
            capability="some_capability",
            task_text="some sub-task",
            chat_client=client,
        )
        return client.calls[0][0]["content"]

    def test_system_prompt_explicitly_demands_capability_only_queries(
        self,
    ) -> None:
        # Lock down the capability-only contract with two independent
        # assertions so a partial rewrite that drops one of them still
        # trips the test.
        prompt = self._capture_system_prompt()
        prompt_lower = prompt.lower()
        self.assertIn("capability", prompt_lower)
        # Either "do not include" or "do not copy" should be present
        # — both wordings express the same prohibition. We accept
        # either to allow future minor rewording without breaking the
        # test, but we require at least one.
        self.assertTrue(
            "do not include" in prompt_lower or "do not copy" in prompt_lower,
            "prompt must explicitly prohibit copying sub-task nouns",
        )

    def test_system_prompt_explicitly_calls_out_judge_filtering_downstream(
        self,
    ) -> None:
        # The reason the LLM can be aggressive about broadening is
        # that the judge filters by goal text downstream. Telling the
        # LLM about that contract reduces the temptation to over-
        # specify "to be helpful".
        prompt = self._capture_system_prompt().lower()
        self.assertIn("judge", prompt)

    def test_user_prompt_marks_subtask_as_context_only(self) -> None:
        # The sub-task is provided so the LLM understands which
        # capability is meant — not as material to copy. The user
        # prompt must mark it as such.
        client = _StubChatClient("{}")
        expand_for_scouts(
            capability="some_capability",
            task_text="some sub-task",
            chat_client=client,
        )
        user_prompt = client.calls[0][1]["content"].lower()
        self.assertIn("context only", user_prompt)


if __name__ == "__main__":
    unittest.main()
