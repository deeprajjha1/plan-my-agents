"""Glama discovery source (https://glama.ai).

The largest community-curated MCP-server directory by volume — 23,794+
MCP servers as of 2026-05-17, broader than Smithery (~5,000) and the
official MCP registry (~200-500 blessed servers). The directory is
public and the JSON API at ``glama.ai/api/mcp/v1/servers`` requires no
auth, so this source can always run regardless of which keys the
deployment has provisioned.

We pull Glama in as a peer of Smithery / MCP Marketplace / Official MCP
Registry for **recall**: Glama indexes a meaningful long tail of
third-party servers that haven't shown up in any of the other three
catalogs yet. The dedupe layer (``dedupe_key`` in
:mod:`planmyagents_api.discovery.dedupe`) drops anything Glama
duplicates with Smithery or the registry; the net-new servers are pure
upside.

Endpoint contract (verified 2026-05-20)::

    GET https://glama.ai/api/mcp/v1/servers
        ?query={text}          # full-text + semantic search (optional)
        &after={cursor}        # cursor-based pagination (optional)

Response shape (one row truncated; verified live)::

    {
      "pageInfo": {
        "endCursor": "eyJjcmVhdGVkQXQiOjE3Nzg1NDc3NDgsImlkIjoibG1vNXplNGh4aCJ9",
        "hasNextPage": true,
        "hasPreviousPage": false,
        "startCursor": "eyJjcmVhdGVkQXQiOjE3NzkxNDUyOTIsImlkIjoidGxiOXZkdHZsayJ9"
      },
      "servers": [
        {
          "id":          "lmo5ze4hxh",
          "name":        "mcp-gmail",
          "namespace":   "knowledgeislands",
          "slug":        "mcp-gmail",
          "description": "An MCP server that lets Claude read, search, send, ...",
          "attributes":  ["hosting:local-only"],     # hosting:local-only | hosting:remote-capable | hosting:hybrid | author:official
          "spdxLicense": {"name": "MIT License", "url": "..."},   # may be null
          "repository":  {"url": "https://github.com/knowledgeislands/mcp-gmail"},
          "environmentVariablesJsonSchema": { ... JSON Schema for env config },
          "tools":       [...],                       # often empty on listing endpoint
          "url":         "https://glama.ai/mcp/servers/lmo5ze4hxh"
        }
      ]
    }

Glama is an open-publish directory in the same way Smithery and MCP
Marketplace are, so we apply the **same** junk pre-filter
(:mod:`planmyagents_api.discovery.sources.mcp_publication_quality`) on
the namespace/slug before paying for any text inference. The judge LLM
at retrieval time is the load-bearing relevance check.
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

DEFAULT_SEARCH_URL = "https://glama.ai/api/mcp/v1/servers"

USER_AGENT = (
    "planmyagents-discovery/0.1 "
    "(+https://planmyagents.com; first-party Tier-1 source)"
)


@dataclass(frozen=True)
class GlamaDirectorySource:
    """Pull MCP servers from Glama's public JSON API.

    Defense in depth (mirrors Smithery / MCP Marketplace / Official MCP
    Registry — see ``mcp_publication_quality`` for the rationale):

    1. ``is_obvious_junk`` regex pre-filter on the
       ``{namespace}/{slug}`` composite drops test/demo/school/fork
       entries before capability inference runs. Glama's open-publish
       model produces the same long tail of low-quality entries the
       other MCP aggregators do.
    2. ``infer_capabilities_for_source`` substring/embedding match —
       must yield at least one capability for a candidate to be
       retained.
    3. Request-time ``CandidateJudge`` (LLM) is the load-bearing
       relevance check at retrieval time.

    Corroboration-or-drop policy (Sprint 2 contract, mirrored from
    Smithery): a Glama listing is only surfaced when SOMETHING signals
    "real" from the upstream. Accepted signals are:

    * A populated ``repository.url`` (Glama's `mcp-listings` workflow
      validates the link, so a present URL is a useful proof-of-life)
    * One or more declared ``attributes`` (hosting tier or
      ``author:official`` provenance)
    * A non-empty ``environmentVariablesJsonSchema.properties`` map
      (the publisher took the time to declare config — a credible
      signal of actual usability)

    Without any of these we drop the entry — Glama's directory
    includes some skeleton placeholders that exist as namespace
    reservations and shouldn't pollute the index.

    No ``requires_token``: Glama's API is unauthenticated, so this
    source always runs. The dispatcher's per-scout time budget is the
    only backstop.
    """

    base_url: str = DEFAULT_SEARCH_URL
    source_id: str = "glama"
    max_pages: int = 3
    timeout_seconds: float = 10.0
    goal_hash: str = ""
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        """Fetch + normalise records via cursor-paginated calls."""

        query = self._build_query(
            task_description=task_description, capabilities=capabilities
        )
        records = self._fetch_pages(query=query)
        candidates: list[DiscoveryCandidate] = []
        seen_ids: set[str] = set()
        for raw in records:
            server_id = str(raw.get("id") or "").strip()
            if not server_id or server_id in seen_ids:
                continue
            seen_ids.add(server_id)
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
        cursor: str | None = None
        for _ in range(max(self.max_pages, 1)):
            url = self._page_url(query=query, cursor=cursor)
            payload = _fetch_json(url, timeout_seconds=self.timeout_seconds)
            if not isinstance(payload, dict):
                break
            servers = payload.get("servers")
            if not isinstance(servers, list) or not servers:
                break
            merged.extend(s for s in servers if isinstance(s, dict))
            page_info = payload.get("pageInfo") or {}
            if not (
                isinstance(page_info, dict)
                and page_info.get("hasNextPage")
            ):
                break
            cursor = page_info.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                break
        return merged

    def _page_url(self, *, query: str, cursor: str | None) -> str:
        params: list[tuple[str, str]] = []
        if query:
            params.append(("query", query))
        if cursor:
            params.append(("after", cursor))
        if not params:
            return self.base_url
        return f"{self.base_url}?{parse.urlencode(params)}"

    def _normalise(self, row: dict[str, Any]) -> DiscoveryCandidate | None:
        server_id = str(row.get("id") or "").strip()
        name = str(row.get("name") or "").strip()
        namespace = str(row.get("namespace") or "").strip()
        slug = str(row.get("slug") or "").strip()
        description = str(row.get("description") or "").strip()
        listing_url = str(row.get("url") or "").strip()

        repository = row.get("repository") or {}
        repo_url = ""
        if isinstance(repository, dict):
            repo_url = str(repository.get("url") or "").strip()

        attributes = row.get("attributes") or []
        if not isinstance(attributes, list):
            attributes = []
        attribute_set = {str(a) for a in attributes if isinstance(a, str)}

        env_schema = row.get("environmentVariablesJsonSchema") or {}
        env_properties: dict[str, Any] = {}
        if isinstance(env_schema, dict):
            properties = env_schema.get("properties") or {}
            if isinstance(properties, dict):
                env_properties = properties

        qualified = f"{namespace}/{slug}" if namespace and slug else (slug or name)

        # Defense layer 1: shared junk filter on the composite name.
        if is_obvious_junk(qualified):
            return None

        # Defense layer 2: corroboration-or-drop.
        has_repo = bool(repo_url) and (
            repo_url.startswith("https://github.com/")
            or repo_url.startswith("https://gitlab.com/")
        )
        has_attributes = len(attribute_set) > 0
        has_env_schema = len(env_properties) > 0
        if not (has_repo or has_attributes or has_env_schema):
            return None

        display = name or slug or qualified

        capability_text = " ".join(
            [name, qualified, description]
        ).strip()
        capabilities = infer_capabilities_for_source(
            text=capability_text,
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        # Vendor extraction: prefer Glama namespace (the publisher
        # handle). Fall back to the repository owner segment when
        # namespace is empty — the GitHub/GitLab path embeds the same
        # signal at lower precision.
        vendor = namespace or _vendor_from_repo_url(repo_url) or display

        # Provenance metadata for the CandidateJudge and the UI.
        # ``glama_hosting`` makes the local/remote/hybrid split
        # surface-visible (a Phase-3 partnership pitch can use the
        # remote-capable split as a routability signal). We keep the
        # raw attribute list too so future filters can read other
        # ``author:*`` style tags without us having to reissue a fetch.
        hosting_tier = ""
        for attr in attribute_set:
            if attr.startswith("hosting:"):
                hosting_tier = attr.split(":", 1)[1]
                break
        is_official = "author:official" in attribute_set

        metadata: dict[str, Any] = {
            "glama_id": server_id,
            "glama_attributes": sorted(attribute_set),
        }
        if hosting_tier:
            metadata["glama_hosting"] = hosting_tier
        if is_official:
            metadata["glama_author_official"] = True
        if env_properties:
            metadata["glama_env_var_count"] = len(env_properties)

        # Verification tier rationale:
        # * ``author:official`` listings are publisher-signed — the
        #   vendor themselves submitted the entry. Treat as
        #   ``known_provider`` (same tier we'd give an official
        #   vendor RSS feed).
        # * Everything else with a valid repo URL is
        #   ``registered_in_directory`` — one tier above
        #   ``unverified``, mirroring the Smithery and MCP
        #   Marketplace defaults.
        verification_status = (
            "known_provider" if is_official else "registered_in_directory"
        )

        raw_candidate = {
            "id": qualified,
            "display_name": display,
            "vendor": vendor,
            "vendor_url": repo_url or listing_url,
            "provider_type": "mcp_server",
            "verification_status": verification_status,
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            "docs": {
                "setup_url": listing_url or repo_url,
                "auth_method": "",
            },
            "evidence_url": listing_url or repo_url,
            "metadata": metadata,
            "description": description,
        }
        try:
            return normalize_candidate(
                raw_candidate,
                source=self.source_id,
                requested_capabilities=sorted(capabilities),
                goal_hash=self.goal_hash,
            )
        except (CandidateNormalizationError, TypeError, ValueError):
            return None


def _vendor_from_repo_url(url: str) -> str:
    """Pull the owner segment out of a GitHub/GitLab URL.

    ``https://github.com/foo/bar`` -> ``foo``. Returns empty string
    when the URL doesn't follow the expected shape.
    """

    if not url:
        return ""
    for prefix in ("https://github.com/", "https://gitlab.com/"):
        if url.startswith(prefix):
            tail = url[len(prefix):]
            owner = tail.split("/", 1)[0]
            return owner.strip()
    return ""


def _fetch_json(url: str, *, timeout_seconds: float) -> Any:
    """Unauthenticated JSON fetch.

    Returns ``None`` on any network/parse failure — the dispatcher
    treats this as "ok / 0 candidates" rather than failing the whole
    /goal request. Mirrors the policy in
    :mod:`planmyagents_api.discovery.sources.smithery`.
    """

    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return None
