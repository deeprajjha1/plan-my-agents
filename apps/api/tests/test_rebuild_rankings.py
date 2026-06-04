"""Tests for :mod:`planmyagents_api.benchmark.rebuild`.

The regression these guard against is the leaderboard bug discovered
on 2026-05-19: ``benchmark_runs`` had three cells with 5 succeeded
real-shape runs each, but ``agent_rankings`` had zero rows because
``scripts/backfill_phase5_cells.py`` never called ``save_rankings``.
The /leaderboards endpoint silently rendered
``Bench-passed 0 · Routable 0 · Real runs No`` for cells with real
runs — contradicting the deck and undermining vendor trust on the
most-scrutinised page.

These tests pin three invariants the fix relies on:

1. After :func:`rebuild_rankings_from_runs` runs, the per-cell ranking
   ``source`` reflects the strongest provenance observed in the runs
   (``real_adapter`` > ``response_fixture`` > ``synthetic``).
2. ``has_real_adapter_runs`` (the field /leaderboards renders as
   "Real runs: Yes/No") is True iff source is anything other than
   ``synthetic``.
3. Calling the rebuild twice in a row leaves ``agent_rankings``
   in the same state (idempotent — operators can run
   ``make rebuild-rankings`` defensively without worrying about
   duplicates or drift).
"""

from __future__ import annotations

import unittest
from typing import Any

from planmyagents_api.benchmark.rankings import AgentRanking
from planmyagents_api.benchmark.rebuild import (
    RebuildReport,
    rebuild_rankings_from_runs,
)


class _FakeBenchmarkStore:
    """Minimal in-memory stand-in for :class:`PostgresBenchmarkStore`.

    Implements only the two methods :func:`rebuild_rankings_from_runs`
    consumes (``load_json`` + ``save_rankings``). Keeps the test
    suite Postgres-free.
    """

    def __init__(self, raw_rows: list[dict[str, Any]]) -> None:
        self._raw_rows = raw_rows
        self.saved_rankings: list[AgentRanking] = []

    def load_json(self) -> list[dict[str, Any]]:
        return list(self._raw_rows)

    def save_rankings(self, rankings: list[AgentRanking]) -> None:
        # PostgresBenchmarkStore truncates per-cell rows it owns before
        # re-inserting; mirror that semantic so the idempotency test
        # exercises the same shape.
        self.saved_rankings = list(rankings)


def _make_run_row(
    *,
    provider_id: str,
    capability: str,
    test_case_id: str,
    succeeded: bool = True,
    quality_score: float = 1.0,
    latency_ms: int = 100,
    cost_usd: float = 0.0,
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a row in the exact shape ``BenchmarkStore.load_json`` emits.

    Mirrors the post-merge structure (flattened columns + nested
    run_json overlay) so the tests exercise the same fall-back logic
    the production code sees.
    """

    return {
        "provider_id": provider_id,
        "capability": capability,
        "test_case_id": test_case_id,
        "difficulty": "smoke_test",
        "score": {
            "quality_score": quality_score,
            "succeeded": succeeded,
            "reason": "test",
            "field_scores": [],
        },
        "response": {
            "succeeded": succeeded,
            "output": output or {},
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "error": None,
            "raw_response": {},
        },
        "created_at": "2026-05-19T10:00:00+00:00",
    }


class RebuildRankingsSourceClassificationTests(unittest.TestCase):
    """Source classification must reflect the strongest provenance signal."""

    def test_replayed_from_marker_promotes_cell_to_real_adapter(self) -> None:
        # Razorpay's exact shape: nested `_replayed_from` on the output.
        rows = [
            _make_run_row(
                provider_id="razorpay-payments",
                capability="payment_authorization",
                test_case_id=f"case-{i}",
                output={"_replayed_from": "agents.json:benchmark_status_evidence"},
            )
            for i in range(5)
        ]
        store = _FakeBenchmarkStore(rows)

        report = rebuild_rankings_from_runs(store)

        self.assertEqual(report.cells_real_adapter, 1)
        self.assertEqual(report.cells_response_fixture, 0)
        self.assertEqual(report.cells_synthetic, 0)
        self.assertEqual(len(store.saved_rankings), 1)
        self.assertEqual(store.saved_rankings[0].source, "real_adapter")
        # /leaderboards renders "Real runs: Yes" when this is True.
        self.assertNotIn(store.saved_rankings[0].source, {"", "synthetic"})

    def test_response_fixture_marker_promotes_cell_to_response_fixture(self) -> None:
        # Phase-5 Resend/Firecrawl shape.
        rows = [
            _make_run_row(
                provider_id="resend-emails",
                capability="email_send",
                test_case_id=f"case-{i}",
                output={"_provenance": "response_fixture_pending_live_key"},
            )
            for i in range(5)
        ]
        store = _FakeBenchmarkStore(rows)

        report = rebuild_rankings_from_runs(store)

        self.assertEqual(report.cells_response_fixture, 1)
        self.assertEqual(report.cells_real_adapter, 0)
        self.assertEqual(store.saved_rankings[0].source, "response_fixture")
        # Critically: response_fixture must ALSO count as "real runs"
        # on the leaderboard. The whole point of the marker is that
        # the wrapper code path is real; only the transport is mocked.
        self.assertNotIn(store.saved_rankings[0].source, {"", "synthetic"})

    def test_strongest_source_wins_when_provenance_is_mixed(self) -> None:
        # One real-adapter row + four response-fixture rows in the same
        # cell → cell is real_adapter (strongest wins, not modal).
        rows = [
            _make_run_row(
                provider_id="vendor",
                capability="cap",
                test_case_id="case-1",
                output={"_replayed_from": "replayed_live_run/2026-05-15"},
            )
        ] + [
            _make_run_row(
                provider_id="vendor",
                capability="cap",
                test_case_id=f"case-{i}",
                output={"_provenance": "response_fixture_pending_live_key"},
            )
            for i in range(2, 6)
        ]
        store = _FakeBenchmarkStore(rows)

        rebuild_rankings_from_runs(store)

        self.assertEqual(store.saved_rankings[0].source, "real_adapter")

    def test_no_provenance_marker_falls_back_to_synthetic(self) -> None:
        rows = [
            _make_run_row(
                provider_id="mock-vendor",
                capability="email_send",
                test_case_id="case-1",
            )
        ]
        store = _FakeBenchmarkStore(rows)

        rebuild_rankings_from_runs(store)

        self.assertEqual(store.saved_rankings[0].source, "synthetic")


class RebuildRankingsAggregationTests(unittest.TestCase):
    """Ranking aggregates must reflect the underlying runs."""

    def test_succeeded_runs_drive_bench_passed_status(self) -> None:
        # 5 succeeded runs at quality_score 1.0 → success_rate 1.0,
        # avg_quality 1.0 → benchmark_status="passed" per
        # benchmark_status_from_ranking (>=0.95 + >=0.8).
        rows = [
            _make_run_row(
                provider_id="resend-emails",
                capability="email_send",
                test_case_id=f"case-{i}",
                succeeded=True,
                quality_score=1.0,
                output={"_provenance": "response_fixture_pending_live_key"},
            )
            for i in range(5)
        ]
        store = _FakeBenchmarkStore(rows)

        rebuild_rankings_from_runs(store)

        self.assertEqual(len(store.saved_rankings), 1)
        ranking = store.saved_rankings[0]
        self.assertEqual(ranking.benchmark_status, "passed")
        self.assertEqual(ranking.sample_size, 5)
        self.assertEqual(ranking.success_rate, 1.0)

    def test_rankings_grouped_per_provider_capability_pair(self) -> None:
        # Two providers × one capability + one provider × another cap
        # → 3 distinct rankings.
        rows = [
            _make_run_row(provider_id="vendor-a", capability="cap-x", test_case_id="c1"),
            _make_run_row(provider_id="vendor-b", capability="cap-x", test_case_id="c2"),
            _make_run_row(provider_id="vendor-a", capability="cap-y", test_case_id="c3"),
        ]
        store = _FakeBenchmarkStore(rows)

        report = rebuild_rankings_from_runs(store)

        self.assertEqual(report.cells_total, 3)
        self.assertEqual(report.rankings_persisted, 3)
        ids = {(r.provider_id, r.capability) for r in store.saved_rankings}
        self.assertEqual(
            ids,
            {("vendor-a", "cap-x"), ("vendor-b", "cap-x"), ("vendor-a", "cap-y")},
        )


class RebuildRankingsIdempotencyTests(unittest.TestCase):
    """Calling rebuild twice must leave the store in the same state."""

    def test_double_invocation_produces_identical_rankings(self) -> None:
        rows = [
            _make_run_row(
                provider_id="firecrawl",
                capability="web_scraping",
                test_case_id=f"case-{i}",
                output={"_provenance": "response_fixture_pending_live_key"},
            )
            for i in range(5)
        ]
        store = _FakeBenchmarkStore(rows)

        first = rebuild_rankings_from_runs(store)
        after_first = list(store.saved_rankings)
        second = rebuild_rankings_from_runs(store)
        after_second = list(store.saved_rankings)

        self.assertEqual(first, second)
        self.assertEqual(len(after_first), len(after_second))
        # Field-by-field equality on every ranking; sorting is
        # deterministic inside compute_rankings, so position-by-position
        # comparison is safe.
        for ranking_a, ranking_b in zip(after_first, after_second, strict=True):
            self.assertEqual(ranking_a, ranking_b)

    def test_empty_runs_produces_empty_rankings_report(self) -> None:
        store = _FakeBenchmarkStore([])

        report = rebuild_rankings_from_runs(store)

        self.assertEqual(report, RebuildReport(0, 0, 0, 0, 0, 0))
        self.assertEqual(store.saved_rankings, [])


if __name__ == "__main__":
    unittest.main()
