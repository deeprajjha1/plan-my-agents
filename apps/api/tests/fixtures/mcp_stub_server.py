"""A deterministic in-process HTTP MCP server stub for eval tests.

Speaks just enough of the MCP JSON-RPC ``tools/call`` wire format that
``GenericMcpAdapter`` can invoke it. It echoes a deterministic result keyed
by the requested arguments so exact-match scoring is reproducible and the
whole eval path is testable in CI with no external network.

Usage::

    with McpStubServer({"scrape": lambda args: {"status": "ok"}}) as base_url:
        ...  # base_url is e.g. "http://127.0.0.1:54321"
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ToolFn = Callable[[dict], dict]


class McpStubServer:
    def __init__(self, tools: dict[str, ToolFn]) -> None:
        self._tools = tools
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> str:
        tools = self._tools

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence test noise
                return

            def do_POST(self):  # noqa: N802 - http.server contract
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    payload = {}
                rpc_id = payload.get("id", "1")
                params = payload.get("params", {}) if isinstance(payload, dict) else {}
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {}) or {}

                fn = tools.get(tool_name)
                if fn is None:
                    response = {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {"code": -32601, "message": f"unknown tool {tool_name}"},
                    }
                else:
                    result_obj = fn(arguments)
                    response = {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "isError": False,
                            "content": [
                                {"type": "text", "text": json.dumps(result_obj)}
                            ],
                            # Mirror the structured object too so adapters that
                            # read structured content can use it.
                            **result_obj,
                        },
                    }
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        host, port = self._server.server_address
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://{host}:{port}"

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
