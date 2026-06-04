"""Enricher that resolves a candidate's OpenAPI spec into discoverable tools.

When a candidate has a populated ``openapi_url`` (set by a discovery source or
by hand in the curated catalogs), this enricher fetches the JSON or YAML spec
and converts the top-N operations into ``CandidateTool`` records.

We intentionally cap the number of operations we surface; some specs (e.g.
GitHub, Stripe) have hundreds of paths and we only need a representative set
to drive auto-generated adapters and the UI.

Network failures are swallowed per-candidate. The transport is pluggable so
unit tests can drive it deterministically.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from planmyagents_api.discovery.models import CandidateTool, DiscoveryCandidate

LOGGER = logging.getLogger(__name__)

OpenApiTransport = Callable[[str, float], str]


@dataclass(frozen=True)
class OpenApiEnricher:
    """Fetch each candidate's OpenAPI spec and surface its operations as tools."""

    timeout_seconds: float = 10.0
    transport: OpenApiTransport | None = None
    max_operations: int = 32
    skip_when_already_populated: bool = True

    def enrich(self, candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
        out: list[DiscoveryCandidate] = []
        for candidate in candidates:
            if not candidate.openapi_url:
                out.append(candidate)
                continue
            if self.skip_when_already_populated and candidate.tools:
                out.append(candidate)
                continue
            try:
                spec = self._fetch_spec(candidate.openapi_url)
            except OpenApiEnricherError as exc:
                LOGGER.info(
                    "openapi fetch failed for %s (%s): %s",
                    candidate.id,
                    candidate.openapi_url,
                    exc,
                )
                out.append(candidate)
                continue
            tools = _operations_to_tools(spec, max_operations=self.max_operations)
            if not tools:
                out.append(candidate)
                continue
            out.append(_with_tools(candidate, tools))
        return out

    def _fetch_spec(self, url: str) -> Any:
        transport = self.transport or _default_transport
        body = transport(url, self.timeout_seconds)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise OpenApiEnricherError(
                f"openapi spec is not JSON (yaml not yet supported): {exc}"
            ) from exc


class OpenApiEnricherError(RuntimeError):
    """Raised when an OpenAPI spec cannot be fetched or parsed."""


def _default_transport(url: str, timeout_seconds: float) -> str:
    req = request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return response.read().decode("utf-8")
    except (OSError, TimeoutError, error.URLError) as exc:
        raise OpenApiEnricherError(f"transport failure: {exc}") from exc


def _operations_to_tools(spec: Any, *, max_operations: int) -> list[CandidateTool]:
    if not isinstance(spec, dict):
        return []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return []

    tools: list[CandidateTool] = []
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
            }:
                continue
            if not isinstance(operation, dict):
                continue
            name = (
                operation.get("operationId")
                or f"{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
            )
            description = (
                operation.get("summary")
                or operation.get("description")
                or ""
            )
            input_schema = _request_schema(operation)
            tools.append(
                CandidateTool(
                    name=str(name),
                    description=str(description).strip(),
                    input_schema=input_schema,
                )
            )
            if len(tools) >= max_operations:
                return tools
    return tools


def _request_schema(operation: dict[str, Any]) -> dict[str, Any]:
    """Project an OpenAPI operation's parameters + body into a single schema."""

    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in operation.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        if not name:
            continue
        schema = parameter.get("schema") or {"type": "string"}
        if not isinstance(schema, dict):
            schema = {"type": "string"}
        properties[name] = schema
        if parameter.get("required"):
            required.append(str(name))

    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        content = request_body.get("content")
        if isinstance(content, dict):
            json_body = content.get("application/json")
            if isinstance(json_body, dict):
                schema = json_body.get("schema")
                if isinstance(schema, dict):
                    properties["body"] = schema
                    if request_body.get("required"):
                        required.append("body")

    if not properties:
        return {}
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = sorted(set(required))
    return schema


def _with_tools(
    candidate: DiscoveryCandidate, tools: list[CandidateTool]
) -> DiscoveryCandidate:
    by_name: dict[str, CandidateTool] = {tool.name: tool for tool in candidate.tools}
    for tool in tools:
        by_name[tool.name] = tool
    sorted_tools = sorted(by_name.values(), key=lambda item: item.name)
    return DiscoveryCandidate(
        **{
            **candidate.__dict__,
            "tools": sorted_tools,
        }
    )


@dataclass(frozen=True)
class StubOpenApiTransport:
    """In-memory transport mapping URL → response body string."""

    responses: dict[str, str]

    def __call__(self, url: str, timeout_seconds: float) -> str:
        if url not in self.responses:
            raise OpenApiEnricherError(f"no stub configured for {url}")
        return self.responses[url]
