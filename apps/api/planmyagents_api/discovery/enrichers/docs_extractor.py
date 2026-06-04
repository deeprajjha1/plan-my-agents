"""LLM-backed enricher that turns vendor docs pages into structured setup info.

Given a candidate with a populated ``evidence_url`` (vendor docs page) but an
empty ``docs`` block, this enricher fetches the page, strips it down to text,
and asks a chat model to fill ``auth_method`` / ``install_steps`` /
``usage_examples`` in a strict JSON schema. The result is merged into the
candidate's ``docs``.

Design notes:
- The HTTP transport and chat client are pluggable so unit tests can drive
  them deterministically.
- Failures (network, malformed JSON, model unavailable) are swallowed
  per-candidate; the candidate is returned unchanged.
- The default chat client is the escalating client (Qwen primary, Groq
  fallback) — same as the planner and intent mapper. A
  ``GroqChatClient`` lands in a follow-up milestone and slots in here without
  any other change.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from planmyagents_api.discovery.models import (
    CandidateDocs,
    DiscoveryCandidate,
    UsageExample,
)

LOGGER = logging.getLogger(__name__)

DocsTransport = Callable[[str, float], str]


class DocsChatClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str:
        """Return the assistant message content for ``messages``."""


@dataclass(frozen=True)
class DocsExtractorEnricher:
    """Fetch vendor docs pages and ask an LLM to fill the setup metadata."""

    chat_client: DocsChatClient | None = None
    transport: DocsTransport | None = None
    timeout_seconds: float = 12.0
    max_text_chars: int = 16_000
    skip_when_already_populated: bool = True

    def enrich(self, candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
        if not candidates:
            return candidates
        if self.chat_client is not None:
            chat = self.chat_client
        else:
            from planmyagents_api.llm.escalating_client import build_default_escalating_client

            chat = build_default_escalating_client()
        out: list[DiscoveryCandidate] = []
        for candidate in candidates:
            url = candidate.evidence_url or candidate.vendor_url
            if not url or not url.startswith(("http://", "https://")):
                out.append(candidate)
                continue
            if self.skip_when_already_populated and not candidate.docs.is_empty:
                out.append(candidate)
                continue
            try:
                page_text = self._fetch_text(url)
            except DocsExtractorError as exc:
                LOGGER.info("docs fetch failed for %s (%s): %s", candidate.id, url, exc)
                out.append(candidate)
                continue
            if not page_text:
                out.append(candidate)
                continue
            try:
                payload = self._extract(chat, candidate=candidate, page_text=page_text)
            except DocsExtractorError as exc:
                LOGGER.info("docs extraction failed for %s: %s", candidate.id, exc)
                out.append(candidate)
                continue
            if payload is None:
                out.append(candidate)
                continue
            docs = _docs_from_payload(payload, fallback_setup_url=url)
            if docs.is_empty:
                out.append(candidate)
                continue
            out.append(_with_docs(candidate, docs))
        return out

    def _fetch_text(self, url: str) -> str:
        transport = self.transport or _default_transport
        body = transport(url, self.timeout_seconds)
        return _html_to_text(body)[: self.max_text_chars]

    def _extract(
        self,
        chat: DocsChatClient,
        *,
        candidate: DiscoveryCandidate,
        page_text: str,
    ) -> dict[str, Any] | None:
        messages = _build_messages(candidate=candidate, page_text=page_text)
        try:
            raw = chat.complete(messages)
        except Exception as exc:  # noqa: BLE001 - intentionally broad
            raise DocsExtractorError(f"chat client failure: {exc}") from exc
        return _parse_extractor_response(raw)


class DocsExtractorError(RuntimeError):
    """Raised when the docs extractor cannot return a usable payload."""


def _default_transport(url: str, timeout_seconds: float) -> str:
    req = request.Request(url, method="GET", headers={"Accept": "text/html"})
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return response.read().decode("utf-8", errors="replace")
    except (OSError, TimeoutError, error.URLError) as exc:
        raise DocsExtractorError(f"transport failure: {exc}") from exc


_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _html_to_text(body: str) -> str:
    """Return a best-effort plaintext projection of HTML for prompt-stuffing."""

    no_script = re.sub(r"<script[\s\S]*?</script>", " ", body, flags=re.IGNORECASE)
    no_style = re.sub(r"<style[\s\S]*?</style>", " ", no_script, flags=re.IGNORECASE)
    text = _HTML_TAG.sub(" ", no_style)
    return _WHITESPACE.sub(" ", text).strip()


def _build_messages(
    *, candidate: DiscoveryCandidate, page_text: str
) -> list[dict[str, str]]:
    system = """
You extract structured onboarding metadata for a single API/MCP/A2A provider
from one documentation page.

Output JSON with this exact shape:
{
  "auth_method": "api_key|oauth2|bearer_token|mcp_stdio|mcp_http|none|other",
  "auth_scopes": ["..."],
  "install_steps": ["short imperative step", "..."],
  "usage_examples": [
    {"title": "short title", "language": "bash|python|typescript|json|http", "snippet": "..."}
  ],
  "pricing_url": "absolute URL or empty",
  "status_page_url": "absolute URL or empty"
}

Rules:
- Only return JSON. No prose, no markdown.
- If a field is unknown, return its empty value (empty string, empty list).
- install_steps must be ≤6 short imperative sentences.
- usage_examples must include the literal code block from the docs.
- Never invent URLs or scopes that are not on the page.
""".strip()

    user = json.dumps(
        {
            "provider": {
                "id": candidate.id,
                "display_name": candidate.display_name,
                "vendor": candidate.vendor,
                "provider_type": candidate.provider_type,
            },
            "doc_text": page_text,
        }
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_extractor_response(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return data


def _docs_from_payload(payload: dict[str, Any], *, fallback_setup_url: str) -> CandidateDocs:
    install_steps = [
        str(step).strip()
        for step in (payload.get("install_steps") or [])
        if isinstance(step, str) and str(step).strip()
    ]
    examples_raw = payload.get("usage_examples") or []
    examples: list[UsageExample] = []
    if isinstance(examples_raw, list):
        for item in examples_raw:
            if not isinstance(item, dict):
                continue
            snippet = str(item.get("snippet") or "").strip()
            if not snippet:
                continue
            examples.append(
                UsageExample(
                    title=str(item.get("title") or "").strip(),
                    language=str(item.get("language") or "text").strip(),
                    snippet=snippet,
                )
            )
    auth_scopes = [
        str(scope).strip()
        for scope in (payload.get("auth_scopes") or [])
        if isinstance(scope, str) and str(scope).strip()
    ]
    return CandidateDocs(
        setup_url=str(payload.get("setup_url") or fallback_setup_url or "").strip(),
        auth_method=str(payload.get("auth_method") or "").strip(),
        auth_scopes=sorted(set(auth_scopes)),
        install_steps=install_steps,
        usage_examples=examples,
        pricing_url=str(payload.get("pricing_url") or "").strip(),
        status_page_url=str(payload.get("status_page_url") or "").strip(),
    )


def _with_docs(candidate: DiscoveryCandidate, docs: CandidateDocs) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        **{
            **candidate.__dict__,
            "docs": docs,
        }
    )


@dataclass(frozen=True)
class StubDocsTransport:
    """In-memory transport for tests; maps URL → response body string."""

    responses: dict[str, str]

    def __call__(self, url: str, timeout_seconds: float) -> str:
        if url not in self.responses:
            raise DocsExtractorError(f"no stub configured for {url}")
        return self.responses[url]


@dataclass(frozen=True)
class StubChatClient:
    """In-memory chat client for tests; returns a fixed completion."""

    completion: str

    def complete(self, messages: list[dict[str, str]]) -> str:
        return self.completion
