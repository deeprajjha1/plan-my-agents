"""Tests for the persistent discovery-gap event store.

The store is intentionally tiny — three backends (JSON, SQLite,
Postgres) sharing one append + load + summarize protocol. The tests
focus on the contract every backend must honour, plus the
``build_discovery_gap_events`` helper that translates one ``/goal``
request into one event per missing capability and the
``summarize_discovery_gaps`` aggregator that powers the leaderboard
tile. We do not exercise the Postgres backend in these unit tests
because that requires a live database; the JSON and SQLite backends
share enough behaviour with Postgres that a contract-level guarantee
suffices for CI.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.discovery_gaps_store import (  # noqa: E402
    DiscoveryGapEvent,
    JsonDiscoveryGapsStore,
    SqliteDiscoveryGapsStore,
    _hash_goal,
    _truncate_goal,
    build_discovery_gap_events,
    discovery_gaps_store_for_path,
    summarize_discovery_gaps,
)


class HelperFunctionTests(unittest.TestCase):
    def test_hash_goal_normalises_whitespace_and_case(self) -> None:
        # Two operationally-identical goals must produce the same hash
        # so the leaderboard can dedupe "asked again with a typo".
        self.assertEqual(
            _hash_goal("Some Example Goal Text"),
            _hash_goal("some example   goal    text"),
        )

    def test_hash_goal_returns_empty_for_empty_input(self) -> None:
        self.assertEqual(_hash_goal(""), "")
        self.assertEqual(_hash_goal("   "), "")

    def test_truncate_goal_short_string_unchanged(self) -> None:
        self.assertEqual(_truncate_goal("hello"), "hello")

    def test_truncate_goal_long_string_capped_with_ellipsis(self) -> None:
        long_text = "x" * 500
        out = _truncate_goal(long_text, max_chars=280)
        self.assertEqual(len(out), 280)
        self.assertTrue(out.endswith("\u2026"))


class BuildDiscoveryGapEventsTests(unittest.TestCase):
    def test_one_event_per_missing_capability(self) -> None:
        events = build_discovery_gap_events(
            goal="some user goal text",
            missing_capabilities=["cap_alpha", "cap_beta"],
            judge_accepted_by_capability={"cap_alpha": 0, "cap_beta": 0},
            judge_evaluated_by_capability={
                "cap_alpha": 11,
                "cap_beta": 0,
            },
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].capability_id, "cap_alpha")
        self.assertEqual(events[0].judge_evaluated, 11)
        self.assertEqual(events[0].judge_accepted, 0)
        self.assertEqual(events[1].capability_id, "cap_beta")
        self.assertEqual(events[1].judge_evaluated, 0)

    def test_blank_capability_ids_are_skipped(self) -> None:
        events = build_discovery_gap_events(
            goal="g",
            missing_capabilities=["", "  ", "real_cap"],
            judge_accepted_by_capability={"real_cap": 0},
            judge_evaluated_by_capability={"real_cap": 0},
        )
        self.assertEqual([e.capability_id for e in events], ["real_cap"])

    def test_empty_missing_capabilities_returns_empty(self) -> None:
        # No missing capabilities -> nothing to record. The /goal
        # route would otherwise log "0 events written" which is
        # noisy for the common executable-plan case.
        self.assertEqual(
            build_discovery_gap_events(
                goal="g",
                missing_capabilities=[],
                judge_accepted_by_capability={},
                judge_evaluated_by_capability={},
            ),
            [],
        )

    def test_optional_scout_dispatch_block_is_zero_filled(self) -> None:
        events = build_discovery_gap_events(
            goal="g",
            missing_capabilities=["x"],
            judge_accepted_by_capability={"x": 0},
            judge_evaluated_by_capability={"x": 0},
            scout_dispatch_by_capability=None,
        )
        self.assertEqual(events[0].scouts_dispatched, 0)
        self.assertEqual(events[0].scouts_returned_zero, 0)

    def test_scout_dispatch_block_is_used_when_provided(self) -> None:
        events = build_discovery_gap_events(
            goal="g",
            missing_capabilities=["cap_alpha"],
            judge_accepted_by_capability={"cap_alpha": 0},
            judge_evaluated_by_capability={"cap_alpha": 11},
            scout_dispatch_by_capability={
                "cap_alpha": {"dispatched": 6, "returned_zero": 5}
            },
        )
        self.assertEqual(events[0].scouts_dispatched, 6)
        self.assertEqual(events[0].scouts_returned_zero, 5)


class SummarizeDiscoveryGapsTests(unittest.TestCase):
    def _ev(
        self,
        cap: str,
        *,
        accepted: int = 0,
        goal_text: str = "g",
        observed: str = "2026-05-12T17:00:00+00:00",
    ) -> DiscoveryGapEvent:
        return DiscoveryGapEvent(
            capability_id=cap,
            goal_excerpt=goal_text,
            goal_hash=_hash_goal(goal_text),
            observed_at=observed,
            judge_evaluated=accepted + 5,
            judge_accepted=accepted,
        )

    def test_zero_yield_count_dominates_sort_order(self) -> None:
        # Capability A: 1 zero-yield event.
        # Capability B: 5 events but only 1 zero-yield.
        # A must sort below B because B has the same zero-yield (1)
        # but more activity (observation_count tiebreaker).
        events = [
            self._ev("A", accepted=0),
            self._ev("B", accepted=1),
            self._ev("B", accepted=1),
            self._ev("B", accepted=1),
            self._ev("B", accepted=1),
            self._ev("B", accepted=0),
        ]
        summaries = summarize_discovery_gaps(events)
        # B has zero_yield_count=1, observation_count=5
        # A has zero_yield_count=1, observation_count=1
        # Same zero_yield, so observation_count breaks the tie -> B first.
        self.assertEqual(summaries[0].capability_id, "B")
        self.assertEqual(summaries[1].capability_id, "A")

    def test_summary_uses_most_recent_distinct_goal_samples(self) -> None:
        # Sample goals must be the *most recent distinct* texts, not the
        # first-N. This matters because the public surface should show
        # current goal language, not week-1 fossils.
        events = [
            self._ev("X", goal_text="oldest", observed="2026-05-10T00:00:00+00:00"),
            self._ev("X", goal_text="duplicate", observed="2026-05-11T00:00:00+00:00"),
            self._ev("X", goal_text="duplicate", observed="2026-05-11T01:00:00+00:00"),
            self._ev("X", goal_text="newest", observed="2026-05-12T00:00:00+00:00"),
        ]
        summaries = summarize_discovery_gaps(events, sample_goals_per_capability=3)
        self.assertEqual(summaries[0].sample_goals, ["newest", "duplicate", "oldest"])
        self.assertEqual(summaries[0].observation_count, 4)
        # Three distinct goal_hash values (oldest, duplicate, newest).
        self.assertEqual(summaries[0].distinct_goal_count, 3)

    def test_blank_capability_ids_are_dropped(self) -> None:
        # Defensive: a row with an empty capability_id slipped through
        # should not show up as a "" tile on the leaderboard.
        events = [self._ev(""), self._ev("real")]
        summaries = summarize_discovery_gaps(events)
        self.assertEqual([s.capability_id for s in summaries], ["real"])

    def test_capability_id_sort_breaks_remaining_ties(self) -> None:
        events = [self._ev("zebra"), self._ev("alpha")]
        summaries = summarize_discovery_gaps(events)
        # Same zero_yield (1), same observation_count (1) -> alpha wins
        # by string sort. This makes the order deterministic across runs
        # which matters for log diffing and snapshot tests.
        self.assertEqual([s.capability_id for s in summaries], ["alpha", "zebra"])


class JsonDiscoveryGapsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name) / "events.jsonl"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_append_then_load_round_trips_events(self) -> None:
        store = JsonDiscoveryGapsStore(self.path)
        events = [
            DiscoveryGapEvent(
                capability_id="x",
                goal_excerpt="g",
                goal_hash="h",
                observed_at="2026-05-12T17:00:00+00:00",
                scouts_dispatched=6,
                scouts_returned_zero=5,
                judge_evaluated=11,
                judge_accepted=0,
            )
        ]
        store.append(events)

        loaded = store.load_all()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].capability_id, "x")
        self.assertEqual(loaded[0].scouts_dispatched, 6)
        self.assertEqual(loaded[0].judge_accepted, 0)

    def test_append_is_append_only_across_calls(self) -> None:
        store = JsonDiscoveryGapsStore(self.path)
        store.append(
            [
                DiscoveryGapEvent(
                    capability_id="a",
                    goal_excerpt="g1",
                    goal_hash="h1",
                    observed_at="2026-05-12T17:00:00+00:00",
                )
            ]
        )
        store.append(
            [
                DiscoveryGapEvent(
                    capability_id="b",
                    goal_excerpt="g2",
                    goal_hash="h2",
                    observed_at="2026-05-12T17:01:00+00:00",
                )
            ]
        )
        loaded = store.load_all()
        self.assertEqual([e.capability_id for e in loaded], ["a", "b"])

    def test_load_all_skips_corrupted_lines(self) -> None:
        # A partial write at process exit can leave a half-formed
        # JSON line; loading must not crash on it.
        with self.path.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "capability_id": "good",
                        "goal_excerpt": "g",
                        "goal_hash": "h",
                        "observed_at": "2026-05-12T17:00:00+00:00",
                    }
                )
                + "\n"
            )
            handle.write("{not-json\n")
            handle.write(
                json.dumps(
                    {
                        "capability_id": "also_good",
                        "goal_excerpt": "g",
                        "goal_hash": "h",
                        "observed_at": "2026-05-12T17:01:00+00:00",
                    }
                )
                + "\n"
            )
        store = JsonDiscoveryGapsStore(self.path)
        loaded = store.load_all()
        self.assertEqual([e.capability_id for e in loaded], ["good", "also_good"])

    def test_load_all_returns_empty_when_file_missing(self) -> None:
        # Fresh-install path: the file doesn't exist yet. Must return
        # [] not raise — the leaderboard tile renders a friendly
        # "no gap signal yet" empty state.
        store = JsonDiscoveryGapsStore(self.path)
        self.assertEqual(store.load_all(), [])

    def test_summarize_routes_through_aggregator(self) -> None:
        store = JsonDiscoveryGapsStore(self.path)
        store.append(
            [
                DiscoveryGapEvent(
                    capability_id="x",
                    goal_excerpt="g",
                    goal_hash="h",
                    observed_at="2026-05-12T17:00:00+00:00",
                    judge_accepted=0,
                )
            ]
        )
        summaries = store.summarize()
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].capability_id, "x")
        self.assertEqual(summaries[0].zero_yield_count, 1)


class SqliteDiscoveryGapsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name) / "events.sqlite"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_round_trip_preserves_all_fields(self) -> None:
        store = SqliteDiscoveryGapsStore(self.path)
        events = [
            DiscoveryGapEvent(
                capability_id="x",
                goal_excerpt="g",
                goal_hash="h",
                observed_at="2026-05-12T17:00:00+00:00",
                scouts_dispatched=6,
                scouts_returned_zero=5,
                judge_evaluated=11,
                judge_accepted=0,
            )
        ]
        store.append(events)
        loaded = store.load_all()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0], events[0])

    def test_schema_is_idempotent_across_reconstruction(self) -> None:
        # Constructing the store twice must not crash on duplicate
        # CREATE TABLE — apply_schema uses IF NOT EXISTS but a regression
        # could easily reintroduce a hard CREATE TABLE without it.
        SqliteDiscoveryGapsStore(self.path)
        SqliteDiscoveryGapsStore(self.path)
        # Sanity check: the table exists.
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='discovery_gap_events'"
            )
            self.assertEqual(cursor.fetchall(), [("discovery_gap_events",)])


class FactoryTests(unittest.TestCase):
    def test_jsonl_path_returns_json_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = discovery_gaps_store_for_path(Path(tmp) / "events.jsonl")
            self.assertIsInstance(store, JsonDiscoveryGapsStore)

    def test_sqlite_path_returns_sqlite_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = discovery_gaps_store_for_path(Path(tmp) / "events.sqlite")
            self.assertIsInstance(store, SqliteDiscoveryGapsStore)

    def test_postgres_dsn_returns_postgres_backend(self) -> None:
        # We can't actually connect from the unit test, but we can
        # assert the factory dispatches correctly. PostgresDiscoveryGapsStore
        # construction calls apply_schema() which contacts the DB, so
        # patch psycopg.connect to a no-op for this test.
        from unittest.mock import MagicMock, patch

        from planmyagents_api.discovery.discovery_gaps_store import (
            PostgresDiscoveryGapsStore,
        )

        with patch("psycopg.connect") as mock_connect:
            mock_connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = (
                MagicMock()
            )
            store = discovery_gaps_store_for_path("postgresql://localhost/x")
            self.assertIsInstance(store, PostgresDiscoveryGapsStore)


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
