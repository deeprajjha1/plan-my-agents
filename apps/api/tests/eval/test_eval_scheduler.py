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
        "    inputs: {tool_name: scrape, arguments: {url: 'https://example.com'}}\n"
        "    expected: {text: {contains: ok}}\n"
        for i in range(n)
    ]
    (cap_dir / "cases.yaml").write_text("cases:\n" + "".join(cases))


def _candidate(base_url: str, provider_type: str = "mcp_server") -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=f"stub-{provider_type}",
        display_name="Stub",
        vendor="Stub",
        vendor_url=base_url,
        provider_type=provider_type,
        capabilities=[CandidateCapability(id="web_scraping", confidence=0.95)],
        verification_status="registered_in_directory",
        tools=[CandidateTool(name="scrape", input_schema={"properties": {"url": {}}})],
    )


class EvalSchedulerTest(unittest.TestCase):
    def test_eval_mode_scores_discovered_mcp_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, McpStubServer(
            {"scrape": lambda args: {"status": "ok", "url": args.get("url")}}
        ) as base_url, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False
        ):
            tmp_path = Path(tmp)
            _write_cases(tmp_path, "web_scraping", 3)

            store_path = tmp_path / "discovery.json"
            discovery_store = discovery_store_for_path(str(store_path))
            discovery_store.save([_candidate(base_url)])

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
            self.assertEqual(report.rankings_updated, 1)
            summary = report.summaries[0]
            self.assertEqual(summary.status, "ran")
            self.assertEqual(summary.source, "exact_match")

            # The persisted ranking is a real run for the credibility classifier.
            rankings = bench_store.load_rankings()
            self.assertEqual(len(rankings), 1)
            self.assertEqual(rankings[0]["source"], "exact_match")

    def test_failure_isolation_one_bad_candidate(self) -> None:
        # One candidate points at a dead URL; the batch must still complete and
        # report both candidates.
        with tempfile.TemporaryDirectory() as tmp, McpStubServer(
            {"scrape": lambda args: {"status": "ok"}}
        ) as base_url, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False
        ):
            tmp_path = Path(tmp)
            _write_cases(tmp_path, "web_scraping", 2)

            good = _candidate(base_url)
            bad = DiscoveryCandidate(
                **{**_candidate("http://127.0.0.1:1/dead").__dict__, "id": "bad-mcp"}
            )
            store_path = tmp_path / "discovery.json"
            discovery_store = discovery_store_for_path(str(store_path))
            discovery_store.save([good, bad])

            framework = build_default_framework(
                benchmarks_dir=tmp_path,
                cost_cap=CostCapPolicy(enabled=True, per_goal_usd=100.0, daily_usd=100.0, ledger=None),
            )
            scheduler = BenchmarkScheduler(
                benchmarks_dir=tmp_path,
                discovery_store=discovery_store,
                benchmark_store=JsonBenchmarkStore(tmp_path / "bench.json"),
                eval_framework=framework,
            )
            report = scheduler.run_eval(run_mode=EvalRunMode.SANDBOX)
            # Both candidates appear in the summary; the batch did not crash.
            summarized_ids = {s.candidate_id for s in report.summaries}
            self.assertEqual(summarized_ids, {"stub-mcp-server", "bad-mcp"})

    def test_run_eval_requires_framework(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "discovery.json"
            discovery_store = discovery_store_for_path(str(store_path))
            scheduler = BenchmarkScheduler(
                benchmarks_dir=tmp_path,
                discovery_store=discovery_store,
                benchmark_store=JsonBenchmarkStore(tmp_path / "bench.json"),
            )
            with self.assertRaises(ValueError):
                scheduler.run_eval(run_mode=EvalRunMode.SANDBOX)

    def test_dry_run_makes_no_live_call_and_scores_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, McpStubServer(
            {"scrape": lambda args: {"status": "ok"}}
        ) as base_url, mock.patch.dict(
            os.environ, {ENABLE_PROTOCOL_EXECUTION_ENV: "true"}, clear=False
        ):
            tmp_path = Path(tmp)
            _write_cases(tmp_path, "web_scraping", 3)
            store_path = tmp_path / "discovery.json"
            discovery_store = discovery_store_for_path(str(store_path))
            discovery_store.save([_candidate(base_url)])
            framework = build_default_framework(benchmarks_dir=tmp_path)
            scheduler = BenchmarkScheduler(
                benchmarks_dir=tmp_path,
                discovery_store=discovery_store,
                benchmark_store=JsonBenchmarkStore(tmp_path / "bench.json"),
                eval_framework=framework,
            )
            report = scheduler.run_eval(run_mode=EvalRunMode.DRY_RUN)
            # dry_run is not a permitted run mode for a live invocation; the
            # safety gate refuses sandbox/live, so no real run is produced.
            self.assertEqual(report.benchmark_runs, 0)


if __name__ == "__main__":
    unittest.main()
