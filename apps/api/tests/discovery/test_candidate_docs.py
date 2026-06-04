"""Tests for the docs / tools / skills enrichment fields on a discovery candidate."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.dedupe import merge_candidates
from planmyagents_api.discovery.models import (
    CandidateCapability,
    CandidateDocs,
    CandidateTool,
    DiscoveryCandidate,
    UsageExample,
)
from planmyagents_api.discovery.normalizer import candidate_from_registry, normalize_candidate
from planmyagents_api.discovery.store import JsonDiscoveryStore


class CandidateDocsTests(unittest.TestCase):
    def test_normaliser_reads_docs_tools_skills(self) -> None:
        candidate = normalize_candidate(
            {
                "id": "github-mcp",
                "display_name": "GitHub MCP",
                "vendor": "GitHub",
                "vendor_url": "https://github.com",
                "provider_type": "mcp_server",
                "capabilities": ["repo_search"],
                "docs": {
                    "setup_url": "https://github.com/modelcontextprotocol/servers#github",
                    "auth_method": "api_key",
                    "auth_scopes": ["repo", "read:user"],
                    "install_steps": [
                        "npm install -g @modelcontextprotocol/server-github",
                        "Set GITHUB_PERSONAL_ACCESS_TOKEN",
                    ],
                    "usage_examples": [
                        {
                            "title": "Search repos",
                            "language": "json",
                            "snippet": '{"tool":"search_repositories","args":{"query":"langchain"}}',
                        }
                    ],
                    "rate_limits": {"requests_per_minute": 60},
                },
                "tools": [
                    {
                        "name": "search_repositories",
                        "description": "Search GitHub repositories",
                        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
                    },
                    "create_issue",
                ],
                "openapi_url": "https://api.github.com/openapi.yaml",
            },
            source="curated",
        )

        self.assertEqual(candidate.docs.auth_method, "api_key")
        self.assertEqual(candidate.docs.auth_scopes, ["read:user", "repo"])
        self.assertEqual(len(candidate.docs.install_steps), 2)
        self.assertEqual(candidate.docs.rate_limit_requests_per_minute, 60)
        self.assertEqual(len(candidate.docs.usage_examples), 1)
        self.assertEqual(candidate.docs.usage_examples[0].language, "json")

        self.assertEqual(len(candidate.tools), 2)
        names = {tool.name for tool in candidate.tools}
        self.assertEqual(names, {"search_repositories", "create_issue"})

        self.assertEqual(candidate.openapi_url, "https://api.github.com/openapi.yaml")

    def test_to_registry_json_roundtrips_docs(self) -> None:
        candidate = DiscoveryCandidate(
            id="x-mcp",
            display_name="X MCP",
            vendor="X",
            vendor_url="https://x.example",
            provider_type="mcp_server",
            capabilities=[CandidateCapability(id="search", confidence=0.6)],
            docs=CandidateDocs(
                setup_url="https://x.example/docs",
                auth_method="oauth2",
                install_steps=["clone repo", "npm i"],
                usage_examples=[
                    UsageExample(title="hello", language="bash", snippet="echo hi"),
                ],
            ),
            tools=[CandidateTool(name="search", description="search docs")],
            skills=[CandidateTool(name="summarise")],
            openapi_url="https://x.example/openapi.json",
        )

        payload = candidate.to_registry_json()
        self.assertEqual(payload["docs"]["auth_method"], "oauth2")
        self.assertEqual(len(payload["tools"]), 1)
        self.assertEqual(payload["openapi_url"], "https://x.example/openapi.json")

        restored = candidate_from_registry(payload)
        self.assertEqual(restored.docs.auth_method, "oauth2")
        self.assertEqual(restored.tools[0].description, "search docs")
        self.assertEqual(restored.skills[0].name, "summarise")
        self.assertEqual(restored.openapi_url, "https://x.example/openapi.json")

    def test_public_summary_exposes_doc_signals(self) -> None:
        candidate = DiscoveryCandidate(
            id="x",
            display_name="X",
            vendor="X",
            vendor_url="https://x.example",
            provider_type="api_provider",
            capabilities=[CandidateCapability(id="search", confidence=0.5)],
            docs=CandidateDocs(auth_method="api_key", setup_url="https://x.example/docs"),
            tools=[CandidateTool(name="search"), CandidateTool(name="fetch")],
        )
        summary = candidate.to_public_summary()
        self.assertTrue(summary["docs_available"])
        self.assertEqual(summary["auth_method"], "api_key")
        self.assertEqual(summary["tool_count"], 2)
        self.assertEqual(summary["skill_count"], 0)

    def test_merge_unions_docs_tools_skills(self) -> None:
        existing = DiscoveryCandidate(
            id="m",
            display_name="M",
            vendor="m",
            vendor_url="https://m.example",
            provider_type="mcp_server",
            capabilities=[CandidateCapability(id="a", confidence=0.4)],
            docs=CandidateDocs(setup_url="https://m.example/docs"),
            tools=[CandidateTool(name="a", description="A from existing")],
        )
        incoming = DiscoveryCandidate(
            id="m",
            display_name="M",
            vendor="m",
            vendor_url="https://m.example",
            provider_type="mcp_server",
            capabilities=[CandidateCapability(id="a", confidence=0.6)],
            docs=CandidateDocs(auth_method="api_key", install_steps=["npm i m-mcp"]),
            tools=[CandidateTool(name="b", description="B from incoming")],
            skills=[CandidateTool(name="extract")],
            openapi_url="https://m.example/openapi.json",
        )

        merged = merge_candidates(existing, incoming)
        self.assertEqual(merged.docs.setup_url, "https://m.example/docs")
        self.assertEqual(merged.docs.auth_method, "api_key")
        self.assertEqual(merged.docs.install_steps, ["npm i m-mcp"])
        self.assertEqual({tool.name for tool in merged.tools}, {"a", "b"})
        self.assertEqual([skill.name for skill in merged.skills], ["extract"])
        self.assertEqual(merged.openapi_url, "https://m.example/openapi.json")

    def test_json_store_persists_docs(self) -> None:
        candidate = normalize_candidate(
            {
                "id": "y",
                "display_name": "Y",
                "vendor": "Y",
                "vendor_url": "https://y.example",
                "provider_type": "mcp_server",
                "capabilities": ["q"],
                "docs": {
                    "setup_url": "https://y.example/docs",
                    "auth_method": "none",
                    "install_steps": ["a", "b"],
                },
                "tools": [{"name": "q", "description": "query"}],
            },
            source="curated",
        )
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "store.json"
            store = JsonDiscoveryStore(path=store_path)
            store.save([candidate])
            payload = json.loads(store_path.read_text())
            self.assertEqual(
                payload["candidates"][0]["docs"]["setup_url"],
                "https://y.example/docs",
            )
            reloaded = store.load()
            self.assertEqual(reloaded[0].docs.install_steps, ["a", "b"])
            self.assertEqual(reloaded[0].tools[0].description, "query")


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
