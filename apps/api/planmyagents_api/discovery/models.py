"""Discovery domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from planmyagents_api.discovery.constants import (
    PROVIDER_TYPE_PRIORITY,
    VALID_PROVIDER_TYPES,
    default_will_fail_reasons,
)
from planmyagents_api.discovery.freshness import freshness_status

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class CandidateCapability:
    """A capability hypothesis for a discovered candidate."""

    id: str
    confidence: float
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("candidate capability id is required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("candidate capability confidence must be between 0 and 1")

    def to_json(self) -> JsonDict:
        return {
            "id": self.id,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 4),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class CandidateTool:
    """A concrete callable surface (MCP tool, A2A skill, or API operation)."""

    name: str
    description: str = ""
    input_schema: JsonDict = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)

    def to_json(self) -> JsonDict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "examples": list(self.examples),
        }


@dataclass(frozen=True)
class UsageExample:
    """A copy-pasteable usage example extracted from a provider's docs."""

    title: str
    language: str
    snippet: str

    def to_json(self) -> JsonDict:
        return {
            "title": self.title,
            "language": self.language,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class CandidateObservation:
    """One discovery source observing this provider.

    A single provider can be confirmed by many sources. Instead of dropping
    duplicates and losing source signal, we store one observation per source
    sighting under each provider. Confirmation count is then ``len(observations)``
    and is a credibility signal we can rank on.
    """

    source_id: str
    observed_at: str
    evidence_url: str = ""
    notes: str = ""

    def to_json(self) -> JsonDict:
        return {
            "source_id": self.source_id,
            "observed_at": self.observed_at,
            "evidence_url": self.evidence_url,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class CandidateDocs:
    """Setup + usage metadata that turns a discovery row into something onboardable.

    All fields are optional. Enrichers fill what they can. The UI reads these
    fields directly so a user can see how to authenticate, install, and call a
    provider without leaving the agent detail page.
    """

    setup_url: str = ""
    auth_method: str = ""
    auth_scopes: list[str] = field(default_factory=list)
    install_steps: list[str] = field(default_factory=list)
    usage_examples: list[UsageExample] = field(default_factory=list)
    pricing_url: str = ""
    status_page_url: str = ""
    rate_limit_requests_per_minute: int = 0
    rate_limit_monthly_quota: int = 0

    def to_json(self) -> JsonDict:
        return {
            "setup_url": self.setup_url,
            "auth_method": self.auth_method,
            "auth_scopes": list(self.auth_scopes),
            "install_steps": list(self.install_steps),
            "usage_examples": [example.to_json() for example in self.usage_examples],
            "pricing_url": self.pricing_url,
            "status_page_url": self.status_page_url,
            "rate_limit_requests_per_minute": self.rate_limit_requests_per_minute,
            "rate_limit_monthly_quota": self.rate_limit_monthly_quota,
        }

    @property
    def is_empty(self) -> bool:
        return (
            not self.setup_url
            and not self.auth_method
            and not self.install_steps
            and not self.usage_examples
            and not self.auth_scopes
        )


@dataclass(frozen=True)
class DiscoveryCandidate:
    """A normalized non-routable candidate from one or more discovery sources."""

    id: str
    display_name: str
    vendor: str
    vendor_url: str
    provider_type: str
    capabilities: list[CandidateCapability]
    required_env_vars: list[str] = field(default_factory=list)
    compatible_provider_ids: list[str] = field(default_factory=list)
    verification_status: str = "unverified"
    evidence_url: str = ""
    lifecycle_status: str = "discovered"
    route_status: str = "will_fail"
    will_fail: bool = True
    will_fail_reasons: list[str] = field(default_factory=lambda: default_will_fail_reasons(""))
    adapter_module: str = ""
    benchmark_status: str = "not_started"
    source: str = "unknown"
    first_seen_at: str = field(default_factory=lambda: date.today().isoformat())
    last_seen_at: str = field(default_factory=lambda: date.today().isoformat())
    requested_capabilities: list[str] = field(default_factory=list)
    goal_hash: str = ""
    docs: CandidateDocs = field(default_factory=CandidateDocs)
    tools: list[CandidateTool] = field(default_factory=list)
    skills: list[CandidateTool] = field(default_factory=list)
    openapi_url: str = ""
    observations: list[CandidateObservation] = field(default_factory=list)
    # Free-form provenance signals from upstream sources that don't
    # fit a typed slot but carry credibility weight (Smithery
    # ``useCount``, MCP Marketplace ``installCommand``, Moltbook
    # ``karma``/``is_claimed``, GitHub ``stargazers_count``, etc.).
    # Sources populate it with whatever they have; the judge prompt
    # and the corroboration filters consume it. Keep VALUES JSON-
    # serialisable (strings, numbers, bools, lists, dicts) — the
    # store layer round-trips this through JSONB.
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("discovery candidate id is required")
        if self.provider_type not in VALID_PROVIDER_TYPES:
            raise ValueError(f"unsupported discovery provider_type: {self.provider_type}")
        if not self.capabilities:
            raise ValueError("discovery candidate must include at least one capability")

    @property
    def discovery_priority(self) -> int:
        return PROVIDER_TYPE_PRIORITY.get(self.provider_type, 99)

    @property
    def confirmation_count(self) -> int:
        if self.observations:
            return len({obs.source_id for obs in self.observations})
        return 1 if self.source else 0

    @property
    def source_ids(self) -> list[str]:
        if self.observations:
            return sorted({obs.source_id for obs in self.observations if obs.source_id})
        return [self.source] if self.source else []

    def supports_any(self, capability_ids: set[str]) -> bool:
        return any(capability.id in capability_ids for capability in self.capabilities)

    def to_registry_json(self) -> JsonDict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "vendor": self.vendor,
            "vendor_url": self.vendor_url,
            "provider_type": self.provider_type,
            "capabilities": [capability.to_json() for capability in self.capabilities],
            "required_env_vars": sorted(set(self.required_env_vars)),
            "compatible_provider_ids": sorted(set(self.compatible_provider_ids)),
            "verification_status": self.verification_status,
            "evidence_url": self.evidence_url,
            "lifecycle_status": self.lifecycle_status,
            "route_status": self.route_status,
            "will_fail": self.will_fail,
            "will_fail_reasons": sorted(set(self.will_fail_reasons)),
            "discovery_priority": self.discovery_priority,
            "adapter_module": self.adapter_module,
            "benchmark_status": self.benchmark_status,
            "docs": self.docs.to_json(),
            "tools": [tool.to_json() for tool in self.tools],
            "skills": [skill.to_json() for skill in self.skills],
            "openapi_url": self.openapi_url,
            "observations": [obs.to_json() for obs in self.observations],
            "confirmation_count": self.confirmation_count,
            "source_ids": self.source_ids,
            "discovery": {
                "source": self.source,
                "first_seen_at": self.first_seen_at,
                "last_seen_at": self.last_seen_at,
                "requested_capabilities": sorted(set(self.requested_capabilities)),
                "goal_hash": self.goal_hash,
            },
            # Provenance signals from upstream sources (Smithery
            # useCount, Moltbook karma, GitHub stars, etc.). See the
            # ``metadata`` field docstring on DiscoveryCandidate.
            "metadata": dict(self.metadata),
        }

    def to_public_summary(self, *, stale_after_days: int = 30) -> JsonDict:
        return {
            "provider_id": self.id,
            "display_name": self.display_name,
            "provider_type": self.provider_type,
            "discovery_priority": self.discovery_priority,
            "capabilities": [capability.id for capability in self.capabilities],
            "lifecycle_status": self.lifecycle_status,
            "will_fail": self.will_fail,
            "will_fail_reasons": sorted(set(self.will_fail_reasons)),
            "required_env_vars": sorted(set(self.required_env_vars)),
            "compatible_provider_ids": sorted(set(self.compatible_provider_ids)),
            "verification_status": self.verification_status,
            "evidence_url": self.evidence_url,
            "benchmark_status": self.benchmark_status,
            "docs_available": not self.docs.is_empty,
            "auth_method": self.docs.auth_method,
            "tool_count": len(self.tools),
            "skill_count": len(self.skills),
            "confirmation_count": self.confirmation_count,
            "source_ids": self.source_ids,
            "freshness": freshness_status(
                first_seen_at=self.first_seen_at,
                last_seen_at=self.last_seen_at,
                stale_after_days=stale_after_days,
            ).to_json(),
            # Public-summary metadata exposes the provenance signals
            # to the frontend (badge tooltips, "this agent has 1.2k
            # github stars", etc.) without leaking internal fields.
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DiscoverySearchResult:
    """Ranked candidate returned by the discovery index."""

    candidate: DiscoveryCandidate
    score: float
    reasons: list[str]

    def to_json(self, *, stale_after_days: int = 30) -> JsonDict:
        payload = self.candidate.to_public_summary(stale_after_days=stale_after_days)
        payload["match_score"] = round(self.score, 4)
        payload["match_reasons"] = self.reasons
        return payload
