"""Firecrawl web_scraping benchmark baseline.

BENCHMARK-ONLY. Per the 16-May-2026 product spec this adapter is
NOT reachable from the customer ``/goal`` execution path. It exists
to give the benchmark runner a known-good web_scraping baseline
against which discovered MCP / A2A / OpenAPI scrapers can be
scored. See ``benchmark/baselines/__init__.py`` for the firewall
rules.

Sprint 3a (S3a-4): fourth commodity-API wrapper.

Why Firecrawl:
    * Free tier of 500 credits/month covers all benchmark runs
      and a healthy amount of dev iteration.
    * Cleanest single-call contract among the scraping providers
      we've shortlisted (one POST, structured markdown out).
    * Markdown output is LLM-friendly out of the box, which is
      the whole point for downstream agent sub-tasks.
    * Already in the curated registry as a discovered candidate.

Wrapper safety model:
    Web-scraping has its own abuse vectors but they're mostly
    target-side (rate-limit a victim, scrape a paywalled site,
    etc.). The wrapper enforces:

    1. Maximum URL length cap (8KB) to refuse pathological inputs.
    2. ``url`` must parse as http(s) — no file://, no javascript:.
    3. Pre-call refusal on empty / non-string URLs.

    Test mode is detected from RFC-2606 reserved domains
    (``example.com``, ``example.org``, ``example.net``,
    ``*.invalid``, ``*.test``, ``*.localhost``) — these are
    safe-by-spec because they're either reserved or non-routable
    on the public internet.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse

FirecrawlTransport = Callable[[str, bytes, dict[str, str], float], dict[str, Any]]


class FirecrawlConfigurationError(RuntimeError):
    """Raised when Firecrawl is invoked without an API key."""


_DEFAULT_API_BASE = "https://api.firecrawl.dev/v1"
_PER_PAGE_USD = 0.002  # Firecrawl's documented per-page cost on the paid tier.
_MAX_URL_LENGTH = 8 * 1024


@dataclass(frozen=True)
class FirecrawlScraper:
    """Firecrawl ``web_scraping`` adapter (POST /v1/scrape)."""

    api_key: str | None = None
    transport: FirecrawlTransport | None = None
    timeout_seconds: float = 60.0  # Firecrawl can take up to a minute for JS-heavy pages.
    api_base_url: str = _DEFAULT_API_BASE
    provider_id: str = "firecrawl"
    capabilities: list[str] = None  # type: ignore[assignment]
    unit_cost_usd: float = _PER_PAGE_USD

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["web_scraping"])
        if self.api_key is None:
            object.__setattr__(self, "api_key", os.getenv("FIRECRAWL_API_KEY"))

    # ---- ProviderAdapter contract -----------------------------------

    async def estimate_cost(self, request: ProviderRequest) -> float:
        _ensure_capability(self.capabilities, request.capability)
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return bool(self.api_key)

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not self.api_key:
            raise FirecrawlConfigurationError(
                "FIRECRAWL_API_KEY is required for provider `firecrawl`."
            )

        url_input = request.inputs.get("url")
        url, refusal = _validate_url(url_input)
        if refusal is not None:
            return refusal

        formats = request.inputs.get("formats")
        if formats is None or not isinstance(formats, list) or not formats:
            formats = ["markdown"]

        body_payload: dict[str, Any] = {
            "url": url,
            "formats": formats,
            # ``onlyMainContent`` strips nav/footer/ads — the LLM-
            # friendly default for downstream summarisation /
            # extraction sub-tasks.
            "onlyMainContent": bool(request.inputs.get("only_main_content", True)),
        }
        if request.inputs.get("include_tags") and isinstance(
            request.inputs["include_tags"], list
        ):
            body_payload["includeTags"] = request.inputs["include_tags"]
        if request.inputs.get("exclude_tags") and isinstance(
            request.inputs["exclude_tags"], list
        ):
            body_payload["excludeTags"] = request.inputs["exclude_tags"]
        if request.inputs.get("wait_for"):
            body_payload["waitFor"] = int(request.inputs["wait_for"])

        body = json.dumps(body_payload).encode("utf-8")
        endpoint = f"{self.api_base_url}/scrape"
        headers = _firecrawl_headers(self.api_key)

        start = time.perf_counter()
        try:
            payload = (self.transport or _default_transport)(
                endpoint, body, headers, self.timeout_seconds
            )
            latency_ms = int((time.perf_counter() - start) * 1000)

            # Firecrawl error envelope: ``success=false`` with
            # ``error`` field. Distinct from a successful empty
            # scrape where ``data`` is present but markdown is "".
            if not payload.get("success", True) or payload.get("error"):
                return ProviderResponse(
                    succeeded=False,
                    output=None,
                    cost_usd=0.0,
                    latency_ms=latency_ms,
                    error=_firecrawl_error_string(payload),
                    raw_response=payload,
                )

            normalized = _normalize_scrape(
                payload, source_url=url, test_mode=_is_test_mode_url(url)
            )
            # Firecrawl charges 1 credit per page on the paid
            # tier; the cost projection above amortises this to
            # the documented dollar-equivalent. On the free tier
            # the operator pays $0; the ledger overstates cost
            # but never understates it (the safer error direction).
            return ProviderResponse(
                succeeded=True,
                output=normalized,
                cost_usd=self.unit_cost_usd,
                latency_ms=latency_ms,
                error=None,
                raw_response=payload,
            )
        except FirecrawlConfigurationError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalise transport failures
            latency_ms = int((time.perf_counter() - start) * 1000)
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=latency_ms,
                error=f"{type(exc).__name__}: {exc}",
                raw_response={},
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _firecrawl_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "PlanMyAgentsBenchmark/0.1 firecrawl-adapter",
    }


def _default_transport(
    url: str, body: bytes, headers: dict[str, str], timeout_seconds: float
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            payload_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001 - error.read can fail
            body_text = ""
        try:
            return json.loads(body_text) if body_text else {"success": False, "error": str(exc)}
        except json.JSONDecodeError:
            return {"success": False, "error": str(exc), "raw": body_text}
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("Firecrawl response was not a JSON object")
    return payload


def _normalize_scrape(
    payload: dict[str, Any], *, source_url: str, test_mode: bool
) -> dict[str, Any]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    markdown = str(data.get("markdown") or "")
    html = str(data.get("html") or "")
    title = str(metadata.get("title") or "")
    description = str(metadata.get("description") or "")
    final_url = str(metadata.get("sourceURL") or source_url)
    links = data.get("links") if isinstance(data.get("links"), list) else []

    # Extract a small sample of the markdown for previews — the
    # workflow stitcher uses this for human-readable surfaces, the
    # full content lives in raw_response for downstream LLM tasks.
    preview = markdown[:500] + ("…" if len(markdown) > 500 else "")

    return {
        "url": final_url,
        "title": title,
        "description": description,
        "markdown": markdown,
        "markdown_preview": preview,
        "char_count": len(markdown),
        "html_present": bool(html),
        "link_count": len(links),
        "status": "scraped",
        "test_mode": test_mode,
    }


def _firecrawl_error_string(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("description")
        if message:
            return f"firecrawl_error: {message}"
    if isinstance(error, str):
        return f"firecrawl_error: {error}"
    return "firecrawl_error: scrape_failed"


def _validate_url(value: Any) -> tuple[str, ProviderResponse | None]:
    if not isinstance(value, str) or not value.strip():
        return "", _refusal(
            error="url_required: provide a non-empty `url` string"
        )
    url = value.strip()
    if len(url) > _MAX_URL_LENGTH:
        return "", _refusal(
            error=f"url_too_long: max {_MAX_URL_LENGTH} chars, got {len(url)}"
        )
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "", _refusal(
            error=f"url_scheme_unsupported: only http/https are accepted, got `{parsed.scheme}`"
        )
    if not parsed.netloc:
        return "", _refusal(error="url_missing_host: cannot parse hostname from URL")
    return url, None


# Re-exported under its historical name so external callers (tests,
# notebooks) that imported ``_is_test_mode_url`` from this module
# keep working. New code should import ``is_reserved_test_url`` from
# ``planmyagents_api.test_domains`` directly — it lives outside the
# benchmark.baselines firewall, so the mock + routing layer can use
# it without crossing the firewall.
from planmyagents_api.test_domains import (  # noqa: E402
    is_reserved_test_url as _is_test_mode_url,
)


def _refusal(*, error: str) -> ProviderResponse:
    return ProviderResponse(
        succeeded=False,
        output=None,
        cost_usd=0.0,
        latency_ms=0,
        error=error,
        raw_response={},
    )


def _ensure_capability(capabilities: list[str], requested: str) -> None:
    if requested not in capabilities:
        raise ValueError(
            f"provider `firecrawl` does not support capability: {requested}"
        )
