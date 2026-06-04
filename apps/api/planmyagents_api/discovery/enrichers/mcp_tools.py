"""Enricher that calls ``tools/list`` against reachable MCP HTTP servers.

The Model Context Protocol exposes a ``tools/list`` JSON-RPC method that
returns the concrete tool surface a server supports. This enricher uses that
method to populate ``DiscoveryCandidate.tools`` for any MCP server we can
reach over HTTP. Stdio-only MCP servers and unreachable hosts are skipped.

Design notes:
- We never invent candidates. We only enrich existing ones.
- Network failures are swallowed and logged at the call site; a candidate that
  cannot be probed is returned unchanged.
- The probe is opt-in by way of a script entry point (``scripts/run_mcp_tool_probe.py``).
- The HTTP transport is pluggable so unit tests can drive it deterministically.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib import error, request

from planmyagents_api.discovery.models import (
    CandidateTool,
    DiscoveryCandidate,
)

LOGGER = logging.getLogger(__name__)

JsonRpcTransport = Callable[[str, dict[str, Any], float], dict[str, Any]]


@dataclass(frozen=True)
class McpToolProbeEnricher:
    """Probe MCP HTTP servers for their tools/list and persist the result.

    ``transport`` is a function ``(url, payload, timeout_seconds) -> response``
    that performs a JSON-RPC POST. It can be replaced in tests.
    """

    timeout_seconds: float = 8.0
    transport: JsonRpcTransport | None = None
    max_tools_per_candidate: int = 64
    skip_when_already_populated: bool = True
    endpoint_extractor: Callable[[DiscoveryCandidate], str | None] | None = None

    def enrich(self, candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
        """Return a list of candidates with tools/docs populated where reachable."""

        out: list[DiscoveryCandidate] = []
        for candidate in candidates:
            if candidate.provider_type != "mcp_server":
                out.append(candidate)
                continue
            if self.skip_when_already_populated and candidate.tools:
                out.append(candidate)
                continue
            endpoint = self._resolve_endpoint(candidate)
            if not endpoint:
                out.append(candidate)
                continue
            try:
                tools = self._fetch_tools(endpoint)
            except McpToolProbeError as exc:
                LOGGER.info("mcp tools/list failed for %s: %s", candidate.id, exc)
                out.append(candidate)
                continue
            if not tools:
                out.append(candidate)
                continue
            limited = tools[: self.max_tools_per_candidate]
            out.append(_with_tools(candidate, limited))
        return out

    def _resolve_endpoint(self, candidate: DiscoveryCandidate) -> str | None:
        if self.endpoint_extractor is not None:
            return self.endpoint_extractor(candidate)
        url = candidate.vendor_url.strip()
        if not url:
            return None
        if not url.startswith(("http://", "https://")):
            return None
        return url

    def _fetch_tools(self, endpoint: str) -> list[CandidateTool]:
        payload = {"jsonrpc": "2.0", "id": "tools-list", "method": "tools/list", "params": {}}
        transport = self.transport or _default_transport
        body = transport(endpoint, payload, self.timeout_seconds)
        return _parse_tools_response(body)


class McpToolProbeError(RuntimeError):
    """Raised when a tools/list probe cannot return a usable response."""


def _default_transport(
    url: str, payload: dict[str, Any], timeout_seconds: float
) -> dict[str, Any]:
    encoded = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
    except (OSError, TimeoutError, error.URLError) as exc:
        raise McpToolProbeError(f"transport failure: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpToolProbeError(f"invalid JSON response: {exc}") from exc


def _parse_tools_response(body: Any) -> list[CandidateTool]:
    if not isinstance(body, dict):
        return []
    if isinstance(body.get("error"), dict):
        return []
    result = body.get("result")
    if not isinstance(result, dict):
        return []
    raw_tools = result.get("tools") or []
    if not isinstance(raw_tools, list):
        return []
    out: list[CandidateTool] = []
    for entry in raw_tools:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        input_schema = entry.get("inputSchema") or entry.get("input_schema") or {}
        if not isinstance(input_schema, dict):
            input_schema = {}
        out.append(
            CandidateTool(
                name=name,
                description=str(entry.get("description") or "").strip(),
                input_schema=input_schema,
            )
        )
    return out


def _with_tools(
    candidate: DiscoveryCandidate, tools: list[CandidateTool]
) -> DiscoveryCandidate:
    """Return a copy of ``candidate`` with the new tool list merged in.

    Tool entries from the probe authoritatively replace any existing tool of
    the same name; tools the probe didn't see are preserved (they may have
    come from a curated catalog).
    """

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
class StubTransport:
    """In-memory transport for tests; maps URL → response payload."""

    responses: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __call__(
        self, url: str, payload: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        if url not in self.responses:
            raise McpToolProbeError(f"no stub configured for {url}")
        return self.responses[url]
