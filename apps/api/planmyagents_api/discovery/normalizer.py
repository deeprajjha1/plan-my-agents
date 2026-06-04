"""Normalize raw discovered records into stable candidates."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from planmyagents_api.discovery.constants import VALID_PROVIDER_TYPES, normalize_will_fail_reasons
from planmyagents_api.discovery.models import (
    CandidateCapability,
    CandidateDocs,
    CandidateObservation,
    CandidateTool,
    DiscoveryCandidate,
    UsageExample,
)


class CandidateNormalizationError(ValueError):
    """Raised when a raw discovery record cannot become a candidate."""


def normalize_verification_status(raw: Any) -> str:
    """Return the canonical trust-tier enum for candidate evidence.

    ``provider_verified`` was the old name for provider identity/docs evidence.
    It implied PlanMyAgents had verified the provider, so new writes use
    ``known_provider`` instead. Keep this reader-side alias until all external
    manifests and old local stores have been migrated.
    """

    status = str(raw or "unverified").strip() or "unverified"
    if status == "provider_verified":
        return "known_provider"
    return status


def normalize_candidate(
    raw: dict[str, Any],
    *,
    source: str,
    requested_capabilities: list[str] | None = None,
    goal_hash: str = "",
) -> DiscoveryCandidate:
    """Normalize one raw candidate dictionary."""

    candidate_id = _slug(str(raw.get("id") or raw.get("display_name") or raw.get("vendor") or ""))
    display_name = str(raw.get("display_name") or raw.get("vendor") or candidate_id).strip()
    vendor = str(raw.get("vendor") or display_name).strip()
    vendor_url = str(raw.get("vendor_url") or "").strip()
    provider_type = str(raw.get("provider_type") or "api_provider").strip()
    verification_status = normalize_verification_status(raw.get("verification_status"))
    evidence_url = str(raw.get("evidence_url") or raw.get("agent_card_url") or "").strip()

    if not candidate_id:
        raise CandidateNormalizationError("candidate is missing an id/display_name/vendor")
    if provider_type not in VALID_PROVIDER_TYPES:
        raise CandidateNormalizationError(f"unsupported provider_type: {provider_type}")

    capabilities = [
        item for item in (_normalize_capability(item) for item in raw.get("capabilities", [])) if item
    ]
    if not capabilities:
        raise CandidateNormalizationError(f"candidate has no capabilities: {candidate_id}")

    required_env_vars = _string_list(raw.get("required_env_vars", []))
    compatible_provider_ids = _string_list(
        raw.get("compatible_provider_ids")
        or raw.get("compatibleProviderIds")
        or raw.get("compatible_booking_providers")
        or raw.get("compatibleBookingProviders")
        or []
    )
    docs = _normalize_docs(raw.get("docs"))
    tools = _normalize_tools(raw.get("tools"))
    skills = _normalize_tools(raw.get("skills"))
    openapi_url = str(raw.get("openapi_url") or raw.get("openapi_spec_url") or "").strip()
    today = date.today().isoformat()
    observations = _normalize_observations(raw.get("observations"))
    if not observations:
        observations = [
            CandidateObservation(
                source_id=source,
                observed_at=today,
                evidence_url=evidence_url,
            )
        ]

    metadata = _normalize_metadata(raw.get("metadata"))
    for key in ("description", "summary", "snippet", "tagline"):
        value = str(raw.get(key) or "").strip()
        if value and key not in metadata:
            metadata[key] = value

    return DiscoveryCandidate(
        id=candidate_id,
        display_name=display_name,
        vendor=vendor,
        vendor_url=vendor_url,
        provider_type=provider_type,
        capabilities=capabilities,
        required_env_vars=required_env_vars,
        compatible_provider_ids=compatible_provider_ids,
        verification_status=verification_status,
        evidence_url=evidence_url,
        will_fail_reasons=normalize_will_fail_reasons(
            provider_type=provider_type,
            required_env_vars=required_env_vars,
            reasons=[str(item) for item in raw.get("will_fail_reasons", [])],
        ),
        source=source,
        requested_capabilities=sorted(set(requested_capabilities or [])),
        goal_hash=goal_hash,
        docs=docs,
        tools=tools,
        skills=skills,
        openapi_url=openapi_url,
        observations=observations,
        metadata=metadata,
    )


def candidate_from_registry(agent: dict[str, Any]) -> DiscoveryCandidate:
    """Convert an existing `discovered_agents` registry entry into a candidate."""

    discovery = agent.get("discovery", {}) if isinstance(agent.get("discovery"), dict) else {}
    candidate = normalize_candidate(
        agent,
        source=str(discovery.get("source") or "registry"),
        requested_capabilities=[str(item) for item in discovery.get("requested_capabilities", [])],
        goal_hash=str(discovery.get("goal_hash") or ""),
    )
    return DiscoveryCandidate(
        **{
            **candidate.__dict__,
            "lifecycle_status": str(agent.get("lifecycle_status", "discovered")),
            "route_status": str(agent.get("route_status", "will_fail")),
            "will_fail": bool(agent.get("will_fail", True)),
            "compatible_provider_ids": _string_list(agent.get("compatible_provider_ids", [])),
            "verification_status": normalize_verification_status(
                agent.get("verification_status", candidate.verification_status)
            ),
            "evidence_url": str(agent.get("evidence_url", candidate.evidence_url)),
            "will_fail_reasons": normalize_will_fail_reasons(
                provider_type=candidate.provider_type,
                required_env_vars=candidate.required_env_vars,
                reasons=[
                    str(item)
                    for item in agent.get("will_fail_reasons", candidate.will_fail_reasons)
                ],
            ),
            "adapter_module": str(agent.get("adapter_module", "")),
            "benchmark_status": str(agent.get("benchmark_status", "not_started")),
            "first_seen_at": str(discovery.get("first_seen_at") or candidate.first_seen_at),
            "last_seen_at": str(discovery.get("last_seen_at") or candidate.last_seen_at),
            "docs": _normalize_docs(agent.get("docs"))
            if agent.get("docs")
            else candidate.docs,
            "tools": _normalize_tools(agent.get("tools")) or candidate.tools,
            "skills": _normalize_tools(agent.get("skills")) or candidate.skills,
            "openapi_url": str(agent.get("openapi_url") or candidate.openapi_url or ""),
            "observations": _normalize_observations(agent.get("observations"))
            or candidate.observations,
            "metadata": _normalize_metadata(agent.get("metadata")) or candidate.metadata,
        }
    )


def _normalize_observations(raw: Any) -> list[CandidateObservation]:
    if not isinstance(raw, list):
        return []
    out: list[CandidateObservation] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or item.get("source") or "").strip()
        if not source_id:
            continue
        evidence_url = str(item.get("evidence_url") or "").strip()
        key = (source_id, evidence_url)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            CandidateObservation(
                source_id=source_id,
                observed_at=str(item.get("observed_at") or "").strip(),
                evidence_url=evidence_url,
                notes=str(item.get("notes") or "").strip(),
            )
        )
    return out


def _normalize_docs(raw: Any) -> CandidateDocs:
    if not isinstance(raw, dict):
        return CandidateDocs()
    examples_raw = raw.get("usage_examples") or raw.get("examples") or []
    usage_examples: list[UsageExample] = []
    if isinstance(examples_raw, list):
        for example in examples_raw:
            if not isinstance(example, dict):
                continue
            snippet = str(example.get("snippet") or example.get("code") or "").strip()
            if not snippet:
                continue
            usage_examples.append(
                UsageExample(
                    title=str(example.get("title") or "").strip(),
                    language=str(example.get("language") or "text").strip(),
                    snippet=snippet,
                )
            )
    rate_limits = raw.get("rate_limits") if isinstance(raw.get("rate_limits"), dict) else {}
    return CandidateDocs(
        setup_url=str(raw.get("setup_url") or raw.get("docs_url") or "").strip(),
        auth_method=str(raw.get("auth_method") or "").strip(),
        auth_scopes=_string_list(raw.get("auth_scopes")),
        install_steps=[
            str(item).strip()
            for item in (raw.get("install_steps") or [])
            if isinstance(item, str) and str(item).strip()
        ],
        usage_examples=usage_examples,
        pricing_url=str(raw.get("pricing_url") or "").strip(),
        status_page_url=str(raw.get("status_page_url") or "").strip(),
        rate_limit_requests_per_minute=int(
            rate_limits.get("requests_per_minute")
            or rate_limits.get("requests_per_min")
            or 0
        ),
        rate_limit_monthly_quota=int(rate_limits.get("monthly_quota") or 0),
    )


def _normalize_tools(raw: Any) -> list[CandidateTool]:
    if not isinstance(raw, list):
        return []
    tools: list[CandidateTool] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name:
                tools.append(CandidateTool(name=name))
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "").strip()
        if not name:
            continue
        input_schema = item.get("input_schema") or item.get("inputSchema") or {}
        if not isinstance(input_schema, dict):
            input_schema = {}
        examples_raw = item.get("examples") or []
        examples = [
            str(example).strip()
            for example in (examples_raw if isinstance(examples_raw, list) else [])
            if isinstance(example, str) and str(example).strip()
        ]
        tools.append(
            CandidateTool(
                name=name,
                description=str(item.get("description") or "").strip(),
                input_schema=input_schema,
                examples=examples,
            )
        )
    return tools


def _normalize_metadata(raw: Any) -> dict[str, Any]:
    """Validate + sanitise the free-form provenance ``metadata`` bag.

    Sources populate this with whatever upstream signals they have
    (Smithery ``useCount``, MCP Marketplace ``installCommand``,
    Moltbook ``karma``, GitHub ``stargazers_count``, etc.). The bag
    survives serialization through both the JSON discovery store and
    the Postgres ``raw_candidate`` JSONB column, which means values
    must be JSON-safe.

    We don't enforce a schema here — different sources contribute
    different keys and downstream consumers (judge prompt, frontend
    badges, corroboration filters) read the keys they understand.
    What we DO enforce:

    1. ``raw`` must be a dict (anything else returns ``{}``).
    2. Keys are coerced to ``str``.
    3. Values must be JSON-primitive (str/int/float/bool/None) or
       a list/dict of those. Anything else is dropped — no
       silent byte / object / function references.
    """

    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        normalised_key = str(key).strip()
        if not normalised_key:
            continue
        if _is_json_safe(value):
            out[normalised_key] = value
    return out


def _is_json_safe(value: Any) -> bool:
    """Return True iff ``value`` round-trips through JSON cleanly."""

    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and _is_json_safe(v)
            for k, v in value.items()
        )
    return False


def _normalize_capability(raw: Any) -> CandidateCapability | None:
    if isinstance(raw, str):
        capability_id = raw.strip()
        return CandidateCapability(id=capability_id, confidence=0.5) if capability_id else None
    if not isinstance(raw, dict):
        return None
    capability_id = str(raw.get("id") or "").strip()
    if not capability_id:
        return None
    return CandidateCapability(
        id=capability_id,
        confidence=float(raw.get("confidence", 0.5)),
        notes=str(raw.get("notes") or "").strip(),
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _slug(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug
