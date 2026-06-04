"""MCP Marketplace discovery source (https://mcp-marketplace.io).

A curated, security-graded MCP-server registry (~1,400+ servers as of
2026-05). Strictly broader than ``official_mcp_registry`` because the
Marketplace also indexes third-party MCP servers that were never
submitted to the canonical protocol-authors' registry. No API key is
required for the search endpoint.

Why this is its own source rather than a configuration knob on the
official-registry source:

* The response shape is different (flat ``results`` list, no nested
  ``server``/``_meta`` envelope, no per-version dedup) and warrants
  its own normaliser.
* The Marketplace adds a ``securityScore`` (0–10) and a per-server
  ``free`` flag that we surface as ``provenance`` so the LLM
  ``CandidateJudge`` can later prefer high-score / free servers when
  multiple candidates tie on capability.
* The Marketplace API supports semantic search via ``q``, so unlike
  the official registry we *do* push the task description into the
  fetch step. This raises recall on long-tail queries (e.g. "store
  locator") that no curated synonym list would cover.

Endpoint contract (verified 2026-05-13)::

    GET https://mcp-marketplace.io/api/registry/search
        ?q={text}            # semantic + keyword search (optional)
        &limit={n}           # page size, max documented 100
        &page={n}            # 1-indexed
        &sort={...}          # optional; we leave default
        &free_only={bool}    # optional; we don't filter

Response shape (one row truncated for brevity)::

    {
      "results": [
        {
          "name":            "Memory",
          "slug":            "memory",
          "tagline":         "Knowledge graph-based persistent memory",
          "url":             "https://mcp-marketplace.io/server/memory",
          "category":        "Productivity",
          "mode":            "local",            # or "remote"
          "free":            true,
          "securityScore":   8,                  # 0–10, may be null
          "rating":          null,
          "githubStars":     85544,
          "toolCount":       0,
          "installCommand":  "claude mcp add memory -- npx -y @modelcontextprotocol/server-memory@2026.1.26"
        }
      ],
      "total": 80,
      "page": 1,
      "pages": 40,
      "limit": 2
    }

The Marketplace is open-publish in the same way the official registry
is, so we apply the **same** junk pre-filter
(:mod:`planmyagents_api.discovery.sources.mcp_publication_quality`).
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

DEFAULT_SEARCH_URL = "https://mcp-marketplace.io/api/registry/search"

USER_AGENT = (
    "planmyagents-discovery/0.1 "
    "(+https://planmyagents.com; first-party Tier-1 source)"
)


@dataclass(frozen=True)
class MCPMarketplaceSource:
    """Pull MCP servers from MCP Marketplace's public search API.

    Defense in depth (same layered approach the official registry uses):

    1. The shared ``is_obvious_junk`` regex pre-filter rejects names
       with test/demo/school tokens, numeric-suffix tells, and
       ``-copy``/``-fork``/``-vN``/``-test*`` suffixes before any
       capability inference runs. Cuts the obvious 80% at zero cost.
    2. ``infer_capabilities_for_source`` substring/embedding match —
       must yield at least one capability for a candidate to be
       retained. Loose by design; the judge cleans up after it.
    3. The request-time ``CandidateJudge`` (LLM, see
       :mod:`planmyagents_api.discovery.candidate_judge`) re-evaluates
       each candidate's relevance against the user's actual goal and
       drops anything that doesn't hold up. **This is the load-bearing
       safety net** for goal/candidate mismatches.

    Like ``OfficialMcpRegistrySource``, the ``agent_classifier`` is
    intentionally not applied here — the classifier is built for
    announcement-shaped sources (RSS/HN posts) and requires
    "Introducing/Launching" verbs to accept anything. Marketplace
    listings are bare product pages without those verbs.

    The Marketplace API supports semantic search via ``q``, so
    ``task_description`` is fed into the fetch step (unlike the
    official registry which ignores it). When ``task_description`` is
    empty we fall back to a single capability-keyword query so the
    fetch isn't wasted on a generic top-N pull.
    """

    base_url: str = DEFAULT_SEARCH_URL
    source_id: str = "mcp_marketplace"
    page_size: int = 50
    max_pages: int = 3
    timeout_seconds: float = 10.0
    goal_hash: str = ""
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        """Fetch + normalise records.

        The Marketplace's ``q`` parameter is fed by:

        1. ``task_description`` if non-empty — gives the upstream's
           semantic search the most signal.
        2. The first capability id (joined as space-separated words)
           if no task description was supplied — keeps the fetch
           non-empty for the cold-start case where the dispatcher
           has only the capability slug.
        3. No query at all (top-N feed) as the last resort.

        Per-capability filtering is applied after fetch — the upstream
        cannot match against our internal capability ids.
        """

        query = self._build_query(
            task_description=task_description, capabilities=capabilities
        )
        records = self._fetch_pages(query=query)
        candidates: list[DiscoveryCandidate] = []
        seen_slugs: set[str] = set()
        for raw in records:
            slug = str(raw.get("slug") or "").strip()
            if not slug or slug in seen_slugs:
                # Same slug across pages (Marketplace pagination
                # occasionally repeats while results re-rank) — keep
                # the first occurrence only so the index doesn't see
                # one server twice from the same fetch.
                continue
            seen_slugs.add(slug)
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
            # Prefer the first capability deterministically for cache-
            # ability; the underlying set() ordering is platform-
            # dependent so we sort.
            first = sorted(capabilities)[0]
            return first.replace("_", " ")
        return ""

    def _fetch_pages(self, *, query: str) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for page_num in range(1, max(self.max_pages, 1) + 1):
            url = self._page_url(query=query, page=page_num)
            payload = _fetch_json(url, timeout_seconds=self.timeout_seconds)
            if not isinstance(payload, dict):
                break
            results = payload.get("results")
            if not isinstance(results, list) or not results:
                break
            merged.extend(r for r in results if isinstance(r, dict))
            # Honour the upstream's reported page count so we don't
            # waste an extra request on an empty trailing page.
            total_pages = payload.get("pages")
            if isinstance(total_pages, int) and page_num >= total_pages:
                break
        return merged

    def _page_url(self, *, query: str, page: int) -> str:
        params: list[tuple[str, str]] = [
            ("limit", str(self.page_size)),
            ("page", str(page)),
        ]
        if query:
            params.append(("q", query))
        return f"{self.base_url}?{parse.urlencode(params)}"

    def _normalise(self, row: dict[str, Any]) -> DiscoveryCandidate | None:
        slug = str(row.get("slug") or "").strip()
        name = str(row.get("name") or "").strip()
        tagline = str(row.get("tagline") or "").strip()
        url = str(row.get("url") or "").strip()
        category = str(row.get("category") or "").strip()
        install_command = str(row.get("installCommand") or "").strip()
        mode = str(row.get("mode") or "").strip().lower()
        is_free = bool(row.get("free"))
        security_score = row.get("securityScore")
        github_stars = row.get("githubStars")
        rating = row.get("rating")
        tool_count = row.get("toolCount")

        # Defense layer 1: drop obvious junk by slug shape before
        # paying for any text inference. Same filter as the official
        # registry — see mcp_publication_quality.is_obvious_junk for
        # the rules and rationale.
        if is_obvious_junk(slug):
            return None

        # Defense layer 2: corroboration-or-drop (Sprint 2). Mirrors
        # the Moltbook + Smithery policy: a Marketplace listing is
        # only worth surfacing when SOMETHING signals real adoption,
        # installability, or upstream-side review. We accept ANY of:
        #
        #   - ``installCommand`` set (user can actually install it)
        #   - ``githubStars > 0`` (real repo with non-zero adoption)
        #   - ``rating`` set (someone has rated it)
        #   - ``toolCount > 0`` (declares concrete capabilities)
        #   - ``securityScore`` set (Marketplace's automated scanner
        #     has actually inspected the server — proves
        #     reachability and a non-trivial codebase)
        #
        # Without any of these, the entry is just a Marketplace
        # placeholder with no proof of life — not actionable and
        # inflates the candidate pool without adding signal.
        has_install = bool(install_command)
        has_stars = isinstance(github_stars, int) and github_stars > 0
        has_rating = isinstance(rating, int | float) and float(rating) > 0
        has_tools = isinstance(tool_count, int) and tool_count > 0
        has_security_score = (
            isinstance(security_score, int | float)
            and float(security_score) > 0
        )
        if not (
            has_install
            or has_stars
            or has_rating
            or has_tools
            or has_security_score
        ):
            return None

        # Capability inference uses everything we have textually:
        # name + tagline + category. The Marketplace's category labels
        # ("Productivity", "Communication", "Search & Web") are
        # often the strongest single signal because they're
        # human-curated, so they go in too.
        capability_text = " ".join([name, tagline, category]).strip()
        capabilities = infer_capabilities_for_source(
            text=capability_text,
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        vendor = _vendor_from_slug(slug)

        # Provenance signals from the Marketplace API. These are
        # consumed by the CandidateJudge LLM prompt (prefer
        # high-security, free, frequently-starred servers when
        # capability matches tie) and the corroboration filter
        # (S2-NEW-2-mcpmkt drops entries with no installCommand and
        # no githubStars > 0). Live in the free-form ``metadata`` bag.
        metadata: dict[str, Any] = {
            "marketplace_is_free": is_free,
        }
        if install_command:
            metadata["marketplace_install_command"] = install_command
        if mode:
            metadata["marketplace_mode"] = mode
        if isinstance(security_score, int | float):
            metadata["marketplace_security_score"] = float(security_score)
        if isinstance(github_stars, int) and github_stars > 0:
            metadata["marketplace_github_stars"] = github_stars
        if isinstance(rating, int | float):
            metadata["marketplace_rating"] = float(rating)
        if isinstance(tool_count, int) and tool_count > 0:
            metadata["marketplace_tool_count"] = tool_count
        if category:
            metadata["marketplace_category"] = category

        raw = {
            "id": slug,
            "display_name": name or slug,
            "vendor": vendor or "mcp-marketplace",
            "vendor_url": url,
            "provider_type": "mcp_server",
            # MCP Marketplace is a vendor-curated registry (cline.bot
            # / Anthropic). Entries pass Marketplace's own publication
            # checks plus our ``mcp_publication_quality`` filter, so
            # they qualify for ``registered_in_directory``. Tier ladder:
            # registered_in_directory < known_provider <
            # capability_verified — we promote to capability_verified
            # only after the post-goal MCP probe successfully fetches
            # tools/list.
            "verification_status": "registered_in_directory",
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            "docs": {
                "setup_url": url,
                "auth_method": "",
            },
            "evidence_url": url,
            "metadata": metadata,
            "tagline": tagline,
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


def _vendor_from_slug(slug: str) -> str:
    """Best-effort vendor extraction from MCP Marketplace slugs.

    Marketplace slugs come in two shapes:

    * Bare names: ``"memory"``, ``"github"`` — vendor unknown
    * Reverse-domain prefixed: ``"io-github-gjeltep-app-store-connect-mcp"``
      — vendor is the third dash-segment (``gjeltep``).

    We only attempt extraction on the reverse-domain shape so we don't
    return misleading vendor strings for bare names.
    """

    if not slug:
        return ""
    parts = slug.split("-")
    if len(parts) >= 4 and parts[0] in {"io", "com", "org", "ai", "co"}:
        # io-github-USERNAME-... or com-VENDOR-...
        if parts[1] in {"github", "gitlab", "bitbucket"}:
            return parts[2]
        return parts[1]
    return ""


def _fetch_json(url: str, *, timeout_seconds: float) -> Any:
    """Fetch JSON with an identifiable User-Agent.

    Returns ``None`` on any network/parse error so the caller can treat
    the fetch as "no data this round" without a try/except block.
    """

    req = request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return None
