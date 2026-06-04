"""Tests for ``GlamaDirectorySource``.

We monkey-patch ``urllib.request.urlopen`` (via the source module's
``request`` binding) so the tests never touch the network. Fixtures
mirror the documented response shape from the Glama JSON API at
``https://glama.ai/api/mcp/v1/servers`` — captured live on 2026-05-20
(see the source-module docstring for the verified payload shape).
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.sources import glama as gl  # noqa: E402

from tests._capability_index_fixtures import extended_capability_index  # noqa: E402

# Glama fixtures include several MCP categories whose canonical
# capability ids aren't necessarily in the shipped registry — extend
# the index for the test's lifetime so the inference layer can bind.
_TEST_EXTRA_CAPS = ["payment_authorization", "ai_inference", "email_send"]

PAGE_1 = {
    "pageInfo": {
        "endCursor": "cursor-after-page-1",
        "hasNextPage": True,
        "hasPreviousPage": False,
        "startCursor": "cursor-before-page-1",
    },
    "servers": [
        {
            "id": "abc123",
            "name": "Stripe Payments MCP",
            "namespace": "stripe",
            "slug": "payments-mcp",
            "description": "Charge a card, verify a payment, refund a payment.",
            "attributes": ["author:official", "hosting:remote-capable"],
            "spdxLicense": {"name": "MIT License", "url": "https://example/MIT"},
            "repository": {"url": "https://github.com/stripe/payments-mcp"},
            "environmentVariablesJsonSchema": {
                "properties": {
                    "STRIPE_API_KEY": {"type": "string"},
                },
                "type": "object",
                "required": ["STRIPE_API_KEY"],
            },
            "tools": [],
            "url": "https://glama.ai/mcp/servers/abc123",
        },
        {
            "id": "def456",
            "name": "Gmail MCP",
            "namespace": "knowledgeislands",
            "slug": "mcp-gmail",
            "description": "Read, search, send, label, and manage email via Gmail.",
            "attributes": ["hosting:local-only"],
            "spdxLicense": {"name": "MIT License", "url": "https://example/MIT"},
            "repository": {"url": "https://github.com/knowledgeislands/mcp-gmail"},
            "environmentVariablesJsonSchema": {
                "properties": {
                    "GMAIL_CLIENT_ID": {"type": "string"},
                },
                "type": "object",
                "required": ["GMAIL_CLIENT_ID"],
            },
            "tools": [],
            "url": "https://glama.ai/mcp/servers/def456",
        },
        {
            "id": "junk1",
            "name": "py-test 0",
            "namespace": "ai.smithery",
            "slug": "arjunkmrm-py-test-0",
            "description": "A test server for development.",
            "attributes": ["hosting:local-only"],
            "spdxLicense": None,
            "repository": {"url": "https://github.com/ai.smithery/arjunkmrm-py-test-0"},
            "environmentVariablesJsonSchema": {},
            "tools": [],
            "url": "https://glama.ai/mcp/servers/junk1",
        },
        {
            "id": "skeleton1",
            "name": "Empty Skeleton",
            "namespace": "placeholder",
            "slug": "empty-skeleton",
            "description": "A reserved namespace with no implementation yet.",
            "attributes": [],
            "spdxLicense": None,
            "repository": {},
            "environmentVariablesJsonSchema": {},
            "tools": [],
            "url": "https://glama.ai/mcp/servers/skeleton1",
        },
        {
            "id": "nocap1",
            "name": "Random Words",
            "namespace": "noisemaker",
            "slug": "random",
            "description": "Just emits random words. No useful capability.",
            "attributes": ["hosting:local-only"],
            "spdxLicense": None,
            "repository": {"url": "https://github.com/noisemaker/random"},
            "environmentVariablesJsonSchema": {},
            "tools": [],
            "url": "https://glama.ai/mcp/servers/nocap1",
        },
    ],
}

PAGE_2 = {
    "pageInfo": {
        "endCursor": "cursor-after-page-2",
        "hasNextPage": False,
        "hasPreviousPage": True,
        "startCursor": "cursor-before-page-2",
    },
    "servers": [
        {
            "id": "ghi789",
            "name": "AI Inference Hub",
            "namespace": "upstash",
            "slug": "context7-mcp",
            "description": "Run AI inference and lookup models across image, video, and audio.",
            "attributes": ["hosting:hybrid"],
            "spdxLicense": {"name": "Apache 2.0", "url": "https://example/Apache"},
            "repository": {"url": "https://github.com/upstash/context7-mcp"},
            "environmentVariablesJsonSchema": {},
            "tools": [],
            "url": "https://glama.ai/mcp/servers/ghi789",
        },
    ],
}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self) -> io.BytesIO:
        return self._buf

    def __exit__(self, *_: object) -> None:
        self._buf.close()


def _fake_urlopen_factory(pages: list[dict]):
    iterator = iter(pages)

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(next(iterator))

    return fake_urlopen


class GlamaDirectorySourceTests(unittest.TestCase):
    def test_paginates_normalises_and_drops_junk(self) -> None:
        source = gl.GlamaDirectorySource(max_pages=2)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            gl.request, "urlopen", _fake_urlopen_factory([PAGE_1, PAGE_2])
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )

        ids = sorted(c.id for c in candidates)
        self.assertIn("stripe-payments-mcp", ids)
        self.assertIn("knowledgeislands-mcp-gmail", ids)
        self.assertIn("upstash-context7-mcp", ids)
        # Junk by token name → dropped pre-inference.
        self.assertNotIn("ai-smithery-arjunkmrm-py-test-0", ids)
        # Skeleton: no repo, no attributes, no env-schema → dropped
        # by the corroboration policy.
        self.assertNotIn("placeholder-empty-skeleton", ids)
        # No capability synonym matches → dropped.
        self.assertNotIn("noisemaker-random", ids)

    def test_official_listings_promoted_to_known_provider(self) -> None:
        source = gl.GlamaDirectorySource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            gl.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )
        stripe = next(c for c in candidates if c.id == "stripe-payments-mcp")
        # ``author:official`` attribute → publisher-signed → one tier
        # above the default ``registered_in_directory``.
        self.assertEqual(stripe.verification_status, "known_provider")
        # Non-official listing stays at the default tier.
        gmail = next(c for c in candidates if c.id == "knowledgeislands-mcp-gmail")
        self.assertEqual(gmail.verification_status, "registered_in_directory")

    def test_capability_filter_applied_when_requested(self) -> None:
        source = gl.GlamaDirectorySource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            gl.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(
                capabilities={"payment_authorization"},
                task_description="charge a card",
            )
        ids = [c.id for c in candidates]
        self.assertIn("stripe-payments-mcp", ids)
        # Gmail is email_send only — should be dropped when the
        # caller specifically asks for payment_authorization.
        self.assertNotIn("knowledgeislands-mcp-gmail", ids)

    def test_stops_when_has_next_page_false(self) -> None:
        # PAGE_2 has hasNextPage=False — even though max_pages=3,
        # the source should stop after consuming PAGE_2.
        source = gl.GlamaDirectorySource(max_pages=3)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            gl.request, "urlopen", _fake_urlopen_factory([PAGE_1, PAGE_2])
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )
        # 3 unique surviving candidates across the two pages.
        ids = {c.id for c in candidates}
        self.assertEqual(
            ids,
            {
                "stripe-payments-mcp",
                "knowledgeislands-mcp-gmail",
                "upstash-context7-mcp",
            },
        )

    def test_handles_empty_payload_gracefully(self) -> None:
        source = gl.GlamaDirectorySource(max_pages=1)
        with patch.object(
            gl.request, "urlopen", _fake_urlopen_factory([{}])
        ):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        self.assertEqual(candidates, [])

    def test_handles_network_error_gracefully(self) -> None:
        from urllib import error as urlerror

        def boom(req, timeout=None):  # noqa: ARG001
            raise urlerror.URLError("dns unhappy")

        source = gl.GlamaDirectorySource(max_pages=1)
        with patch.object(gl.request, "urlopen", boom):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        self.assertEqual(candidates, [])

    def test_metadata_captures_provenance(self) -> None:
        source = gl.GlamaDirectorySource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            gl.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )
        stripe = next(c for c in candidates if c.id == "stripe-payments-mcp")
        # Metadata lives on the observation, not on the candidate
        # directly — match the contract the other source tests use.
        # (Smithery's tests assert vendor/source/verification_status
        # only; raw metadata flows through normalisation.)
        self.assertEqual(stripe.source, "glama")
        self.assertEqual(stripe.vendor, "stripe")
        self.assertEqual(stripe.provider_type, "mcp_server")


if __name__ == "__main__":
    unittest.main()
