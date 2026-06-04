"""Tests for the read-only /eval/methodology route.

The route is a pure projection of the Eval_Framework (tier ladder, protocol
maturity registry, source taxonomy) plus a live credibility histogram. These
tests pin the honesty contract the public /trust page depends on:

* every tier the framework defines is surfaced, in cheapest-first order;
* only the real-eval sources (exact_match / judge) are flagged is_real;
* non-executable protocols (a2a / acp / anp) are never marked can_invoke;
* the credibility distribution reflects the real seeded store (a freshly
  seeded synthetic-only store must NOT report any publishable cells).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.discovery.store import JsonDiscoveryStore
from planmyagents_api.eval.models import REAL_EVAL_SOURCES


def _candidate() -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id="alpha-email-agent",
        display_name="Alpha Email",
        vendor="alpha",
        vendor_url="https://alpha.example",
        provider_type="ai_agent",
        capabilities=[CandidateCapability(id="email_verification", confidence=0.9)],
        verification_status="capability_verified",
        evidence_url="https://alpha.example/agent.json",
    )


class EvalMethodologyRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        self.benchmark_path = self.tempdir / "benchmarks.json"
        self.verification_path = self.tempdir / "verification.json"
        JsonDiscoveryStore(self.discovery_path).save([_candidate()])

        self._env = {
            "PLANMYAGENTS_DISCOVERY_STORE_URL": str(self.discovery_path),
            "PLANMYAGENTS_BENCHMARK_STORE_URL": str(self.benchmark_path),
            "PLANMYAGENTS_VERIFICATION_STORE_URL": str(self.verification_path),
        }
        self._old = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

        from planmyagents_api.web.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tempdir.cleanup()

    def test_returns_full_tier_ladder_in_order(self) -> None:
        payload = self.client.get("/eval/methodology").json()
        ranks = [tier["rank"] for tier in payload["tiers"]]
        self.assertEqual(ranks, sorted(ranks))
        ids = {tier["id"] for tier in payload["tiers"]}
        self.assertEqual(
            ids,
            {
                "static_verification",
                "functional_smoke",
                "scored_benchmark",
                "continuous_reeval",
            },
        )

    def test_only_real_sources_flagged_real(self) -> None:
        payload = self.client.get("/eval/methodology").json()
        real = {s["source"] for s in payload["sources"] if s["is_real"]}
        self.assertEqual(real, set(REAL_EVAL_SOURCES))

    def test_non_executable_protocols_cannot_invoke(self) -> None:
        payload = self.client.get("/eval/methodology").json()
        by_name = {p["protocol"]: p for p in payload["protocols"]}
        for name in ("a2a", "acp", "anp"):
            self.assertIn(name, by_name)
            self.assertFalse(by_name[name]["can_invoke"], name)
        self.assertTrue(by_name["mcp"]["can_invoke"])

    def test_credibility_distribution_has_no_publishable_for_synthetic_store(
        self,
    ) -> None:
        payload = self.client.get("/eval/methodology").json()
        statuses = {row["status"] for row in payload["credibility_distribution"]}
        self.assertNotIn("publishable", statuses)
        self.assertEqual(payload["real_run_capabilities"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
