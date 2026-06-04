"""Tests for `OfficialMcpRegistrySource`.

We monkey-patch `urllib.request.urlopen` (via the source module's `request`
binding) so the tests never touch the network. The fixtures mirror the real
shape we observed at https://registry.modelcontextprotocol.io/v0/servers
on 2026-05-11.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.sources import official_mcp_registry as omr  # noqa: E402

from tests._capability_index_fixtures import extended_capability_index  # noqa: E402

# Source mechanics tests below use realistic vendor descriptions
# (Stripe payments, inference.sh) whose capabilities aren't in the
# 5-slug shipped registry. We extend the index per-test with the
# capability slugs the fixtures imply, so the source tests verify
# source behaviour (dedup, vendor extraction, version selection)
# independent of registry composition.
_TEST_EXTRA_CAPS = ["payment_authorization", "ai_inference"]

PAGE_1 = {
    "servers": [
        {
            "server": {
                "name": "ac.inference.sh/mcp",
                "title": "inference.sh",
                "description": "Search and run image, video, audio AI apps; lookup models.",
                "version": "1.0.0",
                "remotes": [{"type": "streamable-http", "url": "https://api.inference.sh/mcp"}],
                "repository": {"url": "https://github.com/x/y", "source": "github"},
            },
            "_meta": {
                "io.modelcontextprotocol.registry/official": {
                    "status": "active",
                    "isLatest": False,
                }
            },
        },
        {
            "server": {
                "name": "ac.inference.sh/mcp",
                "title": "inference.sh",
                "description": "Search and run image, video, audio AI apps; lookup models.",
                "version": "1.0.1",
                "remotes": [{"type": "streamable-http", "url": "https://api.inference.sh/mcp"}],
                "repository": {"url": "https://github.com/x/y", "source": "github"},
            },
            "_meta": {
                "io.modelcontextprotocol.registry/official": {
                    "status": "active",
                    "isLatest": True,
                }
            },
        },
        {
            "server": {
                "name": "stripe-payments/mcp",
                "title": "Stripe Payments MCP",
                "description": "Charge a card, verify a payment, refund a payment.",
                "version": "0.2.0",
                "repository": {"url": "https://github.com/stripe/mcp"},
            },
            "_meta": {
                "io.modelcontextprotocol.registry/official": {"isLatest": True}
            },
        },
        {
            "server": {
                "name": "noisemaker/random",
                "title": "Random Words",
                "description": "An entirely random server with no useful capability.",
                "version": "1.0.0",
            },
            "_meta": {
                "io.modelcontextprotocol.registry/official": {"isLatest": True}
            },
        },
    ],
    "metadata": {"nextCursor": "page-2"},
}

PAGE_2 = {
    "servers": [
        {
            "server": {
                "name": "scrapehub/mcp",
                "title": "ScrapeHub",
                "description": "Scrape any website and extract structured data.",
                "version": "0.1.0",
                "repository": {"url": "https://github.com/scrapehub/mcp"},
            },
            "_meta": {
                "io.modelcontextprotocol.registry/official": {"isLatest": True}
            },
        },
    ],
    "metadata": {},
}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self) -> io.BytesIO:
        return self._buf

    def __exit__(self, *_: object) -> None:
        self._buf.close()


def _fake_urlopen_factory(pages: list[dict]):
    """Return a urlopen replacement that yields one page per call."""

    iterator = iter(pages)

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(next(iterator))

    return fake_urlopen


class OfficialMcpRegistrySourceTests(unittest.TestCase):
    def test_dedupes_versions_and_keeps_only_latest(self) -> None:
        source = omr.OfficialMcpRegistrySource(max_pages=2)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            omr.request, "urlopen", _fake_urlopen_factory([PAGE_1, PAGE_2])
        ):
            candidates = source.search(capabilities=set(), task_description="")

        ids = sorted(c.id for c in candidates)
        # `noisemaker/random` is dropped (no capability synonym matches).
        self.assertIn("ac-inference-sh-mcp", ids)
        self.assertIn("stripe-payments-mcp", ids)
        self.assertIn("scrapehub-mcp", ids)
        self.assertNotIn("noisemaker-random", ids)

        inference = next(c for c in candidates if c.id == "ac-inference-sh-mcp")
        # Only one `inference.sh` candidate was emitted, despite two versions
        # appearing in the response.
        self.assertEqual(
            sum(1 for c in candidates if c.id == "ac-inference-sh-mcp"),
            1,
            "duplicate versions of the same server name should collapse to one",
        )
        self.assertEqual(inference.provider_type, "mcp_server")
        self.assertEqual(inference.source, "official_mcp_registry")
        self.assertEqual(
            [obs.source_id for obs in inference.observations],
            ["official_mcp_registry"],
        )
        # Fix-3 contract: the official MCP registry is the canonical
        # vendor-curated index for MCP servers, so candidates default
        # to ``registered_in_directory``. Without this, the gap-report
        # gate dropped every official-registry entry as
        # ``unverified``.
        self.assertEqual(
            inference.verification_status, "registered_in_directory"
        )

    def test_capability_filter_applied_when_capabilities_requested(self) -> None:
        source = omr.OfficialMcpRegistrySource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            omr.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(
                capabilities={"payment_authorization"}, task_description=""
            )

        ids = [c.id for c in candidates]
        self.assertIn("stripe-payments-mcp", ids)
        # `inference.sh` has no payment-related synonym in its description.
        self.assertNotIn("ac-inference-sh-mcp", ids)

    def test_handles_empty_payload_gracefully(self) -> None:
        source = omr.OfficialMcpRegistrySource(max_pages=1)
        with patch.object(omr.request, "urlopen", _fake_urlopen_factory([{}])):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(candidates, [])

    def test_handles_network_error_silently(self) -> None:
        source = omr.OfficialMcpRegistrySource(max_pages=1)

        def boom(req, timeout=None):  # noqa: ARG001
            raise OSError("network down")

        with patch.object(omr.request, "urlopen", boom):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(candidates, [])

    def test_vendor_extracted_from_namespaced_name(self) -> None:
        source = omr.OfficialMcpRegistrySource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            omr.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(capabilities=set(), task_description="")
        inference = next(c for c in candidates if c.id == "ac-inference-sh-mcp")
        self.assertEqual(inference.vendor, "inference.sh")


class IsObviousJunkTests(unittest.TestCase):
    """Direct tests for the cheap regex pre-filter the registry source
    runs before any classifier/LLM call. These names are the exact
    shapes that motivated the filter (school projects, test forks,
    numeric-suffix copies that crowded the production index)."""

    def test_school_and_tutorial_names_are_junk(self) -> None:
        for name in (
            "ai.smithery/aicastle-school-openai-api-agent-project",
            "vendor/student-class-assignment",
            "tutorial/getting-started-mcp",
        ):
            self.assertTrue(omr._is_obvious_junk(name), name)

    def test_six_plus_digit_suffix_names_are_junk(self) -> None:
        # The 6+-digit pattern catches "I bumped the test counter until
        # it deployed" entries like ...project123123123 (9 digits) and
        # ...project123123 (6 digits). Three- to five-digit suffixes are
        # deliberately *not* flagged because they are usernames or
        # protocol references in many legitimate cases.
        for name in (
            "ai.smithery/cc25a-openai-api-agent-project123123123",
            "vendor/server-001234",
        ):
            self.assertTrue(omr._is_obvious_junk(name), name)

    def test_repeated_digit_runs_are_junk(self) -> None:
        # Four or more of the same digit in a row is also a clear
        # bumped-test smell.
        for name in (
            "vendor/foo1111",
            "vendor/foo00000-mcp",
        ):
            self.assertTrue(omr._is_obvious_junk(name), name)

    def test_fork_clone_version_suffixes_are_junk(self) -> None:
        for name in (
            "vendor/something-v2",
            "vendor/something-copy",
            "vendor/something-fork",
            "vendor/something-clone",
        ):
            self.assertTrue(omr._is_obvious_junk(name), name)

    def test_test_demo_sandbox_token_names_are_junk(self) -> None:
        for name in (
            "vendor/sandbox-mcp",
            "vendor/playground-tools",
            "vendor/example-agent",
            "vendor/scratch-server",
            "ai.smithery/arjunkmrm-py-test-0",
            "ai.smithery/arjunkmrm-ts-test-2",
            "ai.smithery/blacklotusdev8-test_m",
        ):
            self.assertTrue(omr._is_obvious_junk(name), name)

    def test_test_suffix_names_are_junk(self) -> None:
        # "*-test" and "*-test-N" suffixes are unambiguous, even when
        # the rest of the name reads legitimate. This catches the
        # specific cases of two near-identical entries from the same
        # author where one is the prod build and the other is a test
        # build (e.g. BigVik193-reddit-ads-mcp-api vs ...-mcp-test).
        for name in (
            "ai.smithery/BigVik193-reddit-ads-mcp-test",
            "vendor/something-test",
            "vendor/something-test-3",
        ):
            self.assertTrue(omr._is_obvious_junk(name), name)

    def test_legitimate_names_with_digits_are_kept(self) -> None:
        # Regression for a v1 false positive: the substring rule
        # "\d{3,}\b" flagged any 3-digit run, which over-rejected
        # legitimate usernames + protocol names. The token-aware filter
        # must accept these.
        for name in (
            "stripe-payments/mcp",
            "ac.inference.sh/mcp",
            "scrapehub/mcp",
            "anthropic/claude-tools",
            "github.com/modelcontextprotocol/servers",
            "ai.smithery/Lattiq-x402-trading-signals",
            "ai.smithery/BigVik193-reddit-ads-mcp-api",
            "ai.smithery/eliu243-oura-mcp-server",
        ):
            self.assertFalse(omr._is_obvious_junk(name), name)

    def test_empty_name_is_junk(self) -> None:
        self.assertTrue(omr._is_obvious_junk(""))


class JunkAndClassifierIntegrationTests(unittest.TestCase):
    """End-to-end: PAGE_3 contains real production-shaped junk so the
    full ingest pipeline (regex pre-filter + classifier + capability
    inference) can be tested as one path."""

    def test_school_project_name_is_dropped_by_regex_prefilter(self) -> None:
        page = {
            "servers": [
                {
                    "server": {
                        "name": "ai.smithery/aicastle-school-openai-api-agent-project",
                        "title": "OpenAI API Agent Project",
                        "description": "Cheap to compare prices for school project.",
                        "version": "1.0.0",
                        "repository": {
                            "url": "https://github.com/aicastle-school/openai-api-agent-project"
                        },
                    },
                    "_meta": {
                        "io.modelcontextprotocol.registry/official": {"isLatest": True}
                    },
                },
            ],
            "metadata": {},
        }
        source = omr.OfficialMcpRegistrySource(max_pages=1)
        with patch.object(omr.request, "urlopen", _fake_urlopen_factory([page])):
            candidates = source.search(capabilities=set(), task_description="")
        # Even though the description matches "compare" / "cheap"
        # (which would have lit up `fare_comparison` under the old
        # substring tagging), the regex pre-filter rejects the name
        # before any capability inference runs. This is the test that
        # would have prevented the school-project-as-fare-comparison
        # incident from reaching the index in the first place.
        self.assertEqual(candidates, [])

    def test_legitimate_entry_is_kept_after_junk_filter(self) -> None:
        # Bare registry-shape descriptions (no "Introducing/Launching"
        # verbs) must still pass the registry source. The agent
        # classifier is intentionally NOT applied here — it's built for
        # announcement-shaped feeds (RSS/HN) and would over-reject
        # registry listings. Defense in depth at this stage is: name
        # regex (rejects junk names) plus capability inference (must
        # match at least one synonym). The deeper relevance check
        # happens at retrieval time via the LLM `CandidateJudge`.
        page = {
            "servers": [
                {
                    "server": {
                        "name": "stripe-payments/mcp",
                        "title": "Stripe Payments MCP",
                        "description": "Charge a card, verify a payment, refund a payment.",
                        "version": "1.0.0",
                        "repository": {"url": "https://github.com/stripe/mcp"},
                    },
                    "_meta": {
                        "io.modelcontextprotocol.registry/official": {"isLatest": True}
                    },
                },
            ],
            "metadata": {},
        }
        source = omr.OfficialMcpRegistrySource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            omr.request, "urlopen", _fake_urlopen_factory([page])
        ):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].id, "stripe-payments-mcp")


if __name__ == "__main__":
    unittest.main()
