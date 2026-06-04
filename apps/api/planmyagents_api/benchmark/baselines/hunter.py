"""Hunter.io benchmark baseline.

BENCHMARK-ONLY. Per the 16-May-2026 product spec this adapter is
NOT reachable from the customer ``/goal`` execution path. It exists
to give the benchmark runner a known-good email_verification
baseline against which discovered MCP / A2A / OpenAPI providers
can be scored. See ``benchmark/baselines/__init__.py`` for the
firewall rules.

The adapter is intentionally thin:
- It supports email verification first, because that is the cleanest initial
  benchmark capability.
- It uses only the Python standard library so the prototype remains easy to run.
- Tests inject a fake transport, so no network/API key is needed for CI.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse

Transport = Callable[[str, float], dict[str, Any]]


class HunterConfigurationError(RuntimeError):
    """Raised when Hunter is used without an API key."""


@dataclass(frozen=True)
class HunterEmailVerifier:
    """Hunter.io email-verification provider adapter."""

    api_key: str | None = None
    transport: Transport | None = None
    timeout_seconds: float = 10.0
    provider_id: str = "hunter"
    capabilities: list[str] = None  # type: ignore[assignment]
    unit_cost_usd: float = 0.005

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["email_verification"])
        if self.api_key is None:
            object.__setattr__(self, "api_key", os.getenv("HUNTER_API_KEY"))

    async def estimate_cost(self, request: ProviderRequest) -> float:
        _ensure_capability(self.capabilities, request.capability)
        return self.unit_cost_usd

    async def health_check(self) -> bool:
        return bool(self.api_key)

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not self.api_key:
            raise HunterConfigurationError("HUNTER_API_KEY is required for provider `hunter`")

        email = str(request.inputs.get("email", "")).strip()
        start = time.perf_counter()
        url = _verification_url(email=email, api_key=self.api_key)

        try:
            payload = (self.transport or _default_transport)(url, self.timeout_seconds)
            latency_ms = int((time.perf_counter() - start) * 1000)
            normalized = _normalize_hunter_response(email, payload)
            return ProviderResponse(
                succeeded=True,
                output=normalized,
                cost_usd=self.unit_cost_usd,
                latency_ms=latency_ms,
                raw_response=payload,
            )
        except Exception as exc:  # noqa: BLE001 - normalize provider failures for benchmark runs.
            latency_ms = int((time.perf_counter() - start) * 1000)
            return ProviderResponse(
                succeeded=False,
                output=None,
                cost_usd=0.0,
                latency_ms=latency_ms,
                error=str(exc),
                raw_response={},
            )


def _verification_url(*, email: str, api_key: str) -> str:
    params = urllib.parse.urlencode({"email": email, "api_key": api_key})
    return f"https://api.hunter.io/v2/email-verifier?{params}"


def _default_transport(url: str, timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "PlanMyAgentsBenchmark/0.1"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Hunter response was not a JSON object")
    return payload


def _normalize_hunter_response(email: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"Hunter response missing `data`: {payload}")

    result = str(data.get("result", "unknown")).lower()
    score = int(data.get("score") or 0)

    return {
        "email": str(data.get("email") or email).lower(),
        "result": result,
        "score": score,
        "status": "ok",
        "checks": {
            "regexp": bool(data.get("regexp")),
            "gibberish": bool(data.get("gibberish")),
            "disposable": bool(data.get("disposable")),
            "webmail": bool(data.get("webmail")),
            "mx_records": bool(data.get("mx_records")),
            "smtp_server": bool(data.get("smtp_server")),
            "smtp_check": bool(data.get("smtp_check")),
            "accept_all": bool(data.get("accept_all")),
        },
    }


def _ensure_capability(capabilities: list[str], requested: str) -> None:
    if requested not in capabilities:
        raise ValueError(f"provider does not support capability: {requested}")
