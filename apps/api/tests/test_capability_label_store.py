"""Unit tests for ``capability_label_store`` (JSON + SQLite backends).

The Postgres backend is exercised via the live ``/capability-labels``
integration tests against the dev container; here we focus on the
pure-Python backends so the suite stays hermetic.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from planmyagents_api.planner.capability_label_store import (
    CapabilityLabel,
    JsonCapabilityLabelStore,
    SqliteCapabilityLabelStore,
    capability_label_store_for_path,
)


class JsonCapabilityLabelStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "labels.json"

    def test_first_upsert_inserts_with_usage_count_one(self) -> None:
        store = JsonCapabilityLabelStore(self.path)
        row = store.upsert(
            id="email_dispatch",
            description="Send bulk email to a recipient list.",
            coined_from_goal_hash="abc123",
        )
        self.assertEqual(row.id, "email_dispatch")
        self.assertEqual(row.description, "Send bulk email to a recipient list.")
        self.assertEqual(row.usage_count, 1)
        self.assertEqual(row.coined_from_goal_hash, "abc123")
        self.assertEqual(store.list_ids(), {"email_dispatch"})

    def test_second_upsert_bumps_usage_count_and_preserves_definition(self) -> None:
        store = JsonCapabilityLabelStore(self.path)
        store.upsert(
            id="email_dispatch",
            description="Original description.",
            coined_from_goal_hash="hash-A",
        )
        row = store.upsert(
            id="email_dispatch",
            description="A different attempted description.",
            coined_from_goal_hash="hash-B",
        )
        self.assertEqual(row.usage_count, 2)
        # Definition fields are owned by the first coining and never
        # overwritten on subsequent reuse — that's the contract the
        # reconciler relies on for stable analytics.
        self.assertEqual(row.description, "Original description.")
        self.assertEqual(row.coined_from_goal_hash, "hash-A")

    def test_id_normalisation_strips_and_lowercases(self) -> None:
        store = JsonCapabilityLabelStore(self.path)
        a = store.upsert(id="  Email_Dispatch  ", description="x")
        b = store.upsert(id="email_dispatch", description="y")
        self.assertEqual(a.id, "email_dispatch")
        self.assertEqual(b.usage_count, 2)

    def test_empty_id_raises_value_error(self) -> None:
        store = JsonCapabilityLabelStore(self.path)
        with self.assertRaises(ValueError):
            store.upsert(id="   ", description="x")

    def test_get_returns_none_for_missing_id(self) -> None:
        store = JsonCapabilityLabelStore(self.path)
        self.assertIsNone(store.get("never_coined"))
        store.upsert(id="email_dispatch", description="x")
        self.assertIsNone(store.get("email_dispatch_2"))
        self.assertIsNotNone(store.get("email_dispatch"))

    def test_list_all_is_id_sorted(self) -> None:
        store = JsonCapabilityLabelStore(self.path)
        for cid in ["zeta_capability", "alpha_capability", "mu_capability"]:
            store.upsert(id=cid, description="x")
        ids = [label.id for label in store.list_all()]
        self.assertEqual(ids, ["alpha_capability", "mu_capability", "zeta_capability"])

    def test_corrupt_file_returns_empty_set_without_raising(self) -> None:
        self.path.write_text("not json at all", encoding="utf-8")
        store = JsonCapabilityLabelStore(self.path)
        # The store's read path must never crash the planner.
        self.assertEqual(store.list_ids(), set())
        self.assertEqual(store.list_all(), [])
        # The next UPSERT cleanly rewrites the file.
        store.upsert(id="email_dispatch", description="x")
        self.assertEqual(store.list_ids(), {"email_dispatch"})

    def test_atomic_write_does_not_leave_tmp_file_behind(self) -> None:
        store = JsonCapabilityLabelStore(self.path)
        store.upsert(id="email_dispatch", description="x")
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        self.assertFalse(tmp_path.exists())

    def test_round_trip_through_disk_preserves_all_fields(self) -> None:
        store = JsonCapabilityLabelStore(self.path)
        store.upsert(
            id="bulk_outreach",
            description="Reach out to many targets in one operation.",
            coined_from_goal_hash="goal-hash-7",
        )
        # Re-instantiate against the same file.
        reopened = JsonCapabilityLabelStore(self.path)
        row = reopened.get("bulk_outreach")
        self.assertIsNotNone(row)
        assert row is not None  # narrow for type-checker
        self.assertEqual(row.description, "Reach out to many targets in one operation.")
        self.assertEqual(row.coined_from_goal_hash, "goal-hash-7")
        self.assertEqual(row.usage_count, 1)

    def test_disk_layout_is_a_json_array(self) -> None:
        store = JsonCapabilityLabelStore(self.path)
        store.upsert(id="bulk_outreach", description="x")
        store.upsert(id="alpha_first", description="y")
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsInstance(on_disk, list)
        self.assertEqual([row["id"] for row in on_disk], ["alpha_first", "bulk_outreach"])


class SqliteCapabilityLabelStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "labels.sqlite"

    def test_upsert_increments_usage_count(self) -> None:
        store = SqliteCapabilityLabelStore(self.path)
        store.upsert(id="email_dispatch", description="x", coined_from_goal_hash="g1")
        row = store.upsert(id="email_dispatch", description="y", coined_from_goal_hash="g2")
        self.assertEqual(row.usage_count, 2)
        # Definition stays as-coined the first time.
        self.assertEqual(row.description, "x")
        self.assertEqual(row.coined_from_goal_hash, "g1")

    def test_get_returns_inserted_row(self) -> None:
        store = SqliteCapabilityLabelStore(self.path)
        store.upsert(id="bulk_outreach", description="reach many targets")
        row = store.get("bulk_outreach")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.description, "reach many targets")

    def test_list_ids_returns_normalised_ids(self) -> None:
        store = SqliteCapabilityLabelStore(self.path)
        store.upsert(id="ZETA_capability", description="x")
        store.upsert(id="alpha", description="y")
        self.assertEqual(store.list_ids(), {"zeta_capability", "alpha"})


class FactoryTests(unittest.TestCase):
    def test_factory_picks_json_for_plain_path(self) -> None:
        with TemporaryDirectory() as tmp:
            store = capability_label_store_for_path(Path(tmp) / "labels.json")
            self.assertIsInstance(store, JsonCapabilityLabelStore)

    def test_factory_picks_sqlite_for_sqlite_extension(self) -> None:
        with TemporaryDirectory() as tmp:
            store = capability_label_store_for_path(Path(tmp) / "labels.sqlite")
            self.assertIsInstance(store, SqliteCapabilityLabelStore)

    def test_factory_picks_postgres_for_dsn(self) -> None:
        # Construction would normally apply the schema; we pass a
        # bogus DSN and expect the constructor to fail at apply time
        # so we only test the dispatch by intercepting the import
        # path. To keep this hermetic we just assert the type via the
        # class name on the dispatch. Postgres backend is exercised
        # for real in the live integration script.
        from planmyagents_api.planner.capability_label_store import (
            PostgresCapabilityLabelStore,
        )

        # We can't construct a Postgres store without a running DB,
        # so we monkey-patch ``apply_schema`` to a no-op for this
        # test only.
        original = PostgresCapabilityLabelStore.apply_schema
        try:
            PostgresCapabilityLabelStore.apply_schema = lambda self: None  # type: ignore[assignment]
            store = capability_label_store_for_path(
                "postgresql://user:pw@localhost:5432/db"
            )
            self.assertIsInstance(store, PostgresCapabilityLabelStore)
        finally:
            PostgresCapabilityLabelStore.apply_schema = original  # type: ignore[assignment]


class CapabilityLabelDataclassTests(unittest.TestCase):
    def test_to_json_round_trip(self) -> None:
        original = CapabilityLabel(
            id="bulk_outreach",
            description="Reach out to many targets at once.",
            coined_at="2026-05-01T00:00:00+00:00",
            coined_from_goal_hash="abc123",
            usage_count=3,
            last_used_at="2026-05-12T00:00:00+00:00",
        )
        rebuilt = CapabilityLabel.from_json(original.to_json())
        self.assertEqual(rebuilt, original)


if __name__ == "__main__":
    unittest.main()
