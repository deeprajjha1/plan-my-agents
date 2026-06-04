#!/usr/bin/env python3
"""Run benchmarks for selected discovery candidates and persist runs + rankings."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from importlib import import_module  # noqa: E402

from planmyagents_api.agents.base import ProviderAdapter  # noqa: E402
from planmyagents_api.benchmark.scheduler import BenchmarkScheduler  # noqa: E402
from planmyagents_api.benchmark.store import benchmark_store_for_path  # noqa: E402
from planmyagents_api.discovery.models import DiscoveryCandidate  # noqa: E402
from planmyagents_api.discovery.store import discovery_store_for_path  # noqa: E402
from planmyagents_api.registry.loader import (  # noqa: E402
    gated_benchmark_capabilities,
    load_registry,
)

# Scheduler-only adapter wiring for the six benchmark baselines that ship in
# this tree. Keys are `discovery_candidates.provider_id` (matches the
# `agents.json` provider id) → benchmark-baseline class. These classes live
# under `planmyagents_api.benchmark.baselines.*` and are explicitly firewalled
# from the user-facing router (`agents.router._is_benchmark_baseline`), so
# wiring them here cannot widen the customer execution surface. They get used
# only when the discovery_candidates row's own `adapter_module` column is
# empty — preserves any per-candidate override.
#
# sprint-pitch-align P2-2: without this map, `make benchmark-schedule
# --capability payment_authorization` would discover the Razorpay candidate
# but skip it with `skipped_no_adapter`, defeating the acceptance criterion
# (5 fresh rows in `benchmark_runs`).
BASELINE_ADAPTER_MODULES: dict[str, str] = {
    "razorpay-payments": "planmyagents_api.benchmark.baselines.razorpay:RazorpayPaymentAuthorization",
    "stripe-payments": "planmyagents_api.benchmark.baselines.stripe:StripePaymentAuthorization",
    "resend-emails": "planmyagents_api.benchmark.baselines.resend:ResendEmailSender",
    "firecrawl": "planmyagents_api.benchmark.baselines.firecrawl:FirecrawlScraper",
    "shippo-shipping": "planmyagents_api.benchmark.baselines.shippo:ShippoQuoteFetcher",
    "ebay-browse": "planmyagents_api.benchmark.baselines.ebay:EbayBrowseProvider",
}


def _baseline_adapter_resolver(
    candidate: DiscoveryCandidate, capability: str
) -> ProviderAdapter | None:
    """Resolve a benchmark baseline adapter from a discovery candidate row.

    The scheduler already tries `candidate.adapter_module` first; this
    resolver is only consulted when that field is empty AND the candidate's
    `provider_id` matches one of the hand-written baselines in this tree.
    """

    if candidate.adapter_module.strip():
        return None
    module_path = BASELINE_ADAPTER_MODULES.get(candidate.id)
    if module_path is None:
        return None
    try:
        module_name, class_name = module_path.split(":", maxsplit=1)
        adapter_class = getattr(import_module(module_name), class_name)
    except (ImportError, AttributeError, ValueError):
        return None
    try:
        adapter = adapter_class()
    except TypeError:
        return None
    if capability not in (getattr(adapter, "capabilities", None) or [capability]):
        return None
    return adapter


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run benchmarks against discovery candidates and persist runs + rankings."
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
        "--candidate-id",
        action="append",
        default=[],
        help="Limit scheduling to one or more candidate ids. Repeatable.",
    )
    parser.add_argument(
        "--capability",
        action="append",
        default=[],
        help="Limit scheduling to one or more capability ids. Repeatable.",
    )
    parser.add_argument(
        "--gated-from-registry",
        action="store_true",
        help=(
            "Set --capability automatically to every capability that has at "
            "least one agent with `requires_benchmark_gate=true` in "
            "packages/registry/agents.json. Replaces the historical hardcoded "
            "`email_verification` default and lets cron run every gated cell."
        ),
    )
    parser.add_argument(
        "--registry",
        default=str(ROOT / "packages" / "registry" / "agents.json"),
        help="Path to the provider registry JSON (used with --gated-from-registry).",
    )
    parser.add_argument(
        "--cases-per-capability",
        type=int,
        default=20,
        help="Maximum number of benchmark cases to run per capability.",
    )
    parser.add_argument(
        "--include-raw-leads",
        action="store_true",
        help="Also benchmark raw/unlisted leads. Defaults to candidates with provider evidence.",
    )
    parser.add_argument(
        "--no-mock-fallback",
        action="store_true",
        help="Refuse to fall back to synthetic mock adapters when no real adapter is available.",
    )
    args = parser.parse_args()

    discovery_store = discovery_store_for_path(args.discovery_store)
    benchmark_store = benchmark_store_for_path(args.benchmark_store)

    capabilities = {item for item in args.capability if item}
    gated_capabilities: list[str] = []
    if args.gated_from_registry:
        registry = load_registry(Path(args.registry))
        gated_capabilities = gated_benchmark_capabilities(registry)
        if not gated_capabilities:
            print(
                json.dumps(
                    {
                        "error": "no_gated_capabilities",
                        "registry": args.registry,
                        "message": (
                            "No agents in the registry have "
                            "`requires_benchmark_gate=true`."
                        ),
                    },
                    indent=2,
                )
            )
            return 1
        capabilities.update(gated_capabilities)

    scheduler = BenchmarkScheduler(
        benchmarks_dir=Path(args.benchmarks_dir),
        discovery_store=discovery_store,
        benchmark_store=benchmark_store,
        cases_per_capability=args.cases_per_capability,
        use_mock_fallback=not args.no_mock_fallback,
        adapter_resolver=_baseline_adapter_resolver,
    )
    report = scheduler.run(
        candidate_ids={item for item in args.candidate_id if item} or None,
        require_provider_evidence=not args.include_raw_leads,
        capabilities=capabilities or None,
    )
    print(
        json.dumps(
            {
                "discovery_store": args.discovery_store,
                "benchmark_store": args.benchmark_store,
                "gated_from_registry": bool(args.gated_from_registry),
                "gated_capabilities": gated_capabilities,
                **report.to_json(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
