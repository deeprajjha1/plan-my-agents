"""Moltbook discovery source (https://www.moltbook.com).

Background and what Moltbook actually is
----------------------------------------
Moltbook positions itself as "the front page of the agent internet" —
a Reddit-style social network where AI agents (called "moltys") post,
comment, and build reputation. It is NOT a structured agent capability
registry like MCP Marketplace or Smithery; there is no public
"give me agents that do X" endpoint.

What Moltbook DOES offer that we can use:

* A **semantic search** API over posts and comments. Each result
  carries the author molty's name.
* A **profile lookup** API (``GET /agents/profile?name=...``) that
  returns the molty's curated description and karma.

The discovery model implemented here:

1. Run a semantic search for the user's task description.
2. Group the post/comment results by author to get the unique molty
   set that has actually written about the topic.
3. For the top-K most-frequently-appearing moltys, fetch the profile
   and use the curated description as the capability-inference text.
4. Require **external corroboration** — at least one link to a real
   artifact (GitHub repo, npm/PyPI package, MCP server, third-party
   homepage) — before emitting a candidate. See ``_extract_corroborations``
   below for the full justification.
5. Apply the same junk filter and capability binder as the other MCP
   sources, then normalise into ``DiscoveryCandidate``.

This is lower-precision than MCP Marketplace / Smithery — a molty
posting "I think AI safety is important" doesn't mean they audit AI
safety — but it widens the recall surface significantly. The
request-time LLM ``CandidateJudge`` is the load-bearing relevance
check that filters the noise downstream, so over-fetching here is
acceptable as long as we cap the number of profile lookups.

Why we require corroboration before emitting (the "phantom-agent" risk)
----------------------------------------------------------------------
Moltbook's only registration check is *human identity* (the human
owner's X account is verified via tweet). It does NOT verify that the
agent has any executable surface — anyone with a working X account can
register a name + free-text description and Moltbook will list it as
a "verified agent". Without an additional check, our scout would
happily surface profile pages with no callable backing, leading users
to click out to a dead end.

To mitigate this we drop any molty whose profile description does not
mention at least one of:

* A code-host repo (GitHub / GitLab / Bitbucket / Codeberg / SourceHut)
* A package registry entry (npm, PyPI, RubyGems, crates.io)
* An MCP server registry entry (mcp.so, Smithery)
* A non-Moltbook ``https`` homepage

The strongest such reference is promoted to ``evidence_url`` and
``vendor_url`` so the user lands on the real artifact (not the
Moltbook profile) when they click through, and the candidate is
tagged with ``verification_status="community_listed"`` so the UI can
honestly say "self-listed on Moltbook (links to <github/npm/...>)"
rather than implying we tested anything ourselves. The Moltbook
profile URL is preserved in ``docs.setup_url`` for traceability.

Endpoint contract (per Moltbook SKILL.md, 2026-05)::

    GET https://www.moltbook.com/api/v1/search?q={text}&limit={n}&type=all
    Authorization: Bearer {MOLTBOOK_API_KEY}

    GET https://www.moltbook.com/api/v1/agents/profile?name={author_name}
    Authorization: Bearer {MOLTBOOK_API_KEY}

Behaviour when the key is missing: the dispatcher's
``requires_token`` mechanism skips this scout entirely (see
:class:`planmyagents_api.discovery.scouts.Scout`). Belt-and-braces:
the source's :meth:`search` also returns ``[]`` immediately when the
token is empty, in case it is invoked outside the dispatcher.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
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

DEFAULT_BASE_URL = "https://www.moltbook.com/api/v1"

USER_AGENT = (
    "planmyagents-discovery/0.1 "
    "(+https://planmyagents.com; first-party Tier-1 source)"
)

# Cap on per-request profile fetches. Each /agents/profile lookup is
# one HTTP round-trip; with ``MAX_AUTHOR_PROFILES_PER_REQUEST`` set
# to 8 we cap wall time at ~2s for that step (250ms per fetch with
# some slack), well inside the scout's 10s budget.
DEFAULT_MAX_AUTHOR_PROFILES = 8

# Cap on search-result fetches. Moltbook tops out at 50/page, so we
# stay one page deep by default — beyond the first page the semantic
# similarity drops off fast, and we'd be paying API quota for noise.
DEFAULT_SEARCH_LIMIT = 30

# Lower bound on the molty's text length we're willing to use for
# capability inference. Anything shorter is almost certainly an
# unclaimed-default profile that won't bind to a capability anyway,
# so we skip the inference step rather than spending CPU on it.
MIN_DESCRIPTION_LENGTH = 16

# Verification status assigned to Moltbook candidates that pass the
# corroboration check below. Distinct from the schema's neutral
# "unverified" default (which says "we have no signal at all") and
# from "known_provider" / "capability_verified" (which require
# us to have actually tested the provider). "community_listed" means:
# "self-listed on a community network and we cross-referenced an
# external artifact (GitHub repo / package / homepage), but neither
# the platform nor we have run any executable check". The frontend
# tag mapper (see apps/web/src/lib/tags.ts) renders this as a
# yellow "Self-listed (community)" pill.
COMMUNITY_LISTED_STATUS = "community_listed"

# Lower-priority value = stronger corroboration. Picked so the
# strongest evidence wins when we promote one URL to ``evidence_url``.
# Justification per kind:
#  * github / gitlab / bitbucket / codeberg / sourcehut — actual code,
#    signed commits, issue history. Strongest signal.
#  * npm / pypi / rubygems / crates — published artifact with a known
#    install command and download counts. Strong but no source guarantee.
#  * mcp_server (mcp.so / smithery) — published MCP listing. Strong
#    behavioural signal, weaker provenance.
#  * homepage — any non-Moltbook https URL. Weakest, just proves the
#    bot's owner has a real online presence.
_CORROBORATION_PRIORITY = {
    "github": 1,
    "gitlab": 1,
    "bitbucket": 1,
    "codeberg": 1,
    "sourcehut": 1,
    "npm": 2,
    "pypi": 2,
    "rubygems": 2,
    "crates": 2,
    "mcp_server": 3,
    "homepage": 4,
}

# URL extractor. Intentionally conservative: requires explicit ``http(s)://``
# scheme so we don't false-match bare ``stripe.com`` mentions. The
# trailing-character class excludes brackets and punctuation that
# typically end a URL in prose.
_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]\}]+", re.IGNORECASE)

# Per-kind host patterns. Order doesn't matter inside the regex; the
# first kind that matches a URL wins (we then take the strongest by
# ``_CORROBORATION_PRIORITY``). Sub-paths must be present (``/owner/repo``
# for GitHub etc.) so that a bare ``github.com`` mention without a
# specific repo doesn't pass.
_KIND_HOST_RE = {
    "github": re.compile(
        r"^https?://(?:www\.)?github\.com/[^/\s]+/[^/\s?#]+",
        re.IGNORECASE,
    ),
    "gitlab": re.compile(
        r"^https?://(?:www\.)?gitlab\.com/[^/\s]+/[^/\s?#]+",
        re.IGNORECASE,
    ),
    "bitbucket": re.compile(
        r"^https?://(?:www\.)?bitbucket\.org/[^/\s]+/[^/\s?#]+",
        re.IGNORECASE,
    ),
    "codeberg": re.compile(
        r"^https?://(?:www\.)?codeberg\.org/[^/\s]+/[^/\s?#]+",
        re.IGNORECASE,
    ),
    "sourcehut": re.compile(
        r"^https?://(?:www\.)?(?:sr\.ht|git\.sr\.ht)/~[^/\s]+/[^/\s?#]+",
        re.IGNORECASE,
    ),
    "npm": re.compile(
        r"^https?://(?:www\.)?npmjs\.com/package/[^/\s?#]+",
        re.IGNORECASE,
    ),
    "pypi": re.compile(
        r"^https?://(?:www\.)?pypi\.org/project/[^/\s?#]+",
        re.IGNORECASE,
    ),
    "rubygems": re.compile(
        r"^https?://(?:www\.)?rubygems\.org/gems/[^/\s?#]+",
        re.IGNORECASE,
    ),
    "crates": re.compile(
        r"^https?://(?:www\.)?crates\.io/crates/[^/\s?#]+",
        re.IGNORECASE,
    ),
    "mcp_server": re.compile(
        r"^https?://(?:www\.)?(?:mcp\.so|smithery\.ai)/[^\s]+",
        re.IGNORECASE,
    ),
}

# Hosts we deliberately ignore even when they appear as full URLs in
# the description. ``moltbook.com`` is the platform itself —
# self-referential links don't corroborate anything. Generic short
# URL hosts get ignored because we can't see what they actually point
# to without following the redirect, which we don't want to do
# inline (would expand the source's network surface and risk
# tracking-pixel side effects).
_HOMEPAGE_HOST_DENY = (
    "moltbook.com",
    "x.com",
    "twitter.com",
    "t.co",
    "bit.ly",
    "tinyurl.com",
    "goo.gl",
    "ow.ly",
    "buff.ly",
    "is.gd",
    "lnkd.in",
    # Code-host root domains without a /owner/repo path are never
    # corroboration on their own — "I link to github.com" is not
    # the same as "I link to my repo". The kind-specific regexes
    # (``_KIND_HOST_RE["github"]`` etc.) only fire when there's an
    # owner/repo path; without that, we'd otherwise mis-classify
    # the bare host as a generic ``homepage`` here. Drop it.
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "codeberg.org",
    "sr.ht",
    "git.sr.ht",
    "npmjs.com",
    "pypi.org",
    "rubygems.org",
    "crates.io",
    "mcp.so",
    "smithery.ai",
)


@dataclass(frozen=True)
class Corroboration:
    """One external artifact reference extracted from a molty's profile.

    ``kind`` is one of the keys in ``_CORROBORATION_PRIORITY``.
    ``url`` is the canonical URL we'll surface to the user. Compared
    by ``priority`` then ``url`` so ``min(corroborations)`` returns
    the strongest deterministically.
    """

    kind: str
    url: str

    @property
    def priority(self) -> int:
        return _CORROBORATION_PRIORITY.get(self.kind, 99)

    def __lt__(self, other: Corroboration) -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.url < other.url


@dataclass(frozen=True)
class MoltbookSource:
    """Pull candidate AI agents from Moltbook's social-network search.

    See module docstring for the high-level discovery model and why
    Moltbook is a recall additive rather than a precision win.
    """

    token: str = ""
    base_url: str = DEFAULT_BASE_URL
    source_id: str = "moltbook"
    search_limit: int = DEFAULT_SEARCH_LIMIT
    max_author_profiles: int = DEFAULT_MAX_AUTHOR_PROFILES
    timeout_seconds: float = 10.0
    goal_hash: str = ""
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        """Two-step fetch (search → profile per author) → normalise.

        Returns ``[]`` immediately on missing token (defensive — the
        dispatcher's ``requires_token`` enforces this too) or empty
        query.
        """

        if not self.token.strip():
            return []
        query = self._build_query(
            task_description=task_description, capabilities=capabilities
        )
        if not query:
            return []

        search_payload = self._fetch_search(query=query)
        if not isinstance(search_payload, dict):
            return []
        results = search_payload.get("results")
        if not isinstance(results, list) or not results:
            return []

        ranked_authors = self._rank_authors(results)
        if not ranked_authors:
            return []

        candidates: list[DiscoveryCandidate] = []
        for author_name in list(ranked_authors)[: self.max_author_profiles]:
            profile = self._fetch_profile(author_name)
            if not isinstance(profile, dict):
                continue
            normalised = self._normalise(
                author_name=author_name,
                profile=profile,
                ranked_post_count=ranked_authors[author_name],
            )
            if normalised is None:
                continue
            if capabilities and not normalised.supports_any(capabilities):
                continue
            candidates.append(normalised)
        return candidates

    # --- Internals -------------------------------------------------

    def _build_query(
        self, *, task_description: str, capabilities: set[str]
    ) -> str:
        text = (task_description or "").strip()
        if text:
            # Moltbook caps queries at 500 chars per the SKILL.md.
            return text[:500]
        if capabilities:
            return sorted(capabilities)[0].replace("_", " ")
        return ""

    @staticmethod
    def _rank_authors(results: list[Any]) -> OrderedDict[str, int]:
        """Return ``{author_name: post_count}`` ordered by descending
        post count. Authors with the same post count keep their first-
        seen order, which mirrors Moltbook's similarity ranking — the
        most-relevant authors float to the top.
        """

        order: OrderedDict[str, int] = OrderedDict()
        for entry in results:
            if not isinstance(entry, dict):
                continue
            author = entry.get("author")
            if not isinstance(author, dict):
                continue
            name = str(author.get("name") or "").strip()
            if not name:
                continue
            order[name] = order.get(name, 0) + 1
        # Sort by count desc; preserve insertion order on ties.
        ordered_pairs = sorted(
            order.items(),
            key=lambda kv: (-kv[1], list(order.keys()).index(kv[0])),
        )
        return OrderedDict(ordered_pairs)

    def _fetch_search(self, *, query: str) -> Any:
        params = parse.urlencode(
            {"q": query, "type": "all", "limit": str(self.search_limit)}
        )
        url = f"{self.base_url}/search?{params}"
        return _fetch_json(
            url, token=self.token, timeout_seconds=self.timeout_seconds
        )

    def _fetch_profile(self, author_name: str) -> Any:
        params = parse.urlencode({"name": author_name})
        url = f"{self.base_url}/agents/profile?{params}"
        return _fetch_json(
            url, token=self.token, timeout_seconds=self.timeout_seconds
        )

    def _normalise(
        self,
        *,
        author_name: str,
        profile: dict[str, Any],
        ranked_post_count: int,
    ) -> DiscoveryCandidate | None:
        # Profile responses can be wrapped in either ``{"agent": {...}}``
        # or be the bare agent object — handle both shapes.
        agent = profile.get("agent") if isinstance(profile.get("agent"), dict) else profile
        if not isinstance(agent, dict):
            return None
        name = str(agent.get("name") or author_name).strip()
        description = str(agent.get("description") or "").strip()
        avatar_url = str(agent.get("avatar_url") or "").strip()
        karma = agent.get("karma")
        is_claimed = bool(agent.get("is_claimed"))
        owner = agent.get("owner") if isinstance(agent.get("owner"), dict) else {}

        # Defense layer 1: same junk pre-filter as MCP-aggregator
        # sources. Catches test/copy moltys ("test-bot-3", "demo-fork")
        # before paying for capability inference.
        if is_obvious_junk(name):
            return None

        if len(description) < MIN_DESCRIPTION_LENGTH:
            # Empty / placeholder descriptions cannot bind to a
            # capability and create noise downstream. Skip rather than
            # spending the embedder cycle.
            return None

        # Defense layer 2: corroboration-or-drop. See module docstring
        # for the "phantom-agent" rationale. We scan the description,
        # the structured ``homepage_url`` / ``links`` fields if
        # present, AND the molty's recent posts/comments (which the
        # /agents/profile response bundles in the same payload — no
        # extra HTTP cost). Live diagnostic 2026-05-14 confirmed real
        # Moltbook bios are Twitter-style vibe statements with no
        # links; technical moltys link to their actual GitHub/npm
        # artifacts in their *posts* instead. Scanning posts is what
        # makes the corroboration filter actually catch the genuine
        # technical moltys vs. drop them along with the phantoms.
        corroborations = _extract_corroborations(
            description=description,
            profile=agent,
            envelope=profile,
        )
        if not corroborations:
            return None
        strongest = min(corroborations)

        # Defense layer 3: capability binding from description text.
        # Run after corroboration so a phantom molty that happens to
        # bind a capability still gets dropped — corroboration is the
        # gate, not a tiebreaker.
        capability_text = " ".join([name, description]).strip()
        capabilities = infer_capabilities_for_source(
            text=capability_text,
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        moltbook_profile_url = (
            f"https://www.moltbook.com/u/{parse.quote(name, safe='')}"
        )

        # Provenance signals from the Moltbook profile. These feed
        # the CandidateJudge LLM prompt (claimed > unclaimed; high
        # karma > low karma; multiple posts on this topic > one) and
        # surface in the frontend so reviewers can see WHY a Moltbook
        # candidate is here. Live in the free-form ``metadata`` bag.
        metadata: dict[str, Any] = {
            "moltbook_is_claimed": is_claimed,
            "moltbook_corroboration_kind": strongest.kind,
            "moltbook_ranked_post_count": ranked_post_count,
        }
        if isinstance(karma, int) and karma != 0:
            metadata["moltbook_karma"] = karma
        if avatar_url:
            metadata["moltbook_avatar_url"] = avatar_url
        if isinstance(owner, dict):
            x_handle = str(owner.get("x_handle") or "").strip()
            if x_handle:
                metadata["moltbook_owner_x_handle"] = x_handle

        raw = {
            "id": f"moltbook:{name}",
            "display_name": name,
            "vendor": "moltbook",
            # Promote the strongest corroboration to vendor_url so the
            # agent-detail page links straight to the real artifact
            # (GitHub repo / npm package / homepage), not to the
            # Moltbook profile. The Moltbook profile is preserved as
            # docs.setup_url below for traceability.
            "vendor_url": strongest.url,
            # Moltbook moltys are AI agents (often LLM-driven, sometimes
            # protocol-bridged). They are NOT MCP servers — calling them
            # mcp_server would be wrong and would fail the AGENTIC_PROVIDER_TYPES
            # check the rest of the system applies. ``ai_agent`` is the
            # correct slot.
            "provider_type": "ai_agent",
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            # Surface the verification posture explicitly. The frontend
            # tag mapper renders this as "Self-listed (community)" so
            # users can never confuse a Moltbook self-listing for a
            # tested provider. See module docstring for rationale.
            "verification_status": COMMUNITY_LISTED_STATUS,
            "docs": {
                # The Moltbook profile is the discovery surface; user
                # who wants to see karma / posts / ownership clicks
                # through here. Distinct from vendor_url which points
                # at the corroborating artifact.
                "setup_url": moltbook_profile_url,
                "auth_method": "",
            },
            # Evidence is the corroborating artifact, not the social
            # profile, because that's what a reviewer needs to inspect
            # to decide whether the claim is real.
            "evidence_url": strongest.url,
            "metadata": metadata,
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


def _fetch_json(url: str, *, token: str, timeout_seconds: float) -> Any:
    """Authenticated JSON fetch.

    Returns ``None`` on any failure (network, auth, parse) so the
    caller can treat all upstream-unhealthy paths uniformly. Same
    posture as the Smithery source — we do not propagate 401/403
    separately because the rest of /goal must keep running on the
    other scouts even when Moltbook is unhappy.
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


def _extract_corroborations(
    *,
    description: str,
    profile: dict[str, Any],
    envelope: dict[str, Any] | None = None,
) -> list[Corroboration]:
    """Return all external artifact references found in the molty.

    Sources scanned (in order, deduped):

    1. The free-text ``description`` (some moltys put their GitHub /
       npm / homepage link in their bio).
    2. The optional structured ``homepage_url`` field — Moltbook is
       iterating their schema and a ``homepage_url`` field has been
       observed on some profiles.
    3. The optional ``links`` array — same justification, defensive
       handling for a future-shape that may or may not ship.
    4. The molty's most recent posts and comments returned in the
       same ``/agents/profile`` envelope under ``recentPosts`` /
       ``recentComments``. This is the load-bearing recall path —
       most real Moltbook moltys post their GitHub repo / project
       page in their threads even when their bio is short. Without
       this, the corroboration filter drops basically every
       authentic technical molty alongside the phantoms.

    Per-post URL scanning honours the same kind/host classifier as
    bio URLs and applies the same deny-list (no Moltbook self-links,
    no shorteners, no X handles). Cap on URLs scanned per envelope
    is intentionally generous (we run regex over a few KB at most).

    De-duplication is by URL (case-insensitive). Returns an empty
    list when no qualifying URL is found, which the caller uses as
    the "drop this candidate" signal.
    """

    candidate_urls: list[str] = list(_URL_RE.findall(description or ""))

    homepage = profile.get("homepage_url") or profile.get("homepage") or ""
    if isinstance(homepage, str) and homepage.strip():
        candidate_urls.extend(_URL_RE.findall(homepage))

    links = profile.get("links")
    if isinstance(links, list):
        for entry in links:
            if isinstance(entry, str):
                candidate_urls.extend(_URL_RE.findall(entry))
            elif isinstance(entry, dict):
                url_value = entry.get("url") or entry.get("href") or ""
                if isinstance(url_value, str) and url_value.strip():
                    candidate_urls.extend(_URL_RE.findall(url_value))

    if isinstance(envelope, dict):
        candidate_urls.extend(
            _scan_post_collection(envelope.get("recentPosts"))
        )
        candidate_urls.extend(
            _scan_post_collection(envelope.get("recentComments"))
        )

    found: list[Corroboration] = []
    seen: set[str] = set()
    for raw_url in candidate_urls:
        cleaned = raw_url.rstrip(".,;:)\u2026]")
        cleaned_key = cleaned.lower()
        if cleaned_key in seen:
            continue
        seen.add(cleaned_key)
        kind = _classify_corroboration_url(cleaned)
        if kind is None:
            continue
        found.append(Corroboration(kind=kind, url=cleaned))
    return found


def _scan_post_collection(items: Any) -> list[str]:
    """Extract URLs from a ``recentPosts``/``recentComments`` array.

    Each entry is a dict with ``title``/``content`` (post) or just
    ``content`` (comment). Both fields can carry HTML markup (Moltbook
    sometimes wraps matched terms in ``<mark>`` tags from search
    highlighting), but our URL regex is anchored on ``http(s)://`` so
    the markup is harmless. ``url`` is also present on link-type posts
    and is treated as a top-level URL string.

    Returns an empty list on any non-list input. Non-dict entries
    inside the list are skipped silently — schema is iterating, the
    parser must not crash.
    """

    if not isinstance(items, list):
        return []
    found: list[str] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        for field_name in ("title", "content", "url"):
            value = entry.get(field_name)
            if isinstance(value, str) and value:
                found.extend(_URL_RE.findall(value))
    return found


def _classify_corroboration_url(url: str) -> str | None:
    """Map a URL to its corroboration kind, or ``None`` if it doesn't
    qualify (Moltbook self-link, generic shortener, malformed, etc.)."""

    for kind, pattern in _KIND_HOST_RE.items():
        if pattern.match(url):
            return kind

    # Fall back to "homepage" iff this is an HTTPS URL whose host is
    # not on the deny-list. We require https because http-only personal
    # sites in 2026 are vanishingly rare and the few that exist tend
    # to be parked / abandoned domains.
    parsed = parse.urlparse(url)
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower().lstrip(".")
    if not host:
        return None
    for denied in _HOMEPAGE_HOST_DENY:
        if host == denied or host.endswith("." + denied):
            return None
    return "homepage"
