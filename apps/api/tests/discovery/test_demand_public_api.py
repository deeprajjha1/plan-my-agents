"""Integration tests for the public demand-signal APIs (sprint-6 / C).

Covers ``GET /demand/top-capabilities`` and ``GET /demand/gaps``:

* Pre-seed the JSON-backed demand + gap stores via env-vars so the test
  is hermetic (no Postgres dependency, mirrors the pattern in
  ``test_discovery_gaps_endpoint.py``).
* Assert that each endpoint returns the documented contract shape, sorts
  correctly, respects ``window_days`` + ``limit`` + ``min_zero_yield``,
  and trips the rate limiter when called too often.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402
from planmyagents_api.discovery.demand_store import (  # noqa: E402
    DemandEvent,
    JsonDemandStore,
)
from planmyagents_api.discovery.discovery_gaps_store import (  # noqa: E402
    DiscoveryGapEvent,
    JsonDiscoveryGapsStore,
)


class _DemandEnvShim:
    def __init__(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.demand_path = self.tempdir / "demand.jsonl"
        self.gaps_path = self.tempdir / "gaps.jsonl"

    def install(self, *, rate_per_min: int | None = None) -> None:
        os.environ["PLANMYAGENTS_DEMAND_STORE_PATH"] = str(self.demand_path)
        os.environ["PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH"] = str(
            self.gaps_path
        )
        # Prevent the load_dotenv shim from pointing at a real postgres
        # (mirrors the pattern in test_discovery_gaps_endpoint.py).
        os.environ["PLANMYAGENTS_DISCOVERY_STORE_URL"] = str(
            self.tempdir / "discovery.json"
        )
        os.environ["PLANMYAGENTS_BENCHMARK_STORE_URL"] = str(
            self.tempdir / "benchmark.json"
        )
        os.environ["PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB"] = "false"
        os.environ["PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL"] = ""
        if rate_per_min is not None:
            os.environ["PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN"] = str(
                rate_per_min
            )

    def cleanup(self) -> None:
        self._tempdir.cleanup()
        for key in (
            "PLANMYAGENTS_DEMAND_STORE_PATH",
            "PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH",
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            "PLANMYAGENTS_BENCHMARK_STORE_URL",
            "PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB",
            "PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL",
            "PLANMYAGENTS_PUBLIC_DEMAND_RATE_PER_MIN",
        ):
            os.environ.pop(key, None)


def _build_client(env: _DemandEnvShim) -> TestClient:
    # Important: import AFTER env install so module-level reads see them.
    from planmyagents_api.web.app import create_app

    return TestClient(create_app())


def _seed(env: _DemandEnvShim) -> None:
    now = datetime.now(UTC)

    JsonDemandStore(env.demand_path).append(
        [
            DemandEvent(
                capability_id="liquor_pricing",
                goal_excerpt="cheapest single malt under 5000",
                requester_hash="r1",
                requested_at=(now - timedelta(hours=1)).isoformat(timespec="seconds"),
                has_local_match=False,
                has_apis_without_agents=False,
            ),
            DemandEvent(
                capability_id="liquor_pricing",
                goal_excerpt="compare scotch prices in tamil nadu",
                requester_hash="r2",
                requested_at=(now - timedelta(hours=2)).isoformat(timespec="seconds"),
                has_local_match=False,
                has_apis_without_agents=False,
            ),
            DemandEvent(
                capability_id="visa_application",
                goal_excerpt="book schengen visa appointment",
                requester_hash="r1",
                requested_at=(now - timedelta(hours=3)).isoformat(timespec="seconds"),
                has_local_match=False,
                has_apis_without_agents=False,
            ),
            DemandEvent(
                capability_id="ancient_capability",
                goal_excerpt="this is out of window",
                requester_hash="r9",
                requested_at=(now - timedelta(days=60)).isoformat(timespec="seconds"),
                has_local_match=False,
                has_apis_without_agents=False,
            ),
        ]
    )

    JsonDiscoveryGapsStore(env.gaps_path).append(
        [
            DiscoveryGapEvent(
                capability_id="liquor_pricing",
                goal_excerpt="compare scotch prices",
                goal_hash="h1",
                observed_at=(now - timedelta(hours=1)).isoformat(timespec="seconds"),
                scouts_dispatched=4,
                scouts_returned_zero=4,
                judge_evaluated=0,
                judge_accepted=0,
            ),
            DiscoveryGapEvent(
                capability_id="liquor_pricing",
                goal_excerpt="find single malt",
                goal_hash="h2",
                observed_at=(now - timedelta(hours=2)).isoformat(timespec="seconds"),
                scouts_dispatched=4,
                scouts_returned_zero=4,
                judge_evaluated=0,
                judge_accepted=0,
            ),
            DiscoveryGapEvent(
                capability_id="visa_application",
                goal_excerpt="visa appt",
                goal_hash="h3",
                observed_at=(now - timedelta(hours=3)).isoformat(timespec="seconds"),
                scouts_dispatched=3,
                scouts_returned_zero=2,
                judge_evaluated=2,
                judge_accepted=0,
            ),
        ]
    )


class DemandTopCapabilitiesEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = _DemandEnvShim()
        self.env.install(rate_per_min=0)  # disable limiter for content tests
        _seed(self.env)
        self.client = _build_client(self.env)

    def tearDown(self) -> None:
        self.env.cleanup()

    def test_returns_documented_shape_sorted_by_request_count(self) -> None:
        response = self.client.get("/demand/top-capabilities")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["window_days"], 30)
        self.assertGreaterEqual(body["total"], 2)
        ids = [e["capability_id"] for e in body["entries"]]
        # liquor_pricing has 2 requests; visa_application has 1.
        # ancient_capability is outside the 30-day window.
        self.assertEqual(ids[0], "liquor_pricing")
        self.assertIn("visa_application", ids)
        self.assertNotIn("ancient_capability", ids)
        first = body["entries"][0]
        self.assertEqual(
            set(first.keys()),
            {
                "capability_id",
                "request_count",
                "distinct_requester_count",
                "last_requested_at",
                "routable_today",
                "routable_provider_ids",
                "sample_goals",
            },
        )
        self.assertEqual(first["request_count"], 2)
        self.assertEqual(first["distinct_requester_count"], 2)
        # File-backed store can't compute live routable info, so the
        # call returns false / empty list rather than crashing.
        self.assertFalse(first["routable_today"])
        self.assertEqual(first["routable_provider_ids"], [])

    def test_limit_param_caps_returned_entries(self) -> None:
        response = self.client.get("/demand/top-capabilities?limit=1")
        body = response.json()
        self.assertEqual(len(body["entries"]), 1)
        self.assertGreaterEqual(body["total"], 2)

    def test_window_days_filters_old_events(self) -> None:
        response = self.client.get("/demand/top-capabilities?window_days=365")
        ids = [e["capability_id"] for e in response.json()["entries"]]
        self.assertIn("ancient_capability", ids)


class DemandGapsEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = _DemandEnvShim()
        self.env.install(rate_per_min=0)
        _seed(self.env)
        self.client = _build_client(self.env)

    def tearDown(self) -> None:
        self.env.cleanup()

    def test_returns_capabilities_with_demand_and_gap(self) -> None:
        response = self.client.get("/demand/gaps")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        ids = [e["capability_id"] for e in body["entries"]]
        # liquor_pricing: 2 demand + 2 zero-yield gaps  -> top entry
        # visa_application: 1 demand + 1 zero-yield gap -> second entry
        self.assertIn("liquor_pricing", ids)
        self.assertIn("visa_application", ids)
        first = body["entries"][0]
        self.assertEqual(first["capability_id"], "liquor_pricing")
        self.assertEqual(first["zero_yield_count"], 2)
        self.assertEqual(first["discovery_attempts"], 2)
        self.assertEqual(first["request_count"], 2)

    def test_min_zero_yield_filters_results(self) -> None:
        response = self.client.get("/demand/gaps?min_zero_yield=2")
        ids = [e["capability_id"] for e in response.json()["entries"]]
        # Only liquor_pricing has zero_yield_count >= 2.
        self.assertEqual(ids, ["liquor_pricing"])


class DemandRateLimiterEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = _DemandEnvShim()
        # Capacity 2 RPM = 2 requests per 60 seconds; the 3rd should
        # trip even though the test moves fast.
        self.env.install(rate_per_min=2)
        _seed(self.env)
        self.client = _build_client(self.env)

    def tearDown(self) -> None:
        self.env.cleanup()

    def test_three_rapid_calls_trip_429(self) -> None:
        self.client.get("/demand/top-capabilities")
        self.client.get("/demand/top-capabilities")
        third = self.client.get("/demand/top-capabilities")
        self.assertEqual(third.status_code, 429, third.text)
        self.assertIn("Retry-After", third.headers)
        self.assertGreaterEqual(int(third.headers["Retry-After"]), 1)


if __name__ == "__main__":
    unittest.main()
