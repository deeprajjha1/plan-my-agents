"""Official Model Context Protocol registry discovery source.

Pulls MCP server records from the canonical, unauthenticated registry
operated by the MCP protocol authors at
``https://registry.modelcontextprotocol.io/v0/servers``.

Why this source matters:

* No API key required (strictly better than Smithery for our purposes)
* Records are wrapped as ``{"server": {...}, "_meta": {...}}`` and the same
  server name appears once per published version — we keep only the entry
  marked ``isLatest`` so the index doesn't get version-spammed.
* Capabilities are not declared in the schema, so we *infer* them from the
  server's name + description via :data:`CAPABILITY_SYNONYMS`. Records with
  no inferred capability are dropped at normalization (the normalizer
  refuses capability-less candidates by design).

Registry response shape (abbreviated)::

    {
      "servers": [
        {
          "server": {
            "name": "ac.inference.sh/mcp",
            "title": "inference.sh",
            "description": "Run 150+ AI apps...",
            "version": "1.0.1",
            "remotes": [{"type": "streamable-http", "url": "..."}],
            "repository": {"url": "...", "source": "github"}
          },
          "_meta": {
            "io.modelcontextprotocol.registry/official": {
              "status": "active",
              "isLatest": true
            }
          }
        }
      ],
      "metadata": {"nextCursor": "..."}
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib import error, parse, request

from planmyagents_api.discovery.capability_index import infer_capabilities_for_source
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, normalize_candidate
from planmyagents_api.discovery.sources.mcp_publication_quality import (
    is_obvious_junk as _shared_is_obvious_junk,
)

DEFAULT_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"

OFFICIAL_META_KEY = "io.modelcontextprotocol.registry/official"


@dataclass(frozen=True)
class OfficialMcpRegistrySource:
    """Pull canonical MCP server records from the protocol authors' registry.

    Defense in depth against registry junk:

    1. Cheap regex pass over the server name (``_is_obvious_junk``)
       drops names with numeric suffixes, "school"/"test"/"demo"
       tokens, and similar tells of test/template entries. Cuts the
       obvious 80% at zero cost.
    2. ``_infer_capabilities`` substring match — must yield at least
       one capability for a candidate to be retained. Loose by design;
       the judge cleans up after it.
    3. The request-time ``CandidateJudge`` (LLM, see
       ``planmyagents_api.discovery.candidate_judge``) re-evaluates each
       candidate's relevance against the user's actual goal and drops
       anything that doesn't hold up. **This is the load-bearing
       safety net** for goal/candidate mismatches like the
       school-project-as-fare-comparison incident — substring tagging
       can still happen here, but the judge will reject any candidate
       that doesn't actually serve the goal.

    The ``agent_classifier`` is intentionally *not* applied at this
    stage. The classifier is built for announcement-shaped sources
    (RSS/HN posts) and requires "Introducing/Launching" verbs to
    accept. MCP registry entries are bare product listings — applying
    the classifier here would over-reject legitimate registry rows.
    """

    base_url: str = DEFAULT_REGISTRY_URL
    source_id: str = "official_mcp_registry"
    page_size: int = 100
    max_pages: int = 5
    timeout_seconds: float = 10.0
    goal_hash: str = ""
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        """Fetch + normalize records. Per-capability filtering is applied last.

        We deliberately ignore ``task_description`` for the *fetch* step (the
        registry doesn't take free-text queries we can trust) and ignore it
        for capability inference too — the fields we have on each record
        (name + description) carry the signal.
        """

        records = self._fetch_latest_records()
        candidates: list[DiscoveryCandidate] = []
        for raw in records:
            normalized = self._normalize(raw)
            if normalized is None:
                continue
            if capabilities and not normalized.supports_any(capabilities):
                continue
            candidates.append(normalized)
        return candidates

    def _fetch_latest_records(self) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        cursor: str | None = None
        for _ in range(max(self.max_pages, 1)):
            url = self._page_url(cursor=cursor)
            payload = _fetch_json(url, timeout_seconds=self.timeout_seconds)
            if not isinstance(payload, dict):
                break
            for entry in payload.get("servers", []) or []:
                server = entry.get("server") if isinstance(entry, dict) else None
                meta = entry.get("_meta", {}) if isinstance(entry, dict) else {}
                if not isinstance(server, dict):
                    continue
                official_meta = meta.get(OFFICIAL_META_KEY, {}) if isinstance(meta, dict) else {}
                # The registry returns one row per published version. Keep only
                # the row flagged `isLatest=true`. If a server hasn't been
                # marked latest yet, fall back to "first seen" semantics.
                name = str(server.get("name") or "").strip()
                if not name:
                    continue
                if not official_meta.get("isLatest", True):
                    if name not in latest:
                        # Don't replace a real latest with a non-latest entry.
                        continue
                latest[name] = server
            cursor = (
                payload.get("metadata", {}).get("nextCursor")
                if isinstance(payload.get("metadata"), dict)
                else None
            )
            if not cursor:
                break
        return list(latest.values())

    def _page_url(self, *, cursor: str | None) -> str:
        params: list[tuple[str, str]] = [("limit", str(self.page_size))]
        if cursor:
            params.append(("cursor", cursor))
        return f"{self.base_url}?{parse.urlencode(params)}"

    def _normalize(self, server: dict[str, Any]) -> DiscoveryCandidate | None:
        name = str(server.get("name") or "").strip()
        title = str(server.get("title") or "").strip()
        description = str(server.get("description") or "").strip()
        repository = server.get("repository") if isinstance(server.get("repository"), dict) else {}
        remotes = server.get("remotes") if isinstance(server.get("remotes"), list) else []

        # Defense layer 1: drop obvious junk by name shape before paying
        # for any text inference. Removes the
        # "ai.smithery/aicastle-school-..." and "...project123123123"
        # shaped entries that motivated this whole refactor.
        #
        # Why we don't run the agent_classifier here: the classifier is
        # built for *announcement-shaped* sources (RSS/HN posts) and
        # requires a "launch verb" in the title to accept anything. MCP
        # registry entries are bare product listings — they say
        # "Search and run image AI apps", not "Introducing X". Forcing
        # them through the announcement-shaped classifier would reject
        # legitimate registry listings. The defense-in-depth here is:
        # name regex + capability inference at ingest, then the
        # request-time CandidateJudge does the relevance call against
        # the user's actual goal at retrieval time. The judge is the
        # load-bearing safety net.
        if _is_obvious_junk(name):
            return None

        capabilities = _infer_capabilities(
            text=" ".join([name, title, description]),
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        vendor = _vendor_from_name(name)
        primary_remote_url = ""
        for remote in remotes:
            if isinstance(remote, dict) and remote.get("url"):
                primary_remote_url = str(remote["url"])
                break

        raw = {
            "id": name,
            "display_name": title or name,
            "vendor": vendor or name.split("/")[0],
            "vendor_url": str(repository.get("url") or primary_remote_url or "").strip(),
            "provider_type": "mcp_server",
            # The official MCP registry is the canonical vendor-
            # curated index for MCP servers. Presence there is a
            # stronger signal than community discovery channels —
            # ``registered_in_directory`` reflects that the server
            # cleared registry submission gates.
            "verification_status": "registered_in_directory",
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            "docs": {
                "setup_url": str(repository.get("url") or "").strip(),
                "auth_method": "",
            },
            "evidence_url": str(repository.get("url") or primary_remote_url or "").strip(),
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


def _is_obvious_junk(name: str) -> bool:
    """Backwards-compatible wrapper around the shared MCP-publication
    quality filter. The implementation now lives in
    :mod:`planmyagents_api.discovery.sources.mcp_publication_quality`
    so that the new MCP Marketplace and Smithery sources apply the same
    rules — see the shared module's docstring for the rationale.
    """

    return _shared_is_obvious_junk(name)


def _vendor_from_name(name: str) -> str:
    """Best-effort vendor extraction from MCP-style namespaced server names.

    e.g. "ac.inference.sh/mcp" → "inference.sh"
         "modelcontextprotocol/servers/postgres" → "modelcontextprotocol"
    """

    if not name:
        return ""
    namespace = name.split("/", 1)[0]
    if "." in namespace:
        parts = namespace.split(".")
        # Heuristic: prefer the longest dot-separated segment that looks like a
        # registrable domain ("inference.sh"), falling back to the last two.
        if len(parts) >= 2:
            return ".".join(parts[-2:])
    return namespace


def _infer_capabilities(*, text: str, extra: dict[str, set[str]]) -> set[str]:
    """Map an MCP registry entry's text to registry capability ids.

    Returns an empty set if nothing matches — the normalizer drops the
    candidate, which is the right behaviour for first-pass MCP indexing
    (the official MCP registry has many entries; we only want the
    capability-relevant subset).
    """

    return infer_capabilities_for_source(text=text, extra=extra)


USER_AGENT = (
    "agent-manager-discovery/0.1 "
    "(+https://github.com/deepraj-jha/agent-manager; first-party Tier-1 source)"
)


def _fetch_json(url: str, *, timeout_seconds: float) -> Any:
    # Send a real User-Agent so CDNs don't 403 us (some public catalogs do).
    req = request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return None
