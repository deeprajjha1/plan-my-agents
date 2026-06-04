"""Capability-descriptor reader.

Parses whatever capability descriptor a protocol exposes into one normalized
``OperationSpec`` shape that the Case_Generator and invocation layer consume.
This is the answer to "does it read the agent card?" — yes, and from any
supported descriptor kind, not just MCP.

Sources supported:
* ``mcp_tools_list`` — MCP ``tools`` (name + ``inputSchema`` + description),
  read from the candidate's normalized ``tools`` field.
* ``a2a_agent_card`` — A2A Agent Card ``skills`` array (name/id + description),
  read from the candidate's ``skills`` field.
* ``openapi`` — operation set, read from the candidate's ``tools``/``skills``
  when an enricher has populated them, else left empty (the generic OpenAPI
  adapter re-fetches the live spec at invocation time).

An advertised operation that maps to no known registry capability is reported
as ``unmapped`` so the discovery layer can reconcile it — never fabricated
into a capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from planmyagents_api.discovery.constants import AGENTIC_PROVIDER_TYPES
from planmyagents_api.discovery.models import CandidateTool, DiscoveryCandidate

SOURCE_MCP = "mcp_tools_list"
SOURCE_A2A = "a2a_agent_card"
SOURCE_OPENAPI = "openapi"


@dataclass(frozen=True)
class OperationSpec:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_hint: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "output_hint": dict(self.output_hint),
        }


@dataclass(frozen=True)
class CapabilityDescriptor:
    source_kind: str
    operations: list[OperationSpec]
    unmapped_operations: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.operations

    def to_json(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "operations": [op.to_json() for op in self.operations],
            "unmapped_operations": list(self.unmapped_operations),
        }


def _ops_from_tools(tools: list[CandidateTool]) -> list[OperationSpec]:
    ops: list[OperationSpec] = []
    for tool in tools:
        if not tool.name:
            continue
        ops.append(
            OperationSpec(
                name=tool.name,
                description=tool.description,
                input_schema=dict(tool.input_schema or {}),
            )
        )
    return ops


@dataclass
class DescriptorReader:
    """Reads a candidate's capability descriptor into normalized operations."""

    def read(self, candidate: DiscoveryCandidate) -> CapabilityDescriptor | None:
        if candidate.provider_type not in AGENTIC_PROVIDER_TYPES:
            return None

        source_kind, operations = self._operations_for(candidate)
        if not operations:
            # No descriptor available (server not probed yet / stdio-only /
            # no skills). Return an empty descriptor so the coordinator can
            # record the missing-descriptor reason rather than crashing.
            return CapabilityDescriptor(source_kind=source_kind, operations=[])

        known = {cap.id for cap in candidate.capabilities}
        unmapped = self._unmapped(operations=operations, known_capabilities=known)
        return CapabilityDescriptor(
            source_kind=source_kind,
            operations=operations,
            unmapped_operations=unmapped,
        )

    def _operations_for(
        self, candidate: DiscoveryCandidate
    ) -> tuple[str, list[OperationSpec]]:
        if candidate.provider_type == "mcp_server":
            # OpenAPI surface takes precedence only when there are no MCP tools
            # AND an openapi_url is present (rare for mcp_server, but possible).
            if not candidate.tools and candidate.openapi_url.strip():
                return SOURCE_OPENAPI, _ops_from_tools(candidate.skills)
            return SOURCE_MCP, _ops_from_tools(candidate.tools)
        if candidate.provider_type == "a2a_agent":
            return SOURCE_A2A, _ops_from_tools(candidate.skills or candidate.tools)
        # ai_agent — prefer OpenAPI when a spec url exists, else whatever tools
        # were probed.
        if candidate.openapi_url.strip():
            return SOURCE_OPENAPI, _ops_from_tools(candidate.tools or candidate.skills)
        return SOURCE_MCP, _ops_from_tools(candidate.tools or candidate.skills)

    def _unmapped(
        self, *, operations: list[OperationSpec], known_capabilities: set[str]
    ) -> list[str]:
        """Operations whose name shares no token with any known capability.

        Reported for discovery reconciliation; we never fabricate a capability
        from them. Matching is token-overlap on the snake/camel-split name
        (e.g. ``web_scraping_fetch`` shares ``web``+``scraping`` with
        ``web_scraping``). Semantic (cross-vocabulary) reconciliation happens
        downstream in the capability index — this is the cheap lexical pass.
        """

        cap_tokens = {tok for cap in known_capabilities for tok in _tokens(cap)}
        unmapped: list[str] = []
        for op in operations:
            op_tokens = _tokens(op.name)
            if not op_tokens:
                continue
            if op_tokens & cap_tokens:
                continue
            unmapped.append(op.name)
        return unmapped


def schema_for_capability(
    descriptor: CapabilityDescriptor, capability: str
) -> dict[str, Any] | None:
    """Pick the best input schema for ``capability`` from a descriptor.

    Prefers the operation whose name shares the most tokens with the
    capability slug; falls back to the first operation when there is no
    overlap. Returns ``None`` when no operations exist.
    """

    if descriptor.is_empty:
        return None
    cap_tokens = _tokens(capability)
    best: OperationSpec | None = None
    best_overlap = 0
    for op in descriptor.operations:
        overlap = len(_tokens(op.name) & cap_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best = op
    if best is not None:
        return dict(best.input_schema)
    return dict(descriptor.operations[0].input_schema)


def _tokens(value: str) -> set[str]:
    """Split a snake/camel/kebab identifier into a lowercase token set."""

    import re

    # Split on non-alphanumerics first, then split camelCase within tokens.
    parts: list[str] = []
    for chunk in re.split(r"[^a-zA-Z0-9]+", value):
        if not chunk:
            continue
        parts.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+", chunk) or [chunk])
    return {p.lower() for p in parts if p}
