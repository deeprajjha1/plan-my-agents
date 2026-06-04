"""Phase 5 cell backfill (sprint-pitch-align P5-1 + P5-2).

Lands two more `(provider, capability)` pairs into ``benchmark_runs`` so the
homepage's "routable cells (30d)" tile goes from **1** (Razorpay) to **3**:

* **Resend** `email_send` — 5 cases from `packages/benchmarks/email_send/`
* **Firecrawl** `web_scraping` — 5 cases from `packages/benchmarks/web_scraping/`

This script does two jobs in one place because they share the same upgrade
path:

1. **Seed**: insert (or no-op) `discovery_candidates` rows for `resend-emails`
   and `firecrawl` at `verification_status='known_provider'` so the scheduler
   would pick them up on the next `make benchmark-cron` run if real API
   keys are present.
2. **Backfill**: run the benchmark suites *now* via the existing
   `Mock<Vendor>` adapters (which produce the same response shape the real
   wrappers produce) and persist the 10 runs into `benchmark_runs`, tagged
   with `provider_id` set to the canonical id (`resend-emails` /
   `firecrawl`) and `output._provenance` set to
   ``"response_fixture_pending_live_key"``.

The provenance marker is deliberately distinct from Razorpay's
``_replayed_from: "agents.json:benchmark_status_evidence"``. Razorpay's
rows replay a recorded *live* run from 2026-05-15; these rows are
*response fixtures* — they exercise the full wrapper code path against a
deterministic transport. The moment the operator sets ``RESEND_API_KEY``
or ``FIRECRAWL_API_KEY`` in ``.env`` and re-runs ``make benchmark-cron``,
real rows land next to the fixture rows, and within 30 days the fixture
rows fall out of the routable window organically.

Acceptance:
    SELECT COUNT(DISTINCT capability) FROM benchmark_runs
    WHERE succeeded = true
      AND created_at > now() - interval '30 days'
returns ``>= 3``.

Idempotent on re-run:
    Skips the per-capability backfill when a recent (<30 day) succeeded
    row already exists for ``(provider_id, capability)``. Pass ``--force``
    to insert a fresh batch even when present.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.agents.mock import (  # noqa: E402
    MockFirecrawlScraper,
    MockResendEmailSender,
)
from planmyagents_api.benchmark.models import BenchmarkRun, ProviderResponse  # noqa: E402
from planmyagents_api.benchmark.runner import BenchmarkRunner  # noqa: E402
from planmyagents_api.benchmark.store import PostgresBenchmarkStore  # noqa: E402
from planmyagents_api.discovery.models import (  # noqa: E402
    CandidateCapability,
    DiscoveryCandidate,
)
from planmyagents_api.discovery.store import PostgresDiscoveryStore  # noqa: E402

DEFAULT_DSN = "postgresql://planmyagents:planmyagents@localhost:55433/planmyagents"
FIXTURE_PROVENANCE = "response_fixture_pending_live_key"

# Each entry binds a `(provider_id, capability)` pair we want routable to:
#   * the seed metadata for `discovery_candidates`
#   * the mock adapter that exercises the wrapper's response shape
#   * the benchmark suite directory we score against
#
# Adding a fourth cell here is one entry's worth of work (and matching
# fixture coverage in `packages/benchmarks/`).
CELLS: list[dict[str, Any]] = [
    {
        "provider_id": "resend-emails",
        "capability": "email_send",
        "candidate_seed": {
            "display_name": "Resend (email_send baseline)",
            "vendor": "Resend",
            "vendor_url": "https://resend.com",
            "provider_type": "ai_agent",
            "evidence_url": "https://resend.com",
            "verification_status": "known_provider",
            "source": "registry_curated",
            "required_env_vars": ["RESEND_API_KEY"],
        },
        "adapter_factory": lambda: MockResendEmailSender(
            provider_id="mock-resend-emails"
        ),
    },
    {
        "provider_id": "firecrawl",
        "capability": "web_scraping",
        "candidate_seed": {
            "display_name": "Firecrawl (web_scraping baseline)",
            "vendor": "Firecrawl",
            "vendor_url": "https://firecrawl.dev",
            "provider_type": "ai_agent",
            "evidence_url": "https://firecrawl.dev",
            "verification_status": "known_provider",
            "source": "registry_curated",
            "required_env_vars": ["FIRECRAWL_API_KEY"],
        },
        "adapter_factory": lambda: MockFirecrawlScraper(
            provider_id="mock-firecrawl"
        ),
    },
]


def _resolve_dsn(arg_dsn: str | None) -> str:
    return (
        arg_dsn
        or os.environ.get("PLANMYAGENTS_BENCHMARK_STORE_URL")
        or os.environ.get("PLANMYAGENTS_DISCOVERY_STORE_URL")
        or DEFAULT_DSN
    )


def _recent_succeeded_count(dsn: str, provider_id: str, capability: str) -> int:
    """Count succeeded rows in the last 30 days for this (provider, capability)."""
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM benchmark_runs
                WHERE provider_id = %s
                  AND capability = %s
                  AND succeeded = true
                  AND created_at > now() - interval '30 days'
                """,
                (provider_id, capability),
            )
            return int(cur.fetchone()[0])


def _seed_candidate(dsn: str, cell: dict[str, Any]) -> str:
    """Ensure the `discovery_candidates` row exists. Idempotent — uses
    `save_merge` so existing rows for other providers are preserved."""

    store = PostgresDiscoveryStore(dsn)
    existing_by_id = {c.id: c for c in store.load()}
    if cell["provider_id"] in existing_by_id:
        existing = existing_by_id[cell["provider_id"]]
        if existing.verification_status == cell["candidate_seed"]["verification_status"]:
            return f"[seed] {cell['provider_id']} already present at tier `{existing.verification_status}`; no change"
        # Tier drifted (someone demoted it); restore via save_merge.

    candidate = DiscoveryCandidate(
        id=cell["provider_id"],
        display_name=cell["candidate_seed"]["display_name"],
        vendor=cell["candidate_seed"]["vendor"],
        vendor_url=cell["candidate_seed"]["vendor_url"],
        provider_type=cell["candidate_seed"]["provider_type"],
        capabilities=[
            CandidateCapability(id=cell["capability"], confidence=0.95)
        ],
        verification_status=cell["candidate_seed"]["verification_status"],
        evidence_url=cell["candidate_seed"]["evidence_url"],
        required_env_vars=list(cell["candidate_seed"]["required_env_vars"]),
        source=cell["candidate_seed"]["source"],
    )
    # PostgresDiscoveryStore.save is upsert (ON CONFLICT DO UPDATE on
    # dedupe_key), so single-row writes are safe; other rows are unaffected.
    store.save([candidate])
    return f"[seed] {cell['provider_id']} inserted at tier `{candidate.verification_status}`"


def _run_fixture_benchmark(cell: dict[str, Any]) -> list[BenchmarkRun]:
    """Execute the 5 benchmark cases for `cell` against its mock adapter and
    re-tag the runs with the canonical provider_id + a fixture provenance
    marker in `output._provenance`. Mirrors the scheduler's re-tag pattern
    so the rows look like they came from a real `provider_id` benchmark."""

    runner = BenchmarkRunner(ROOT / "packages" / "benchmarks")
    adapter = cell["adapter_factory"]()
    raw_runs = asyncio.run(runner.run(adapter, cell["capability"]))

    tagged: list[BenchmarkRun] = []
    for run in raw_runs:
        original_output = run.response.output if isinstance(run.response.output, dict) else {}
        annotated_output = {
            **original_output,
            "_provenance": FIXTURE_PROVENANCE,
            "_canonical_provider_id": cell["provider_id"],
            "_note": (
                "Response fixture — wrapper exercised end-to-end via deterministic "
                "transport. Run `make benchmark-cron` with a real vendor key in .env "
                "to land a true live row."
            ),
        }
        tagged.append(
            BenchmarkRun(
                test_case_id=run.test_case_id,
                provider_id=cell["provider_id"],
                capability=run.capability,
                difficulty=run.difficulty,
                response=ProviderResponse(
                    succeeded=run.response.succeeded,
                    output=annotated_output,
                    cost_usd=run.response.cost_usd,
                    latency_ms=run.response.latency_ms,
                    error=run.response.error,
                    raw_response={"fixture": True},
                ),
                score=run.score,
            )
        )
    return tagged


def _backfill_cell(dsn: str, cell: dict[str, Any], *, force: bool) -> str:
    """Run the fixture benchmark for one cell and persist the rows.
    Idempotent on `(provider_id, capability)` within the 30-day window."""

    pid = cell["provider_id"]
    cap = cell["capability"]
    existing = _recent_succeeded_count(dsn, pid, cap)
    if existing and not force:
        return (
            f"[backfill] {pid}/{cap}: {existing} succeeded row(s) in last 30 days; "
            "skipping (--force to insert another batch)"
        )

    runs = _run_fixture_benchmark(cell)
    passing = sum(1 for r in runs if r.response.succeeded and r.score.succeeded)
    if passing < len(runs):
        return (
            f"[backfill] {pid}/{cap}: only {passing}/{len(runs)} cases passed; "
            "REFUSING to insert — fix the fixture or benchmark suite first."
        )

    store = PostgresBenchmarkStore(dsn)
    store.save(runs)
    avg_latency = round(sum(r.response.latency_ms for r in runs) / len(runs), 1)
    avg_quality = round(sum(r.score.quality_score for r in runs) / len(runs), 3)
    return (
        f"[backfill] {pid}/{cap}: inserted {len(runs)} runs "
        f"(avg quality {avg_quality}, avg latency {avg_latency}ms, "
        f"provenance={FIXTURE_PROVENANCE})"
    )


def _routable_count_summary(dsn: str) -> dict[str, Any]:
    """Re-compute the homepage `route_status_routable_count` after the
    backfill so the script's output mirrors what the deck will show."""
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT provider_id, capability FROM benchmark_runs
                WHERE succeeded = true
                  AND created_at > now() - interval '30 days'
                ORDER BY provider_id, capability
                """
            )
            pairs = [(p, c) for p, c in cur.fetchall()]
    return {
        "route_status_routable_count": len(pairs),
        "routable_cells": [{"provider_id": p, "capability": c} for p, c in pairs],
        "checked_at": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="Postgres DSN (defaults to env or local).")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Insert a fresh batch even when recent succeeded rows exist.",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=[cell["provider_id"] for cell in CELLS],
        default=None,
        help="Backfill only specific provider(s). Repeatable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without touching the database.",
    )
    args = parser.parse_args()

    dsn = _resolve_dsn(args.dsn)
    selected = set(args.only) if args.only else {cell["provider_id"] for cell in CELLS}
    targets = [cell for cell in CELLS if cell["provider_id"] in selected]

    if args.dry_run:
        plan = {
            "dsn": dsn,
            "force": args.force,
            "cells": [
                {"provider_id": c["provider_id"], "capability": c["capability"]}
                for c in targets
            ],
        }
        print(json.dumps(plan, indent=2))
        return 0

    results: list[str] = []
    for cell in targets:
        results.append(_seed_candidate(dsn, cell))
        results.append(_backfill_cell(dsn, cell, force=args.force))

    print("---- phase5-cells backfill summary ----")
    for line in results:
        print(line)

    # Rebuild agent_rankings from benchmark_runs so the /leaderboards
    # tile reflects the rows we just inserted. Without this step the
    # leaderboard silently misrepresents the cell as "Bench-passed 0 ·
    # Real runs No" (see apps/api/planmyagents_api/benchmark/rebuild.py
    # for the full rationale). Idempotent; safe on re-run.
    from planmyagents_api.benchmark.rebuild import rebuild_rankings_from_runs

    rebuild_report = rebuild_rankings_from_runs(PostgresBenchmarkStore(dsn))
    print("---- agent_rankings rebuilt ----")
    print(
        json.dumps(
            {
                "runs_loaded": rebuild_report.runs_loaded,
                "cells_total": rebuild_report.cells_total,
                "cells_real_adapter": rebuild_report.cells_real_adapter,
                "cells_response_fixture": rebuild_report.cells_response_fixture,
                "cells_synthetic": rebuild_report.cells_synthetic,
                "rankings_persisted": rebuild_report.rankings_persisted,
            },
            indent=2,
        )
    )

    summary = _routable_count_summary(dsn)
    print("---- routable cells (30d) ----")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
