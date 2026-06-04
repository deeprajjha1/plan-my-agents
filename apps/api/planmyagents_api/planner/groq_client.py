"""Groq chat client implementing the same ``ChatClient`` protocol as Ollama.

Groq serves Llama 3.x / Mixtral / Qwen / DeepSeek models behind an
OpenAI-compatible REST API at ``https://api.groq.com/openai/v1``. We hit the
``chat/completions`` endpoint with ``response_format=json_object`` so the
planner JSON-parser can rely on a JSON-only completion.

This client implements the same single-method ``ChatClient`` Protocol as
``OllamaQwenClient`` so it slots into:
  - ``plan_goal_with_local_qwen``
  - ``decompose_goal``
  - ``DocsExtractorEnricher``

Config (env):
- ``GROQ_API_KEY``        — required.
- ``GROQ_MODEL``          — defaults to ``llama-3.3-70b-versatile``.
- ``GROQ_BASE_URL``       — defaults to ``https://api.groq.com/openai/v1``.
- ``GROQ_TIMEOUT``        — request timeout in seconds (default 60).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib import error, request

DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


class GroqChatError(RuntimeError):
    """Raised when the Groq chat endpoint cannot return a usable completion."""


@dataclass(frozen=True)
class GroqChatClient:
    """Tiny OpenAI-compatible client for Groq's hosted models."""

    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout_seconds: float = 0.0
    use_json_format: bool = True

    def __post_init__(self) -> None:
        # We can't mutate frozen dataclass fields directly; resolve env-derived
        # defaults via object.__setattr__.
        if not self.api_key:
            object.__setattr__(self, "api_key", os.getenv("GROQ_API_KEY", ""))
        if not self.model:
            object.__setattr__(self, "model", os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL))
        if not self.base_url:
            object.__setattr__(
                self, "base_url", os.getenv("GROQ_BASE_URL", DEFAULT_GROQ_BASE_URL)
            )
        if not self.timeout_seconds:
            object.__setattr__(
                self,
                "timeout_seconds",
                float(os.getenv("GROQ_TIMEOUT", "60")),
            )

    def complete(self, messages: list[dict[str, str]]) -> str:
        if not self.api_key:
            raise GroqChatError(
                "GROQ_API_KEY is not set. Export it or pass api_key= when "
                "constructing GroqChatClient."
            )

        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        if self.use_json_format:
            payload["response_format"] = {"type": "json_object"}

        url = self.base_url.rstrip("/") + "/chat/completions"
        encoded = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=encoded,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                # Cloudflare (which fronts api.groq.com) returns
                # 403 with error code 1010 for any request whose
                # User-Agent matches a blocklisted bot signature.
                # urllib's default UA ``Python-urllib/3.x`` is on
                # that list, so without overriding it every Groq
                # call from this client would 403 in ~200ms before
                # ever reaching the inference path. We declare
                # ourselves as a real client identifier instead;
                # see https://developers.cloudflare.com/support/troubleshooting/cloudflare-errors/troubleshooting-cloudflare-1xxx-errors/#error-1010-access-denied
                "User-Agent": "agent-manager/1.0 (+groq-chat-client)",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            # urllib treats 4xx/5xx as exceptions; surface the response
            # body in the error message so operators can see the actual
            # Groq / Cloudflare rejection (e.g. "model decommissioned",
            # "rate limit exceeded", "messages must contain 'json'")
            # instead of just "HTTP Error 403: Forbidden".
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # noqa: BLE001 - detail is best-effort
                detail = ""
            raise GroqChatError(
                f"transport failure: {exc}"
                + (f" — body: {detail}" if detail else "")
            ) from exc
        except (OSError, TimeoutError, error.URLError) as exc:
            raise GroqChatError(f"transport failure: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise GroqChatError(f"invalid JSON response from Groq: {exc}") from exc

        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices:
            raise GroqChatError(f"Groq returned no choices: {body!r}")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise GroqChatError("Groq returned an empty completion.")
        return content
