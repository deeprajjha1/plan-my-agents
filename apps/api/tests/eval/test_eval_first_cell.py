"""The first real cell: a discovered MCP server scored end-to-end, proving the
credibility classifier moves the capability off ``synthetic_only``.

This is the milestone the whole agent-eval-framework spec exists to reach.
Runs fully in-process against a local MCP stub — no external network.
"""

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
from planmyagents_api.benchmark.credibility import classify
from planmyagents_api.benchmark.scheduler import BenchmarkScheduler
from planmyagents_api.benchmark.store import JsonBenchmarkStore
from planmyagents_api.cost.cost_cap import CostCapPolicy
from planmyagents_api.discovery.models import (
    CandidateCapability,
    CandidateTool,
    DiscoveryCandidate,
)
from planmyagents_api.discovery.store import discovery_store_for_path
from planmyagents_api.eval.framework import build_default_framework
from planmyagents_api.eval.models import EvalRunMode

from tests.fixtures.mcp_stub_server import McpStubServer


def _write_cases(benchmarks_dir: Path, capability: str, n: int) -> None:
    cap_dir = benchmarks_dir / capability
    cap_dir.mkdir(parents=True, exist_ok=True)
    cases = [
        f"  - id: case-{i}\n"
        f"    capability: {capability}\n"
        "    difficulty: easy\n"
        "    inputs: {tool_name: scrape, arguments: {url: 'https://example.invalid'}}\n"
        "    expected: {text: {contains: ok}}\n"
        for i in range(n)
    ]
    (cap_dir / "cases.yaml").write_text("cases:\n" + "".join(cases))


class FirstRealCellTest(unittest.TestCase):
    def test_real_cell_moves_capability_off_synthetic_only(self) -> None:
        capability = "web_scraping"
        with tempfile.TemporaryDirectory() as tmp, McpStubServer(
            {"scrape": lambda args: {"status": "ok", "url": args.get("url")}}
        ) as base_url, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False
        ):
            tmp_path = Path(tmp)
            _write_cases(tmp_path, capability, 5)

            candidate = DiscoveryCandidate(
                id="stub-scraper-mcp",
                display_name="Stub Scraper",
                vendor="Stub",
                vendor_url=base_url,
                provider_type="mcp_server",
                capabilities=[CandidateCapability(id=capability, confidence=0.95)],
                verification_status="registered_in_directory",
                tools=[CandidateTool(name="scrape", input_schema={"properties": {"url": {}}})],
            )

            discovery_store = discovery_store_for_path(str(tmp_path / "discovery.json"))
            discovery_store.save([candidate])
            bench_store = JsonBenchmarkStore(tmp_path / "bench.json")

            framework = build_default_framework(
                benchmarks_dir=tmp_path,
                cost_cap=CostCapPolicy(enabled=True, per_goal_usd=100.0, daily_usd=100.0, ledger=None),
            )
            scheduler = BenchmarkScheduler(
                benchmarks_dir=tmp_path,
                discovery_store=discovery_store,
                benchmark_store=bench_store,
                eval_framework=framework,
            )
            report = scheduler.run_eval(run_mode=EvalRunMode.SANDBOX)
            self.assertGreater(report.benchmark_runs, 0)

            # BEFORE: with no real rankings, the cell is synthetic_only.
            empty_verdict = classify(
                capability=capability, candidates=[candidate], rankings=[]
            )
            self.assertEqual(empty_verdict.status, "synthetic_only")

            # AFTER: feed the persisted real eval rankings to the classifier.
            rankings = bench_store.load_rankings()
            verdict = classify(
                capability=capability, candidates=[candidate], rankings=rankings
            )
            # The single real provider with 5 samples clears synthetic_only and
            # lands in smoke_test (below the 3-provider / 30-sample bar).
            self.assertNotEqual(verdict.status, "synthetic_only")
            self.assertEqual(verdict.status, "smoke_test")
            self.assertEqual(verdict.real_provider_count, 1)


if __name__ == "__main__":
    unittest.main()
