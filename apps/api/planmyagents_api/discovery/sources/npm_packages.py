"""npm discovery source (https://registry.npmjs.org).

The npm public registry is the **largest single channel** by which MCP
servers are distributed today — 47,000+ packages tagged with the
``mcp`` keyword as of 2026-05. Most published MCP servers ship as
``@modelcontextprotocol/server-*`` or community ``@*/*-mcp-*`` npm
packages; many of those packages **never get listed** in Smithery,
Glama, or the official MCP registry because publication to those
catalogs is a separate manual step.

This scout catches the npm population directly. The dedupe layer
(:mod:`planmyagents_api.discovery.dedupe`) collapses anything we
already pulled from one of the curated MCP aggregators (by
``install_command`` or ``setup_url``), so the net effect is recall
addition rather than duplication.

Endpoint contract (verified 2026-05-20)::

    GET https://registry.npmjs.org/-/v1/search
        ?text={query}            # full-text + keyword search
        &size={n}                # max 250 per request
        &from={offset}           # for paging

Response shape (one row truncated; verified live)::

    {
      "total": 47215,
      "objects": [
        {
          "downloads": {"weekly": 35990764, "monthly": 144096407},
          "dependents": "49564",
          "updated": "2026-05-19T08:11:05.875Z",
          "searchScore": 44.72969,
          "package": {
            "name":         "@modelcontextprotocol/sdk",
            "description":  "Model Context Protocol implementation for TypeScript",
            "version":      "1.29.0",
            "keywords":     ["modelcontextprotocol", "mcp"],
            "publisher":    {...},
            "maintainers":  [...],
            "license":      "MIT",
            "date":         "2026-03-30T16:50:42.718Z",
            "links": {
              "homepage":   "https://modelcontextprotocol.io",
              "repository": "git+https://github.com/modelcontextprotocol/typescript-sdk.git",
              "bugs":       "...",
              "npm":        "https://www.npmjs.com/package/@modelcontextprotocol/sdk"
            }
          },
          "score": {
            "final": 44.72969,
            "detail": {"popularity": 1, "quality": 1, "maintenance": 1}
          }
        }
      ]
    }

The npm registry's ``score.final`` is a 0–100 composite of
(popularity × quality × maintenance). Combined with the integer
``dependents`` count, we have a much richer corroboration signal than
Smithery's binary ``isDeployed`` — see the corroboration policy in the
class docstring below. No API key is required; this scout runs in
every deployment.
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

DEFAULT_SEARCH_URL = "https://registry.npmjs.org/-/v1/search"

USER_AGENT = (
    "planmyagents-discovery/0.1 "
    "(+https://planmyagents.com; first-party Tier-1 source)"
)

# Keyword queries to issue when no explicit ``task_description`` is
# supplied. ``keywords:mcp`` alone returns 47K results; adding the
# narrower ``a2a-agent`` tag picks up the (much smaller) A2A-agent
# packaging trend.
DEFAULT_KEYWORD_QUERIES: tuple[str, ...] = (
    "keywords:mcp",
    "keywords:model-context-protocol",
    "keywords:a2a-agent",
)


@dataclass(frozen=True)
class NpmMcpPackagesSource:
    """Pull MCP/A2A packages from the public npm registry search.

    Defense in depth — matches the MCP-aggregator pattern but adds an
    npm-specific corroboration gate that exploits the registry's
    richer signal::

    1. ``is_obvious_junk`` regex pre-filter on the package name drops
       test/copy/fork entries before paying for inference. npm's open-
       publish model has the same skew Smithery does.
    2. Corroboration-or-drop using npm-native fields. Accept ANY of:

        * ``dependents`` count >= ``min_dependents_for_signal`` (i.e.
          someone else's build pipeline depends on this package — the
          single strongest "real" signal npm exposes)
        * ``downloads.weekly`` >= ``min_weekly_downloads_for_signal``
          (raw consumption; users have actually installed and pulled
          it)
        * ``score.final`` >= ``min_score_for_signal`` (npm's own
          composite quality metric — leans on the publisher having
          filled out the manifest properly)
        * Repository URL pointing at github.com / gitlab.com — a
          credible proof-of-source-of-truth

       Without any of these the entry is a published-but-untested
       package, almost never useful for routing real /goal traffic.
    3. ``infer_capabilities_for_source`` substring/embedding match
       against ``name + description + keywords`` — must yield at
       least one capability to be retained.
    4. Request-time ``CandidateJudge`` (LLM) is the load-bearing
       relevance check at retrieval time.

    No ``requires_token``: npm's search endpoint is unauthenticated,
    so this scout always runs. The dispatcher's per-scout time budget
    is the only backstop.
    """

    base_url: str = DEFAULT_SEARCH_URL
    source_id: str = "npm_mcp_packages"
    # page_size + max_pages tuned 2026-05-20 against a budget of
    # 8 s in the dispatcher (see ``scouts.py``). Earlier values
    # (100 × 2 = 200 packages/query) reliably tripped the
    # dispatcher timeout because each surviving package needs an
    # embedder inference (~50 ms) for capability binding —
    # 200 packages × 50 ms = 10 s of CPU-bound work alone, before
    # any HTTP overhead. 50 × 1 = 50 packages/query, of which
    # typically 5-15 survive the corroboration filter, fits well
    # under 8 s and still gives the judge LLM enough candidates to
    # rank. The cron backfill (``make discovery-refresh``)
    # overrides these via constructor args to scan more deeply.
    page_size: int = 50
    max_pages: int = 1
    timeout_seconds: float = 6.0
    goal_hash: str = ""
    # Corroboration thresholds — tuned against the 47K-result baseline
    # so that ``mcp``-keyword packages with ANY of (a) at least 1
    # dependent, (b) >= 25 weekly downloads, or (c) >= 0.05 npm
    # composite score, or (d) a github/gitlab repo URL get through. The
    # bars are deliberately low because the judge LLM downstream is
    # the high-precision filter; this gate's only job is to drop
    # "I-published-a-skeleton" noise.
    min_dependents_for_signal: int = 1
    min_weekly_downloads_for_signal: int = 25
    min_score_for_signal: float = 0.05
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        """Fetch + normalise records from npm search."""

        queries = self._build_queries(
            task_description=task_description, capabilities=capabilities
        )
        records = self._fetch_pages_across_queries(queries=queries)

        candidates: list[DiscoveryCandidate] = []
        seen_names: set[str] = set()
        for raw in records:
            package = raw.get("package") or {}
            if not isinstance(package, dict):
                continue
            name = str(package.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            normalised = self._normalise(raw)
            if normalised is None:
                continue
            if capabilities and not normalised.supports_any(capabilities):
                continue
            candidates.append(normalised)
        return candidates

    def _build_queries(
        self, *, task_description: str, capabilities: set[str]
    ) -> list[str]:
        """Compose the queries we'll issue against npm search.

        When the caller gave a task description we use it directly
        prepended with the ``keywords:mcp`` filter (so we don't drown
        in random npm packages). When there's no description we fall
        back to the canonical keyword queries — useful for cold
        upfront crawls (``make discovery-refresh``) when there's no
        user goal in the picture.
        """

        text = (task_description or "").strip()
        if text:
            return [f"keywords:mcp {text}"]
        if capabilities:
            cap_term = sorted(capabilities)[0].replace("_", " ")
            return [f"keywords:mcp {cap_term}"]
        return list(DEFAULT_KEYWORD_QUERIES)

    def _fetch_pages_across_queries(
        self, *, queries: list[str]
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for query in queries:
            for page_idx in range(max(self.max_pages, 1)):
                offset = page_idx * self.page_size
                url = self._page_url(query=query, offset=offset)
                payload = _fetch_json(url, timeout_seconds=self.timeout_seconds)
                if not isinstance(payload, dict):
                    break
                objects = payload.get("objects")
                if not isinstance(objects, list) or not objects:
                    break
                merged.extend(o for o in objects if isinstance(o, dict))
                # The ``total`` field tells us when we've drained the
                # result set. Stop as soon as the next page would be
                # past the total to avoid wasted round trips.
                total = payload.get("total")
                if isinstance(total, int) and offset + len(objects) >= total:
                    break
        return merged

    def _page_url(self, *, query: str, offset: int) -> str:
        params: list[tuple[str, str]] = [
            ("text", query),
            ("size", str(self.page_size)),
        ]
        if offset:
            params.append(("from", str(offset)))
        return f"{self.base_url}?{parse.urlencode(params)}"

    def _normalise(self, row: dict[str, Any]) -> DiscoveryCandidate | None:
        package = row.get("package") or {}
        name = str(package.get("name") or "").strip()
        description = str(package.get("description") or "").strip()
        keywords_raw = package.get("keywords") or []
        if not isinstance(keywords_raw, list):
            keywords_raw = []
        keywords: list[str] = [
            str(k).strip().lower() for k in keywords_raw if isinstance(k, str)
        ]
        version = str(package.get("version") or "").strip()
        links = package.get("links") or {}
        if not isinstance(links, dict):
            links = {}
        homepage = str(links.get("homepage") or "").strip()
        repo_url = _clean_repo_url(str(links.get("repository") or "").strip())
        npm_url = str(links.get("npm") or "").strip()
        date = str(package.get("date") or "").strip()

        # Defense layer 1: shared junk filter on the package name.
        if is_obvious_junk(name):
            return None

        # Defense layer 2: corroboration-or-drop using npm-native
        # signals. See class docstring for the rationale; each
        # threshold is configurable.
        dependents = _safe_int(row.get("dependents"))
        downloads = row.get("downloads") or {}
        weekly_downloads = (
            _safe_int(downloads.get("weekly"))
            if isinstance(downloads, dict)
            else 0
        )
        score = row.get("score") or {}
        final_score = (
            _safe_float(score.get("final"))
            if isinstance(score, dict)
            else 0.0
        )
        has_repo = repo_url.startswith("https://github.com/") or repo_url.startswith(
            "https://gitlab.com/"
        )
        has_dependents = dependents >= self.min_dependents_for_signal
        has_downloads = weekly_downloads >= self.min_weekly_downloads_for_signal
        has_score = final_score >= self.min_score_for_signal
        if not (has_repo or has_dependents or has_downloads or has_score):
            return None

        # Capability inference — give the inferencer name + description
        # + ANY explicit keywords the publisher tagged. Keywords are
        # often the highest-precision signal (publishers tag their own
        # packages with the actual capabilities they expose).
        capability_text = " ".join(
            [name, description, *keywords]
        ).strip()
        capabilities = infer_capabilities_for_source(
            text=capability_text,
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        vendor = _vendor_from_package_name(name)

        # Provenance metadata for the CandidateJudge + UI badges.
        # Surfacing weekly_downloads and dependents lets the judge
        # break ties: an MCP server with 100K weekly downloads should
        # win against one with 25.
        metadata: dict[str, Any] = {
            "npm_package_name": name,
            "npm_package_url": npm_url,
            "npm_version": version,
        }
        if dependents > 0:
            metadata["npm_dependents"] = dependents
        if weekly_downloads > 0:
            metadata["npm_weekly_downloads"] = weekly_downloads
        if final_score > 0:
            metadata["npm_score_final"] = round(final_score, 3)
        if date:
            metadata["npm_published_at"] = date
        if keywords:
            metadata["npm_keywords"] = keywords[:20]

        # Install command — what the user would actually paste into
        # Claude Desktop / Cursor to wire the server up. Stashed in
        # both ``metadata`` (matches the MCP Marketplace convention so
        # downstream recipe-export readers find it in one place) and
        # ``docs.install_steps`` (where the existing normaliser
        # already reads ordered install steps). See the
        # ``install_resolvable`` gate from Sprint 2 P1-6 for the
        # downstream consumer.
        install_command = f"npx -y {name}"
        metadata["npm_install_command"] = install_command

        raw_candidate = {
            "id": name,
            "display_name": name,
            "vendor": vendor,
            "vendor_url": homepage or repo_url or npm_url,
            "provider_type": "mcp_server",
            # npm packages are publisher-signed (npm requires email
            # verification + 2FA for publishing in many namespaces).
            # The npm registry itself doesn't certify "this is an MCP
            # server", so we mirror the Smithery / Marketplace default
            # of ``registered_in_directory`` — one tier above
            # ``unverified``, leaving the actual capability-verified
            # promotion to the request-time MCP probe.
            "verification_status": "registered_in_directory",
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            "docs": {
                "setup_url": homepage or repo_url or npm_url,
                "auth_method": "",
                "install_steps": [install_command],
            },
            "evidence_url": npm_url or homepage or repo_url,
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


def _vendor_from_package_name(name: str) -> str:
    """Pull the vendor segment out of an npm package name.

    ``@modelcontextprotocol/server-memory`` -> ``modelcontextprotocol``.
    ``mcp-server-foo`` -> ``mcp-server-foo`` (no scope; the package
    name itself acts as the vendor identifier).
    """

    if not name:
        return ""
    if name.startswith("@") and "/" in name:
        return name[1:].split("/", 1)[0]
    return name


def _clean_repo_url(url: str) -> str:
    """Strip the ``git+`` prefix and trailing ``.git`` that npm puts
    on ``links.repository`` URLs. Returns the bare HTTPS URL so it
    matches the format the rest of the discovery layer assumes."""

    if not url:
        return ""
    cleaned = url
    if cleaned.startswith("git+"):
        cleaned = cleaned[4:]
    if cleaned.startswith("git@github.com:"):
        cleaned = "https://github.com/" + cleaned[len("git@github.com:"):]
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    return cleaned


def _safe_int(value: Any) -> int:
    """npm returns ``dependents`` as a string and various counters as
    ints. Coerce defensively; treat anything unparseable as zero."""

    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _safe_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _fetch_json(url: str, *, timeout_seconds: float) -> Any:
    """Unauthenticated JSON fetch. Returns ``None`` on any failure
    so the dispatcher records the scout as ``ok / 0 candidates``
    rather than aborting the whole /goal request."""

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
