"""Tests for the Gap-4 generic protocol adapters.

These adapters used to return a synthesised "metadata_only" success
no matter what URL they pointed at. Gap 4 replaced that stub with
real invocation paths for MCP (JSON-RPC ``tools/call``) and OpenAPI
(re-fetch spec, build request, call), and structured refusals for
A2A and free-form AI agents (whose wire formats aren't pinned down
enough for a generic client).

This test file pins three layers of contract:

1. Safety gate — every adapter returns a "disabled" response when
   ``PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION`` is unset, no
   matter how complete the registry blob is. A misconfigured deploy
   cannot accidentally hit live third parties.
2. Capability validation — requesting a capability the adapter
   doesn't declare raises ``ValueError``. This is a routing-time
   misconfiguration; failing fast at the boundary keeps the bug
   close to its source.
3. Per-protocol invocation — MCP and OpenAPI happy paths and the
   most important error paths (network failure, malformed response,
   tool-level error envelope, missing tool / operation, missing
   path parameter, HTTP error status).

Network is stubbed throughout via ``urllib.request.urlopen`` patches.
The adapter never touches a live endpoint inside this test file.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error as urllib_error

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.protocol import (
    ENABLE_PROTOCOL_EXECUTION_ENV,
    GenericA2AAdapter,
    GenericAiAgentAdapter,
    GenericMcpAdapter,
    GenericOpenApiAdapter,
    GenericProtocolAdapter,
)
from planmyagents_api.benchmark.models import ProviderRequest


def _enable_execution_env():
    """Context manager wrapper used by every test that needs the gate
    flipped. Captured as a function rather than a setUp because some
    tests want to compare gated-on vs gated-off behaviour in the
    same method."""

    return patch.dict(
        os.environ,
        {ENABLE_PROTOCOL_EXECUTION_ENV: "true"},
        clear=False,
    )


def _make_request(*, capability="cap_a", inputs=None, idem="test-key") -> ProviderRequest:
    return ProviderRequest(
        capability=capability,
        inputs=dict(inputs or {}),
        idempotency_key=idem,
    )


def _fake_urlopen_factory(body: bytes, *, status: int = 200):
    """Build a fake urlopen returning ``body`` with a stubbed
    ``response.status``. Tests that need to assert on the request shape
    capture the ``Request`` object themselves; tests that only care
    about the response use this factory directly."""

    class _FakeResponse:
        def __init__(self, payload: bytes, status_code: int) -> None:
            self._stream = io.BytesIO(payload)
            self.status = status_code

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self._stream.close()

        def read(self, _size: int = -1) -> bytes:
            return self._stream.read()

    def _fake(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(body, status)

    return _fake


# ---------------------------------------------------------------------------
# Capability validation


class CapabilityValidationTests(unittest.TestCase):
    def test_request_for_unknown_capability_raises_value_error(self) -> None:
        adapter = GenericMcpAdapter(
            registry_agent={
                "id": "test-mcp",
                "vendor_url": "https://example.com",
                "capabilities": [{"id": "cap_a"}],
            }
        )
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(adapter.execute(_make_request(capability="cap_b")))
        self.assertIn("does not support capability", str(ctx.exception))


# ---------------------------------------------------------------------------
# Safety gate


class ExecutionGateTests(unittest.TestCase):
    """The gate is OFF by default. Every adapter must check it BEFORE
    doing anything observable (no network calls, no log spam)."""

    def setUp(self) -> None:
        # Belt-and-braces: hermetic-test conftest doesn't touch this var,
        # so we explicitly clear it to re-confirm the off-by-default
        # contract.
        self._prev = os.environ.pop(ENABLE_PROTOCOL_EXECUTION_ENV, None)

    def tearDown(self) -> None:
        if self._prev is not None:
            os.environ[ENABLE_PROTOCOL_EXECUTION_ENV] = self._prev

    def _build_adapter(self, cls):
        return cls(
            registry_agent={
                "id": f"test-{cls.__name__}",
                "vendor_url": "https://example.com",
                "openapi_url": "https://example.com/openapi.json",
                "capabilities": [{"id": "cap_a"}],
            }
        )

    def test_mcp_adapter_returns_disabled_response_when_gate_off(self) -> None:
        response = asyncio.run(
            self._build_adapter(GenericMcpAdapter).execute(_make_request())
        )
        self.assertFalse(response.succeeded)
        self.assertIn("execution is disabled", response.error)
        self.assertEqual(0.0, response.cost_usd)

    def test_openapi_adapter_returns_disabled_response_when_gate_off(self) -> None:
        response = asyncio.run(
            self._build_adapter(GenericOpenApiAdapter).execute(_make_request())
        )
        self.assertFalse(response.succeeded)
        self.assertIn("execution is disabled", response.error)

    def test_a2a_adapter_returns_disabled_response_when_gate_off(self) -> None:
        """Even though A2A would refuse anyway (spec not implemented),
        the gate's "disabled" message wins so an operator sees an
        actionable env-var error before they see a "not implemented"
        feature limitation."""

        response = asyncio.run(
            self._build_adapter(GenericA2AAdapter).execute(_make_request())
        )
        self.assertFalse(response.succeeded)
        self.assertIn("execution is disabled", response.error)

    def test_ai_agent_adapter_returns_disabled_response_when_gate_off(self) -> None:
        response = asyncio.run(
            self._build_adapter(GenericAiAgentAdapter).execute(_make_request())
        )
        self.assertFalse(response.succeeded)
        self.assertIn("execution is disabled", response.error)

    def test_base_adapter_refuses_with_subclass_promotion_message(self) -> None:
        """Direct ``GenericProtocolAdapter()`` use is a registry/router
        misconfiguration. The base class has no protocol to invoke;
        the adapter surfaces a structured refusal pointing at the fix."""

        adapter = GenericProtocolAdapter(
            registry_agent={
                "id": "raw",
                "vendor_url": "https://example.com",
                "capabilities": [{"id": "cap_a"}],
            }
        )
        response = asyncio.run(adapter.execute(_make_request()))
        self.assertFalse(response.succeeded)
        self.assertIn("base adapter has no concrete invocation surface", response.error)


# ---------------------------------------------------------------------------
# A2A / AI agent honest refusals (gate ON)


class A2AAndAiAgentRefusalTests(unittest.TestCase):
    def test_a2a_refuses_with_implementation_explanation_when_gate_on(self) -> None:
        adapter = GenericA2AAdapter(
            registry_agent={
                "id": "a2a-test",
                "vendor_url": "https://example.com",
                "capabilities": [{"id": "cap_a"}],
            }
        )
        with _enable_execution_env():
            response = asyncio.run(adapter.execute(_make_request()))
        self.assertFalse(response.succeeded)
        self.assertIn("A2A skill invocation is not implemented", response.error)
        self.assertTrue(response.raw_response.get("refused"))
        self.assertEqual("a2a", response.raw_response.get("protocol"))

    def test_ai_agent_refuses_with_no_wire_format_explanation_when_gate_on(self) -> None:
        adapter = GenericAiAgentAdapter(
            registry_agent={
                "id": "ai-test",
                "vendor_url": "https://example.com",
                "capabilities": [{"id": "cap_a"}],
            }
        )
        with _enable_execution_env():
            response = asyncio.run(adapter.execute(_make_request()))
        self.assertFalse(response.succeeded)
        self.assertIn("no published wire format", response.error)


# ---------------------------------------------------------------------------
# MCP adapter


class GenericMcpAdapterTests(unittest.TestCase):
    """End-to-end MCP ``tools/call`` invocation, network stubbed."""

    def setUp(self) -> None:
        self._gate_ctx = _enable_execution_env()
        self._gate_ctx.start()

    def tearDown(self) -> None:
        self._gate_ctx.stop()

    def _adapter_with_one_tool(self, *, tool_name="send_email"):
        return GenericMcpAdapter(
            registry_agent={
                "id": "mail-mcp",
                "vendor_url": "https://mail.example/mcp",
                "capabilities": [{"id": "email_send"}],
                "tools": [{"name": tool_name, "description": "send an email"}],
            }
        )

    def test_happy_path_extracts_text_content_and_returns_succeeded(self) -> None:
        """tools/call returns ``{result: {content: [{type: text, text: ...}]}}``
        on success; the adapter normalises this to ``output.text``."""

        captured = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["headers"] = dict(req.header_items())
            return _fake_urlopen_factory(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "test-key",
                        "result": {
                            "isError": False,
                            "content": [{"type": "text", "text": "queued msg-42"}],
                        },
                    }
                ).encode("utf-8")
            )(req, timeout=timeout)

        with patch("planmyagents_api.agents.protocol.request.urlopen", fake_urlopen):
            response = asyncio.run(
                self._adapter_with_one_tool().execute(
                    _make_request(
                        capability="email_send",
                        inputs={"to": "x@example.com", "subject": "hi", "body": "test"},
                    )
                )
            )

        self.assertTrue(response.succeeded)
        self.assertEqual("send_email", response.output["tool_name"])
        self.assertEqual("queued msg-42", response.output["text"])
        self.assertEqual(0.0, response.cost_usd)
        # Wire shape is real JSON-RPC 2.0 with the right method.
        self.assertEqual("https://mail.example/mcp", captured["url"])
        self.assertEqual("tools/call", captured["body"]["method"])
        self.assertEqual("send_email", captured["body"]["params"]["name"])
        # Caller didn't pass an ``arguments`` dict explicitly; the
        # adapter forwards non-reserved inputs as the arguments dict
        # for ergonomics.
        self.assertEqual(
            {"to": "x@example.com", "subject": "hi", "body": "test"},
            captured["body"]["params"]["arguments"],
        )

    def test_explicit_tool_name_input_overrides_auto_resolution(self) -> None:
        """If the caller specifies inputs.tool_name, use it verbatim
        (production routing path) — even if a different tool's name
        would also pass the auto-resolver."""

        adapter = GenericMcpAdapter(
            registry_agent={
                "id": "multi",
                "vendor_url": "https://multi.example",
                "capabilities": [{"id": "email_send"}],
                "tools": [
                    {"name": "auto_match_email_send"},
                    {"name": "send_via_smtp_explicit"},
                ],
            }
        )
        captured = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _fake_urlopen_factory(
                json.dumps(
                    {"jsonrpc": "2.0", "id": "k", "result": {"content": []}}
                ).encode("utf-8")
            )(req, timeout=timeout)

        with patch("planmyagents_api.agents.protocol.request.urlopen", fake_urlopen):
            asyncio.run(
                adapter.execute(
                    _make_request(
                        capability="email_send",
                        inputs={"tool_name": "send_via_smtp_explicit", "to": "x"},
                    )
                )
            )

        self.assertEqual("send_via_smtp_explicit", captured["body"]["params"]["name"])

    def test_explicit_arguments_block_wins_over_kwarg_forwarding(self) -> None:
        """When inputs has both ``arguments`` AND extra top-level keys,
        the explicit ``arguments`` dict is what reaches the server.
        Letting both flow would silently double-specify and let the
        server pick — too dangerous when the keys differ."""

        captured = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _fake_urlopen_factory(
                json.dumps(
                    {"jsonrpc": "2.0", "id": "k", "result": {"content": []}}
                ).encode("utf-8")
            )(req, timeout=timeout)

        with patch("planmyagents_api.agents.protocol.request.urlopen", fake_urlopen):
            asyncio.run(
                self._adapter_with_one_tool().execute(
                    _make_request(
                        capability="email_send",
                        inputs={
                            "arguments": {"to": "explicit"},
                            # Should be ignored because arguments was provided.
                            "to": "implicit",
                            "extra": "ignored",
                        },
                    )
                )
            )
        self.assertEqual({"to": "explicit"}, captured["body"]["params"]["arguments"])

    def test_jsonrpc_error_envelope_returns_succeeded_false(self) -> None:
        """JSON-RPC ``error`` envelope is mutually exclusive with
        ``result``; the adapter must surface it as succeeded=False with
        the upstream code/message in the error string."""

        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "test",
                "error": {"code": -32602, "message": "Invalid params"},
            }
        ).encode("utf-8")
        with patch(
            "planmyagents_api.agents.protocol.request.urlopen",
            _fake_urlopen_factory(body),
        ):
            response = asyncio.run(
                self._adapter_with_one_tool().execute(
                    _make_request(capability="email_send", inputs={"to": "x"})
                )
            )
        self.assertFalse(response.succeeded)
        self.assertIn("jsonrpc error -32602", response.error)
        self.assertIn("Invalid params", response.error)

    def test_tool_isError_true_with_text_content_returns_succeeded_false(self) -> None:
        """Per the MCP spec, tool-level failures surface as
        ``result.isError = true`` with a text content block carrying
        the explanation. The adapter respects that flag — succeeded
        follows the tool's verdict, not the JSON-RPC envelope."""

        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "test",
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "rate limited"}],
                },
            }
        ).encode("utf-8")
        with patch(
            "planmyagents_api.agents.protocol.request.urlopen",
            _fake_urlopen_factory(body),
        ):
            response = asyncio.run(
                self._adapter_with_one_tool().execute(
                    _make_request(capability="email_send", inputs={"to": "x"})
                )
            )
        self.assertFalse(response.succeeded)
        self.assertEqual("rate limited", response.error)
        self.assertEqual("rate limited", response.output["text"])

    def test_transport_failure_returns_succeeded_false_with_transport_error(self) -> None:
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            raise urllib_error.URLError("connection refused")

        with patch("planmyagents_api.agents.protocol.request.urlopen", fake_urlopen):
            response = asyncio.run(
                self._adapter_with_one_tool().execute(
                    _make_request(capability="email_send", inputs={"to": "x"})
                )
            )
        self.assertFalse(response.succeeded)
        self.assertIn("transport:", response.error)

    def test_http_error_response_is_caught_and_returned_as_failure(self) -> None:
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            raise urllib_error.HTTPError(
                req.full_url, 500, "Server Error", {}, io.BytesIO(b"upstream blew up")
            )

        with patch("planmyagents_api.agents.protocol.request.urlopen", fake_urlopen):
            response = asyncio.run(
                self._adapter_with_one_tool().execute(
                    _make_request(capability="email_send", inputs={"to": "x"})
                )
            )
        self.assertFalse(response.succeeded)
        self.assertIn("transport:", response.error)
        self.assertIn("http 500", response.error)

    def test_invalid_json_response_body_returns_failure(self) -> None:
        """Server returned 200 but the body isn't JSON — a malformed
        upstream that must be surfaced rather than swallowed."""

        with patch(
            "planmyagents_api.agents.protocol.request.urlopen",
            _fake_urlopen_factory(b"not json at all"),
        ):
            response = asyncio.run(
                self._adapter_with_one_tool().execute(
                    _make_request(capability="email_send", inputs={"to": "x"})
                )
            )
        self.assertFalse(response.succeeded)
        self.assertIn("transport:", response.error)
        self.assertIn("invalid JSON", response.error)

    def test_no_tools_and_no_explicit_tool_name_returns_structured_refusal(self) -> None:
        """Tool-less candidate + no inputs.tool_name = no honest tool
        to call. Adapter refuses rather than guessing."""

        adapter = GenericMcpAdapter(
            registry_agent={
                "id": "no-tools",
                "vendor_url": "https://example.com",
                "capabilities": [{"id": "cap_a"}],
                "tools": [],
            }
        )
        response = asyncio.run(adapter.execute(_make_request()))
        self.assertFalse(response.succeeded)
        self.assertIn("could not resolve a tool", response.error)
        self.assertTrue(response.raw_response.get("refused"))

    def test_multiple_tools_no_match_returns_structured_refusal(self) -> None:
        """Several tools, none whose name overlaps the capability —
        auto-resolution refuses rather than picking arbitrarily."""

        adapter = GenericMcpAdapter(
            registry_agent={
                "id": "many",
                "vendor_url": "https://example.com",
                "capabilities": [{"id": "payment_authorization"}],
                "tools": [
                    {"name": "ping"},
                    {"name": "list_users"},
                    {"name": "fetch_metadata"},
                ],
            }
        )
        response = asyncio.run(adapter.execute(_make_request(capability="payment_authorization")))
        self.assertFalse(response.succeeded)
        self.assertIn("could not resolve a tool", response.error)

    def test_stdio_only_server_url_is_refused(self) -> None:
        """A discovered MCP server with no HTTP URL (e.g. stdio-only)
        cannot be invoked from this adapter — return a structured
        refusal instead of trying to POST to an empty string."""

        adapter = GenericMcpAdapter(
            registry_agent={
                "id": "stdio",
                "vendor_url": "",
                "capabilities": [{"id": "cap_a"}],
                "tools": [{"name": "a_tool"}],
            }
        )
        response = asyncio.run(adapter.execute(_make_request()))
        self.assertFalse(response.succeeded)
        self.assertIn("no HTTP api_base_url", response.error)


# ---------------------------------------------------------------------------
# OpenAPI adapter


class GenericOpenApiAdapterTests(unittest.TestCase):
    """End-to-end OpenAPI invocation. Spec re-fetch + operation pick +
    request build + HTTP call, all stubbed at the urlopen layer."""

    def setUp(self) -> None:
        self._gate_ctx = _enable_execution_env()
        self._gate_ctx.start()

    def tearDown(self) -> None:
        self._gate_ctx.stop()

    def _spec(self) -> dict:
        return {
            "openapi": "3.0.0",
            "servers": [{"url": "https://api.example.com/v1"}],
            "paths": {
                "/users/{id}": {
                    "get": {
                        "operationId": "user_lookup",
                        "summary": "Look up a user",
                        "parameters": [
                            {"name": "id", "in": "path", "required": True}
                        ],
                    }
                },
                "/payments": {
                    "post": {
                        "operationId": "payment_create",
                        "summary": "Create a payment",
                    }
                },
            },
        }

    def _build_two_response_urlopen(self, *, second_status=200, second_body=b"{}"):
        """Return a fake urlopen that serves the spec on call 1 and
        the operation response on call 2. Captures the second request
        for assertions."""

        captured: dict = {}

        spec_body = json.dumps(self._spec()).encode("utf-8")
        call_count = {"n": 0}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Spec fetch.
                return _fake_urlopen_factory(spec_body)(req, timeout=timeout)
            # Operation call.
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["headers"] = dict(req.header_items())
            captured["body"] = req.data
            return _fake_urlopen_factory(second_body, status=second_status)(
                req, timeout=timeout
            )

        return fake_urlopen, captured

    def test_happy_path_get_with_path_param_substitutes_and_returns_body(self) -> None:
        adapter = GenericOpenApiAdapter(
            registry_agent={
                "id": "api",
                "openapi_url": "https://api.example.com/spec.json",
                "capabilities": [{"id": "user_lookup"}],
            }
        )

        fake, captured = self._build_two_response_urlopen(
            second_body=b'{"name": "alice"}'
        )
        with patch("planmyagents_api.agents.protocol.request.urlopen", fake):
            response = asyncio.run(
                adapter.execute(
                    _make_request(
                        capability="user_lookup",
                        inputs={"path_params": {"id": "u-1"}},
                    )
                )
            )

        self.assertTrue(response.succeeded)
        self.assertEqual(200, response.output["status_code"])
        self.assertEqual({"name": "alice"}, response.output["body"])
        self.assertEqual("https://api.example.com/v1/users/u-1", captured["url"])
        self.assertEqual("GET", captured["method"])

    def test_post_with_body_serializes_json_and_sets_content_type(self) -> None:
        adapter = GenericOpenApiAdapter(
            registry_agent={
                "id": "api",
                "openapi_url": "https://api.example.com/spec.json",
                "capabilities": [{"id": "payment_create"}],
            }
        )

        fake, captured = self._build_two_response_urlopen(second_body=b'{"id": "pay_1"}')
        with patch("planmyagents_api.agents.protocol.request.urlopen", fake):
            response = asyncio.run(
                adapter.execute(
                    _make_request(
                        capability="payment_create",
                        inputs={"body": {"amount": 100, "currency": "USD"}},
                    )
                )
            )

        self.assertTrue(response.succeeded)
        self.assertEqual("POST", captured["method"])
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual({"amount": 100, "currency": "USD"}, sent)
        headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual("application/json", headers_lower.get("content-type"))

    def test_explicit_operation_id_input_picks_that_operation(self) -> None:
        adapter = GenericOpenApiAdapter(
            registry_agent={
                "id": "api",
                "openapi_url": "https://api.example.com/spec.json",
                "capabilities": [
                    {"id": "user_lookup"},
                    {"id": "payment_create"},
                ],
            }
        )

        fake, captured = self._build_two_response_urlopen(second_body=b'{"id": "pay_explicit"}')
        with patch("planmyagents_api.agents.protocol.request.urlopen", fake):
            asyncio.run(
                adapter.execute(
                    _make_request(
                        capability="user_lookup",
                        inputs={"operation_id": "payment_create", "body": {"a": 1}},
                    )
                )
            )

        # Routed to /payments (POST) because operation_id won, not to
        # /users/{id} (GET) which would have been the capability match.
        self.assertEqual("https://api.example.com/v1/payments", captured["url"])
        self.assertEqual("POST", captured["method"])

    def test_missing_path_param_returns_structured_refusal(self) -> None:
        adapter = GenericOpenApiAdapter(
            registry_agent={
                "id": "api",
                "openapi_url": "https://api.example.com/spec.json",
                "capabilities": [{"id": "user_lookup"}],
            }
        )

        spec_body = json.dumps(self._spec()).encode("utf-8")

        with patch(
            "planmyagents_api.agents.protocol.request.urlopen",
            _fake_urlopen_factory(spec_body),
        ):
            response = asyncio.run(
                adapter.execute(
                    _make_request(capability="user_lookup", inputs={})
                )
            )
        self.assertFalse(response.succeeded)
        self.assertIn("missing required path parameter 'id'", response.error)
        self.assertTrue(response.raw_response.get("refused"))

    def test_unknown_operation_returns_structured_refusal(self) -> None:
        """Capability-name overlap fails AND no inputs.operation_id —
        adapter refuses rather than picking the first available
        operation arbitrarily."""

        adapter = GenericOpenApiAdapter(
            registry_agent={
                "id": "api",
                "openapi_url": "https://api.example.com/spec.json",
                "capabilities": [{"id": "completely_unrelated"}],
            }
        )
        spec_body = json.dumps(self._spec()).encode("utf-8")
        with patch(
            "planmyagents_api.agents.protocol.request.urlopen",
            _fake_urlopen_factory(spec_body),
        ):
            response = asyncio.run(
                adapter.execute(_make_request(capability="completely_unrelated"))
            )
        self.assertFalse(response.succeeded)
        self.assertIn("could not resolve an operation", response.error)

    def test_http_error_status_marks_response_as_failed_but_still_returns(self) -> None:
        """A 5xx response is a real network result, not a transport
        failure — we still return it (with the body preview) so the
        caller can decide whether to retry."""

        adapter = GenericOpenApiAdapter(
            registry_agent={
                "id": "api",
                "openapi_url": "https://api.example.com/spec.json",
                "capabilities": [{"id": "user_lookup"}],
            }
        )

        fake, captured = self._build_two_response_urlopen(
            second_status=500, second_body=b"db is on fire"
        )
        with patch("planmyagents_api.agents.protocol.request.urlopen", fake):
            response = asyncio.run(
                adapter.execute(
                    _make_request(
                        capability="user_lookup",
                        inputs={"path_params": {"id": "u-1"}},
                    )
                )
            )
        self.assertFalse(response.succeeded)
        self.assertIn("http 500", response.error)
        self.assertIn("db is on fire", response.raw_response["body_preview"])

    def test_bearer_auth_header_is_added_from_env(self) -> None:
        adapter = GenericOpenApiAdapter(
            registry_agent={
                "id": "api",
                "openapi_url": "https://api.example.com/spec.json",
                "capabilities": [{"id": "user_lookup"}],
                "auth": {"type": "bearer", "env": "MY_API_KEY"},
            }
        )

        fake, captured = self._build_two_response_urlopen(second_body=b"{}")
        with patch.dict(os.environ, {"MY_API_KEY": "sk_test_abc"}, clear=False), patch(
            "planmyagents_api.agents.protocol.request.urlopen", fake
        ):
            asyncio.run(
                adapter.execute(
                    _make_request(
                        capability="user_lookup",
                        inputs={"path_params": {"id": "u-1"}},
                    )
                )
            )
        headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual("Bearer sk_test_abc", headers_lower.get("authorization"))

    def test_api_key_header_auth_is_added_from_env(self) -> None:
        adapter = GenericOpenApiAdapter(
            registry_agent={
                "id": "api",
                "openapi_url": "https://api.example.com/spec.json",
                "capabilities": [{"id": "user_lookup"}],
                "auth": {"type": "api_key", "header": "X-API-Key", "env": "MY_KEY"},
            }
        )
        fake, captured = self._build_two_response_urlopen()
        with patch.dict(os.environ, {"MY_KEY": "abc-123"}, clear=False), patch(
            "planmyagents_api.agents.protocol.request.urlopen", fake
        ):
            asyncio.run(
                adapter.execute(
                    _make_request(
                        capability="user_lookup",
                        inputs={"path_params": {"id": "u-1"}},
                    )
                )
            )
        headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual("abc-123", headers_lower.get("x-api-key"))

    def test_api_key_query_auth_is_added_to_url(self) -> None:
        adapter = GenericOpenApiAdapter(
            registry_agent={
                "id": "api",
                "openapi_url": "https://api.example.com/spec.json",
                "capabilities": [{"id": "user_lookup"}],
                "auth": {"type": "api_key", "query": "key", "env": "MY_KEY"},
            }
        )
        fake, captured = self._build_two_response_urlopen()
        with patch.dict(os.environ, {"MY_KEY": "abc-123"}, clear=False), patch(
            "planmyagents_api.agents.protocol.request.urlopen", fake
        ):
            asyncio.run(
                adapter.execute(
                    _make_request(
                        capability="user_lookup",
                        inputs={"path_params": {"id": "u-1"}},
                    )
                )
            )
        self.assertIn("key=abc-123", captured["url"])

    def test_missing_openapi_url_returns_refusal(self) -> None:
        adapter = GenericOpenApiAdapter(
            registry_agent={
                "id": "api",
                "openapi_url": "",
                "capabilities": [{"id": "user_lookup"}],
            }
        )
        response = asyncio.run(adapter.execute(_make_request(capability="user_lookup")))
        self.assertFalse(response.succeeded)
        self.assertIn("openapi_url is missing", response.error)


if __name__ == "__main__":
    unittest.main()
