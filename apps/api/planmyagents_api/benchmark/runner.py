"""Benchmark runner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from planmyagents_api.agents.base import ProviderAdapter
from planmyagents_api.benchmark.loader import load_test_cases
from planmyagents_api.benchmark.models import BenchmarkRun, ProviderRequest
from planmyagents_api.benchmark.scoring import score_response


class BenchmarkRunner:
    """Runs benchmark cases for one provider."""

    def __init__(self, benchmarks_dir: Path) -> None:
        self.benchmarks_dir = benchmarks_dir

    async def run(
        self,
        provider: ProviderAdapter,
        capability: str,
        limit: int | None = None,
    ) -> list[BenchmarkRun]:
        """Run benchmark cases for a provider/capability."""

        cases = load_test_cases(self.benchmarks_dir, capability=capability)
        if limit is not None:
            cases = cases[:limit]

        runs: list[BenchmarkRun] = []
        for case in cases:
            request = ProviderRequest(
                capability=case.capability,
                inputs=case.inputs,
                idempotency_key=f"{provider.provider_id}:{case.id}",
            )
            response = await provider.execute(request)
            score = score_response(case, response)
            runs.append(
                BenchmarkRun(
                    test_case_id=case.id,
                    provider_id=provider.provider_id,
                    capability=case.capability,
                    difficulty=case.difficulty,
                    response=response,
                    score=score,
                )
            )
        return runs


def run_sync(
    provider: ProviderAdapter,
    capability: str,
    benchmarks_dir: Path,
    limit: int | None = None,
) -> list[BenchmarkRun]:
    """Synchronous wrapper for CLI/tests."""

    return asyncio.run(BenchmarkRunner(benchmarks_dir).run(provider, capability, limit))


def write_runs_json(runs: list[BenchmarkRun], output_path: Path) -> None:
    """Write benchmark runs to JSON."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [run.to_json() for run in runs]
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_runs_json(path: Path) -> list[dict]:
    """Read benchmark run JSON produced by `write_runs_json`."""

    return json.loads(path.read_text())
