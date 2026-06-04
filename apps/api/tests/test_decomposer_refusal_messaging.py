"""Unit tests for the post-Fix-2 ``_apply_decomposer`` refusal branches.

Why this file exists
--------------------
Fix 1 made the decomposer correctly reuse router-supported capability
slugs for goals like the gift-buying example. Fix 2a added an honest
"planner-synthesis" refusal message when ``missing_capabilities=[]``
but the constrained planner still refused.

Fix 2b — the version pinned by these tests — extends the refusal
contract three more ways:

1. Decomposed sub-tasks are ALWAYS surfaced on ``plan.sub_tasks``
   when the decomposer ran. The UI needs cards to render and the
   discovery layer collects capabilities from ``plan.sub_tasks``
   in addition to ``plan.missing_capabilities``.

2. ``router.known_capabilities()`` only tells us "the catalog has
   this slug" — it does NOT mean a provider can fulfil it. Fix 2b
   computes per-capability provider availability via
   ``router.route(cap)`` and partitions every reused slug into
   ``executable`` / ``configurable`` / ``no_provider``.

3. Capabilities with no provider in the registry are added to
   ``missing_capabilities`` (so the discovery scouts hunt for
   them) and named in a per-capability refusal reason. The user
   stops seeing the misleading "0 candidates / refused" with no
   explanation for the gift-goal failure mode.

Test strategy
-------------
All tests exercise ``_apply_decomposer`` directly with hand-built
inputs (a synthetic ``GoalPlan``, a hand-rolled
``CapabilityCatalog``, a configurable :class:`_FakeRouter`, and a
patched ``decompose_goal``). No live LLMs, no live registry. The
fake router lets each test pin the per-capability provider
availability surface independently of the shipped ``agents.json``.
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.planner.capability_catalog import CapabilityCatalog
from planmyagents_api.planner.goal import GoalPlan
from planmyagents_api.planner.goal_decomposer import (
    DecomposedSubTask,
    GoalDecomposition,
)


@dataclass
class _FakeRouteDecision:
    """Mirrors the public surface of
    :class:`planmyagents_api.agents.router.RouteDecision` that
    ``_apply_decomposer`` reads (``provider_id``, ``candidates``,
    ``reason``)."""

    capability: str
    provider_id: str | None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


class _FakeRouter:
    """Minimal stand-in for ``ProviderRouter`` that supports the two
    methods ``_apply_decomposer`` reaches for: ``known_capabilities``
    (the catalog of slugs the planner code knows about) and
    ``route(cap)`` (per-capability provider availability).

    Each test sets up the fake with three optional inputs so the
    test harness can model the full provider-status spectrum:

    * ``catalog``      — slugs in ``known_capabilities``
    * ``executable``   — slugs that should route to a working
                         provider (``provider_id`` non-None)
    * ``configurable`` — slugs that have at least one candidate
                         provider but no ``provider_id``; the
                         test supplies the candidate ids and the
                         env var the user would need to set

    Any slug NOT in any of those buckets routes to a ``no_provider``
    decision (provider_id=None, candidates=[]). That's the default
    for the gift-goal style "router knows the slug but the
    registry has 0 providers" failure mode.
    """

    def __init__(
        self,
        catalog: set[str] | None = None,
        *,
        executable: set[str] | None = None,
        configurable: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._catalog = set(catalog or set())
        self._executable = set(executable or set())
        self._configurable = dict(configurable or {})

    def known_capabilities(self) -> set[str]:
        return set(self._catalog)

    def route(self, capability: str) -> _FakeRouteDecision:
        if capability in self._executable:
            return _FakeRouteDecision(
                capability=capability,
                provider_id=f"{capability}-provider",
                candidates=[
                    {
                        "id": f"{capability}-provider",
                        "auth": {"env_var": None},
                    }
                ],
                reason="exec",
            )
        if capability in self._configurable:
            spec = self._configurable[capability]
            providers = spec.get("providers", [])
            env_var = spec.get("env_var")
            return _FakeRouteDecision(
                capability=capability,
                provider_id=None,
                candidates=[
                    {"id": p, "auth": {"env_var": env_var}} for p in providers
                ],
                reason=f"configurable; set {env_var}",
            )
        return _FakeRouteDecision(
            capability=capability,
            provider_id=None,
            candidates=[],
            reason=f"No provider in the registry supports capability `{capability}`.",
        )


def _decomposition(
    *,
    sub_tasks_caps: list[str],
    reused: list[str] | None = None,
    coined: list[str] | None = None,
    intent_summary: str = "test intent",
) -> GoalDecomposition:
    """Hand-roll a :class:`GoalDecomposition` with one
    ``DecomposedSubTask`` per slug in ``sub_tasks_caps``.

    ``catalog_reused_capabilities`` and ``new_capabilities`` are
    threaded in literally so each test pins the exact partition the
    decomposer would have produced. The partition matters because
    the refusal branch uses it to render the "Capabilities
    considered" line — testing the message requires knowing what
    that list will be."""

    return GoalDecomposition(
        intent_summary=intent_summary,
        sub_tasks=[
            DecomposedSubTask(
                description=f"do {cap}",
                user_facing_step=f"do {cap}",
                search_query=f"{cap} api",
                acceptance_criteria=f"agent does {cap}",
                suggested_capability_id=cap,
            )
            for cap in sub_tasks_caps
        ],
        confidence=0.9,
        catalog_reused_capabilities=list(reused or []),
        new_capabilities=list(coined or []),
    )


class _ApplyDecomposerHarness:
    """Common setup the per-test classes share.

    ``_apply_decomposer`` lives in ``planmyagents_api.web.planning``
    and imports ``decompose_goal`` at module level; the patch target
    is the module-local symbol, not the original import. Tests use
    ``patch.object(planning_module, "decompose_goal", ...)`` to
    inject a deterministic decomposition.

    The label reconciler and slug canonicalisation are disabled via
    env vars so the decomposer output flows through unchanged.
    """

    def setUp(self) -> None:
        self._previous_env: dict[str, str | None] = {}
        for key, value in (
            ("PLANMYAGENTS_LABEL_RECONCILER", "off"),
            ("PLANMYAGENTS_SLUG_CANONICALIZATION", "false"),
        ):
            self._previous_env[key] = os.environ.get(key)
            os.environ[key] = value

    def tearDown(self) -> None:
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _call(
        self,
        *,
        original_plan: GoalPlan,
        decomposition: GoalDecomposition,
        router: _FakeRouter,
    ):
        """Invoke ``_apply_decomposer`` with the supplied harness
        state. Returns the ``(plan, metadata)`` tuple."""

        from planmyagents_api.web import planning as planning_module

        with (
            patch.object(
                planning_module, "decompose_goal", return_value=decomposition
            ),
            patch(
                "planmyagents_api.web.planning.describe_capabilities",
                return_value={},
            ),
        ):
            return planning_module._apply_decomposer(
                plan=original_plan,
                goal="test goal",
                capability_catalog=CapabilityCatalog(aliases_by_capability={}),
                router=router,
            )


class CoinedSlugsRefusalReasons(_ApplyDecomposerHarness, unittest.TestCase):
    """When the decomposer coins slugs the registry doesn't know
    about, the refusal reasons name those slugs in the
    "New capability slugs (not in registry):" line and
    ``missing_capabilities`` includes them so discovery fires."""

    def test_one_coined_one_executable_lists_both_in_distinct_lines(self) -> None:
        original = GoalPlan(
            status="unsupported",
            summary="rule planner refused",
            refusal_reasons=["rule-planner-reason"],
            missing_capabilities=["some_other_slug"],
        )
        decomposition = _decomposition(
            sub_tasks_caps=["web_search", "unobtainable_capability"],
            reused=["web_search"],
            coined=["unobtainable_capability"],
        )
        router = _FakeRouter(
            catalog={"web_search"},
            executable={"web_search"},
        )
        plan, metadata = self._call(
            original_plan=original,
            decomposition=decomposition,
            router=router,
        )

        self.assertEqual(plan.status, "unsupported")
        # Coined slug must land in missing so discovery scouts hunt.
        self.assertEqual(plan.missing_capabilities, ["unobtainable_capability"])
        joined = " ".join(plan.refusal_reasons).lower()
        # Each capability gets its own honest line.
        self.assertIn("executable today: web_search", joined)
        self.assertIn(
            "new capability slugs (not in registry): unobtainable_capability",
            joined,
        )
        # The misleading "registry cannot route" boilerplate must be
        # gone — Fix 2b replaced it with per-capability reasons.
        self.assertNotIn("registry cannot route at least one", joined)

        # Metadata pins the per-cap partition the UI can render.
        self.assertEqual(
            metadata["decomposer"]["executable_capabilities"], ["web_search"]
        )
        self.assertEqual(
            metadata["decomposer"]["no_provider_capabilities"], []
        )

    def test_all_slugs_coined_lands_in_missing_with_discovery_hint(self) -> None:
        original = GoalPlan(
            status="unsupported",
            summary="rule planner refused",
            refusal_reasons=["rule-planner-reason"],
            missing_capabilities=["a", "b"],
        )
        decomposition = _decomposition(
            sub_tasks_caps=["coined_a", "coined_b"],
            reused=[],
            coined=["coined_a", "coined_b"],
        )
        router = _FakeRouter(catalog=set())
        plan, _ = self._call(
            original_plan=original,
            decomposition=decomposition,
            router=router,
        )
        self.assertEqual(plan.status, "unsupported")
        self.assertEqual(plan.missing_capabilities, ["coined_a", "coined_b"])
        joined = " ".join(plan.refusal_reasons).lower()
        self.assertIn(
            "new capability slugs (not in registry): coined_a, coined_b",
            joined,
        )
        self.assertIn("discovery scouts will run", joined)


class AllReusedNoProviderRefusalReasons(
    _ApplyDecomposerHarness, unittest.TestCase
):
    """The exact gift-goal failure mode: every slug the decomposer
    suggests IS in the catalog, but the registry has zero providers
    backing them. The refusal must name each slug and tell the user
    discovery scouts will hunt."""

    def test_gift_goal_shape_lists_all_caps_under_no_provider(self) -> None:
        original = GoalPlan(
            status="unsupported",
            summary="rule planner refused",
            refusal_reasons=[
                "Original planner hallucinated something, doesn't matter."
            ],
            missing_capabilities=["a_hallucinated_slug"],
        )
        gift_caps = [
            "web_search",
            "store_locator",
            "price_comparison",
            "payment_authorization",
            "shipping_quote",
        ]
        decomposition = _decomposition(
            sub_tasks_caps=gift_caps,
            reused=gift_caps,
            coined=[],
            intent_summary="Buy a birthday gift for a 3-year-old",
        )
        # Catalog knows all 5; router has no provider for any of them
        # (the bedrock failure mode the user reported).
        router = _FakeRouter(catalog=set(gift_caps))
        plan, metadata = self._call(
            original_plan=original,
            decomposition=decomposition,
            router=router,
        )
        self.assertEqual(plan.status, "unsupported")
        # Every reused capability with no provider lands in missing
        # so the discovery layer fires for them.
        self.assertEqual(plan.missing_capabilities, sorted(gift_caps))

        joined = " ".join(plan.refusal_reasons).lower()
        # The "registry cannot route" boilerplate is gone.
        self.assertNotIn("registry cannot route at least one", joined)
        # The honest line names every capability.
        self.assertIn("no provider in registry for:", joined)
        for cap in gift_caps:
            self.assertIn(cap, joined)
        # The user is told what we're going to do about it.
        self.assertIn("discovery scouts will hunt", joined)

        # Sub-task count surfaces so the user knows decomposition
        # granularity.
        self.assertIn(f"{len(gift_caps)} sub-task", joined)

        # Sanity-check the decomposer metadata is intact.
        self.assertEqual(metadata["decomposer"]["status"], "applied")
        self.assertEqual(
            metadata["decomposer"]["no_provider_capabilities"],
            sorted(gift_caps),
        )

    def test_one_reused_no_provider_still_uses_per_cap_message(self) -> None:
        """One sub-task that's catalog-known but provider-less. The
        per-cap message must still fire instead of falling back to a
        generic "everything refused" boilerplate."""

        original = GoalPlan(
            status="unsupported",
            summary="x",
            refusal_reasons=["original"],
            missing_capabilities=["something"],
        )
        decomposition = _decomposition(
            sub_tasks_caps=["web_search"],
            reused=["web_search"],
            coined=[],
        )
        router = _FakeRouter(catalog={"web_search"})  # no provider
        plan, _ = self._call(
            original_plan=original,
            decomposition=decomposition,
            router=router,
        )
        self.assertEqual(plan.missing_capabilities, ["web_search"])
        joined = " ".join(plan.refusal_reasons).lower()
        self.assertNotIn("registry cannot route at least one", joined)
        self.assertIn("no provider in registry for: web_search", joined)
        self.assertIn("1 sub-task", joined)


class ConfigurableProviderRefusalReasons(
    _ApplyDecomposerHarness, unittest.TestCase
):
    """When the registry has providers but they're not configured
    (env vars missing), the refusal must tell the user which env
    var to set instead of recording the capability as missing."""

    def test_configurable_lists_env_var_and_provider_id(self) -> None:
        original = GoalPlan(
            status="unsupported",
            summary="x",
            refusal_reasons=["original"],
            missing_capabilities=["unrelated"],
        )
        decomposition = _decomposition(
            sub_tasks_caps=["payment_authorization"],
            reused=["payment_authorization"],
        )
        router = _FakeRouter(
            catalog={"payment_authorization"},
            configurable={
                "payment_authorization": {
                    "providers": ["razorpay-payments"],
                    "env_var": "RAZORPAY_KEY_ID",
                },
            },
        )
        plan, metadata = self._call(
            original_plan=original,
            decomposition=decomposition,
            router=router,
        )
        # Configurable capabilities are NOT marked missing — that
        # would be wrong; the user can fix by setting an env var,
        # not by waiting for discovery to find a new provider.
        self.assertEqual(plan.missing_capabilities, [])
        joined = " ".join(plan.refusal_reasons).lower()
        self.assertIn("configurable: `payment_authorization`", joined)
        self.assertIn("set razorpay_key_id", joined)
        self.assertIn("razorpay-payments", joined)

        # Metadata records the partition so the UI can render badges.
        self.assertEqual(
            metadata["decomposer"]["configurable_capabilities"],
            ["payment_authorization"],
        )

    def test_mixed_executable_configurable_no_provider(self) -> None:
        """The full gift-goal partition: one capability per status
        class. Every line must show up in the right order."""

        original = GoalPlan(
            status="unsupported",
            summary="x",
            refusal_reasons=["original"],
            missing_capabilities=[],
        )
        decomposition = _decomposition(
            sub_tasks_caps=[
                "web_search",
                "price_comparison",
                "payment_authorization",
            ],
            reused=[
                "web_search",
                "price_comparison",
                "payment_authorization",
            ],
        )
        router = _FakeRouter(
            catalog={"web_search", "price_comparison", "payment_authorization"},
            executable={"web_search"},
            configurable={
                "price_comparison": {
                    "providers": ["ebay-browse"],
                    "env_var": "EBAY_OAUTH_TOKEN",
                },
            },
            # payment_authorization → no_provider (default)
        )
        plan, metadata = self._call(
            original_plan=original,
            decomposition=decomposition,
            router=router,
        )
        self.assertEqual(plan.missing_capabilities, ["payment_authorization"])
        joined = " ".join(plan.refusal_reasons).lower()
        self.assertIn("executable today: web_search", joined)
        self.assertIn("configurable: `price_comparison`", joined)
        self.assertIn("set ebay_oauth_token", joined)
        self.assertIn(
            "no provider in registry for: payment_authorization", joined
        )
        # Per-cap metadata partition is exhaustive.
        self.assertEqual(
            metadata["decomposer"]["executable_capabilities"], ["web_search"]
        )
        self.assertEqual(
            metadata["decomposer"]["configurable_capabilities"],
            ["price_comparison"],
        )
        self.assertEqual(
            metadata["decomposer"]["no_provider_capabilities"],
            ["payment_authorization"],
        )


class SubTasksAlwaysSurfacedOnPlan(
    _ApplyDecomposerHarness, unittest.TestCase
):
    """The bedrock UI fix: the refused plan MUST carry the
    decomposer's sub-tasks on ``plan.sub_tasks`` so the UI can
    render cards instead of an empty refusal."""

    def test_sub_tasks_present_on_refused_plan(self) -> None:
        original = GoalPlan(
            status="unsupported",
            summary="x",
            refusal_reasons=["original"],
            missing_capabilities=["x"],
        )
        decomposition = _decomposition(
            sub_tasks_caps=[
                "web_search",
                "store_locator",
                "price_comparison",
                "payment_authorization",
            ],
            reused=[
                "web_search",
                "store_locator",
                "price_comparison",
                "payment_authorization",
            ],
        )
        router = _FakeRouter(
            catalog={
                "web_search",
                "store_locator",
                "price_comparison",
                "payment_authorization",
            }
        )
        plan, _ = self._call(
            original_plan=original,
            decomposition=decomposition,
            router=router,
        )
        # 4 sub-tasks on the refused plan — UI cards are now possible.
        self.assertEqual(len(plan.sub_tasks), 4)
        self.assertEqual(
            sorted(t.capability for t in plan.sub_tasks),
            sorted(
                [
                    "web_search",
                    "store_locator",
                    "price_comparison",
                    "payment_authorization",
                ]
            ),
        )
        # Each sub-task carries the LLM-written descriptions on
        # ``inputs`` so downstream consumers (scouts, judge, UI)
        # can read them off ``plan.sub_tasks`` directly.
        for task in plan.sub_tasks:
            self.assertIn("search_query", task.inputs)
            self.assertIn("acceptance_criteria", task.inputs)
            self.assertIn("user_facing_step", task.inputs)

    def test_decomposer_sub_tasks_with_empty_capability_id_are_dropped(
        self,
    ) -> None:
        """Defensive: malformed decomposer output (empty capability)
        must not blow up the refusal — the bad sub-task is silently
        dropped with a log line."""

        original = GoalPlan(
            status="unsupported",
            summary="x",
            refusal_reasons=["original"],
            missing_capabilities=["x"],
        )
        # First sub-task has an empty capability — must be dropped
        # without raising.
        decomposition = GoalDecomposition(
            intent_summary="test",
            sub_tasks=[
                DecomposedSubTask(
                    description="will be dropped",
                    user_facing_step="x",
                    search_query="x",
                    acceptance_criteria="x",
                    suggested_capability_id="placeholder",
                ),
                DecomposedSubTask(
                    description="kept",
                    user_facing_step="x",
                    search_query="x",
                    acceptance_criteria="x",
                    suggested_capability_id="web_search",
                ),
            ],
            confidence=0.9,
            catalog_reused_capabilities=["web_search"],
            new_capabilities=["placeholder"],
        )
        # Override the placeholder sub-task's capability to empty
        # AFTER construction (DecomposedSubTask validates non-empty
        # at __post_init__, so this is the only way to exercise the
        # defensive drop in _planned_sub_tasks_from_decomposition).
        object.__setattr__(decomposition.sub_tasks[0], "suggested_capability_id", "")

        router = _FakeRouter(catalog={"web_search"})
        plan, _ = self._call(
            original_plan=original,
            decomposition=decomposition,
            router=router,
        )
        self.assertEqual(len(plan.sub_tasks), 1)
        self.assertEqual(plan.sub_tasks[0].capability, "web_search")


class SummaryFallbacks(_ApplyDecomposerHarness, unittest.TestCase):
    """The refused plan's summary echoes the decomposer's
    ``intent_summary`` when present, otherwise falls back to a
    static message that names the planner-synthesis vs missing-
    capability distinction."""

    def test_summary_echoes_decomposer_intent_summary(self) -> None:
        original = GoalPlan(
            status="unsupported",
            summary="rule planner summary",
            refusal_reasons=["x"],
            missing_capabilities=["other"],
        )
        decomposition = _decomposition(
            sub_tasks_caps=["web_search"],
            reused=["web_search"],
            intent_summary="Find the perfect gift for a friend",
        )
        router = _FakeRouter(
            catalog={"web_search"}, executable={"web_search"}
        )
        plan, _ = self._call(
            original_plan=original,
            decomposition=decomposition,
            router=router,
        )
        self.assertEqual(plan.summary, "Find the perfect gift for a friend")

    def test_summary_falls_back_when_intent_is_blank(self) -> None:
        original = GoalPlan(
            status="unsupported",
            summary="x",
            refusal_reasons=["x"],
            missing_capabilities=["other"],
        )
        decomposition = _decomposition(
            sub_tasks_caps=["web_search"],
            reused=["web_search"],
            intent_summary="",
        )
        router = _FakeRouter(catalog={"web_search"})
        plan, _ = self._call(
            original_plan=original,
            decomposition=decomposition,
            router=router,
        )
        self.assertIn("decomposes into sub-tasks", plan.summary)
        self.assertIn("cannot run yet", plan.summary)


if __name__ == "__main__":
    unittest.main()
