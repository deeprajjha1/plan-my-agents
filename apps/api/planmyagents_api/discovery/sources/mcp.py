"""MCP catalog discovery source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate
from planmyagents_api.discovery.sources.json_source import _load_json


@dataclass(frozen=True)
class McpCatalogSource:
    """Load MCP server candidates from JSON catalogs.

    This connector intentionally supports several common catalog shapes because
    MCP registries are still fragmented.
    """

    locations: list[str]
    source_id: str = "mcp_catalog"
    timeout_seconds: float = 10.0
    goal_hash: str = ""

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        candidates: list[DiscoveryCandidate] = []
        for location in self.locations:
            for item in _mcp_records(_load_json(location, timeout_seconds=self.timeout_seconds)):
                raw = _candidate_from_mcp_record(item)
                try:
                    candidate = normalize_candidate(
                        raw,
                        source=self.source_id,
                        requested_capabilities=sorted(capabilities),
                        goal_hash=self.goal_hash,
                    )
                except (CandidateNormalizationError, TypeError, ValueError):
                    continue
                if capabilities and not candidate.supports_any(capabilities):
                    continue
                candidates.append(candidate)
        return candidates


def _mcp_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("servers", "mcpServers", "items", "packages", "tools"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [{"id": name, **item} for name, item in value.items() if isinstance(item, dict)]
    return []


def _candidate_from_mcp_record(item: dict[str, Any]) -> dict[str, Any]:
    raw_capabilities = item.get("capabilities") or item.get("tools") or item.get("categories") or []
    capability_payload, mcp_tools = _split_capabilities_and_tools(raw_capabilities)

    extra_tools = item.get("mcp_tools") or item.get("tool_schemas") or item.get("tools_detail")
    if isinstance(extra_tools, list):
        for entry in extra_tools:
            if isinstance(entry, dict) and entry.get("name"):
                mcp_tools.append(entry)

    docs_payload = item.get("docs") if isinstance(item.get("docs"), dict) else {}
    setup_url = (
        docs_payload.get("setup_url")
        or item.get("docs_url")
        or item.get("documentation")
        or item.get("homepage")
        or ""
    )
    auth_method = docs_payload.get("auth_method") or item.get("auth_method") or ""
    install_steps = docs_payload.get("install_steps") or item.get("install_steps") or []
    examples = docs_payload.get("usage_examples") or item.get("examples") or []
    docs: dict[str, Any] = {
        "setup_url": setup_url,
        "auth_method": auth_method,
        "install_steps": install_steps,
        "usage_examples": examples,
    }
    if docs_payload.get("auth_scopes"):
        docs["auth_scopes"] = docs_payload["auth_scopes"]
    if docs_payload.get("rate_limits"):
        docs["rate_limits"] = docs_payload["rate_limits"]
    if docs_payload.get("pricing_url"):
        docs["pricing_url"] = docs_payload["pricing_url"]
    if docs_payload.get("status_page_url"):
        docs["status_page_url"] = docs_payload["status_page_url"]

    return {
        "id": item.get("id") or item.get("name") or item.get("package") or item.get("slug"),
        "display_name": item.get("display_name")
        or item.get("displayName")
        or item.get("name")
        or item.get("title"),
        "vendor": item.get("vendor")
        or item.get("publisher")
        or item.get("owner")
        or item.get("author"),
        "vendor_url": item.get("vendor_url")
        or item.get("homepage")
        or item.get("repository")
        or item.get("url"),
        "provider_type": "mcp_server",
        "capabilities": capability_payload,
        "required_env_vars": item.get("required_env_vars") or item.get("env") or [],
        "tools": mcp_tools,
        "docs": docs,
        "openapi_url": item.get("openapi_url") or "",
    }


def _split_capabilities_and_tools(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate PlanMyAgents capability ids from real MCP-protocol tool entries.

    Many curated catalogs use ``tools`` as a list of capability slugs (strings)
    while the MCP spec defines a tool as ``{name, description, inputSchema}``.
    We treat dict entries with ``inputSchema`` (or ``input_schema``) as real
    MCP tools; everything else feeds the capability hypothesis layer.
    """

    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return [], []

    capabilities: list[dict[str, Any]] = []
    mcp_tools: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            capability_id = item.strip()
            if capability_id:
                capabilities.append(
                    {
                        "id": capability_id,
                        "confidence": 0.65,
                        "notes": "Capability declared by MCP catalog.",
                    }
                )
            continue
        if not isinstance(item, dict):
            continue

        has_input_schema = "inputSchema" in item or "input_schema" in item
        if has_input_schema and (item.get("name") or item.get("id")):
            mcp_tools.append(item)
            continue

        capability_id = item.get("id") or item.get("name") or item.get("capability")
        if capability_id:
            capabilities.append(
                {
                    "id": str(capability_id),
                    "confidence": float(item.get("confidence", 0.65)),
                    "notes": str(
                        item.get("description")
                        or item.get("notes")
                        or "Capability declared by MCP catalog."
                    ),
                }
            )
    return capabilities, mcp_tools


