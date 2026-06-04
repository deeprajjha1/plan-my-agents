"""Web/documentation manifest discovery source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate
from planmyagents_api.discovery.sources.json_source import _load_json


@dataclass(frozen=True)
class WebDocDiscoverySource:
    """Load API/tool candidates from curated web-doc manifests.

    This source does not crawl arbitrary pages. It ingests reviewed manifests
    produced by research or future crawlers.
    """

    locations: list[str]
    source_id: str = "web_doc_manifest"
    timeout_seconds: float = 10.0
    goal_hash: str = ""

    def search(self, *, capabilities: set[str], task_description: str) -> list[DiscoveryCandidate]:
        candidates: list[DiscoveryCandidate] = []
        for location in self.locations:
            for item in _doc_records(_load_json(location, timeout_seconds=self.timeout_seconds)):
                raw = _candidate_from_doc_record(item)
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


def _doc_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("documents", "docs", "providers", "apis", "items", "candidates"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _candidate_from_doc_record(item: dict[str, Any]) -> dict[str, Any]:
    provider_type = str(item.get("provider_type") or item.get("providerType") or "api_provider")
    capabilities = (
        item.get("capabilities") or item.get("api_capabilities") or item.get("tasks") or []
    )
    return {
        "id": item.get("id") or item.get("slug") or item.get("name"),
        "display_name": item.get("display_name") or item.get("displayName") or item.get("name"),
        "vendor": item.get("vendor")
        or item.get("company")
        or item.get("owner")
        or item.get("name"),
        "vendor_url": item.get("vendor_url")
        or item.get("docs_url")
        or item.get("url")
        or item.get("homepage"),
        "provider_type": provider_type,
        "capabilities": _normalize_capabilities(capabilities),
        "required_env_vars": item.get("required_env_vars") or item.get("requiredEnvVars") or [],
        "verification_status": item.get("verification_status") or "known_provider",
        "evidence_url": item.get("evidence_url") or item.get("docs_url") or item.get("url") or "",
    }


def _normalize_capabilities(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            normalized.append(
                {
                    "id": item,
                    "confidence": 0.62,
                    "notes": "Capability inferred from documentation manifest.",
                }
            )
        elif isinstance(item, dict):
            capability_id = item.get("id") or item.get("name") or item.get("capability")
            if capability_id:
                normalized.append(
                    {
                        "id": str(capability_id),
                        "confidence": float(item.get("confidence", 0.62)),
                        "notes": str(
                            item.get("description")
                            or item.get("notes")
                            or "Capability inferred from documentation manifest."
                        ),
                    }
                )
    return normalized
