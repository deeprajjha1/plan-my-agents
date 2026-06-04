"""User/vendor-submitted Agent Card ingestion.

Turns a submitted agent-card URL (ANY domain) into a verified, indexed
``DiscoveryCandidate``. This is a *registration/resolution* mechanism, not a
discovery crawler: the submitter supplies the URL, so it generalizes to any
domain without enumeration. Finding unknown agents on unknown domains remains
an unsolved, out-of-scope problem.

Reuse-only by design (see ``.kiro/specs/agent-card-ingestion/``). This module
is a thin orchestrator over existing components — it adds NO new fetcher,
parser, verifier, normalizer, candidate store, or verification store:

* fetch + parse + normalize  → :class:`A2AAgentCardSource`
* claim extraction           → :class:`DescriptorReader`
* existence/claim verify     → :func:`verify_candidate` + :func:`candidate_with_verification`
* persist verification check → :class:`VerificationRecord` + ``verification_store_for_path``
* persist candidate (agentic)→ ``discovery_store_for_path`` → ``RoutingDiscoveryStore.save_merge``
* audit the attempt          → :class:`DiscoveryRunLogger`

Honest trust ceiling: a self-asserted card claim is verified as *made*
(existence/claim), never as *true* (quality). Ingestion never sets a benchmark
status beyond ``not_started`` and never makes a candidate routable. A2A quality
attestation is blocked-on-invocation and routes through the Eval_Framework once
A2A invocation exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib import error, parse, request

from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError
from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource
from planmyagents_api.discovery.sources.json_source import DiscoverySourceLoadError
from planmyagents_api.discovery.store import discovery_store_for_path
from planmyagents_api.discovery.verification import (
    candidate_with_verification,
    verify_candidate,
)
from planmyagents_api.discovery.verification_store import (
    VerificationRecord,
    verification_store_for_path,
)

WELL_KNOWN_PATH = "/.well-known/agent.json"
_SOURCE_ID = "card_ingestion"


@dataclass
class IngestionResult:
    """Transient (non-persisted) summary of one ingestion attempt."""

    provider_id: str
    card_url: str
    resolved: bool
    verification_status: str = "unverified"
    verified_capabilities: list[str] = field(default_factory=list)
    declared_capabilities: list[str] = field(default_factory=list)
    unmapped_skills: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    benchmark_status: str = "not_started"
    routable: bool = False
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "card_url": self.card_url,
            "resolved": self.resolved,
            "verification_status": self.verification_status,
            "verified_capabilities": list(self.verified_capabilities),
            "declared_capabilities": list(self.declared_capabilities),
            "unmapped_skills": list(self.unmapped_skills),
            "blockers": list(self.blockers),
            "benchmark_status": self.benchmark_status,
            "routable": self.routable,
            "error": self.error,
        }


@dataclass
class CardIngestionService:
    """Resolve, verify, and index a submitted Agent Card URL."""

    discovery_store_url: str
    verification_store_url: str
    run_logger: Any | None = None
    timeout_seconds: float = 10.0
    max_card_bytes: int = 1_000_000
    # Production is https-only (R1.3). Tests inject {"https", "http"} to run
    # against a local in-process stub — same pattern as the transport/probe
    # injection seams elsewhere in the codebase.
    allowed_schemes: frozenset[str] = frozenset({"https"})

    # -- public API ---------------------------------------------------------

    def ingest(self, card_url: str) -> IngestionResult:
        """Resolve a submitted card URL into a verified, indexed candidate.

        Always returns an ``IngestionResult`` — never raises on a bad/unreachable
        URL (Property 7). On success the candidate is persisted through the
        existing routing facade and a verification record + run-log event are
        written.
        """

        normalized_url, url_error = self._normalize_url(card_url)
        if url_error:
            return IngestionResult(
                provider_id="",
                card_url=card_url,
                resolved=False,
                error=url_error,
            )

        logger = self._logger()
        try:
            with logger.record(
                source_id=_SOURCE_ID,
                source_type="CardIngestionService",
                query=normalized_url,
                trigger="manual",
            ) as observation:
                result = self._ingest_resolved(normalized_url)
                observation.candidates_returned = 1 if result.resolved else 0
                return result
        except Exception as exc:  # noqa: BLE001 — contract: always return a result
            return IngestionResult(
                provider_id="",
                card_url=normalized_url,
                resolved=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    def attest_quality(self, provider_id: str, capability: str) -> dict[str, Any]:
        """A2A quality attestation is blocked-on-invocation (R7).

        Returns a structured blocked result whose ``source`` is in the
        credibility classifier's non-real deny set, so an ingested agent can
        never appear as quality-scored. When A2A invocation lands, this routes
        through the Eval_Framework rather than a parallel mechanism.
        """

        return {
            "provider_id": provider_id,
            "capability": capability,
            "status": "blocked_on_invocation",
            "source": "refused",
            "reason": (
                "A2A skill invocation is not implemented; quality attestation "
                "is blocked. Existence/claim verification is available via "
                "ingest(). When A2A invocation ships, attestation routes "
                "through the Eval_Framework."
            ),
        }

    # -- internals ----------------------------------------------------------

    def _ingest_resolved(self, card_url: str) -> IngestionResult:
        # 1. Pre-flight bounded fetch so an oversized/unreachable card fails
        #    fast with a structured error BEFORE the source parses it. We hand
        #    nothing from this read to the parser — A2AAgentCardSource does its
        #    own fetch+parse; this is purely a guard (size + reachability).
        guard_error = self._preflight(card_url)
        if guard_error:
            return IngestionResult(
                provider_id="", card_url=card_url, resolved=False, error=guard_error
            )

        # 2. Resolve via the existing A2A card source (reuse — no new parser).
        source = A2AAgentCardSource(
            locations=[card_url],
            source_id=_SOURCE_ID,
            timeout_seconds=self.timeout_seconds,
        )
        try:
            candidates = source.search(capabilities=set(), task_description="")
        except (DiscoverySourceLoadError, CandidateNormalizationError) as exc:
            return IngestionResult(
                provider_id="",
                card_url=card_url,
                resolved=False,
                error=f"resolution_failed: {exc}",
            )
        if not candidates:
            return IngestionResult(
                provider_id="",
                card_url=card_url,
                resolved=False,
                error="resolution_failed: card unreachable, unparseable, or has no capabilities",
            )

        candidate = candidates[0]
        # Ensure the evidence URL is the submitted card URL (R2.5).
        if candidate.evidence_url != card_url:
            candidate = DiscoveryCandidate(
                **{**candidate.__dict__, "evidence_url": card_url}
            )

        # 3. Claim extraction: declared capabilities come from the card. A
        #    skill is "unmapped" when it doesn't correspond to any known
        #    REGISTRY capability (not merely the candidate's self-declared set,
        #    which is derived from the skill name and would trivially match).
        #    Reuses the existing capability index — no new vocabulary logic.
        from planmyagents_api.discovery.capability_index import (
            get_default_capability_index,
        )

        declared = [cap.id for cap in candidate.capabilities]
        registry_caps = set(get_default_capability_index().capability_ids)
        unmapped = [cap_id for cap_id in declared if cap_id not in registry_caps]

        # 4. Existence + claim verification (reuse verify_candidate).
        verification = verify_candidate(candidate, timeout_seconds=self.timeout_seconds)
        verified_candidate = candidate_with_verification(candidate, verification)

        # 5. Honest ceiling: never a quality/benchmark claim, never routable.
        ceiled = DiscoveryCandidate(
            **{
                **verified_candidate.__dict__,
                "benchmark_status": "not_started",
                "route_status": "will_fail",
                "will_fail": True,
            }
        )

        # 6. Persist the verification check (reuse the verification store).
        self._verification_store().append([VerificationRecord.from_result(verification)])

        # 7. Persist the candidate through the routing facade (dedupe on
        #    re-submit via save_merge). Agentic rows land in the agentic store.
        self._discovery_store().save_merge([ceiled])

        return IngestionResult(
            provider_id=ceiled.id,
            card_url=card_url,
            resolved=True,
            verification_status=ceiled.verification_status,
            verified_capabilities=list(verification.verified_capabilities),
            declared_capabilities=declared,
            unmapped_skills=unmapped,
            blockers=list(verification.blockers),
            benchmark_status="not_started",
            routable=False,
        )

    def _normalize_url(self, card_url: str) -> tuple[str, str]:
        """Return (normalized_url, error). Error is '' on success."""

        raw = (card_url or "").strip()
        if not raw:
            return "", "empty_url"
        parsed = parse.urlparse(raw)
        if parsed.scheme not in self.allowed_schemes:
            return "", "non_https_url"
        if not parsed.netloc:
            return "", "invalid_url"
        # Bare domain (no path or only "/") → conventional well-known path.
        if parsed.path in ("", "/"):
            raw = f"{parsed.scheme}://{parsed.netloc}{WELL_KNOWN_PATH}"
        return raw, ""

    def _preflight(self, card_url: str) -> str:
        """Bounded reachability + size guard. Returns '' on success.

        We fetch at most ``max_card_bytes + 1`` bytes and only check the
        response is reachable and not oversized. We do NOT parse here — the
        A2A source owns parsing. This keeps a hostile huge body from being
        loaded by the parser.
        """

        req = request.Request(card_url, headers={"Accept": "application/json"})
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:  # noqa: S310
                body = response.read(self.max_card_bytes + 1)
        except (OSError, TimeoutError, error.URLError) as exc:
            return f"resolution_failed: {exc}"
        if len(body) > self.max_card_bytes:
            return f"card_too_large: exceeds {self.max_card_bytes} bytes"
        # Cheap parse check so we fail with a clear error rather than letting
        # the source silently return [] on invalid JSON.
        try:
            json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            return f"resolution_failed: invalid JSON: {exc}"
        return ""

    def _logger(self):
        if self.run_logger is not None:
            return self.run_logger
        from planmyagents_api.discovery.run_log import DiscoveryRunLogger

        return DiscoveryRunLogger.default()

    def _discovery_store(self):
        return discovery_store_for_path(self.discovery_store_url)

    def _verification_store(self):
        return verification_store_for_path(self.verification_store_url)
