"""Integration tests for the request-time live discovery scout fleet wired
into the /goal FastAPI endpoint.

We mock out the chat client and the scout fleet so the test runs offline
and deterministically, but the rest of the /goal pipeline (planner →
discovery → response shaping → live_discovery branch) is exercised
end-to-end.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402
from planmyagents_api.discovery.normalizer import normalize_candidate  # noqa: E402
from planmyagents_api.discovery.scouts import Scout  # noqa: E402
from planmyagents_api.discovery.store import JsonDiscoveryStore  # noqa: E402


def _agent_candidate() -> object:
    return normalize_candidate(
        {
            "id": "fresh-find-from-scout",
            "display_name": "Fresh Find From Scout",
            "vendor": "fresh-vendor.example",
            "vendor_url": "https://fresh-vendor.example",
            "provider_type": "ai_agent",
            "capabilities": [{"id": "kyc_aml_check", "confidence": 0.7}],
        },
        source="hacker_news_agent_watch",
    )


@dataclass
class _FakeSource:
    candidates: list

    def search(self, *, capabilities, task_description):  # noqa: ARG002
        return list(self.candidates)


def _fake_default_scouts() -> list[Scout]:
    return [
        Scout(scout_id="hacker_news_agent_watch", source=_FakeSource([_agent_candidate()])),
        Scout(scout_id="vendor_rss", source=_FakeSource([])),
    ]


class GoalLiveDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        self.benchmark_path = self.tempdir / "benchmarks.json"
        self.verification_path = self.tempdir / "verification.json"

        # Empty stores — every capability will be missing, so the scout
        # fleet should be invoked.
        JsonDiscoveryStore(self.discovery_path).save([])

        os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(self.discovery_path)
        os.environ["PLANMYAGENTS_BENCHMARK_STORE_URL"] = str(self.benchmark_path)
        os.environ["PLANMYAGENTS_VERIFICATION_STORE_URL"] = str(self.verification_path)
        os.environ["PLANMYAGENTS_PLANNER"] = "rules"
        os.environ["PLANMYAGENTS_INTENT_MAPPER"] = "off"
        # Disable all default-on batch live network discovery sources so
        # the local store search inside _refusal_discovery doesn't hit
        # real upstreams. We're testing only the scout dispatcher here.
        for key in (
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
        ):
            os.environ[key] = "false"
        # Live scout dispatcher: explicitly ON, but with a fake fleet.
        os.environ["PLANMYAGENTS_LIVE_DISCOVERY"] = "true"
        # Disable the LLM-driven candidate judge in tests; see the
        # matching note in `test_web_app.py`'s setUp for the rationale.
        os.environ["PLANMYAGENTS_CANDIDATE_JUDGE"] = "off"
        # Demand log redirected into tempdir to avoid polluting repo data/.
        self.demand_store_path = self.tempdir / "demand.jsonl"
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = str(self.demand_store_path)
        # Same for the discovery run-event audit log: redirect into the
        # tempdir so each test gets a clean slate and we don't leak files
        # into apps/data/ as a side effect.
        self.run_log_path = self.tempdir / "discovery_run_events.jsonl"
        os.environ["PLANMYAGENTS_RUN_LOG_STORE_PATH"] = str(self.run_log_path)

        from planmyagents_api.web.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self._tempdir.cleanup()
        for var in (
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            "PLANMYAGENTS_BENCHMARK_STORE_URL",
            "PLANMYAGENTS_VERIFICATION_STORE_URL",
            "PLANMYAGENTS_PLANNER",
            "PLANMYAGENTS_INTENT_MAPPER",
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
            "PLANMYAGENTS_LIVE_DISCOVERY",
            "PLANMYAGENTS_CANDIDATE_JUDGE",
            "PLANMYAGENTS_DEMAND_STORE_PATH",
            "PLANMYAGENTS_RUN_LOG_STORE_PATH",
        ):
            os.environ.pop(var, None)

    def test_goal_response_includes_live_discovery_block_with_scout_results(self) -> None:
        # Patch `default_scouts` (used inside _live_discovery) AND the
        # chat-client builder (so we don't try to reach Ollama) for the
        # life of this request.
        with patch(
            "planmyagents_api.web.app.default_scouts", _fake_default_scouts
        ), patch(
            "planmyagents_api.web.app._maybe_chat_client_for_query_expansion",
            lambda: None,
        ):
            response = self.client.post(
                "/goal",
                json={
                    "goal": (
                        "Help me complete a KYC AML check for a UAE retail buyer."
                    ),
                    "execute": False,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        plan = body.get("plan") or {}
        discovery = plan.get("discovery") or {}
        live_discovery = discovery.get("live_discovery")
        self.assertIsNotNone(
            live_discovery,
            f"missing live_discovery block in response: {discovery.keys()}",
        )
        self.assertEqual(live_discovery.get("status"), "ran")
        self.assertGreaterEqual(live_discovery.get("scouts_dispatched", 0), 1)
        self.assertGreaterEqual(live_discovery.get("candidates_found_total", 0), 1)
        self.assertGreaterEqual(live_discovery.get("candidates_persisted", 0), 1)

        names = [
            c.get("display_name")
            for c in live_discovery.get("newly_discovered_candidates", [])
        ]
        self.assertIn("Fresh Find From Scout", names)

        # Per-capability summaries should include scout-level metadata.
        per_cap = live_discovery.get("per_capability") or []
        self.assertGreaterEqual(len(per_cap), 1)
        first = per_cap[0]
        self.assertIn("query_expansion", first)
        # No chat client → fallback should fire.
        self.assertEqual(
            first["query_expansion"]["used_llm"], False,
            "no chat client patched in → expansion must use rule-based fallback",
        )
        self.assertEqual(
            first["query_expansion"]["fallback_reason"], "no_chat_client"
        )

        # Dispatch summary should record per-scout latency + status.
        scout_summaries = first["dispatch"]["scouts"]
        scout_ids = sorted(s["scout_id"] for s in scout_summaries)
        self.assertEqual(scout_ids, ["hacker_news_agent_watch", "vendor_rss"])

    def test_live_discovery_can_be_disabled_via_env(self) -> None:
        os.environ["PLANMYAGENTS_LIVE_DISCOVERY"] = "false"
        with patch(
            "planmyagents_api.web.app.default_scouts", _fake_default_scouts
        ):
            response = self.client.post(
                "/goal",
                json={
                    "goal": "Help me complete a KYC AML check.",
                    "execute": False,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        discovery = (response.json().get("plan") or {}).get("discovery") or {}
        self.assertIsNone(
            discovery.get("live_discovery"),
            "live_discovery block must be absent when disabled by env",
        )


if __name__ == "__main__":
    unittest.main()
