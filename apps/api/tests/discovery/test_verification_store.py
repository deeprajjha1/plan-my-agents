from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.store import BENCHMARK_STORE_SCHEMA
from planmyagents_api.discovery.store import POSTGRES_DISCOVERY_STORE_SCHEMA
from planmyagents_api.discovery.verification import VerificationResult
from planmyagents_api.discovery.verification_store import (
    VERIFICATION_STORE_SCHEMA,
    JsonVerificationStore,
    VerificationRecord,
    verification_store_for_path,
)


class JsonVerificationStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name) / "verification.json"
        self.store = JsonVerificationStore(self.path)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_roundtrip_per_provider(self) -> None:
        a = VerificationRecord(
            provider_id="alpha",
            status="capability_verified",
            evidence_url="https://alpha.example/agent.json",
            verified_capabilities=["email_verification"],
            blockers=[],
            notes=["evidence_capabilities:email_verification"],
        )
        b = VerificationRecord(
            provider_id="alpha",
            status="known_provider",
            evidence_url="https://alpha.example/agent.json",
            verified_capabilities=[],
            blockers=["capability_evidence_missing"],
            notes=[],
        )
        c = VerificationRecord(
            provider_id="beta",
            status="unverified",
            evidence_url="",
            blockers=["evidence_url_missing"],
        )
        self.store.append([a, b, c])

        history_alpha = self.store.history_for("alpha")
        self.assertEqual(len(history_alpha), 2)
        latest_alpha = self.store.latest_for("alpha")
        self.assertIsNotNone(latest_alpha)
        # Latest is determined by ISO timestamp; we only assert it is one of ours.
        self.assertIn(
            latest_alpha["status"],
            {"capability_verified", "known_provider"},
        )

        history_beta = self.store.history_for("beta")
        self.assertEqual(len(history_beta), 1)
        self.assertEqual(history_beta[0]["status"], "unverified")

        self.assertIsNone(self.store.latest_for("ghost"))

    def test_from_verification_result_preserves_all_fields(self) -> None:
        result = VerificationResult(
            provider_id="gamma",
            status="capability_verified",
            evidence_url="https://gamma.example/agent.json",
            verified_capabilities=["semantic_search"],
            blockers=[],
            notes=["evidence_capabilities:semantic_search"],
        )
        record = VerificationRecord.from_result(result)
        self.assertEqual(record.provider_id, result.provider_id)
        self.assertEqual(record.status, result.status)
        self.assertEqual(record.evidence_url, result.evidence_url)
        self.assertEqual(record.verified_capabilities, result.verified_capabilities)
        self.assertEqual(record.notes, result.notes)
        # Round-trips through JSON.
        decoded = json.loads(json.dumps(record.to_json()))
        self.assertEqual(decoded["provider_id"], "gamma")

    def test_factory_returns_postgres_store_for_url(self) -> None:
        store = verification_store_for_path("postgresql://x@localhost/y")
        from planmyagents_api.discovery.verification_store import PostgresVerificationStore

        self.assertIsInstance(store, PostgresVerificationStore)


class SchemaParseTest(unittest.TestCase):
    """Cheap guards so schemas stay well-formed without needing a live Postgres."""

    def test_schemas_are_non_empty_and_idempotent_shapes(self) -> None:
        for name, schema in (
            ("discovery", POSTGRES_DISCOVERY_STORE_SCHEMA),
            ("benchmark", BENCHMARK_STORE_SCHEMA),
            ("verification", VERIFICATION_STORE_SCHEMA),
        ):
            statements = [s.strip() for s in schema.split(";") if s.strip()]
            self.assertGreater(len(statements), 0, f"{name} schema is empty")
            for statement in statements:
                upper = statement.upper()
                self.assertTrue(
                    upper.startswith(
                        (
                            "CREATE TABLE IF NOT EXISTS",
                            "CREATE INDEX IF NOT EXISTS",
                            "CREATE UNIQUE INDEX IF NOT EXISTS",
                            "CREATE EXTENSION IF NOT EXISTS",
                            "ALTER TABLE",
                        )
                    ),
                    f"{name} statement is not idempotent: {statement[:80]}",
                )


if __name__ == "__main__":
    unittest.main()
