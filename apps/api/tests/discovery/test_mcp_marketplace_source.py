"""Tests for ``MCPMarketplaceSource``.

We monkey-patch ``urllib.request.urlopen`` (via the source module's
``request`` binding) so the tests never touch the network. Fixtures
mirror the real shape we observed at
``https://mcp-marketplace.io/api/registry/search`` on 2026-05-13.
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

from planmyagents_api.discovery.sources import mcp_marketplace as mm  # noqa: E402

from tests._capability_index_fixtures import extended_capability_index  # noqa: E402

# Source mechanics tests below use realistic vendor descriptions
# (Stripe payments, inference.sh) whose capabilities aren't in the
# 5-slug shipped registry. We extend the index per-test with the
# capability slugs the fixtures imply, so the source tests verify
# source behaviour (dedup, vendor extraction, junk filter)
# independent of registry composition.
_TEST_EXTRA_CAPS = ["payment_authorization", "ai_inference"]

# Marketplace returns one row per server (no version envelope, unlike
# the official MCP registry). Pagination is via ``page=1..pages``.
PAGE_1 = {
    "results": [
        {
            "name": "Stripe Payments MCP",
            "slug": "stripe-payments-mcp",
            "tagline": "Charge a card, verify a payment, refund a payment.",
            "url": "https://mcp-marketplace.io/server/stripe-payments-mcp",
            "category": "Finance",
            "mode": "remote",
            "free": False,
            "securityScore": 9,
            "rating": 4.6,
            "githubStars": 1234,
            "toolCount": 8,
            "installCommand": "claude mcp add stripe -- npx -y @stripe/mcp@1",
        },
        {
            "name": "inference.sh",
            "slug": "ac-inference-sh-mcp",
            "tagline": "Search and run image, video, audio AI apps; lookup models.",
            "url": "https://mcp-marketplace.io/server/ac-inference-sh-mcp",
            "category": "AI & ML",
            "mode": "remote",
            "free": True,
            "securityScore": 8,
            "rating": None,
            "githubStars": 50,
            "toolCount": 15,
            "installCommand": "claude mcp add inference -- npx -y @inference-sh/mcp",
        },
        {
            # Junk by name token — should never reach the candidate list.
            "name": "Demo MCP",
            "slug": "demo-mcp",
            "tagline": "A demo server that does nothing in particular.",
            "url": "https://mcp-marketplace.io/server/demo-mcp",
            "category": "Productivity",
            "mode": "local",
            "free": True,
            "securityScore": None,
            "rating": None,
            "githubStars": None,
            "toolCount": 0,
            "installCommand": "",
        },
        {
            # No capability synonym matches — should also be dropped.
            "name": "Random Words",
            "slug": "noisemaker-random",
            "tagline": "An entirely random server with no useful capability.",
            "url": "https://mcp-marketplace.io/server/noisemaker-random",
            "category": "Other",
            "mode": "local",
            "free": True,
            "securityScore": 7,
            "rating": None,
            "githubStars": 3,
            "toolCount": 1,
            "installCommand": "",
        },
    ],
    "total": 5,
    "page": 1,
    "pages": 2,
    "limit": 50,
}

PAGE_2 = {
    "results": [
        {
            "name": "ScrapeHub",
            "slug": "scrapehub-mcp",
            "tagline": "Scrape any website and extract structured data.",
            "url": "https://mcp-marketplace.io/server/scrapehub-mcp",
            "category": "Search & Web",
            "mode": "remote",
            "free": False,
            "securityScore": 6,
            "rating": 4.0,
            "githubStars": 78,
            "toolCount": 4,
            "installCommand": "claude mcp add scrape -- npx -y @scrapehub/mcp",
        },
    ],
    "total": 5,
    "page": 2,
    "pages": 2,
    "limit": 50,
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


class MCPMarketplaceSourceTests(unittest.TestCase):
    def test_paginates_and_normalises_real_shape(self) -> None:
        source = mm.MCPMarketplaceSource(max_pages=2, page_size=50)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mm.request, "urlopen", _fake_urlopen_factory([PAGE_1, PAGE_2])
        ):
            candidates = source.search(
                capabilities=set(), task_description="test query"
            )

        ids = sorted(c.id for c in candidates)
        self.assertIn("stripe-payments-mcp", ids)
        self.assertIn("ac-inference-sh-mcp", ids)
        self.assertIn("scrapehub-mcp", ids)
        # Junk-by-token-name is dropped before capability inference runs.
        self.assertNotIn("demo-mcp", ids)
        # No capability synonym matches → dropped.
        self.assertNotIn("noisemaker-random", ids)

        stripe = next(c for c in candidates if c.id == "stripe-payments-mcp")
        self.assertEqual(stripe.provider_type, "mcp_server")
        self.assertEqual(stripe.source, "mcp_marketplace")
        self.assertEqual(
            [obs.source_id for obs in stripe.observations],
            ["mcp_marketplace"],
        )
        # vendor_url + evidence_url should both point at the
        # Marketplace listing page so the engineer drawer can open it.
        self.assertEqual(
            stripe.vendor_url,
            "https://mcp-marketplace.io/server/stripe-payments-mcp",
        )
        # Fix-3 contract: MCP Marketplace is a vendor-curated
        # registry, so candidates default to
        # ``registered_in_directory``. Without this default, every
        # Marketplace-discovered MCP was dropped by the gap-report
        # qualification gate (which used to require
        # ``capability_verified``) — including obvious gift-goal
        # matches like Walmart MCP and Forage Shopping.
        self.assertEqual(stripe.verification_status, "registered_in_directory")

    def test_capability_filter_applied_when_capabilities_requested(self) -> None:
        source = mm.MCPMarketplaceSource(max_pages=1, page_size=50)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mm.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(
                capabilities={"payment_authorization"},
                task_description="charge a card",
            )

        ids = [c.id for c in candidates]
        self.assertIn("stripe-payments-mcp", ids)
        # ``inference.sh`` has no payment-related capability.
        self.assertNotIn("ac-inference-sh-mcp", ids)

    def test_handles_empty_payload_gracefully(self) -> None:
        source = mm.MCPMarketplaceSource(max_pages=1)
        with patch.object(mm.request, "urlopen", _fake_urlopen_factory([{}])):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        self.assertEqual(candidates, [])

    def test_handles_network_error_silently(self) -> None:
        source = mm.MCPMarketplaceSource(max_pages=1)

        def boom(req, timeout=None):  # noqa: ARG001
            raise OSError("network down")

        with patch.object(mm.request, "urlopen", boom):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        self.assertEqual(candidates, [])

    def test_dedupes_repeated_slug_across_pages(self) -> None:
        """Marketplace pagination occasionally re-ranks results on the
        next page; if the same slug shows up twice, we keep the first
        occurrence and silently drop the duplicate so the index never
        sees the same server twice from a single fetch."""

        # Use the same long tagline as the PAGE_1 fixture above — it
        # contains the synonyms ("verify a payment", "refund a
        # payment") that trip the capability classifier on the
        # extended index. A shortened tagline like "Charge a card."
        # is too thin for the embedding-based matcher to bind to
        # ``payment_authorization``, which would mask the actual
        # behaviour we want to test (dedupe).
        stripe_row = {
            "name": "Stripe Payments MCP",
            "slug": "stripe-payments-mcp",
            "tagline": "Charge a card, verify a payment, refund a payment.",
            "url": "https://mcp-marketplace.io/server/stripe-payments-mcp",
            "category": "Finance",
            "mode": "remote",
            "free": False,
            "securityScore": 9,
        }
        page_a = {
            "results": [stripe_row],
            "total": 1,
            "page": 1,
            "pages": 2,
            "limit": 50,
        }
        page_b = {
            "results": [stripe_row],
            "total": 1,
            "page": 2,
            "pages": 2,
            "limit": 50,
        }
        source = mm.MCPMarketplaceSource(max_pages=2)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mm.request, "urlopen", _fake_urlopen_factory([page_a, page_b])
        ):
            candidates = source.search(
                capabilities=set(), task_description="payment"
            )
        self.assertEqual(
            sum(1 for c in candidates if c.id == "stripe-payments-mcp"),
            1,
        )

    def test_stops_paginating_when_total_pages_reached(self) -> None:
        """Marketplace tells us how many pages exist; we trust it and
        stop paginating early. This avoids burning the per-scout
        budget on guaranteed-empty pages."""

        only_page = {
            "results": [
                {
                    "name": "Stripe Payments MCP",
                    "slug": "stripe-payments-mcp",
                    # Long-form tagline so the embedding-based
                    # capability classifier actually binds — see the
                    # comment above the dedup test for why this matters.
                    "tagline": "Charge a card, verify a payment, refund a payment.",
                    "url": "https://mcp-marketplace.io/server/stripe-payments-mcp",
                    "category": "Finance",
                    "mode": "remote",
                    "free": False,
                    "securityScore": 9,
                },
            ],
            "total": 1,
            "page": 1,
            "pages": 1,
            "limit": 50,
        }
        # Configure max_pages=5 but the upstream reports pages=1, so we
        # should stop after one fetch. The fake_urlopen iterator only
        # has one entry; if we called it a second time we'd raise
        # StopIteration. The test passes precisely because that doesn't
        # happen.
        source = mm.MCPMarketplaceSource(max_pages=5)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mm.request, "urlopen", _fake_urlopen_factory([only_page])
        ):
            candidates = source.search(
                capabilities=set(), task_description="payment"
            )
        self.assertEqual(len(candidates), 1)

    def test_query_falls_back_to_capability_when_no_task(self) -> None:
        """When ``task_description`` is empty, the source should still
        send a ``q`` param derived from a capability so the upstream's
        semantic search has something to rank on. We assert this by
        capturing the constructed URL."""

        source = mm.MCPMarketplaceSource(max_pages=1)
        captured_urls: list[str] = []

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured_urls.append(req.full_url)
            return _FakeResponse({"results": [], "total": 0, "page": 1, "pages": 1})

        with patch.object(mm.request, "urlopen", fake_urlopen):
            source.search(
                capabilities={"payment_authorization"}, task_description=""
            )

        self.assertEqual(len(captured_urls), 1)
        # Capability slug should be tokenised into the q param,
        # underscore-replaced.
        self.assertIn("q=payment+authorization", captured_urls[0])


class MarketplaceVendorExtractionTests(unittest.TestCase):
    def test_vendor_from_reverse_domain_slug(self) -> None:
        self.assertEqual(
            mm._vendor_from_slug("io-github-gjeltep-app-store-connect-mcp"),
            "gjeltep",
        )

    def test_vendor_from_bare_slug_is_empty(self) -> None:
        self.assertEqual(mm._vendor_from_slug("memory"), "")

    def test_vendor_from_empty_slug_is_empty(self) -> None:
        self.assertEqual(mm._vendor_from_slug(""), "")


class MarketplaceJunkFilterIntegrationTests(unittest.TestCase):
    """End-to-end: confirm the shared MCP-publication junk filter runs
    on Marketplace slugs the same way it does on official-registry
    names."""

    def test_school_project_slug_is_dropped(self) -> None:
        page = {
            "results": [
                {
                    "name": "OpenAI API School Project",
                    # Same shape the official-registry filter rejects.
                    "slug": "aicastle-school-openai-api-agent-project",
                    "tagline": "Compare prices for school project.",
                    "url": "https://mcp-marketplace.io/server/aicastle-school-...",
                    "category": "Education & Research",
                    "mode": "local",
                    "free": True,
                    "securityScore": 5,
                },
            ],
            "total": 1,
            "page": 1,
            "pages": 1,
        }
        source = mm.MCPMarketplaceSource(max_pages=1)
        with patch.object(
            mm.request, "urlopen", _fake_urlopen_factory([page])
        ):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        # Even though the tagline contains "compare" / "prices", the
        # shared junk filter rejects "school" as a token before any
        # capability inference runs. This is exactly the school-project-
        # as-fare-comparison failure mode the filter exists to prevent.
        self.assertEqual(candidates, [])


class MCPMarketplaceCorroborationFilterTests(unittest.TestCase):
    """Sprint 2 corroboration-or-drop filter (S2-NEW-2-mcpmkt).

    Mirrors the Smithery + Moltbook policy: a Marketplace listing
    is only worth surfacing when SOMETHING from the upstream API
    proves real adoption / installability / upstream-side review.
    Without ``installCommand`` / ``githubStars`` / ``rating`` /
    ``toolCount`` / ``securityScore``, the entry is just a
    placeholder and we drop it.
    """

    @staticmethod
    def _row(**overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "name": "Stripe Payments MCP",
            "slug": "stripe-payments-mcp",
            "tagline": "Charge a card, verify a payment, refund a payment.",
            "url": "https://mcp-marketplace.io/server/stripe-payments-mcp",
            "category": "Finance",
            "mode": "remote",
            "free": False,
            # Default to NO corroboration; each test sets the
            # specific signal it's exercising. ``free`` doesn't
            # corroborate (it's just a tag).
        }
        base.update(overrides)
        return base

    def _run_with_row(
        self, row: dict[str, object]
    ) -> list[object]:
        page = {
            "results": [row],
            "total": 1,
            "page": 1,
            "pages": 1,
            "limit": 50,
        }
        responses = [json.dumps(page).encode("utf-8")]

        def _fake_urlopen(req: object, timeout: float = 0):  # noqa: ARG001
            data = responses.pop(0) if responses else b'{"results":[]}'
            return io.BytesIO(data)

        urlopen = patch.object(mm.request, "urlopen", _fake_urlopen)
        source = mm.MCPMarketplaceSource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), urlopen:
            return source.search(
                capabilities=set(),
                task_description="payment authorization",
            )

    def test_drops_when_no_corroboration_signal(self) -> None:
        candidates = self._run_with_row(self._row())
        self.assertEqual(candidates, [])

    def test_keeps_when_install_command_set(self) -> None:
        candidates = self._run_with_row(
            self._row(installCommand="npx -y stripe-mcp")
        )
        self.assertEqual(len(candidates), 1)

    def test_keeps_when_github_stars_positive(self) -> None:
        candidates = self._run_with_row(self._row(githubStars=42))
        self.assertEqual(len(candidates), 1)

    def test_keeps_when_rating_positive(self) -> None:
        candidates = self._run_with_row(self._row(rating=4.5))
        self.assertEqual(len(candidates), 1)

    def test_keeps_when_tool_count_positive(self) -> None:
        candidates = self._run_with_row(self._row(toolCount=3))
        self.assertEqual(len(candidates), 1)

    def test_keeps_when_security_score_positive(self) -> None:
        candidates = self._run_with_row(self._row(securityScore=8))
        self.assertEqual(len(candidates), 1)

    def test_drops_when_github_stars_zero(self) -> None:
        candidates = self._run_with_row(self._row(githubStars=0))
        self.assertEqual(candidates, [])

    def test_drops_when_only_free_tag(self) -> None:
        # ``free`` is a category tag, not adoption proof. Don't
        # accidentally promote it to a corroboration signal.
        candidates = self._run_with_row(self._row(free=True))
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
