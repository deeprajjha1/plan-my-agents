"""Tests for ``SmitherySource``.

We monkey-patch ``urllib.request.urlopen`` (via the source module's
``request`` binding) so the tests never touch the network. Fixtures
mirror the documented response shape from the Smithery API reference.
The source's ``token`` parameter is exercised explicitly because
auth-required sources have an additional skip-on-empty-token path
that the dispatcher relies on.
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

from planmyagents_api.discovery.sources import smithery as sm  # noqa: E402

from tests._capability_index_fixtures import extended_capability_index  # noqa: E402

# Same rationale as the other source tests: realistic vendor fixtures
# exercise capabilities the live registry doesn't ship, so we extend
# the index for the test's lifetime.
_TEST_EXTRA_CAPS = ["payment_authorization", "ai_inference"]

PAGE_1 = {
    "servers": [
        {
            "qualifiedName": "@stripe/payments-mcp",
            "displayName": "Stripe Payments MCP",
            "description": "Charge a card, verify a payment, refund a payment.",
            "homepage": "https://smithery.ai/server/@stripe/payments-mcp",
            "iconUrl": "https://smithery.ai/icons/stripe.png",
            "useCount": 12345,
            "remote": True,
            "isDeployed": True,
            "createdAt": "2026-01-12T00:00:00Z",
        },
        {
            # We deliberately choose a description that contains the
            # literal "inference" cue so the embedding-based capability
            # classifier reliably binds it to ``ai_inference``. The
            # original "@upstash/context7-mcp" description "Search and
            # run image, video, audio AI apps" did *not* bind in
            # practice because the matcher needs a stronger semantic
            # anchor than "AI apps" — a real-world recall gap worth
            # owning honestly in the fixture.
            "qualifiedName": "@upstash/context7-mcp",
            "displayName": "Context7 Inference",
            "description": "Run AI inference and lookup models across image, video, and audio.",
            "homepage": "https://smithery.ai/server/@upstash/context7-mcp",
            "useCount": 1000,
            "remote": True,
            "isDeployed": True,
            "createdAt": "2026-02-01T00:00:00Z",
        },
        {
            # Junk by token name — should be dropped pre-inference.
            "qualifiedName": "ai.smithery/arjunkmrm-py-test-0",
            "displayName": "py-test 0",
            "description": "A test server for development.",
            "homepage": "",
            "useCount": 0,
            "remote": False,
            "isDeployed": False,
            "createdAt": "",
        },
        {
            # No capability synonym matches — also dropped.
            "qualifiedName": "@noisemaker/random",
            "displayName": "Random Words",
            "description": "An entirely random server with no useful capability.",
            "homepage": "https://smithery.ai/server/@noisemaker/random",
            "useCount": 5,
            "remote": False,
            "isDeployed": True,
            "createdAt": "",
        },
    ],
    "pagination": {
        "currentPage": 1,
        "pageSize": 50,
        "totalPages": 2,
        "totalCount": 5,
    },
}

PAGE_2 = {
    "servers": [
        {
            "qualifiedName": "@scrapehub/mcp",
            "displayName": "ScrapeHub",
            "description": "Scrape any website and extract structured data.",
            "homepage": "https://smithery.ai/server/@scrapehub/mcp",
            "useCount": 78,
            "remote": True,
            "isDeployed": True,
            "createdAt": "2026-03-15T00:00:00Z",
        },
    ],
    "pagination": {
        "currentPage": 2,
        "pageSize": 50,
        "totalPages": 2,
        "totalCount": 5,
    },
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


class SmitherySourceTests(unittest.TestCase):
    def test_skips_when_token_is_empty(self) -> None:
        """The dispatcher's ``requires_token`` already guards this in
        production, but the source's :meth:`search` must also no-op on
        an empty token so direct callers (audits, scripts) can't
        accidentally hit the upstream unauthenticated."""

        source = sm.SmitherySource(token="")
        # If we *did* call urlopen, the test would explode because
        # there's no fake registered. We deliberately don't patch.
        candidates = source.search(
            capabilities=set(), task_description="payment"
        )
        self.assertEqual(candidates, [])

    def test_skips_when_token_is_whitespace_only(self) -> None:
        source = sm.SmitherySource(token="   ")
        candidates = source.search(
            capabilities=set(), task_description="payment"
        )
        self.assertEqual(candidates, [])

    def test_paginates_and_normalises_real_shape(self) -> None:
        source = sm.SmitherySource(
            token="fake-token-deadbeef", max_pages=2, page_size=50
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            sm.request, "urlopen", _fake_urlopen_factory([PAGE_1, PAGE_2])
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )

        ids = sorted(c.id for c in candidates)
        self.assertIn("stripe-payments-mcp", ids)
        self.assertIn("upstash-context7-mcp", ids)
        self.assertIn("scrapehub-mcp", ids)
        # Junk-by-token name is dropped before capability inference runs.
        self.assertNotIn("ai-smithery-arjunkmrm-py-test-0", ids)
        # No capability synonym matches → dropped.
        self.assertNotIn("noisemaker-random", ids)

        stripe = next(c for c in candidates if c.id == "stripe-payments-mcp")
        self.assertEqual(stripe.provider_type, "mcp_server")
        self.assertEqual(stripe.source, "smithery")
        self.assertEqual(stripe.vendor, "stripe")  # extracted from "@stripe/..."
        self.assertEqual(
            [obs.source_id for obs in stripe.observations],
            ["smithery"],
        )
        # Fix-3 contract: Smithery is a vendor-curated MCP registry,
        # so candidates default to ``registered_in_directory`` (one
        # tier above ``unverified``). This is the qualification level
        # the gap-report verification gate accepts; the prior default
        # of ``unverified`` was the root cause of the gift-goal
        # "no agents discovered" failure mode where high-quality
        # Smithery entries (Perplexity MCP @ score 0.93, Linkup MCP)
        # were dropped before they reached the user.
        self.assertEqual(stripe.verification_status, "registered_in_directory")

    def test_capability_filter_applied_when_capabilities_requested(self) -> None:
        source = sm.SmitherySource(
            token="fake-token", max_pages=1, page_size=50
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            sm.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(
                capabilities={"payment_authorization"},
                task_description="charge a card",
            )

        ids = [c.id for c in candidates]
        self.assertIn("stripe-payments-mcp", ids)
        self.assertNotIn("upstash-context7-mcp", ids)

    def test_handles_empty_payload_gracefully(self) -> None:
        source = sm.SmitherySource(token="fake-token", max_pages=1)
        with patch.object(sm.request, "urlopen", _fake_urlopen_factory([{}])):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        self.assertEqual(candidates, [])

    def test_handles_network_error_silently(self) -> None:
        """When the upstream returns 401 / 403 / 5xx or the network
        flakes entirely, the source returns ``[]`` instead of raising
        — the dispatcher then logs the scout as ``ok / 0 candidates``
        and the rest of the /goal request continues."""

        source = sm.SmitherySource(token="fake-token", max_pages=1)

        def boom(req, timeout=None):  # noqa: ARG001
            raise OSError("network down")

        with patch.object(sm.request, "urlopen", boom):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        self.assertEqual(candidates, [])

    def test_handles_401_silently(self) -> None:
        """Specific check for HTTP 401 — ``urllib.error.HTTPError`` is a
        subclass of ``OSError`` so the same except clause catches it,
        but we test it explicitly to lock down the contract that auth
        failures don't propagate up."""

        import io as _io
        from urllib import error as urllib_error

        source = sm.SmitherySource(token="invalid-token", max_pages=1)

        def auth_fail(req, timeout=None):  # noqa: ARG001
            # The ``fp`` arg is a real file-like so HTTPError's lazy
            # tempfile machinery has something to close — avoids a
            # benign ResourceWarning during the test run.
            raise urllib_error.HTTPError(
                "https://api.smithery.ai/servers",
                401,
                "Unauthorized",
                {},
                _io.BytesIO(b""),
            )

        with patch.object(sm.request, "urlopen", auth_fail):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        self.assertEqual(candidates, [])

    def test_authorization_header_is_sent(self) -> None:
        """Sanity check that the Bearer token actually makes it onto
        the wire. Captures the request and inspects its headers."""

        source = sm.SmitherySource(token="my-secret-key", max_pages=1)
        captured: list[dict] = []

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            # Headers on a urllib Request are case-mangled to
            # ``Header-name`` by ``add_header``; mirror that on lookup.
            captured.append(dict(req.header_items()))
            return _FakeResponse({"servers": [], "pagination": {}})

        with patch.object(sm.request, "urlopen", fake_urlopen):
            source.search(
                capabilities=set(), task_description="anything"
            )

        self.assertEqual(len(captured), 1)
        # urllib normalises header names to title-case.
        self.assertIn("Authorization", captured[0])
        self.assertEqual(captured[0]["Authorization"], "Bearer my-secret-key")

    def test_dedupes_repeated_qualified_name_across_pages(self) -> None:
        stripe_row = {
            "qualifiedName": "@stripe/payments-mcp",
            "displayName": "Stripe Payments MCP",
            "description": "Charge a card, verify a payment, refund a payment.",
            "homepage": "https://smithery.ai/server/@stripe/payments-mcp",
            "useCount": 1,
            "remote": True,
            "isDeployed": True,
            "createdAt": "2026-01-12T00:00:00Z",
        }
        page_a = {
            "servers": [stripe_row],
            "pagination": {
                "currentPage": 1,
                "pageSize": 50,
                "totalPages": 2,
                "totalCount": 1,
            },
        }
        page_b = {
            "servers": [stripe_row],
            "pagination": {
                "currentPage": 2,
                "pageSize": 50,
                "totalPages": 2,
                "totalCount": 1,
            },
        }
        source = sm.SmitherySource(token="fake-token", max_pages=2)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            sm.request, "urlopen", _fake_urlopen_factory([page_a, page_b])
        ):
            candidates = source.search(
                capabilities=set(), task_description="payment"
            )
        self.assertEqual(
            sum(1 for c in candidates if c.id == "stripe-payments-mcp"),
            1,
        )

    def test_stops_paginating_when_total_pages_reached(self) -> None:
        only_page = {
            "servers": [
                {
                    "qualifiedName": "@stripe/payments-mcp",
                    "displayName": "Stripe Payments MCP",
                    "description": "Charge a card, verify a payment, refund a payment.",
                    "homepage": "https://smithery.ai/server/@stripe/payments-mcp",
                    "useCount": 1,
                    "remote": True,
                    "isDeployed": True,
                    "createdAt": "2026-01-12T00:00:00Z",
                },
            ],
            "pagination": {
                "currentPage": 1,
                "pageSize": 50,
                "totalPages": 1,
                "totalCount": 1,
            },
        }
        source = sm.SmitherySource(token="fake-token", max_pages=5)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            sm.request, "urlopen", _fake_urlopen_factory([only_page])
        ):
            candidates = source.search(
                capabilities=set(), task_description="payment"
            )
        self.assertEqual(len(candidates), 1)


class SmitheryVendorExtractionTests(unittest.TestCase):
    def test_vendor_from_at_prefixed_qualified_name(self) -> None:
        self.assertEqual(
            sm._vendor_from_qualified_name("@upstash/context7-mcp"),
            "upstash",
        )

    def test_vendor_from_dotted_namespace(self) -> None:
        # ``ai.smithery/foo`` → first segment looks like a registrable
        # domain so the second slash-segment ("foo") is the vendor.
        self.assertEqual(
            sm._vendor_from_qualified_name("ai.smithery/foo-bar"),
            "foo-bar",
        )

    def test_vendor_from_simple_namespace(self) -> None:
        self.assertEqual(
            sm._vendor_from_qualified_name("upstash/context7"),
            "upstash",
        )

    def test_vendor_from_empty_qualified_name(self) -> None:
        self.assertEqual(sm._vendor_from_qualified_name(""), "")


class SmitheryJunkFilterIntegrationTests(unittest.TestCase):
    def test_school_project_qualified_name_is_dropped(self) -> None:
        page = {
            "servers": [
                {
                    "qualifiedName": "ai.smithery/aicastle-school-openai-api-agent-project",
                    "displayName": "OpenAI API School Project",
                    "description": "Compare prices for school project.",
                    "homepage": "",
                    "useCount": 0,
                    "remote": False,
                    "isDeployed": False,
                    "createdAt": "",
                },
            ],
            "pagination": {
                "currentPage": 1,
                "pageSize": 50,
                "totalPages": 1,
                "totalCount": 1,
            },
        }
        source = sm.SmitherySource(token="fake-token", max_pages=1)
        with patch.object(
            sm.request, "urlopen", _fake_urlopen_factory([page])
        ):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        # Same school-project filter that the official-registry source
        # uses — the shared ``mcp_publication_quality`` module makes
        # this reuse mechanical.
        self.assertEqual(candidates, [])


class SmitheryCorroborationFilterTests(unittest.TestCase):
    """Sprint 2 corroboration-or-drop filter (S2-NEW-2-smithery).

    Mirrors the Moltbook policy: a Smithery server is only worth
    surfacing when SOMETHING from the upstream API signals real
    adoption / reachability. Without ``isDeployed`` / ``useCount`` /
    a plausible homepage, the entry is just self-published noise.
    """

    @staticmethod
    def _row(**overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "qualifiedName": "@stripe/payments-mcp",
            "displayName": "Stripe Payments MCP",
            "description": "Charge a card, verify a payment, refund a payment.",
            # Default to NO corroboration; each test sets the
            # specific signal it's exercising.
            "homepage": "",
            "isDeployed": False,
            "useCount": 0,
        }
        base.update(overrides)
        return base

    def _run_with_row(
        self, row: dict[str, object]
    ) -> list[object]:
        page = {"servers": [row], "pagination": {"totalPages": 1}}
        responses = [json.dumps(page).encode("utf-8")]
        urlopen = patch.object(
            sm.request,
            "urlopen",
            lambda req, timeout=None: io.BytesIO(responses.pop(0))
            if responses
            else io.BytesIO(b'{"servers":[]}'),
        )
        source = sm.SmitherySource(token="t")
        with extended_capability_index(_TEST_EXTRA_CAPS), urlopen:
            return source.search(
                capabilities=set(),
                task_description="payment authorization",
            )

    def test_drops_when_no_corroboration_signal(self) -> None:
        candidates = self._run_with_row(self._row())
        self.assertEqual(candidates, [])

    def test_keeps_when_is_deployed(self) -> None:
        candidates = self._run_with_row(self._row(isDeployed=True))
        self.assertEqual(len(candidates), 1)

    def test_keeps_when_use_count_positive(self) -> None:
        candidates = self._run_with_row(self._row(useCount=42))
        self.assertEqual(len(candidates), 1)

    def test_keeps_when_https_homepage_present(self) -> None:
        candidates = self._run_with_row(
            self._row(homepage="https://example.com/server")
        )
        self.assertEqual(len(candidates), 1)

    def test_drops_when_only_http_homepage(self) -> None:
        # http (non-https) is not enough — likely a placeholder /
        # abandoned domain. Same standard as Moltbook's filter.
        candidates = self._run_with_row(
            self._row(homepage="http://example.com/server")
        )
        self.assertEqual(candidates, [])

    def test_drops_when_use_count_zero(self) -> None:
        candidates = self._run_with_row(self._row(useCount=0))
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
