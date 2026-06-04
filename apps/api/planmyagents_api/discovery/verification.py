"""Conservative verification for discovered candidates.

Verification records evidence quality. It does not make a candidate routable by
itself.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from planmyagents_api.discovery.models import DiscoveryCandidate

FetchText = Callable[[str, float], str]


@dataclass(frozen=True)
class VerificationResult:
    provider_id: str
    status: str
    evidence_url: str
    verified_capabilities: list[str]
    blockers: list[str]
    notes: list[str]

    @property
    def has_provider_evidence(self) -> bool:
        return self.status in {"known_provider", "capability_verified"}

    def to_json(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "status": self.status,
            "evidence_url": self.evidence_url,
            "verified_capabilities": self.verified_capabilities,
            "blockers": self.blockers,
            "notes": self.notes,
        }


def verify_candidate(
    candidate: DiscoveryCandidate,
    *,
    fetch_text: FetchText | None = None,
    timeout_seconds: float = 8.0,
) -> VerificationResult:
    """Verify candidate evidence without trusting it for execution."""

    evidence_url = candidate.evidence_url or candidate.vendor_url
    if not evidence_url:
        return _blocked(candidate, "evidence_url_missing")

    text = (fetch_text or _fetch_text)(evidence_url, timeout_seconds)
    if not text.strip():
        return _blocked(candidate, "evidence_fetch_failed", evidence_url=evidence_url)

    payload = _json_payload(text)
    requested = {capability.id for capability in candidate.capabilities}
    evidence_capabilities = _capabilities_from_evidence(candidate.provider_type, payload, text)
    verified_capabilities = sorted(requested & evidence_capabilities)
    notes = [f"evidence_capabilities:{','.join(sorted(evidence_capabilities))}"]

    if verified_capabilities:
        return VerificationResult(
            provider_id=candidate.id,
            status="capability_verified",
            evidence_url=evidence_url,
            verified_capabilities=verified_capabilities,
            blockers=[],
            notes=notes,
        )
    if _provider_evidence_present(candidate, payload, text):
        return VerificationResult(
            provider_id=candidate.id,
            status="known_provider",
            evidence_url=evidence_url,
            verified_capabilities=[],
            blockers=["capability_evidence_missing"],
            notes=notes,
        )
    return _blocked(candidate, "provider_evidence_missing", evidence_url=evidence_url, notes=notes)


def candidate_with_verification(
    candidate: DiscoveryCandidate, result: VerificationResult
) -> DiscoveryCandidate:
    """Return candidate updated with verification metadata only."""

    return DiscoveryCandidate(
        **{
            **candidate.__dict__,
            "verification_status": result.status,
            "evidence_url": result.evidence_url or candidate.evidence_url,
        }
    )


def _blocked(
    candidate: DiscoveryCandidate,
    blocker: str,
    *,
    evidence_url: str = "",
    notes: list[str] | None = None,
) -> VerificationResult:
    return VerificationResult(
        provider_id=candidate.id,
        status="unverified",
        evidence_url=evidence_url,
        verified_capabilities=[],
        blockers=[blocker],
        notes=notes or [],
    )


def _fetch_text(url: str, timeout_seconds: float) -> str:
    req = request.Request(
        url,
        headers={
            "Accept": "application/json,text/plain,text/html",
            "User-Agent": "PlanMyAgentsVerifier/0.1",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return response.read(200_000).decode("utf-8", errors="ignore")
    except (OSError, TimeoutError, error.URLError):
        return ""


def _json_payload(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _capabilities_from_evidence(provider_type: str, payload: Any, text: str) -> set[str]:
    if provider_type == "a2a_agent":
        return _a2a_capabilities(payload)
    if provider_type == "mcp_server":
        return _mcp_capabilities(payload)
    if provider_type == "api_provider":
        return _openapi_capabilities(payload, text)
    return _text_capabilities(text)


def _a2a_capabilities(payload: Any) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    skills = payload.get("skills") or payload.get("capabilities") or []
    if not isinstance(skills, list):
        return set()
    capabilities = set()
    for skill in skills:
        if isinstance(skill, str):
            capabilities.add(skill)
        elif isinstance(skill, dict):
            value = skill.get("name") or skill.get("id") or skill.get("capability")
            if value:
                capabilities.add(str(value))
    return capabilities


def _mcp_capabilities(payload: Any) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    tools = payload.get("tools") or []
    if not isinstance(tools, list):
        return set()
    capabilities = set()
    for tool in tools:
        if isinstance(tool, str):
            capabilities.add(tool)
        elif isinstance(tool, dict):
            value = tool.get("name") or tool.get("id")
            if value:
                capabilities.add(str(value))
    return capabilities


def _openapi_capabilities(payload: Any, text: str) -> set[str]:
    if isinstance(payload, dict) and ("openapi" in payload or "swagger" in payload):
        return _text_capabilities(json.dumps(payload))
    return _text_capabilities(text)


def _text_capabilities(text: str) -> set[str]:
    lowered = text.lower()
    capabilities = set()
    for capability in (
        "company_data_lookup",
        "contact_enrichment",
        "email_verification",
        "semantic_search",
        "web_scraping",
        "travel_search",
        "booking_execution",
        "payment_authorization",
    ):
        if capability in lowered or capability.replace("_", " ") in lowered:
            capabilities.add(capability)
    return capabilities


def _provider_evidence_present(candidate: DiscoveryCandidate, payload: Any, text: str) -> bool:
    if isinstance(payload, dict):
        if candidate.provider_type == "a2a_agent" and (
            payload.get("skills") or payload.get("capabilities")
        ):
            return True
        if candidate.provider_type == "mcp_server" and payload.get("tools"):
            return True
        if candidate.provider_type == "api_provider" and (
            payload.get("openapi") or payload.get("swagger")
        ):
            return True
    lowered = text.lower()
    # Match against display_name / id / vendor / the first token of the id
    # (slug style like "razorpay-payments" -> "razorpay") so we don't
    # downgrade real first-party homepages to unverified just because the
    # marketing site doesn't repeat the full slug back at us.
    needles = {
        candidate.display_name.lower(),
        candidate.id.lower(),
        (candidate.vendor or "").lower(),
        candidate.id.split("-", 1)[0].lower() if "-" in candidate.id else "",
    }
    needles.discard("")
    return any(needle in lowered for needle in needles)
