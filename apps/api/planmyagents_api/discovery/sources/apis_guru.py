"""APIs.guru OpenAPI directory discovery source.

Loads the public, unauthenticated APIs.guru directory at
``https://api.apis.guru/v2/list.json`` (CC0, ~2,500 providers, ~9 MB).

Why this source matters:

* Zero cost, zero key — the canonical free OpenAPI directory.
* Each entry yields the URL of the actual OpenAPI spec, which our
  ``run_openapi_enricher.py`` script can then deepen with tool/operation
  schemas.
* Fills the long tail of API providers (Stripe, Twilio, GitHub, Mailchimp,
  AWS, etc.) that GitHub code search is too coarse to find reliably.

Response shape (abbreviated)::

    {
      "1forge.com": {
        "added": "...",
        "preferred": "0.0.1",
        "versions": {
          "0.0.1": {
            "info": {
              "title": "1Forge Finance APIs",
              "description": "Stock and Forex Data and News",
              "x-providerName": "1forge.com",
              "contact": {"url": "..."}
            },
            "swaggerUrl": "https://api.apis.guru/v2/specs/1forge.com/0.0.1/swagger.json",
            "openapiVer": "2.0",
            "updated": "..."
          }
        }
      },
      ...
    }

Capability inference is heuristic (text matching against
:data:`CAPABILITY_SYNONYMS`). Entries that don't map to any known capability
are dropped at normalization — that's intentional, because emitting 2,500
capability-less rows would pollute the index. Future work: expand the
capability taxonomy or use semantic embeddings to map entries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib import error, request

from planmyagents_api.discovery.capability_index import infer_capabilities_for_source
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate

DEFAULT_LIST_URL = "https://api.apis.guru/v2/list.json"


@dataclass(frozen=True)
class ApisGuruSource:
    """Pull OpenAPI provider records from the APIs.guru public directory."""

    list_url: str = DEFAULT_LIST_URL
    source_id: str = "apis_guru"
    timeout_seconds: float = 15.0
    goal_hash: str = ""
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)
    # Cap how many candidates we emit per refresh so a single source can't
    # dominate the store. APIs.guru has ~2,500 entries; in practice only a
    # fraction map to our capability taxonomy, but the cap keeps refresh
    # times bounded even if the taxonomy expands.
    max_candidates: int = 1000

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        payload = _fetch_json(self.list_url, timeout_seconds=self.timeout_seconds)
        if not isinstance(payload, dict):
            return []
        candidates: list[DiscoveryCandidate] = []
        for provider_key, provider_payload in payload.items():
            if len(candidates) >= self.max_candidates:
                break
            if not isinstance(provider_payload, dict):
                continue
            preferred = self._preferred_version(provider_payload)
            if preferred is None:
                continue
            normalized = self._normalize(
                provider_key=provider_key,
                provider_payload=provider_payload,
                version_payload=preferred,
            )
            if normalized is None:
                continue
            if capabilities and not normalized.supports_any(capabilities):
                continue
            candidates.append(normalized)
        return candidates

    def _preferred_version(self, provider_payload: dict[str, Any]) -> dict[str, Any] | None:
        versions = provider_payload.get("versions")
        if not isinstance(versions, dict) or not versions:
            return None
        preferred = provider_payload.get("preferred")
        if isinstance(preferred, str) and preferred in versions:
            value = versions[preferred]
            return value if isinstance(value, dict) else None
        # Fallback: pick the lexicographically last version (rough proxy for
        # latest in the absence of a `preferred` marker).
        for key in sorted(versions.keys(), reverse=True):
            value = versions[key]
            if isinstance(value, dict):
                return value
        return None

    def _normalize(
        self,
        *,
        provider_key: str,
        provider_payload: dict[str, Any],  # noqa: ARG002 — kept for future use
        version_payload: dict[str, Any],
    ) -> DiscoveryCandidate | None:
        info = version_payload.get("info") if isinstance(version_payload.get("info"), dict) else {}
        title = str(info.get("title") or provider_key).strip()
        description = str(info.get("description") or "").strip()
        contact = info.get("contact") if isinstance(info.get("contact"), dict) else {}
        external_docs = (
            info.get("externalDocs")
            if isinstance(info.get("externalDocs"), dict)
            else {}
        )

        capabilities = _infer_capabilities(
            text=" ".join([provider_key, title, description]),
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        vendor = str(info.get("x-providerName") or provider_key.split(":", 1)[0]).strip()
        vendor_url = (
            str(contact.get("url") or "").strip()
            or str(external_docs.get("url") or "").strip()
            or f"https://{vendor}"
        )
        openapi_url = str(version_payload.get("swaggerUrl") or "").strip()

        raw = {
            "id": provider_key,
            "display_name": title,
            "vendor": vendor,
            "vendor_url": vendor_url,
            "provider_type": "api_provider",
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            "openapi_url": openapi_url,
            "docs": {
                "setup_url": vendor_url,
                "auth_method": "",
            },
            "evidence_url": openapi_url or vendor_url,
        }
        try:
            return normalize_candidate(
                raw,
                source=self.source_id,
                requested_capabilities=sorted(capabilities),
                goal_hash=self.goal_hash,
            )
        except (CandidateNormalizationError, TypeError, ValueError):
            return None


def _infer_capabilities(*, text: str, extra: dict[str, set[str]]) -> set[str]:
    # Note: no fallback here. APIs.guru entries that don't match any
    # registry capability get dropped by the caller (apis_guru emits
    # ~2,500 OpenAPI specs and we only want the routable subset). This
    # matches the source's pre-existing behaviour intentionally.
    return infer_capabilities_for_source(text=text, extra=extra)


USER_AGENT = (
    "agent-manager-discovery/0.1 "
    "(+https://github.com/deepraj-jha/agent-manager; first-party Tier-1 source)"
)


def _fetch_json(url: str, *, timeout_seconds: float) -> Any:
    # Many public JSON catalogs are fronted by CDNs that 403 default Python
    # urllib User-Agents (we hit this on apis.guru). Send a real, identifiable
    # UA so our crawler can be allow-listed or rate-limited politely.
    req = request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return None
