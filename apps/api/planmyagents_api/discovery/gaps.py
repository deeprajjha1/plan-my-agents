"""Per-capability gap reporting for unsupported plans."""

from __future__ import annotations

from typing import Any

from planmyagents_api.discovery.constants import AGENTIC_PROVIDER_TYPES, PROVIDER_TYPE_PRIORITY
from planmyagents_api.planner.goal import GoalPlan

BLOCKER_TO_PROMOTION_STEP = {
    "API key is required before execution.": "Configure credentials or BYOK setup.",
    "Credentials are required before execution.": "Configure credentials or BYOK setup.",
    "Remote agent endpoint or protocol client is not configured yet.": (
        "Configure the remote endpoint or protocol client."
    ),
    "Executable adapter is not implemented yet.": (
        "Implement and review provider adapter or protocol client."
    ),
    "Executable adapter or protocol client is not implemented yet.": (
        "Implement and review provider adapter or protocol client."
    ),
    "PlanMyAgents has not benchmarked this provider for this capability yet.": (
        "Add benchmark cases and pass the benchmark gate."
    ),
    "Benchmark suite has not passed for this capability.": (
        "Add benchmark cases and pass the benchmark gate."
    ),
    "No promoted MCP/A2A/AI-agent provider is routable for this capability yet.": (
        "Promote an MCP/A2A/AI-agent provider or approve API fallback."
    ),
}


def build_gap_report(*, plan: GoalPlan, discovery: dict[str, Any] | None) -> dict[str, Any]:
    """Build user-facing unsupported-task gap report."""

    missing = sorted(set(plan.missing_capabilities) or {task.capability for task in plan.sub_tasks})
    candidates = discovery.get("candidates", []) if discovery else []
    selected = {capability: _best_ranked_candidate(capability, candidates) for capability in missing}
    gaps = [
        _gap_for_capability(capability, candidates, selected_candidates=selected)
        for capability in missing
    ]
    workflow_options = _workflow_options(missing, candidates, limit=3)
    return {
        "status": "blocked",
        "summary": _summary(gaps),
        "qualification_policy": {
            "qualified_provider_types": sorted(AGENTIC_PROVIDER_TYPES),
            "excluded_provider_types": ["api_provider", "payment_provider"],
        },
        "missing_capabilities": missing,
        "capability_gaps": gaps,
        "workflow_options": workflow_options,
        "next_steps": _next_steps(gaps),
    }


def _gap_for_capability(
    capability: str,
    candidates: list[dict[str, Any]],
    *,
    selected_candidates: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    ranked = _ranked_candidates(capability, candidates)
    rejected = _rejected_candidates(
        capability=capability,
        candidates=candidates,
        selected_candidates=selected_candidates,
    )
    best = _compatible_best_candidate(
        capability=capability,
        ranked=ranked,
        selected_candidates=selected_candidates,
    )
    if not ranked and rejected:
        blockers = _qualification_blockers(capability)
        return {
            "capability": capability,
            "status": "no_qualified_candidate_found",
            "best_candidate": None,
            "blockers": blockers,
            "required_promotion_steps": [_promotion_step(blocker) for blocker in blockers],
            "candidate_count": len(ranked),
            "rejected_candidates": rejected,
        }
    if ranked and best is None:
        blockers = _compatibility_blockers(
            capability=capability,
            selected_candidates=selected_candidates,
        )
        return {
            "capability": capability,
            "status": "candidate_found_but_incompatible",
            "best_candidate": None,
            "blockers": blockers,
            "required_promotion_steps": [_promotion_step(blocker) for blocker in blockers],
            "candidate_count": len(ranked),
            "rejected_candidates": rejected,
        }

    blockers = _blockers(best)
    return {
        "capability": capability,
        "status": "candidate_found_but_not_routable" if best else "no_candidate_found",
        "best_candidate": _candidate_summary(best) if best else None,
        "blockers": blockers,
        "required_promotion_steps": [_promotion_step(blocker) for blocker in blockers],
        "candidate_count": len(ranked),
        "rejected_candidates": rejected,
    }


def _best_ranked_candidate(
    capability: str, candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    ranked = _ranked_candidates(capability, candidates)
    return ranked[0] if ranked else None


def _ranked_candidates(
    capability: str, candidates: list[dict[str, Any]], *, qualified_only: bool = True
) -> list[dict[str, Any]]:
    matching = [
        candidate
        for candidate in candidates
        if capability in set(candidate.get("capabilities", []))
        and (
            not qualified_only
            or _is_qualified_candidate(candidate)
        )
    ]
    return sorted(
        matching,
        key=lambda candidate: (
            PROVIDER_TYPE_PRIORITY.get(str(candidate.get("provider_type", "api_provider")), 99),
            -float(candidate.get("match_score", 0.0)),
            str(candidate.get("provider_id", "")),
        ),
    )


def _workflow_options(
    missing: list[str], candidates: list[dict[str, Any]], *, limit: int
) -> list[dict[str, Any]]:
    if not missing:
        return []
    if "booking_execution" not in missing:
        return [_generic_workflow_option(missing, candidates)]

    booking_candidates = _ranked_candidates("booking_execution", candidates)[:3]
    if not booking_candidates:
        option = _generic_workflow_option(missing, candidates)
        option["rank"] = 1
        return [option]

    options: list[dict[str, Any]] = []
    for booking_candidate in booking_candidates:
        options.append(
            _booking_workflow_option(
                missing=missing,
                candidates=candidates,
                booking_candidate=booking_candidate,
                travel_anchor=None,
                label="Booking-provider cohesive flow",
            )
        )
        anchors = _travel_anchors(missing, candidates, booking_candidate)[:3]
        for anchor in anchors:
            options.append(
                _booking_workflow_option(
                    missing=missing,
                    candidates=candidates,
                    booking_candidate=booking_candidate,
                    travel_anchor=anchor,
                    label=f"{anchor.get('display_name') or anchor.get('provider_id')} assisted flow",
                )
            )

    deduped = _dedupe_workflow_options(options)
    ranked = sorted(deduped, key=lambda option: (-float(option["score"]), option["title"]))
    for index, option in enumerate(ranked[:limit], start=1):
        option["rank"] = index
    return ranked[:limit]


def _generic_workflow_option(missing: list[str], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    steps = []
    selected: dict[str, dict[str, Any] | None] = {}
    for capability in missing:
        candidate = _best_ranked_candidate(capability, candidates)
        selected[capability] = candidate
        steps.append(
            _workflow_step(
                capability=capability,
                candidate=candidate,
                status="candidate" if candidate else "no_qualified_candidate_found",
                rejected_candidates=_rejected_candidates(
                    capability=capability,
                    candidates=candidates,
                    selected_candidates=selected,
                ),
            )
        )
    return _score_workflow_option(title="Best available capability matches", steps=steps)


def _booking_workflow_option(
    *,
    missing: list[str],
    candidates: list[dict[str, Any]],
    booking_candidate: dict[str, Any],
    travel_anchor: dict[str, Any] | None,
    label: str,
) -> dict[str, Any]:
    steps = []
    booking_provider_id = str(booking_candidate.get("provider_id") or "")
    for capability in missing:
        candidate = _candidate_for_booking_flow_capability(
            capability=capability,
            candidates=candidates,
            booking_candidate=booking_candidate,
            travel_anchor=travel_anchor,
        )
        if capability == "payment_authorization" and candidate is None:
            steps.append(
                _workflow_step(
                    capability=capability,
                    candidate=None,
                    status="candidate_found_but_incompatible",
                    blockers=_compatibility_blockers(
                        capability=capability,
                        selected_candidates={"booking_execution": booking_candidate},
                    ),
                    rejected_candidates=_rejected_candidates(
                        capability=capability,
                        candidates=candidates,
                        selected_candidates={"booking_execution": booking_candidate},
                    ),
                )
            )
            continue
        status = "candidate" if candidate else "no_candidate_found"
        steps.append(
            _workflow_step(
                capability=capability,
                candidate=candidate,
                status=status,
                rejected_candidates=_rejected_candidates(
                    capability=capability,
                    candidates=candidates,
                    selected_candidates={"booking_execution": booking_candidate},
                ),
            )
        )
    title = f"{label}: {booking_provider_id or 'unknown booking provider'}"
    return _score_workflow_option(title=title, steps=steps)


def _candidate_for_booking_flow_capability(
    *,
    capability: str,
    candidates: list[dict[str, Any]],
    booking_candidate: dict[str, Any],
    travel_anchor: dict[str, Any] | None,
) -> dict[str, Any] | None:
    booking_provider_id = str(booking_candidate.get("provider_id") or "")
    if capability == "booking_execution":
        return booking_candidate
    if capability == "payment_authorization":
        return _compatible_best_candidate(
            capability=capability,
            ranked=_ranked_candidates(capability, candidates),
            selected_candidates={"booking_execution": booking_candidate},
        )
    if travel_anchor and _candidate_supports(travel_anchor, capability):
        return travel_anchor
    if _candidate_supports(booking_candidate, capability):
        return booking_candidate
    if booking_provider_id:
        same_provider = _candidate_by_id_for_capability(candidates, booking_provider_id, capability)
        if same_provider:
            return same_provider
    return _best_ranked_candidate(capability, candidates)


def _travel_anchors(
    missing: list[str], candidates: list[dict[str, Any]], booking_candidate: dict[str, Any]
) -> list[dict[str, Any]]:
    anchor_capabilities = [cap for cap in ("travel_search", "fare_comparison") if cap in missing]
    anchors_by_id: dict[str, dict[str, Any]] = {}
    booking_provider_id = str(booking_candidate.get("provider_id") or "")
    for capability in anchor_capabilities:
        for candidate in _ranked_candidates(capability, candidates)[:5]:
            candidate_id = str(candidate.get("provider_id") or "")
            if candidate_id and candidate_id != booking_provider_id:
                anchors_by_id[candidate_id] = candidate
    return sorted(
        anchors_by_id.values(),
        key=lambda candidate: (
            PROVIDER_TYPE_PRIORITY.get(str(candidate.get("provider_type", "api_provider")), 99),
            -float(candidate.get("match_score", 0.0)),
            str(candidate.get("provider_id", "")),
        ),
    )


def _candidate_by_id_for_capability(
    candidates: list[dict[str, Any]], provider_id: str, capability: str
) -> dict[str, Any] | None:
    for candidate in candidates:
        if str(candidate.get("provider_id") or "") == provider_id and _candidate_supports(
            candidate, capability
        ):
            return candidate
    return None


def _rejected_candidates(
    *,
    capability: str,
    candidates: list[dict[str, Any]],
    selected_candidates: dict[str, dict[str, Any] | None],
) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    booking_candidate = selected_candidates.get("booking_execution")
    booking_provider_id = str((booking_candidate or {}).get("provider_id") or "")
    for candidate in _ranked_candidates(capability, candidates, qualified_only=False):
        provider_type = str(candidate.get("provider_type", "api_provider"))
        if provider_type not in AGENTIC_PROVIDER_TYPES:
            rejected.append(
                {
                    **(_candidate_summary(candidate) or {}),
                    "rejection_reason": (
                        "Excluded by current qualification policy: only MCP servers, "
                        "A2A agents, and AI agents count as qualified candidates."
                    ),
                }
            )
            continue
        if not _is_qualified_candidate(candidate):
            verification_status = str(candidate.get("verification_status") or "unverified")
            rejected.append(
                {
                    **(_candidate_summary(candidate) or {}),
                    "rejection_reason": (
                        f"Excluded by verification policy: status `{verification_status}` "
                        "is below the trust threshold. Eligible statuses are "
                        + ", ".join(sorted(QUALIFIED_VERIFICATION_STATUSES))
                        + ". Run the post-goal MCP probe stage or add the provider to "
                        "the curated registry to upgrade this candidate."
                    ),
                }
            )
            continue
        if (
            capability == "payment_authorization"
            and booking_provider_id
            and not _is_payment_compatible_with_booking(candidate, booking_provider_id)
        ):
            rejected.append(
                {
                    **(_candidate_summary(candidate) or {}),
                    "rejection_reason": (
                        f"No verified payment compatibility with `{booking_provider_id}`."
                    ),
                }
            )
    return rejected[:5]


def _candidate_supports(candidate: dict[str, Any], capability: str) -> bool:
    return capability in set(candidate.get("capabilities", []))


def _workflow_step(
    *,
    capability: str,
    candidate: dict[str, Any] | None,
    status: str,
    blockers: list[str] | None = None,
    rejected_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "capability": capability,
        "status": status,
        "candidate": _candidate_summary(candidate) if candidate else None,
        "blockers": blockers or ([] if candidate else ["No qualified candidate selected."]),
        "rejected_candidates": rejected_candidates or [],
    }


def _score_workflow_option(*, title: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(steps) or 1
    candidate_steps = [step for step in steps if step["candidate"]]
    candidate_ids = [
        str(step["candidate"]["provider_id"])
        for step in candidate_steps
        if step.get("candidate") and step["candidate"].get("provider_id")
    ]
    unique_candidate_ids = set(candidate_ids)
    incompatible_steps = [
        step for step in steps if step["status"] == "candidate_found_but_incompatible"
    ]
    missing_steps = [step for step in steps if step["status"] == "no_candidate_found"]
    score = len(candidate_steps) / total
    if unique_candidate_ids:
        score += 0.12 * (1 - ((len(unique_candidate_ids) - 1) / max(1, len(candidate_ids))))
    score -= 0.2 * len(incompatible_steps)
    score -= 0.25 * len(missing_steps)
    score = round(max(0.0, min(1.0, score)), 4)
    blockers = sorted(
        {
            blocker
            for step in steps
            for blocker in step.get("blockers", [])
            if step["status"] != "candidate"
        }
    )
    return {
        "rank": 0,
        "title": title,
        "status": "blocked" if blockers else "candidate_found_but_not_routable",
        "score": score,
        "score_reasons": _workflow_score_reasons(steps, blockers),
        "steps": steps,
        "blockers": blockers,
    }


def _workflow_score_reasons(steps: list[dict[str, Any]], blockers: list[str]) -> list[str]:
    candidate_count = sum(1 for step in steps if step["candidate"])
    reasons = [f"{candidate_count}/{len(steps)} capabilities have candidate coverage"]
    providers = {
        step["candidate"]["provider_id"]
        for step in steps
        if step.get("candidate") and step["candidate"].get("provider_id")
    }
    if providers:
        reasons.append(f"{len(providers)} provider(s) across the flow")
    if blockers:
        reasons.append("workflow has unresolved compatibility or coverage blockers")
    return reasons


def _dedupe_workflow_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, ...], dict[str, Any]] = {}
    for option in options:
        key = tuple(
            str((step.get("candidate") or {}).get("provider_id") or "")
            for step in option["steps"]
        )
        existing = deduped.get(key)
        if existing is None or float(option["score"]) > float(existing["score"]):
            deduped[key] = option
    return list(deduped.values())


def _compatible_best_candidate(
    *,
    capability: str,
    ranked: list[dict[str, Any]],
    selected_candidates: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    if capability != "payment_authorization" or not selected_candidates.get("booking_execution"):
        return ranked[0] if ranked else None

    booking_candidate = selected_candidates["booking_execution"]
    if not booking_candidate:
        return ranked[0] if ranked else None
    booking_provider_id = str(booking_candidate.get("provider_id") or "")
    for candidate in ranked:
        if _is_payment_compatible_with_booking(candidate, booking_provider_id):
            return candidate
    return None


def _is_payment_compatible_with_booking(candidate: dict[str, Any], booking_provider_id: str) -> bool:
    candidate_id = str(candidate.get("provider_id") or "")
    if candidate_id and candidate_id == booking_provider_id:
        return True
    compatible_provider_ids = {
        str(item).strip()
        for item in candidate.get("compatible_provider_ids", [])
        if str(item).strip()
    }
    return booking_provider_id in compatible_provider_ids


# Verification statuses we consider "qualified" — i.e., trustworthy
# enough to surface as a real candidate to the user instead of
# rejecting them as unverified noise.
#
# History (Fix 3): the original gate required ``capability_verified``
# (we probed the agent's tools/list and confirmed). That single-tier
# rule rejected:
#
#   * Every curated agent in ``packages/discovery/sources/curated_ai_agents.json``
#     (Stripe, Shopify, Google Places, Razorpay, etc. — all
#     ``known_provider``).
#   * Every freshly-discovered MCP server from Smithery / MCP
#     Marketplace (provider exists, tools/list succeeds, but the
#     post-goal probe stage hadn't yet upgraded the row to
#     ``capability_verified``).
#
# Result: the gift goal would discover Perplexity MCP, Linkup MCP,
# Walmart MCP, Shopify Storefront, etc. with match_score ≥ 0.9, then
# this gate dropped all of them with the boilerplate
# "Excluded by verification policy: no verified public evidence...".
# The user saw "no agents discovered".
#
# Fix 3 broadens the gate to the full trust ladder above
# ``unverified``. Tier definitions match
# ``apps/web/src/lib/tags.ts`` so the badge the UI renders matches
# what the gate accepted.
QUALIFIED_VERIFICATION_STATUSES = frozenset({
    "capability_verified",  # we tested the candidate end-to-end
    "registered_in_directory",  # vendor-curated registry
    "known_provider",  # known provider identity/docs signal; capability inferred
})


def _is_qualified_candidate(candidate: dict[str, Any]) -> bool:
    return (
        str(candidate.get("provider_type", "api_provider")) in AGENTIC_PROVIDER_TYPES
        and str(candidate.get("verification_status") or "")
        in QUALIFIED_VERIFICATION_STATUSES
    )


def _compatibility_blockers(
    *,
    capability: str,
    selected_candidates: dict[str, dict[str, Any] | None],
) -> list[str]:
    if capability == "payment_authorization" and selected_candidates.get("booking_execution"):
        booking_candidate = selected_candidates["booking_execution"] or {}
        booking_provider_id = str(booking_candidate.get("provider_id") or "selected booking provider")
        return [
            f"No payment candidate has verified compatibility with `{booking_provider_id}`.",
            "Review the booking provider's payment, settlement, and merchant-of-record flow.",
        ]
    return ["Candidate compatibility with the selected workflow has not been verified."]


def _qualification_blockers(capability: str) -> list[str]:
    return [
        f"No verified MCP/A2A/AI-agent candidate was found for `{capability}` in the configured local sources.",
        "Live web, GitHub, MCP registry, and A2A directory discovery has not run for this request.",
        "API and payment-provider candidates are visible as fallbacks, not qualified agentic candidates.",
    ]


def _candidate_summary(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    return {
        "provider_id": candidate.get("provider_id"),
        "display_name": candidate.get("display_name"),
        "provider_type": candidate.get("provider_type"),
        "benchmark_status": candidate.get("benchmark_status"),
        "required_env_vars": candidate.get("required_env_vars", []),
        "compatible_provider_ids": candidate.get("compatible_provider_ids", []),
        "verification_status": candidate.get("verification_status"),
        "evidence_url": candidate.get("evidence_url"),
        "match_score": candidate.get("match_score"),
        "will_fail": bool(candidate.get("will_fail", True)),
    }


def _blockers(candidate: dict[str, Any] | None) -> list[str]:
    if not candidate:
        return ["No discovered candidate currently maps to this capability."]
    blockers = [str(item) for item in candidate.get("will_fail_reasons", []) if str(item).strip()]
    return blockers or ["Candidate has not passed promotion gates."]


def _promotion_step(blocker: str) -> str:
    if blocker.startswith("No verified MCP/A2A/AI-agent candidate was found for "):
        return "Run live discovery for MCP/A2A/AI-agent candidates before concluding coverage."
    if blocker == "Live web, GitHub, MCP registry, and A2A directory discovery has not run for this request.":
        return "Add live source discovery or scheduled research jobs."
    if blocker == "API and payment-provider candidates are visible as fallbacks, not qualified agentic candidates.":
        return "Keep fallback APIs separate from qualified agentic candidates."
    if blocker.startswith("No payment candidate has verified compatibility with "):
        return "Verify booking-provider payment compatibility before promotion."
    if blocker == "Review the booking provider's payment, settlement, and merchant-of-record flow.":
        return "Review booking, settlement, and merchant-of-record requirements."
    return BLOCKER_TO_PROMOTION_STEP.get(blocker, "Review candidate and complete promotion gates.")


def _next_steps(gaps: list[dict[str, Any]]) -> list[str]:
    steps = []
    for gap in gaps:
        steps.extend(gap["required_promotion_steps"])
    return sorted(set(steps))


def _summary(gaps: list[dict[str, Any]]) -> str:
    found = sum(1 for gap in gaps if gap["best_candidate"])
    total = len(gaps)
    if not total:
        return "No external capability gaps were identified."
    return (
        f"Execution is blocked. Candidate providers exist for {found}/{total} "
        "missing capability gap(s), but none are routable yet."
    )
