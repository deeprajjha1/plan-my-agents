from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.models import (
    BenchmarkRun,
    ProviderResponse,
    ScoreResult,
)
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.eval.models import EvalRunMode, EvalTier
from planmyagents_api.eval.provenance import ProvenanceRecorder, resolve_agent_version


def _candidate(metadata: dict | None = None) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id="p1",
        display_name="P1",
        vendor="Vendor",
        vendor_url="https://example.com",
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
        metadata=metadata or {},
    )


def _run() -> BenchmarkRun:
    return BenchmarkRun(
        test_case_id="c1",
        provider_id="p1",
        capability="web_scraping",
        difficulty="easy",
        response=ProviderResponse(
            succeeded=True, output={"ok": True}, cost_usd=0.0, latency_ms=10
        ),
        score=ScoreResult(quality_score=1.0, succeeded=True, field_scores=[], reason="ok"),
    )


class ProvenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.recorder = ProvenanceRecorder()

    def test_all_required_fields_present(self) -> None:
        prov = self.recorder.build(
            candidate=_candidate({"version": "1.2.3"}),
            eval_tier=EvalTier.SCORED_BENCHMARK,
            run_mode=EvalRunMode.SANDBOX,
            safety_class="read_only",
            protocol="mcp",
            protocol_maturity="executable",
            descriptor_source="mcp_tools_list",
            scoring_method="exact_match",
            case_set_version="gt:web_scraping:abc",
            ground_truth_version="gt:web_scraping:abc",
            ground_truth_kind="exact_match",
        )
        payload = prov.to_json()
        for key in (
            "eval_tier",
            "run_mode",
            "safety_class",
            "provider_type",
            "agent_version",
            "case_set_version",
            "ground_truth_version",
            "protocol",
            "protocol_maturity",
            "descriptor_source",
            "scoring_method",
            "run_timestamp",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["agent_version"], "1.2.3")
        self.assertTrue(payload["run_timestamp"])

    def test_fixture_ids_recorded_for_side_effecting(self) -> None:
        prov = self.recorder.build(
            candidate=_candidate(),
            eval_tier=EvalTier.SCORED_BENCHMARK,
            run_mode=EvalRunMode.SANDBOX,
            safety_class="side_effecting",
            fixture_ids=["resend.dev"],
        )
        self.assertEqual(prov.fixture_ids, ["resend.dev"])

    def test_resolve_agent_version_defaults_unknown(self) -> None:
        self.assertEqual(resolve_agent_version(_candidate()), "unknown")
        self.assertEqual(
            resolve_agent_version(_candidate({"npm_version": "9.9.9"})), "9.9.9"
        )

    def test_attach_puts_provenance_under_eval_key(self) -> None:
        prov = self.recorder.build(
            candidate=_candidate(),
            eval_tier=EvalTier.SCORED_BENCHMARK,
            run_mode=EvalRunMode.SANDBOX,
            safety_class="read_only",
        )
        attached = self.recorder.attach(_run(), prov)
        self.assertIn("_eval", attached.response.raw_response)
        self.assertEqual(
            attached.response.raw_response["_eval"]["safety_class"], "read_only"
        )
        # original output preserved
        self.assertEqual(attached.response.output, {"ok": True})

    def test_provenance_carries_no_credential_field(self) -> None:
        prov = self.recorder.build(
            candidate=_candidate({"api_key": "sk-secret"}),
            eval_tier=EvalTier.SCORED_BENCHMARK,
            run_mode=EvalRunMode.SANDBOX,
            safety_class="read_only",
        )
        serialized = str(prov.to_json())
        self.assertNotIn("sk-secret", serialized)
        self.assertNotIn("api_key", prov.to_json())


if __name__ == "__main__":
    unittest.main()
