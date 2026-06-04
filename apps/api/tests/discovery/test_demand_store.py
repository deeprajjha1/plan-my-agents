"""Unit tests for the capability-demand log store and recorder."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.demand_recorder import (
    demand_recording_enabled,
    record_refusal_demand,
)
from planmyagents_api.discovery.demand_store import (
    DemandEvent,
    JsonDemandStore,
    SqliteDemandStore,
    build_demand_events_for_request,
    demand_store_for_path,
)


class DemandEventBuilderTests(unittest.TestCase):
    def test_build_one_event_per_capability(self) -> None:
        events = build_demand_events_for_request(
            goal="I want to verify emails AND check phone numbers.",
            missing_capabilities=["email_verification", "phone_lookup"],
            candidates_by_capability={"email_verification": [{"provider_id": "x"}]},
            apis_without_agents_by_capability={"phone_lookup": [{"provider_id": "y"}]},
            requester="203.0.113.5",
        )
        self.assertEqual(len(events), 2)
        ids = sorted(e.capability_id for e in events)
        self.assertEqual(ids, ["email_verification", "phone_lookup"])
        email_event = next(e for e in events if e.capability_id == "email_verification")
        self.assertTrue(email_event.has_local_match)
        self.assertFalse(email_event.has_apis_without_agents)
        phone_event = next(e for e in events if e.capability_id == "phone_lookup")
        self.assertFalse(phone_event.has_local_match)
        self.assertTrue(phone_event.has_apis_without_agents)
        # Requester is hashed, never the raw IP.
        self.assertNotEqual(email_event.requester_hash, "203.0.113.5")
        self.assertEqual(len(email_event.requester_hash), 16)

    def test_truncates_long_goal_text(self) -> None:
        long_goal = "x" * 600
        events = build_demand_events_for_request(
            goal=long_goal,
            missing_capabilities=["foo"],
            candidates_by_capability={},
            apis_without_agents_by_capability={},
            requester=None,
        )
        self.assertLessEqual(len(events[0].goal_excerpt), 281)
        self.assertEqual(events[0].requester_hash, "")

    def test_empty_capabilities_returns_empty_list(self) -> None:
        events = build_demand_events_for_request(
            goal="anything",
            missing_capabilities=[],
            candidates_by_capability={},
            apis_without_agents_by_capability={},
            requester=None,
        )
        self.assertEqual(events, [])

    def test_blank_capability_ids_skipped(self) -> None:
        events = build_demand_events_for_request(
            goal="anything",
            missing_capabilities=["", "  ", "real"],
            candidates_by_capability={},
            apis_without_agents_by_capability={},
            requester=None,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].capability_id, "real")


class JsonDemandStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name) / "demand.jsonl"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_append_and_load_roundtrip(self) -> None:
        store = JsonDemandStore(self.path)
        store.append(
            [
                DemandEvent(
                    capability_id="cross_border_commerce",
                    goal_excerpt="Buy a house in Dubai with crypto",
                    requester_hash="abc123def456",
                    requested_at="2026-05-11T10:00:00+00:00",
                    has_local_match=False,
                    has_apis_without_agents=True,
                ),
                DemandEvent(
                    capability_id="cross_border_commerce",
                    goal_excerpt="Send USDC to a vendor in Argentina",
                    requester_hash="abc123def456",
                    requested_at="2026-05-11T10:30:00+00:00",
                    has_local_match=False,
                    has_apis_without_agents=False,
                ),
            ]
        )
        loaded = store.load_all()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].capability_id, "cross_border_commerce")

    def test_summarize_ranks_by_request_count(self) -> None:
        store = JsonDemandStore(self.path)
        store.append(
            [
                DemandEvent("alpha", "Goal A1", "h1", "2026-01-01T00:00:00+00:00", False, True),
                DemandEvent("alpha", "Goal A2", "h2", "2026-01-02T00:00:00+00:00", False, True),
                DemandEvent("alpha", "Goal A3", "h3", "2026-01-03T00:00:00+00:00", False, True),
                DemandEvent("beta",  "Goal B1", "h1", "2026-01-01T00:00:00+00:00", True,  False),
            ]
        )
        summaries = store.summarize()
        self.assertEqual(summaries[0].capability_id, "alpha")
        self.assertEqual(summaries[0].request_count, 3)
        self.assertEqual(summaries[0].distinct_requester_count, 3)
        self.assertEqual(summaries[0].has_apis_without_agents_count, 3)
        self.assertEqual(summaries[1].capability_id, "beta")
        self.assertEqual(summaries[1].request_count, 1)
        self.assertEqual(summaries[1].has_local_match_count, 1)

    def test_summarize_returns_freshest_sample_goals_first(self) -> None:
        store = JsonDemandStore(self.path)
        store.append(
            [
                DemandEvent("foo", "Old goal", "", "2026-01-01T00:00:00+00:00", False, False),
                DemandEvent("foo", "Mid goal", "", "2026-02-01T00:00:00+00:00", False, False),
                DemandEvent("foo", "New goal", "", "2026-03-01T00:00:00+00:00", False, False),
            ]
        )
        summary = store.summarize(sample_goals_per_capability=2)[0]
        self.assertEqual(summary.sample_goals, ["New goal", "Mid goal"])

    def test_load_all_skips_corrupted_lines(self) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    DemandEvent("good", "g", "", "2026-01-01T00:00:00+00:00", False, False).to_json()
                )
                + "\n"
            )
            fh.write("not-json\n")
            fh.write("\n")  # blank line
        loaded = JsonDemandStore(self.path).load_all()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].capability_id, "good")


class SqliteDemandStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name) / "demand.sqlite"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_sqlite_roundtrip_and_summarize(self) -> None:
        store = SqliteDemandStore(self.path)
        store.append(
            [
                DemandEvent("k", "g1", "h", "2026-01-01T00:00:00+00:00", False, True),
                DemandEvent("k", "g2", "h", "2026-01-02T00:00:00+00:00", False, True),
            ]
        )
        loaded = store.load_all()
        self.assertEqual(len(loaded), 2)
        summaries = store.summarize()
        self.assertEqual(summaries[0].request_count, 2)


class StoreFactoryTests(unittest.TestCase):
    def test_factory_picks_sqlite_for_db_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = demand_store_for_path(Path(tempdir) / "x.sqlite")
            self.assertIsInstance(store, SqliteDemandStore)

    def test_factory_picks_json_for_jsonl_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = demand_store_for_path(Path(tempdir) / "x.jsonl")
            self.assertIsInstance(store, JsonDemandStore)


class DemandRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name) / "demand.jsonl"
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = str(self.path)
        os.environ.pop("PLANMYAGENTS_DEMAND_RECORDING_ENABLED", None)

    def tearDown(self) -> None:
        os.environ.pop("PLANMYAGENTS_DEMAND_STORE_PATH", None)
        os.environ.pop("PLANMYAGENTS_DEMAND_RECORDING_ENABLED", None)
        self._tempdir.cleanup()

    def test_recorder_writes_events_to_configured_store(self) -> None:
        written = record_refusal_demand(
            goal="I want a Pakistan KYC agent.",
            missing_capabilities=["kyc_aml_check"],
            candidates_by_capability={},
            apis_without_agents_by_capability={"kyc_aml_check": [{"provider_id": "trulioo"}]},
            requester="user-session-1",
        )
        self.assertEqual(written, 1)
        loaded = JsonDemandStore(self.path).load_all()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].capability_id, "kyc_aml_check")
        self.assertTrue(loaded[0].has_apis_without_agents)
        # Requester is hashed.
        self.assertNotEqual(loaded[0].requester_hash, "user-session-1")

    def test_recorder_disabled_via_env(self) -> None:
        os.environ["PLANMYAGENTS_DEMAND_RECORDING_ENABLED"] = "false"
        self.assertFalse(demand_recording_enabled())
        written = record_refusal_demand(
            goal="anything",
            missing_capabilities=["foo"],
            candidates_by_capability={},
            apis_without_agents_by_capability={},
            requester=None,
        )
        self.assertEqual(written, 0)
        self.assertFalse(self.path.exists())

    def test_recorder_no_op_when_no_missing_capabilities(self) -> None:
        written = record_refusal_demand(
            goal="anything",
            missing_capabilities=[],
            candidates_by_capability={},
            apis_without_agents_by_capability={},
            requester=None,
        )
        self.assertEqual(written, 0)

    def test_recorder_swallows_store_failures(self) -> None:
        # Point to an unwritable location to force failure on append.
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = "/proc/this/path/cannot/exist/demand.jsonl"
        # Should not raise.
        result = record_refusal_demand(
            goal="anything",
            missing_capabilities=["foo"],
            candidates_by_capability={},
            apis_without_agents_by_capability={},
            requester=None,
        )
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
