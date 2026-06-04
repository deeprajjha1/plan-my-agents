"""GitHub ``awesome-*`` lists discovery source.

The ``awesome-mcp-servers``, ``awesome-ai-agents``, ``awesome-llm-
agents`` and sibling community-curated lists on GitHub catalogue
hundreds of MCP servers / A2A agents / AI-agent frameworks that don't
always appear in the formal registries (Smithery, Glama, MCP
Marketplace, official MCP registry). Each list maintainer effectively
runs their own taste-curated discovery loop; pulling from a half-
dozen of them gives us a fast-moving signal of "what the community
considers worth surfacing" without us having to be that taste-maker
ourselves.

The lists are markdown-formatted READMEs in public GitHub repos, so
this source:

1. Fetches each README via GitHub's REST API with
   ``Accept: application/vnd.github.v3.raw`` (plain text, no base64
   wrapping).
2. Parses ``- [Name](url) - description`` style link rows out of the
   markdown. The awesome-list community uses this format consistently
   enough that a regex captures most entries with minimal false
   positives.
3. Filters extracted entries against the same capability-inference
   pipeline every other source uses; entries with no capability match
   are dropped.

This source ``requires_token="GITHUB_TOKEN"`` because the GitHub REST
API quickly hits 60 req/hr unauthenticated and the dispatcher would
silently degrade to "0 candidates" in production. With a token the
ceiling is 5,000 req/hr — far above what this scout needs (one
request per curated awesome-list repo per /goal).

Why this is a separate source from ``github_code_search``:

* Different signal: code search finds repos that *contain* the
  literal text "model context protocol"; awesome-lists find repos
  that a human has *judged* worth listing. Independent recall
  populations.
* Different latency budget: a single README parse is one round-trip
  per list; code search is one round-trip per capability query.
* Different normalisation: awesome-list entries usually include
  description text in the link row itself; code-search entries don't
  and need a follow-up fetch.
"""

from __future__ import annotations

import json
import re
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

DEFAULT_README_URL_TEMPLATE = (
    "https://api.github.com/repos/{owner}/{repo}/contents/README.md"
)

USER_AGENT = (
    "planmyagents-discovery/0.1 "
    "(+https://planmyagents.com; first-party Tier-1 source)"
)

# Curated set of awesome-* lists. Each entry is (owner, repo,
# provider_type_hint). The hint informs the default ``provider_type``
# assigned to extracted candidates and is overridden when the entry
# text itself implies a different type.
#
# Sources picked for: (a) high curation quality, (b) recency of
# updates, (c) distinct populations (i.e. each list catalogues servers
# the others don't). All are public and Apache/MIT licensed.
DEFAULT_AWESOME_LISTS: tuple[tuple[str, str, str], ...] = (
    ("punkpeye", "awesome-mcp-servers", "mcp_server"),
    ("appcypher", "awesome-mcp-servers", "mcp_server"),
    ("wong2", "awesome-mcp-servers", "mcp_server"),
    ("e2b-dev", "awesome-ai-agents", "ai_agent"),
    ("kaushikb11", "awesome-llm-agents", "ai_agent"),
    ("francedot", "acu", "ai_agent"),
)

# Markdown link-row pattern.
#
# Match shapes the awesome-lists actually use, in order of precedence:
#
#   - [Name](https://example.com) - description
#   * [Name](https://example.com) - description
#   - [Name](https://example.com) -- description    (some maintainers)
#   - [Name](https://example.com) — description     (em-dash; less common)
#   - [Name](https://example.com)                   (no description; we still
#                                                    accept and try to infer
#                                                    capability from name)
#
# The ``re.MULTILINE`` flag is set so ``^`` matches at line starts; we
# anchor on ``-`` or ``*`` followed by exactly one space to avoid
# capturing nested sub-bullets that are usually sub-features rather
# than separately-discoverable agents.
LINK_ROW_PATTERN = re.compile(
    r"""
    ^[-*]\s+                # bullet
    \[([^\]]+)\]            # [Name]   -> group 1
    \(\s*(https?://[^\s)]+)\s*\)   # (url) -> group 2
    (?:\s*[-—]+\s*(.+?))?   # optional " - description" -> group 3
    \s*$
    """,
    re.VERBOSE | re.MULTILINE,
)

# Owner/repo extractor for GitHub URLs only — anything else falls
# back to "use the host as vendor".
GITHUB_REPO_PATTERN = re.compile(
    r"^https?://github\.com/([^/]+)/([^/?#\s]+)/?"
)


@dataclass(frozen=True)
class GitHubAwesomeListsSource:
    """Pull discovery candidates from curated awesome-* GitHub lists.

    Defense in depth (mirrors the other MCP-aggregator sources):

    1. ``is_obvious_junk`` regex pre-filter on the extracted name
       drops test/copy/fork entries. The awesome-list maintainers do
       their own quality curation, but their bar is "interesting"
       not "production-grade" — entries like ``mcp-test-server`` do
       slip through and the shared filter is cheap.
    2. URL allow-list: we only retain entries with valid HTTPS URLs.
       Plain ``http://`` and ``mailto:`` rows are dropped because
       they're never agent-shaped.
    3. ``infer_capabilities_for_source`` substring/embedding match on
       ``name + description`` — at least one capability required.
    4. Request-time ``CandidateJudge`` (LLM) is the load-bearing
       relevance check at retrieval time.

    Repo-extraction precedence:

    * GitHub URLs -> ``{owner}/{repo}`` becomes the candidate ID and
      ``owner`` becomes the vendor.
    * Non-GitHub URLs -> use the URL's host as vendor and a slug
      derived from name + host as ID.

    ``requires_token="GITHUB_TOKEN"`` because the README endpoint is
    rate-limited heavily without auth. The dispatcher silently skips
    this source when the token is absent — same behaviour as
    ``GitHubCodeSearchSource`` and ``GitHubRecentlyPushedSource``.
    """

    token: str = ""
    source_id: str = "github_awesome_lists"
    lists: tuple[tuple[str, str, str], ...] = DEFAULT_AWESOME_LISTS
    readme_url_template: str = DEFAULT_README_URL_TEMPLATE
    # Cap per-list parsing at 60 entries. The bottleneck on the
    # dispatcher's per-scout budget is the embedder call inside
    # ``infer_capabilities_for_source`` — empirically ~100 ms per
    # entry with ``nomic-embed-text`` via Ollama. 6 lists × 60 entries
    # × 100 ms ≈ 36 s in the worst case (we trim early when a list
    # has fewer relevant rows), comfortably under the dispatcher's
    # 28 s global cap once entries are processed in parallel across
    # other scouts. Operators on a faster embedder (e.g., remote
    # OpenAI) can override this in the scout factory.
    # max_entries_per_list tuned 2026-05-20 against a dispatcher
    # budget of 12 s (see ``scouts.py``). Earlier value (60) made
    # this scout the request-time bottleneck: 6 lists × 60 entries
    # × ~100 ms per-entry embedder call = up to 36 s of inference
    # alone. 25 entries/list × 6 lists × 100 ms = 15 s worst case,
    # which fits under the dispatcher's 12 s budget once HTTP work
    # parallelises with the rest of the fleet. Curators with
    # legitimately deeper lists get picked up over multiple cron
    # runs (the cron pass overrides this via constructor args to
    # scan the full list).
    max_entries_per_list: int = 25
    max_total_candidates: int = 150
    timeout_seconds: float = 6.0
    goal_hash: str = ""
    extra_capability_synonyms: dict[str, set[str]] = field(default_factory=dict)

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]:
        """Fetch each curated README + extract candidates."""

        if not self.token.strip():
            return []

        candidates: list[DiscoveryCandidate] = []
        seen_ids: set[str] = set()
        for owner, repo, provider_type_hint in self.lists:
            if len(candidates) >= self.max_total_candidates:
                break
            url = self.readme_url_template.format(owner=owner, repo=repo)
            markdown = _fetch_readme(
                url, token=self.token, timeout_seconds=self.timeout_seconds
            )
            if not markdown:
                continue
            rows = LINK_ROW_PATTERN.findall(markdown)[: self.max_entries_per_list]
            for name, link, description in rows:
                if len(candidates) >= self.max_total_candidates:
                    break
                normalised = self._normalise(
                    name=name.strip(),
                    link=link.strip(),
                    description=(description or "").strip(),
                    list_owner=owner,
                    list_repo=repo,
                    provider_type_hint=provider_type_hint,
                )
                if normalised is None:
                    continue
                if normalised.id in seen_ids:
                    continue
                if capabilities and not normalised.supports_any(capabilities):
                    continue
                seen_ids.add(normalised.id)
                candidates.append(normalised)
        return candidates

    def _normalise(
        self,
        *,
        name: str,
        link: str,
        description: str,
        list_owner: str,
        list_repo: str,
        provider_type_hint: str,
    ) -> DiscoveryCandidate | None:
        if not name or not link:
            return None
        if not link.startswith("https://"):
            return None
        if is_obvious_junk(name):
            return None

        github_match = GITHUB_REPO_PATTERN.match(link)
        if github_match:
            owner, repo = github_match.group(1), github_match.group(2)
            candidate_id = f"{owner}/{repo}"
            vendor = owner
            vendor_url = f"https://github.com/{owner}/{repo}"
        else:
            try:
                host = parse.urlparse(link).netloc.lower()
            except ValueError:
                return None
            if not host:
                return None
            vendor = host.split(":", 1)[0]
            candidate_id = f"{vendor}/{_slugify(name)}"
            vendor_url = link

        capability_text = " ".join([name, description]).strip()
        capabilities = infer_capabilities_for_source(
            text=capability_text,
            extra=self.extra_capability_synonyms,
        )
        if not capabilities:
            return None

        # Provenance: which awesome-list referenced this candidate.
        # Surface BOTH the upstream list and the list maintainer's
        # GitHub URL so the UI can credit them (and so we have a
        # paper trail if a vendor disputes the listing).
        metadata: dict[str, Any] = {
            "awesome_list_owner": list_owner,
            "awesome_list_repo": list_repo,
            "awesome_list_url": f"https://github.com/{list_owner}/{list_repo}",
        }

        raw_candidate = {
            "id": candidate_id,
            "display_name": name,
            "vendor": vendor,
            "vendor_url": vendor_url,
            "provider_type": provider_type_hint,
            # Verification: a curated awesome-list mention is meaningful
            # signal ("a human chose to feature this") but it's not
            # vendor-signed and not capability-probed. Keep at
            # ``registered_in_directory`` — same default as Smithery /
            # MCP Marketplace / Glama.
            "verification_status": "registered_in_directory",
            "capabilities": [{"id": cap} for cap in sorted(capabilities)],
            "docs": {
                "setup_url": vendor_url,
                "auth_method": "",
            },
            "evidence_url": vendor_url,
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


def _slugify(text: str) -> str:
    """Lower-case + alphanumeric-only slug, for synthesising candidate
    IDs when the URL isn't a GitHub repo."""

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "entry"


def _fetch_readme(
    url: str, *, token: str, timeout_seconds: float
) -> str:
    """Fetch a README as raw markdown.

    Uses ``Accept: application/vnd.github.v3.raw`` so the response is
    plain markdown text rather than the default base64-encoded JSON
    wrapper. Returns empty string on any failure — the scout records
    that as "0 candidates from this list" rather than aborting the
    /goal request.
    """

    headers = {
        "Accept": "application/vnd.github.v3.raw",
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            body = response.read()
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return ""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")
