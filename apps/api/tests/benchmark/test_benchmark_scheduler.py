from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.scheduler import BenchmarkScheduler
from planmyagents_api.benchmark.store import JsonBenchmarkStore
from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.discovery.store import JsonDiscoveryStore
from planmyagents_api.registry.loader import gated_benchmark_capabilities

BENCHMARKS_DIR = ROOT / "packages" / "benchmarks"


def _candidate(
    *,
    candidate_id: str,
    capability_id: str,
    provider_type: str = "ai_agent",
    verification_status: str = "capability_verified",
    adapter_module: str = "",
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id=candidate_id,
        display_name=candidate_id,
        vendor=candidate_id,
        vendor_url=f"https://{candidate_id}.example",
        provider_type=provider_type,
        capabilities=[CandidateCapability(id=capability_id, confidence=0.9)],
        verification_status=verification_status,
        adapter_module=adapter_module,
        evidence_url=f"https://{candidate_id}.example/agent.json",
    )


class BenchmarkSchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.tempdir = Path(self._tempdir.name)
        self.discovery_path = self.tempdir / "discovery.json"
        self.benchmark_path = self.tempdir / "benchmarks.json"
        self.discovery_store = JsonDiscoveryStore(self.discovery_path)
        self.benchmark_store = JsonBenchmarkStore(self.benchmark_path)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _scheduler(self, **overrides) -> BenchmarkScheduler:
        defaults = {
            "benchmarks_dir": BENCHMARKS_DIR,
            "discovery_store": self.discovery_store,
            "benchmark_store": self.benchmark_store,
            "cases_per_capability": 5,
            "use_mock_fallback": True,
        }
        defaults.update(overrides)
        return BenchmarkScheduler(**defaults)

    def test_runs_benchmarks_for_verified_candidate_with_mock_fallback(self) -> None:
        candidate = _candidate(
            candidate_id="alpha-email-agent",
            capability_id="email_verification",
        )
        self.discovery_store.save([candidate])

        report = self._scheduler().run()

        self.assertGreater(report.benchmark_runs, 0)
        self.assertEqual(report.rankings_updated, 1)
        self.assertEqual(report.candidates_updated, 1)
        summary = report.summaries[0]
        self.assertEqual(summary.candidate_id, "alpha-email-agent")
        self.assertEqual(summary.capability, "email_verification")
        self.assertEqual(summary.status, "ran")
        self.assertEqual(summary.source, "synthetic")
        self.assertGreater(summary.success_rate, 0.0)

        # Persistence: runs and rankings end up in the benchmark store.
        payload = json.loads(self.benchmark_path.read_text())
        self.assertGreater(len(payload["runs"]), 0)
        self.assertEqual(len(payload["rankings"]), 1)
        self.assertEqual(payload["rankings"][0]["provider_id"], "alpha-email-agent")

        # Discovery store: benchmark_status is updated on the candidate.
        candidates = self.discovery_store.load()
        self.assertEqual(candidates[0].benchmark_status, summary.benchmark_status)

    def test_skips_raw_leads_when_provider_evidence_required(self) -> None:
        unverified = _candidate(
            candidate_id="beta-agent",
            capability_id="email_verification",
            verification_status="unverified",
        )
        self.discovery_store.save([unverified])

        report = self._scheduler().run(require_provider_evidence=True)
        self.assertEqual(report.benchmark_runs, 0)
        self.assertEqual(report.rankings_updated, 0)
        self.assertEqual(report.summaries, [])

    def test_includes_unverified_when_explicitly_selected(self) -> None:
        unverified = _candidate(
            candidate_id="gamma-agent",
            capability_id="email_verification",
            verification_status="unverified",
        )
        self.discovery_store.save([unverified])

        report = self._scheduler().run(
            candidate_ids={"gamma-agent"}, require_provider_evidence=False
        )
        self.assertEqual(len(report.summaries), 1)
        self.assertEqual(report.summaries[0].status, "ran")

    def test_skips_protocol_beta_adapter_module(self) -> None:
        candidate = _candidate(
            candidate_id="delta-agent",
            capability_id="email_verification",
            adapter_module="planmyagents_api.agents.protocol:GenericA2AAdapter",
        )
        self.discovery_store.save([candidate])

        report = self._scheduler().run()
        self.assertEqual(report.benchmark_runs, 0)
        self.assertEqual(len(report.summaries), 1)
        self.assertEqual(report.summaries[0].status, "skipped_no_adapter")
        self.assertTrue(
            any("protocol_beta" in note for note in report.summaries[0].notes)
        )

    def test_skips_capability_without_mock_or_real_adapter(self) -> None:
        candidate = _candidate(
            candidate_id="epsilon-agent",
            capability_id="travel_search",
        )
        self.discovery_store.save([candidate])

        report = self._scheduler().run()
        self.assertEqual(report.benchmark_runs, 0)
        self.assertEqual(len(report.summaries), 1)
        self.assertEqual(report.summaries[0].status, "skipped_no_adapter")

    def test_no_mock_fallback_skips_capability_even_when_mock_exists(self) -> None:
        candidate = _candidate(
            candidate_id="zeta-agent",
            capability_id="email_verification",
        )
        self.discovery_store.save([candidate])

        scheduler = self._scheduler(use_mock_fallback=False)
        report = scheduler.run()
        self.assertEqual(report.benchmark_runs, 0)
        self.assertEqual(report.summaries[0].status, "skipped_no_adapter")

    def test_adapter_exception_does_not_kill_scheduler_run(self) -> None:
        """sprint-pitch-align P2-2 regression — when an adapter raises (e.g.
        Razorpay's RazorpayConfigurationError on a cron host without keys),
        the scheduler must surface a clean error summary so cron alerting
        still fires instead of the whole process crashing."""

        class _RaisingAdapter:
            provider_id = "raising-provider"
            capabilities = ["email_verification"]

            async def estimate_cost(self, request):
                return 0.0

            async def execute(self, request):
                raise RuntimeError("simulated missing credentials")

            async def health_check(self):
                return False

        candidate = _candidate(
            candidate_id="raising-provider",
            capability_id="email_verification",
        )
        self.discovery_store.save([candidate])

        scheduler = self._scheduler(
            adapter_resolver=lambda c, cap: _RaisingAdapter(),
        )
        report = scheduler.run()

        self.assertEqual(report.benchmark_runs, 0)
        self.assertEqual(len(report.summaries), 1)
        summary = report.summaries[0]
        self.assertEqual(summary.status, "error")
        self.assertTrue(
            any("adapter_error:RuntimeError" in note for note in summary.notes),
            f"expected adapter_error in notes, got: {summary.notes}",
        )


class GatedBenchmarkCapabilitiesTest(unittest.TestCase):
    """sprint-pitch-align P2-2 regression — scheduler must iterate over every
    `requires_benchmark_gate=true` agent rather than be hardcoded to
    `email_verification` (the bug audit-flagged in sprint-3.md:175)."""

    def test_returns_gated_capabilities_only(self) -> None:
        registry = {
            "agents": [
                {
                    "id": "a-stripe",
                    "is_active": True,
                    "requires_benchmark_gate": True,
                    "capabilities": [{"id": "payment_authorization"}],
                },
                {
                    "id": "a-resend",
                    "is_active": True,
                    "requires_benchmark_gate": True,
                    "capabilities": [{"id": "email_send"}],
                },
                {
                    "id": "a-router",
                    "is_active": True,
                    "requires_benchmark_gate": False,
                    "capabilities": [{"id": "general_research"}],
                },
                {
                    "id": "a-inactive-gated",
                    "is_active": False,
                    "requires_benchmark_gate": True,
                    "capabilities": [{"id": "should_be_filtered"}],
                },
            ]
        }

        gated = gated_benchmark_capabilities(registry)

        self.assertEqual(gated, ["email_send", "payment_authorization"])
        self.assertNotIn("general_research", gated)
        self.assertNotIn("should_be_filtered", gated)

    def test_empty_when_no_agent_is_gated(self) -> None:
        registry = {
            "agents": [
                {
                    "id": "a-router",
                    "is_active": True,
                    "requires_benchmark_gate": False,
                    "capabilities": [{"id": "general_research"}],
                }
            ]
        }
        self.assertEqual(gated_benchmark_capabilities(registry), [])

    def test_live_registry_returns_known_gated_capabilities(self) -> None:
        """Locks in the live agents.json so a future re-classification cannot
        accidentally drop the Razorpay live cell off the cron schedule."""

        from planmyagents_api.registry.loader import load_registry

        registry = load_registry(ROOT / "packages" / "registry" / "agents.json")
        gated = set(gated_benchmark_capabilities(registry))
        self.assertIn("payment_authorization", gated)
        self.assertGreaterEqual(len(gated), 2)


if __name__ == "__main__":
    unittest.main()
