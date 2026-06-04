#!/usr/bin/env python3
"""Run the Eval_Framework against discovered agentic candidates.

Cron-driven, idempotent batch worker — mirrors ``run_benchmark_scheduler.py``.
Evaluates discovered mcp_server / a2a_agent / ai_agent candidates through the
``EvalFramework`` (instead of skipping them as ``protocol_beta``) and persists
the real runs + rankings the framework produces.

Safe in a cron:
* ``--run-mode dry_run`` is the default → no live provider call.
* The protocol execution gate (PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION,
  default off) must be enabled for any sandbox/live run.
* Exit 0 even when nothing was evaluated (idempotent re-runs are fine).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.benchmark.scheduler import BenchmarkScheduler  # noqa: E402
from planmyagents_api.benchmark.store import benchmark_store_for_path  # noqa: E402
from planmyagents_api.cost.cost_cap import cost_cap_policy_from_env  # noqa: E402
from planmyagents_api.discovery.store import discovery_store_for_path  # noqa: E402
from planmyagents_api.eval.framework import build_default_framework  # noqa: E402
from planmyagents_api.eval.models import EvalRunMode  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate discovered agentic candidates via the Eval_Framework."
    )
    parser.add_argument(
        "--discovery-store",
        default=os.getenv(
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "discovery-store.sqlite"),
        ),
        help="Discovery store path or Postgres URL.",
    )
    parser.add_argument(
        "--benchmark-store",
        default=os.getenv(
            "PLANMYAGENTS_BENCHMARK_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "benchmark-store.json"),
        ),
        help="Benchmark store path or Postgres URL.",
    )
    parser.add_argument(
        "--benchmarks-dir",
        default=str(ROOT / "packages" / "benchmarks"),
        help="Directory containing benchmark YAML cases.",
    )
    parser.add_argument(
        "--run-mode",
        choices=[mode.value for mode in EvalRunMode],
        default=EvalRunMode.DRY_RUN.value,
        help="Eval run mode. dry_run (default) makes no live call.",
    )
    parser.add_argument(
        "--candidate-id",
        action="append",
        default=[],
        help="Limit evaluation to one or more candidate ids. Repeatable.",
    )
    parser.add_argument(
        "--capability",
        action="append",
        default=[],
        help="Limit evaluation to one or more capability ids. Repeatable.",
    )
    parser.add_argument(
        "--cases-per-capability",
        type=int,
        default=30,
        help="Maximum number of benchmark cases to run per capability.",
    )
    parser.add_argument(
        "--enable-live-probe",
        action="store_true",
        help="Allow the static-verification stage to make a live tools/list probe.",
    )
    args = parser.parse_args()

    discovery_store = discovery_store_for_path(args.discovery_store)
    benchmark_store = benchmark_store_for_path(args.benchmark_store)
    run_mode = EvalRunMode(args.run_mode)

    framework = build_default_framework(
        benchmarks_dir=Path(args.benchmarks_dir),
        cost_cap=cost_cap_policy_from_env(goal_hash="eval-scheduler"),
        enable_live_probe=args.enable_live_probe,
    )
    framework.cases_per_capability = args.cases_per_capability

    scheduler = BenchmarkScheduler(
        benchmarks_dir=Path(args.benchmarks_dir),
        discovery_store=discovery_store,
        benchmark_store=benchmark_store,
        cases_per_capability=args.cases_per_capability,
        eval_framework=framework,
    )
    report = scheduler.run_eval(
        run_mode=run_mode,
        candidate_ids={item for item in args.candidate_id if item} or None,
        capabilities={item for item in args.capability if item} or None,
    )
    print(
        json.dumps(
            {
                "discovery_store": args.discovery_store,
                "benchmark_store": args.benchmark_store,
                "run_mode": run_mode.value,
                **report.to_json(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
