"""Tests for the LLM-backed docs extractor enricher."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.enrichers.docs_extractor import (
    DocsExtractorEnricher,
    StubChatClient,
    StubDocsTransport,
    _html_to_text,
    _parse_extractor_response,
)
from planmyagents_api.discovery.models import (
    CandidateCapability,
    CandidateDocs,
    DiscoveryCandidate,
)


def _candidate(**overrides) -> DiscoveryCandidate:
    base = dict(
        id="x",
        display_name="X",
        vendor="X",
        vendor_url="https://x.example",
        provider_type="api_provider",
        capabilities=[CandidateCapability(id="search", confidence=0.5)],
        evidence_url="https://x.example/docs",
    )
    base.update(overrides)
    return DiscoveryCandidate(**base)


class HtmlToTextTests(unittest.TestCase):
    def test_strips_tags_and_collapses_whitespace(self) -> None:
        html = "<html><body><h1>X API</h1><p>Use <code>X-API-Key</code></p></body></html>"
        self.assertIn("X API", _html_to_text(html))
        self.assertIn("X-API-Key", _html_to_text(html))
        self.assertNotIn("<h1>", _html_to_text(html))

    def test_drops_script_and_style_blocks(self) -> None:
        html = "<style>.x{}</style><script>alert('x')</script><p>visible</p>"
        out = _html_to_text(html)
        self.assertEqual(out.strip(), "visible")


class ParseResponseTests(unittest.TestCase):
    def test_returns_none_for_garbage(self) -> None:
        self.assertIsNone(_parse_extractor_response(""))
        self.assertIsNone(_parse_extractor_response("not json at all"))
        self.assertIsNone(_parse_extractor_response("[1,2,3]"))

    def test_recovers_json_from_surrounding_prose(self) -> None:
        raw = 'Here is the answer: {"auth_method":"api_key"} ok?'
        self.assertEqual(
            _parse_extractor_response(raw), {"auth_method": "api_key"}
        )


class DocsExtractorEnricherTests(unittest.TestCase):
    def test_skips_when_no_evidence_url(self) -> None:
        candidate = _candidate(evidence_url="", vendor_url="ftp://x.example")
        enricher = DocsExtractorEnricher(
            chat_client=StubChatClient(completion="{}"),
            transport=StubDocsTransport(responses={}),
        )
        out = enricher.enrich([candidate])
        self.assertTrue(out[0].docs.is_empty)

    def test_skips_when_already_populated(self) -> None:
        candidate = _candidate(
            docs=CandidateDocs(setup_url="https://x.example/docs", auth_method="api_key")
        )
        enricher = DocsExtractorEnricher(
            chat_client=StubChatClient(completion="{}"),
            transport=StubDocsTransport(responses={}),
        )
        out = enricher.enrich([candidate])
        self.assertEqual(out[0].docs.auth_method, "api_key")

    def test_returns_unchanged_when_transport_fails(self) -> None:
        candidate = _candidate()
        enricher = DocsExtractorEnricher(
            chat_client=StubChatClient(completion="{}"),
            transport=StubDocsTransport(responses={}),
        )
        out = enricher.enrich([candidate])
        self.assertTrue(out[0].docs.is_empty)

    def test_populates_docs_from_chat_response(self) -> None:
        candidate = _candidate()
        chat = StubChatClient(
            completion=json.dumps(
                {
                    "auth_method": "api_key",
                    "auth_scopes": ["read", "write"],
                    "install_steps": [
                        "Sign up at x.example",
                        "Create API key",
                        "Set X_API_KEY env var",
                    ],
                    "usage_examples": [
                        {
                            "title": "Search",
                            "language": "bash",
                            "snippet": "curl -H 'X-API-Key: ...' https://x.example/search?q=x",
                        }
                    ],
                    "pricing_url": "https://x.example/pricing",
                }
            )
        )
        transport = StubDocsTransport(
            responses={"https://x.example/docs": "<p>X API docs body</p>"}
        )
        enricher = DocsExtractorEnricher(chat_client=chat, transport=transport)
        out = enricher.enrich([candidate])

        self.assertEqual(out[0].docs.auth_method, "api_key")
        self.assertEqual(out[0].docs.auth_scopes, ["read", "write"])
        self.assertEqual(len(out[0].docs.install_steps), 3)
        self.assertEqual(out[0].docs.usage_examples[0].language, "bash")
        self.assertEqual(out[0].docs.pricing_url, "https://x.example/pricing")
        self.assertEqual(out[0].docs.setup_url, "https://x.example/docs")

    def test_returns_unchanged_when_chat_returns_garbage(self) -> None:
        candidate = _candidate()
        chat = StubChatClient(completion="not even json")
        transport = StubDocsTransport(
            responses={"https://x.example/docs": "<p>body</p>"}
        )
        enricher = DocsExtractorEnricher(chat_client=chat, transport=transport)
        out = enricher.enrich([candidate])
        self.assertTrue(out[0].docs.is_empty)


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
