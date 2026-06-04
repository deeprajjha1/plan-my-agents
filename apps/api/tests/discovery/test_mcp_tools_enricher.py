"""Tests for MCP source tool/docs passthrough and the live tools/list enricher."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.enrichers.mcp_tools import (
    McpToolProbeEnricher,
    StubTransport,
    _parse_tools_response,
)
from planmyagents_api.discovery.models import (
    CandidateCapability,
    DiscoveryCandidate,
)
from planmyagents_api.discovery.normalizer import normalize_candidate
from planmyagents_api.discovery.sources.mcp import _candidate_from_mcp_record


class McpRecordPassthroughTests(unittest.TestCase):
    def test_strings_are_capabilities_dicts_with_input_schema_are_tools(self) -> None:
        raw = _candidate_from_mcp_record(
            {
                "id": "demo-mcp",
                "name": "Demo MCP",
                "publisher": "Demo Inc",
                "homepage": "https://demo.example",
                "tools": [
                    "search_documents",
                    {
                        "name": "fetch_url",
                        "description": "Fetch a URL",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"url": {"type": "string"}},
                        },
                    },
                ],
            }
        )

        self.assertEqual(len(raw["capabilities"]), 1)
        self.assertEqual(raw["capabilities"][0]["id"], "search_documents")
        self.assertEqual(len(raw["tools"]), 1)
        self.assertEqual(raw["tools"][0]["name"], "fetch_url")

    def test_docs_passthrough(self) -> None:
        raw = _candidate_from_mcp_record(
            {
                "name": "x-mcp",
                "homepage": "https://x.example",
                "tools": ["q"],
                "docs": {
                    "setup_url": "https://x.example/docs",
                    "auth_method": "api_key",
                    "install_steps": ["npm i @x/mcp"],
                    "rate_limits": {"requests_per_minute": 30},
                },
            }
        )
        self.assertEqual(raw["docs"]["setup_url"], "https://x.example/docs")
        self.assertEqual(raw["docs"]["auth_method"], "api_key")
        self.assertEqual(raw["docs"]["rate_limits"]["requests_per_minute"], 30)

    def test_normaliser_lifts_mcp_tools_into_candidate(self) -> None:
        raw = _candidate_from_mcp_record(
            {
                "name": "y-mcp",
                "publisher": "Y",
                "homepage": "https://y.example",
                "tools": [
                    "echo",
                    {"name": "search", "description": "search", "inputSchema": {}},
                ],
            }
        )
        candidate = normalize_candidate(raw, source="curated_mcp")
        self.assertEqual(len(candidate.tools), 1)
        self.assertEqual(candidate.tools[0].name, "search")
        self.assertEqual(len(candidate.capabilities), 1)
        self.assertEqual(candidate.capabilities[0].id, "echo")


class McpToolProbeEnricherTests(unittest.TestCase):
    def _candidate(self, *, vendor_url: str, with_tools: bool = False) -> DiscoveryCandidate:
        return DiscoveryCandidate(
            id="demo",
            display_name="Demo",
            vendor="Demo",
            vendor_url=vendor_url,
            provider_type="mcp_server",
            capabilities=[CandidateCapability(id="search", confidence=0.5)],
            tools=[],
        )

    def test_skips_non_mcp_provider_types(self) -> None:
        candidate = DiscoveryCandidate(
            id="api",
            display_name="API",
            vendor="V",
            vendor_url="https://v.example",
            provider_type="api_provider",
            capabilities=[CandidateCapability(id="search", confidence=0.5)],
        )
        enricher = McpToolProbeEnricher(transport=StubTransport())
        out = enricher.enrich([candidate])
        self.assertEqual(out, [candidate])

    def test_skips_when_vendor_url_is_not_http(self) -> None:
        candidate = self._candidate(vendor_url="stdio://demo")
        enricher = McpToolProbeEnricher(transport=StubTransport())
        out = enricher.enrich([candidate])
        self.assertEqual(out[0].tools, [])

    def test_returns_candidate_unchanged_on_transport_failure(self) -> None:
        candidate = self._candidate(vendor_url="https://demo.example/mcp")
        enricher = McpToolProbeEnricher(transport=StubTransport(responses={}))
        out = enricher.enrich([candidate])
        self.assertEqual(out[0].tools, [])

    def test_populates_tools_on_success(self) -> None:
        candidate = self._candidate(vendor_url="https://demo.example/mcp")
        transport = StubTransport(
            responses={
                "https://demo.example/mcp": {
                    "jsonrpc": "2.0",
                    "id": "tools-list",
                    "result": {
                        "tools": [
                            {
                                "name": "search_documents",
                                "description": "Find docs",
                                "inputSchema": {"type": "object"},
                            },
                            {"name": "create_note"},
                        ]
                    },
                }
            }
        )
        enricher = McpToolProbeEnricher(transport=transport)
        out = enricher.enrich([candidate])

        self.assertEqual(len(out[0].tools), 2)
        names = {tool.name for tool in out[0].tools}
        self.assertEqual(names, {"search_documents", "create_note"})

    def test_skip_when_already_populated(self) -> None:
        from planmyagents_api.discovery.models import CandidateTool

        candidate = DiscoveryCandidate(
            id="demo",
            display_name="Demo",
            vendor="Demo",
            vendor_url="https://demo.example/mcp",
            provider_type="mcp_server",
            capabilities=[CandidateCapability(id="search", confidence=0.5)],
            tools=[CandidateTool(name="curated_tool")],
        )
        # Transport would fail loudly if reached; test asserts we don't reach it.
        enricher = McpToolProbeEnricher(transport=StubTransport())
        out = enricher.enrich([candidate])
        self.assertEqual([tool.name for tool in out[0].tools], ["curated_tool"])

    def test_handles_jsonrpc_error_envelope(self) -> None:
        candidate = self._candidate(vendor_url="https://demo.example/mcp")
        transport = StubTransport(
            responses={
                "https://demo.example/mcp": {
                    "jsonrpc": "2.0",
                    "id": "tools-list",
                    "error": {"code": -32601, "message": "Method not found"},
                }
            }
        )
        enricher = McpToolProbeEnricher(transport=transport)
        out = enricher.enrich([candidate])
        self.assertEqual(out[0].tools, [])


class ParseToolsResponseTests(unittest.TestCase):
    def test_drops_invalid_entries(self) -> None:
        body = {
            "result": {
                "tools": [
                    {"name": "ok"},
                    {},
                    "not a dict",
                    {"description": "missing name"},
                ]
            }
        }
        tools = _parse_tools_response(body)
        self.assertEqual([tool.name for tool in tools], ["ok"])

    def test_returns_empty_for_non_dict(self) -> None:
        self.assertEqual(_parse_tools_response("nonsense"), [])
        self.assertEqual(_parse_tools_response(None), [])
        self.assertEqual(_parse_tools_response({"result": "not a dict"}), [])


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
