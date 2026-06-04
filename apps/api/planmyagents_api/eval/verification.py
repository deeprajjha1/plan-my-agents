"""Static verification (the cheapest eval tier).

Wraps the existing ``discovery.verification.verify_candidate`` (which already
knows how to read MCP ``tools``, A2A ``skills``, and OpenAPI specs from an
evidence URL) and produces a verification-only outcome. This NEVER emits a
scored ranking and NEVER assigns a quality score — it answers only "does this
provider exist and advertise the capability?".

When the protocol execution gate is off (default), no live probe is made;
the verifier still runs against the candidate's evidence URL via the existing
fetch path, which is a read of public metadata, not a generic-protocol
invocation. A truly network-free outcome is produced when no evidence URL is
present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.verification import (
    VerificationResult,
    verify_candidate,
)


@dataclass(frozen=True)
class VerificationOutcome:
    provider_id: str
    verification_status: str
    verified_capabilities: list[str]
    blockers: list[str]
    evidence_url: str
    record: dict[str, Any]

    @property
    def passed(self) -> bool:
        # "passed" == we have at least directory/provider-level existence
        # evidence. capability_verified is the strongest; known_provider and
        # registered_in_directory also clear the existence bar.
        return self.verification_status in {
            "capability_verified",
            "known_provider",
            "registered_in_directory",
        }


@dataclass
class VerificationProber:
    """Performs cheap static verification of a candidate."""

    enable_live_probe: bool = False
    timeout_seconds: float = 8.0

    def probe(self, candidate: DiscoveryCandidate) -> VerificationOutcome:
        evidence_url = candidate.evidence_url or candidate.vendor_url

        # No evidence URL and live probe disabled → trust the candidate's
        # existing catalogue-derived verification status without any network.
        if not evidence_url or not self.enable_live_probe:
            status = candidate.verification_status or "unverified"
            return VerificationOutcome(
                provider_id=candidate.id,
                verification_status=status,
                verified_capabilities=[],
                blockers=[] if status != "unverified" else ["live_probe_disabled"],
                evidence_url=evidence_url,
                record={
                    "provider_id": candidate.id,
                    "status": status,
                    "evidence_url": evidence_url,
                    "verified_capabilities": [],
                    "blockers": [] if status != "unverified" else ["live_probe_disabled"],
                    "notes": ["catalogue_status_no_live_probe"],
                },
            )

        result: VerificationResult = verify_candidate(
            candidate, timeout_seconds=self.timeout_seconds
        )
        return VerificationOutcome(
            provider_id=result.provider_id,
            verification_status=result.status,
            verified_capabilities=list(result.verified_capabilities),
            blockers=list(result.blockers),
            evidence_url=result.evidence_url,
            record=result.to_json(),
        )
