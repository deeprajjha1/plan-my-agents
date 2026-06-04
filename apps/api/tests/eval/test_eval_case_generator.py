from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.eval.case_generator import (
    CaseGenerator,
    filter_curated_for_scored,
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "max_results": {"type": "integer", "default": 5},
        "format": {"type": "string", "enum": ["json", "text"]},
    },
}


class CaseGeneratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gen = CaseGenerator(cases_per_capability=4)

    def test_generates_testcase_shaped_cases(self) -> None:
        result = self.gen.generate(capability="web_scraping", tool_schema=_SCHEMA)
        self.assertEqual(len(result.cases), 4)
        case = result.cases[0]
        self.assertEqual(case.capability, "web_scraping")
        self.assertIn("url", case.inputs)
        self.assertEqual(case.inputs["max_results"], 5)
        self.assertEqual(case.inputs["format"], "json")  # first enum value
        self.assertIn(case.difficulty, {"easy", "medium", "hard"})

    def test_generated_set_is_uncurated(self) -> None:
        result = self.gen.generate(capability="web_scraping", tool_schema=_SCHEMA)
        self.assertFalse(result.curated)

    def test_version_is_content_hash(self) -> None:
        r1 = self.gen.generate(capability="web_scraping", tool_schema=_SCHEMA)
        r2 = self.gen.generate(capability="web_scraping", tool_schema=_SCHEMA)
        self.assertEqual(r1.version, r2.version)
        r3 = self.gen.generate(capability="other_cap", tool_schema=_SCHEMA)
        self.assertNotEqual(r1.version, r3.version)

    def test_uncurated_excluded_from_scored(self) -> None:
        result = self.gen.generate(capability="web_scraping", tool_schema=_SCHEMA)
        self.assertEqual(filter_curated_for_scored(result), [])

    def test_empty_schema_still_produces_cases(self) -> None:
        result = self.gen.generate(capability="web_scraping", tool_schema=None)
        self.assertTrue(result.cases)
        self.assertEqual(result.cases[0].inputs, {})


if __name__ == "__main__":
    unittest.main()
