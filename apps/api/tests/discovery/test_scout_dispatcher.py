"""Tests for `ScoutDispatcher` and the `Scout` wiring.

Each test uses synthetic in-process sources so dispatcher behavior
(parallelism, timeouts, errors, dedupe) is verified deterministically.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

# Disable the discovery run-event audit log for this test module —
# `dispatcher.dispatch` would otherwise write JSONL events into
# `apps/data/discovery_run_events.jsonl` as a side effect.
os.environ.setdefault("PLANMYAGENTS_RUN_LOG_ENABLED", "false")

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.normalizer import normalize_candidate  # noqa: E402
from planmyagents_api.discovery.scouts import (  # noqa: E402
    Scout,
    ScoutDispatcher,
    default_scouts,
)


def _candidate(slug: str, source: str = "test") -> object:
    return normalize_candidate(
        {
            "id": slug,
            "display_name": slug.replace("-", " "),
            "vendor": slug.split("-", 1)[0],
            "vendor_url": f"https://example.com/{slug}",
            "provider_type": "ai_agent",
            "capabilities": [{"id": "semantic_search", "confidence": 0.7}],
        },
        source=source,
    )


@dataclass
class _FakeSource:
    """Fake DiscoverySource that returns a fixed list (optionally after
    sleeping or raising)."""

    candidates: list
    sleep_seconds: float = 0.0
    raise_exc: BaseException | None = None
    record_calls: list[tuple[set, str]] | None = None

    def search(self, *, capabilities, task_description):
        if self.record_calls is not None:
            self.record_calls.append((set(capabilities), task_description))
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        if self.raise_exc:
            raise self.raise_exc
        return list(self.candidates)


class ScoutDispatcherTests(unittest.TestCase):
    def test_runs_all_scouts_and_merges_unique_candidates(self) -> None:
        scout_a = Scout(
            scout_id="a",
            source=_FakeSource([_candidate("alpha"), _candidate("beta")]),
        )
        scout_b = Scout(
            scout_id="b",
            source=_FakeSource([_candidate("gamma")]),
        )
        result = ScoutDispatcher([scout_a, scout_b]).dispatch(
            capability="semantic_search", task_description="task"
        )
        ids = sorted(c.id for c in result.merged_candidates)
        self.assertEqual(ids, ["alpha", "beta", "gamma"])
        self.assertEqual(len(result.scout_results), 2)
        for r in result.scout_results:
            self.assertEqual(r.status, "ok")

    def test_dedupes_duplicate_candidates_across_scouts(self) -> None:
        shared = _candidate("alpha")
        scout_a = Scout(scout_id="a", source=_FakeSource([shared]))
        scout_b = Scout(scout_id="b", source=_FakeSource([shared]))
        result = ScoutDispatcher([scout_a, scout_b]).dispatch(
            capability="semantic_search", task_description="t"
        )
        self.assertEqual(len(result.merged_candidates), 1)

    def test_slow_scout_times_out_without_blocking_fast_scouts(self) -> None:
        slow = Scout(
            scout_id="slow",
            source=_FakeSource([_candidate("never")], sleep_seconds=2.0),
            budget_seconds=0.2,
        )
        fast = Scout(
            scout_id="fast",
            source=_FakeSource([_candidate("alpha")]),
            budget_seconds=2.0,
        )
        wall_start = time.monotonic()
        result = ScoutDispatcher(
            [slow, fast], global_budget_seconds=2.0
        ).dispatch(capability=None, task_description="t")
        elapsed = time.monotonic() - wall_start
        self.assertLess(elapsed, 1.5, f"dispatch took {elapsed:.2f}s — slow scout was waited on too long")
        statuses = {r.scout_id: r.status for r in result.scout_results}
        self.assertEqual(statuses, {"slow": "timeout", "fast": "ok"})
        ids = [c.id for c in result.merged_candidates]
        self.assertEqual(ids, ["alpha"])

    def test_failing_scout_recorded_as_error_not_raised(self) -> None:
        boom = Scout(
            scout_id="boom",
            source=_FakeSource([], raise_exc=RuntimeError("bad upstream")),
        )
        ok = Scout(scout_id="ok", source=_FakeSource([_candidate("alpha")]))
        result = ScoutDispatcher([boom, ok]).dispatch(
            capability=None, task_description="t"
        )
        boom_result = next(r for r in result.scout_results if r.scout_id == "boom")
        self.assertEqual(boom_result.status, "error")
        self.assertIn("RuntimeError", boom_result.error or "")
        self.assertIn("alpha", [c.id for c in result.merged_candidates])

    def test_skips_scouts_when_required_token_missing(self) -> None:
        gated = Scout(
            scout_id="needs_token",
            source=_FakeSource([_candidate("alpha")]),
            requires_token="MY_FAKE_TOKEN",
        )
        # token_resolver returns None → skip
        result = ScoutDispatcher([gated]).dispatch(
            capability=None,
            task_description="t",
            token_resolver=lambda _: None,
        )
        self.assertEqual(len(result.merged_candidates), 0)
        self.assertEqual(result.scout_results[0].status, "skipped")
        self.assertIn("MY_FAKE_TOKEN", result.scout_results[0].skipped_reason or "")

    def test_runs_scouts_in_parallel(self) -> None:
        # Three scouts each sleeping 0.4s. If parallel, total ≈ 0.4s.
        # If sequential, ≈ 1.2s.
        scouts = [
            Scout(
                scout_id=f"s{i}",
                source=_FakeSource(
                    [_candidate(f"alpha-{i}")], sleep_seconds=0.4
                ),
                budget_seconds=2.0,
            )
            for i in range(3)
        ]
        wall_start = time.monotonic()
        result = ScoutDispatcher(scouts, global_budget_seconds=2.0).dispatch(
            capability=None, task_description="t"
        )
        elapsed = time.monotonic() - wall_start
        self.assertLess(elapsed, 0.9, f"parallel dispatch took {elapsed:.2f}s — likely sequential")
        self.assertEqual(len(result.merged_candidates), 3)

    def test_slow_first_scout_does_not_starve_later_scouts(self) -> None:
        """Regression for the dispatcher residual-budget bug.

        Before the fix, scouts were waited on in submission order and
        each later scout got ``global_budget - elapsed_so_far`` as its
        effective budget. A real /goal run was observed where
        ``official_mcp_registry`` consumed 7.9s of a 10s global budget,
        leaving ``vendor_rss`` with 2.1s and ``github_recently_pushed``
        with 0.0s — both timed out despite their futures having
        completed in parallel almost immediately.

        This test reproduces that shape: scout A sleeps 0.6s (the
        "slow first scout"), scouts B and C return instantly. Each has
        a 0.3s per-scout budget. Before the fix, B and C would be
        marked timeout because by the time the dispatcher iterated to
        them their residual was below their budget AND their per-scout
        budget was less than A's elapsed. After the fix, B and C
        complete inside their own 0.3s budget regardless of A's slowness
        because we wait on completion order, not submission order.
        """

        slow_first = Scout(
            scout_id="slow_first",
            source=_FakeSource([_candidate("from-slow")], sleep_seconds=0.6),
            budget_seconds=1.5,
        )
        fast_b = Scout(
            scout_id="fast_b",
            source=_FakeSource([_candidate("from-b")]),
            budget_seconds=0.3,
        )
        fast_c = Scout(
            scout_id="fast_c",
            source=_FakeSource([_candidate("from-c")]),
            budget_seconds=0.3,
        )
        result = ScoutDispatcher(
            [slow_first, fast_b, fast_c], global_budget_seconds=2.0
        ).dispatch(capability=None, task_description="t")

        statuses = {r.scout_id: r.status for r in result.scout_results}
        # All three scouts must succeed. Pre-fix this would have shown
        # ``slow_first=ok, fast_b=timeout, fast_c=timeout`` because the
        # iteration-order budget shrunk to ~0s by the time we got to
        # B and C.
        self.assertEqual(
            statuses,
            {"slow_first": "ok", "fast_b": "ok", "fast_c": "ok"},
            f"unexpected statuses (regression?): {statuses}",
        )
        # And all three candidates land in the merged set.
        ids = sorted(c.id for c in result.merged_candidates)
        self.assertEqual(ids, ["from-b", "from-c", "from-slow"])

    def test_per_scout_query_overrides_passed_to_source(self) -> None:
        calls_a: list[tuple[set, str]] = []
        calls_b: list[tuple[set, str]] = []
        scout_a = Scout(
            scout_id="a",
            source=_FakeSource([_candidate("alpha")], record_calls=calls_a),
        )
        scout_b = Scout(
            scout_id="b",
            source=_FakeSource([_candidate("beta")], record_calls=calls_b),
        )
        ScoutDispatcher([scout_a, scout_b]).dispatch(
            capability="x",
            task_description="default-task-text",
            per_scout_query_overrides={"a": "custom-a-query"},
        )
        # Scout a should have received the override; scout b the default.
        self.assertEqual(calls_a[0][1], "custom-a-query")
        self.assertEqual(calls_b[0][1], "default-task-text")

    def test_dispatch_logs_per_scout_status_lines(self) -> None:
        """Operator-visibility regression test: the dispatcher must
        emit one log line per scout outcome so a stuck /goal request
        is debuggable by `tail -f`. We assert on substrings rather
        than full format strings because the format is presentational
        but the shape ('scout=<id> status=<state>') is the contract.
        """

        import io
        import logging

        scout_ok = Scout(
            scout_id="ok",
            source=_FakeSource([_candidate("alpha"), _candidate("beta")]),
        )
        scout_err = Scout(
            scout_id="boom",
            source=_FakeSource([], raise_exc=RuntimeError("kaboom")),
        )
        scout_skipped = Scout(
            scout_id="needs_token",
            source=_FakeSource([_candidate("gamma")]),
            requires_token="MY_FAKE_TOKEN",
        )

        capture = io.StringIO()
        handler = logging.StreamHandler(capture)
        scout_logger = logging.getLogger("planmyagents_api.discovery.scouts")
        scout_logger.addHandler(handler)
        original_level = scout_logger.level
        scout_logger.setLevel(logging.DEBUG)
        try:
            ScoutDispatcher(
                [scout_ok, scout_err, scout_skipped]
            ).dispatch(
                capability="semantic_search",
                task_description="t",
                token_resolver=lambda _: None,
            )
        finally:
            scout_logger.removeHandler(handler)
            scout_logger.setLevel(original_level)

        logs = capture.getvalue()
        self.assertIn("scout dispatch starting", logs)
        self.assertIn("scout=ok status=ok", logs)
        self.assertIn("scout=boom status=error", logs)
        self.assertIn(
            "scout=needs_token status=skipped reason=missing_token:MY_FAKE_TOKEN",
            logs,
        )
        self.assertIn("scout dispatch complete", logs)

    def test_capability_passed_as_single_element_set(self) -> None:
        calls: list[tuple[set, str]] = []
        scout = Scout(
            scout_id="x",
            source=_FakeSource([_candidate("alpha")], record_calls=calls),
        )
        ScoutDispatcher([scout]).dispatch(
            capability="email_verification", task_description="t"
        )
        self.assertEqual(calls[0][0], {"email_verification"})

    def test_default_scouts_returns_full_fleet(self) -> None:
        """Full snapshot of the default fleet. Update this test when
        registering a new source — it doubles as documentation of the
        canonical scout set and as the gate that blocks accidental
        removals."""

        scouts = default_scouts()
        ids = {s.scout_id for s in scouts}
        self.assertEqual(
            ids,
            {
                # Public, no auth
                "curated_static",
                "curated_mcp_catalog",
                "curated_a2a_cards",
                "curated_ai_agents",
                "curated_web_docs",
                "official_mcp_registry",
                "apis_guru",
                "hacker_news_agent_watch",
                "vendor_rss",
                "mcp_marketplace",
                # 2026-05-20 Tier-1 discovery-breadth expansion:
                # Glama community catalog (largest MCP directory at
                # 23,794+ servers; clean public JSON API, no auth) +
                # npm registry scout (47,000+ MCP-tagged packages;
                # catches servers before they hit any aggregator).
                "glama",
                "npm_mcp_packages",
                # Auth required (silently skipped without env var)
                "github_code_search",
                "github_recently_pushed",
                # 2026-05-20 Tier-1 expansion: GitHub awesome-* lists
                # community catalog scraper. Reuses GITHUB_TOKEN.
                "github_awesome_lists",
                "smithery",
                # Slice 3: Moltbook social-network discovery for AI
                # agents. Bearer token; lower precision than the
                # MCP-aggregator sources above, used for recall
                # additivity rather than primary signal.
                "moltbook",
            },
        )
        curated_ai = next(s for s in scouts if s.scout_id == "curated_ai_agents")
        commerce_candidates = curated_ai.source.search(
            capabilities={"price_comparison"},
            task_description="find a birthday gift and compare prices",
        )
        self.assertTrue(
            {
                "amazon-product-advertising",
                "google-shopping-search",
                "etsy-open-api",
                "ebay-buy-api",
            }.issubset({candidate.id for candidate in commerce_candidates})
        )
        gated = {s.scout_id for s in scouts if s.requires_token}
        self.assertEqual(
            gated,
            {
                "github_code_search",
                "github_recently_pushed",
                "smithery",
                "moltbook",
                # 2026-05-20 Tier-1 expansion: the awesome-lists
                # scraper reuses GITHUB_TOKEN. Listed here so the
                # canonical "which sources are auth-gated" snapshot
                # stays accurate.
                "github_awesome_lists",
            },
        )


if __name__ == "__main__":
    unittest.main()
