"""Tests for embedding-driven planner slug canonicalization.

Production maps planner-emitted slugs that don't appear verbatim
in the registry onto the closest registry slug via embedding
similarity. Without it, a planner emission of ``payment_processing``
flows into the scout dispatch with no overlap against the
registry's ``payment_authorization``, and APIs.guru's Adyen
(correctly inferred as ``payment_authorization``) is silently
dropped — exactly the symptom the audit caught.

These tests assume the env var
``PLANMYAGENTS_SLUG_CANONICALIZATION=true`` is set before
``_canonicalize_unsupported_slugs`` is called. The default test
init disables canonicalization to keep older tests green; this
suite re-enables it inside ``setUp``.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.planner.goal_decomposer import (
    DecomposedSubTask,
    GoalDecomposition,
)
from planmyagents_api.web.planning import _canonicalize_unsupported_slugs


def _decomposition_with(*, suggested: str) -> GoalDecomposition:
    """Build a 1-sub-task GoalDecomposition with the given suggested slug."""
    return GoalDecomposition(
        intent_summary="t",
        sub_tasks=[
            DecomposedSubTask(
                description="d",
                user_facing_step="u",
                search_query="q",
                acceptance_criteria="a",
                suggested_capability_id=suggested,
            )
        ],
        confidence=0.9,
        catalog_reused_capabilities=[],
        new_capabilities=[suggested],
        raw_response="{}",
    )


class _StubIndex:
    """Stand-in CapabilityIndex that returns rule-based matches.

    Avoids a real Ollama / hash embedder roundtrip in tests by
    returning configured slug → registry-slug pairs verbatim.
    """

    def __init__(self, mapping: dict[str, str | None]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def match_slug(self, slug: str) -> str | None:
        self.calls.append(slug)
        return self.mapping.get(slug)


class SlugCanonicalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        # The test-package init sets canonicalization to false by
        # default (see apps/api/tests/__init__.py). This suite
        # specifically verifies the canonicalization path, so we
        # turn it back on for the lifetime of each test.
        self._prev_env = os.environ.get("PLANMYAGENTS_SLUG_CANONICALIZATION")
        os.environ["PLANMYAGENTS_SLUG_CANONICALIZATION"] = "true"

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop("PLANMYAGENTS_SLUG_CANONICALIZATION", None)
        else:
            os.environ["PLANMYAGENTS_SLUG_CANONICALIZATION"] = self._prev_env

    def test_planner_slug_with_supported_match_is_rewritten(self) -> None:
        decomposition = _decomposition_with(suggested="payment_processing")
        index = _StubIndex({"payment_processing": "payment_authorization"})
        with patch(
            "planmyagents_api.discovery.capability_index.get_default_capability_index",
            return_value=index,
        ):
            rewritten, metadata = _canonicalize_unsupported_slugs(
                decomposition=decomposition,
                supported={"payment_authorization", "email_send"},
            )
        self.assertEqual(metadata["status"], "applied")
        self.assertEqual(metadata["rewrites"], [
            {"from": "payment_processing", "to": "payment_authorization"},
        ])
        self.assertEqual(
            rewritten.sub_tasks[0].suggested_capability_id,
            "payment_authorization",
        )
        # The new_capabilities partition was updated so the rewritten
        # slug is no longer counted as "newly coined".
        self.assertEqual(rewritten.new_capabilities, [])
        self.assertIn(
            "payment_authorization",
            rewritten.catalog_reused_capabilities,
        )

    def test_unmatched_slug_flows_through(self) -> None:
        decomposition = _decomposition_with(suggested="kyc_aml_check")
        index = _StubIndex({"kyc_aml_check": None})
        with patch(
            "planmyagents_api.discovery.capability_index.get_default_capability_index",
            return_value=index,
        ):
            rewritten, metadata = _canonicalize_unsupported_slugs(
                decomposition=decomposition,
                supported={"payment_authorization"},
            )
        self.assertEqual(metadata["status"], "no_rewrites")
        self.assertEqual(metadata["rewrites"], [])
        self.assertEqual(
            rewritten.sub_tasks[0].suggested_capability_id,
            "kyc_aml_check",
        )

    def test_match_to_unsupported_slug_is_not_rewritten(self) -> None:
        # If the index returns a match that isn't itself in the
        # supported set (e.g., a stale registry entry), we don't
        # rewrite — better to surface the original slug as a gap
        # than to silently swap to an equally-unrouted name.
        decomposition = _decomposition_with(suggested="payment_processing")
        index = _StubIndex({"payment_processing": "deprecated_payment_v0"})
        with patch(
            "planmyagents_api.discovery.capability_index.get_default_capability_index",
            return_value=index,
        ):
            rewritten, metadata = _canonicalize_unsupported_slugs(
                decomposition=decomposition,
                supported={"payment_authorization"},
            )
        self.assertEqual(metadata["status"], "no_rewrites")
        self.assertEqual(
            rewritten.sub_tasks[0].suggested_capability_id,
            "payment_processing",
        )

    def test_disabled_returns_decomposition_unchanged(self) -> None:
        os.environ["PLANMYAGENTS_SLUG_CANONICALIZATION"] = "false"
        decomposition = _decomposition_with(suggested="payment_processing")
        rewritten, metadata = _canonicalize_unsupported_slugs(
            decomposition=decomposition,
            supported={"payment_authorization"},
        )
        self.assertEqual(metadata["status"], "disabled")
        self.assertEqual(
            rewritten.sub_tasks[0].suggested_capability_id,
            "payment_processing",
        )

    def test_already_supported_slug_skipped(self) -> None:
        # The slug is already in the supported set, so canonicalization
        # has nothing to rewrite. The index must not even be called.
        decomposition = _decomposition_with(suggested="payment_authorization")
        index = _StubIndex({})
        with patch(
            "planmyagents_api.discovery.capability_index.get_default_capability_index",
            return_value=index,
        ):
            _, metadata = _canonicalize_unsupported_slugs(
                decomposition=decomposition,
                supported={"payment_authorization"},
            )
        self.assertEqual(metadata["status"], "no_rewrites")
        self.assertEqual(index.calls, [])  # short-circuited

    def test_index_failure_does_not_break_request(self) -> None:
        decomposition = _decomposition_with(suggested="payment_processing")

        class BoomIndex:
            def match_slug(self, slug: str) -> str | None:
                raise RuntimeError("ollama down")

        with patch(
            "planmyagents_api.discovery.capability_index.get_default_capability_index",
            return_value=BoomIndex(),
        ):
            rewritten, metadata = _canonicalize_unsupported_slugs(
                decomposition=decomposition,
                supported={"payment_authorization"},
            )
        # A per-slug failure produces no_rewrites + the decomposition
        # passes through unchanged. We log the failure but never raise.
        self.assertEqual(metadata["status"], "no_rewrites")
        self.assertEqual(
            rewritten.sub_tasks[0].suggested_capability_id,
            "payment_processing",
        )


if __name__ == "__main__":
    unittest.main()
