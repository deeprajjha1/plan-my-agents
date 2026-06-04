"""Resend email_send benchmark baseline.

BENCHMARK-ONLY. Per the 16-May-2026 product spec this adapter is
NOT reachable from the customer ``/goal`` execution path. It exists
to give the benchmark runner a known-good email_send baseline
against which discovered MCP / A2A / OpenAPI email providers can
be scored. See ``benchmark/baselines/__init__.py`` for the firewall
rules.

Sprint 3a (S3a-4): third commodity-API wrapper.

Why Resend:
    * Open signup globally (no India invite gate).
    * Free tier of 3000 emails/month covers all benchmark runs.
    * Documented synthetic test addresses
      (``delivered@resend.dev``, ``bounced@resend.dev``,
      ``complained@resend.dev``) produce deterministic
      delivery/bounce/complaint webhook events without sending
      any real email — the safest possible test surface.
    * REST API is one POST per email; no SMTP, no SDK required.
      Stays stdlib-only.

Wrapper safety model:
    Email is the easiest channel to abuse, so the wrapper has
    two safety layers:

    1. The default ``from`` address is ``onboarding@resend.dev``
       (Resend's owned shared sender). Using this guarantees no
       email ever lands in someone's inbox unless they explicitly
       opted into a Resend test scenario.
    2. The wrapper REFUSES any from-address whose domain isn't
       in ``resend.dev`` unless ``RESEND_ALLOW_REAL_DOMAINS=true``
       is set. This is belt-and-braces against accidentally
       sending production-shaped traffic from a benchmark or
       a misconfigured plan. Set the opt-in env var only when
       you've verified your sender domain and deliberately want
       to send real email.

The wrapper models the *queue* step (POST /emails returns an
``id`` immediately). Actual delivery / bounce / complaint events
are delivered via webhook async, out of scope for the v1 wrapper.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from planmyagents_api.benchmark.models import ProviderRequest, ProviderResponse

ResendTransport = Callable[[str, bytes, dict[str, str], float], dict[str, Any]]


class ResendConfigurationError(RuntimeError):
    """Raised when Resend is invoked without an API key, or when
    a real-domain sender is used without explicit opt-in."""


_DEFAULT_API_BASE = "https://api.resend.com"
_TEST_SAFE_FROM = "onboarding@resend.dev"
_TEST_SAFE_FROM_DOMAIN = "resend.dev"
_REAL_DOMAINS_OPT_IN_ENV = "RESEND_ALLOW_REAL_DOMAINS"
# Per-email cost at Resend's paid tier ($20/month for 50k = $0.0004
# per email). Free tier gets the same cost reported as 0 since the
# operator pays nothing — but we report the paid-tier amount in
# the cost cap projections so cap math behaves the same regardless
# of which tier the operator is on. (Underestimating cost would
# break the cap; overestimating just makes the cap conservative.)
_RESEND_PER_EMAIL_USD = 0.0004


@dataclass(frozen=True)
class ResendEmailSender:
    """Resend ``email_send`` adapter (POST /emails)."""

    api_key: str | None = None
    transport: ResendTransport | None = None
    timeout_seconds: float = 15.0
    api_base_url: str = _DEFAULT_API_BASE
    provider_id: str = "resend-emails"
    capabilities: list[str] = None  # type: ignore[assignment]
    unit_cost_usd: float = _RESEND_PER_EMAIL_USD
    default_from: str = _TEST_SAFE_FROM

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", ["email_send"])
        if self.api_key is None:
            object.__setattr__(self, "api_key", os.getenv("RESEND_API_KEY"))

    # ---- ProviderAdapter contract -----------------------------------

    async def estimate_cost(self, request: ProviderRequest) -> float:
        _ensure_capability(self.capabilities, request.capability)
        # One email per recipient.
        recipients = _normalise_recipients(request.inputs.get("to"))
        return round(self.unit_cost_usd * max(len(recipients), 1), 6)

    async def health_check(self) -> bool:
        return bool(self.api_key)

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        _ensure_capability(self.capabilities, request.capability)
        if not self.api_key:
            raise ResendConfigurationError(
                "RESEND_API_KEY is required for provider `resend-emails`."
            )

        recipients = _normalise_recipients(request.inputs.get("to"))
        if not recipients:
            return _refusal(error="recipient_required: provide at least one `to` address")

        subject = str(request.inputs.get("subject") or "").strip()
        if not subject:
            return _refusal(error="subject_required: provide a non-empty `subject`")

        html = request.inputs.get("html")
        text = request.inputs.get("text")
        if not (html or text):
            return _refusal(
                error="body_required: provide either `html` or `text` (or both)",
            )

        from_address = str(request.inputs.get("from") or self.default_from).strip()
        if not _real_domains_allowed(from_address):
            return _refusal(
                error=(
                    f"real_domain_refused: from-address `{from_address}` is not on "
                    f"resend.dev; set {_REAL_DOMAINS_OPT_IN_ENV}=true to allow real "
                    "sender domains. Defaulting from to `onboarding@resend.dev` is "
                    "safer for benchmarks and dev workflows."
                ),
            )

        body_payload: dict[str, Any] = {
            "from": from_address,
            "to": recipients,
            "subject": subject,
        }
        if html:
            body_payload["html"] = str(html)
        if text:
            body_payload["text"] = str(text)
        if request.inputs.get("reply_to"):
            body_payload["reply_to"] = request.inputs["reply_to"]
        if request.inputs.get("cc"):
            body_payload["cc"] = _normalise_recipients(request.inputs["cc"])
        if request.inputs.get("bcc"):
            body_payload["bcc"] = _normalise_recipients(request.inputs["bcc"])
        if request.inputs.get("tags") and isinstance(request.inputs["tags"], list):
            body_payload["tags"] = request.inputs["tags"]

        body = json.dumps(body_payload).encode("utf-8")
        url = f"{self.api_base_url}/emails"
        headers = _resend_headers(
            self.api_key, idempotency_key=request.idempotency_key
        )

        start = time.perf_counter()
        try:
            payload = (self.transport or _default_transport)(
                url, body, headers, self.timeout_seconds
            )
            latency_ms = int((time.perf_counter() - start) * 1000)

            # Resend errors carry a ``message`` and ``name`` field
            # (and optionally a ``statusCode``). They do NOT carry
            # an ``id``. Same wrapper-success vs business-success
            # split as Stripe / Razorpay.
            if "id" not in payload and ("message" in payload or "error" in payload):
                return ProviderResponse(
                    succeeded=False,
                    output=None,
                    cost_usd=0.0,
                    latency_ms=latency_ms,
                    error=_resend_error_string(payload),
                    raw_response=payload,
                )

            normalized = _normalize_email_response(
                payload,
                from_address=from_address,
                recipients=recipients,
                subject=subject,
                test_mode=_is_test_send(from_address, recipients),
            )
            # Resend doesn't tell us the per-email cost on the
            # response — the paid tier amortises across the monthly
            # quota. We charge one email per recipient at the
            # paid-tier rate so the cost ledger reflects reality
            # under load (operator pays nothing on the free tier;
            # ledger overstates cost by the free quota until they
            # exhaust it, which is the safer error direction).
            cost = round(self.unit_cost_usd * len(recipients), 6)
            return ProviderResponse(
                succeeded=True,
                output=normalized,
                cost_usd=cost,
                latency_ms=latency_ms,
                error=None,
                raw_response=payload,
            )
        except ResendConfigurationError:
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


def _resend_headers(api_key: str, *, idempotency_key: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "PlanMyAgentsBenchmark/0.1 resend-adapter",
    }
    if idempotency_key:
        # Resend's documented Idempotency-Key header: identical
        # requests within 24 hours return the same email id.
        # Critical for retry-safe automation.
        headers["Idempotency-Key"] = idempotency_key
    return headers


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
            return json.loads(body_text) if body_text else {"message": str(exc)}
        except json.JSONDecodeError:
            return {"message": str(exc), "raw": body_text}
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("Resend response was not a JSON object")
    return payload


def _normalize_email_response(
    payload: dict[str, Any],
    *,
    from_address: str,
    recipients: list[str],
    subject: str,
    test_mode: bool,
) -> dict[str, Any]:
    return {
        "email_id": str(payload.get("id") or ""),
        "from": from_address,
        "to": recipients,
        "subject": subject,
        # Synthesise a wrapper-level status: any 200 response with
        # an id means Resend accepted the email for delivery. The
        # actual delivery / bounce / complaint state arrives later
        # via webhook (out of scope for the v1 wrapper).
        "status": "queued",
        "test_mode": test_mode,
    }


def _resend_error_string(payload: dict[str, Any]) -> str:
    name = payload.get("name") or payload.get("error") or ""
    message = payload.get("message") or ""
    if name and message:
        return f"resend_error[{name}]: {message}"
    if message:
        return f"resend_error: {message}"
    return "resend_error: send_failed"


def _normalise_recipients(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    return []


def _is_test_send(from_address: str, recipients: list[str]) -> bool:
    """Detect whether the call is fully sandboxed.

    A send is fully sandboxed when the from-domain is resend.dev
    AND every recipient also ends in @resend.dev (which guarantees
    Resend treats them as synthetic test addresses, never delivers
    real email). One real recipient with a resend.dev sender is
    *not* sandboxed because the email would be sent.
    """

    if not from_address.endswith(f"@{_TEST_SAFE_FROM_DOMAIN}"):
        return False
    return all(r.endswith(f"@{_TEST_SAFE_FROM_DOMAIN}") for r in recipients)


def _real_domains_allowed(from_address: str) -> bool:
    if from_address.endswith(f"@{_TEST_SAFE_FROM_DOMAIN}"):
        return True
    return os.getenv(_REAL_DOMAINS_OPT_IN_ENV, "").lower() in {"1", "true", "yes"}


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
            f"provider `resend-emails` does not support capability: {requested}"
        )
