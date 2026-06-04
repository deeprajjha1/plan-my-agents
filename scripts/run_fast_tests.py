"""Fast subset of the Python test suite.

Why
---
``make test`` discovers 1,200+ tests across ``apps/api/tests``. The
vast majority are pure unit tests that run in milliseconds, but
``FastAPIAppTest`` (29 tests) deliberately exercises the live LLM
tier — each ``/goal`` test triggers a real Ollama call against
``qwen3.6:27b``, taking 25-30 seconds. With 29 such tests plus
external HTTP for scout discovery, the suite easily exceeds 10
minutes of wall clock even when nothing is broken.

For the inner-loop "did I break anything" check we want sub-minute
feedback. This runner discovers the same tests as ``make test`` but
skips ``FastAPIAppTest``, keeping the other 1,189 cases — every
unit test of every store, planner module, scoring helper,
credibility classifier, recorder, reconciler, and every router
test EXCEPT the live-LLM integration class.

Usage:
    make test-fast            # via Makefile target
    PYTHONPATH=apps/api python scripts/run_fast_tests.py

When to use the slow suite anyway
---------------------------------
* Before tagging a release.
* When you touch ``apps/api/planmyagents_api/web/app.py`` or any
  ``/goal`` request-pipeline code (planner, decomposer, discovery
  scout dispatch, judge).
* Before a design-partner demo / external review.

In every other case, ``make test-fast`` is the right call.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

# Class names that live-call Ollama or external HTTP and are
# excluded from the fast subset. Add new slow classes here rather
# than scattering `skipIf` decorators across the codebase.
SLOW_TEST_CLASS_NAMES: set[str] = {
    "FastAPIAppTest",
}


def _flatten(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    cases: list[unittest.TestCase] = []
    for entry in suite:
        if isinstance(entry, unittest.TestSuite):
            cases.extend(_flatten(entry))
        else:
            cases.append(entry)
    return cases


def main() -> int:
    loader = unittest.TestLoader()
    discovered = loader.discover(
        start_dir=str(ROOT / "apps" / "api" / "tests"),
        pattern="test_*.py",
        top_level_dir=str(ROOT),
    )
    all_cases = _flatten(discovered)
    fast_cases = [
        case
        for case in all_cases
        if type(case).__name__ not in SLOW_TEST_CLASS_NAMES
    ]
    skipped = len(all_cases) - len(fast_cases)

    print(
        f"[run-fast-tests] discovered={len(all_cases)} "
        f"fast={len(fast_cases)} skipped_slow={skipped} "
        f"(slow classes: {', '.join(sorted(SLOW_TEST_CLASS_NAMES))})"
    )

    fast_suite = unittest.TestSuite(fast_cases)
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(fast_suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
