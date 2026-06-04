from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.loader import BenchmarkLoadError, load_test_cases


class BenchmarkLoaderTest(unittest.TestCase):
    def test_loads_email_verification_cases_from_cases_list(self) -> None:
        cases = load_test_cases(ROOT / "packages" / "benchmarks", capability="email_verification")

        self.assertEqual(len(cases), 20)
        self.assertEqual(cases[0].id, "email-0001-valid-saas-work-email")
        self.assertEqual(cases[0].capability, "email_verification")
        self.assertIn("email", cases[0].inputs)
        self.assertIn("result", cases[0].expected)

    def test_filters_by_capability(self) -> None:
        cases = load_test_cases(ROOT / "packages" / "benchmarks", capability="missing_capability")

        self.assertEqual(cases, [])

    def test_raises_for_missing_directory(self) -> None:
        with self.assertRaises(BenchmarkLoadError):
            load_test_cases(ROOT / "does-not-exist")


if __name__ == "__main__":
    unittest.main()
