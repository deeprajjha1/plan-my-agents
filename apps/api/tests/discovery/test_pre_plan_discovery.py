"""Tests for the Gap-3 pre-plan discovery wrapper and helper.

Covers two layers:

1. ``run_pre_plan_discovery`` (the discovery helper) — verifies that
   capabilities/queries are pulled correctly from the decomposer
   output, that the cap is enforced, that scout failures don't abort
   the stage, and that persistence failures degrade gracefully.

2. ``plan_goal_with_pre_plan_discovery`` (the planning wrapper) —
   verifies the gate (env var off ⇒ no scouts), the executable-plan
   short-circuit (no scouts on the hot path), and the metadata
   contract that the ``/goal`` route uses to skip a duplicate
   ``_live_discovery`` dispatch.

These tests intentionally do not call the real scout fleet. The
discovery layer's hermetic-test contract (``ScoutDispatcher`` runs
threads against arbitrary callables) means we can substitute deterministic
fakes for ``ScoutDispatcher`` and ``discovery_store_for_path`` and assert
on the orchestration shape directly.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery import pre_plan_discovery as ppd
from planmyagents_api.discovery import store as store_module
from planmyagents_api.discovery.models import (
    CandidateCapability,
    DiscoveryCandidate,
)


def _make_candidate(provider_id: str, *, capability: str) -> DiscoveryCandidate:
    """Construct a minimal valid DiscoveryCandidate for the persistence
    side of the test. The fixture mirrors what scouts return after dedupe
    (one capability hypothesis, no tools, dummy first/last_seen)."""

    return DiscoveryCandidate(
        id=provider_id,
        display_name=provider_id,
        vendor=provider_id.split("/", 1)[0],
        vendor_url=f"https://{provider_id.split('/', 1)[0]}.example",
        provider_type="mcp_server",
        capabilities=(CandidateCapability(id=capability, confidence=0.7),),
        source="test",
        first_seen_at="2026-05-15",
        last_seen_at="2026-05-15",
        evidence_url=f"https://{provider_id.split('/', 1)[0]}.example",
    )


class _StubStore:
    """In-memory replacement for the ``RoutingDiscoveryStore`` facade.

    We only need ``save_merge`` to record what was passed and ``load``
    to be a no-op for tests that walk the wrapper end to end. Tests
    inject an instance via ``patch.object(store_module, ...)``.
    """

    def __init__(self):
        self.save_merge_calls: list[list[DiscoveryCandidate]] = []
        self.fail_save: bool = False

    def load(self):
        return []

    def save_merge(self, candidates):
        if self.fail_save:
            raise RuntimeError("simulated persistence failure")
        self.save_merge_calls.append(list(candidates))


class _StubDispatcherFactory:
    """Builds a stub ScoutDispatcher whose ``dispatch`` returns canned
    candidates per (capability, task_text) pair. Records every call so
    tests can assert on the order, the per-scout overrides, and the
    cap behaviour.
    """

    def __init__(self, results_by_capability: dict[str, list[DiscoveryCandidate]]):
        self._results = results_by_capability
        self.dispatch_calls: list[dict[str, object]] = []

    def __call__(self, *_args, **_kwargs):
        return self  # the dispatcher itself; ScoutDispatcher(scouts) → us

    def dispatch(self, *, capability, task_description, per_scout_query_overrides=None):
        self.dispatch_calls.append(
            {
                "capability": capability,
                "task_description": task_description,
                "per_scout_query_overrides": dict(per_scout_query_overrides or {}),
            }
        )

        class _DispatchResult:
            def __init__(self, capability, candidates):
                self._capability = capability
                self.merged_candidates = list(candidates)

            def to_summary(self):
                return {
                    "capability": self._capability,
                    "merged_candidate_count": len(self.merged_candidates),
                }

        return _DispatchResult(
            capability, self._results.get(capability, [])
        )


class _StubExpansion:
    used_llm = False
    fallback_reason = "stubbed for tests"
    per_scout_queries: dict[str, str] = {}


class RunPrePlanDiscoveryHelperTests(unittest.TestCase):
    """Unit tests for ``run_pre_plan_discovery`` itself."""

    def test_skipped_when_no_decomposed_sub_tasks(self) -> None:
        """No sub-tasks ⇒ no scouts, no persistence, status reflects it."""

        result = ppd.run_pre_plan_discovery(
            goal="anything",
            decomposed_sub_tasks=[],
            store_url="dummy",
        )
        self.assertEqual("skipped_no_sub_tasks", result["status"])
        self.assertNotIn("capabilities_dispatched", result)

    def test_skipped_when_sub_tasks_have_no_capability_ids(self) -> None:
        """Sub-tasks without ``suggested_capability_id`` don't drive
        scout dispatch — the capability id is the only field that maps
        cleanly onto the dispatcher's contract. Status reflects 'we
        tried but had nothing routable'."""

        result = ppd.run_pre_plan_discovery(
            goal="anything",
            decomposed_sub_tasks=[
                {"description": "verb without slug", "search_query": "x"},
                "not even a dict",
            ],
            store_url="dummy",
        )
        self.assertEqual("skipped_no_capabilities", result["status"])

    def test_dispatches_scouts_and_persists_in_capability_order(self) -> None:
        """Happy path: two distinct capabilities ⇒ two dispatches in
        decomposer-emitted order, with the per-sub-task search_query
        used as the dispatcher's task_description. Returned summary
        includes capabilities_dispatched, persisted_count, and the
        per-capability breakdown the route surfaces in the response."""

        candidates_for_alpha = [
            _make_candidate("alpha/one", capability="alpha_cap"),
            _make_candidate("alpha/two", capability="alpha_cap"),
        ]
        candidates_for_beta = [
            _make_candidate("beta/one", capability="beta_cap"),
        ]
        dispatcher = _StubDispatcherFactory(
            {
                "alpha_cap": candidates_for_alpha,
                "beta_cap": candidates_for_beta,
            }
        )
        store = _StubStore()

        with patch.object(
            ppd, "_maybe_chat_client_for_query_expansion", lambda: None
        ), patch(
            "planmyagents_api.discovery.scouts.ScoutDispatcher", dispatcher
        ), patch(
            "planmyagents_api.discovery.scouts.default_scouts", lambda: []
        ), patch.object(
            store_module, "discovery_store_for_path", lambda _u: store
        ), patch(
            "planmyagents_api.discovery.query_expansion.expand_for_scouts",
            lambda **_: _StubExpansion(),
        ):
            result = ppd.run_pre_plan_discovery(
                goal="raw goal text fallback",
                decomposed_sub_tasks=[
                    {
                        "suggested_capability_id": "alpha_cap",
                        "search_query": "alpha-shaped query",
                    },
                    {
                        "suggested_capability_id": "beta_cap",
                        "search_query": "beta-shaped query",
                    },
                ],
                store_url="dummy",
            )

        self.assertEqual("ran", result["status"])
        self.assertEqual(["alpha_cap", "beta_cap"], result["capabilities_dispatched"])
        self.assertEqual(3, result["persisted_count"])
        self.assertEqual(
            ["alpha_cap", "beta_cap"], result["new_candidate_capabilities"]
        )
        # save_merge called exactly once with the union of all three
        # candidates, in the order scouts produced them.
        self.assertEqual(1, len(store.save_merge_calls))
        self.assertEqual(
            ["alpha/one", "alpha/two", "beta/one"],
            [c.id for c in store.save_merge_calls[0]],
        )
        # Dispatcher saw the LLM-written search_query as task_description,
        # not the raw goal.
        self.assertEqual(
            "alpha-shaped query", dispatcher.dispatch_calls[0]["task_description"]
        )

    def test_falls_back_to_raw_goal_when_search_query_missing(self) -> None:
        """A sub-task that doesn't include its own search_query falls
        back to the raw goal text. The per-capability summary records
        the source so the response can show provenance."""

        store = _StubStore()
        candidates = [_make_candidate("only/one", capability="only_cap")]
        dispatcher = _StubDispatcherFactory({"only_cap": candidates})

        with patch.object(
            ppd, "_maybe_chat_client_for_query_expansion", lambda: None
        ), patch(
            "planmyagents_api.discovery.scouts.ScoutDispatcher", dispatcher
        ), patch(
            "planmyagents_api.discovery.scouts.default_scouts", lambda: []
        ), patch.object(
            store_module, "discovery_store_for_path", lambda _u: store
        ), patch(
            "planmyagents_api.discovery.query_expansion.expand_for_scouts",
            lambda **_: _StubExpansion(),
        ):
            result = ppd.run_pre_plan_discovery(
                goal="raw goal text",
                decomposed_sub_tasks=[
                    {"suggested_capability_id": "only_cap"},
                ],
                store_url="dummy",
            )

        self.assertEqual("ran", result["status"])
        self.assertEqual("raw goal text", dispatcher.dispatch_calls[0]["task_description"])
        per_cap = result["per_capability"][0]
        self.assertEqual("raw_goal", per_cap["scout_task_text_source"])

    def test_capability_cap_is_enforced(self) -> None:
        """When the decomposer emits more than ``max_capabilities``
        sub-tasks, only the first N drive scouts. This is the worst-
        case-latency guard for /goal — without it a 10-sub-task
        decomposition would dispatch 10 × 28s of scouts."""

        store = _StubStore()
        candidates_per_cap = {
            f"cap_{i}": [_make_candidate(f"src/{i}", capability=f"cap_{i}")]
            for i in range(5)
        }
        dispatcher = _StubDispatcherFactory(candidates_per_cap)

        with patch.object(
            ppd, "_maybe_chat_client_for_query_expansion", lambda: None
        ), patch(
            "planmyagents_api.discovery.scouts.ScoutDispatcher", dispatcher
        ), patch(
            "planmyagents_api.discovery.scouts.default_scouts", lambda: []
        ), patch.object(
            store_module, "discovery_store_for_path", lambda _u: store
        ), patch(
            "planmyagents_api.discovery.query_expansion.expand_for_scouts",
            lambda **_: _StubExpansion(),
        ):
            result = ppd.run_pre_plan_discovery(
                goal="x",
                decomposed_sub_tasks=[
                    {"suggested_capability_id": f"cap_{i}", "search_query": "q"}
                    for i in range(5)
                ],
                store_url="dummy",
                max_capabilities=2,  # explicit override
            )

        self.assertEqual("ran", result["status"])
        self.assertEqual(["cap_0", "cap_1"], result["capabilities_dispatched"])
        self.assertEqual(2, len(dispatcher.dispatch_calls))

    def test_duplicate_capabilities_consume_one_slot(self) -> None:
        """If two sub-tasks resolve to the same capability slug, the
        second should NOT consume a scout slot — that would burn the
        cap on duplicate work and leave room for one fewer real
        capability."""

        store = _StubStore()
        candidates = [_make_candidate("only/one", capability="shared")]
        dispatcher = _StubDispatcherFactory({"shared": candidates, "other": candidates})

        with patch.object(
            ppd, "_maybe_chat_client_for_query_expansion", lambda: None
        ), patch(
            "planmyagents_api.discovery.scouts.ScoutDispatcher", dispatcher
        ), patch(
            "planmyagents_api.discovery.scouts.default_scouts", lambda: []
        ), patch.object(
            store_module, "discovery_store_for_path", lambda _u: store
        ), patch(
            "planmyagents_api.discovery.query_expansion.expand_for_scouts",
            lambda **_: _StubExpansion(),
        ):
            result = ppd.run_pre_plan_discovery(
                goal="x",
                decomposed_sub_tasks=[
                    {"suggested_capability_id": "shared", "search_query": "q1"},
                    {"suggested_capability_id": "shared", "search_query": "q2"},
                    {"suggested_capability_id": "other", "search_query": "q3"},
                ],
                store_url="dummy",
                max_capabilities=2,
            )

        self.assertEqual(["shared", "other"], result["capabilities_dispatched"])

    def test_persistence_failure_degrades_to_persistence_failed_status(self) -> None:
        """A save_merge failure is caught and reflected in the status —
        the wrapper must NOT raise, since the rest of /goal can still
        return a useful refusal even without persistence."""

        store = _StubStore()
        store.fail_save = True
        candidates = [_make_candidate("x/one", capability="cap")]
        dispatcher = _StubDispatcherFactory({"cap": candidates})

        with patch.object(
            ppd, "_maybe_chat_client_for_query_expansion", lambda: None
        ), patch(
            "planmyagents_api.discovery.scouts.ScoutDispatcher", dispatcher
        ), patch(
            "planmyagents_api.discovery.scouts.default_scouts", lambda: []
        ), patch.object(
            store_module, "discovery_store_for_path", lambda _u: store
        ), patch(
            "planmyagents_api.discovery.query_expansion.expand_for_scouts",
            lambda **_: _StubExpansion(),
        ):
            result = ppd.run_pre_plan_discovery(
                goal="x",
                decomposed_sub_tasks=[
                    {"suggested_capability_id": "cap", "search_query": "q"}
                ],
                store_url="dummy",
            )

        self.assertEqual("persistence_failed", result["status"])
        self.assertEqual(0, result["persisted_count"])
        self.assertIn("simulated persistence failure", result["persistence_error"])

    def test_per_capability_dispatch_failure_does_not_abort_stage(self) -> None:
        """A scout dispatch raising for one capability must not abort
        the stage — every other capability still runs and the failed
        capability is recorded with status="error"."""

        candidates = [_make_candidate("good/one", capability="good_cap")]

        class _FailingDispatcher:
            def __init__(self):
                self.calls = 0

            def __call__(self, *_a, **_kw):
                return self

            def dispatch(self, *, capability, **_kwargs):
                self.calls += 1
                if capability == "boom_cap":
                    raise RuntimeError("simulated scout failure")

                class _Result:
                    merged_candidates = list(candidates)

                    def to_summary(self):
                        return {"capability": capability}

                return _Result()

        dispatcher = _FailingDispatcher()
        store = _StubStore()

        with patch.object(
            ppd, "_maybe_chat_client_for_query_expansion", lambda: None
        ), patch(
            "planmyagents_api.discovery.scouts.ScoutDispatcher", dispatcher
        ), patch(
            "planmyagents_api.discovery.scouts.default_scouts", lambda: []
        ), patch.object(
            store_module, "discovery_store_for_path", lambda _u: store
        ), patch(
            "planmyagents_api.discovery.query_expansion.expand_for_scouts",
            lambda **_: _StubExpansion(),
        ):
            result = ppd.run_pre_plan_discovery(
                goal="x",
                decomposed_sub_tasks=[
                    {"suggested_capability_id": "boom_cap", "search_query": "q"},
                    {"suggested_capability_id": "good_cap", "search_query": "q"},
                ],
                store_url="dummy",
            )

        self.assertEqual("ran", result["status"])
        # Both capabilities were attempted; only the good one
        # contributed a candidate.
        self.assertEqual(["boom_cap", "good_cap"], result["capabilities_dispatched"])
        self.assertEqual(["good_cap"], result["new_candidate_capabilities"])
        statuses = {entry["capability"]: entry["status"] for entry in result["per_capability"]}
        self.assertEqual({"boom_cap": "error", "good_cap": "ok"}, statuses)


class PrePlanDiscoveryGateTests(unittest.TestCase):
    """The boolean gate keys off an env var. Default OFF in tests; the
    runtime check honours an explicit ``true`` override."""

    def setUp(self) -> None:
        self._prev = os.environ.get("PLANMYAGENTS_PRE_PLAN_DISCOVERY")

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("PLANMYAGENTS_PRE_PLAN_DISCOVERY", None)
        else:
            os.environ["PLANMYAGENTS_PRE_PLAN_DISCOVERY"] = self._prev

    def test_default_false_in_tests(self) -> None:
        """The package-level conftest in ``tests/__init__.py`` sets the
        env to ``"false"`` so this is the test-default behaviour."""

        os.environ.pop("PLANMYAGENTS_PRE_PLAN_DISCOVERY", None)
        # Re-set explicitly to the false value the conftest installs,
        # since some other test in the same process may have raised it.
        os.environ["PLANMYAGENTS_PRE_PLAN_DISCOVERY"] = "false"
        self.assertFalse(ppd.pre_plan_discovery_enabled())

    def test_true_yes_on_one_all_enable(self) -> None:
        for value in ("true", "TRUE", "yes", "on", "1"):
            os.environ["PLANMYAGENTS_PRE_PLAN_DISCOVERY"] = value
            self.assertTrue(
                ppd.pre_plan_discovery_enabled(),
                f"expected enabled for value {value!r}",
            )

    def test_garbage_values_default_to_false(self) -> None:
        """Defensive: a typo'd env var (``"yse"``, ``"truee"``) reads
        as 'not enabled' rather than as some surprising default."""

        for value in ("yse", "truee", "yeah", "no"):
            os.environ["PLANMYAGENTS_PRE_PLAN_DISCOVERY"] = value
            self.assertFalse(
                ppd.pre_plan_discovery_enabled(),
                f"expected disabled for value {value!r}",
            )


class PlanGoalWithPrePlanDiscoveryWrapperTests(unittest.TestCase):
    """Wrapper-level tests: gate, executable short-circuit, metadata
    contract that ``/goal`` reads to skip duplicate ``_live_discovery``."""

    def setUp(self) -> None:
        self._prev = os.environ.get("PLANMYAGENTS_PRE_PLAN_DISCOVERY")

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("PLANMYAGENTS_PRE_PLAN_DISCOVERY", None)
        else:
            os.environ["PLANMYAGENTS_PRE_PLAN_DISCOVERY"] = self._prev

    def _stub_plan_goal_smart(self, *, executable: bool, decomposed=None):
        """Build a callable that mimics ``plan_goal_smart``'s return shape
        (plan, metadata) without invoking the real planner."""

        from planmyagents_api.planner.goal import GoalPlan, PlannedSubTask

        if executable:
            plan = GoalPlan(
                status="executable",
                summary="ok",
                sub_tasks=[
                    PlannedSubTask(
                        capability="email_verification",
                        description="placeholder sub-task for the executable hot path test",
                        inputs={"email": "test@example.com"},
                    )
                ],
            )
        else:
            plan = GoalPlan(
                status="unsupported",
                summary="no",
                sub_tasks=[],
                missing_capabilities=["alpha_cap"],
            )
        metadata: dict = {
            "mode": "escalating",
            "decomposed_sub_tasks": list(decomposed or []),
        }
        return lambda *_args, **_kwargs: (plan, metadata)

    def test_disabled_gate_returns_disabled_status_and_skips_dispatch(self) -> None:
        from planmyagents_api.web import planning as planning_module

        os.environ["PLANMYAGENTS_PRE_PLAN_DISCOVERY"] = "false"
        called: list = []

        def _exploding_run(**_kwargs):
            called.append("ran")
            raise AssertionError("run_pre_plan_discovery must not be called when gate is off")

        with patch.object(
            planning_module,
            "plan_goal_smart",
            self._stub_plan_goal_smart(executable=False),
        ), patch.object(planning_module, "run_pre_plan_discovery", _exploding_run):
            _, metadata = planning_module.plan_goal_with_pre_plan_discovery(
                "any goal"
            )

        self.assertEqual({"status": "disabled"}, metadata["pre_plan_discovery"])
        self.assertEqual([], called)

    def test_executable_first_pass_short_circuits_even_when_enabled(self) -> None:
        """An executable plan from the first planner pass means there's
        nothing to discover — the user has a runnable plan. The wrapper
        MUST NOT add latency in this case, because that's the hot path."""

        from planmyagents_api.web import planning as planning_module

        os.environ["PLANMYAGENTS_PRE_PLAN_DISCOVERY"] = "true"
        called: list = []

        def _exploding_run(**_kwargs):
            called.append("ran")
            raise AssertionError(
                "run_pre_plan_discovery must not be called for executable plans"
            )

        with patch.object(
            planning_module,
            "plan_goal_smart",
            self._stub_plan_goal_smart(executable=True),
        ), patch.object(planning_module, "run_pre_plan_discovery", _exploding_run):
            _, metadata = planning_module.plan_goal_with_pre_plan_discovery(
                "any goal"
            )

        self.assertEqual(
            {"status": "skipped_executable_plan"},
            metadata["pre_plan_discovery"],
        )
        self.assertEqual([], called)

    def test_unsupported_plan_with_no_decomposed_sub_tasks_skips(self) -> None:
        """Decomposer ran but emitted nothing usable (LLM tier failed,
        decomposer disabled, etc.) ⇒ wrapper has no input for scouts.
        Status reflects it; downstream refusal-path discovery still runs
        as a fallback."""

        from planmyagents_api.web import planning as planning_module

        os.environ["PLANMYAGENTS_PRE_PLAN_DISCOVERY"] = "true"
        called: list = []

        def _exploding_run(**_kwargs):
            called.append("ran")
            raise AssertionError("must not run without decomposed sub-tasks")

        with patch.object(
            planning_module,
            "plan_goal_smart",
            self._stub_plan_goal_smart(executable=False, decomposed=[]),
        ), patch.object(planning_module, "run_pre_plan_discovery", _exploding_run):
            _, metadata = planning_module.plan_goal_with_pre_plan_discovery(
                "any goal"
            )

        self.assertEqual(
            {"status": "skipped_no_decomposed_sub_tasks"},
            metadata["pre_plan_discovery"],
        )
        self.assertEqual([], called)

    def test_unsupported_plan_runs_discovery_and_threads_summary_into_metadata(
        self,
    ) -> None:
        """Happy path: refused plan + decomposed sub-tasks + gate ON ⇒
        discovery runs and its summary appears under
        ``metadata['pre_plan_discovery']``. This is the contract the
        ``/goal`` route depends on for skipping duplicate live
        dispatch."""

        from planmyagents_api.web import planning as planning_module

        os.environ["PLANMYAGENTS_PRE_PLAN_DISCOVERY"] = "true"

        captured_call: dict = {}

        def _capturing_run(*, goal, decomposed_sub_tasks, store_url):
            captured_call["goal"] = goal
            captured_call["sub_tasks"] = list(decomposed_sub_tasks)
            captured_call["store_url"] = store_url
            return {
                "status": "ran",
                "capabilities_dispatched": ["alpha_cap"],
                "persisted_count": 2,
                "new_candidate_capabilities": ["alpha_cap"],
            }

        decomposed = [
            {"suggested_capability_id": "alpha_cap", "search_query": "alpha-shaped"},
        ]
        with patch.object(
            planning_module,
            "plan_goal_smart",
            self._stub_plan_goal_smart(executable=False, decomposed=decomposed),
        ), patch.object(planning_module, "run_pre_plan_discovery", _capturing_run):
            _, metadata = planning_module.plan_goal_with_pre_plan_discovery(
                "buy me something"
            )

        self.assertEqual("buy me something", captured_call["goal"])
        self.assertEqual(decomposed, captured_call["sub_tasks"])
        self.assertEqual("ran", metadata["pre_plan_discovery"]["status"])
        self.assertEqual(
            ["alpha_cap"], metadata["pre_plan_discovery"]["capabilities_dispatched"]
        )

    def test_inner_failure_is_caught_and_reflected_as_error_status(self) -> None:
        """An unexpected exception inside ``run_pre_plan_discovery``
        must not propagate out of the wrapper — the planner's plan and
        decomposer metadata are still useful, and the route must keep
        returning something."""

        from planmyagents_api.web import planning as planning_module

        os.environ["PLANMYAGENTS_PRE_PLAN_DISCOVERY"] = "true"

        def _exploding_run(**_kwargs):
            raise RuntimeError("simulated module-level failure")

        decomposed = [
            {"suggested_capability_id": "alpha_cap", "search_query": "q"}
        ]
        with patch.object(
            planning_module,
            "plan_goal_smart",
            self._stub_plan_goal_smart(executable=False, decomposed=decomposed),
        ), patch.object(planning_module, "run_pre_plan_discovery", _exploding_run):
            plan, metadata = planning_module.plan_goal_with_pre_plan_discovery(
                "x"
            )

        self.assertEqual("unsupported", plan.status)
        self.assertEqual("error", metadata["pre_plan_discovery"]["status"])
        self.assertIn("simulated module-level failure", metadata["pre_plan_discovery"]["error"])


if __name__ == "__main__":
    unittest.main()
