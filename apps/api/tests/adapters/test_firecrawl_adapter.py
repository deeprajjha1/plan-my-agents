"""Tests for the Firecrawl ``web_scraping`` adapter."""

from __future__ import annotations

import os
import unittest
from typing import Any

from planmyagents_api.benchmark.baselines.firecrawl import (
    FirecrawlConfigurationError,
    FirecrawlScraper,
)
from planmyagents_api.benchmark.models import ProviderRequest


def _request(
    *,
    url: str = "https://example.com",
    formats: list[str] | None = None,
    extras: dict[str, Any] | None = None,
    idempotency_key: str = "wf:1:k",
) -> ProviderRequest:
    inputs: dict[str, Any] = {"url": url}
    if formats is not None:
        inputs["formats"] = formats
    if extras:
        inputs.update(extras)
    return ProviderRequest(
        capability="web_scraping",
        inputs=inputs,
        idempotency_key=idempotency_key,
    )


def _success_payload(
    *,
    markdown: str = "Example Domain content here.",
    title: str = "Example Domain",
    source_url: str = "https://example.com",
    links: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "success": True,
        "data": {
            "markdown": markdown,
            "html": "<html><body>...</body></html>",
            "metadata": {
                "title": title,
                "description": "An example.",
                "sourceURL": source_url,
            },
            "links": links if links is not None else [{"url": "https://www.iana.org/"}],
        },
    }


class _RecordingTransport:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, bytes, dict[str, str], float]] = []

    def __call__(
        self, url: str, body: bytes, headers: dict[str, str], timeout: float
    ) -> dict[str, Any]:
        self.calls.append((url, body, headers, timeout))
        return self.payload


# ---------------------------------------------------------------------------
# Configuration / safety gates
# ---------------------------------------------------------------------------


class FirecrawlConfigurationGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._preserved = os.environ.pop("FIRECRAWL_API_KEY", None)

    def tearDown(self) -> None:
        if self._preserved is not None:
            os.environ["FIRECRAWL_API_KEY"] = self._preserved
        else:
            os.environ.pop("FIRECRAWL_API_KEY", None)

    async def test_missing_key_raises_configuration_error(self) -> None:
        adapter = FirecrawlScraper()
        with self.assertRaises(FirecrawlConfigurationError):
            await adapter.execute(_request())

    async def test_with_key_runs(self) -> None:
        transport = _RecordingTransport(_success_payload())
        adapter = FirecrawlScraper(api_key="fc_test_abc", transport=transport)
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


class FirecrawlUrlValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transport = _RecordingTransport(_success_payload())
        self.adapter = FirecrawlScraper(
            api_key="fc_test_abc", transport=self.transport
        )

    async def test_empty_url_refused_locally(self) -> None:
        response = await self.adapter.execute(_request(url=""))
        self.assertFalse(response.succeeded)
        self.assertIn("url_required", response.error or "")
        self.assertEqual(len(self.transport.calls), 0)

    async def test_non_http_scheme_refused(self) -> None:
        response = await self.adapter.execute(_request(url="ftp://example.com/"))
        self.assertFalse(response.succeeded)
        self.assertIn("url_scheme_unsupported", response.error or "")
        # javascript: shouldn't make it to the transport either.
        response = await self.adapter.execute(
            _request(url="javascript:alert('xss')")
        )
        self.assertFalse(response.succeeded)
        self.assertEqual(len(self.transport.calls), 0)

    async def test_url_too_long_refused(self) -> None:
        long_url = "https://example.com/" + ("a" * 9000)
        response = await self.adapter.execute(_request(url=long_url))
        self.assertFalse(response.succeeded)
        self.assertIn("url_too_long", response.error or "")


# ---------------------------------------------------------------------------
# Request body shape
# ---------------------------------------------------------------------------


class FirecrawlRequestShapeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transport = _RecordingTransport(_success_payload())
        self.adapter = FirecrawlScraper(
            api_key="fc_test_abc", transport=self.transport
        )

    async def test_default_formats_is_markdown_only(self) -> None:
        await self.adapter.execute(_request())
        body = self.transport.calls[0][1].decode()
        self.assertIn('"formats": ["markdown"]', body)
        self.assertIn('"onlyMainContent": true', body)

    async def test_explicit_formats_propagate(self) -> None:
        await self.adapter.execute(_request(formats=["markdown", "html"]))
        body = self.transport.calls[0][1].decode()
        self.assertIn('"formats": ["markdown", "html"]', body)

    async def test_optional_options_propagate(self) -> None:
        await self.adapter.execute(
            _request(
                extras={
                    "include_tags": ["main", "article"],
                    "exclude_tags": ["nav", "footer"],
                    "wait_for": 2000,
                }
            )
        )
        body = self.transport.calls[0][1].decode()
        self.assertIn('"includeTags": ["main", "article"]', body)
        self.assertIn('"excludeTags": ["nav", "footer"]', body)
        self.assertIn('"waitFor": 2000', body)


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


class FirecrawlResponseNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_path_normalises_scrape(self) -> None:
        transport = _RecordingTransport(
            _success_payload(markdown="hello world this is content")
        )
        adapter = FirecrawlScraper(api_key="fc_test_abc", transport=transport)
        response = await adapter.execute(_request())
        self.assertTrue(response.succeeded)
        self.assertEqual(response.output["status"], "scraped")
        self.assertEqual(response.output["title"], "Example Domain")
        self.assertEqual(response.output["char_count"], 27)
        self.assertTrue(response.output["test_mode"])  # example.com host
        self.assertEqual(response.output["link_count"], 1)
        self.assertTrue(response.output["html_present"])

    async def test_test_mode_detected_for_invalid_tld(self) -> None:
        # An RFC-6761 .invalid host is not routable; treat as test.
        transport = _RecordingTransport(
            _success_payload(source_url="http://my-app.invalid/")
        )
        adapter = FirecrawlScraper(api_key="fc_test_abc", transport=transport)
        response = await adapter.execute(_request(url="http://my-app.invalid/"))
        self.assertTrue(response.output["test_mode"])

    async def test_test_mode_false_for_real_domain(self) -> None:
        transport = _RecordingTransport(
            _success_payload(source_url="https://www.firecrawl.dev/")
        )
        adapter = FirecrawlScraper(api_key="fc_test_abc", transport=transport)
        response = await adapter.execute(_request(url="https://www.firecrawl.dev/"))
        self.assertFalse(response.output["test_mode"])

    async def test_firecrawl_error_response_normalised(self) -> None:
        transport = _RecordingTransport(
            {"success": False, "error": "Page not found"}
        )
        adapter = FirecrawlScraper(api_key="fc_test_abc", transport=transport)
        response = await adapter.execute(_request())
        self.assertFalse(response.succeeded)
        self.assertIn("Page not found", response.error or "")

    async def test_transport_exception_surfaces_as_failed_response(self) -> None:
        def raising_transport(url, body, headers, timeout):  # noqa: ARG001
            raise TimeoutError("page took too long")

        adapter = FirecrawlScraper(
            api_key="fc_test_abc", transport=raising_transport
        )
        response = await adapter.execute(_request())
        self.assertFalse(response.succeeded)
        self.assertIn("TimeoutError", response.error or "")


# ---------------------------------------------------------------------------
# Cost projection
# ---------------------------------------------------------------------------


class FirecrawlCostEstimateTests(unittest.IsolatedAsyncioTestCase):
    async def test_estimate_uses_documented_per_page_cost(self) -> None:
        adapter = FirecrawlScraper(api_key="fc_test_abc")
        cost = await adapter.estimate_cost(_request())
        self.assertAlmostEqual(cost, 0.002, places=4)


if __name__ == "__main__":
    unittest.main()
