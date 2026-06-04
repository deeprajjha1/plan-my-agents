"""Shared test fixtures for source tests that need a richer capability set.

The live registry (``packages/registry/agents.json``) ships with 25
capability ids today (expanded from 5 in Sprint 2). Source tests, on
the other hand, often use realistic vendor descriptions (Stripe
payments, inference.sh AI apps, HN announcements about hospitality
APIs) whose capabilities may still sit *outside* the shipped registry
when test fixtures pre-date a registry expansion. Under the
embedding-based classifier those candidates correctly fail to
classify, which makes it impossible to test source mechanics like
dedup / version selection / vendor extraction with realistic vendor
fixtures.

This helper provides a ``with extended_capability_index([...]):``
context manager that patches the discovery sources' capability index to
include extra slugs for the duration of the test. Use this in any
source test whose fixtures mention capabilities not in the live
registry. Keeps the production code free of test-specific knobs.

Embedder choice
---------------
Test fixtures HARD-CODE the ``DeterministicHashEmbedder`` regardless
of ``PLANMYAGENTS_EMBEDDING_MODEL`` env. Three reasons:

1. **Hermeticity** — production now defaults to ``OllamaEmbedder``
   (nomic-embed-text), which requires Ollama to be running locally
   and pulled. CI must not depend on Ollama.
2. **Determinism** — ollama can return slightly different vectors
   across model versions / hardware. Test fixture descriptions were
   calibrated against the deterministic hash embedder's exact
   token-bucket behaviour and would flake on a real semantic
   embedder.
3. **Threshold isolation** — the production threshold (0.55) is
   tuned for nomic; the hash embedder uses 0.30. We force both the
   embedder *and* the threshold here so tests are independent of
   whatever the operator put in ``.env``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from planmyagents_api.discovery import capability_index as ci_module
from planmyagents_api.discovery.capability_index import CapabilityIndex
from planmyagents_api.discovery.embeddings import DeterministicHashEmbedder

# Hash-embedder threshold — see module docstring for why we pin this
# rather than read from PLANMYAGENTS_CAPABILITY_MATCH_THRESHOLD.
_HASH_EMBEDDER_TEST_THRESHOLD = 0.30


@contextmanager
def extended_capability_index(extra_capability_ids: list[str]) -> Iterator[CapabilityIndex]:
    """Patch ``get_default_capability_index`` to include extra slugs.

    Source modules call the function indirectly via
    ``infer_capabilities_for_source`` (also defined in
    ``capability_index``), which means a single patch on the
    module-level function name is enough — there's no per-source bound
    copy to patch.

    Yields the augmented index so tests can also call ``match_slug`` /
    ``infer_from_text`` against it directly when useful.
    """

    base_caps = ci_module._load_default_registry_capabilities()
    full_caps = list(dict.fromkeys([*base_caps, *extra_capability_ids]))
    augmented = CapabilityIndex(
        full_caps,
        embedder=DeterministicHashEmbedder(),
        match_threshold=_HASH_EMBEDDER_TEST_THRESHOLD,
    )

    with patch.object(
        ci_module, "get_default_capability_index", lambda: augmented
    ):
        yield augmented
