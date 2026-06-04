"""Tests for `planmyagents_api.discovery.agent_classifier`.

The classifier is the smart filter between "RSS/HN raw item" and "row
written to discovery_candidates". The tests below pin three things:

1. The heuristic layer produces the right verdict for the obvious 80%
   (discussion-venue URLs, user-post titles, clear launches) without
   touching the LLM.
2. The LLM layer is only consulted for items the heuristic explicitly
   marks `uncertain`.
3. A flaky / unavailable LLM never causes the index to be polluted —
   the classifier defaults to `not_agent` on errors.
"""

from __future__ import annotations

import unittest

from planmyagents_api.discovery.agent_classifier import (
    classify_candidate,
    classify_heuristic,
    classify_with_llm,
)


class _ScriptedClient:
    """Records calls and returns a scripted response string."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.response


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        raise self._exc


class HeuristicLayerTests(unittest.TestCase):
    def test_reddit_host_rejected_regardless_of_title(self) -> None:
        result = classify_heuristic(
            title="Introducing the best MCP server ever",
            url="https://www.reddit.com/r/mcp/comments/abc/some-thread",
        )
        self.assertEqual(result.verdict, "not_agent")
        self.assertEqual(result.source, "heuristic")
        self.assertIn("discussion venue", result.reason)

    def test_hn_thread_url_rejected(self) -> None:
        result = classify_heuristic(
            title="Show HN: my MCP server",
            url="https://news.ycombinator.com/item?id=12345",
        )
        self.assertEqual(result.verdict, "not_agent")
        self.assertIn("hn thread", result.reason)

    def test_first_person_title_rejected(self) -> None:
        result = classify_heuristic(
            title="The more I use AI for research, the less I want a linear chat",
            url="https://example.com/blog/post",
        )
        self.assertEqual(result.verdict, "not_agent")

    def test_question_title_rejected(self) -> None:
        result = classify_heuristic(
            title="What's the best agent framework in 2026?",
            url="https://example.com/post",
        )
        self.assertEqual(result.verdict, "not_agent")

    def test_clear_launch_with_agent_noun_accepted(self) -> None:
        result = classify_heuristic(
            title="Introducing Claude Code MCP server",
            url="https://anthropic.com/news/claude-code-mcp",
            description="A new MCP server for code editing.",
        )
        self.assertEqual(result.verdict, "agent")
        self.assertEqual(result.source, "heuristic")

    def test_launch_verb_without_agent_noun_is_uncertain(self) -> None:
        # The catch: "Introducing X" but X has nothing to do with agents.
        # Heuristic shouldn't auto-accept.
        result = classify_heuristic(
            title="Introducing our new pricing model",
            url="https://example.com/pricing",
        )
        self.assertEqual(result.verdict, "uncertain")

    def test_empty_title_or_url_rejected(self) -> None:
        self.assertEqual(
            classify_heuristic(title="", url="https://example.com").verdict,
            "not_agent",
        )
        self.assertEqual(
            classify_heuristic(title="real title", url="").verdict,
            "not_agent",
        )

    def test_subdomain_of_denied_host_rejected(self) -> None:
        result = classify_heuristic(
            title="Introducing some new MCP server",
            url="https://r.reddit.com/r/AI_Agents/comments/xyz/",
        )
        self.assertEqual(result.verdict, "not_agent")

    def test_unknown_host_with_neutral_title_is_uncertain(self) -> None:
        result = classify_heuristic(
            title="LangGraph release notes for May",
            url="https://blog.langchain.dev/release-notes-may/",
        )
        self.assertEqual(result.verdict, "uncertain")


class LlmLayerTests(unittest.TestCase):
    def test_llm_accepts_with_well_formed_json(self) -> None:
        client = _ScriptedClient(
            '{"is_agent": true, "confidence": 0.92, "reason": "real MCP server"}'
        )
        result = classify_with_llm(
            title="LangGraph release notes for May",
            url="https://blog.langchain.dev/release-notes-may/",
            description="Adds tool-call streaming.",
            client=client,
        )
        self.assertEqual(result.verdict, "agent")
        self.assertEqual(result.source, "llm")
        self.assertGreater(result.confidence, 0.5)
        self.assertEqual(len(client.calls), 1)

    def test_llm_rejects_with_well_formed_json(self) -> None:
        client = _ScriptedClient(
            '{"is_agent": false, "confidence": 0.8, "reason": "corporate blog"}'
        )
        result = classify_with_llm(
            title="Our Q1 hiring update",
            url="https://example.com/blog/hiring",
            description="We hired N people.",
            client=client,
        )
        self.assertEqual(result.verdict, "not_agent")
        self.assertEqual(result.source, "llm")

    def test_llm_malformed_json_falls_back_to_reject(self) -> None:
        client = _ScriptedClient("not valid json at all")
        result = classify_with_llm(
            title="Something",
            url="https://example.com",
            description="",
            client=client,
        )
        self.assertEqual(result.verdict, "not_agent")
        self.assertEqual(result.source, "fallback")
        self.assertIn("non-json", result.reason)

    def test_llm_error_falls_back_to_reject(self) -> None:
        client = _RaisingClient(RuntimeError("ollama is down"))
        result = classify_with_llm(
            title="Something",
            url="https://example.com",
            description="",
            client=client,
        )
        self.assertEqual(result.verdict, "not_agent")
        self.assertEqual(result.source, "fallback")
        self.assertIn("RuntimeError", result.reason)
        self.assertEqual(client.calls, 1)

    def test_llm_confidence_clamped_to_unit_interval(self) -> None:
        client = _ScriptedClient(
            '{"is_agent": true, "confidence": 7.0, "reason": "ok"}'
        )
        result = classify_with_llm(
            title="X", url="https://example.com", description="", client=client
        )
        self.assertLessEqual(result.confidence, 1.0)
        self.assertGreaterEqual(result.confidence, 0.0)


class CompositeClassifierTests(unittest.TestCase):
    def test_heuristic_short_circuit_skips_llm(self) -> None:
        client = _ScriptedClient(
            '{"is_agent": true, "confidence": 0.9, "reason": "should not be called"}'
        )
        result = classify_candidate(
            title="The more I use AI for research, the less I want a chat thread",
            url="https://www.reddit.com/r/AI_Agents/comments/xyz/",
            llm_client=client,
        )
        self.assertEqual(result.verdict, "not_agent")
        self.assertEqual(client.calls, [])

    def test_uncertain_with_no_client_is_rejected(self) -> None:
        result = classify_candidate(
            title="LangGraph release notes for May",
            url="https://blog.langchain.dev/release-notes-may/",
            llm_client=None,
        )
        self.assertEqual(result.verdict, "not_agent")
        self.assertEqual(result.source, "fallback")
        self.assertIn("no llm configured", result.reason)

    def test_uncertain_escalates_to_llm(self) -> None:
        client = _ScriptedClient(
            '{"is_agent": true, "confidence": 0.85, "reason": "release post"}'
        )
        result = classify_candidate(
            title="LangGraph release notes for May",
            url="https://blog.langchain.dev/release-notes-may/",
            description="Adds tool-call streaming.",
            llm_client=client,
        )
        self.assertEqual(result.verdict, "agent")
        self.assertEqual(result.source, "llm")
        self.assertEqual(len(client.calls), 1)

    def test_clear_agent_accept_skips_llm(self) -> None:
        client = _ScriptedClient(
            '{"is_agent": false, "confidence": 0.9, "reason": "should not be called"}'
        )
        result = classify_candidate(
            title="Introducing Claude Code MCP server",
            url="https://anthropic.com/news/claude-code-mcp",
            description="A new MCP server.",
            llm_client=client,
        )
        self.assertEqual(result.verdict, "agent")
        self.assertEqual(result.source, "heuristic")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
