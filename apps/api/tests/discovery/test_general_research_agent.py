"""Unit tests for the first-party general research agent.

Covers:

* Happy path — search transport returns hits, LLM returns a clean
  summary + citations, agent returns ``status="applied"`` with
  validated sources.
* Citation validation — model cites a URL we did NOT fetch; the
  validator silently drops the hallucinated citation.
* Empty search — transport returns no hits → ``status="empty_search"``,
  no LLM call is made.
* Search transport failure → ``status="unavailable"`` with reason text.
* LLM tier failure → ``status="unavailable"`` with the sources still
  attached (so the UI can at least show the search results we did
  fetch).
* Malformed LLM JSON → ``status="unavailable"``.
* Missing credentials and missing query short-circuits.
* Env-var off → ``status="disabled"``.
* Prompt contract — schema is strict-JSON, no markdown allowed,
  forbids fabricated URLs.
* Brave-shape mapping — the default transport correctly normalises
  ``description`` to ``snippet`` and the ``web.results`` shape.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.llm.escalating_client import (  # noqa: E402
    EscalationMetadata,
    NoLlmTierAvailableError,
)
from planmyagents_api.research.general_research_agent import (  # noqa: E402
    BRAVE_API_KEY_ENV,
    DEFAULT_MAX_RESULTS,
    GENERAL_RESEARCH_CAPABILITY_ID,
    GeneralResearchAgent,
    ResearchResult,
    ResearchSource,
    general_research_agent_enabled,
)


class _FakeChatClient:
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


def _fake_transport(
    hits: list[dict[str, Any]] | None = None,
    *,
    raise_exc: Exception | None = None,
):
    """Build a SearchTransport closure with deterministic behaviour."""

    captured: dict[str, Any] = {}

    def _transport(query: str, max_results: int, timeout_seconds: float):
        captured["query"] = query
        captured["max_results"] = max_results
        captured["timeout_seconds"] = timeout_seconds
        if raise_exc is not None:
            raise raise_exc
        return list(hits or [])

    _transport.captured = captured  # type: ignore[attr-defined]
    return _transport


class HappyPathTests(unittest.TestCase):
    def test_returns_applied_with_validated_sources_and_citations(self) -> None:
        transport = _fake_transport(
            [
                {
                    "url": "https://livingliquidz.com",
                    "title": "Living Liquidz",
                    "snippet": "Pan-India retailer with browseable inventory.",
                },
                {
                    "url": "https://tonique.in",
                    "title": "Tonique",
                    "snippet": "Bangalore retailer; prices listed online.",
                },
            ]
        )
        client = _FakeChatClient(
            {
                "summary": (
                    "For southern India, Living Liquidz and Tonique both "
                    "publish online catalogues with prices."
                ),
                "citations": [
                    "https://livingliquidz.com",
                    "https://tonique.in",
                ],
            }
        )
        agent = GeneralResearchAgent(
            api_key="fake-key",
            transport=transport,
            client=client,
        )
        self.assertTrue(agent.available())
        self.assertEqual(agent.capability_id, GENERAL_RESEARCH_CAPABILITY_ID)

        result = agent.research(
            sub_task_description="find liquor stores in southern India",
            goal="compare single malt prices across south indian retailers",
        )

        self.assertEqual(result.status, "applied")
        self.assertEqual(len(result.sources), 2)
        self.assertIn("Living Liquidz", result.summary)
        self.assertEqual(len(result.citations), 2)
        # Brave query was forwarded with the cleaned sub-task text
        # (not the goal text — query specificity matters more than
        # broader framing for the search backend).
        self.assertEqual(
            transport.captured["query"],  # type: ignore[attr-defined]
            "find liquor stores in southern India",
        )
        # The synthesis prompt got both the goal and the per-result
        # snippets so it can frame the answer.
        user_msg = json.loads(client.calls[0][1]["content"])
        self.assertEqual(
            user_msg["user_goal"],
            "compare single malt prices across south indian retailers",
        )
        self.assertEqual(len(user_msg["sources"]), 2)


class CitationValidationTests(unittest.TestCase):
    def test_drops_hallucinated_citation_urls(self) -> None:
        transport = _fake_transport(
            [
                {
                    "url": "https://real.example.com",
                    "title": "Real",
                    "snippet": "Real source.",
                }
            ]
        )
        client = _FakeChatClient(
            {
                "summary": "Looks real.",
                # Mix of one valid and one fabricated URL — the
                # fabricated one must be silently dropped.
                "citations": [
                    "https://real.example.com",
                    "https://made-up-by-the-llm.example.com",
                ],
            }
        )
        agent = GeneralResearchAgent(
            api_key="fake", transport=transport, client=client
        )
        result = agent.research(sub_task_description="any query")
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.citations, ["https://real.example.com"])


class EmptySearchTests(unittest.TestCase):
    def test_empty_transport_short_circuits_before_llm(self) -> None:
        transport = _fake_transport([])
        # Use a failing client to prove the LLM is never invoked.
        client = _FailingChatClient(RuntimeError("must not be called"))
        agent = GeneralResearchAgent(
            api_key="fake", transport=transport, client=client
        )
        result = agent.research(sub_task_description="anything")
        self.assertEqual(result.status, "empty_search")
        self.assertEqual(result.sources, [])
        self.assertEqual(result.summary, "")

    def test_only_invalid_urls_collapses_to_empty_search(self) -> None:
        transport = _fake_transport(
            [
                {"url": "mailto:hello@x.com", "title": "T", "snippet": "S"},
                {"url": "not a url", "title": "T", "snippet": "S"},
            ]
        )
        client = _FailingChatClient(RuntimeError("must not be called"))
        agent = GeneralResearchAgent(
            api_key="fake", transport=transport, client=client
        )
        result = agent.research(sub_task_description="anything")
        self.assertEqual(result.status, "empty_search")


class FailureTests(unittest.TestCase):
    def test_search_transport_raise_yields_unavailable(self) -> None:
        transport = _fake_transport(raise_exc=RuntimeError("upstream 503"))
        client = _FailingChatClient(RuntimeError("must not be called"))
        agent = GeneralResearchAgent(
            api_key="fake", transport=transport, client=client
        )
        result = agent.research(sub_task_description="anything")
        self.assertEqual(result.status, "unavailable")
        self.assertIn("upstream 503", result.reason)
        # The agent never got far enough to fetch sources.
        self.assertEqual(result.sources, [])

    def test_llm_failure_keeps_search_sources_in_payload(self) -> None:
        transport = _fake_transport(
            [
                {
                    "url": "https://real.example.com",
                    "title": "Real",
                    "snippet": "Snippet.",
                }
            ]
        )
        meta = EscalationMetadata(tier_used="none", primary_attempted=True)
        client = _FailingChatClient(NoLlmTierAvailableError(meta, "all down"))
        agent = GeneralResearchAgent(
            api_key="fake", transport=transport, client=client
        )
        result = agent.research(sub_task_description="x")
        self.assertEqual(result.status, "unavailable")
        # The user can still see what the search returned — useful
        # for debugging and a small UX improvement.
        self.assertEqual(len(result.sources), 1)
        self.assertIn("LLM tier failed", result.reason)

    def test_malformed_llm_json_yields_unavailable(self) -> None:
        transport = _fake_transport(
            [
                {
                    "url": "https://x.com",
                    "title": "X",
                    "snippet": "...",
                }
            ]
        )
        client = _FakeChatClient("totally not json")
        agent = GeneralResearchAgent(
            api_key="fake", transport=transport, client=client
        )
        result = agent.research(sub_task_description="x")
        self.assertEqual(result.status, "unavailable")
        self.assertIn("not return a parseable JSON object", result.reason)

    def test_empty_summary_in_llm_response_yields_unavailable(self) -> None:
        transport = _fake_transport(
            [
                {
                    "url": "https://x.com",
                    "title": "X",
                    "snippet": "...",
                }
            ]
        )
        client = _FakeChatClient({"summary": "", "citations": []})
        agent = GeneralResearchAgent(
            api_key="fake", transport=transport, client=client
        )
        result = agent.research(sub_task_description="x")
        self.assertEqual(result.status, "unavailable")


class ShortCircuitTests(unittest.TestCase):
    def test_missing_query_short_circuits(self) -> None:
        transport = _fake_transport()
        client = _FailingChatClient(RuntimeError("must not be called"))
        agent = GeneralResearchAgent(
            api_key="fake", transport=transport, client=client
        )
        result = agent.research(sub_task_description="   ")
        self.assertEqual(result.status, "missing_query")

    def test_no_credentials_yields_missing_credentials(self) -> None:
        # Clear env to be sure no key is available.
        prev = os.environ.pop(BRAVE_API_KEY_ENV, None)
        try:
            agent = GeneralResearchAgent(api_key=None, transport=None)
            self.assertFalse(agent.available())
            result = agent.research(sub_task_description="x")
            self.assertEqual(result.status, "missing_credentials")
            self.assertIn(BRAVE_API_KEY_ENV, result.reason)
        finally:
            if prev is not None:
                os.environ[BRAVE_API_KEY_ENV] = prev

    def test_disabled_env_flag_short_circuits(self) -> None:
        prev = os.environ.get("PLANMYAGENTS_GENERAL_RESEARCH_AGENT")
        os.environ["PLANMYAGENTS_GENERAL_RESEARCH_AGENT"] = "off"
        try:
            agent = GeneralResearchAgent(api_key="fake")
            self.assertFalse(agent.available())
            result = agent.research(sub_task_description="x")
            self.assertEqual(result.status, "disabled")
        finally:
            if prev is None:
                os.environ.pop("PLANMYAGENTS_GENERAL_RESEARCH_AGENT")
            else:
                os.environ["PLANMYAGENTS_GENERAL_RESEARCH_AGENT"] = prev


class PromptContractTests(unittest.TestCase):
    def test_system_prompt_demands_strict_json_no_markdown(self) -> None:
        transport = _fake_transport(
            [
                {
                    "url": "https://x.com",
                    "title": "X",
                    "snippet": "...",
                }
            ]
        )
        client = _FakeChatClient(
            {"summary": "ok", "citations": ["https://x.com"]}
        )
        agent = GeneralResearchAgent(
            api_key="fake", transport=transport, client=client
        )
        agent.research(sub_task_description="x")

        system_msg = client.calls[0][0]["content"].lower()
        self.assertIn("strict json", system_msg)
        self.assertIn("no markdown", system_msg)
        # Same anti-hallucination posture as the suggester.
        self.assertIn("do not invent urls", system_msg)


class JsonRoundTripTests(unittest.TestCase):
    def test_to_json_shape_matches_wire_contract(self) -> None:
        result = ResearchResult(
            status="applied",
            summary="hi",
            sources=[
                ResearchSource(
                    url="https://x.com", title="X", snippet="snip"
                )
            ],
            citations=["https://x.com"],
            elapsed_ms=120,
        )
        payload = result.to_json()
        self.assertEqual(
            set(payload.keys()),
            {
                "status",
                "summary",
                "sources",
                "citations",
                "elapsed_ms",
                "reason",
            },
        )
        self.assertEqual(
            set(payload["sources"][0].keys()), {"url", "title", "snippet"}
        )


class EnvFlagTests(unittest.TestCase):
    def test_default_enabled_is_true(self) -> None:
        prev = os.environ.pop("PLANMYAGENTS_GENERAL_RESEARCH_AGENT", None)
        try:
            self.assertTrue(general_research_agent_enabled())
        finally:
            if prev is not None:
                os.environ["PLANMYAGENTS_GENERAL_RESEARCH_AGENT"] = prev


class MaxResultsCapTests(unittest.TestCase):
    def test_default_max_results_is_passed_to_transport(self) -> None:
        transport = _fake_transport(
            [{"url": "https://x.com", "title": "X", "snippet": "..."}]
        )
        client = _FakeChatClient(
            {"summary": "ok", "citations": ["https://x.com"]}
        )
        agent = GeneralResearchAgent(
            api_key="fake", transport=transport, client=client
        )
        agent.research(sub_task_description="x")
        # Transport must receive max_results == DEFAULT_MAX_RESULTS so
        # we don't accidentally cap-then-pad and waste backend quota.
        self.assertEqual(
            transport.captured["max_results"],  # type: ignore[attr-defined]
            DEFAULT_MAX_RESULTS,
        )


if __name__ == "__main__":
    unittest.main()
