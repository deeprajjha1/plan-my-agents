"""Embedder-backed index over the registry's known capabilities.

This module owns *one* job: given the registry's flat list of capability
ids, expose two semantic queries that the rest of the system relies on
without anyone hardcoding English synonym dictionaries:

1. ``match_slug(planner_slug) -> registry_slug | None`` — used by the
   planner to soft-match an open-world capability slug emitted by the LLM
   (e.g. ``local_business_search``) against the closest registry slug
   (e.g. ``semantic_search``). Without this, every paraphrase the LLM
   produces falls into ``missing_capabilities`` and triggers needless
   discovery work; with it, only genuinely-novel capabilities do.

2. ``infer_from_text(free_text) -> set[registry_slug]`` — used by
   discovery sources (HN scout, vendor RSS, APIs.guru, etc.) to derive
   a set of likely-relevant capabilities from arbitrary text without the
   ``CAPABILITY_SYNONYMS`` dictionary the codebase used to maintain.
   That dictionary was the single biggest source of domain bias in the
   discovery pipeline: 11 entries hand-tagged with English keywords for
   travel, payments, and lead-intelligence, which silently steered every
   query toward those three verticals.

Backend selection:
- The default ``DeterministicHashEmbedder`` does *not* provide real
  semantic understanding; with it, ``match_slug`` will only resolve
  paraphrases that happen to share tokens (``email_verify`` ↔
  ``email_verification`` works, ``business_lookup`` ↔
  ``company_data_lookup`` does not). That's intentional — the hash
  backend is the safe default that runs in CI without external deps and
  errs on the side of *not* matching, which preserves the existing
  exact-match behaviour as a baseline.
- Set ``PLANMYAGENTS_EMBEDDING_MODEL`` (e.g. ``nomic-embed-text``) to
  upgrade to a real semantic embedder via Ollama, at which point
  cross-vocabulary matches start working.
- Threshold is configurable via
  ``PLANMYAGENTS_CAPABILITY_MATCH_THRESHOLD`` (default 0.78). Lower
  values increase recall at the cost of precision; higher values
  preserve precision at the cost of more false negatives. We picked 0.78
  because cosine ≥ 0.78 on a normalised dense embedding usually
  corresponds to "obviously the same intent" and is the threshold most
  popular semantic-search libraries default to.

Caching:
- Capability embeddings are computed once per ``(registry, embedder)``
  pair via ``functools.lru_cache``; subsequent matches are O(N) cosine
  comparisons over a small in-memory list (the registry today has 5
  capabilities; even at 500 the dot products take microseconds).
- We do NOT LRU-cache per-call slug embeddings inside this class — the
  hash embedder is instantaneous and the Ollama embedder is rare/opt-in.
  If real-world load proves otherwise, add a ``functools.lru_cache``
  around ``_encode_slug``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from planmyagents_api.discovery.embeddings import (
    Embedder,
    EmbedderError,
    cosine_similarity,
    embedder_from_env,
)

_logger = logging.getLogger("planmyagents_api.discovery.capability_index")


# Default similarity threshold.
#
# 2026-05-14: The default embedder is now the dense semantic
# ``nomic-embed-text`` (via Ollama) — see ``embeddings.py``. The hash
# embedder is still a fallback (no Ollama installed → CI), but since
# nomic and the hash embedder produce score distributions an order of
# magnitude apart, the default here is calibrated for nomic. Operators
# running without Ollama should override
# ``PLANMYAGENTS_CAPABILITY_MATCH_THRESHOLD=0.30`` in their .env.
#
# Score distribution we measured against the 25-capability registry
# using nomic-embed-text on real Moltbook-style bios:
#
#   strong matches (clear single-cap bios):       0.65 - 0.85
#   real but chatty matches (bio + name + filler):0.55 - 0.65
#   soft / partial matches (one keyword overlap): 0.45 - 0.55
#   pure noise (unrelated text):                  0.30 - 0.45
#
# Sample scores (see .env for full table):
#   "browser automation, web scraping" → web_scraping 0.696
#   "verifies a payment...refunds" → payment_authorization 0.592
#   "compose a piano sonata" → max any cap 0.40 (correctly rejected)
#
# 0.55 catches all real matches plus a few soft matches; the LLM
# CandidateJudge filters the soft matches downstream. Was 0.30
# under the hash embedder, which had a much narrower spread.
DEFAULT_MATCH_THRESHOLD = float(
    os.getenv("PLANMYAGENTS_CAPABILITY_MATCH_THRESHOLD", "0.55")
)

# Repo-root-relative path to the canonical registry JSON. Discovery
# sources don't have direct access to the registry (they're stateless
# classifiers); this constant is what lets ``get_default_capability_index``
# reach for it without every source needing a ``registry_path`` argument
# plumbed through. Override via ``PLANMYAGENTS_REGISTRY_PATH`` for tests
# or alternate deployments.
_DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[4]
    / "packages"
    / "registry"
    / "agents.json"
)


class CapabilityIndex:
    """Pre-computed cosine-similarity index over registry capabilities."""

    def __init__(
        self,
        capability_ids: Iterable[str],
        *,
        embedder: Embedder | None = None,
        match_threshold: float | None = None,
    ) -> None:
        # Deduplicate + filter blanks defensively. Order is preserved so
        # the index remains deterministic across processes — this matters
        # because the LLM's planner slug -> registry-slug match should be
        # reproducible from logs.
        seen: list[str] = []
        seen_set: set[str] = set()
        for capability_id in capability_ids:
            slug = (capability_id or "").strip()
            if not slug or slug in seen_set:
                continue
            seen.append(slug)
            seen_set.add(slug)
        self._capability_ids: tuple[str, ...] = tuple(seen)
        self._embedder = embedder or embedder_from_env()
        # Resolve the threshold default inside the body, NOT in the
        # signature, so tests (and any future hot-reload path) can
        # monkeypatch the module-level ``DEFAULT_MATCH_THRESHOLD``
        # constant and have it actually take effect on subsequent
        # CapabilityIndex constructions. With the default captured in
        # the signature, the value would freeze at the value of the
        # constant at function-definition time and patches would
        # silently no-op.
        if match_threshold is None:
            match_threshold = DEFAULT_MATCH_THRESHOLD
        self.match_threshold = float(match_threshold)
        # Pre-compute capability embeddings exactly once. Costs ~5
        # encoder calls per process for today's registry; trivial.
        self._capability_vectors: dict[str, list[float]] = {
            cap_id: self._embedder.encode(_slug_to_text(cap_id))
            for cap_id in self._capability_ids
        }

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return self._capability_ids

    @property
    def embedder_name(self) -> str:
        return self._embedder.name

    def match_slug(
        self,
        planner_slug: str,
        *,
        threshold: float | None = None,
    ) -> str | None:
        """Return the closest registry slug for ``planner_slug``.

        Falls back to ``None`` when the best similarity is below the
        threshold so the caller can route the slug to
        ``missing_capabilities`` (which feeds discovery). Exact matches
        short-circuit without an encoder call.
        """

        slug = (planner_slug or "").strip()
        if not slug or not self._capability_ids:
            return None
        if slug in self._capability_vectors:
            return slug

        # Embedder failure here used to silently raise out of the
        # planner code path. Returning ``None`` instead routes the
        # slug into ``missing_capabilities`` (which triggers
        # discovery) — strictly better than the planner blowing up
        # mid-/goal. Producing a one-line warning so operators can
        # see WHEN this is happening (e.g. Ollama OOM, daemon
        # restart) without losing user-facing service.
        try:
            slug_vec = self._embedder.encode(_slug_to_text(slug))
        except EmbedderError as exc:
            _logger.warning(
                "match_slug: embedder failed for %r (%s); treating as no match",
                slug,
                exc,
            )
            return None
        cutoff = self.match_threshold if threshold is None else float(threshold)
        best_id: str | None = None
        best_sim = 0.0
        for cap_id, cap_vec in self._capability_vectors.items():
            sim = cosine_similarity(slug_vec, cap_vec)
            if sim > best_sim:
                best_sim = sim
                best_id = cap_id

        if best_id is not None and best_sim >= cutoff:
            _logger.debug(
                "match_slug: %r -> %r (sim=%.3f, threshold=%.3f, embedder=%s)",
                slug,
                best_id,
                best_sim,
                cutoff,
                self._embedder.name,
            )
            return best_id

        _logger.debug(
            "match_slug: %r -> None (best=%r sim=%.3f below threshold=%.3f)",
            slug,
            best_id,
            best_sim,
            cutoff,
        )
        return None

    def infer_from_text(
        self,
        text: str,
        *,
        threshold: float | None = None,
    ) -> set[str]:
        """Return registry capability slugs whose embedding sits within
        ``threshold`` cosine similarity of ``text``.

        The semantic of "within threshold" is symmetric to ``match_slug``
        but the typical caller is different: this is used by discovery
        sources to classify a candidate's free-text description (e.g. an
        HN post title or a vendor RSS blurb) into the registry's
        vocabulary.

        Returns an empty set when ``text`` is empty or the registry is
        empty — both legitimate quiet states, not errors.
        """

        cleaned = (text or "").strip()
        if not cleaned or not self._capability_ids:
            return set()

        # Same defence-in-depth as ``match_slug`` above: an embedder
        # failure here used to bubble all the way out to the /goal
        # route. The wrapper in ``infer_capabilities_for_source``
        # catches it for the source-classification path, but direct
        # callers of ``infer_from_text`` (e.g. the API search route)
        # would still 500. Treating an embedder failure as "no
        # inferences" is the same fail-safe the empty-registry path
        # already takes, so the contract stays consistent.
        try:
            text_vec = self._embedder.encode(cleaned)
        except EmbedderError as exc:
            _logger.warning(
                "infer_from_text: embedder failed (%s); returning no inferences",
                exc,
            )
            return set()
        cutoff = self.match_threshold if threshold is None else float(threshold)
        matches: set[str] = set()
        for cap_id, cap_vec in self._capability_vectors.items():
            sim = cosine_similarity(text_vec, cap_vec)
            if sim >= cutoff:
                matches.add(cap_id)
        return matches


def _slug_to_text(slug: str) -> str:
    """Convert ``capability_id_snake_case`` to ``capability id snake case``
    so token-level embedders see actual words rather than glued tokens.
    """

    return slug.replace("_", " ").strip()


@lru_cache(maxsize=4)
def _build_default_index(
    capability_ids: tuple[str, ...],
    embedder_name: str,
) -> CapabilityIndex:
    """Cached factory keyed on (capability tuple, embedder name).

    The embedder name is part of the cache key so a process that
    switches embedders (test code, config reload) doesn't accidentally
    reuse the wrong vectors. We cap maxsize at 4 because in practice the
    registry doesn't change at runtime; the cache exists to avoid
    rebuilding on every call from background workers.
    """

    return CapabilityIndex(capability_ids)


def get_capability_index(
    capability_ids: Iterable[str],
    *,
    embedder: Embedder | None = None,
) -> CapabilityIndex:
    """Return a (cached) ``CapabilityIndex`` for the given capability set.

    Pass ``embedder`` explicitly only in tests; production callers should
    rely on the env-driven default so swapping ``DeterministicHashEmbedder``
    for ``OllamaEmbedder`` is a one-env-var change.
    """

    if embedder is not None:
        # Custom embedder -> bypass the cache (tests want hermetic behaviour).
        return CapabilityIndex(capability_ids, embedder=embedder)
    cap_tuple = tuple(sorted({(c or "").strip() for c in capability_ids if c}))
    embedder_for_key = embedder_from_env()
    return _build_default_index(cap_tuple, embedder_for_key.name)


@lru_cache(maxsize=1)
def _load_default_registry_capabilities() -> tuple[str, ...]:
    """Cached load of the live registry's capability ids.

    Wrapped in ``lru_cache(maxsize=1)`` because (a) the registry is a
    static JSON file we don't want to re-parse on every discovery
    classification call, and (b) when the registry expands at runtime
    (per the product's "expanding agents repository" goal), the next
    process that picks up the new file will rebuild the cache naturally.
    On failure we return an empty tuple rather than raising — discovery
    sources should keep working even if the registry path is misconfigured;
    they'll just produce no inferred capabilities until it's fixed.
    """

    from planmyagents_api.registry.loader import load_registry

    raw_path = os.getenv("PLANMYAGENTS_REGISTRY_PATH")
    path = Path(raw_path) if raw_path else _DEFAULT_REGISTRY_PATH
    try:
        registry = load_registry(path)
    except Exception as exc:  # noqa: BLE001 - any load error is non-fatal here
        _logger.warning(
            "default capability index: registry load failed at %s (%s); "
            "discovery sources will infer no capabilities until this is fixed",
            path,
            exc,
        )
        return ()
    return tuple(str(cap) for cap in registry.get("capabilities", []) if cap)


def get_default_capability_index() -> CapabilityIndex:
    """Return a ``CapabilityIndex`` built over the live registry.

    Designed to be the *single* call discovery sources make when they
    need to classify free text. Replaces the per-source pattern of
    iterating ``CAPABILITY_SYNONYMS`` (a hardcoded English-keyword
    dictionary that biased the system toward finance/travel/lead-intel).

    If the registry is unreachable (filesystem error, malformed JSON),
    the returned index has zero capabilities — sources will infer
    nothing rather than crash. This is the conservative failure mode:
    candidates pass through the rest of the pipeline with whatever
    capabilities the source itself can derive (e.g. from a vendor's own
    OpenAPI tags), which is honest.
    """

    return get_capability_index(_load_default_registry_capabilities())


def infer_capabilities_for_source(
    *,
    text: str,
    extra: dict[str, set[str]] | None = None,
    fallback: str | None = None,
) -> set[str]:
    """Shared classifier for discovery sources.

    Replaces the near-identical 8-line ``_infer_capabilities`` helper
    that lived in ``hacker_news.py``, ``vendor_rss.py``, ``apis_guru.py``,
    ``github_recently_pushed.py``, and ``official_mcp_registry.py`` —
    each of which iterated the deleted ``CAPABILITY_SYNONYMS`` dict.

    Behaviour:

    1. Run ``CapabilityIndex.infer_from_text`` against the live
       registry. With the deterministic embedder the threshold is high
       enough that this only fires on strong matches; with an Ollama
       embedder it's the real domain-agnostic semantic path.
    2. If ``extra`` is supplied, also OR-in any capability whose
       per-source keyword appears as a substring of the lowered text.
       This is the documented escape hatch for source operators who
       want to fine-tune their scout (e.g. an HN-specific synonym set
       for launch-post jargon) without re-introducing global bias. In
       practice every source ships with an empty ``extra``; the hook
       is here so we never need to add a new global dict.
    3. If nothing matches and ``fallback`` is provided, return
       ``{fallback}``. This preserves the per-source catch-all
       semantics (some sources opt in, some don't — see each source
       for the rationale of its choice).
    """

    if not text:
        return set()

    # Embedder failure must NOT crash the calling route. Discovery
    # sources call this from inside the /goal request path, and the
    # embedder is an external process (Ollama daemon / OpenAI HTTP).
    # Any failure mode — daemon restart, OOM, oversized input, network
    # blip — used to bubble up as a 500 from /goal. The right
    # behaviour is to return no inferred capabilities, log a warning,
    # and let the source-level keyword `extra` and `fallback` paths
    # below still work. Discovery degrades gracefully instead of the
    # whole goal route refusing.
    try:
        matched: set[str] = set(
            get_default_capability_index().infer_from_text(text)
        )
    except EmbedderError as exc:
        _logger.warning(
            "infer_capabilities_for_source: embedder failed (%s); "
            "falling back to keyword-only matching for this call",
            exc,
        )
        matched = set()

    if extra:
        lowered = text.lower()
        for capability, keywords in extra.items():
            if not keywords:
                continue
            if any(keyword and keyword in lowered for keyword in keywords):
                matched.add(capability)

    if not matched and fallback:
        matched.add(fallback)

    return matched
