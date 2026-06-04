"""Tests for the OpenAPI enricher."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.enrichers.openapi import (
    OpenApiEnricher,
    StubOpenApiTransport,
    _operations_to_tools,
    _request_schema,
)
from planmyagents_api.discovery.models import (
    CandidateCapability,
    DiscoveryCandidate,
)


def _candidate(**overrides) -> DiscoveryCandidate:
    base = dict(
        id="x",
        display_name="X",
        vendor="X",
        vendor_url="https://x.example",
        provider_type="api_provider",
        capabilities=[CandidateCapability(id="search", confidence=0.5)],
        openapi_url="https://x.example/openapi.json",
        tools=[],
    )
    base.update(overrides)
    return DiscoveryCandidate(**base)


class OpenApiEnricherTests(unittest.TestCase):
    def test_skips_when_no_openapi_url(self) -> None:
        candidate = _candidate(openapi_url="")
        enricher = OpenApiEnricher(transport=StubOpenApiTransport(responses={}))
        out = enricher.enrich([candidate])
        self.assertEqual(out, [candidate])

    def test_skips_when_already_populated(self) -> None:
        from planmyagents_api.discovery.models import CandidateTool

        candidate = _candidate(tools=[CandidateTool(name="curated")])
        enricher = OpenApiEnricher(transport=StubOpenApiTransport(responses={}))
        out = enricher.enrich([candidate])
        self.assertEqual([tool.name for tool in out[0].tools], ["curated"])

    def test_returns_unchanged_on_transport_failure(self) -> None:
        candidate = _candidate()
        enricher = OpenApiEnricher(transport=StubOpenApiTransport(responses={}))
        out = enricher.enrich([candidate])
        self.assertEqual(out[0].tools, [])

    def test_populates_tools_from_minimal_spec(self) -> None:
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/search": {
                    "get": {
                        "operationId": "searchDocs",
                        "summary": "Search documents",
                        "parameters": [
                            {
                                "name": "q",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                    }
                },
                "/notes": {
                    "post": {
                        "operationId": "createNote",
                        "summary": "Create note",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"title": {"type": "string"}},
                                    }
                                }
                            },
                        },
                    }
                },
            },
        }
        candidate = _candidate()
        transport = StubOpenApiTransport(
            responses={"https://x.example/openapi.json": json.dumps(spec)}
        )
        enricher = OpenApiEnricher(transport=transport)
        out = enricher.enrich([candidate])

        names = sorted(tool.name for tool in out[0].tools)
        self.assertEqual(names, ["createNote", "searchDocs"])
        search = next(tool for tool in out[0].tools if tool.name == "searchDocs")
        self.assertEqual(search.description, "Search documents")
        self.assertEqual(search.input_schema["properties"]["q"]["type"], "string")
        self.assertEqual(search.input_schema["required"], ["q"])
        create = next(tool for tool in out[0].tools if tool.name == "createNote")
        self.assertEqual(create.input_schema["required"], ["body"])

    def test_synthesises_operation_id_when_missing(self) -> None:
        spec = {
            "paths": {
                "/users/{id}": {
                    "get": {"summary": "Get user"},
                }
            }
        }
        candidate = _candidate()
        transport = StubOpenApiTransport(
            responses={"https://x.example/openapi.json": json.dumps(spec)}
        )
        enricher = OpenApiEnricher(transport=transport)
        out = enricher.enrich([candidate])
        self.assertEqual([tool.name for tool in out[0].tools], ["get_users_id"])

    def test_max_operations_caps_output(self) -> None:
        spec = {
            "paths": {
                f"/p{i}": {"get": {"operationId": f"op{i}"}} for i in range(10)
            }
        }
        candidate = _candidate()
        transport = StubOpenApiTransport(
            responses={"https://x.example/openapi.json": json.dumps(spec)}
        )
        enricher = OpenApiEnricher(transport=transport, max_operations=3)
        out = enricher.enrich([candidate])
        self.assertEqual(len(out[0].tools), 3)


class OperationsToToolsTests(unittest.TestCase):
    def test_ignores_non_dict_paths(self) -> None:
        self.assertEqual(_operations_to_tools({"paths": "noop"}, max_operations=10), [])
        self.assertEqual(_operations_to_tools("noop", max_operations=10), [])

    def test_ignores_non_http_methods(self) -> None:
        spec = {"paths": {"/x": {"head": {"operationId": "head_x"}}}}
        self.assertEqual(_operations_to_tools(spec, max_operations=10), [])


class RequestSchemaTests(unittest.TestCase):
    def test_returns_empty_dict_when_no_inputs(self) -> None:
        self.assertEqual(_request_schema({}), {})


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
