"""Tests for the Groq chat client."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.planner.groq_client import (
    DEFAULT_GROQ_MODEL,
    GroqChatClient,
    GroqChatError,
)


class _StubResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


class GroqChatClientTests(unittest.TestCase):
    def test_raises_when_api_key_missing(self) -> None:
        client = GroqChatClient(api_key="")
        with self.assertRaises(GroqChatError):
            client.complete([{"role": "user", "content": "hi"}])

    def test_returns_first_choice_content(self) -> None:
        client = GroqChatClient(api_key="key", model=DEFAULT_GROQ_MODEL)
        response_body = json.dumps(
            {
                "choices": [
                    {"message": {"content": '{"status":"unsupported"}'}}
                ]
            }
        ).encode("utf-8")

        with patch("planmyagents_api.planner.groq_client.request.urlopen") as mock:
            mock.return_value = _StubResponse(response_body)
            content = client.complete([{"role": "user", "content": "plan"}])
        self.assertEqual(content, '{"status":"unsupported"}')

        args, kwargs = mock.call_args
        sent_request = args[0]
        self.assertEqual(sent_request.method, "POST")
        self.assertTrue(sent_request.full_url.endswith("/chat/completions"))
        sent_payload = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(sent_payload["model"], DEFAULT_GROQ_MODEL)
        self.assertEqual(sent_payload["temperature"], 0)
        self.assertEqual(
            sent_payload["response_format"], {"type": "json_object"}
        )

    def test_raises_when_response_has_no_choices(self) -> None:
        client = GroqChatClient(api_key="key")
        response_body = json.dumps({"choices": []}).encode("utf-8")
        with patch("planmyagents_api.planner.groq_client.request.urlopen") as mock:
            mock.return_value = _StubResponse(response_body)
            with self.assertRaises(GroqChatError):
                client.complete([{"role": "user", "content": "x"}])

    def test_raises_when_completion_is_empty(self) -> None:
        client = GroqChatClient(api_key="key")
        response_body = json.dumps(
            {"choices": [{"message": {"content": "  "}}]}
        ).encode("utf-8")
        with patch("planmyagents_api.planner.groq_client.request.urlopen") as mock:
            mock.return_value = _StubResponse(response_body)
            with self.assertRaises(GroqChatError):
                client.complete([{"role": "user", "content": "x"}])

    def test_overrides_default_user_agent_to_avoid_cloudflare_403(self) -> None:
        """Regression for the Cloudflare 1010 block.

        ``api.groq.com`` is fronted by Cloudflare which returns
        ``403 Forbidden`` (Cloudflare error code 1010) for any request
        whose User-Agent matches its bot blocklist. ``urllib``'s default
        UA is ``Python-urllib/3.x`` and is on that list — observed in a
        live ``/goal`` smoke run where every Groq call from the planner
        failed in ~200ms with ``HTTP Error 403: Forbidden`` and the
        escalating client fell back to local Qwen on every request,
        adding the failed Groq round-trip on top of Qwen's 60s+ latency
        instead of replacing it.

        We must therefore set an explicit User-Agent on every request.
        This test pins that contract so a future refactor doesn't
        accidentally drop it.
        """

        client = GroqChatClient(api_key="key", model=DEFAULT_GROQ_MODEL)
        response_body = json.dumps(
            {"choices": [{"message": {"content": "{}"}}]}
        ).encode("utf-8")

        with patch("planmyagents_api.planner.groq_client.request.urlopen") as mock:
            mock.return_value = _StubResponse(response_body)
            client.complete([{"role": "user", "content": "plan as json"}])

        sent_request = mock.call_args[0][0]
        # urllib normalises header names to title-case via
        # add_header / get_header, which is why we look up "User-agent".
        ua = sent_request.headers.get("User-agent")
        self.assertIsNotNone(ua, "GroqChatClient must set a User-Agent header")
        self.assertNotIn(
            "python-urllib", ua.lower(),
            "User-Agent must not be the urllib default (Cloudflare blocks it)",
        )

    def test_http_error_includes_response_body_in_error_message(self) -> None:
        """Regression for opaque transport errors.

        The original implementation translated ``urllib.error.HTTPError``
        into a bare ``GroqChatError("transport failure: HTTP Error 403:
        Forbidden")``. That error message gave operators no way to tell
        a Cloudflare 403 (User-Agent issue) from a credentials 401
        (key revoked) from a 429 (rate limit). The fix is to read the
        response body and append it to the error message, capped at 500
        chars so a verbose HTML error page doesn't blow up logs.
        """

        from urllib import error as _url_error

        class _StubHTTPError(_url_error.HTTPError):
            def __init__(self) -> None:
                super().__init__(
                    url="https://api.groq.com/openai/v1/chat/completions",
                    code=403,
                    msg="Forbidden",
                    hdrs=None,  # type: ignore[arg-type]
                    fp=None,
                )

            def read(self) -> bytes:  # type: ignore[override]
                return b"error code: 1010"

        client = GroqChatClient(api_key="key", model=DEFAULT_GROQ_MODEL)
        with patch("planmyagents_api.planner.groq_client.request.urlopen") as mock:
            mock.side_effect = _StubHTTPError()
            with self.assertRaises(GroqChatError) as ctx:
                client.complete([{"role": "user", "content": "x"}])

        message = str(ctx.exception)
        self.assertIn("HTTP Error 403", message)
        self.assertIn("error code: 1010", message)


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
