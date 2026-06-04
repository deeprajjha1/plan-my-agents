from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.models import ProviderResponse, TestCase
from planmyagents_api.benchmark.scoring import score_response


class ScoringTest(unittest.TestCase):
    def test_scores_accept_and_min_score_fields(self) -> None:
        case = TestCase(
            id="case-1",
            capability="email_verification",
            difficulty="easy",
            inputs={"email": "jane@acme.com"},
            expected={
                "result": {"accept": ["deliverable"], "weight": 0.7},
                "score": {"min_score": 80, "weight": 0.3},
            },
        )
        response = ProviderResponse(
            succeeded=True,
            output={"result": "deliverable", "score": 95},
            cost_usd=0.001,
            latency_ms=10,
        )

        result = score_response(case, response)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.quality_score, 1.0)
        self.assertEqual(len(result.field_scores), 2)

    def test_scores_partial_credit_by_weight(self) -> None:
        case = TestCase(
            id="case-2",
            capability="email_verification",
            difficulty="medium",
            inputs={"email": "jane@acme.com"},
            expected={
                "result": {"accept": ["deliverable"], "weight": 0.75},
                "score": {"min_score": 90, "weight": 0.25},
            },
        )
        response = ProviderResponse(
            succeeded=True,
            output={"result": "deliverable", "score": 50},
            cost_usd=0.001,
            latency_ms=10,
        )

        result = score_response(case, response)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.quality_score, 0.75)

    def test_scores_regex_format_checks(self) -> None:
        case = TestCase(
            id="case-3",
            capability="contact_enrichment",
            difficulty="easy",
            inputs={"first_name": "Jane"},
            expected={
                "email": {"format_check": r"^.+@acme\.com$", "weight": 1.0},
            },
        )
        response = ProviderResponse(
            succeeded=True,
            output={"email": "jane@acme.com"},
            cost_usd=0.01,
            latency_ms=10,
        )

        result = score_response(case, response)

        self.assertEqual(result.quality_score, 1.0)

    def test_scores_unknown_expected_as_success_when_provider_refuses(self) -> None:
        case = TestCase(
            id="case-4",
            capability="contact_enrichment",
            difficulty="adversarial",
            inputs={"first_name": "Fake"},
            expected={
                "must_indicate_unknown": True,
                "forbidden_outputs": ["fake.person@nonexistent.com"],
            },
        )
        response = ProviderResponse(
            succeeded=True,
            output={"status": "not_found", "found": False},
            cost_usd=0.01,
            latency_ms=10,
        )

        result = score_response(case, response)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.quality_score, 1.0)

    def test_scores_unknown_expected_as_failure_when_provider_hallucinates(self) -> None:
        case = TestCase(
            id="case-5",
            capability="contact_enrichment",
            difficulty="adversarial",
            inputs={"first_name": "Fake"},
            expected={
                "must_indicate_unknown": True,
                "forbidden_outputs": ["fake.person@nonexistent.com"],
            },
        )
        response = ProviderResponse(
            succeeded=True,
            output={"email": "fake.person@nonexistent.com", "status": "ok"},
            cost_usd=0.01,
            latency_ms=10,
        )

        result = score_response(case, response)

        self.assertFalse(result.succeeded)
        self.assertEqual(result.quality_score, 0.0)
        self.assertEqual(result.reason, "hallucinated_when_unknown_expected")


if __name__ == "__main__":
    unittest.main()
