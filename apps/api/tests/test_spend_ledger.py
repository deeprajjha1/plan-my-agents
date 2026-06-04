"""Tests for the spend ledger backends."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from planmyagents_api.cost.spend_ledger import (
    DEFAULT_SPEND_LEDGER_RELATIVE_PATH,
    JsonSpendLedger,
    SpendEvent,
    resolve_spend_ledger_path,
    spend_ledger_for_path,
    total_spend,
)


class SpendEventValidationTests(unittest.TestCase):
    def test_negative_cost_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SpendEvent(goal_hash="g", capability="email_send", provider_id="x", cost_usd=-0.01)

    def test_blank_capability_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SpendEvent(goal_hash="g", capability="", provider_id="x", cost_usd=0.01)

    def test_blank_provider_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SpendEvent(goal_hash="g", capability="x", provider_id="", cost_usd=0.01)

    def test_round_trip(self) -> None:
        original = SpendEvent(
            goal_hash="abc",
            capability="email_verification",
            provider_id="hunter",
            cost_usd=0.005,
            idempotency_key="wf:1:foo",
        )
        roundtrip = SpendEvent.from_json(original.to_json())
        self.assertEqual(original, roundtrip)


class JsonSpendLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name) / "spend.jsonl"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_empty_ledger_returns_zero(self) -> None:
        ledger = JsonSpendLedger(self.path)
        self.assertEqual(ledger.load_all(), [])
        self.assertEqual(ledger.spend_today_usd(), 0.0)

    def test_append_and_load(self) -> None:
        ledger = JsonSpendLedger(self.path)
        events = [
            SpendEvent(goal_hash="g1", capability="a", provider_id="p", cost_usd=0.01),
            SpendEvent(goal_hash="g2", capability="b", provider_id="q", cost_usd=0.02),
        ]
        ledger.append(events)
        loaded = ledger.load_all()
        self.assertEqual(len(loaded), 2)
        self.assertEqual({e.capability for e in loaded}, {"a", "b"})

    def test_spend_today_usd_filters_by_date(self) -> None:
        ledger = JsonSpendLedger(self.path)
        # Manually craft events on different days. spend_today_usd
        # honours occurred_date verbatim — that's the contract.
        today_event = SpendEvent(
            goal_hash="g", capability="x", provider_id="p",
            cost_usd=0.10, occurred_date="2026-05-15",
        )
        yesterday_event = SpendEvent(
            goal_hash="g", capability="x", provider_id="p",
            cost_usd=0.05, occurred_date="2026-05-14",
        )
        ledger.append([today_event, yesterday_event])
        self.assertEqual(ledger.spend_today_usd(today="2026-05-15"), 0.10)
        self.assertEqual(ledger.spend_today_usd(today="2026-05-14"), 0.05)
        self.assertEqual(ledger.spend_today_usd(today="2026-05-13"), 0.0)

    def test_corrupted_lines_skipped(self) -> None:
        # Manually write a file with one good event and two bad lines.
        # The loader should skip the bad lines, not crash.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as h:
            h.write("not-json\n")
            h.write(json.dumps({"capability": "a", "provider_id": "p", "cost_usd": 0.01}) + "\n")
            h.write("{\"capability\":\"\"}\n")  # blank capability fails validation in from_json
        ledger = JsonSpendLedger(self.path)
        events = ledger.load_all()
        # The middle line is the only valid one; corrupted ones are dropped.
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].capability, "a")

    def test_appends_are_concatenated(self) -> None:
        ledger = JsonSpendLedger(self.path)
        ledger.append([SpendEvent(goal_hash="g", capability="a", provider_id="p", cost_usd=0.01)])
        ledger.append([SpendEvent(goal_hash="g", capability="b", provider_id="q", cost_usd=0.02)])
        loaded = ledger.load_all()
        self.assertEqual(len(loaded), 2)


class SpendLedgerFactoryTests(unittest.TestCase):
    def test_path_picks_json_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spend.jsonl"
            ledger = spend_ledger_for_path(path)
        self.assertIsInstance(ledger, JsonSpendLedger)

    def test_postgres_dsn_picks_postgres_backend(self) -> None:
        # Don't actually connect — just check the dispatch.
        from planmyagents_api.cost.spend_ledger import PostgresSpendLedger

        ledger = spend_ledger_for_path("postgresql://localhost/foo")
        self.assertIsInstance(ledger, PostgresSpendLedger)
        self.assertEqual(ledger.dsn, "postgresql://localhost/foo")

    def test_resolve_uses_env_when_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "custom.jsonl"
            os.environ["PLANMYAGENTS_SPEND_LEDGER_PATH"] = str(target)
            try:
                self.assertEqual(resolve_spend_ledger_path(), str(target))
            finally:
                del os.environ["PLANMYAGENTS_SPEND_LEDGER_PATH"]

    def test_resolve_default_is_repo_relative(self) -> None:
        os.environ.pop("PLANMYAGENTS_SPEND_LEDGER_PATH", None)
        path = Path(resolve_spend_ledger_path())
        # The default relative tail should be present even though the
        # absolute prefix depends on where the repo is checked out.
        self.assertTrue(
            str(path).endswith(str(DEFAULT_SPEND_LEDGER_RELATIVE_PATH)),
            f"unexpected default path: {path!r}",
        )


class TotalSpendHelperTests(unittest.TestCase):
    def test_sum_handles_empty(self) -> None:
        self.assertEqual(total_spend([]), 0.0)

    def test_sum_aggregates(self) -> None:
        events = [
            SpendEvent(goal_hash="g", capability="a", provider_id="p", cost_usd=0.10),
            SpendEvent(goal_hash="g", capability="b", provider_id="q", cost_usd=0.05),
        ]
        self.assertAlmostEqual(total_spend(events), 0.15)


if __name__ == "__main__":
    unittest.main()
