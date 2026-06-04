"""Tests for `HackerNewsAgentWatcherSource`. HTTP is mocked end-to-end."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.sources import hacker_news as hn  # noqa: E402

NEWSTORIES_IDS = [101, 102, 103, 104, 105]
TOPSTORIES_IDS = [201, 102]  # 102 overlaps with newstories — must be deduped

ITEM_PAYLOADS: dict[int, dict] = {
    101: {
        "id": 101,
        "type": "story",
        "title": "Show HN: My new MCP server for stripe payments",
        "text": "It exposes payment_authorization via the Model Context Protocol.",
        "url": "https://github.com/example/stripe-mcp",
        "score": 42,
    },
    102: {
        "id": 102,
        "type": "story",
        # Title now includes a launch verb so it passes the classifier.
        "title": "Open-sourcing an AI agent framework for autonomous workflows",
        "text": "Tool-use, function calling, and multi-step planning.",
        "url": "https://example.com/agent-framework",
        "score": 18,
    },
    103: {
        # Filtered out: no agent/MCP keywords.
        "id": 103,
        "type": "story",
        "title": "Why I switched from Vim to Emacs",
        "text": "A thoughtful editor war post.",
        "url": "https://example.com/vim-vs-emacs",
        "score": 88,
    },
    104: {
        # Filtered out: not a story (job post).
        "id": 104,
        "type": "job",
        "title": "AI agent infrastructure engineer at FrontierLab",
        "url": "https://frontierlab.example/jobs/123",
        "score": 1,
    },
    105: {
        # Filtered out: matches keyword but score below threshold (default 2).
        "id": 105,
        "type": "story",
        "title": "Tiny ai agent for hobbyist",
        "text": "function calling, tool-use",
        "url": "https://example.com/tiny",
        "score": 1,
    },
    201: {
        "id": 201,
        "type": "story",
        "title": "Anthropic releases Model Context Protocol agent.json spec",
        "url": "https://anthropic.example/news/mcp-spec",
        "score": 312,
    },
}


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self._buf

    def __exit__(self, *_):
        self._buf.close()


def _build_fake_urlopen():
    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/newstories.json"):
            return _FakeResponse(NEWSTORIES_IDS)
        if url.endswith("/topstories.json"):
            return _FakeResponse(TOPSTORIES_IDS)
        # Item endpoints look like .../item/{id}.json
        for story_id, payload in ITEM_PAYLOADS.items():
            if url.endswith(f"/item/{story_id}.json"):
                return _FakeResponse(payload)
        return _FakeResponse(None)

    return fake_urlopen


class HackerNewsAgentWatcherSourceTests(unittest.TestCase):
    def test_emits_only_keyword_matching_stories_with_min_score(self) -> None:
        source = hn.HackerNewsAgentWatcherSource(max_items_per_firehose=10)
        with patch.object(hn.request, "urlopen", _build_fake_urlopen()):
            candidates = source.search(capabilities=set(), task_description="")

        ids = sorted(c.id for c in candidates)
        self.assertIn("hn-101", ids)  # Show HN MCP post
        self.assertIn("hn-102", ids)  # AI agent framework
        self.assertIn("hn-201", ids)  # Anthropic MCP spec
        self.assertNotIn("hn-103", ids, "Vim/Emacs post is off-topic, must be filtered")
        self.assertNotIn("hn-104", ids, "job posts must be filtered out")
        self.assertNotIn("hn-105", ids, "score-1 posts below default min_score must be filtered")

    def test_dedupes_across_firehoses(self) -> None:
        # 102 appears in both newstories and topstories.
        source = hn.HackerNewsAgentWatcherSource(max_items_per_firehose=10)
        with patch.object(hn.request, "urlopen", _build_fake_urlopen()):
            candidates = source.search(capabilities=set(), task_description="")

        self.assertEqual(
            sum(1 for c in candidates if c.id == "hn-102"),
            1,
            "story 102 appears in two firehoses but must yield one candidate",
        )

    def test_capability_filter_applied_when_capabilities_requested(self) -> None:
        # See _capability_index_fixtures.py — payment_authorization
        # isn't in the shipped registry, so we extend the index for
        # this source-mechanics test only.
        from tests._capability_index_fixtures import extended_capability_index

        source = hn.HackerNewsAgentWatcherSource(max_items_per_firehose=10)
        with extended_capability_index(["payment_authorization"]), patch.object(
            hn.request, "urlopen", _build_fake_urlopen()
        ):
            candidates = source.search(
                capabilities={"payment_authorization"}, task_description=""
            )

        ids = [c.id for c in candidates]
        self.assertIn("hn-101", ids)  # Stripe MCP — has payment terms
        self.assertNotIn("hn-102", ids, "agent framework has no payment terms")

    def test_handles_network_error_silently(self) -> None:
        source = hn.HackerNewsAgentWatcherSource()

        def boom(req, timeout=None):  # noqa: ARG001
            raise OSError("dns failure")

        with patch.object(hn.request, "urlopen", boom):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(candidates, [])

    def test_ask_hn_style_questions_are_rejected_by_classifier(self) -> None:
        # The old behavior was to emit a candidate for Ask HN questions
        # using the HN thread URL as evidence. That was wrong — these
        # are discussion threads, not products. The classifier now
        # rejects them: the URL hits the HN-thread-URL deny pattern
        # AND the title hits the ?-suffix user-post pattern.
        ITEM_PAYLOADS[999] = {
            "id": 999,
            "type": "story",
            "title": "Ask HN: Best MCP server for postgres?",
            "text": "Looking for recommendations.",
            "score": 5,
        }
        with patch.object(
            hn,
            "_keyword_regex",
            lambda kw: __import__("re").compile(r"mcp", __import__("re").IGNORECASE),
        ):
            source = hn.HackerNewsAgentWatcherSource(
                max_items_per_firehose=1,
                firehoses=("custom",),
            )

            def fake_urlopen_for_999(req, timeout=None):  # noqa: ARG001
                url = req.full_url
                if url.endswith("/custom.json"):
                    return _FakeResponse([999])
                if url.endswith("/item/999.json"):
                    return _FakeResponse(ITEM_PAYLOADS[999])
                return _FakeResponse(None)

            with patch.object(hn.request, "urlopen", fake_urlopen_for_999):
                cands = source.search(capabilities=set(), task_description="")

        self.assertEqual(cands, [], "Ask HN questions must not become candidates")


if __name__ == "__main__":
    unittest.main()
