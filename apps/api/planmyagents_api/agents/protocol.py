"""Generic protocol adapters for discovered MCP / OpenAPI / A2A providers.

Why this module exists (Gap 4 in the honest-scope audit)
--------------------------------------------------------
The discovery layer surfaces large numbers of MCP / OpenAPI / A2A
providers, but until this module learned to *invoke* them they were
metadata-only — registered, indexed, embedded, and unreachable. Without
real execution, every refused goal looked the same regardless of how
many candidates discovery found, and the user-facing experience was
"we discovered 14 MCP servers and used zero of them".

This module closes that gap for the two protocols where real, public
specifications exist today:

* **MCP** — JSON-RPC 2.0 ``tools/call`` invocation against a server's
  HTTP endpoint. Tool selection is either explicit (``inputs.tool_name``)
  or auto-resolved by name overlap against the requested capability.
  Tool arguments come from ``inputs.arguments`` (or, for ergonomics,
  any ``inputs`` key not reserved by the adapter itself).

* **OpenAPI** — re-fetches the spec at execution time, picks an
  operation either explicitly (``inputs.operation_id``) or by name
  overlap against the requested capability, and builds the matching
  HTTP request. Auth is opt-in: a ``registry_agent['auth']`` block can
  declare a bearer token, an API-key header, or query-parameter auth,
  with secret values resolved from environment variables — the secret
  itself never lives in the registry blob.

For the two protocols whose invocation surface is still in flux we
return a structured refusal:

* **A2A** — the public draft is changing fast enough that wiring an
  invocation client today would just have to be rewritten. The
  refusal payload tells operators what's missing so they can decide
  whether to write a vendor-specific wrapper or wait.
* **AI agent (free-form)** — without a published wire format the only
  honest move is the same refusal.

Safety guardrails
-----------------
* All execution is gated by
  ``PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION``. Default OFF.
  An accidental import or an unsandboxed deploy CANNOT trigger live
  third-party calls without an operator explicitly opting in.
* Every adapter pre-validates the requested capability against the
  ``registry_agent`` capability list; mismatched capabilities raise
  ``ValueError`` at the boundary instead of being silently routed to
  a tool that may share a name accident.
* Network failures, JSON-RPC errors, HTTP error responses, and
  protocol-level "this tool failed" signals are all converted to
  ``ProviderResponse(succeeded=False, error=...)`` rather than
  raising — the caller's contract is "always return a response".
* ``cost_usd`` is set to 0.0 by default. The cost-cap policy at the
  workflow level enforces operator-imposed budgets per provider; we
  do not synthesize protocol-level prices here.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib import error, parse, request

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse

LOGGER = logging.getLogger(__name__)

ENABLE_PROTOCOL_EXECUTION_ENV = "PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION"

# Default per-call HTTP timeout. Operators override per-deploy via env;
# this default is sized for interactive /goal latency budgets — too
# tight and slow upstreams produce false-negative refusals; too loose
# and a single hung server can blow the workflow's overall deadline.
_DEFAULT_HTTP_TIMEOUT_SECONDS = 12.0

# Hard cap on response body size we will read into memory. MCP
# tools/call responses are usually tiny (a few KB of text content)
# but a misbehaving server returning a multi-MB blob would happily
# OOM the worker. 2 MB is the same ceiling Stripe/PayPal docs cite
# for typical webhook payloads — comfortable for any well-formed
# tool response and tight enough to fail fast on garbage upstreams.
_MAX_RESPONSE_BYTES = 2_000_000

_USER_AGENT = "PlanMyAgents-ProtocolAdapter/1.0"


class ProtocolAdapterExecutionBlocked(RuntimeError):
    """Raised when a generic protocol adapter is invoked outside an enabled sandbox."""


@dataclass(frozen=True)
class GenericProtocolAdapter:
    """Metadata-driven protocol adapter base class.

    Subclasses override :attr:`protocol_name` and :meth:`execute`. The
    base class implements the common boilerplate (capability
    validation, gate enforcement, response envelope) so each protocol's
    real client is a few dozen lines instead of repeating the same
    safety-and-shape scaffolding.
    """

    registry_agent: dict[str, Any] = field(default_factory=dict)
    protocol_name: str = "generic"

    @property
    def provider_id(self) -> str:
        return str(self.registry_agent.get("id") or f"generic-{self.protocol_name}-adapter")

    @property
    def capabilities(self) -> list[str]:
        return [
            str(capability.get("id"))
            for capability in self.registry_agent.get("capabilities", [])
            if capability.get("id")
        ]

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Return the registry agent's ``tools`` field as a list of dicts.

        Defensive parsing: a missing field, a non-list value, and
        non-dict entries are all silently coerced to "no tools",
        because protocol execution is best-effort and a malformed
        tool list shouldn't blow up the adapter constructor."""

        raw = self.registry_agent.get("tools", [])
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    @property
    def base_url(self) -> str:
        """Resolve the candidate's invocable URL.

        Order of preference: ``api_base_url`` (explicit invocation
        URL set by the registry / promotions step), then
        ``vendor_url`` (the URL the discovery source originally
        reported the candidate at). Empty / non-HTTP values produce
        a structured refusal at execution time, never an exception.
        """

        for key in ("api_base_url", "vendor_url"):
            value = self.registry_agent.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    async def estimate_cost(self, request: ProviderRequest) -> float:
        """Generic protocol adapters do not synthesise prices.

        The cost-cap policy at the workflow level is the source of
        truth for "how expensive is this provider" — it applies an
        operator-supplied schedule keyed by provider id. Returning a
        non-zero value here would either double-count (cost cap
        already added) or contradict the operator's schedule.
        """

        _ensure_capability(self.capabilities, request.capability)
        return 0.0

    async def health_check(self) -> bool:
        """Liveness signal: the adapter is *configured* (URL + capabilities)
        AND execution is enabled. Returning True without execution
        enabled would mislead the workflow router into trying calls
        the gate is going to refuse."""

        return bool(self.base_url) and _protocol_execution_enabled()

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        """Default execution path — refused. Subclasses override.

        We deliberately keep the base class non-executable so a
        ``GenericProtocolAdapter()`` instantiated directly never makes
        any network call. Each protocol-specific subclass is
        responsible for enabling its own execution path.
        """

        _ensure_capability(self.capabilities, request.capability)
        return _structured_refusal(
            provider_id=self.provider_id,
            protocol=self.protocol_name,
            reason=(
                "Generic base adapter has no concrete invocation surface. "
                "This indicates a registry / router misconfiguration: "
                "promote to a protocol-specific subclass "
                "(GenericMcpAdapter / GenericOpenApiAdapter / etc.)."
            ),
        )


@dataclass(frozen=True)
class GenericMcpAdapter(GenericProtocolAdapter):
    """Invokes MCP-spec ``tools/call`` JSON-RPC against discovered servers.

    Wire contract reminder (from the Model Context Protocol spec):

    * Request:  ``POST <server_url>``
                ``Content-Type: application/json``
                Body: ``{"jsonrpc": "2.0", "id": "...", "method": "tools/call",
                          "params": {"name": "<tool>", "arguments": {...}}}``
    * Response: JSON-RPC envelope. Tool-level errors surface as
                ``result.isError = true`` with a content array carrying
                the human-readable explanation. Transport-level errors
                surface as ``error.{code, message}`` per JSON-RPC 2.0.

    Mapping to ``ProviderRequest.inputs``:

    * ``inputs["tool_name"]`` — explicit tool selector (production path).
    * ``inputs["arguments"]`` — dict of args passed verbatim to the tool.
    * Other ``inputs`` keys (excluding the reserved selector and
      arguments fields) are forwarded as ``arguments`` for ergonomics
      when the caller doesn't want to nest.
    """

    protocol_name: str = "mcp"

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not _protocol_execution_enabled():
            return _disabled_response(self.provider_id, self.protocol_name)

        url = self.base_url
        if not _is_http_url(url):
            return _structured_refusal(
                provider_id=self.provider_id,
                protocol=self.protocol_name,
                reason=(
                    "registry_agent has no HTTP api_base_url / vendor_url; "
                    "stdio-only MCP servers are not invocable from this "
                    "adapter. Provide an HTTP transport endpoint."
                ),
            )

        tool_name = _resolve_mcp_tool(
            request=request,
            tools=self.tools,
            capability=request.capability,
        )
        if not tool_name:
            return _structured_refusal(
                provider_id=self.provider_id,
                protocol=self.protocol_name,
                reason=(
                    f"could not resolve a tool for capability "
                    f"'{request.capability}'. Provide inputs.tool_name "
                    f"explicitly, or populate the candidate's tools "
                    f"field via the McpToolProbeEnricher so name "
                    f"overlap can resolve a match."
                ),
            )

        rpc_arguments = _extract_rpc_arguments(request.inputs)

        rpc_body = {
            "jsonrpc": "2.0",
            "id": request.idempotency_key or "mcp-call",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": rpc_arguments},
        }

        started = time.monotonic()
        try:
            response_payload = _post_json_rpc(url, rpc_body)
        except _ProtocolHttpError as exc:
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=f"transport: {exc}",
                raw_response={
                    "provider_id": self.provider_id,
                    "protocol": self.protocol_name,
                    "tool_name": tool_name,
                },
            )
        latency_ms = int((time.monotonic() - started) * 1000)

        # JSON-RPC error envelope. Per spec, presence of ``error`` is
        # mutually exclusive with ``result``; we surface the structured
        # error verbatim so operators debugging a failure can inspect
        # the upstream code/message without re-running the call.
        if isinstance(response_payload, dict) and isinstance(
            response_payload.get("error"), dict
        ):
            err = response_payload["error"]
            return ProviderResponse(
                succeeded=False,
                output={"tool_name": tool_name},
                cost_usd=0.0,
                latency_ms=latency_ms,
                error=(
                    f"jsonrpc error {err.get('code', 'unknown')}: "
                    f"{err.get('message', 'no message')}"
                ),
                raw_response=response_payload,
            )

        result = (
            response_payload.get("result")
            if isinstance(response_payload, dict)
            else None
        )
        if not isinstance(result, dict):
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=latency_ms,
                error="malformed response: missing or non-dict result",
                raw_response=response_payload
                if isinstance(response_payload, dict)
                else {"raw": response_payload},
            )

        # MCP convention: ``isError`` flips the success bit, content
        # carries the human-readable failure detail.
        is_error = bool(result.get("isError", False))
        text_blocks = _extract_mcp_text_blocks(result.get("content"))
        normalized_text = "\n".join(text_blocks) if text_blocks else ""

        return ProviderResponse(
            succeeded=not is_error,
            output={
                "tool_name": tool_name,
                "text": normalized_text,
                "content": result.get("content"),
            },
            cost_usd=0.0,
            latency_ms=latency_ms,
            error=normalized_text if is_error else None,
            raw_response=response_payload,
        )


@dataclass(frozen=True)
class GenericOpenApiAdapter(GenericProtocolAdapter):
    """Invokes a discovered OpenAPI provider by re-fetching its spec.

    This adapter intentionally does NOT cache the spec across requests
    — caching is the operator's job (a CDN, a registry-side spec
    bundler, etc.). At the framework level we'd rather pay one extra
    fetch per call than serve stale path/method/auth info that could
    silently hit the wrong endpoint after a vendor publishes a new
    version of their API.

    Mapping to ``ProviderRequest.inputs``:

    * ``inputs["operation_id"]`` — explicit OpenAPI ``operationId``.
      Production path; selector ambiguity becomes a configuration error
      at deploy time instead of a runtime guess.
    * Otherwise, the adapter picks the first operation whose
      ``operationId`` overlaps with the requested capability slug.
    * ``inputs["path_params"]`` — dict substituted into ``{name}``
      placeholders in the path.
    * ``inputs["query"]`` — dict appended as a query string.
    * ``inputs["body"]`` — dict serialised as the JSON request body
      for non-GET methods.
    * ``inputs["headers"]`` — dict merged into the request headers.

    Auth (opt-in via ``registry_agent["auth"]``):

    * ``{"type": "bearer", "env": "MY_API_KEY"}`` ⇒
      ``Authorization: Bearer $MY_API_KEY``.
    * ``{"type": "api_key", "header": "X-API-Key", "env": "MY_API_KEY"}`` ⇒
      ``X-API-Key: $MY_API_KEY``.
    * ``{"type": "api_key", "query": "key", "env": "MY_API_KEY"}`` ⇒
      appended to the request as ``?key=$MY_API_KEY``.

    Secret values come from environment variables only. The registry
    blob declares the *contract*; the operator's deploy supplies the
    secret. This is the same pattern the hand-written wrappers use
    (Razorpay/Stripe/etc.).
    """

    protocol_name: str = "openapi"

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not _protocol_execution_enabled():
            return _disabled_response(self.provider_id, self.protocol_name)

        spec_url = str(self.registry_agent.get("openapi_url") or "").strip()
        if not _is_http_url(spec_url):
            return _structured_refusal(
                provider_id=self.provider_id,
                protocol=self.protocol_name,
                reason=(
                    "registry_agent.openapi_url is missing or non-HTTP. "
                    "Either populate it via the OpenAPI enricher or "
                    "promote to a hand-written adapter."
                ),
            )

        try:
            spec = _fetch_openapi_spec(spec_url)
        except _ProtocolHttpError as exc:
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=0,
                error=f"openapi spec fetch failed: {exc}",
                raw_response={"openapi_url": spec_url},
            )

        operation = _resolve_openapi_operation(
            spec=spec,
            request=request,
            capability=request.capability,
        )
        if operation is None:
            return _structured_refusal(
                provider_id=self.provider_id,
                protocol=self.protocol_name,
                reason=(
                    f"could not resolve an operation for capability "
                    f"'{request.capability}'. Provide inputs.operation_id "
                    f"explicitly, or check that the spec includes an "
                    f"operationId whose name overlaps the capability."
                ),
            )

        try:
            url, method, headers, body = _build_openapi_request(
                spec=spec,
                operation=operation,
                request=request,
                auth=self.registry_agent.get("auth"),
            )
        except _ProtocolBuildError as exc:
            return _structured_refusal(
                provider_id=self.provider_id,
                protocol=self.protocol_name,
                reason=f"openapi request build failed: {exc}",
            )

        started = time.monotonic()
        try:
            status_code, response_body = _http_call(
                url, method=method, headers=headers, body=body
            )
        except _ProtocolHttpError as exc:
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=f"transport: {exc}",
                raw_response={"url": url, "method": method},
            )
        latency_ms = int((time.monotonic() - started) * 1000)

        succeeded = 200 <= status_code < 300
        parsed = _try_parse_json(response_body)
        return ProviderResponse(
            succeeded=succeeded,
            output={
                "status_code": status_code,
                "url": url,
                "method": method,
                "operation_id": operation.get("operationId"),
                "body": parsed if parsed is not None else response_body[:4000],
            },
            cost_usd=0.0,
            latency_ms=latency_ms,
            error=None if succeeded else f"http {status_code}",
            raw_response={
                "status_code": status_code,
                "body_preview": response_body[:4000],
            },
        )


@dataclass(frozen=True)
class GenericA2AAdapter(GenericProtocolAdapter):
    """Honest refusal: A2A invocation is not implemented in v1.

    The Agent-to-Agent draft spec is still in flux at the time of
    writing; wiring a client today would have to be rewritten as the
    spec stabilises. Until then we surface a structured refusal that
    tells operators what's missing rather than pretending the call
    happened. Hand-written adapters for specific A2A endpoints remain
    the supported path in the meantime.

    Gate ordering note: we check the execution gate FIRST so an
    unsandboxed deploy gets the "execution is disabled" message
    (which an operator can act on) before the "spec not implemented"
    message (which they can't). The disabled message is the safer
    error to surface in production.
    """

    protocol_name: str = "a2a"

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not _protocol_execution_enabled():
            return _disabled_response(self.provider_id, self.protocol_name)
        return _structured_refusal(
            provider_id=self.provider_id,
            protocol=self.protocol_name,
            reason=(
                "A2A skill invocation is not implemented in the generic "
                "adapter. The A2A wire format is still in active "
                "specification work; until it stabilises, write a "
                "vendor-specific adapter and route through the static "
                "registry instead of relying on protocol-level execution."
            ),
        )


@dataclass(frozen=True)
class GenericAiAgentAdapter(GenericProtocolAdapter):
    """Honest refusal: free-form 'AI agent' has no published wire format.

    The ``ai_agent`` provider type covers everything from a hosted
    chatbot to a custom REST endpoint with no shared schema. Without
    a published invocation contract there is no honest generic call
    to make — the operator must supply a vendor-specific adapter.

    Same gate-first ordering as :class:`GenericA2AAdapter` for the
    same reason — the disabled message is the actionable error.
    """

    protocol_name: str = "ai-agent"

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not _protocol_execution_enabled():
            return _disabled_response(self.provider_id, self.protocol_name)
        return _structured_refusal(
            provider_id=self.provider_id,
            protocol=self.protocol_name,
            reason=(
                "Free-form AI agent invocation has no published wire "
                "format the framework can default to. Provide a "
                "vendor-specific adapter or constrain the discovery to "
                "providers that publish an MCP / OpenAPI surface."
            ),
        )


# ---------------------------------------------------------------------------
# Internal helpers


def _protocol_execution_enabled() -> bool:
    """Honor the env-var gate. Default OFF — opt-in even in dev."""

    return os.getenv(ENABLE_PROTOCOL_EXECUTION_ENV, "").lower() in {"1", "true", "yes"}


def _ensure_capability(capabilities: list[str], requested: str) -> None:
    """Raise ``ValueError`` if the requested capability isn't in the
    candidate's declared capabilities. We raise rather than refuse
    here because this represents a routing-time misconfiguration
    (the planner / router asked for the wrong provider) — surfacing
    it as an exception keeps the bug close to its source."""

    if requested not in capabilities:
        raise ValueError(f"provider does not support capability: {requested}")


def _is_http_url(url: str) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def _disabled_response(provider_id: str, protocol_name: str) -> ProviderResponse:
    return ProviderResponse(
        succeeded=False,
        output=None,
        cost_usd=0.0,
        latency_ms=0,
        error=(
            "Generic protocol adapter execution is disabled. "
            f"Set {ENABLE_PROTOCOL_EXECUTION_ENV}=true only in a sandbox."
        ),
        raw_response={
            "provider_id": provider_id,
            "protocol": protocol_name,
            "execution_gated": True,
        },
    )


def _structured_refusal(
    *, provider_id: str, protocol: str, reason: str
) -> ProviderResponse:
    """Uniform shape for every soft refusal so downstream consumers
    (workflow executor, cost ledger, UI) can render them identically.
    ``succeeded=False`` and ``error`` carries the human-readable
    explanation; ``raw_response`` carries the structured tag set."""

    return ProviderResponse(
        succeeded=False,
        output=None,
        cost_usd=0.0,
        latency_ms=0,
        error=reason,
        raw_response={
            "provider_id": provider_id,
            "protocol": protocol,
            "refused": True,
        },
    )


# --- MCP helpers ----------------------------------------------------


# Reserved input keys are protocol-specific because the same key name
# (``body``) means different things to MCP and OpenAPI: for MCP it is
# just another tool argument, for OpenAPI it is the JSON request body.
# Sharing one global set across both protocols would either silently
# drop legitimate MCP tool args (the original bug) or incorrectly
# treat OpenAPI bodies as args.
_MCP_RESERVED_INPUT_KEYS = frozenset({"tool_name", "arguments"})


def _resolve_mcp_tool(
    *,
    request: ProviderRequest,
    tools: list[dict[str, Any]],
    capability: str,
) -> str | None:
    """Pick the tool to invoke for this MCP request.

    Order:

    1. ``inputs.tool_name`` if the caller provided it (production path).
    2. If exactly one tool is available, use it (single-tool servers).
    3. First tool whose ``name`` overlaps the capability slug after
       case-folding and underscore stripping (best-effort auto-routing).

    We deliberately do NOT fall back to "first tool" — picking
    arbitrarily would let a misconfigured planner route a payment
    request to a tool named ``ping`` and return success. Honest
    refusal beats silent miscall.
    """

    explicit = str(request.inputs.get("tool_name") or "").strip()
    if explicit:
        return explicit

    if len(tools) == 1:
        return str(tools[0].get("name") or "").strip() or None

    capability_normalised = capability.replace("_", "").lower()
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        normalised = name.replace("_", "").replace("-", "").lower()
        if capability_normalised in normalised or normalised in capability_normalised:
            return name
    return None


def _extract_rpc_arguments(inputs: dict[str, Any]) -> dict[str, Any]:
    """Build the ``arguments`` dict for a JSON-RPC tools/call.

    If the caller provided ``inputs["arguments"]`` it wins verbatim
    (the explicit-is-better-than-implicit production path). Otherwise
    we forward all non-reserved inputs — this is the dev-ergonomics
    path that lets a quick test pass ``inputs={"to": "x", "subject": "y"}``
    without nesting under an ``arguments`` key.
    """

    explicit = inputs.get("arguments")
    if isinstance(explicit, dict):
        return dict(explicit)
    return {k: v for k, v in inputs.items() if k not in _MCP_RESERVED_INPUT_KEYS}


def _extract_mcp_text_blocks(content: Any) -> list[str]:
    """Project the MCP ``content`` array into a list of text strings.

    Per the spec, ``content`` is an array of typed blocks; the most
    common type by far is ``{"type": "text", "text": "..."}``. We
    silently skip non-text blocks (image/audio/etc.) — they require
    consumer-specific handling that doesn't belong in the framework.
    """

    if not isinstance(content, list):
        return []
    out: list[str] = []
    for entry in content:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "text":
            text = entry.get("text", "")
            if isinstance(text, str):
                out.append(text)
    return out


def _post_json_rpc(url: str, body: dict[str, Any]) -> Any:
    """POST a JSON-RPC body and return the parsed response.

    Network failures, timeouts, non-JSON responses, and HTTP error
    statuses all raise :class:`_ProtocolHttpError` so the adapter can
    convert them uniformly to ``ProviderResponse(succeeded=False)``."""

    encoded = json.dumps(body).encode("utf-8")
    req = request.Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with request.urlopen(req, timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310
            raw = response.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
    except error.HTTPError as exc:  # 4xx/5xx with body
        body_preview = ""
        try:
            body_preview = exc.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        raise _ProtocolHttpError(
            f"http {exc.code}: {body_preview[:400]}"
        ) from exc
    except (OSError, TimeoutError, error.URLError) as exc:
        raise _ProtocolHttpError(str(exc)) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _ProtocolHttpError(f"invalid JSON response: {exc}") from exc


# --- OpenAPI helpers ------------------------------------------------


def _fetch_openapi_spec(url: str) -> dict[str, Any]:
    """Fetch and parse an OpenAPI JSON spec.

    YAML specs are explicitly not supported — the dependency cost
    (PyYAML) is non-trivial and discovery sources that surface YAML-
    only specs are vanishingly rare in practice. Operators with a
    YAML-only vendor should pre-convert it to JSON in a discovery
    enricher.
    """

    req = request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
    )
    try:
        with request.urlopen(req, timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310
            raw = response.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        raise _ProtocolHttpError(f"http {exc.code} fetching spec") from exc
    except (OSError, TimeoutError, error.URLError) as exc:
        raise _ProtocolHttpError(str(exc)) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _ProtocolHttpError(
            f"openapi spec is not JSON (yaml unsupported in this adapter): {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise _ProtocolHttpError("openapi spec root must be a JSON object")
    return parsed


def _resolve_openapi_operation(
    *,
    spec: dict[str, Any],
    request: ProviderRequest,
    capability: str,
) -> dict[str, Any] | None:
    """Walk the spec's paths and return the dict matching the request.

    Returned dict carries the original operation dict plus injected
    ``_path`` and ``_method`` keys — those are the only structurally
    significant fields beyond what OpenAPI carries natively, so we
    stash them on the same object the caller is going to consult.

    Selection order:

    1. ``inputs.operation_id`` literal match.
    2. First operation whose ``operationId`` (case-folded, underscore-
       stripped) overlaps the capability slug.

    No fallback to "first operation" — same rationale as MCP tool
    selection: a misroute is worse than a refusal.
    """

    target_op_id = str(request.inputs.get("operation_id") or "").strip()
    capability_normalised = capability.replace("_", "").lower()

    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return None

    # First pass: explicit operationId match.
    if target_op_id:
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, op in methods.items():
                if not isinstance(op, dict):
                    continue
                if op.get("operationId") == target_op_id:
                    return {**op, "_path": str(path), "_method": str(method).lower()}
        return None

    # Second pass: capability-name overlap.
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            op_id = str(op.get("operationId") or "")
            if not op_id:
                continue
            normalised = op_id.replace("_", "").replace("-", "").lower()
            if (
                capability_normalised in normalised
                or normalised in capability_normalised
            ):
                return {**op, "_path": str(path), "_method": str(method).lower()}
    return None


class _ProtocolBuildError(RuntimeError):
    """Raised when an adapter cannot build a request from inputs."""


def _build_openapi_request(
    *,
    spec: dict[str, Any],
    operation: dict[str, Any],
    request: ProviderRequest,
    auth: Any,
) -> tuple[str, str, dict[str, str], bytes | None]:
    """Compose an HTTP request from an OpenAPI operation + caller inputs.

    Returns ``(url, method, headers, body_bytes)``. The url has path
    parameters interpolated AND query parameters appended; the body
    is JSON-serialised when the caller provided ``inputs.body``.
    Path-template substitution failures (missing required path
    param) and other build-time errors raise :class:`_ProtocolBuildError`
    so the adapter can surface a clean refusal.
    """

    method = str(operation.get("_method") or "get").lower()
    raw_path = str(operation.get("_path") or "")
    path_params = request.inputs.get("path_params") or {}
    if not isinstance(path_params, dict):
        path_params = {}
    interpolated_path = raw_path
    try:
        # Substitute every {name} placeholder; missing keys raise
        # so the operator sees the gap instead of a server-side
        # 404 that's hard to attribute.
        import re

        def _sub(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in path_params:
                raise _ProtocolBuildError(
                    f"missing required path parameter '{name}'"
                )
            return parse.quote(str(path_params[name]), safe="")

        interpolated_path = re.sub(r"\{([^}]+)\}", _sub, raw_path)
    except _ProtocolBuildError:
        raise
    except Exception as exc:  # noqa: BLE001 — defensive
        raise _ProtocolBuildError(f"path build failed: {exc}") from exc

    base = _openapi_base_url(spec)
    if not base:
        raise _ProtocolBuildError(
            "openapi spec has no usable servers entry; cannot build URL"
        )
    url = base.rstrip("/") + interpolated_path

    query = request.inputs.get("query") or {}
    if isinstance(query, dict) and query:
        url = url + ("&" if "?" in url else "?") + parse.urlencode(query, doseq=True)

    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    extra_headers = request.inputs.get("headers")
    if isinstance(extra_headers, dict):
        for k, v in extra_headers.items():
            headers[str(k)] = str(v)

    url, headers = _apply_openapi_auth(url=url, headers=headers, auth=auth)

    body_bytes: bytes | None = None
    body_payload = request.inputs.get("body")
    if body_payload is not None and method in {"post", "put", "patch", "delete"}:
        body_bytes = json.dumps(body_payload).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    return url, method, headers, body_bytes


def _openapi_base_url(spec: dict[str, Any]) -> str:
    """Pick the first ``servers[].url`` entry from the spec.

    OpenAPI 3.x specs are required to declare a ``servers`` array; we
    honour the first entry as a deterministic, operator-overrideable
    default. Older Swagger 2.x specs use ``host`` + ``basePath`` +
    ``schemes`` — we synthesise the equivalent URL when present so
    common Swagger-2 catalogue entries (Swagger Hub, APIs.guru) work
    without a manual conversion.
    """

    servers = spec.get("servers")
    if isinstance(servers, list) and servers:
        first = servers[0]
        if isinstance(first, dict) and isinstance(first.get("url"), str):
            return first["url"]

    host = spec.get("host")
    if isinstance(host, str) and host:
        scheme_list = spec.get("schemes")
        scheme = (
            scheme_list[0]
            if isinstance(scheme_list, list) and scheme_list
            else "https"
        )
        base_path = spec.get("basePath") or ""
        return f"{scheme}://{host}{base_path}"
    return ""


def _apply_openapi_auth(
    *,
    url: str,
    headers: dict[str, str],
    auth: Any,
) -> tuple[str, dict[str, str]]:
    """Apply the ``registry_agent.auth`` block, if any.

    Unknown / malformed auth blocks are silently ignored — the request
    will then 401 against the upstream, which is exactly the operator
    feedback they need.
    """

    if not isinstance(auth, dict):
        return url, headers

    auth_type = str(auth.get("type") or "").lower()
    env_var = str(auth.get("env") or "")
    secret = os.getenv(env_var) if env_var else None
    if not secret:
        # No secret available — leave the request unauthenticated.
        # The upstream's 401 is the right place for this failure.
        return url, headers

    if auth_type == "bearer":
        headers = {**headers, "Authorization": f"Bearer {secret}"}
        return url, headers
    if auth_type == "api_key":
        header_name = str(auth.get("header") or "")
        if header_name:
            headers = {**headers, header_name: secret}
            return url, headers
        query_param = str(auth.get("query") or "")
        if query_param:
            url = url + ("&" if "?" in url else "?") + parse.urlencode(
                {query_param: secret}
            )
            return url, headers
    return url, headers


def _http_call(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
) -> tuple[int, str]:
    """Perform a generic HTTP call and return ``(status, body)``."""

    req = request.Request(
        url,
        data=body,
        method=method.upper(),
        headers=headers,
    )
    try:
        with request.urlopen(req, timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310
            raw = response.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
            status_code = response.status
    except error.HTTPError as exc:  # 4xx / 5xx still produce a body
        try:
            raw = exc.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            raw = ""
        return exc.code, raw
    except (OSError, TimeoutError, error.URLError) as exc:
        raise _ProtocolHttpError(str(exc)) from exc
    return status_code, raw


def _try_parse_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# --- shared error type ---------------------------------------------


class _ProtocolHttpError(RuntimeError):
    """Marker for transport / parse failures the adapter converts to
    a structured ``ProviderResponse(succeeded=False)``."""


# Backwards-compat: some operator scripts and tests imported the
# private ``_fetch_protocol_metadata`` helper from the v0 of this
# module to do a "what does this URL serve?" probe. Keep it around as
# a thin wrapper over the new transport so those callers don't break,
# but mark it as legacy in the docstring.
def _fetch_protocol_metadata(url: str) -> dict[str, Any]:
    """Legacy helper retained for backwards compat.

    Originally used by the metadata-only stub of GenericProtocolAdapter
    to fetch arbitrary JSON / text from a discovered URL. New code
    should use the protocol-specific adapters (which validate the
    response shape against the protocol contract). This stays a no-
    schema fetch so existing callers don't break.
    """

    if not _is_http_url(url):
        raise ProtocolAdapterExecutionBlocked(
            "Protocol metadata URL is missing or invalid."
        )
    req = request.Request(
        url,
        headers={"Accept": "application/json,text/plain", "User-Agent": _USER_AGENT},
    )
    try:
        with request.urlopen(req, timeout=8.0) as response:  # noqa: S310
            text = response.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="ignore")
    except (OSError, TimeoutError, error.URLError) as exc:
        raise ProtocolAdapterExecutionBlocked(
            f"Protocol metadata fetch failed: {exc}"
        ) from exc
    parsed = _try_parse_json(text)
    if isinstance(parsed, dict):
        return parsed
    if parsed is not None:
        return {"items": parsed}
    return {"text_preview": text[:1000]}


# Sequence import retained for the legacy public API; not used in v1
# but referenced by some operator scripts that introspect the module.
__all__: Sequence[str] = (
    "ENABLE_PROTOCOL_EXECUTION_ENV",
    "GenericA2AAdapter",
    "GenericAiAgentAdapter",
    "GenericMcpAdapter",
    "GenericOpenApiAdapter",
    "GenericProtocolAdapter",
    "ProtocolAdapterExecutionBlocked",
)
