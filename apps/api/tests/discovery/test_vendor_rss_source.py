"""Tests for `VendorRssSource`. HTTP is mocked end-to-end. Fixtures cover
both RSS 2.0 and Atom feed shapes."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.sources import vendor_rss as vrss  # noqa: E402

RSS_2_0 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Acme AI</title>
    <link>https://acme.example/blog</link>
    <description>Acme news</description>
    <item>
      <title>Acme launches MCP server for payment authorization</title>
      <link>https://acme.example/blog/mcp-billing</link>
      <description>Charge a card via Model Context Protocol.</description>
    </item>
    <item>
      <title>Acme Q3 earnings report</title>
      <link>https://acme.example/blog/earnings</link>
      <description>Revenue grew 30%.</description>
    </item>
    <item>
      <title>Introducing our new AI agent framework for web scraping</title>
      <link>https://acme.example/blog/agent-framework</link>
      <description>Tool use and function calling out of the box; scrape any page.</description>
    </item>
  </channel>
</rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Beta Vendor</title>
  <entry>
    <title>Beta releases A2A agent.json spec for contact search</title>
    <link href="https://beta.example/news/a2a-spec"/>
    <summary>Agent-to-agent discovery to find email contacts via agent.json.</summary>
  </entry>
  <entry>
    <title>Beta releases new logo</title>
    <link href="https://beta.example/news/logo"/>
    <summary>Refreshing our brand identity.</summary>
  </entry>
</feed>"""


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._buf = io.BytesIO(body.encode("utf-8"))

    def __enter__(self):
        return self._buf

    def __exit__(self, *_):
        self._buf.close()


def _build_fake_urlopen(url_to_body: dict[str, str]):
    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        url = req.full_url
        if url in url_to_body:
            return _FakeResponse(url_to_body[url])
        # Empty body to test graceful handling of empty/missing feeds.
        return _FakeResponse("")

    return fake_urlopen


class VendorRssSourceTests(unittest.TestCase):
    def test_emits_only_keyword_matching_entries_from_rss_2_0(self) -> None:
        feed_url = "https://acme.example/feed.xml"
        source = vrss.VendorRssSource(feed_urls=[feed_url])
        with patch.object(vrss.request, "urlopen", _build_fake_urlopen({feed_url: RSS_2_0})):
            candidates = source.search(capabilities=set(), task_description="")

        names = sorted(c.display_name for c in candidates)
        # Both items have launch verbs ("launches", "Introducing"), an
        # agent noun, AND an explicit capability word — they pass the
        # classifier. The earnings post fails the keyword filter outright.
        self.assertIn("Acme launches MCP server for payment authorization", names)
        self.assertIn("Introducing our new AI agent framework for web scraping", names)
        self.assertNotIn("Acme Q3 earnings report", names)

    def test_emits_keyword_matching_entries_from_atom(self) -> None:
        feed_url = "https://beta.example/atom.xml"
        source = vrss.VendorRssSource(feed_urls=[feed_url])
        with patch.object(vrss.request, "urlopen", _build_fake_urlopen({feed_url: ATOM})):
            candidates = source.search(capabilities=set(), task_description="")

        names = [c.display_name for c in candidates]
        self.assertIn("Beta releases A2A agent.json spec for contact search", names)
        self.assertNotIn("Beta releases new logo", names)

    def test_links_extracted_correctly_from_atom_href_attribute(self) -> None:
        feed_url = "https://beta.example/atom.xml"
        source = vrss.VendorRssSource(feed_urls=[feed_url])
        with patch.object(vrss.request, "urlopen", _build_fake_urlopen({feed_url: ATOM})):
            candidates = source.search(capabilities=set(), task_description="")
        spec_post = next(c for c in candidates if "A2A" in c.display_name)
        self.assertEqual(spec_post.vendor_url, "https://beta.example/news/a2a-spec")

    def test_capability_filter_applied(self) -> None:
        # See _capability_index_fixtures.py — payment_authorization
        # isn't in the shipped registry, so we extend the index for
        # this source-mechanics test only.
        from tests._capability_index_fixtures import extended_capability_index

        feed_url = "https://acme.example/feed.xml"
        source = vrss.VendorRssSource(feed_urls=[feed_url])
        with extended_capability_index(["payment_authorization"]), patch.object(
            vrss.request, "urlopen", _build_fake_urlopen({feed_url: RSS_2_0})
        ):
            candidates = source.search(
                capabilities={"payment_authorization"}, task_description=""
            )
        names = [c.display_name for c in candidates]
        self.assertIn("Acme launches MCP server for payment authorization", names)
        self.assertNotIn(
            "Introducing our new AI agent framework for web scraping", names
        )

    def test_dedupes_same_link_across_feeds(self) -> None:
        feed_a = "https://acme.example/feed.xml"
        feed_b = "https://acme.example/duplicate.xml"
        source = vrss.VendorRssSource(feed_urls=[feed_a, feed_b])
        with patch.object(
            vrss.request,
            "urlopen",
            _build_fake_urlopen({feed_a: RSS_2_0, feed_b: RSS_2_0}),
        ):
            candidates = source.search(capabilities=set(), task_description="")
        ids = [c.id for c in candidates]
        self.assertEqual(len(ids), len(set(ids)), "candidate ids must be unique across feeds")

    def test_handles_malformed_xml_silently(self) -> None:
        feed_url = "https://broken.example/feed"
        source = vrss.VendorRssSource(feed_urls=[feed_url])
        with patch.object(
            vrss.request,
            "urlopen",
            _build_fake_urlopen({feed_url: "<not really xml<<<"}),
        ):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(candidates, [])

    def test_handles_network_error_silently(self) -> None:
        source = vrss.VendorRssSource(feed_urls=["https://example/feed"])

        def boom(req, timeout=None):  # noqa: ARG001
            raise OSError("dns failure")

        with patch.object(vrss.request, "urlopen", boom):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(candidates, [])

    def test_strips_html_from_descriptions_before_keyword_match(self) -> None:
        # The point of this test is that HTML tags in the description
        # don't break keyword + capability extraction. The classifier
        # needs a launch-verb title to pass too, so use one.
        feed_url = "https://html.example/feed"
        body = """<rss version="2.0"><channel><item>
          <title>Introducing MCP server X for web scraping</title>
          <link>https://html.example/post</link>
          <description><![CDATA[<p>Scrape any <b>website</b> via the new MCP server.</p>]]></description>
        </item></channel></rss>"""
        source = vrss.VendorRssSource(feed_urls=[feed_url])
        with patch.object(vrss.request, "urlopen", _build_fake_urlopen({feed_url: body})):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(len(candidates), 1)


if __name__ == "__main__":
    unittest.main()
