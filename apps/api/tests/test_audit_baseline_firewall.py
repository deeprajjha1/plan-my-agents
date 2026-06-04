"""Unit tests for the firewall-audit script (sprint-6 / B).

The deep firewall invariant lives in
``apps/api/tests/test_benchmark_baseline_firewall.py``. This test
covers the cheap belt-and-braces grep:

* On the live repo, the audit must return ``ok=True`` (no baseline
  imports in routing modules). If anyone re-introduces a baseline
  import in agents/planner/web/workflows, this test fails.
* Synthetic fixture trees verify the audit correctly flags
  ``import``, ``from … import …``, and dotted attribute references,
  and correctly IGNORES docstring / comment text mentions.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_baseline_firewall import audit  # noqa: E402


class LiveRepoAuditTests(unittest.TestCase):
    def test_repo_is_clean(self) -> None:
        """Production assertion. If anyone re-introduces a baseline
        import in any routing module, this fails immediately. The
        sister CI workflow (``firewall-audit.yml``) also runs this
        check on every push to ``main``."""

        api_src = ROOT / "apps" / "api" / "planmyagents_api"
        report = audit(api_src)
        self.assertTrue(
            report["ok"],
            msg=(
                "Baseline import detected in routing modules — "
                f"violations={report['violations']}"
            ),
        )


class FixtureTreeAuditTests(unittest.TestCase):
    """Hand-built fixture trees so the audit's positive + negative
    detection logic is locked in independently of the live repo."""

    def _build_api_src(self, files: dict[str, str]) -> Path:
        # Create a temp directory shaped like apps/api/planmyagents_api
        # so the audit's ROUTING_DIRS resolution works unmodified.
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(self._cleanup, tmpdir)
        api_src = Path(tmpdir) / "planmyagents_api"
        api_src.mkdir()
        for rel_path, body in files.items():
            file_path = api_src / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(textwrap.dedent(body))
        return api_src

    def _cleanup(self, tmpdir: str) -> None:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_import_from_baseline_is_flagged(self) -> None:
        api_src = self._build_api_src({
            "agents/__init__.py": "",
            "agents/router.py": """
                from planmyagents_api.benchmark.baselines.firecrawl import FirecrawlScraper

                def route():
                    return FirecrawlScraper()
            """,
        })
        report = audit(api_src)
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["violations"]), 1)
        self.assertIn(
            "benchmark.baselines.firecrawl",
            report["violations"][0]["text"],
        )

    def test_plain_import_baseline_is_flagged(self) -> None:
        api_src = self._build_api_src({
            "agents/__init__.py": "",
            "agents/router.py": """
                import planmyagents_api.benchmark.baselines.razorpay as r

                def route():
                    return r.RazorpayPaymentAuthorization()
            """,
        })
        report = audit(api_src)
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                "benchmark.baselines.razorpay" in v["text"]
                for v in report["violations"]
            ),
            msg=report["violations"],
        )

    def test_attribute_chain_to_baseline_is_flagged(self) -> None:
        # Even if the import lives elsewhere, an attribute chain like
        # planmyagents_api.benchmark.baselines.razorpay.X inside a
        # routing module should fail the audit.
        api_src = self._build_api_src({
            "agents/__init__.py": "",
            "agents/router.py": """
                import planmyagents_api

                def route():
                    return planmyagents_api.benchmark.baselines.razorpay.X()
            """,
        })
        report = audit(api_src)
        self.assertFalse(report["ok"])

    def test_docstring_mention_is_ignored(self) -> None:
        """The whole reason we use AST instead of a regex: comments
        and docstrings that mention the firewall by name (e.g.
        documentation in router.py, baselines/__init__.py) MUST NOT
        trip the audit. The audit cares about CODE references, not
        explanatory text."""

        api_src = self._build_api_src({
            "agents/__init__.py": "",
            "agents/router.py": '''
                """Router that intentionally does NOT import from
                planmyagents_api.benchmark.baselines — see firewall in
                planmyagents_api/benchmark/baselines/__init__.py."""

                # The planmyagents_api.benchmark.baselines.firecrawl
                # module must never be referenced here.
                def route():
                    return None
            ''',
        })
        report = audit(api_src)
        self.assertTrue(report["ok"], msg=report["violations"])

    def test_file_outside_routing_dirs_is_skipped(self) -> None:
        # Files under benchmark/, scripts/, discovery/, etc. are
        # free to reference baselines (that's where they belong).
        api_src = self._build_api_src({
            "benchmark/__init__.py": "",
            "benchmark/scheduler.py": """
                from planmyagents_api.benchmark.baselines.razorpay import X
            """,
        })
        report = audit(api_src)
        self.assertTrue(report["ok"], msg=report["violations"])


if __name__ == "__main__":
    unittest.main()
