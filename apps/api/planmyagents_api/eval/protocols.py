"""Pluggable per-protocol invocation registry.

Why this exists
---------------
"Apply to all agents broadly" must not mean a hardcoded ``if mcp / elif
openapi`` in the coordinator. This registry maps a discovered candidate's
protocol to a :class:`ProtocolInvoker` that knows (a) its honest maturity
status and (b) how to either build a real adapter or return a structured
refusal.

Current protocol landscape (2026), reflected in the seed registry:

* **MCP** — JSON-RPC ``tools/call``; ``executable`` via ``GenericMcpAdapter``.
* **OpenAPI** — re-fetch spec + build request; ``executable`` via
  ``GenericOpenApiAdapter``. (Not an agent protocol per se, but a real
  invocation surface discovered candidates expose.)
* **A2A** — Agent Card capability model; wire format still in flux →
  ``refusal_only`` (``GenericA2AAdapter`` returns a structured refusal).
* **ACP** — merged into A2A under the Linux Foundation; no separate stable
  invocation surface → ``planned``.
* **ANP** — DID + JSON-LD open-network discovery; no stable invocation
  surface we can honestly score yet → ``planned``.

Adding a protocol later is registering a new invoker (+ tests). The
coordinator, scheduler, and scoring path do not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from planmyagents_api.agents.protocol import (
    GenericA2AAdapter,
    GenericAiAgentAdapter,
    GenericMcpAdapter,
    GenericOpenApiAdapter,
    GenericProtocolAdapter,
)
from planmyagents_api.benchmark.models import ProviderResponse
from planmyagents_api.discovery.models import DiscoveryCandidate


class ProtocolMaturity(StrEnum):
    """Honest per-protocol invocation status."""

    EXECUTABLE = "executable"        # stable wire format + implemented invoker
    REFUSAL_ONLY = "refusal_only"    # recognised, but no stable invocation surface
    PLANNED = "planned"              # recognised, not yet implemented


@runtime_checkable
class ProtocolInvoker(Protocol):
    """Knows how to invoke (or honestly refuse) one agent protocol."""

    protocol: str
    maturity: ProtocolMaturity

    def build_adapter(self, candidate: DiscoveryCandidate) -> GenericProtocolAdapter:
        """Return a generic-protocol adapter for an ``executable`` protocol."""

    def refusal(
        self, candidate: DiscoveryCandidate, capability: str
    ) -> ProviderResponse:
        """Return a structured refusal for a non-executable protocol."""


def _refusal_response(
    *, candidate: DiscoveryCandidate, protocol: str, reason: str
) -> ProviderResponse:
    return ProviderResponse(
        succeeded=False,
        output=None,
        cost_usd=0.0,
        latency_ms=0,
        error=reason,
        raw_response={
            "provider_id": candidate.id,
            "protocol": protocol,
            "refused": True,
        },
    )


@dataclass(frozen=True)
class _AdapterBackedInvoker:
    """Invoker for protocols that have a real generic adapter class."""

    protocol: str
    maturity: ProtocolMaturity
    adapter_cls: type[GenericProtocolAdapter]

    def build_adapter(self, candidate: DiscoveryCandidate) -> GenericProtocolAdapter:
        # The generic adapters take the candidate's registry-shaped dict so
        # they can read base_url / tools / openapi_url / auth from it.
        return self.adapter_cls(registry_agent=candidate.to_registry_json())

    def refusal(
        self, candidate: DiscoveryCandidate, capability: str
    ) -> ProviderResponse:
        # Executable protocols still expose a refusal path for the case where
        # the coordinator declines to run (gate off / safety). The adapter's
        # own gate handles the live path; this is the metadata-only refusal.
        return _refusal_response(
            candidate=candidate,
            protocol=self.protocol,
            reason=(
                f"{self.protocol} invocation not performed for capability "
                f"'{capability}' (gated or declined by the eval coordinator)."
            ),
        )


@dataclass(frozen=True)
class _NonExecutableInvoker:
    """Invoker for protocols with no stable invocation surface yet.

    ``build_adapter`` raises — the coordinator must check ``maturity`` and
    take the verification-only path instead of ever calling this.
    """

    protocol: str
    maturity: ProtocolMaturity
    reason: str
    adapter_cls: type[GenericProtocolAdapter] | None = None

    def build_adapter(self, candidate: DiscoveryCandidate) -> GenericProtocolAdapter:
        raise NotImplementedError(
            f"protocol {self.protocol!r} has maturity {self.maturity.value!r}; "
            "no executable adapter. The coordinator must take the "
            "verification-only path."
        )

    def refusal(
        self, candidate: DiscoveryCandidate, capability: str
    ) -> ProviderResponse:
        if self.adapter_cls is not None:
            # Reuse the adapter's own structured refusal text when one exists
            # (e.g. GenericA2AAdapter explains the spec-in-flux situation),
            # but without enabling execution.
            return _refusal_response(
                candidate=candidate, protocol=self.protocol, reason=self.reason
            )
        return _refusal_response(
            candidate=candidate, protocol=self.protocol, reason=self.reason
        )


# Map a discovery ``provider_type`` to a protocol name. ``ai_agent`` is
# free-form; we treat it as its own protocol with no wire format.
PROVIDER_TYPE_TO_PROTOCOL: dict[str, str] = {
    "mcp_server": "mcp",
    "a2a_agent": "a2a",
    "ai_agent": "ai_agent",
    # api_provider candidates aren't agentic and are rejected upstream by the
    # coordinator, but OpenAPI invocation is available for candidates that
    # carry an openapi_url. The registry still recognises "openapi".
}


def default_registry() -> ProtocolInvokerRegistry:
    """Build the seed registry reflecting the current protocol landscape."""

    invokers: dict[str, ProtocolInvoker] = {
        "mcp": _AdapterBackedInvoker(
            protocol="mcp",
            maturity=ProtocolMaturity.EXECUTABLE,
            adapter_cls=GenericMcpAdapter,
        ),
        "openapi": _AdapterBackedInvoker(
            protocol="openapi",
            maturity=ProtocolMaturity.EXECUTABLE,
            adapter_cls=GenericOpenApiAdapter,
        ),
        "a2a": _NonExecutableInvoker(
            protocol="a2a",
            maturity=ProtocolMaturity.REFUSAL_ONLY,
            reason=(
                "A2A skill invocation is not implemented: the wire format is "
                "still in active specification work. Candidate receives a "
                "verification-only result until an A2A invoker ships."
            ),
            adapter_cls=GenericA2AAdapter,
        ),
        "acp": _NonExecutableInvoker(
            protocol="acp",
            maturity=ProtocolMaturity.PLANNED,
            reason=(
                "ACP has merged into A2A under the Linux Foundation and has no "
                "separate stable invocation surface; verification-only."
            ),
        ),
        "anp": _NonExecutableInvoker(
            protocol="anp",
            maturity=ProtocolMaturity.PLANNED,
            reason=(
                "ANP (DID + JSON-LD open-network discovery) has no stable "
                "invocation surface we can score yet; verification-only."
            ),
        ),
        "ai_agent": _NonExecutableInvoker(
            protocol="ai_agent",
            maturity=ProtocolMaturity.REFUSAL_ONLY,
            reason=(
                "Free-form AI agent has no published wire format; "
                "verification-only unless it also exposes an MCP/OpenAPI "
                "surface."
            ),
            adapter_cls=GenericAiAgentAdapter,
        ),
    }
    return ProtocolInvokerRegistry(invokers=invokers)


@dataclass
class ProtocolInvokerRegistry:
    """Maps a candidate's protocol to its :class:`ProtocolInvoker`."""

    invokers: dict[str, ProtocolInvoker] = field(default_factory=dict)

    def register(self, invoker: ProtocolInvoker) -> None:
        """Add or replace an invoker. This is the entire extension surface."""

        self.invokers[invoker.protocol] = invoker

    def protocol_for_candidate(self, candidate: DiscoveryCandidate) -> str:
        """Resolve the protocol name for a candidate.

        Prefers an MCP/A2A/ai_agent protocol from ``provider_type``, but
        upgrades an ``ai_agent``/``mcp_server`` candidate that carries an
        ``openapi_url`` to ``openapi`` when no native MCP surface is present,
        because OpenAPI is the executable surface in that case.
        """

        protocol = PROVIDER_TYPE_TO_PROTOCOL.get(candidate.provider_type, "")
        if protocol in {"", "ai_agent"} and candidate.openapi_url.strip():
            return "openapi"
        return protocol or candidate.provider_type

    def for_candidate(self, candidate: DiscoveryCandidate) -> ProtocolInvoker | None:
        return self.invokers.get(self.protocol_for_candidate(candidate))

    def maturity(self, protocol: str) -> ProtocolMaturity:
        invoker = self.invokers.get(protocol)
        if invoker is None:
            return ProtocolMaturity.PLANNED
        return invoker.maturity
