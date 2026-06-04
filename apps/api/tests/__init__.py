"""Test-package init that forces hermetic embedder defaults.

The production ``.env`` checked into the repo now sets
``PLANMYAGENTS_EMBEDDING_MODEL=nomic-embed-text`` so dev runs use the
real semantic embedder. Tests must NOT inherit that — see
``_capability_index_fixtures`` docstring for the hermeticity argument.

This module runs at the moment ``apps/api/tests/...`` is first
imported by ``unittest discover``, which is *before* any test class
constructs a discovery source or calls ``get_default_capability_index``.
We unset the env var (forcing the discovery layer to pick the
deterministic hash backend) and ALSO patch the module-level constant
``DEFAULT_MATCH_THRESHOLD`` directly in case the production module was
already imported by side-effect before this file ran (which would
otherwise leave the constant captured at the production value of 0.55,
too tight for the hash embedder used in tests).

We deliberately don't run ``ollama`` against the production model in
tests — too slow, requires a daemon, version-dependent.
"""

from __future__ import annotations

import os

# Belt-and-braces: also clamp the threshold to the hash-embedder
# default so any test that forgets to construct its own
# ``CapabilityIndex`` still gets predictable behaviour.
os.environ["PLANMYAGENTS_EMBEDDING_MODEL"] = ""
os.environ["PLANMYAGENTS_CAPABILITY_MATCH_THRESHOLD"] = "0.30"

# Disable the post-/goal background discovery refresh during tests.
# The refresh kicks off real network traffic to OfficialMcp registry,
# APIs.guru, GitHub, etc. — all of which are unwanted side-effects in
# a unit test run. Production defaults to ON; tests opt out here so
# they don't have to remember to mock the kick-off site by site.
os.environ.setdefault("PLANMYAGENTS_POST_GOAL_REFRESH", "false")

# Disable the embedder cache wrapper for tests by default. ``embedder_from_env``
# normally wraps every backend in :class:`CachedEmbedder` for production
# latency wins, but most tests assert ``isinstance(result, DeterministicHashEmbedder)``
# (or another concrete backend) and would break if the wrapper interposed.
# Tests that specifically verify cache behaviour construct ``CachedEmbedder``
# explicitly, so this default of 0 is the right hermetic choice.
os.environ.setdefault("PLANMYAGENTS_EMBEDDING_CACHE_SIZE", "0")

# Disable the cost-cap policy for tests by default. The
# WorkflowExecutor reads it for every paid call (Sprint 3a), and
# in production it's wired to a JSONL spend ledger living at
# ``data/spend_events.jsonl``. Touching that ledger from tests
# would (a) pollute the real on-disk file and (b) make tests
# observably depend on prior-run state. Tests that specifically
# verify cap behaviour construct their own CostCapPolicy directly
# — see ``test_cost_cap.py`` and the
# ``CostCapWorkflowIntegrationTests`` suite below.
os.environ.setdefault("PLANMYAGENTS_COST_CAP_ENABLED", "false")

# Disable slug canonicalization in tests by default. In production
# the planner's emitted slug ``payment_processing`` is rewritten to
# the registry's ``payment_authorization`` so scouts and the router
# share the same vocabulary. But many existing tests assume the
# planner's slug flows through verbatim (e.g. tests that assert a
# coined ``email_dispatch`` label gets persisted, or that
# ``payment_processing`` shows up in the gap rollup). Tests that
# specifically verify canonicalization set this env to ``true``
# inside their setUp.
os.environ.setdefault("PLANMYAGENTS_SLUG_CANONICALIZATION", "false")

# Disable Gap-3 pre-plan discovery (re-sequenced /goal flow) in tests.
# The wrapper runs scouts INSIDE the planner if a first-pass plan is
# unsupported, so leaving it enabled would have every refusal-path
# planner test reach out to live MCP / GitHub / APIs.guru sources.
# Tests that specifically verify the pre-plan flow set this env to
# ``true`` inside their setUp.
os.environ.setdefault("PLANMYAGENTS_PRE_PLAN_DISCOVERY", "false")

# Defensive: if any module already imported the capability index, drop
# its caches so the env override above takes effect on next call AND
# patch the module-level threshold constant in place — the constant
# is captured at module-import time from os.getenv() so a late env
# override won't reach it via the env path alone. A bunch of tests
# read DEFAULT_MATCH_THRESHOLD directly (or build a CapabilityIndex
# without a threshold arg, which uses the constant), so this matters.
try:
    from planmyagents_api.discovery import capability_index as _ci

    _ci.DEFAULT_MATCH_THRESHOLD = 0.30
    _ci._build_default_index.cache_clear()
    _ci._load_default_registry_capabilities.cache_clear()
except Exception:  # noqa: BLE001 - best-effort cache reset
    pass

# Same trick for the embedder module: ``_EMBEDDING_CACHE_SIZE`` is
# captured at import time. If embeddings was imported before this
# file ran (e.g. via an integration test that touched the discovery
# package), the constant retains its production default. Force it
# to 0 here so ``embedder_from_env`` skips the cache wrapper for the
# rest of the test session — required for tests that assert on the
# concrete backend type.
try:
    from planmyagents_api.discovery import embeddings as _emb

    _emb._EMBEDDING_CACHE_SIZE = 0
except Exception:  # noqa: BLE001 - best-effort
    pass
