"""Unit tests for ``post_goal_refresh`` background kick-off helper.

These tests pin the three properties /goal latency depends on:
* ``kick_off_post_goal_refresh`` returns synchronously (never blocks).
* Concurrent kick-offs collapse to a single in-flight refresh.
* The debounce window prevents back-to-back kick-offs after a
  refresh completes.
* The env-var off-switch disables the kick-off entirely (tests rely
  on this so they don't need to mock the network).
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery import post_goal_refresh as pgr


class PostGoalRefreshKickOffTests(unittest.TestCase):
    def setUp(self) -> None:
        # Tests must run with the kick-off ENABLED to exercise the
        # real branch logic. The package-level conftest sets the
        # env var to "false" by default to keep other tests
        # hermetic, so we override it here for the duration of
        # these tests only.
        self._prev_enabled = os.environ.get("PLANMYAGENTS_POST_GOAL_REFRESH")
        os.environ["PLANMYAGENTS_POST_GOAL_REFRESH"] = "true"
        # Tighten the debounce window so the debounce test doesn't
        # have to sleep for 60s. We patch the module-level constant
        # at construction time; the tests read it via env at
        # start-up so a direct patch is the cleanest.
        self._prev_debounce = pgr._DEBOUNCE_SECONDS
        pgr._DEBOUNCE_SECONDS = 0.5
        pgr.reset_for_tests()

    def tearDown(self) -> None:
        if self._prev_enabled is None:
            os.environ.pop("PLANMYAGENTS_POST_GOAL_REFRESH", None)
        else:
            os.environ["PLANMYAGENTS_POST_GOAL_REFRESH"] = self._prev_enabled
        pgr._DEBOUNCE_SECONDS = self._prev_debounce
        pgr.reset_for_tests()

    def test_disabled_via_env_returns_disabled_status(self) -> None:
        os.environ["PLANMYAGENTS_POST_GOAL_REFRESH"] = "false"
        result = pgr.kick_off_post_goal_refresh(store_url="dummy")
        self.assertEqual({"status": "disabled"}, result)

    def test_kicks_off_thread_and_returns_immediately(self) -> None:
        """Patch the inner blocking refresh to a no-op so we don't
        actually hit the network; just verify the thread is spawned
        and the synchronous return is fast.
        """

        ran = threading.Event()

        def fake_blocking(*, store_url):  # noqa: ARG001
            ran.set()
            return {"total_candidates": 0, "source_count": 0}

        with patch.object(pgr, "_run_refresh_blocking", fake_blocking):
            t0 = time.monotonic()
            result = pgr.kick_off_post_goal_refresh(store_url="dummy")
            elapsed = time.monotonic() - t0
            # Synchronous return must be effectively instant
            # regardless of how slow the refresh itself is.
            self.assertLess(elapsed, 0.1)
            self.assertEqual("kicked", result["status"])
            self.assertTrue(ran.wait(timeout=2.0))

    def test_concurrent_kickoffs_collapse_to_single_in_flight(self) -> None:
        """While a refresh is running, subsequent kick-offs must
        report ``in_flight`` rather than spawning more threads. This
        is the property that prevents N concurrent /goal requests
        from starting N concurrent refreshes.
        """

        block = threading.Event()
        proceed = threading.Event()

        def slow_blocking(*, store_url):  # noqa: ARG001
            proceed.set()
            block.wait(timeout=5.0)
            return {"total_candidates": 0, "source_count": 0}

        with patch.object(pgr, "_run_refresh_blocking", slow_blocking):
            first = pgr.kick_off_post_goal_refresh(store_url="dummy")
            self.assertEqual("kicked", first["status"])
            # Wait until the refresh thread has actually entered the
            # blocking section before testing the second kick-off,
            # otherwise we'd race the in-flight flag.
            self.assertTrue(proceed.wait(timeout=2.0))

            second = pgr.kick_off_post_goal_refresh(store_url="dummy")
            self.assertEqual("in_flight", second["status"])

            block.set()  # release the first refresh

    def test_debounces_after_recent_completion(self) -> None:
        """Even after the in-flight refresh finishes, a kick-off
        within ``_DEBOUNCE_SECONDS`` must be rejected with the
        ``debounced`` status. After the window elapses, kick-off
        must succeed again.
        """

        with patch.object(
            pgr,
            "_run_refresh_blocking",
            lambda *, store_url: {"total_candidates": 0, "source_count": 0},  # noqa: ARG005
        ):
            self.assertEqual(
                "kicked",
                pgr.kick_off_post_goal_refresh(store_url="dummy")["status"],
            )
            # Give the daemon thread time to finish the no-op
            # refresh and update _last_completed_at.
            time.sleep(0.1)

            second = pgr.kick_off_post_goal_refresh(store_url="dummy")
            self.assertEqual("debounced", second["status"])
            self.assertIn("age_seconds", second)

            # Sleep past the debounce window and confirm we can
            # kick off again.
            time.sleep(pgr._DEBOUNCE_SECONDS + 0.1)
            third = pgr.kick_off_post_goal_refresh(store_url="dummy")
            self.assertEqual("kicked", third["status"])

    def test_in_flight_flag_is_cleared_when_refresh_raises(self) -> None:
        """Critical regression: a raised exception inside the
        blocking refresh must NOT leave the in-flight flag set.
        Otherwise one bad refresh would permanently disable all
        subsequent kick-offs.
        """

        finished = threading.Event()

        def boom(*, store_url):  # noqa: ARG001
            finished.set()
            raise RuntimeError("simulated source failure")

        with patch.object(pgr, "_run_refresh_blocking", boom):
            self.assertEqual(
                "kicked",
                pgr.kick_off_post_goal_refresh(store_url="dummy")["status"],
            )
            # Wait for the boom to land; the daemon thread sets
            # finished BEFORE raising.
            self.assertTrue(finished.wait(timeout=2.0))
            # Also wait briefly for the finally clause that clears
            # the flag — the raise happens inside the try, so the
            # flag clear happens just after.
            time.sleep(0.05)

            # After debounce window, kick-off must succeed.
            time.sleep(pgr._DEBOUNCE_SECONDS + 0.05)
            second = pgr.kick_off_post_goal_refresh(store_url="dummy")
            # If the in-flight flag had leaked, this would be
            # "in_flight" rather than "kicked".
            self.assertEqual("kicked", second["status"])


class McpToolProbeStageTests(unittest.TestCase):
    """Gap 2: the post-goal refresh runs an MCP ``tools/list`` probe stage
    after the source pull, so reachable MCP servers get their tool surface
    populated automatically (no operator-invoked script required) and the
    inline embedding step picks up the new tool data on save_merge."""

    def setUp(self) -> None:
        # The probe stage reads multiple env vars; capture+restore the
        # set we touch so test isolation holds even when run in random
        # order.
        self._env_keys = (
            "PLANMYAGENTS_POST_GOAL_PROBE",
            "PLANMYAGENTS_POST_GOAL_PROBE_LIMIT",
            "PLANMYAGENTS_POST_GOAL_PROBE_TIMEOUT_SEC",
            "PLANMYAGENTS_POST_GOAL_PROBE_MAX_WORKERS",
        )
        self._prev_env = {k: os.environ.get(k) for k in self._env_keys}

    def tearDown(self) -> None:
        for k, v in self._prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _make_candidate(
        self,
        *,
        provider_id: str,
        url: str,
        tools=None,
        provider_type: str = "mcp_server",
    ):
        """Build a minimal DiscoveryCandidate suitable for probe-stage
        targeting. Imported lazily so the test module stays cheap."""

        from planmyagents_api.discovery.models import (
            CandidateCapability,
            DiscoveryCandidate,
        )

        return DiscoveryCandidate(
            id=provider_id,
            display_name=provider_id,
            vendor=provider_id.split("/", 1)[0],
            vendor_url=url,
            provider_type=provider_type,
            capabilities=(CandidateCapability(id="general_research", confidence=0.5),),
            source="test",
            first_seen_at="2026-05-15",
            last_seen_at="2026-05-15",
            tools=tuple(tools or ()),
            evidence_url=url,
        )

    def test_disabled_via_env_returns_disabled_status(self) -> None:
        os.environ["PLANMYAGENTS_POST_GOAL_PROBE"] = "false"
        result = pgr._run_mcp_tool_probe_stage(store_url="dummy")
        self.assertEqual({"status": "disabled"}, result)

    def test_no_targets_when_no_mcp_rows_missing_tools(self) -> None:
        """A store with only api_provider rows, or only MCP rows that
        already have tools, has nothing to probe — the stage must
        short-circuit cleanly without attempting any HTTP."""

        from planmyagents_api.discovery.models import CandidateTool

        candidates = [
            self._make_candidate(
                provider_id="already-probed/foo",
                url="https://foo.example",
                tools=[CandidateTool(name="x")],
            ),
            self._make_candidate(
                provider_id="non-mcp/bar",
                url="https://bar.example",
                provider_type="api_provider",
            ),
        ]

        class _StubStore:
            def __init__(self, payload):
                self._payload = payload
                self.save_merge_calls = []

            def load(self):
                return list(self._payload)

            def save_merge(self, items):
                self.save_merge_calls.append(list(items))

        stub = _StubStore(candidates)
        with patch.object(
            pgr, "discovery_store_for_path", lambda _u: stub, create=True
        ):
            # `discovery_store_for_path` is imported lazily inside the
            # stage; we patch the LOOKUP target. To do this we need to
            # patch the symbol in the module the function imports from.
            from planmyagents_api.discovery import store as store_module

            with patch.object(store_module, "discovery_store_for_path", lambda _u: stub):
                result = pgr._run_mcp_tool_probe_stage(store_url="dummy")

        self.assertEqual("no_targets", result["status"])
        self.assertEqual([], stub.save_merge_calls)

    def test_probe_persists_only_candidates_whose_tools_grew(self) -> None:
        """Happy path: two MCP servers needing a probe; the enricher
        returns tools for one of them; only that one ends up in the
        save_merge call. The unreachable one is silently skipped."""

        from planmyagents_api.discovery import store as store_module
        from planmyagents_api.discovery.enrichers import mcp_tools as enricher_module
        from planmyagents_api.discovery.models import CandidateTool

        target_a = self._make_candidate(
            provider_id="mcp/needs-probe-a", url="https://a.example"
        )
        target_b = self._make_candidate(
            provider_id="mcp/needs-probe-b", url="https://b.example"
        )

        class _StubStore:
            def __init__(self, payload):
                self._payload = payload
                self.save_merge_calls = []

            def load(self):
                return list(self._payload)

            def save_merge(self, items):
                self.save_merge_calls.append(list(items))

        stub = _StubStore([target_a, target_b])

        # Replace the enricher with a deterministic one that only
        # populates tools for `a.example`. The real enricher's HTTP
        # path is irrelevant to this test — we're verifying the stage's
        # filtering and persistence behaviour, not the JSON-RPC
        # transport (which has its own dedicated tests).
        class _StubEnricher:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def enrich(self, candidates):
                out = []
                for c in candidates:
                    if "a.example" in c.vendor_url:
                        # Build an enriched copy by passing through the
                        # real model — the candidate is frozen so we
                        # construct a new instance.
                        from planmyagents_api.discovery.models import DiscoveryCandidate

                        new = DiscoveryCandidate(
                            **{
                                **c.__dict__,
                                "tools": (
                                    CandidateTool(
                                        name="ping",
                                        description="health check",
                                    ),
                                ),
                            }
                        )
                        out.append(new)
                    else:
                        out.append(c)
                return out

        with patch.object(
            store_module, "discovery_store_for_path", lambda _u: stub
        ), patch.object(
            enricher_module, "McpToolProbeEnricher", _StubEnricher
        ):
            result = pgr._run_mcp_tool_probe_stage(store_url="dummy")

        self.assertEqual("ok", result["status"])
        self.assertEqual(2, result["targets"])
        self.assertEqual(1, result["populated"])
        self.assertEqual(1, result["persisted"])
        self.assertEqual(1, len(stub.save_merge_calls))
        saved_ids = {c.id for c in stub.save_merge_calls[0]}
        self.assertEqual({"mcp/needs-probe-a"}, saved_ids)

    def test_skips_stdio_only_servers_with_no_http_url(self) -> None:
        """MCP servers exposed only over stdio have no callable HTTP
        endpoint, so the probe would always fail. Filter them out at
        target selection so the parallelism budget targets work that
        can actually succeed."""

        from planmyagents_api.discovery import store as store_module

        stdio_only = self._make_candidate(
            provider_id="mcp/stdio-only", url=""
        )

        class _StubStore:
            def __init__(self, payload):
                self._payload = payload
                self.save_merge_calls = []

            def load(self):
                return list(self._payload)

            def save_merge(self, items):
                self.save_merge_calls.append(list(items))

        stub = _StubStore([stdio_only])
        with patch.object(
            store_module, "discovery_store_for_path", lambda _u: stub
        ):
            result = pgr._run_mcp_tool_probe_stage(store_url="dummy")

        self.assertEqual("no_targets", result["status"])

    def test_per_worker_failure_is_isolated_and_does_not_abort_stage(
        self,
    ) -> None:
        """If one probe raises (network error, malformed response, etc.)
        the rest of the probes must still run and the stage must
        report ``ok`` with the surviving populations. This is critical
        for keeping post-goal refresh resilient in the face of a single
        bad MCP server."""

        from planmyagents_api.discovery import store as store_module
        from planmyagents_api.discovery.enrichers import mcp_tools as enricher_module
        from planmyagents_api.discovery.models import (
            CandidateTool,
            DiscoveryCandidate,
        )

        good = self._make_candidate(
            provider_id="mcp/good", url="https://good.example"
        )
        bad = self._make_candidate(
            provider_id="mcp/bad", url="https://bad.example"
        )

        class _StubStore:
            def __init__(self, payload):
                self._payload = payload
                self.save_merge_calls = []

            def load(self):
                return list(self._payload)

            def save_merge(self, items):
                self.save_merge_calls.append(list(items))

        stub = _StubStore([good, bad])

        class _MixedEnricher:
            def __init__(self, **_kwargs):
                pass

            def enrich(self, candidates):
                # Target list always has one candidate (the stage
                # submits one per worker). Raise for the bad one,
                # populate tools for the good one.
                if any("bad.example" in c.vendor_url for c in candidates):
                    raise RuntimeError("simulated network error")
                out = []
                for c in candidates:
                    out.append(
                        DiscoveryCandidate(
                            **{
                                **c.__dict__,
                                "tools": (CandidateTool(name="ok"),),
                            }
                        )
                    )
                return out

        with patch.object(
            store_module, "discovery_store_for_path", lambda _u: stub
        ), patch.object(
            enricher_module, "McpToolProbeEnricher", _MixedEnricher
        ):
            result = pgr._run_mcp_tool_probe_stage(store_url="dummy")

        self.assertEqual("ok", result["status"])
        self.assertEqual(2, result["targets"])
        # Only the good one survived the per-worker failure.
        self.assertEqual(1, result["populated"])
        self.assertEqual(1, result["persisted"])
        saved_ids = {c.id for c in stub.save_merge_calls[0]}
        self.assertEqual({"mcp/good"}, saved_ids)

    def test_respects_probe_limit_env(self) -> None:
        """Operator caps how many candidates one stage attempts via
        ``PLANMYAGENTS_POST_GOAL_PROBE_LIMIT``. With limit=1 and three
        targets, the stage must probe exactly one of them — preserving
        the bounded-runtime contract."""

        from planmyagents_api.discovery import store as store_module
        from planmyagents_api.discovery.enrichers import mcp_tools as enricher_module

        os.environ["PLANMYAGENTS_POST_GOAL_PROBE_LIMIT"] = "1"

        targets = [
            self._make_candidate(
                provider_id=f"mcp/t{i}",
                url=f"https://t{i}.example",
            )
            for i in range(3)
        ]

        class _StubStore:
            def __init__(self, payload):
                self._payload = payload

            def load(self):
                return list(self._payload)

            def save_merge(self, items):  # not exercised in this test
                pass

        stub = _StubStore(targets)

        seen_targets: list[str] = []

        class _CountingEnricher:
            def __init__(self, **_kwargs):
                pass

            def enrich(self, candidates):
                for c in candidates:
                    seen_targets.append(c.id)
                return candidates  # no tool growth → not persisted

        with patch.object(
            store_module, "discovery_store_for_path", lambda _u: stub
        ), patch.object(
            enricher_module, "McpToolProbeEnricher", _CountingEnricher
        ):
            result = pgr._run_mcp_tool_probe_stage(store_url="dummy")

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["targets"])
        self.assertEqual(1, len(seen_targets))


if __name__ == "__main__":
    unittest.main()
