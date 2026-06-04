"""Benchmark CLI implementation."""

from __future__ import annotations

import argparse
from pathlib import Path

from planmyagents_api.agents.mock import MockContactEnricher, MockEmailVerifier
from planmyagents_api.benchmark.baselines.hunter import HunterEmailVerifier
from planmyagents_api.benchmark.report import render_markdown_report
from planmyagents_api.benchmark.runner import read_runs_json, run_sync, write_runs_json
from planmyagents_api.benchmark.store import benchmark_store_for_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="planmyagents-bench",
        description="Run PlanMyAgents provider benchmarks",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    run_parser = subcommands.add_parser("run", help="Run benchmark cases")
    run_parser.add_argument("--capability", required=True)
    run_parser.add_argument(
        "--provider",
        required=True,
        choices=["mock-email", "mock-email-bad", "mock-contact", "hunter"],
    )
    run_parser.add_argument("--benchmarks", default="packages/benchmarks")
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--output", default=".planmyagents_runs/latest.json")
    run_parser.add_argument("--store", help="Optional benchmark store path/URL for persisted runs")

    report_parser = subcommands.add_parser("report", help="Render a markdown report from run JSON")
    report_parser.add_argument("--input", default=".planmyagents_runs/latest.json")
    report_parser.add_argument("--output")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        provider = _provider(args.provider)
        runs = run_sync(
            provider=provider,
            capability=args.capability,
            benchmarks_dir=Path(args.benchmarks),
            limit=args.limit,
        )
        write_runs_json(runs, Path(args.output))
        if args.store:
            benchmark_store_for_path(args.store).save(runs)
        print(f"wrote {len(runs)} runs to {args.output}")
        return 0

    if args.command == "report":
        runs = read_runs_json(Path(args.input))
        report = render_markdown_report(runs)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(report)
            print(f"wrote report to {args.output}")
        else:
            print(report)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def _provider(provider_id: str):
    if provider_id == "mock-email":
        return MockEmailVerifier()
    if provider_id == "mock-email-bad":
        return MockEmailVerifier(provider_id="mock-email-bad", dishonest=True)
    if provider_id == "mock-contact":
        return MockContactEnricher()
    if provider_id == "hunter":
        return HunterEmailVerifier()
    raise ValueError(f"unknown provider: {provider_id}")


if __name__ == "__main__":
    raise SystemExit(main())
