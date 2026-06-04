from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.credibility import _is_real_run
from planmyagents_api.discovery.card_ingestion import (
    CardIngestionService,
    IngestionResult,
)
from planmyagents_api.discovery.run_log import DiscoveryRunLogger
from planmyagents_api.discovery.store import discovery_store_for_path

from tests.fixtures.agent_card_server import AgentCardServer

# Allow http:// for the in-process stub (production stays https-only).
_TEST_SCHEMES = frozenset({"https", "http"})


def _card(*, name: str = "PolicyCheck", skill: str = "web_scraping") -> dict:
    """An A2A-style agent card. The skill name is a registry capability so
    verify_candidate can confirm the claim from the card evidence."""

    return {
        "id": name.lower(),
        "name": name,
        "displayName": name,
        "url": "https://example.invalid/agent",
        "provider": {"organization": name, "url": "https://example.invalid"},
        "skills": [
            {
                "id": skill,
                "name": skill,
                "description": f"{skill} skill",
                "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
            }
        ],
    }


def _service(tmp: Path, **kwargs) -> CardIngestionService:
    return CardIngestionService(
        discovery_store_url=str(tmp / "discovery.json"),
        verification_store_url=str(tmp / "verification.json"),
        run_logger=DiscoveryRunLogger(store=None, enabled=False),
        allowed_schemes=_TEST_SCHEMES,
        **kwargs,
    )


class CardIngestionResultTest(unittest.TestCase):
    def test_ingestion_result_round_trips(self) -> None:
        result = IngestionResult(
            provider_id="p1", card_url="https://x/agent.json", resolved=True
        )
        payload = result.to_json()
        self.assertEqual(payload["provider_id"], "p1")
        self.assertEqual(payload["benchmark_status"], "not_started")
        self.assertFalse(payload["routable"])


class CardIngestionUrlGuardTest(unittest.TestCase):
    def test_non_https_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Production default: only https allowed.
            svc = CardIngestionService(
                discovery_store_url=str(Path(tmp) / "d.json"),
                verification_store_url=str(Path(tmp) / "v.json"),
                run_logger=DiscoveryRunLogger(store=None, enabled=False),
            )
            result = svc.ingest("http://policycheck.tools/.well-known/agent.json")
            self.assertFalse(result.resolved)
            self.assertEqual(result.error, "non_https_url")

    def test_empty_url_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _service(Path(tmp)).ingest("")
            self.assertFalse(result.resolved)
            self.assertEqual(result.error, "empty_url")


class CardIngestionResolveTest(unittest.TestCase):
    def test_resolves_and_verifies_any_published_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, AgentCardServer(
            _card(name="PolicyCheck", skill="web_scraping")
        ) as base_url:
            svc = _service(Path(tmp))
            result = svc.ingest(f"{base_url}/.well-known/agent.json")

            self.assertTrue(result.resolved, result.error)
            self.assertEqual(result.provider_id, "policycheck")
            self.assertIn("web_scraping", result.declared_capabilities)
            # Card advertises the skill → existence/claim verified.
            self.assertEqual(result.verification_status, "capability_verified")
            self.assertIn("web_scraping", result.verified_capabilities)
            # Honest ceiling.
            self.assertEqual(result.benchmark_status, "not_started")
            self.assertFalse(result.routable)

    def test_bare_domain_completes_to_well_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, AgentCardServer(_card()) as base_url:
            # Submit just the host:port (no path) — server answers any path.
            svc = _service(Path(tmp))
            result = svc.ingest(base_url)  # no /.well-known path
            self.assertTrue(result.resolved, result.error)
            self.assertTrue(result.card_url.endswith("/.well-known/agent.json"))

    def test_candidate_persisted_to_agentic_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, AgentCardServer(_card()) as base_url:
            tmp_path = Path(tmp)
            svc = _service(tmp_path)
            svc.ingest(f"{base_url}/.well-known/agent.json")

            store = discovery_store_for_path(str(tmp_path / "discovery.json"))
            rows = store.load()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].provider_type, "a2a_agent")
            self.assertTrue(rows[0].will_fail)  # non-routable
            self.assertEqual(rows[0].benchmark_status, "not_started")

    def test_verification_record_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, AgentCardServer(_card()) as base_url:
            tmp_path = Path(tmp)
            svc = _service(tmp_path)
            svc.ingest(f"{base_url}/.well-known/agent.json")

            from planmyagents_api.discovery.verification_store import (
                verification_store_for_path,
            )

            vstore = verification_store_for_path(str(tmp_path / "verification.json"))
            latest = vstore.latest_for("policycheck")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["status"], "capability_verified")

    def test_unmapped_skill_recorded_not_fabricated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, AgentCardServer(
            _card(name="WeirdAgent", skill="defenestrate_widgets")
        ) as base_url:
            svc = _service(Path(tmp))
            result = svc.ingest(f"{base_url}/.well-known/agent.json")
            # The skill becomes a declared capability (from the card) but is
            # reported as unmapped against the known registry vocabulary.
            self.assertIn("defenestrate_widgets", result.declared_capabilities)
            self.assertIn("defenestrate_widgets", result.unmapped_skills)

    def test_idempotent_resubmission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, AgentCardServer(_card()) as base_url:
            tmp_path = Path(tmp)
            svc = _service(tmp_path)
            url = f"{base_url}/.well-known/agent.json"
            svc.ingest(url)
            svc.ingest(url)
            store = discovery_store_for_path(str(tmp_path / "discovery.json"))
            self.assertEqual(len(store.load()), 1)


class CardIngestionFailureTest(unittest.TestCase):
    def test_unreachable_url_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = _service(Path(tmp))
            result = svc.ingest("http://127.0.0.1:1/.well-known/agent.json")
            self.assertFalse(result.resolved)
            self.assertIn("resolution_failed", result.error)
            self.assertEqual(result.provider_id, "")

    def test_oversized_card_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, AgentCardServer(
            _card(), oversized=True
        ) as base_url:
            svc = _service(Path(tmp), max_card_bytes=1000)
            result = svc.ingest(f"{base_url}/.well-known/agent.json")
            self.assertFalse(result.resolved)
            self.assertIn("card_too_large", result.error)

    def test_invalid_json_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, AgentCardServer(
            _card(), invalid_json=True
        ) as base_url:
            svc = _service(Path(tmp))
            result = svc.ingest(f"{base_url}/.well-known/agent.json")
            self.assertFalse(result.resolved)
            self.assertIn("resolution_failed", result.error)


class CardIngestionQualityBlockedTest(unittest.TestCase):
    def test_attest_quality_is_blocked_and_non_real(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = _service(Path(tmp))
            blocked = svc.attest_quality("policycheck", "web_scraping")
            self.assertEqual(blocked["status"], "blocked_on_invocation")
            self.assertEqual(blocked["source"], "refused")
            # The credibility classifier must treat it as non-real, so an
            # ingested A2A agent can never appear as quality-scored.
            self.assertFalse(
                _is_real_run({"source": blocked["source"], "sample_size": 1})
            )


if __name__ == "__main__":
    unittest.main()
