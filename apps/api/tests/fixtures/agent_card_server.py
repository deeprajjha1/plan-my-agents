"""A deterministic in-process HTTP server that serves an A2A Agent Card.

Lets the card-ingestion tests exercise the full resolve → verify → persist
path against ANY simulated published card, with no external network.

Usage::

    card = {"name": "PolicyCheck", "url": "...", "skills": [...]}
    with AgentCardServer(card) as base_url:
        service.ingest(f"{base_url}/.well-known/agent.json")
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class AgentCardServer:
    """Serves a single agent card JSON at any GET path.

    ``card`` is returned for any path by default (so both
    ``/.well-known/agent.json`` and a custom path work). Pass
    ``oversized=True`` to emit a body larger than the ingestion size cap,
    or ``invalid_json=True`` to emit non-JSON, for negative tests.
    """

    def __init__(
        self,
        card: dict[str, Any],
        *,
        oversized: bool = False,
        invalid_json: bool = False,
    ) -> None:
        self._card = card
        self._oversized = oversized
        self._invalid_json = invalid_json
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> str:
        card = self._card
        oversized = self._oversized
        invalid_json = self._invalid_json

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence test noise
                return

            def do_GET(self):  # noqa: N802 - http.server contract
                if invalid_json:
                    body = b"this is not json {{{"
                elif oversized:
                    # ~2MB of padding inside a valid JSON doc.
                    padded = dict(card)
                    padded["_pad"] = "x" * 2_000_000
                    body = json.dumps(padded).encode("utf-8")
                else:
                    body = json.dumps(card).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

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
