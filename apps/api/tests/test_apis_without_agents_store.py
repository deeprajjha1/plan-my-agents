"""Unit tests for the apis_without_agents store + the routing facade.

Covers:

* `ApiWithoutAgentRecord` rejects agentic provider types (model invariant)
* `JsonApisWithoutAgentsStore` round-trips records and dedupes by key
* `SqliteApisWithoutAgentsStore` round-trips records and supports
  `mark_superseded`
* `record_from_discovery_candidate` correctly extracts only the fields
  the slim record needs
* `RoutingDiscoveryStore.save` partitions by provider_type — agentic
  rows go to `discovery_candidates`, api_provider rows go to
  `apis_without_agents`
* `RoutingDiscoveryStore.load` returns agent-only candidates even when
  legacy non-agentic rows still live in the agentic store
* `RoutingDiscoveryStore.load_apis_without_agents` falls back to legacy
  rows in the agentic store (so the surface is correct mid-migration)
* `RoutingDiscoveryStore.migrate_legacy_non_agentic_rows` is idempotent
* `RoutingDiscoveryStore.save_merge` does load-merge-save (preserves
  existing rows when the caller passes only new ones)
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from planmyagents_api.discovery.apis_without_agents_store import (  # noqa: E402
    ApiWithoutAgentRecord,
    JsonApisWithoutAgentsStore,
    SqliteApisWithoutAgentsStore,
    apis_without_agents_store_for_path,
    record_from_discovery_candidate,
)
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate  # noqa: E402
from planmyagents_api.discovery.store import (  # noqa: E402
    JsonDiscoveryStore,
    RoutingDiscoveryStore,
    discovery_store_for_path,
)


def _api_record(
    provider_id: str = "stripe-openapi",
    *,
    capabilities: tuple[str, ...] = ("payment_authorization",),
    provider_type: str = "api_provider",
) -> ApiWithoutAgentRecord:
    return ApiWithoutAgentRecord(
        dedupe_key=provider_id,
        provider_id=provider_id,
        display_name=provider_id.replace("-", " ").title(),
        vendor=provider_id.split("-")[0],
        vendor_url=f"https://{provider_id}.example",
        provider_type=provider_type,
        openapi_url=f"https://{provider_id}.example/openapi.json",
        capabilities=capabilities,
        source_id="apis_guru",
        first_seen_at="2026-05-01",
        last_seen_at="2026-05-01",
    )


def _agentic_candidate(
    provider_id: str = "acme-mcp",
    capability: str = "payment_authorization",
    provider_type: str = "mcp_server",
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=provider_id,
        display_name=provider_id.replace("-", " ").title(),
        vendor="acme",
        vendor_url="https://acme.example",
        provider_type=provider_type,
        capabilities=[CandidateCapability(id=capability, confidence=0.9)],
        source="curated",
    )


def _api_provider_candidate(
    provider_id: str = "stripe-openapi", capability: str = "payment_authorization"
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=provider_id,
        display_name=provider_id.replace("-", " ").title(),
        vendor=provider_id.split("-")[0],
        vendor_url=f"https://{provider_id}.example",
        provider_type="api_provider",
        capabilities=[CandidateCapability(id=capability, confidence=0.6)],
        evidence_url=f"https://{provider_id}.example/openapi.json",
        source="apis_guru",
    )


class ApiWithoutAgentRecordTests(unittest.TestCase):
    def test_rejects_agentic_provider_types(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            ApiWithoutAgentRecord(
                dedupe_key="x",
                provider_id="x",
                display_name="X",
                vendor="x",
                vendor_url="",
                provider_type="mcp_server",
                openapi_url="",
                capabilities=("foo",),
                source_id="",
                first_seen_at="2026-05-01",
                last_seen_at="2026-05-01",
            )
        self.assertIn("provider_type", str(ctx.exception))

    def test_accepts_api_provider_and_payment_provider(self) -> None:
        for provider_type in ("api_provider", "payment_provider"):
            record = _api_record(provider_type=provider_type)
            self.assertEqual(record.provider_type, provider_type)

    def test_to_json_round_trips(self) -> None:
        original = _api_record()
        round_tripped = ApiWithoutAgentRecord.from_json(original.to_json())
        self.assertEqual(original, round_tripped)


class RecordFromCandidateTests(unittest.TestCase):
    def test_only_extracts_slim_fields(self) -> None:
        candidate = _api_provider_candidate()
        record = record_from_discovery_candidate(candidate, dedupe_key="stripe-openapi")
        self.assertEqual(record.provider_id, "stripe-openapi")
        self.assertEqual(record.openapi_url, "https://stripe-openapi.example/openapi.json")
        self.assertEqual(record.capabilities, ("payment_authorization",))
        # Slim record doesn't have will_fail / benchmark_status / adapter_module
        self.assertFalse(hasattr(record, "will_fail"))
        self.assertFalse(hasattr(record, "benchmark_status"))
        self.assertFalse(hasattr(record, "adapter_module"))

    def test_rejects_agentic_candidate(self) -> None:
        agentic = _agentic_candidate()
        with self.assertRaises(ValueError):
            record_from_discovery_candidate(agentic, dedupe_key="acme-mcp")


class JsonStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.path = self.tempdir / "apis.json"
        self.store = JsonApisWithoutAgentsStore(self.path)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_save_and_load_round_trips(self) -> None:
        records = [_api_record("stripe-openapi"), _api_record("plaid-openapi")]
        self.store.save(records)
        loaded = self.store.load()
        loaded_ids = sorted(r.provider_id for r in loaded)
        self.assertEqual(loaded_ids, ["plaid-openapi", "stripe-openapi"])

    def test_dedupes_by_dedupe_key(self) -> None:
        # Two records with the same dedupe_key — second should win,
        # capabilities should be merged.
        first = _api_record("stripe-openapi", capabilities=("payment_authorization",))
        second = _api_record("stripe-openapi", capabilities=("payment_capture",))
        self.store.save([first, second])
        loaded = self.store.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(
            sorted(loaded[0].capabilities),
            ["payment_authorization", "payment_capture"],
        )

    def test_empty_load_when_file_missing(self) -> None:
        self.assertEqual(self.store.load(), [])


class SqliteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.path = self.tempdir / "apis.sqlite"
        self.store = SqliteApisWithoutAgentsStore(self.path)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_save_and_load_round_trips(self) -> None:
        self.store.save([_api_record("stripe-openapi")])
        loaded = self.store.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].provider_id, "stripe-openapi")

    def test_mark_superseded_updates_field(self) -> None:
        self.store.save([_api_record("stripe-openapi")])
        self.store.mark_superseded("stripe-openapi", "acme-stripe-mcp")
        loaded = self.store.load()
        self.assertEqual(loaded[0].superseded_by_provider_id, "acme-stripe-mcp")

    def test_save_replaces_previous_set(self) -> None:
        # SQLite store uses DELETE-then-INSERT, matching the agentic
        # SQLite store. This is documented behaviour.
        self.store.save([_api_record("stripe-openapi")])
        self.store.save([_api_record("plaid-openapi")])
        loaded_ids = [r.provider_id for r in self.store.load()]
        self.assertEqual(loaded_ids, ["plaid-openapi"])


class StoreFactoryTests(unittest.TestCase):
    def test_postgres_url_returns_postgres_backend(self) -> None:
        from planmyagents_api.discovery.apis_without_agents_store import (
            PostgresApisWithoutAgentsStore,
        )

        store = apis_without_agents_store_for_path(
            "postgresql://x:y@localhost:5432/db"
        )
        self.assertIsInstance(store, PostgresApisWithoutAgentsStore)

    def test_sqlite_path_returns_sqlite_backend(self) -> None:
        store = apis_without_agents_store_for_path(Path("/tmp/x.sqlite"))
        self.assertIsInstance(store, SqliteApisWithoutAgentsStore)

    def test_json_path_returns_json_backend(self) -> None:
        store = apis_without_agents_store_for_path(Path("/tmp/x.json"))
        self.assertIsInstance(store, JsonApisWithoutAgentsStore)


class RoutingFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery-store.json"
        self.facade = discovery_store_for_path(self.discovery_path)
        self.assertIsInstance(self.facade, RoutingDiscoveryStore)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_save_routes_agentic_to_discovery_candidates_and_api_to_apis_table(self) -> None:
        agentic = _agentic_candidate("acme-mcp", capability="email_verification")
        api = _api_provider_candidate("hunter-openapi", capability="email_verification")

        self.facade.save([agentic, api])

        # Agent-only loader returns only the agentic row
        agentic_loaded = self.facade.load()
        self.assertEqual([c.id for c in agentic_loaded], ["acme-mcp"])

        # APIs loader returns only the API row
        api_loaded = self.facade.load_apis_without_agents()
        self.assertEqual([r.provider_id for r in api_loaded], ["hunter-openapi"])

    def test_load_filters_legacy_non_agentic_rows_from_agentic_store(self) -> None:
        # Simulate a stale dev SQLite/JSON file that pre-dates the
        # split: an api_provider row sitting in the agentic store.
        # The facade load() must filter it out so /leaderboards etc.
        # never see it.
        underlying = JsonDiscoveryStore(self.discovery_path)
        underlying.save([
            _agentic_candidate("acme-mcp"),
            _api_provider_candidate("stripe-openapi"),
        ])
        loaded = self.facade.load()
        loaded_types = {c.provider_type for c in loaded}
        self.assertEqual(loaded_types, {"mcp_server"})
        self.assertEqual([c.id for c in loaded], ["acme-mcp"])

    def test_load_apis_without_agents_falls_back_to_legacy_rows(self) -> None:
        # Mid-migration state: api_provider rows still in the agentic
        # store, dedicated apis_without_agents store empty. The Open
        # MCP Opportunities page should still see the rows.
        underlying = JsonDiscoveryStore(self.discovery_path)
        underlying.save([_api_provider_candidate("stripe-openapi")])

        api_loaded = self.facade.load_apis_without_agents()
        self.assertEqual(len(api_loaded), 1)
        self.assertEqual(api_loaded[0].provider_id, "stripe-openapi")

    def test_migrate_lifts_legacy_rows_into_dedicated_store(self) -> None:
        underlying = JsonDiscoveryStore(self.discovery_path)
        underlying.save([
            _agentic_candidate("acme-mcp"),
            _api_provider_candidate("stripe-openapi"),
        ])
        migrated = self.facade.migrate_legacy_non_agentic_rows()
        self.assertEqual(migrated, 1)

        # After migration the agentic store is agent-only
        agentic_after = JsonDiscoveryStore(self.discovery_path).load()
        self.assertEqual([c.id for c in agentic_after], ["acme-mcp"])

        # And the dedicated APIs store has the row
        api_after = self.facade.load_apis_without_agents()
        self.assertEqual([r.provider_id for r in api_after], ["stripe-openapi"])

    def test_migrate_is_idempotent(self) -> None:
        underlying = JsonDiscoveryStore(self.discovery_path)
        underlying.save([
            _agentic_candidate("acme-mcp"),
            _api_provider_candidate("stripe-openapi"),
        ])
        first = self.facade.migrate_legacy_non_agentic_rows()
        second = self.facade.migrate_legacy_non_agentic_rows()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_save_merge_preserves_existing_rows(self) -> None:
        # Initial set: one agentic row, one api row.
        self.facade.save([
            _agentic_candidate("alpha-mcp", capability="alpha"),
            _api_provider_candidate("a-api", capability="alpha"),
        ])

        # Caller has only NEW candidates from a live discovery run.
        # Plain .save would wipe the previous rows; .save_merge keeps them.
        self.facade.save_merge([
            _agentic_candidate("beta-mcp", capability="beta"),
        ])

        agentic_ids = sorted(c.id for c in self.facade.load())
        self.assertEqual(agentic_ids, ["alpha-mcp", "beta-mcp"])

        api_ids = sorted(r.provider_id for r in self.facade.load_apis_without_agents())
        self.assertEqual(api_ids, ["a-api"])


if __name__ == "__main__":
    unittest.main()
