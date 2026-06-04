from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.eval.ground_truth import (
    GroundTruthKind,
    GroundTruthManager,
    Rubric,
)

REPO_BENCHMARKS = ROOT / "packages" / "benchmarks"


class GroundTruthTest(unittest.TestCase):
    def test_existing_capability_resolves_exact_match(self) -> None:
        mgr = GroundTruthManager(benchmarks_dir=REPO_BENCHMARKS)
        gt = mgr.resolve("web_scraping")
        self.assertEqual(gt.kind, GroundTruthKind.EXACT_MATCH)
        self.assertTrue(gt.cases)
        self.assertTrue(gt.version.startswith("gt:web_scraping:"))
        self.assertTrue(gt.is_eval_able)

    def test_unknown_capability_is_none_with_reason(self) -> None:
        mgr = GroundTruthManager(benchmarks_dir=REPO_BENCHMARKS)
        gt = mgr.resolve("totally_unknown_capability_xyz")
        self.assertEqual(gt.kind, GroundTruthKind.NONE)
        self.assertFalse(gt.is_eval_able)
        self.assertIn("totally_unknown_capability_xyz", gt.reason_if_none)

    def test_registered_rubric_without_reference_cases_is_non_eval_able(self) -> None:
        # A rubric describes HOW to score; without reference cases there is
        # nothing to invoke the agent with, so the capability is honestly
        # non-eval-able (rather than emitting an empty, unrunnable run set).
        mgr = GroundTruthManager(
            benchmarks_dir=REPO_BENCHMARKS,
            rubrics={
                "summarization": Rubric(
                    capability="summarization",
                    criteria=["faithful", "concise"],
                )
            },
        )
        gt = mgr.resolve("summarization")
        self.assertEqual(gt.kind, GroundTruthKind.NONE)
        self.assertIsNotNone(gt.rubric)
        self.assertIn("no reference cases", gt.reason_if_none)

    def test_rubric_shaped_yaml_resolves_as_rubric(self) -> None:
        # The curated return_policy_analysis YAML is rubric-shaped (every case
        # declares a rubric_version), so it resolves as RUBRIC and carries the
        # reference cases that make it runnable.
        mgr = GroundTruthManager(benchmarks_dir=REPO_BENCHMARKS)
        gt = mgr.resolve("return_policy_analysis")
        self.assertEqual(gt.kind, GroundTruthKind.RUBRIC)
        self.assertTrue(gt.cases)
        self.assertIsNotNone(gt.rubric)
        self.assertTrue(gt.rubric.criteria)

    def test_version_changes_when_cases_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cap_dir = Path(tmp) / "demo_cap"
            cap_dir.mkdir(parents=True)
            case_file = cap_dir / "cases.yaml"
            case_file.write_text(
                "cases:\n"
                "  - id: d1\n"
                "    capability: demo_cap\n"
                "    difficulty: easy\n"
                "    inputs: {q: hello}\n"
                "    expected: {status: {accept: [ok]}}\n"
            )
            mgr = GroundTruthManager(benchmarks_dir=Path(tmp))
            v1 = mgr.resolve("demo_cap").version

            case_file.write_text(
                "cases:\n"
                "  - id: d1\n"
                "    capability: demo_cap\n"
                "    difficulty: easy\n"
                "    inputs: {q: changed}\n"
                "    expected: {status: {accept: [ok]}}\n"
            )
            v2 = mgr.resolve("demo_cap").version
            self.assertNotEqual(v1, v2)


if __name__ == "__main__":
    unittest.main()
