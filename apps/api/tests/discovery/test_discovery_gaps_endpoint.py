"""Integration tests for ``/discovery-gaps`` and the ``/goal``-side
recording hook.

The recorder is wired into the ``/goal`` route so a single refusal
should produce one gap event per missing capability, and the new
``/discovery-gaps`` endpoint should surface those events in the
expected leaderboard shape. Unit tests in
:mod:`test_discovery_gaps_store` cover the data model in depth; this
test focuses on the route-level wiring so a future regression
(e.g. someone removes the recorder call from ``/goal``) trips a CI
failure rather than silently disabling the leaderboard tile.

We use the JSON backend by setting ``PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH``
to a temp file so the test is hermetic — no Postgres, no shared
on-disk state across the suite.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient
from planmyagents_api.discovery.discovery_gaps_store import (
    JsonDiscoveryGapsStore,
)
from planmyagents_api.discovery.models import (
    CandidateCapability,
    DiscoveryCandidate,
)
from planmyagents_api.discovery.store import JsonDiscoveryStore


def _api_provider(provider_id: str, capability: str) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=provider_id,
        display_name=provider_id.replace("-", " ").title(),
        vendor="acme",
        vendor_url="https://acme.example",
        provider_type="api_provider",
        capabilities=[CandidateCapability(id=capability, confidence=0.6)],
        evidence_url="https://acme.example/openapi.json",
        source="apis_guru",
    )


class _GapsEnvShim:
    """Repeated env-var setup the two test classes both need."""

    def __init__(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        self.demand_path = self.tempdir / "demand.jsonl"
        self.gaps_path = self.tempdir / "gaps.jsonl"
        self.run_log_path = self.tempdir / "discovery_run_events.jsonl"

    def install(self) -> None:
        os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(self.discovery_path)
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = str(self.demand_path)
        os.environ["PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH"] = str(
            self.gaps_path
        )
        os.environ["PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED"] = "true"
        os.environ["PLANMYAGENTS_RUN_LOG_STORE_PATH"] = str(self.run_log_path)
        os.environ["PLANMYAGENTS_PLANNER"] = "rules"
        os.environ["PLANMYAGENTS_INTENT_MAPPER"] = "off"
        # The web app calls ``load_dotenv_once()`` at module import,
        # which seeds these two with the production postgres URL from
        # the repo ``.env``. Without overriding them here the goal route
        # falls into ``ProviderRouter.merge_promoted_agents_from_store``
        # (because LOAD_PROMOTED_PROVIDERS_FROM_DB=true in .env) and
        # tries to open a psycopg connection to a postgres that isn't
        # running in CI / on dev laptops. Force the merge branch off
        # and clear the URL so any accidental call to
        # discovery_store_for_path picks the file backend instead.
        # Mirrors the same shim used by ``test_goal_research_backstop``
        # and ``test_goal_human_alternatives``.
        os.environ["PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB"] = "false"
        os.environ["PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL"] = ""
        for key in (
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
        ):
            os.environ[key] = "false"
        os.environ["PLANMYAGENTS_LIVE_DISCOVERY"] = "false"
        os.environ["PLANMYAGENTS_CANDIDATE_JUDGE"] = "off"
        # Disable the slug canonicaliser and label reconciler so the
        # stubbed decomposer output flows through unmodified. Without
        # these the canonicaliser will rewrite ``payment_information_retrieval``
        # → ``payment_authorization`` (which IS in the router), and the
        # reconciler may also call the LLM to rewrite freshly-coined
        # slugs. Both behaviours are correct in production but make
        # this test indeterministic — it's testing the gap-recording
        # PERSISTENCE wiring, not the upstream rewrite logic.
        os.environ["PLANMYAGENTS_SLUG_CANONICALIZATION"] = "false"
        os.environ["PLANMYAGENTS_LABEL_RECONCILER"] = "off"

    def cleanup(self) -> None:
        self._tempdir.cleanup()
        for key in (
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            "PLANMYAGENTS_DEMAND_STORE_PATH",
            "PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH",
            "PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED",
            "PLANMYAGENTS_RUN_LOG_STORE_PATH",
            "PLANMYAGENTS_PLANNER",
            "PLANMYAGENTS_INTENT_MAPPER",
            "PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB",
            "PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL",
            "PLANMYAGENTS_DISCOVERY_OFFICIAL_MCP_REGISTRY",
            "PLANMYAGENTS_DISCOVERY_APIS_GURU",
            "PLANMYAGENTS_DISCOVERY_HACKER_NEWS",
            "PLANMYAGENTS_DISCOVERY_VENDOR_RSS",
            "PLANMYAGENTS_DISCOVERY_GITHUB_RECENTLY_PUSHED",
            "PLANMYAGENTS_LIVE_DISCOVERY",
            "PLANMYAGENTS_CANDIDATE_JUDGE",
            "PLANMYAGENTS_SLUG_CANONICALIZATION",
            "PLANMYAGENTS_LABEL_RECONCILER",
        ):
            os.environ.pop(key, None)


class DiscoveryGapsEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = _GapsEnvShim()
        self.env.install()
        # Pre-seed the gap store so the endpoint has data to render.
        # Using the JSON backend directly skips the route flow and keeps
        # this test focused on the GET surface.
        store = JsonDiscoveryGapsStore(self.env.gaps_path)
        from planmyagents_api.discovery.discovery_gaps_store import DiscoveryGapEvent

        store.append(
            [
                DiscoveryGapEvent(
                    capability_id="fare_comparison",
                    goal_excerpt="cheapest single malt liquor store",
                    goal_hash="h1",
                    observed_at="2026-05-12T17:00:00+00:00",
                    judge_evaluated=11,
                    judge_accepted=0,
                ),
                DiscoveryGapEvent(
                    capability_id="fare_comparison",
                    goal_excerpt="cheapest flight to tokyo",
                    goal_hash="h2",
                    observed_at="2026-05-12T17:01:00+00:00",
                    judge_evaluated=4,
                    judge_accepted=0,
                ),
                DiscoveryGapEvent(
                    capability_id="email_verification",
                    goal_excerpt="verify a list of leads",
                    goal_hash="h3",
                    observed_at="2026-05-12T17:02:00+00:00",
                    judge_evaluated=8,
                    judge_accepted=2,  # not zero-yield
                ),
            ]
        )

        from planmyagents_api.web.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.env.cleanup()

    def test_endpoint_returns_aggregated_summary(self) -> None:
        response = self.client.get("/discovery-gaps")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["total_capabilities"], 2)
        # fare_comparison: zero_yield_count=2, observation_count=2 -> first
        # email_verification: zero_yield_count=0, observation_count=1 -> second
        self.assertEqual(
            [c["capability_id"] for c in body["capabilities"]],
            ["fare_comparison", "email_verification"],
        )
        first = body["capabilities"][0]
        self.assertEqual(first["zero_yield_count"], 2)
        self.assertEqual(first["distinct_goal_count"], 2)
        # Most recent goal must come first in sample_goals.
        self.assertEqual(first["sample_goals"][0], "cheapest flight to tokyo")

    def test_endpoint_supports_capability_filter(self) -> None:
        response = self.client.get(
            "/discovery-gaps", params={"capability": "fare_comparison"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["filter"], {"capability": "fare_comparison"})
        self.assertEqual(len(body["capabilities"]), 1)
        self.assertEqual(
            body["capabilities"][0]["capability_id"], "fare_comparison"
        )

    def test_endpoint_respects_limit_param(self) -> None:
        response = self.client.get("/discovery-gaps", params={"limit": 1})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["capabilities"]), 1)
        # total_capabilities counts the full set; limit only trims rows.
        self.assertEqual(body["total_capabilities"], 2)


class GoalRouteRecordsGapEventsTests(unittest.TestCase):
    """End-to-end: a /goal request that produces missing capabilities
    must persist one DiscoveryGapEvent per missing capability.

    Decomposer stub
    ---------------
    These tests intentionally stub ``decompose_goal`` with a fixed
    payload that coins synonymous slugs. After the Fix-1 catalog-
    descriptions change, the live LLM correctly REUSES router-
    supported slugs for goals like "authorize a $42 payment to
    Stripe" — which means the goal is fully covered and produces zero
    missing capabilities, which means there is nothing for these
    gap-recording tests to assert on. Stubbing the decomposer
    restores the "user goal coined a slug the registry can't route"
    failure mode that these tests are designed to exercise, and makes
    them hermetic (no live Groq call) as a side benefit."""

    def setUp(self) -> None:
        self.env = _GapsEnvShim()
        self.env.install()
        # Seed an api_provider so the planner produces a refusal with
        # a missing capability.
        JsonDiscoveryStore(self.env.discovery_path).save(
            [_api_provider("stripe-payments", "payment_authorization")]
        )

        # Stub the decomposer to a fixed coined-only payload. Patched
        # at ``planmyagents_api.web.planning.decompose_goal`` because
        # that's where ``planning.py`` resolves the symbol.
        from planmyagents_api.planner.goal_decomposer import (
            DecomposedSubTask,
            GoalDecomposition,
        )

        self._decomposer_patcher = patch(
            "planmyagents_api.web.planning.decompose_goal",
            return_value=GoalDecomposition(
                intent_summary="Stubbed decomposition for gap-recording test.",
                sub_tasks=[
                    DecomposedSubTask(
                        description="Authorize a payment for the stated amount.",
                        user_facing_step="Authorize the payment.",
                        search_query="payment authorization API",
                        acceptance_criteria=(
                            "Charges a card via a hosted payments processor."
                        ),
                        # Deliberately coined as a synonym of
                        # ``payment_authorization`` so the slug is NOT
                        # router-routable and produces one gap event.
                        suggested_capability_id="payment_information_retrieval",
                    ),
                ],
                confidence=0.9,
                catalog_reused_capabilities=[],
                new_capabilities=["payment_information_retrieval"],
            ),
        )
        self._decomposer_patcher.start()
        self.addCleanup(self._decomposer_patcher.stop)

        from planmyagents_api.web.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.env.cleanup()

    def test_goal_request_writes_one_gap_event_per_missing_capability(
        self,
    ) -> None:
        response = self.client.post(
            "/goal",
            json={"goal": "Authorize a $42 payment to Stripe."},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        # The discovery block lives under plan.discovery in the
        # refusal response shape; the new gap-recording counter sits
        # alongside the existing demand counter.
        gap_block = body["plan"]["discovery"]["discovery_gaps"]
        self.assertGreaterEqual(gap_block["events_recorded"], 1)
        self.assertEqual(gap_block["leaderboard_link"], "/discovery-gaps")

        # Verify the events landed on disk.
        store = JsonDiscoveryGapsStore(self.env.gaps_path)
        events = store.load_all()
        self.assertGreaterEqual(len(events), 1)
        # Every recorded event must have a non-empty capability id.
        for event in events:
            self.assertNotEqual(event.capability_id, "")

    def test_goal_request_rolls_up_into_endpoint_response(self) -> None:
        # End-to-end sanity: post a goal, then GET /discovery-gaps and
        # confirm at least one payment-related capability appears in
        # the rollup.
        #
        # Note: we don't assert a specific slug because the planner
        # LLM is non-deterministic about exact paraphrases (it might
        # emit ``payment_authorization``, ``payment_processing``,
        # ``payment_information_retrieval``, etc.). Whether those
        # paraphrases collapse to the registry slug depends on the
        # embedder's match_slug() tolerance — the hash embedder used
        # in tests requires literal token overlap and won't collapse
        # ``payment_processing`` → ``payment_authorization``. The
        # test's actual contract is "missing capabilities flow into
        # the gap rollup", not "the LLM picks a specific slug".
        self.client.post(
            "/goal", json={"goal": "Authorize a $42 payment to Stripe."}
        )
        response = self.client.get("/discovery-gaps")
        self.assertEqual(response.status_code, 200)
        capability_ids = {
            row["capability_id"] for row in response.json()["capabilities"]
        }
        # At least one capability with the "payment" stem must show
        # up — the planner has been deterministically observed to
        # always include some payment-related slug for this goal.
        payment_caps = {c for c in capability_ids if "payment" in c}
        self.assertTrue(
            payment_caps,
            f"expected a payment-related capability in gap rollup, "
            f"got: {capability_ids}",
        )


if __name__ == "__main__":  # pragma: no cover - script entry
    unittest.main()
