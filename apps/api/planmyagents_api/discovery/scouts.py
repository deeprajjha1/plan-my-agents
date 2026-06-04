"""Request-time scout dispatcher for live agent discovery.

A `Scout` is a lightweight wrapper around a `DiscoverySource` (or any
callable that returns `DiscoveryCandidate` records) annotated with
metadata the dispatcher needs: a stable id, a per-scout time budget, and
optional configuration for query rewriting.

The `ScoutDispatcher` runs N scouts in parallel for a single sub-task,
collects their results, dedupes against the existing index, and returns
a `DispatchResult` that carries:

* the new candidates found,
* per-scout latency / outcome / error metadata for observability, and
* a summary that downstream callers (the planner, the API response
  shaping, the frontend) can render.

Design choices:

* Threads, not asyncio. Every existing `DiscoverySource.search` is
  synchronous and HTTP-bound. Bridging via asyncio would just shove the
  call into an executor anyway. ThreadPoolExecutor + per-future
  `Future.result(timeout=...)` matches the workload directly and keeps
  the abstraction debuggable.
* Time budget is enforced both per-scout (cancel one slow scout) and
  globally (fail-fast cap that protects /goal latency).
* The dispatcher never raises on a single scout failure; failures are
  recorded in `ScoutResult.error` so the API can show the user "GitHub
  scout timed out — 4 of 5 scouts returned."
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass, field
from typing import Any, Protocol

from planmyagents_api.discovery.dedupe import dedupe_key
from planmyagents_api.discovery.models import DiscoveryCandidate

_logger = logging.getLogger("planmyagents_api.discovery.scouts")

ScoutSearchFn = Callable[[set[str], str], list[DiscoveryCandidate]]


class _SearchableSource(Protocol):
    """Anything with the existing DiscoverySource.search() signature."""

    def search(
        self, *, capabilities: set[str], task_description: str
    ) -> list[DiscoveryCandidate]: ...


@dataclass(frozen=True)
class Scout:
    """A discoverability probe the dispatcher runs in parallel.

    Args:
        scout_id: Stable identifier ("github_code_search", "hn",
            "official_mcp_registry"). Used to key per-scout metadata in
            results and to attribute candidate provenance.
        source: The underlying source to query. Must expose
            `search(capabilities=..., task_description=...)`.
        budget_seconds: Hard upper bound on time the dispatcher will
            wait for this scout. Slow scouts are cancelled, not waited
            on. Default 6s — chosen because /goal end-to-end already
            spends ~2-3s on planner + lookup, and we don't want live
            discovery to push p99 past 15s.
        is_live_only: If True, this scout only runs when the sub-task
            has zero matches in the existing index (the "unmet" path).
            If False, this scout always runs (e.g., for high-value sub-
            tasks where we want freshness even when matches exist).
        requires_token: Optional env-var name. If the env var is unset
            the dispatcher silently skips this scout. Used for
            github_code_search / github_recently_pushed which no-op
            without a token.
    """

    scout_id: str
    source: _SearchableSource
    budget_seconds: float = 6.0
    is_live_only: bool = True
    requires_token: str | None = None


@dataclass
class ScoutResult:
    """Outcome of a single scout for a single sub-task."""

    scout_id: str
    candidates: list[DiscoveryCandidate] = field(default_factory=list)
    elapsed_ms: int = 0
    status: str = "ok"  # one of: ok, timeout, error, skipped
    skipped_reason: str | None = None
    error: str | None = None

    def to_summary(self) -> dict:
        """Compact dict for inclusion in /goal API responses."""
        return {
            "scout_id": self.scout_id,
            "status": self.status,
            "candidate_count": len(self.candidates),
            "elapsed_ms": self.elapsed_ms,
            "skipped_reason": self.skipped_reason,
            "error": self.error,
        }


@dataclass
class DispatchResult:
    """Aggregate result from one dispatch (one sub-task, N scouts).

    `merged_candidates` are the dedupe-merged candidates across all
    scouts. `scout_results` carries per-scout metadata even for scouts
    that returned zero — that's important for transparency in the UI.
    """

    sub_task_id: str | None
    capability: str | None
    merged_candidates: list[DiscoveryCandidate] = field(default_factory=list)
    scout_results: list[ScoutResult] = field(default_factory=list)
    total_elapsed_ms: int = 0

    def to_summary(self) -> dict:
        return {
            "sub_task_id": self.sub_task_id,
            "capability": self.capability,
            "total_elapsed_ms": self.total_elapsed_ms,
            "merged_candidate_count": len(self.merged_candidates),
            "scouts": [r.to_summary() for r in self.scout_results],
        }


class ScoutDispatcher:
    """Runs scouts in parallel for a single discovery probe.

    One dispatcher instance is reusable across many requests. The
    underlying ThreadPoolExecutor is bounded; `max_workers` should be
    larger than the typical scout count to allow scouts within one
    dispatch + concurrent dispatches across requests.
    """

    def __init__(
        self,
        scouts: list[Scout],
        *,
        max_workers: int = 16,
        # Global budget is the *ceiling* on dispatch wall time. Per-scout
        # budgets are what we actually want operators to tune; the
        # global cap exists only to prevent a single misconfigured
        # scout (e.g. budget=120s) from blowing /goal latency. Bumped
        # 10s → 18s → 28s as the per-scout caps below grew to
        # 22-24s for the slow third-party APIs (apis_guru,
        # official_mcp_registry, hacker_news, github_recently_pushed)
        # — without raising the global cap to clear them, the
        # ``min(scout.budget_seconds, global_budget_seconds)`` formula
        # would silently re-clip the per-scout budgets back to 28s
        # anyway. 28s is the smallest value where every per-scout
        # cap below applies as written, with ~4s of slack for
        # dispatcher overhead and the merge/dedupe step.
        global_budget_seconds: float = 28.0,
        run_logger: Any = None,
    ) -> None:
        """Args:
            scouts: The scout fleet to run on each dispatch.
            max_workers: ThreadPoolExecutor size. Tune > scout count to
                allow concurrent dispatches across requests.
            global_budget_seconds: Hard cap on dispatch wall time;
                whichever-is-smaller of this and per-scout budget wins.
            run_logger: Optional `DiscoveryRunLogger`. If `None` we
                resolve a default at dispatch time. Each scout invocation
                produces one `discovery_run_event` row tagged with
                trigger='scout' so the operator dashboard can compare
                scout-mode vs batch-mode source health.
        """

        self._scouts = scouts
        self._max_workers = max_workers
        self._global_budget_seconds = global_budget_seconds
        self._run_logger = run_logger

    def dispatch(
        self,
        *,
        capability: str | None,
        task_description: str,
        token_resolver: Callable[[str], str | None] | None = None,
        sub_task_id: str | None = None,
        per_scout_query_overrides: dict[str, str] | None = None,
    ) -> DispatchResult:
        """Run all configured scouts in parallel for this sub-task.

        Args:
            capability: The single capability id the planner says is
                missing. Pass `None` if the dispatcher is being used for
                a free-form query.
            task_description: Free-text description of the sub-task. Each
                scout's own `task_description` parameter receives this
                (or its override from `per_scout_query_overrides`).
            token_resolver: Function that returns the value of an env
                var. Defaults to `os.getenv`. Injectable for tests.
            sub_task_id: Optional planner sub-task id, recorded in the
                `DispatchResult` for traceability.
            per_scout_query_overrides: Map scout_id → custom
                task_description for that scout (used by LLM query
                expansion to produce source-specific query phrasings).
        """

        import os

        from planmyagents_api.discovery.run_log import DiscoveryRunEvent, DiscoveryRunLogger

        token_resolver = token_resolver or os.getenv
        capability_set: set[str] = {capability} if capability else set()
        per_scout_query_overrides = per_scout_query_overrides or {}
        run_logger = self._run_logger or DiscoveryRunLogger.default()
        searched_caps = sorted(capability_set)

        wall_start = time.monotonic()
        # Operator-level breadcrumb so a /goal request showing 14
        # capabilities × 6 scouts isn't a black box. Logged once per
        # dispatch (i.e. once per sub-task / capability) so the volume
        # stays manageable even on plans with many sub-tasks.
        _logger.info(
            "scout dispatch starting: scouts=%d capability=%s task=%r",
            len(self._scouts),
            capability or "—",
            (task_description or "")[:60],
        )
        results: list[ScoutResult] = []
        # We deliberately *don't* use `with ThreadPoolExecutor(...)` because
        # its __exit__ defaults to `shutdown(wait=True)` — that would block
        # on slow scouts the dispatcher has already cancelled, defeating
        # the timeout. We shutdown with `wait=False` after collecting
        # results; orphaned threads finish in the background and exit.
        executor = ThreadPoolExecutor(max_workers=self._max_workers)
        try:
            # List of (scout, future) so we don't require Scout to be
            # hashable — `Scout.source` is arbitrary user-supplied
            # (often unhashable, e.g., a dataclass with a list field).
            scout_to_future: list[tuple[Scout, Future]] = []
            for scout in self._scouts:
                if scout.requires_token and not token_resolver(scout.requires_token):
                    results.append(
                        ScoutResult(
                            scout_id=scout.scout_id,
                            status="skipped",
                            skipped_reason=(
                                f"missing token: {scout.requires_token}"
                            ),
                        )
                    )
                    run_logger.append_event(
                        DiscoveryRunEvent(
                            source_id=scout.scout_id,
                            source_type=scout.source.__class__.__name__,
                            status="skipped",
                            error=f"missing token: {scout.requires_token}",
                            elapsed_ms=0,
                            query=task_description,
                            searched_capabilities=tuple(searched_caps),
                            trigger="scout",
                        )
                    )
                    _logger.info(
                        "scout=%s status=skipped reason=missing_token:%s",
                        scout.scout_id,
                        scout.requires_token,
                    )
                    continue
                description = per_scout_query_overrides.get(
                    scout.scout_id, task_description
                )
                future = executor.submit(
                    _safe_scout_search,
                    scout.source,
                    capability_set,
                    description,
                )
                scout_to_future.append((scout, future))

            # Reap completed futures in *completion* order, not submission
            # order. The previous implementation iterated
            # ``scout_to_future`` sequentially and computed each scout's
            # effective budget as ``global_budget - elapsed_so_far``.
            # That meant the first slow scout consumed everyone's budget:
            # a real /goal run was observed where ``official_mcp_registry``
            # took 7.9s of a 10s global budget, leaving ``vendor_rss``
            # with 2.1s and ``github_recently_pushed`` with 0.0s — both
            # were marked timeout despite executor threads being free
            # and (in github's case) the scout being killed before it
            # could even start its first I/O.
            #
            # The fix is two-pronged:
            #
            # 1. Each scout gets its OWN deadline at submission time
            #    (``min(scout.budget_seconds, global_budget_seconds)``),
            #    independent of how long other scouts take. This is the
            #    operator-meaningful contract: "vendor_rss has up to 6s
            #    for its own work" — not "vendor_rss has 6s minus
            #    whatever official_mcp_registry happened to use".
            # 2. We loop on ``concurrent.futures.wait(FIRST_COMPLETED)``
            #    so we process each future the moment it finishes, in
            #    completion order. A slow scout can no longer block the
            #    main loop from collecting results from fast scouts.
            #
            # The global budget is still a hard ceiling: when the global
            # deadline elapses, any still-running future is marked
            # ``timeout`` and we bail. Scouts that already completed
            # before the global deadline keep their results regardless
            # of how late others ran.
            future_to_scout: dict[Future, Scout] = {
                future: scout for scout, future in scout_to_future
            }
            global_deadline = wall_start + self._global_budget_seconds
            scout_deadlines: dict[Future, float] = {
                future: wall_start
                + min(scout.budget_seconds, self._global_budget_seconds)
                for scout, future in scout_to_future
            }
            pending: set[Future] = set(future_to_scout.keys())

            while pending:
                now = time.monotonic()
                # Earliest deadline among pending = how long we wait
                # before re-checking. Wake at least at the global
                # deadline so we don't sleep past it.
                next_deadline = min(
                    [scout_deadlines[f] for f in pending] + [global_deadline]
                )
                wait_for = max(0.0, next_deadline - now)
                done, _still_pending = wait(
                    pending, timeout=wait_for, return_when=FIRST_COMPLETED
                )

                # Process anything that completed inside this slice.
                for future in done:
                    scout = future_to_scout[future]
                    elapsed_ms = int((time.monotonic() - wall_start) * 1000)
                    source_type = scout.source.__class__.__name__
                    candidates, error = future.result()
                    if error is not None:
                        results.append(
                            ScoutResult(
                                scout_id=scout.scout_id,
                                elapsed_ms=elapsed_ms,
                                status="error",
                                error=error,
                            )
                        )
                        run_logger.append_event(
                            DiscoveryRunEvent(
                                source_id=scout.scout_id,
                                source_type=source_type,
                                status="error",
                                error=error,
                                elapsed_ms=elapsed_ms,
                                query=task_description,
                                searched_capabilities=tuple(searched_caps),
                                trigger="scout",
                            )
                        )
                        _logger.warning(
                            "scout=%s status=error elapsed=%dms error=%s",
                            scout.scout_id,
                            elapsed_ms,
                            error,
                        )
                    else:
                        results.append(
                            ScoutResult(
                                scout_id=scout.scout_id,
                                candidates=candidates,
                                elapsed_ms=elapsed_ms,
                                status="ok",
                            )
                        )
                        run_logger.append_event(
                            DiscoveryRunEvent(
                                source_id=scout.scout_id,
                                source_type=source_type,
                                status="ok",
                                candidates_returned=len(candidates),
                                elapsed_ms=elapsed_ms,
                                query=task_description,
                                searched_capabilities=tuple(searched_caps),
                                trigger="scout",
                            )
                        )
                        _logger.info(
                            "scout=%s status=ok elapsed=%dms candidates=%d",
                            scout.scout_id,
                            elapsed_ms,
                            len(candidates),
                        )
                    pending.discard(future)

                # Reap any scout that hit its per-scout deadline OR the
                # global deadline. Both checks happen here — we don't
                # need to wait for ``wait`` to return again because we
                # already know which futures are over budget right now.
                now = time.monotonic()
                expired: list[Future] = []
                for future in list(pending):
                    scout = future_to_scout[future]
                    if (
                        now >= scout_deadlines[future]
                        or now >= global_deadline
                    ):
                        expired.append(future)
                for future in expired:
                    scout = future_to_scout[future]
                    elapsed_ms = int((time.monotonic() - wall_start) * 1000)
                    source_type = scout.source.__class__.__name__
                    # Best-effort cancel — Python can't actually kill a
                    # running thread, so the I/O may keep going in the
                    # background. The dispatcher is done waiting on it
                    # either way.
                    future.cancel()
                    effective_budget = min(
                        scout.budget_seconds, self._global_budget_seconds
                    )
                    results.append(
                        ScoutResult(
                            scout_id=scout.scout_id,
                            elapsed_ms=elapsed_ms,
                            status="timeout",
                            error=f"exceeded {effective_budget:.1f}s budget",
                        )
                    )
                    run_logger.append_event(
                        DiscoveryRunEvent(
                            source_id=scout.scout_id,
                            source_type=source_type,
                            status="timeout",
                            error=f"exceeded {effective_budget:.1f}s budget",
                            elapsed_ms=elapsed_ms,
                            query=task_description,
                            searched_capabilities=tuple(searched_caps),
                            trigger="scout",
                        )
                    )
                    _logger.warning(
                        "scout=%s status=timeout elapsed=%dms budget=%.1fs",
                        scout.scout_id,
                        elapsed_ms,
                        effective_budget,
                    )
                    pending.discard(future)

                # If the global deadline already passed, anything still
                # pending at this point would be re-marked next iteration
                # — short-circuit so we exit cleanly.
                if time.monotonic() >= global_deadline and pending:
                    continue
        finally:
            executor.shutdown(wait=False)

        merged = _merge_dedupe([c for r in results for c in r.candidates])
        total_elapsed_ms = int((time.monotonic() - wall_start) * 1000)
        # Tally per-status counts so the operator gets a one-line
        # answer to "did discovery actually help?" — useful both for
        # interactive debugging and for spotting silent regressions
        # (e.g., all scouts skipped because tokens disappeared).
        status_counts: dict[str, int] = {}
        for r in results:
            status_counts[r.status] = status_counts.get(r.status, 0) + 1
        _logger.info(
            "scout dispatch complete: capability=%s elapsed=%dms merged_candidates=%d %s",
            capability or "—",
            total_elapsed_ms,
            len(merged),
            " ".join(f"{k}={v}" for k, v in sorted(status_counts.items())),
        )
        return DispatchResult(
            sub_task_id=sub_task_id,
            capability=capability,
            merged_candidates=merged,
            scout_results=results,
            total_elapsed_ms=total_elapsed_ms,
        )


def _safe_scout_search(
    source: _SearchableSource, capabilities: set[str], task_description: str
) -> tuple[list[DiscoveryCandidate], str | None]:
    """Wraps `source.search` to convert exceptions into a (results, error)
    tuple instead of bubbling out — the dispatcher records error state
    in `ScoutResult` rather than aborting other scouts."""

    try:
        return (
            source.search(
                capabilities=capabilities, task_description=task_description
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001 - we deliberately catch everything
        return ([], f"{type(exc).__name__}: {exc}")


def _merge_dedupe(candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
    """Dedupe candidates across scouts using the same dedupe_key the
    persistent index uses, so the dispatcher result is shape-compatible
    with whatever downstream consumes it (e.g., the existing
    DiscoveryIndex.ingest path)."""

    out: dict[str, DiscoveryCandidate] = {}
    for candidate in candidates:
        key = dedupe_key(candidate)
        if key not in out:
            out[key] = candidate
    return list(out.values())


def default_scouts() -> list[Scout]:
    """Build the default scout fleet from existing first-party sources.

    Each scout's `budget_seconds` is calibrated to the source's typical
    latency on a warm cache: the official MCP registry returns in
    ~1-2s, APIs.guru in ~3-5s, the GitHub code search in ~2-4s, HN in
    ~5-8s (parallel item fetches), and RSS in ~3-5s (13 feeds in
    parallel inside the source).

    Sources that need credentials (`requires_token`) are silently
    skipped at dispatch time when the env var is absent — keeps the
    fleet declarative without forcing every deployment to provision
    every key.
    """
    # Local imports keep the scout module standalone (no circular
    # imports between scouts.py and service.py since service can later
    # call default_scouts() too).
    import os
    from pathlib import Path

    from planmyagents_api.discovery.service import _load_curated_rss_feeds
    from planmyagents_api.discovery.sources.a2a import A2AAgentCardSource
    from planmyagents_api.discovery.sources.ai_directory import AiAgentDirectorySource
    from planmyagents_api.discovery.sources.apis_guru import ApisGuruSource
    from planmyagents_api.discovery.sources.github_awesome_lists import (
        GitHubAwesomeListsSource,
    )
    from planmyagents_api.discovery.sources.github_recently_pushed import (
        GitHubRecentlyPushedSource,
    )
    from planmyagents_api.discovery.sources.glama import GlamaDirectorySource
    from planmyagents_api.discovery.sources.hacker_news import HackerNewsAgentWatcherSource
    from planmyagents_api.discovery.sources.live import GitHubCodeSearchSource
    from planmyagents_api.discovery.sources.mcp import McpCatalogSource
    from planmyagents_api.discovery.sources.mcp_marketplace import MCPMarketplaceSource
    from planmyagents_api.discovery.sources.moltbook import MoltbookSource
    from planmyagents_api.discovery.sources.npm_packages import NpmMcpPackagesSource
    from planmyagents_api.discovery.sources.official_mcp_registry import (
        OfficialMcpRegistrySource,
    )
    from planmyagents_api.discovery.sources.smithery import SmitherySource
    from planmyagents_api.discovery.sources.static import StaticDiscoverySource
    from planmyagents_api.discovery.sources.vendor_rss import VendorRssSource
    from planmyagents_api.discovery.sources.web_doc import WebDocDiscoverySource

    # Optional LLM arbiter for ambiguous "is this an agent?" decisions
    # inside the RSS/HN sources. We reuse the planner's local Qwen
    # client so we don't stand up a parallel Ollama config. When the
    # user opts out (PLANMYAGENTS_CLASSIFIER_LLM=false) the classifier
    # falls back to "uncertain → reject" — fine, just under-collects.
    classifier_client = _build_classifier_client_or_none()
    repo_root = Path(__file__).resolve().parents[4]
    curated = repo_root / "packages" / "discovery" / "sources"

    scouts: list[Scout] = [
        # Local curated sources are cheap and deterministic. They must
        # run in request-time discovery, not only in batch/post-goal
        # refresh, otherwise a fresh store can miss known commerce/search
        # providers while noisy remote scouts still return long tails.
        Scout(
            scout_id="curated_static",
            source=StaticDiscoverySource(),
            budget_seconds=1.0,
        ),
        Scout(
            scout_id="curated_mcp_catalog",
            source=McpCatalogSource([str(curated / "curated_mcp_catalog.json")]),
            budget_seconds=1.0,
        ),
        Scout(
            scout_id="curated_a2a_cards",
            source=A2AAgentCardSource([str(curated / "curated_a2a_cards.json")]),
            budget_seconds=1.0,
        ),
        Scout(
            scout_id="curated_ai_agents",
            source=AiAgentDirectorySource([str(curated / "curated_ai_agents.json")]),
            budget_seconds=1.0,
        ),
        Scout(
            scout_id="curated_web_docs",
            source=WebDocDiscoverySource([str(curated / "curated_web_docs.json")]),
            budget_seconds=1.0,
        ),
        Scout(
            scout_id="official_mcp_registry",
            # No agent_classifier here on purpose — see the OfficialMcp
            # RegistrySource docstring. Junk is filtered by the source's
            # `_is_obvious_junk` regex, and goal-relevance is filtered by
            # the request-time `CandidateJudge`.
            source=OfficialMcpRegistrySource(),
            # Empirically (2026-05-14 logs): cursor-based pagination
            # through ~60-200 servers consistently took the full 8s
            # budget and timed out. 14s wasn't enough either on cold
            # caches; bumped to 24s so the source can return 2-3
            # pages of results and let the embedder classify each
            # without the dispatcher killing the call mid-stream.
            budget_seconds=24.0,
        ),
        Scout(
            scout_id="apis_guru",
            source=ApisGuruSource(),
            # ~2MB list.json payload + per-spec follow-ups. The
            # follow-up step is what blows the budget on cold runs;
            # 24s gives the source enough headroom to hydrate
            # several specs before being cancelled.
            budget_seconds=24.0,
        ),
        Scout(
            scout_id="hacker_news_agent_watch",
            source=HackerNewsAgentWatcherSource(
                # Tighter cap for request-time use — we don't need 100
                # items per firehose live; recent + top together gives
                # enough coverage in ~3s.
                max_items_per_firehose=40,
                llm_classifier_client=classifier_client,
            ),
            # HN's per-item firehose fetch (40 items × ~80ms each) plus
            # the LLM classifier roundtrip routinely overran 12s in
            # the field. 22s gives the classifier room without
            # forcing us to drop the LLM-arbitrated path.
            budget_seconds=22.0,
        ),
        Scout(
            scout_id="vendor_rss",
            source=VendorRssSource(
                feed_urls=_load_curated_rss_feeds(),
                llm_classifier_client=classifier_client,
            ),
            # Curated RSS feeds + LLM classifier roundtrip per matched
            # item ran out of budget at 6s in the surprise-birthday-gift
            # audit. 12s aligns with the rest of the LLM-classifier
            # scouts (HN, github_code_search) and prevents the false
            # "0 candidates" outcome that just means "we ran out of
            # time before classifying anything".
            budget_seconds=12.0,
        ),
        Scout(
            scout_id="github_code_search",
            source=GitHubCodeSearchSource(token=os.getenv("GITHUB_TOKEN", "")),
            budget_seconds=6.0,
            requires_token="GITHUB_TOKEN",
        ),
        Scout(
            scout_id="github_recently_pushed",
            source=GitHubRecentlyPushedSource(
                token=os.getenv("GITHUB_TOKEN", ""),
                # Tighter window for live calls — only the past week.
                pushed_within_days=7,
            ),
            # GitHub search API + repo metadata + README sniff per
            # result is consistently 8-10s on a goal that produces
            # several pages, and the README sniff alone can spike
            # past 12s on large repos. 22s matches the HN budget so
            # the GitHub-shaped scouts share one bottleneck rather
            # than each capping at a different ceiling.
            budget_seconds=22.0,
            requires_token="GITHUB_TOKEN",
        ),
        # MCP Marketplace — broader than the canonical MCP registry
        # because it indexes third-party servers that never made it to
        # the protocol-authors' catalogue. No auth, semantic ``q``
        # search, ~1,400 servers and growing. Budget is generous (10s)
        # because the upstream is a hosted Vercel/Next.js endpoint
        # whose cold-cache p99 has been observed in the 5-7s range.
        Scout(
            scout_id="mcp_marketplace",
            source=MCPMarketplaceSource(),
            # Bumped from 10s to 18s on 2026-05-15. Marketplace lists
            # paginate by 25, and corroboration-filter walks per item
            # to GitHub for star counts — 10s was clipping the
            # corroboration step on cold caches.
            budget_seconds=18.0,
        ),
        # Smithery — the largest MCP server registry by volume
        # (5,000+ servers). Bearer-token auth means the scout
        # silently skips when SMITHERY_API_KEY is unset, keeping the
        # default deployment credential-free. Budget matches MCP
        # Marketplace; semantic search reduces the page count we
        # actually need.
        Scout(
            scout_id="smithery",
            source=SmitherySource(token=os.getenv("SMITHERY_API_KEY", "")),
            # Bumped from 10s to 18s on 2026-05-15 — the
            # surprise-birthday-gift audit caught it timing out on
            # payment_processing exactly at the 10s budget. Smithery's
            # corroboration walk (search → useCount → GitHub
            # cross-ref) often goes 8-12s on cold lookups.
            budget_seconds=18.0,
            requires_token="SMITHERY_API_KEY",
        ),
        # Moltbook — social-network discovery for AI agents
        # ("moltys"). Lower-precision than the MCP-aggregator
        # sources above (a molty posting about a topic doesn't mean
        # they can do that topic), but adds recall by surfacing
        # agents that nobody indexed in a structured registry. The
        # source does a two-step fetch (semantic search + per-author
        # profile) so the budget is generous; bearer-token auth
        # means we silently skip when MOLTBOOK_API_KEY is unset.
        # The downstream LLM CandidateJudge is the load-bearing
        # relevance filter for noisy entries.
        Scout(
            scout_id="moltbook",
            source=MoltbookSource(token=os.getenv("MOLTBOOK_API_KEY", "")),
            budget_seconds=12.0,
            requires_token="MOLTBOOK_API_KEY",
        ),
        # Glama — community-curated MCP directory at 23,794+ servers
        # (vs Smithery ~5,000, MCP Marketplace ~1,400, Official MCP
        # Registry ~200–500). Open public JSON API, no auth required.
        # Indexes a meaningful long tail of third-party servers that
        # never made it into the other catalogs; the dedupe layer
        # collapses overlaps so the net effect is pure recall
        # addition. Budget mirrors MCP Marketplace because the API
        # shape (cursor-paginated JSON) and cold-cache latency are
        # similar.
        Scout(
            scout_id="glama",
            source=GlamaDirectorySource(),
            budget_seconds=18.0,
        ),
        # npm registry — the largest single channel by which MCP
        # servers are distributed today (47,000+ packages tagged
        # ``mcp`` as of 2026-05). Catches packages BEFORE they get
        # listed in the curated MCP aggregators. Rich corroboration
        # signal: weekly downloads, dependents count, and npm's own
        # composite quality score — all surfaced as metadata for
        # the CandidateJudge to break ties. Unauthenticated; always
        # runs.
        #
        # Budget rationale (8 s, lowered from 12 s on 2026-05-20):
        # the initial 12 s budget was reliably tripping the
        # dispatcher's timeout on every observed /goal call — i.e.,
        # the scout always burnt its full budget without returning
        # results. Root cause: `page_size=100 × max_pages=2 = 200`
        # candidates per query, each requiring an embedder inference
        # (~50 ms) after corroboration filtering = >12 s total.
        # The source-side fix lowered the work per query
        # (page_size=50, max_pages=1) to fit comfortably under 8 s,
        # which matches the budget here. If you raise this budget
        # you almost certainly want to raise the source's
        # ``page_size`` / ``max_pages`` too, otherwise the extra
        # budget gets spent waiting for an idle scout to finish.
        Scout(
            scout_id="npm_mcp_packages",
            source=NpmMcpPackagesSource(),
            budget_seconds=8.0,
        ),
        # GitHub awesome-* lists — community-curated catalogs of MCP
        # servers / AI agents / agent frameworks. Each list is a
        # README that gets manually updated; together they cover
        # populations the formal aggregators miss (niche / newly-
        # published / non-English entries). Reuses GITHUB_TOKEN so
        # the dispatcher silently skips when no token is configured.
        #
        # Budget rationale (12 s, lowered from 25 s on 2026-05-20):
        # the initial 25 s budget made this scout the request-time
        # bottleneck on every per-capability dispatch — higher than
        # the prior fleet ceiling of 18 s (smithery). It dominated
        # /goal latency by 4-7 s per dispatch even when it returned
        # zero candidates. Root cause: 6 lists × up to 60 entries/
        # list × ~100 ms per-entry embedder call = potential 36 s
        # of CPU-bound inference. The source-side fix lowered
        # ``max_entries_per_list`` from 60 → 25 to fit under 12 s
        # at the worst-case cold-cache fetch. If a curator's
        # awesome-list legitimately has > 25 high-signal entries we
        # rely on the cron backfill (``make discovery-refresh``)
        # to pick them up over time, NOT on the request-time scout.
        Scout(
            scout_id="github_awesome_lists",
            source=GitHubAwesomeListsSource(token=os.getenv("GITHUB_TOKEN", "")),
            budget_seconds=12.0,
            requires_token="GITHUB_TOKEN",
        ),
    ]
    return scouts


def _build_classifier_client_or_none():
    """Build a ChatClient for the agent classifier, or return None.

    Returns the escalating client (Qwen primary, Groq fallback) so a
    Qwen outage at scout-time doesn't silently disable classifier
    filtering. Off-switch: `PLANMYAGENTS_CLASSIFIER_LLM=false` skips
    wiring the LLM even if a tier is available — useful for tests and
    for ops who'd rather not pay the latency on every RSS poll. Without
    an LLM, the classifier rejects ambiguous items — under-collect,
    never over-collect.
    """

    import os

    if os.getenv("PLANMYAGENTS_CLASSIFIER_LLM", "true").lower() in {"0", "false", "no"}:
        return None
    try:
        from planmyagents_api.llm.escalating_client import build_default_escalating_client

        return build_default_escalating_client()
    except Exception:  # noqa: BLE001 — never block scout startup
        return None
