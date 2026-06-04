"""A2A Agent Card discovery source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate
from planmyagents_api.discovery.sources.json_source import _load_json


@dataclass(frozen=True)
class A2AAgentCardSource:
    """Load A2A Agent Card candidates from JSON files or URLs."""

    locations: list[str]
    source_id: str = "a2a_agent_cards"
    timeout_seconds: float = 10.0
    goal_hash: str = ""

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        candidates: list[DiscoveryCandidate] = []
        for location in self.locations:
            for card in _agent_cards(_load_json(location, timeout_seconds=self.timeout_seconds)):
                raw = _candidate_from_agent_card(card)
                try:
                    candidate = normalize_candidate(
                        raw,
                        source=self.source_id,
                        requested_capabilities=sorted(capabilities),
                        goal_hash=self.goal_hash,
                    )
                except (CandidateNormalizationError, TypeError, ValueError):
                    continue
                if capabilities and not candidate.supports_any(capabilities):
                    continue
                candidates.append(candidate)
        return candidates


def _agent_cards(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("agent_cards", "agentCards", "agents", "items", "cards"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payload]


def _candidate_from_agent_card(card: dict[str, Any]) -> dict[str, Any]:
    provider = card.get("provider") if isinstance(card.get("provider"), dict) else {}
    skills_payload = card.get("skills") if isinstance(card.get("skills"), list) else []

    capability_payload = _capability_payload_from_card(card, skills_payload)
    skill_records = _skill_records(skills_payload)

    docs = _docs_from_card(card, provider)

    return {
        "id": card.get("id") or card.get("name") or card.get("url"),
        "display_name": card.get("display_name")
        or card.get("displayName")
        or card.get("name")
        or card.get("title"),
        "vendor": card.get("vendor")
        or provider.get("organization")
        or provider.get("name")
        or card.get("name"),
        "vendor_url": card.get("vendor_url")
        or provider.get("url")
        or card.get("url")
        or card.get("endpoint"),
        "provider_type": "a2a_agent",
        "capabilities": capability_payload,
        "required_env_vars": card.get("required_env_vars") or card.get("requiredEnvVars") or [],
        "verification_status": card.get("verification_status") or "unverified",
        "evidence_url": card.get("evidence_url") or card.get("agent_card_url") or card.get("url") or "",
        "skills": skill_records,
        "docs": docs,
    }


def _capability_payload_from_card(
    card: dict[str, Any], skills: list[Any]
) -> list[dict[str, Any]]:
    """Derive capability hypotheses from the card.

    The A2A spec uses ``capabilities`` as a protocol-features object
    (streaming, pushNotifications). Real operations live in ``skills``. When
    callers also store legacy capability lists under ``capabilities`` or
    ``functions`` we honour them, but ``skills`` is the canonical source.
    """

    legacy = card.get("functions")
    raw_capabilities = card.get("capabilities")
    if isinstance(raw_capabilities, dict) or raw_capabilities is None:
        # Protocol features (streaming, pushNotifications). Fall back to skills.
        candidates = legacy or skills or []
    else:
        candidates = raw_capabilities

    return _normalize_capability_records(candidates)


def _skill_records(skills: list[Any]) -> list[dict[str, Any]]:
    """Surface A2A skills as ``CandidateTool``-shaped records."""

    out: list[dict[str, Any]] = []
    for skill in skills:
        if isinstance(skill, str):
            name = skill.strip()
            if name:
                out.append({"name": name})
            continue
        if not isinstance(skill, dict):
            continue
        name = (skill.get("id") or skill.get("name") or "").strip()
        if not name:
            continue
        examples = skill.get("examples") or []
        if not isinstance(examples, list):
            examples = []
        record: dict[str, Any] = {
            "name": name,
            "description": str(skill.get("description") or "").strip(),
            "examples": [str(example).strip() for example in examples if str(example).strip()],
        }
        input_schema = skill.get("inputSchema") or skill.get("input_schema")
        if isinstance(input_schema, dict):
            record["input_schema"] = input_schema
        out.append(record)
    return out


def _docs_from_card(card: dict[str, Any], provider: dict[str, Any]) -> dict[str, Any]:
    """Extract any provider documentation hints from the AgentCard."""

    docs_payload = card.get("docs") if isinstance(card.get("docs"), dict) else {}

    auth_payload = (
        card.get("authentication")
        or card.get("auth")
        or docs_payload.get("auth")
        or {}
    )
    if isinstance(auth_payload, dict):
        scheme = (
            auth_payload.get("scheme")
            or auth_payload.get("type")
            or auth_payload.get("method")
            or ""
        )
        scopes = auth_payload.get("scopes") or auth_payload.get("scope") or []
        if not isinstance(scopes, list):
            scopes = [scopes]
    else:
        scheme = ""
        scopes = []

    return {
        "setup_url": docs_payload.get("setup_url")
        or card.get("documentationUrl")
        or card.get("documentation_url")
        or provider.get("docs_url")
        or "",
        "auth_method": str(scheme),
        "auth_scopes": [str(scope) for scope in scopes if str(scope).strip()],
        "install_steps": docs_payload.get("install_steps") or [],
        "usage_examples": docs_payload.get("usage_examples") or [],
        "pricing_url": docs_payload.get("pricing_url") or "",
        "status_page_url": docs_payload.get("status_page_url") or "",
    }


def _normalize_capability_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return []
    capabilities: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            capabilities.append(
                {"id": item, "confidence": 0.7, "notes": "Capability declared by A2A Agent Card."}
            )
        elif isinstance(item, dict):
            capability_id = item.get("id") or item.get("name") or item.get("capability")
            if capability_id:
                capabilities.append(
                    {
                        "id": str(capability_id),
                        "confidence": float(item.get("confidence", 0.7)),
                        "notes": str(
                            item.get("description")
                            or item.get("notes")
                            or "Capability declared by A2A Agent Card."
                        ),
                    }
                )
    return capabilities
