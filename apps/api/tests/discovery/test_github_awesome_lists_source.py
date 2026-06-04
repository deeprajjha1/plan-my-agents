"""Tests for ``GitHubAwesomeListsSource``.

We monkey-patch the source module's ``request.urlopen`` so tests never
touch GitHub. The README fixture format mirrors what the real
awesome-lists ship (verified against ``punkpeye/awesome-mcp-servers``
on 2026-05-20): a flat list of ``- [Name](github-url) - description``
rows.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.sources import (  # noqa: E402
    github_awesome_lists as gal,
)

from tests._capability_index_fixtures import extended_capability_index  # noqa: E402

_TEST_EXTRA_CAPS = ["payment_authorization", "ai_inference", "email_send"]


README_FIXTURE_PUNKPEYE = """\
# Awesome MCP Servers

> A curated list of awesome MCP servers.

## Payments

- [Stripe MCP](https://github.com/stripe/mcp-payments) - Charge a card, verify a payment, refund a payment.
- [Razorpay MCP](https://github.com/razorpay/mcp-razorpay) - Indian payment gateway MCP server.

## Email

- [Gmail MCP](https://github.com/gsuite-mcp/gmail-mcp) - Send an email and read inbox.
* [Postmark MCP](https://github.com/postmark-mcp/server) -- Transactional email send.

## Tools

- [Test Server Copy](https://github.com/foo/mcp-test-server-copy) - A test server for development.
- [No Capability](https://github.com/blank/blank) - Random words; not a real agent.
- [Mail Tester](mailto:test@example.com) - Not an HTTPS URL.

## Misc

- [Plain Description Entry](https://example.com/inference) An entry without a dash separator
- [Multi Provider Inference](https://github.com/inference-lab/multi-llm) - Run AI inference across models.
"""


README_FIXTURE_E2BDEV = """\
# Awesome AI Agents

- [LangChain](https://github.com/langchain-ai/langchain) - Framework for building agents.
- [AI Inference Hub](https://github.com/inference-lab/multi-llm) - Run AI inference across models.
"""


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)

    def __enter__(self) -> io.BytesIO:
        return self._buf

    def __exit__(self, *_: object) -> None:
        self._buf.close()


def _fake_urlopen_factory(responses_by_url: dict[str, str | Exception]):
    """Build a ``urlopen`` stub that returns different markdown per URL.

    Maps the URL prefix to either a markdown string (returned as the
    response body) or an exception (raised). Lets us simulate a
    multi-list fetch where one source 404s and the others succeed.
    """

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for prefix, response in responses_by_url.items():
            if url.startswith(prefix) or prefix in url:
                if isinstance(response, Exception):
                    raise response
                return _FakeResponse(response.encode("utf-8"))
        return _FakeResponse(b"")

    return fake_urlopen


class GitHubAwesomeListsSourceTests(unittest.TestCase):
    def test_skips_when_token_is_empty(self) -> None:
        """No-op when the GITHUB_TOKEN env var (and constructor arg)
        are empty. The dispatcher's ``requires_token`` guard handles
        this in production; the source's defensive skip protects
        direct callers (scripts / audits) from hitting GitHub
        unauthenticated and exhausting the 60-req/hr ceiling."""

        source = gal.GitHubAwesomeListsSource(token="")
        candidates = source.search(
            capabilities=set(), task_description="payment"
        )
        self.assertEqual(candidates, [])

    def test_parses_links_and_normalises(self) -> None:
        source = gal.GitHubAwesomeListsSource(
            token="fake-token-deadbeef",
            lists=(
                ("punkpeye", "awesome-mcp-servers", "mcp_server"),
            ),
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            gal.request,
            "urlopen",
            _fake_urlopen_factory({"awesome-mcp-servers": README_FIXTURE_PUNKPEYE}),
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )

        ids = sorted(c.id for c in candidates)
        # Real github-rooted rows with capability matches survive.
        # IDs are slug-normalised by ``normalize_candidate`` (slashes
        # become dashes) so we match the post-normalisation form.
        self.assertIn("stripe-mcp-payments", ids)
        self.assertIn("razorpay-mcp-razorpay", ids)
        self.assertIn("gsuite-mcp-gmail-mcp", ids)
        self.assertIn("postmark-mcp-server", ids)
        self.assertIn("inference-lab-multi-llm", ids)
        # Test/copy junk dropped pre-inference.
        self.assertNotIn("foo-mcp-test-server-copy", ids)
        # Mailto URL → not HTTPS → dropped at the URL allow-list.
        self.assertNotIn("blank-blank", ids)

    def test_provenance_metadata_records_the_listing_repo(self) -> None:
        source = gal.GitHubAwesomeListsSource(
            token="fake",
            lists=(
                ("punkpeye", "awesome-mcp-servers", "mcp_server"),
            ),
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            gal.request,
            "urlopen",
            _fake_urlopen_factory({"awesome-mcp-servers": README_FIXTURE_PUNKPEYE}),
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )
        stripe = next(c for c in candidates if c.id == "stripe-mcp-payments")
        self.assertEqual(stripe.source, "github_awesome_lists")
        self.assertEqual(stripe.vendor, "stripe")
        self.assertEqual(stripe.provider_type, "mcp_server")
        self.assertEqual(stripe.verification_status, "registered_in_directory")

    def test_per_list_provider_type_hint_overrides_default(self) -> None:
        """Each curated list declares its provider_type hint. AI-agent
        lists (e.g. e2b-dev/awesome-ai-agents) should produce
        ``ai_agent`` rows even though the underlying URL shape is the
        same as the MCP lists."""

        source = gal.GitHubAwesomeListsSource(
            token="fake",
            lists=(
                ("e2b-dev", "awesome-ai-agents", "ai_agent"),
            ),
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            gal.request,
            "urlopen",
            _fake_urlopen_factory({"awesome-ai-agents": README_FIXTURE_E2BDEV}),
        ):
            candidates = source.search(
                capabilities=set(), task_description="run an llm"
            )
        ids = {c.id for c in candidates}
        self.assertIn("inference-lab-multi-llm", ids)
        # The hint propagates: provider_type for entries from this
        # list is ai_agent, not mcp_server.
        inf = next(c for c in candidates if c.id == "inference-lab-multi-llm")
        self.assertEqual(inf.provider_type, "ai_agent")

    def test_skips_lists_that_404(self) -> None:
        """One 404'd list must not block the rest. Mirrors the
        dispatcher's per-scout error isolation but at the per-list
        sub-fetch granularity inside this source."""

        from urllib import error as urlerror

        source = gal.GitHubAwesomeListsSource(
            token="fake",
            lists=(
                ("missing", "awesome-x", "mcp_server"),
                ("e2b-dev", "awesome-ai-agents", "ai_agent"),
            ),
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            gal.request,
            "urlopen",
            _fake_urlopen_factory(
                {
                    "awesome-x": urlerror.HTTPError(
                        "http://github", 404, "Not Found", {}, None
                    ),
                    "awesome-ai-agents": README_FIXTURE_E2BDEV,
                }
            ),
        ):
            candidates = source.search(
                capabilities=set(), task_description="run an llm"
            )
        ids = {c.id for c in candidates}
        # The surviving list still yields its candidate.
        self.assertIn("inference-lab-multi-llm", ids)

    def test_max_total_candidates_caps_yield(self) -> None:
        """A README with 200 entries shouldn't return all 200 — the
        ``max_total_candidates`` cap guards memory + downstream
        latency. We don't pretend to be a directory; we're a scout."""

        big_readme = "\n".join(
            f"- [Entry {i}](https://github.com/vendor/email-send-{i}) - "
            "Send an email and AI inference."
            for i in range(50)
        )
        source = gal.GitHubAwesomeListsSource(
            token="fake",
            lists=(
                ("punkpeye", "awesome-mcp-servers", "mcp_server"),
            ),
            max_total_candidates=10,
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            gal.request,
            "urlopen",
            _fake_urlopen_factory({"awesome-mcp-servers": big_readme}),
        ):
            candidates = source.search(
                capabilities=set(), task_description="send email"
            )
        self.assertLessEqual(len(candidates), 10)


if __name__ == "__main__":
    unittest.main()
