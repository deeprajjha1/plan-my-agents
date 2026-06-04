from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.models import (
    CandidateCapability,
    CandidateTool,
    DiscoveryCandidate,
)
from planmyagents_api.eval.descriptor import (
    SOURCE_A2A,
    SOURCE_MCP,
    DescriptorReader,
    schema_for_capability,
)


def _mcp(tools: list[CandidateTool]) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id="mcp-1",
        display_name="MCP One",
        vendor="Vendor",
        vendor_url="https://example.com",
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
        tools=tools,
    )


class DescriptorReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reader = DescriptorReader()

    def test_mcp_tools_parsed_to_operations(self) -> None:
        cand = _mcp(
            [
                CandidateTool(
                    name="scrape_page",
                    description="Scrape a URL",
                    input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
                )
            ]
        )
        descriptor = self.reader.read(cand)
        self.assertEqual(descriptor.source_kind, SOURCE_MCP)
        self.assertEqual(len(descriptor.operations), 1)
        self.assertEqual(descriptor.operations[0].name, "scrape_page")
        self.assertIn("url", descriptor.operations[0].input_schema["properties"])

    def test_a2a_skills_parsed(self) -> None:
        cand = DiscoveryCandidate(
            id="a2a-1",
            display_name="A2A One",
            vendor="Vendor",
            vendor_url="https://example.com",
            provider_type="a2a_agent",
            capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
            skills=[CandidateTool(name="fetch", description="fetch a page")],
        )
        descriptor = self.reader.read(cand)
        self.assertEqual(descriptor.source_kind, SOURCE_A2A)
        self.assertEqual(descriptor.operations[0].name, "fetch")

    def test_missing_descriptor_is_empty(self) -> None:
        cand = _mcp([])  # no tools probed yet
        descriptor = self.reader.read(cand)
        self.assertTrue(descriptor.is_empty)

    def test_non_agentic_returns_none(self) -> None:
        cand = DiscoveryCandidate(
            id="api-1",
            display_name="API",
            vendor="Vendor",
            vendor_url="https://example.com",
            provider_type="api_provider",
            capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
        )
        self.assertIsNone(self.reader.read(cand))

    def test_unmapped_operation_recorded_not_fabricated(self) -> None:
        cand = _mcp(
            [
                CandidateTool(name="web_scraping_fetch"),  # shares web+scraping
                CandidateTool(name="delete_everything"),   # shares nothing known
            ]
        )
        descriptor = self.reader.read(cand)
        self.assertIn("delete_everything", descriptor.unmapped_operations)
        self.assertNotIn("web_scraping_fetch", descriptor.unmapped_operations)
        # The known capabilities are unchanged — no fabrication.
        self.assertEqual({c.id for c in cand.capabilities}, {"web_scraping"})

    def test_schema_for_capability_prefers_name_overlap(self) -> None:
        cand = _mcp(
            [
                CandidateTool(name="ping", input_schema={"properties": {"x": {}}}),
                CandidateTool(
                    name="web_scraping_tool",
                    input_schema={"properties": {"url": {"type": "string"}}},
                ),
            ]
        )
        descriptor = self.reader.read(cand)
        schema = schema_for_capability(descriptor, "web_scraping")
        self.assertIn("url", schema["properties"])


if __name__ == "__main__":
    unittest.main()
