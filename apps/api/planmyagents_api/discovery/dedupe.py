"""Deduplication helpers for discovered candidates."""

from __future__ import annotations

from urllib.parse import urlparse

from planmyagents_api.discovery.constants import normalize_will_fail_reasons
from planmyagents_api.discovery.models import (
    CandidateDocs,
    CandidateObservation,
    CandidateTool,
    DiscoveryCandidate,
    UsageExample,
)


def dedupe_key(candidate: DiscoveryCandidate) -> str:
    """Return a stable dedupe key for a candidate."""

    if candidate.provider_type in {"mcp_server", "a2a_agent", "ai_agent"} and candidate.id:
        return f"{candidate.provider_type}:{_normalize(candidate.id)}"

    domain = _domain(candidate.vendor_url)
    if domain:
        return f"domain:{domain}"
    if candidate.vendor:
        return f"vendor:{_normalize(candidate.vendor)}"
    return f"id:{candidate.id}"


def merge_candidates(
    existing: DiscoveryCandidate, incoming: DiscoveryCandidate
) -> DiscoveryCandidate:
    """Merge metadata for duplicate candidates, preserving non-routable safety."""

    capabilities_by_id = {capability.id: capability for capability in existing.capabilities}
    for capability in incoming.capabilities:
        current = capabilities_by_id.get(capability.id)
        if current is None or capability.confidence > current.confidence:
            capabilities_by_id[capability.id] = capability

    required_env_vars = sorted(set(existing.required_env_vars) | set(incoming.required_env_vars))
    compatible_provider_ids = sorted(
        set(existing.compatible_provider_ids) | set(incoming.compatible_provider_ids)
    )
    promoted = _promoted_candidate(existing=existing, incoming=incoming)
    route_status = "ready_for_promotion" if promoted else "will_fail"
    docs = _merge_docs(existing.docs, incoming.docs)
    tools = _merge_tools(existing.tools, incoming.tools)
    skills = _merge_tools(existing.skills, incoming.skills)
    openapi_url = existing.openapi_url or incoming.openapi_url
    observations = _merge_observations(existing.observations, incoming.observations)
    return DiscoveryCandidate(
        id=existing.id,
        display_name=existing.display_name or incoming.display_name,
        vendor=existing.vendor or incoming.vendor,
        vendor_url=existing.vendor_url or incoming.vendor_url,
        provider_type=existing.provider_type,
        capabilities=sorted(capabilities_by_id.values(), key=lambda item: item.id),
        required_env_vars=required_env_vars,
        compatible_provider_ids=compatible_provider_ids,
        verification_status=_best_verification_status(
            existing.verification_status, incoming.verification_status
        ),
        evidence_url=existing.evidence_url or incoming.evidence_url,
        lifecycle_status=promoted.lifecycle_status if promoted else existing.lifecycle_status,
        route_status=route_status,
        will_fail=route_status != "ready_for_promotion",
        will_fail_reasons=normalize_will_fail_reasons(
            provider_type=existing.provider_type,
            required_env_vars=required_env_vars,
            reasons=sorted(set(existing.will_fail_reasons) | set(incoming.will_fail_reasons)),
        ),
        adapter_module=promoted.adapter_module if promoted else existing.adapter_module,
        benchmark_status=promoted.benchmark_status if promoted else existing.benchmark_status,
        source=existing.source,
        first_seen_at=min(existing.first_seen_at, incoming.first_seen_at),
        last_seen_at=max(existing.last_seen_at, incoming.last_seen_at),
        requested_capabilities=sorted(
            set(existing.requested_capabilities) | set(incoming.requested_capabilities)
        ),
        goal_hash=existing.goal_hash or incoming.goal_hash,
        docs=docs,
        tools=tools,
        skills=skills,
        openapi_url=openapi_url,
        observations=observations,
    )


def _domain(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _normalize(value: str) -> str:
    return value.strip().lower().replace(" ", "-")


def _best_verification_status(existing: str, incoming: str) -> str:
    """Pick the higher-trust verification status when merging duplicates.

    The full ladder, highest trust first:

    * ``capability_verified``     — we ran the candidate's tools/list
                                    or executed a benchmark and got a
                                    successful response.
    * ``registered_in_directory`` — the candidate appears in a vendor-
                                    curated registry (Smithery, MCP
                                    Marketplace, official MCP
                                    registry). Submission gates have
                                    vetted that the provider exists.
    * ``known_provider``          — known provider identity or docs
                                    signal; the capability is inferred
                                    from the description.
    * ``community_listed``        — listed on a community network with
                                    at least one corroborating
                                    artifact link (Moltbook etc.).
    * ``unverified_example``      — historic alias for "unverified
                                    but came in via a static example
                                    fixture", kept for backwards-
                                    compat with old store rows.
    * ``unverified``              — anonymous discovery, no signal.

    Pre-Fix-3 the map only knew the ``capability_verified`` /
    ``known_provider`` / ``unverified`` tiers. ``registered_in_directory``
    and ``community_listed`` (added by the Smithery / MCP Marketplace /
    Moltbook scouts) were missing, so the dedupe step silently
    demoted them to priority 0 and let stale ``unverified`` rows win
    on collision. That is the root cause of the gift-goal regression
    where Smithery-backed candidates kept showing up as ``unverified``
    even after the source was upgraded to emit
    ``registered_in_directory``.
    """

    priority = {
        "capability_verified": 5,
        "registered_in_directory": 4,
        "known_provider": 3,
        "community_listed": 2,
        "unverified_example": 1,
        "unverified": 0,
    }
    return existing if priority.get(existing, 0) >= priority.get(incoming, 0) else incoming


def _merge_observations(
    existing: list[CandidateObservation], incoming: list[CandidateObservation]
) -> list[CandidateObservation]:
    """Union observations across two duplicates of the same provider.

    We dedupe inside the union by ``(source_id, evidence_url)`` so re-running
    the same source against the same URL doesn't bloat the list. The newest
    observation wins on collision so ``observed_at`` stays fresh.
    """

    by_key: dict[tuple[str, str], CandidateObservation] = {}
    for observation in list(existing) + list(incoming):
        key = (observation.source_id, observation.evidence_url)
        current = by_key.get(key)
        if current is None or observation.observed_at >= current.observed_at:
            by_key[key] = observation
    return sorted(
        by_key.values(),
        key=lambda obs: (obs.source_id, obs.observed_at, obs.evidence_url),
    )


def _merge_docs(existing: CandidateDocs, incoming: CandidateDocs) -> CandidateDocs:
    """Merge two doc bundles, preferring populated fields."""

    examples_by_title: dict[tuple[str, str], UsageExample] = {}
    for example in list(existing.usage_examples) + list(incoming.usage_examples):
        key = (example.title, example.language)
        if key not in examples_by_title:
            examples_by_title[key] = example
    return CandidateDocs(
        setup_url=existing.setup_url or incoming.setup_url,
        auth_method=existing.auth_method or incoming.auth_method,
        auth_scopes=sorted(set(existing.auth_scopes) | set(incoming.auth_scopes)),
        install_steps=existing.install_steps or incoming.install_steps,
        usage_examples=list(examples_by_title.values()),
        pricing_url=existing.pricing_url or incoming.pricing_url,
        status_page_url=existing.status_page_url or incoming.status_page_url,
        rate_limit_requests_per_minute=max(
            existing.rate_limit_requests_per_minute,
            incoming.rate_limit_requests_per_minute,
        ),
        rate_limit_monthly_quota=max(
            existing.rate_limit_monthly_quota, incoming.rate_limit_monthly_quota
        ),
    )


def _merge_tools(
    existing: list[CandidateTool], incoming: list[CandidateTool]
) -> list[CandidateTool]:
    """Union two tool lists by tool name; prefer the entry with a description."""

    by_name: dict[str, CandidateTool] = {tool.name: tool for tool in existing}
    for tool in incoming:
        current = by_name.get(tool.name)
        if current is None:
            by_name[tool.name] = tool
            continue
        by_name[tool.name] = CandidateTool(
            name=tool.name,
            description=current.description or tool.description,
            input_schema=current.input_schema or tool.input_schema,
            examples=sorted(set(current.examples) | set(tool.examples)),
        )
    return sorted(by_name.values(), key=lambda item: item.name)


def _promoted_candidate(
    *, existing: DiscoveryCandidate, incoming: DiscoveryCandidate
) -> DiscoveryCandidate | None:
    """Preserve explicit promotion state while keeping raw discovery non-routable."""

    for candidate in (existing, incoming):
        if (
            candidate.route_status == "ready_for_promotion"
            and candidate.lifecycle_status in {"configured", "promoted"}
            and candidate.benchmark_status == "passed"
            and candidate.adapter_module
            and not candidate.will_fail
        ):
            return candidate
    return None
