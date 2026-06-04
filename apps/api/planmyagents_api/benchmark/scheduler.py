"""Benchmark scheduler.

Drives benchmark runs against discovery candidates and feeds the results back
into both the benchmark store (runs + rankings) and the discovery store
(`benchmark_status`). The scheduler is the bridge between
"a candidate was discovered" and "we have honest performance numbers we can
publish in a category page".

It is intentionally conservative:

- Only candidates that have a usable adapter are scheduled.
- Generic protocol adapters in `protocol_beta` are skipped — they return
  metadata-only responses and would pollute rankings.
- A mock-adapter fallback is allowed only for capabilities that have a
  registered mock and only when explicitly opted in. Mock-backed rankings are
  tagged `source="synthetic"` so the UI can label them honestly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

from planmyagents_api.agents.base import ProviderAdapter
from planmyagents_api.agents.mock import MockContactEnricher, MockEmailVerifier
from planmyagents_api.benchmark.models import BenchmarkRun
from planmyagents_api.benchmark.rankings import AgentRanking, compute_rankings
from planmyagents_api.benchmark.runner import BenchmarkRunner
from planmyagents_api.benchmark.store import JsonBenchmarkStore, PostgresBenchmarkStore
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.store import (
    JsonDiscoveryStore,
    PostgresDiscoveryStore,
    SqliteDiscoveryStore,
)

GENERIC_PROTOCOL_PREFIX = "planmyagents_api.agents.protocol:"

DiscoveryStore = JsonDiscoveryStore | SqliteDiscoveryStore | PostgresDiscoveryStore
BenchmarkStore = JsonBenchmarkStore | PostgresBenchmarkStore
AdapterFactory = Callable[[DiscoveryCandidate, str], ProviderAdapter | None]

# Mock adapters keyed by capability. Used only when use_mock_fallback=True.
MOCK_ADAPTERS_BY_CAPABILITY: dict[str, Callable[[str], ProviderAdapter]] = {
    "email_verification": lambda provider_id: MockEmailVerifier(provider_id=provider_id),
    "contact_enrichment": lambda provider_id: MockContactEnricher(provider_id=provider_id),
}


@dataclass(frozen=True)
class CandidateBenchmarkSummary:
    """Per-candidate scheduler outcome."""

    candidate_id: str
    capability: str
    status: str  # ran | skipped_no_adapter | skipped_protocol_beta | skipped_no_cases | error
    sample_size: int
    success_rate: float
    avg_quality_score: float
    composite_score: float
    benchmark_status: str
    adapter_module: str
    source: str
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "capability": self.capability,
            "status": self.status,
            "sample_size": self.sample_size,
            "success_rate": round(self.success_rate, 4),
            "avg_quality_score": round(self.avg_quality_score, 4),
            "composite_score": round(self.composite_score, 4),
            "benchmark_status": self.benchmark_status,
            "adapter_module": self.adapter_module,
            "source": self.source,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SchedulerReport:
    benchmark_runs: int
    rankings_updated: int
    candidates_updated: int
    summaries: list[CandidateBenchmarkSummary]

    def to_json(self) -> dict[str, Any]:
        return {
            "benchmark_runs": self.benchmark_runs,
            "rankings_updated": self.rankings_updated,
            "candidates_updated": self.candidates_updated,
            "summaries": [summary.to_json() for summary in self.summaries],
        }


@dataclass
class BenchmarkScheduler:
    """Run benchmarks for selected discovery candidates."""

    benchmarks_dir: Path
    discovery_store: DiscoveryStore
    benchmark_store: BenchmarkStore
    cases_per_capability: int = 20
    use_mock_fallback: bool = True
    adapter_resolver: AdapterFactory | None = None

    def run(
        self,
        *,
        candidate_ids: set[str] | None = None,
        require_provider_evidence: bool = True,
        capabilities: set[str] | None = None,
    ) -> SchedulerReport:
        candidates = self.discovery_store.load()
        selected = self._select_candidates(
            candidates,
            candidate_ids=candidate_ids,
            require_provider_evidence=require_provider_evidence,
        )

        all_runs: list[BenchmarkRun] = []
        all_rankings: list[AgentRanking] = []
        summaries: list[CandidateBenchmarkSummary] = []
        runner = BenchmarkRunner(self.benchmarks_dir)
        new_status_by_id: dict[str, dict[str, str]] = {}

        for candidate in selected:
            for capability in self._capabilities_for(candidate, capabilities):
                summary, runs, ranking = self._benchmark_one(
                    runner=runner, candidate=candidate, capability=capability
                )
                summaries.append(summary)
                if runs:
                    all_runs.extend(runs)
                if ranking is not None:
                    all_rankings.append(ranking)
                    new_status_by_id.setdefault(candidate.id, {})[
                        capability
                    ] = ranking.benchmark_status

        if all_runs:
            self.benchmark_store.save(all_runs)
        if all_rankings:
            self.benchmark_store.save_rankings(all_rankings)

        candidates_updated = self._apply_status_updates(candidates, new_status_by_id)
        return SchedulerReport(
            benchmark_runs=len(all_runs),
            rankings_updated=len(all_rankings),
            candidates_updated=candidates_updated,
            summaries=summaries,
        )

    def _select_candidates(
        self,
        candidates: list[DiscoveryCandidate],
        *,
        candidate_ids: set[str] | None,
        require_provider_evidence: bool,
    ) -> list[DiscoveryCandidate]:
        if candidate_ids:
            return [candidate for candidate in candidates if candidate.id in candidate_ids]
        if require_provider_evidence:
            return [
                candidate
                for candidate in candidates
                if candidate.verification_status
                in {"capability_verified", "known_provider"}
            ]
        return list(candidates)

    def _capabilities_for(
        self, candidate: DiscoveryCandidate, allowed: set[str] | None
    ) -> list[str]:
        capability_ids = [capability.id for capability in candidate.capabilities]
        if allowed:
            capability_ids = [item for item in capability_ids if item in allowed]
        return sorted(set(capability_ids))

    def _benchmark_one(
        self, *, runner: BenchmarkRunner, candidate: DiscoveryCandidate, capability: str
    ) -> tuple[CandidateBenchmarkSummary, list[BenchmarkRun], AgentRanking | None]:
        adapter, source, notes = self._resolve_adapter(candidate, capability)
        if adapter is None:
            return (
                CandidateBenchmarkSummary(
                    candidate_id=candidate.id,
                    capability=capability,
                    status="skipped_no_adapter",
                    sample_size=0,
                    success_rate=0.0,
                    avg_quality_score=0.0,
                    composite_score=0.0,
                    benchmark_status=candidate.benchmark_status,
                    adapter_module=candidate.adapter_module,
                    source=source,
                    notes=notes,
                ),
                [],
                None,
            )
        try:
            runs = asyncio.run(runner.run(adapter, capability, limit=self.cases_per_capability))
        except FileNotFoundError as exc:
            return (
                CandidateBenchmarkSummary(
                    candidate_id=candidate.id,
                    capability=capability,
                    status="error",
                    sample_size=0,
                    success_rate=0.0,
                    avg_quality_score=0.0,
                    composite_score=0.0,
                    benchmark_status=candidate.benchmark_status,
                    adapter_module=candidate.adapter_module,
                    source=source,
                    notes=[*notes, f"benchmarks_dir_missing:{exc}"],
                ),
                [],
                None,
            )
        except Exception as exc:
            # Defense for cron: adapters that refuse without credentials
            # (Razorpay/Stripe/Resend/etc. all raise ConfigurationError when
            # their env vars are unset) must surface as a single error
            # summary, not crash the whole scheduler. The error is recorded
            # in the report so observability + cron alerting still fire.
            return (
                CandidateBenchmarkSummary(
                    candidate_id=candidate.id,
                    capability=capability,
                    status="error",
                    sample_size=0,
                    success_rate=0.0,
                    avg_quality_score=0.0,
                    composite_score=0.0,
                    benchmark_status=candidate.benchmark_status,
                    adapter_module=candidate.adapter_module,
                    source=source,
                    notes=[*notes, f"adapter_error:{type(exc).__name__}:{exc}"],
                ),
                [],
                None,
            )
        if not runs:
            return (
                CandidateBenchmarkSummary(
                    candidate_id=candidate.id,
                    capability=capability,
                    status="skipped_no_cases",
                    sample_size=0,
                    success_rate=0.0,
                    avg_quality_score=0.0,
                    composite_score=0.0,
                    benchmark_status=candidate.benchmark_status,
                    adapter_module=candidate.adapter_module,
                    source=source,
                    notes=[*notes, "no benchmark cases registered for this capability"],
                ),
                [],
                None,
            )
        # Re-tag every run with the candidate id so rankings group correctly.
        retagged = [
            BenchmarkRun(
                test_case_id=run.test_case_id,
                provider_id=candidate.id,
                capability=run.capability,
                difficulty=run.difficulty,
                response=run.response,
                score=run.score,
            )
            for run in runs
        ]
        rankings = compute_rankings(retagged, source=source)
        ranking = rankings[0] if rankings else None
        if ranking is None:
            return (
                CandidateBenchmarkSummary(
                    candidate_id=candidate.id,
                    capability=capability,
                    status="error",
                    sample_size=0,
                    success_rate=0.0,
                    avg_quality_score=0.0,
                    composite_score=0.0,
                    benchmark_status=candidate.benchmark_status,
                    adapter_module=candidate.adapter_module,
                    source=source,
                    notes=[*notes, "ranking computation produced no result"],
                ),
                retagged,
                None,
            )
        return (
            CandidateBenchmarkSummary(
                candidate_id=candidate.id,
                capability=capability,
                status="ran",
                sample_size=ranking.sample_size,
                success_rate=ranking.success_rate,
                avg_quality_score=ranking.avg_quality_score,
                composite_score=ranking.composite_score,
                benchmark_status=ranking.benchmark_status,
                adapter_module=candidate.adapter_module,
                source=source,
                notes=notes,
            ),
            retagged,
            ranking,
        )

    def _resolve_adapter(
        self, candidate: DiscoveryCandidate, capability: str
    ) -> tuple[ProviderAdapter | None, str, list[str]]:
        if self.adapter_resolver is not None:
            adapter = self.adapter_resolver(candidate, capability)
            if adapter is not None:
                return adapter, "real_adapter", ["resolved via custom adapter resolver"]

        adapter_module = candidate.adapter_module.strip()
        if adapter_module and adapter_module.startswith(GENERIC_PROTOCOL_PREFIX):
            return (
                None,
                "synthetic",
                [
                    "skipped: generic protocol_beta adapter cannot produce useful benchmark scores"
                ],
            )
        if adapter_module:
            adapter = self._instantiate_module_adapter(adapter_module, candidate)
            if adapter is not None and capability in (adapter.capabilities or [capability]):
                return adapter, "real_adapter", [f"using adapter_module={adapter_module}"]
            if adapter is not None:
                return (
                    None,
                    "real_adapter",
                    [
                        f"adapter_module={adapter_module} does not declare capability {capability}"
                    ],
                )
        if self.use_mock_fallback and capability in MOCK_ADAPTERS_BY_CAPABILITY:
            mock_provider_id = f"mock-{candidate.id}-{capability}"
            adapter = MOCK_ADAPTERS_BY_CAPABILITY[capability](mock_provider_id)
            return (
                adapter,
                "synthetic",
                [
                    "using mock adapter fallback (synthetic benchmark; "
                    "label as such in any UI)"
                ],
            )
        return (
            None,
            "synthetic",
            ["no real adapter and no mock fallback for this capability"],
        )

    def _instantiate_module_adapter(
        self, adapter_module: str, candidate: DiscoveryCandidate
    ) -> ProviderAdapter | None:
        try:
            module_name, class_name = adapter_module.split(":", maxsplit=1)
            adapter_class = getattr(import_module(module_name), class_name)
        except (ImportError, AttributeError, ValueError):
            return None
        try:
            return adapter_class(registry_agent=candidate.to_registry_json())
        except TypeError:
            try:
                return adapter_class()
            except TypeError:
                return None

    def _apply_status_updates(
        self,
        candidates: list[DiscoveryCandidate],
        new_status_by_id: dict[str, dict[str, str]],
    ) -> int:
        if not new_status_by_id:
            return 0
        updated_candidates: list[DiscoveryCandidate] = []
        changed = 0
        for candidate in candidates:
            updates = new_status_by_id.get(candidate.id)
            if not updates:
                updated_candidates.append(candidate)
                continue
            new_status = _aggregate_status(updates.values(), candidate.benchmark_status)
            if new_status == candidate.benchmark_status:
                updated_candidates.append(candidate)
                continue
            updated_candidates.append(
                DiscoveryCandidate(
                    **{**candidate.__dict__, "benchmark_status": new_status}
                )
            )
            changed += 1
        if changed:
            self.discovery_store.save(updated_candidates)
        return changed


def _aggregate_status(statuses, fallback: str) -> str:
    statuses = list(statuses)
    if not statuses:
        return fallback
    if all(status == "passed" for status in statuses):
        return "passed"
    if any(status == "failed" for status in statuses):
        return "failed"
    return fallback
