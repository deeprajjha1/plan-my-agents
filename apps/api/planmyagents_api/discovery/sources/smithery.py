"""Smithery discovery source (https://smithery.ai).

The largest open MCP-server registry by volume (5,000+ servers as of
2026-05). Smithery's API is bearer-token-authenticated, so this source
silently skips itself when ``SMITHERY_API_KEY`` is absent — mirroring
the GitHub Code Search source's ``requires_token`` behaviour. This
keeps the default deployment "no extra credentials needed" while
offering a 5–10× recall boost for operators who provision the key.

Endpoint contract (per Smithery API reference, 2026-05)::

    GET https://api.smithery.ai/servers
        ?q={text}            # semantic + full-text search (optional)
        &page={n}            # 1-indexed
        &pageSize={n}        # default 10, max 100
        &topK={n}            # candidate pool, 10-500
    Authorization: Bearer {SMITHERY_API_KEY}

Documented response shape (one row truncated for brevity)::

    {
      "servers": [
        {
          "qualifiedName":   "@upstash/context7-mcp",
          "displayName":     "Context7",
          "description":     "Reference the latest docs for most major SDKs",
          "homepage":        "https://smithery.ai/server/@upstash/context7-mcp",
          "iconUrl":         "...",
          "useCount":        12345,
          "remote":          true,
          "isDeployed":      true,
          "createdAt":       "2026-01-12T...",
          "tools":           [...]
        }
      ],
      "pagination": {
        "currentPage":  1,
        "pageSize":     50,
        "totalPages":   42,
        "totalCount":   2087
      }
    }

Smithery is open-publish like the official MCP registry and the
Marketplace, so we apply the same shared
:mod:`planmyagents_api.discovery.sources.mcp_publication_quality`
filter on the qualifiedName before any LLM cost is paid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib import error, parse, request

from planmyagents_api.discovery.capability_index import infer_capabilities_for_source
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import (
    CandidateNormalizationError,
    normalize_candidate,
)
from planmyagents_api.discovery.sources.mcp_publication_quality import (
    is_obvious_junk,
)

DEFAULT_SEARCH_URL = "https://api.smithery.ai/servers"

USER_AGENT = (
    "planmyagents-discovery/0.1 "
    "(+https://planmyagents.com; first-party Tier-1 source)"
)


@dataclass(frozen=True)
class SmitherySource:
    """Pull MCP servers from Smithery's authenticated registry API.

    Defense in depth (mirrors the other two MCP sources):

    1. ``is_obvious_junk`` regex pre-filter on the qualifiedName drops
       test/demo/school/copy entries before any text inference.
       Smithery's open-publish model produces a notable volume of these
       (the original junk-filter tokens were tuned partly against
       Smithery payloads — see ``test_test_demo_sandbox_token_names``).
    2. ``infer_capabilities_for_source`` substring/embedding match —
       must yield at least one capability for a candidate to be
       retained.
    3. Request-time ``CandidateJudge`` (LLM) is the load-bearing
       relevance check at retrieval time.

    Behaviour when ``SMITHERY_API_KEY`` is empty:

    * The dispatcher's ``requires_token`` mechanism (see
      :class:`planmyagents_api.discovery.scouts.Scout`) skips this scout
      entirely, so we never even construct the URL. Belt-and-braces:
      this source's :meth:`search` also returns ``[]`` immediately when
      ``token`` is empty in case it's used outside the dispatcher.

    Behaviour when the API returns 401 / 403 (revoked key, quota
    exhausted): the underlying urllib raises ``HTTPError``, which our
    catch-all in :func:`_fetch_json` swallows — the source returns
    ``[]`` and the dispatcher logs the scout as ``ok / 0 candidates``
    rather than failing the whole /goal request.
    """

    token: str = ""
    base_url: str = DEFAULT_SEARCH_URL
    source_id: str = "smithery"
    page_size: int = 50
    max_pages: int = 3
    timeout_seconds: float = 10.0
    goal_hash: str = ""
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        """Fetch + normalise records.

        Returns ``[]`` immediately when the token is missing — the
        dispatcher's ``requires_token`` already enforces this, but the
        defensive check matters for callers that use the source
        directly (audits, scripts).
        """

        if not self.token.strip():
            return []

        query = self._build_query(
            task_description=task_description, capabilities=capabilities
        )
        records = self._fetch_pages(query=query)
        candidates: list[DiscoveryCandidate] = []
        seen_names: set[str] = set()
        for raw in records:
            qualified = str(raw.get("qualifiedName") or "").strip()
            if not qualified or qualified in seen_names:
                continue
            seen_names.add(qualified)
            normalised = self._normalise(raw)
            if normalised is None:
                continue
            if capabilities and not normalised.supports_any(capabilities):
                continue
            candidates.append(normalised)
        return candidates

    def _build_query(
        self, *, task_description: str, capabilities: set[str]
    ) -> str:
        text = (task_description or "").strip()
        if text:
            return text
        if capabilities:
            return sorted(capabilities)[0].replace("_", " ")
        return ""

    def _fetch_pages(self, *, query: str) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for page_num in range(1, max(self.max_pages, 1) + 1):
            url = self._page_url(query=query, page=page_num)
            payload = _fetch_json(
                url, token=self.token, timeout_seconds=self.timeout_seconds
            )
            if not isinstance(payload, dict):
                break
            servers = payload.get("servers")
            if not isinstance(servers, list) or not servers:
                break
            merged.extend(s for s in servers if isinstance(s, dict))
            pagination = payload.get("pagination") or {}
            total_pages = (
                pagination.get("totalPages")
                if isinstance(pagination, dict)
                else None
            )
            if isinstance(total_pages, int) and page_num >= total_pages:
                break
        return merged

    def _page_url(self, *, query: str, page: int) -> str:
        params: list[tuple[str, str]] = [
            ("pageSize", str(self.page_size)),
            ("page", str(page)),
        ]
        if query:
            params.append(("q", query))
        return f"{self.base_url}?{parse.urlencode(params)}"

    def _normalise(self, row: dict[str, Any]) -> DiscoveryCandidate | None:
        qualified = str(row.get("qualifiedName") or "").strip()
        display = str(row.get("displayName") or "").strip()
        description = str(row.get("description") or "").strip()
        homepage = str(row.get("homepage") or "").strip()
        is_remote = bool(row.get("remote"))
        is_deployed = bool(row.get("isDeployed"))
        use_count = row.get("useCount")
        created_at = str(row.get("createdAt") or "").strip()

        # Defense layer 1: drop obvious junk by qualifiedName before
        # paying for any text inference. Smithery's open-publish model
        # produces measurable volume of test/copy entries, and the
        # shared filter was originally tuned against shapes seen here
        # (e.g. ``ai.smithery/arjunkmrm-py-test-0``).
        if is_obvious_junk(qualified):
            return None

        # Defense layer 2: corroboration-or-drop (Sprint 2). Mirrors
        # the Moltbook policy: a Smithery server is only worth
        # surfacing when SOMETHING signals "real" from the upstream
        # API itself. We accept ANY of:
        #
        #   - ``isDeployed=True`` (Smithery has actually attempted
        #     to serve this server's tools — strongest signal)
        #   - ``useCount > 0`` (someone has actually called it)
        #   - a plausible homepage URL (gives the user a place to
        #     land outside Smithery)
        #
        # Without any of these, the entry is just a self-published
        # listing with no proof of life — not actionable and inflates
        # the candidate pool without adding signal.
        has_deployment = bool(is_deployed)
        has_usage = isinstance(use_count, int) and use_count > 0
        has_homepage = bool(homepage) and homepage.startswith("https://")
        if not (has_deployment or has_usage or has_homepage):
            return None

        capability_text = " ".join([qualified, display, description]).strip()
        capabilities = infer_capabilities_for_source(
            text=capability_text,
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        vendor = _vendor_from_qualified_name(qualified)

        # Provenance signals from Smithery's API. These are
        # consumed by the CandidateJudge LLM prompt (high-use,
        # currently-deployed remote servers should win ties) and the
        # frontend (badges like "1.2k installs"). They live in the
        # free-form ``metadata`` bag — see DiscoveryCandidate's
        # ``metadata`` field for the contract.
        metadata: dict[str, Any] = {
            "smithery_is_remote": is_remote,
            "smithery_is_deployed": is_deployed,
        }
        if isinstance(use_count, int) and use_count > 0:
            metadata["smithery_use_count"] = use_count
        if created_at:
            metadata["smithery_created_at"] = created_at

        raw = {
            "id": qualified,
            "display_name": display or qualified,
            "vendor": vendor or qualified.split("/", 1)[0],
            "vendor_url": homepage,
            "provider_type": "mcp_server",
            # Smithery is a vendor-curated MCP registry. Entries are
            # gated by Smithery's own publication-quality checks
            # (we apply ``mcp_publication_quality`` on top), and
            # presence in the registry means at minimum that the
            # underlying MCP server exists and exposes a card. That
            # warrants ``registered_in_directory`` — one tier above
            # ``unverified``, one tier below ``capability_verified``
            # which we only assign after probing tools/list ourselves.
            "verification_status": "registered_in_directory",
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            "docs": {
                "setup_url": homepage,
                "auth_method": "",
            },
            "evidence_url": homepage,
            "metadata": metadata,
            "description": description,
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


def _vendor_from_qualified_name(qualified: str) -> str:
    """Smithery qualifiedNames come in two shapes:

    * ``"@upstash/context7-mcp"`` — vendor is the part after ``@``.
    * ``"github.com/foo/bar"`` — vendor is the second segment.

    Returns an empty string when nothing reliable can be extracted; the
    caller falls back to the first slash-segment.
    """

    if not qualified:
        return ""
    if qualified.startswith("@") and "/" in qualified:
        return qualified[1:].split("/", 1)[0]
    if "/" in qualified:
        parts = qualified.split("/")
        if len(parts) >= 2:
            return parts[1] if "." in parts[0] else parts[0]
    return ""


def _fetch_json(url: str, *, token: str, timeout_seconds: float) -> Any:
    """Authenticated JSON fetch.

    Returns ``None`` on any network/parse/auth failure so the caller
    can treat the fetch uniformly. We deliberately do *not* propagate
    401 / 403 separately — the dispatcher should keep the rest of the
    /goal request running on the other scouts even when Smithery is
    unhappy with our key.
    """

    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
    }
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return None
