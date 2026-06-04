"""Background discovery-store refresh kicked off after a /goal completes.

Why this exists
---------------

The /goal route only runs *targeted* scouts (per missing capability,
~10s budget per scout). The full ``make discovery-refresh`` pull
(curated MCP catalog, A2A cards, AI-agent directory, OfficialMcp
registry, APIs.guru, vendor RSS, GitHub recently-pushed) takes
30-180s and would never fit inside the user-visible /goal latency.

But the dashboards (``/agents``, ``/categories``, ``/leaderboards``,
``/open-mcp-opportunities``) read from the same persistent store the
upkeep script writes to. If we never re-run upkeep between user
goals, those dashboards drift stale — they'll only show whatever
the last manual ``make discovery-refresh`` indexed plus the slim
slice the live scouts found for whichever capability the planner
happened to mark missing.

So: every successful /goal kicks off the refresh in the
background. The HTTP response returns immediately; the refresh
work happens on a daemon thread.

Design constraints
------------------

* **Never block /goal.** Even if the refresh is broken, the user's
  HTTP response must return on time.
* **Single-flight.** A refresh can take >1 minute; if 10 users
  submit goals in 5 seconds we must NOT spawn 10 refresh threads
  fighting each other for the Postgres store and Ollama daemon.
  A module-level lock + ``in_flight`` flag enforces this.
* **Debounce.** Even after one finishes, we don't re-fire if the
  last completion was less than ``_DEBOUNCE_SECONDS`` ago. This
  caps the refresh rate at ~1/minute regardless of /goal QPS.
* **Opt-out at config.** ``PLANMYAGENTS_POST_GOAL_REFRESH=false``
  disables the kick-off entirely — useful in tests, and as a
  break-glass switch if the refresh starts misbehaving.

Operator visibility
-------------------

Every kick-off emits one INFO log line. Every refresh emits one
INFO at start and one INFO at completion (with elapsed seconds and
candidate counts). Failures emit a single WARNING with the
exception type and message — never a stack trace at WARNING level
because this is best-effort and we don't want to noise up
production logs.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

_LOG = logging.getLogger(__name__)

# How long to wait after one refresh finishes before allowing the
# next kick-off to run. The full upkeep cycle takes 30-180s on the
# target hardware; without a debounce, /goal QPS would drive
# back-to-back refreshes and OOM the embedder. 60s means at most
# one full refresh per minute, which is more than fast enough for
# dashboard freshness.
_DEBOUNCE_SECONDS = float(os.getenv("PLANMYAGENTS_POST_GOAL_REFRESH_DEBOUNCE_SEC", "60"))

# Module-level state. Plain mutex + flag rather than a queue —
# we explicitly DON'T want queued refreshes; we want at most one
# in-flight, plus debounce protection.
_lock = threading.Lock()
_in_flight: bool = False
_last_completed_at: float | None = None


def _enabled() -> bool:
    """``PLANMYAGENTS_POST_GOAL_REFRESH=false`` disables the kick-off.

    Default ON — the user explicitly asked for "wire ``make
    discovery-refresh`` after every goal finishes".
    """

    raw = os.getenv("PLANMYAGENTS_POST_GOAL_REFRESH", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def kick_off_post_goal_refresh(*, store_url: str) -> dict[str, Any]:
    """Fire-and-forget background discovery refresh.

    Returns a small status dict synchronously so the caller can log
    "kicked / debounced / disabled" without having to know about
    the threading internals. The actual refresh runs on a daemon
    thread; the caller does NOT wait for it.

    Status values:
      * ``kicked``     — a refresh thread started.
      * ``in_flight``  — a previous refresh is still running, skipped.
      * ``debounced``  — a refresh finished too recently, skipped.
      * ``disabled``   — env var turned the kick-off off.
    """

    global _in_flight  # noqa: PLW0603 — module-level state by design

    if not _enabled():
        return {"status": "disabled"}

    with _lock:
        if _in_flight:
            return {"status": "in_flight"}
        if _last_completed_at is not None:
            age = time.monotonic() - _last_completed_at
            if age < _DEBOUNCE_SECONDS:
                return {
                    "status": "debounced",
                    "age_seconds": round(age, 1),
                    "debounce_seconds": _DEBOUNCE_SECONDS,
                }
        _in_flight = True

    thread = threading.Thread(
        target=_run_refresh_safely,
        kwargs={"store_url": store_url},
        name="post-goal-discovery-refresh",
        daemon=True,
    )
    thread.start()
    _LOG.info("post-goal discovery refresh kicked off (store=%s)", store_url)
    return {"status": "kicked"}


def _run_refresh_safely(*, store_url: str) -> None:
    """Run the refresh and ALWAYS clear the in-flight flag.

    ``finally`` is critical here: if the refresh raises mid-flight,
    leaving ``_in_flight=True`` would permanently disable
    subsequent kick-offs. The flag must be cleared regardless of
    outcome, then ``_last_completed_at`` is updated whether or not
    the run succeeded — a failed refresh still counts as "we tried
    recently, debounce the next attempt" so we don't busy-loop on
    a broken source.
    """

    global _in_flight, _last_completed_at  # noqa: PLW0603

    started = time.monotonic()
    _LOG.info("post-goal discovery refresh starting (store=%s)", store_url)
    try:
        result = _run_refresh_blocking(store_url=store_url)
        elapsed = time.monotonic() - started
        _LOG.info(
            "post-goal discovery refresh complete in %.1fs: "
            "candidates=%d sources=%d",
            elapsed,
            result.get("total_candidates", 0),
            result.get("source_count", 0),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never propagate
        elapsed = time.monotonic() - started
        _LOG.warning(
            "post-goal discovery refresh failed after %.1fs: %s: %s",
            elapsed,
            type(exc).__name__,
            exc,
        )
    finally:
        with _lock:
            _in_flight = False
            _last_completed_at = time.monotonic()


def _run_refresh_blocking(*, store_url: str) -> dict[str, Any]:
    """Programmatic equivalent of ``make discovery-refresh``.

    Mirrors :mod:`scripts.run_discovery_upkeep` so an in-process
    refresh produces the same persistent store as the operator-
    invoked CLI. We deliberately reuse the same source list and
    the same ``search_candidates(..., persist=True)`` call so
    there's only one source of truth for "what counts as upkeep".

    Curated-file paths are resolved relative to the repo root so
    this works whether the API is run from the repo, from a
    container, or from an installed wheel — the
    ``packages/discovery/sources/`` tree always lives next to
    ``apps/api`` per the monorepo layout.

    After the source pull, a second stage probes reachable MCP
    servers for their ``tools/list`` (Gap 2). The probe stage
    is best-effort: probe failures NEVER prevent the source pull
    from being considered successful, because the source pull is
    what the dashboards depend on.
    """

    # Local imports keep this module cheap to import (e.g. for
    # tests that just want to call ``kick_off_post_goal_refresh``
    # with refresh disabled).
    from pathlib import Path

    from planmyagents_api.discovery.service import (
        _load_curated_rss_feeds,
        search_candidates,
    )
    from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource
    from planmyagents_api.discovery.sources.ai_directory import AiAgentDirectorySource
    from planmyagents_api.discovery.sources.apis_guru import ApisGuruSource
    from planmyagents_api.discovery.sources.github_recently_pushed import (
        GitHubRecentlyPushedSource,
    )
    from planmyagents_api.discovery.sources.hacker_news import (
        HackerNewsAgentWatcherSource,
    )
    from planmyagents_api.discovery.sources.mcp import McpCatalogSource
    from planmyagents_api.discovery.sources.official_mcp_registry import (
        OfficialMcpRegistrySource,
    )
    from planmyagents_api.discovery.sources.static import StaticDiscoverySource
    from planmyagents_api.discovery.sources.vendor_rss import VendorRssSource
    from planmyagents_api.discovery.sources.web_doc import WebDocDiscoverySource

    repo_root = Path(__file__).resolve().parents[4]
    curated = repo_root / "packages" / "discovery" / "sources"

    sources: list[Any] = [
        StaticDiscoverySource(),
        McpCatalogSource([str(curated / "curated_mcp_catalog.json")]),
        A2AAgentCardSource([str(curated / "curated_a2a_cards.json")]),
        AiAgentDirectorySource([str(curated / "curated_ai_agents.json")]),
        WebDocDiscoverySource([str(curated / "curated_web_docs.json")]),
        OfficialMcpRegistrySource(),
        ApisGuruSource(),
        HackerNewsAgentWatcherSource(),
        GitHubRecentlyPushedSource(token=os.getenv("GITHUB_TOKEN", "")),
    ]
    feeds = _load_curated_rss_feeds()
    if feeds:
        sources.append(VendorRssSource(feed_urls=feeds))

    payload = search_candidates(
        capabilities=set(),
        task_description=(
            "post-goal background refresh of local discovery store"
        ),
        sources=sources,
        store_path=store_url,
        limit=200,
        persist=True,
        load_store=False,
        include_stale=True,
        stale_after_days=30,
    )

    probe_summary = _run_mcp_tool_probe_stage(store_url=store_url)

    return {
        "total_candidates": payload.get("total_candidates", 0),
        "source_count": len(sources),
        "mcp_tool_probe": probe_summary,
    }


# Defaults for the MCP tool-probe stage. These are sized so the stage
# fits comfortably inside the 60s debounce window even on cold caches:
#
#   limit=20 candidates × per_probe_timeout=5s / max_workers=8
#   ≈ 12.5s wall-clock worst case
#
# Operators with a thicker MCP fleet can raise the limit; operators
# tuning for tighter latency can drop max_workers.
_PROBE_LIMIT_DEFAULT = 20
_PROBE_TIMEOUT_DEFAULT_SEC = 5.0
_PROBE_MAX_WORKERS_DEFAULT = 8


def _probe_enabled() -> bool:
    """``PLANMYAGENTS_POST_GOAL_PROBE=false`` disables the probe stage.

    Default ON. Disabling is useful in tests that don't want the daemon
    thread reaching out to live MCP servers, and as a break-glass switch
    if a particular MCP fleet has hostile timeout/auth behaviour.
    """

    raw = os.getenv("PLANMYAGENTS_POST_GOAL_PROBE", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _probe_int_env(name: str, default: int) -> int:
    """Parse a positive int env var; fall back to ``default`` on any
    parse error. Negative or zero values are treated as 'use default'
    so an operator can't accidentally disable the probe via a typo."""

    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _probe_float_env(name: str, default: float) -> float:
    """Parse a positive float env var; same fallback semantics as
    :func:`_probe_int_env`."""

    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _run_mcp_tool_probe_stage(*, store_url: str) -> dict[str, Any]:
    """Probe reachable MCP servers for their ``tools/list`` and persist.

    Walks the discovery store for ``provider_type == "mcp_server"`` rows
    that don't yet have a tools list, fans out probes in parallel, and
    saves the enriched candidates back. The save path re-runs inline
    embedding so the new tool data flows into pgvector immediately —
    that's the Gap-1 + Gap-2 closure: tools surface in the index AND
    in the embedding text on the same write.

    Status semantics in the returned dict:

      * ``disabled`` — env switch turned the stage off.
      * ``no_targets`` — no MCP rows missing tools; nothing to do.
      * ``ok`` — probed N candidates, persisted M with new tools.
      * ``error`` — probe stage itself raised. Worker-level failures
        inside the probe are absorbed by the enricher and reported via
        the ``populated_count`` total, never as a stage error.

    The stage NEVER raises out of this function — the caller treats it
    as informational and the rest of the refresh continues.
    """

    if not _probe_enabled():
        return {"status": "disabled"}

    from concurrent.futures import ThreadPoolExecutor

    from planmyagents_api.discovery.enrichers.mcp_tools import McpToolProbeEnricher
    from planmyagents_api.discovery.store import discovery_store_for_path

    limit = _probe_int_env("PLANMYAGENTS_POST_GOAL_PROBE_LIMIT", _PROBE_LIMIT_DEFAULT)
    timeout = _probe_float_env(
        "PLANMYAGENTS_POST_GOAL_PROBE_TIMEOUT_SEC", _PROBE_TIMEOUT_DEFAULT_SEC
    )
    max_workers = _probe_int_env(
        "PLANMYAGENTS_POST_GOAL_PROBE_MAX_WORKERS", _PROBE_MAX_WORKERS_DEFAULT
    )

    started = time.monotonic()
    try:
        store = discovery_store_for_path(store_url)
        all_candidates = store.load()
    except Exception as exc:  # noqa: BLE001 — best-effort, never propagate
        _LOG.warning(
            "post-goal mcp probe: store load failed: %s: %s",
            type(exc).__name__,
            exc,
        )
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    targets = [
        c
        for c in all_candidates
        if c.provider_type == "mcp_server"
        and not c.tools
        # Only probe HTTP-reachable servers. Stdio-only MCP servers
        # have no callable URL so the enricher would skip them anyway,
        # but filtering here keeps the parallelism budget focused on
        # work that can actually succeed.
        and c.vendor_url.strip().startswith(("http://", "https://"))
    ][:limit]

    if not targets:
        return {
            "status": "no_targets",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    enricher = McpToolProbeEnricher(
        timeout_seconds=timeout,
        skip_when_already_populated=True,
    )

    # Fan out probes in parallel. We call enricher.enrich on a SINGLE-
    # candidate list per worker rather than one big enrich(targets)
    # call, because the enricher iterates sequentially internally.
    # Per-worker concurrency comes from the ThreadPoolExecutor here.
    enriched_by_id: dict[str, Any] = {}
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_id = {
                executor.submit(enricher.enrich, [target]): target.id
                for target in targets
            }
            for future in future_to_id:
                target_id = future_to_id[future]
                try:
                    enriched = future.result()
                except Exception as exc:  # noqa: BLE001 — per-worker isolation
                    _LOG.info(
                        "post-goal mcp probe: %s: %s",
                        type(exc).__name__,
                        exc,
                    )
                    continue
                if enriched:
                    enriched_by_id[target_id] = enriched[0]
    except Exception as exc:  # noqa: BLE001 — executor failure
        _LOG.warning(
            "post-goal mcp probe: executor failed: %s: %s",
            type(exc).__name__,
            exc,
        )
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    # Persist only the candidates whose tool list actually grew. Save
    # via save_merge so every other row in the store survives, and so
    # the inline embedding step picks up the new tool text (Gap 1).
    populated = [
        candidate
        for candidate in enriched_by_id.values()
        if candidate.tools
    ]
    persisted_count = 0
    if populated:
        try:
            store.save_merge(populated)
            persisted_count = len(populated)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "post-goal mcp probe: save_merge failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return {
                "status": "error",
                "targets": len(targets),
                "populated": len(populated),
                "persisted": 0,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }

    elapsed_ms = int((time.monotonic() - started) * 1000)
    _LOG.info(
        "post-goal mcp probe complete in %dms: targets=%d populated=%d persisted=%d",
        elapsed_ms,
        len(targets),
        len(populated),
        persisted_count,
    )
    return {
        "status": "ok",
        "targets": len(targets),
        "populated": len(populated),
        "persisted": persisted_count,
        "elapsed_ms": elapsed_ms,
    }


def reset_for_tests() -> None:
    """Clear module-level state. Tests-only; not part of the public API.

    Without this, the in-flight flag from one test could leak into
    the next and silently mask kick-off behaviour. Production code
    has no reason to call it.
    """

    global _in_flight, _last_completed_at  # noqa: PLW0603
    with _lock:
        _in_flight = False
        _last_completed_at = None
