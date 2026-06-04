from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.protocol import ENABLE_PROTOCOL_EXECUTION_ENV
from planmyagents_api.cost.cost_cap import CostCapPolicy
from planmyagents_api.discovery.models import (
    CandidateCapability,
    CandidateTool,
    DiscoveryCandidate,
)
from planmyagents_api.eval.framework import build_default_framework
from planmyagents_api.eval.models import EvalRunMode, EvalTier

from tests.fixtures.mcp_stub_server import McpStubServer


def _write_cases(benchmarks_dir: Path, capability: str, n: int) -> None:
    cap_dir = benchmarks_dir / capability
    cap_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    for i in range(n):
        cases.append(
            f"  - id: case-{i}\n"
            f"    capability: {capability}\n"
            "    difficulty: easy\n"
            "    inputs: {tool_name: scrape, arguments: {url: 'https://example.com'}}\n"
            "    expected: {text: {contains: ok}}\n"
        )
    (cap_dir / "cases.yaml").write_text("cases:\n" + "".join(cases))


def _mcp_candidate(base_url: str) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id="stub-mcp",
        display_name="Stub MCP",
        vendor="Stub",
        vendor_url=base_url,
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id="web_scraping", confidence=0.95)],
        verification_status="registered_in_directory",
        tools=[CandidateTool(name="scrape", input_schema={"properties": {"url": {}}})],
    )


class EvalFrameworkTest(unittest.TestCase):
    def test_non_agentic_candidate_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            framework = build_default_framework(benchmarks_dir=Path(tmp))
            cand = DiscoveryCandidate(
                id="api-1",
                display_name="API",
                vendor="V",
                vendor_url="https://e.com",
                provider_type="api_provider",
                capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
            )
            result = framework.evaluate_cell(candidate=cand, capability="web_scraping")
            self.assertFalse(result.eval_able)
            self.assertEqual(result.error, "non_agentic_provider_type")
            self.assertIsNone(result.quality_score)

    def test_gate_off_yields_gated_verification_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: ""}, clear=False
        ):
            _write_cases(Path(tmp), "web_scraping", 3)
            framework = build_default_framework(benchmarks_dir=Path(tmp))
            cand = _mcp_candidate("https://stub.example.com")
            result = framework.evaluate_cell(
                candidate=cand, capability="web_scraping", run_mode=EvalRunMode.SANDBOX
            )
            self.assertIsNone(result.quality_score)
            self.assertEqual(result.source, "gated")
            self.assertFalse(result.is_real_run)

    def test_a2a_is_verification_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False
        ):
            _write_cases(Path(tmp), "web_scraping", 3)
            framework = build_default_framework(benchmarks_dir=Path(tmp))
            cand = DiscoveryCandidate(
                id="a2a-1",
                display_name="A2A",
                vendor="V",
                vendor_url="https://a2a.example.com",
                provider_type="a2a_agent",
                capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
                verification_status="registered_in_directory",
                skills=[CandidateTool(name="scrape")],
            )
            result = framework.evaluate_cell(
                candidate=cand, capability="web_scraping", run_mode=EvalRunMode.SANDBOX
            )
            self.assertIsNone(result.quality_score)
            self.assertEqual(result.source, "verification_only")
            self.assertEqual(result.provenance.protocol_maturity, "refusal_only")

    def test_no_ground_truth_is_non_eval_able(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False
        ):
            # No cases written → ground truth NONE for this capability.
            framework = build_default_framework(benchmarks_dir=Path(tmp))
            cand = _mcp_candidate("https://stub.example.com")
            result = framework.evaluate_cell(
                candidate=cand, capability="web_scraping", run_mode=EvalRunMode.SANDBOX
            )
            self.assertFalse(result.eval_able)
            self.assertIn("non_eval_able", result.reason)

    def test_happy_path_scores_real_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, McpStubServer(
            {"scrape": lambda args: {"status": "ok", "url": args.get("url")}}
        ) as base_url, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False
        ):
            _write_cases(Path(tmp), "web_scraping", 5)
            framework = build_default_framework(
                benchmarks_dir=Path(tmp),
                cost_cap=CostCapPolicy(enabled=True, per_goal_usd=100.0, daily_usd=100.0, ledger=None),
            )
            cand = _mcp_candidate(base_url)
            result = framework.evaluate_cell(
                candidate=cand, capability="web_scraping", run_mode=EvalRunMode.SANDBOX
            )
            self.assertTrue(result.eval_able)
            self.assertEqual(result.source, "exact_match")
            self.assertIsNotNone(result.quality_score)
            self.assertTrue(result.is_real_run)
            self.assertEqual(result.sample_size, 5)
            # 5 < 30 → functional_smoke tier label
            self.assertEqual(result.tier_reached, EvalTier.FUNCTIONAL_SMOKE)
            self.assertEqual(result.ranking.source, "exact_match")
            # provenance carries protocol + descriptor + scoring method
            self.assertEqual(result.provenance.protocol, "mcp")
            self.assertEqual(result.provenance.descriptor_source, "mcp_tools_list")
            self.assertEqual(result.provenance.scoring_method, "exact_match")

    def test_failed_verification_stops_ladder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False
        ):
            _write_cases(Path(tmp), "web_scraping", 3)
            framework = build_default_framework(benchmarks_dir=Path(tmp))
            cand = _mcp_candidate("https://stub.example.com")
            # Force unverified with no evidence so static_verification fails.
            cand = DiscoveryCandidate(
                **{**cand.__dict__, "verification_status": "unverified", "vendor_url": ""}
            )
            result = framework.evaluate_cell(
                candidate=cand, capability="web_scraping", run_mode=EvalRunMode.SANDBOX
            )
            self.assertEqual(result.tier_reached, EvalTier.STATIC_VERIFICATION)
            self.assertIsNone(result.quality_score)


if __name__ == "__main__":
    unittest.main()
