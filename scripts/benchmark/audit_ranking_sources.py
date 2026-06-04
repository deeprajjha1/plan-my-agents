#!/usr/bin/env python3
"""Audit the `source` field on every row in the agent-rankings store.

Stage 0 of the trust-layer credibility roadmap: before we publish anything,
we have to know what we have. This script prints the per-capability split of
synthetic vs real-adapter rankings, flags rows whose `source` is missing or
unrecognised, and writes a JSON snapshot under `.planmyagents_runs/` so subsequent
runs can diff.

Read-only: never mutates the store. Cheap enough to run in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.benchmark.store import benchmark_store_for_path  # noqa: E402

KNOWN_REAL_SOURCES = {"real_adapter", "live", "vendor", "production"}
KNOWN_SYNTHETIC_SOURCES = {"synthetic", "mock", "fixture", "stub"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-store",
        default=os.getenv(
            "PLANMYAGENTS_BENCHMARK_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "benchmark-store.json"),
        ),
        help="Benchmark store URL (.json file or postgres:// URL).",
    )
    parser.add_argument(
        "--snapshot-dir",
        default=str(ROOT / ".planmyagents_runs" / "credibility"),
        help="Directory to write the audit snapshot JSON.",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress per-capability breakdown."
    )
    args = parser.parse_args()

    store = benchmark_store_for_path(args.benchmark_store)
    rankings = list(store.load_rankings())
    if not rankings:
        print(
            f"No rankings found in {args.benchmark_store}. "
            "Run `make benchmark-schedule` first.",
            file=sys.stderr,
        )
        return 1

    by_capability: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rankings:
        capability = str(row.get("capability") or "")
        if capability:
            by_capability[capability].append(row)

    overall_source_counts: Counter[str] = Counter()
    anomalies: list[dict[str, Any]] = []
    capability_summaries: list[dict[str, Any]] = []

    for capability, rows in sorted(by_capability.items()):
        sources = Counter(str(row.get("source") or "<missing>") for row in rows)
        overall_source_counts.update(sources)
        unknown_sources = sorted(
            source
            for source in sources
            if source not in KNOWN_REAL_SOURCES
            and source not in KNOWN_SYNTHETIC_SOURCES
        )
        for source in unknown_sources:
            for row in rows:
                if str(row.get("source") or "<missing>") == source:
                    anomalies.append(
                        {
                            "capability": capability,
                            "provider_id": row.get("provider_id"),
                            "source": source,
                            "sample_size": row.get("sample_size"),
                        }
                    )
        real_count = sum(
            count for src, count in sources.items() if src in KNOWN_REAL_SOURCES
        )
        synthetic_count = sum(
            count for src, count in sources.items() if src in KNOWN_SYNTHETIC_SOURCES
        )
        capability_summaries.append(
            {
                "capability": capability,
                "ranked_providers": len(rows),
                "real_count": real_count,
                "synthetic_count": synthetic_count,
                "unknown_sources": unknown_sources,
            }
        )

    if not args.quiet:
        print("Per-capability source breakdown")
        print("-" * 60)
        for summary in capability_summaries:
            print(
                f"{summary['capability']:<30}  "
                f"providers={summary['ranked_providers']:>3}  "
                f"real={summary['real_count']:>3}  "
                f"synthetic={summary['synthetic_count']:>3}  "
                f"unknown={','.join(summary['unknown_sources']) or '-'}"
            )
        print()
        print("Overall source distribution")
        print("-" * 60)
        for source, count in overall_source_counts.most_common():
            label = (
                "real"
                if source in KNOWN_REAL_SOURCES
                else "synthetic"
                if source in KNOWN_SYNTHETIC_SOURCES
                else "UNKNOWN"
            )
            print(f"  {source:<20} ({label:>9})  {count}")
        if anomalies:
            print()
            print(f"Anomalies: {len(anomalies)} ranking row(s) with unknown source.")
            print("Investigate before publishing any leaderboard externally.")

    snapshot_dir = Path(args.snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = snapshot_dir / f"audit-{timestamp}.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "benchmark_store": args.benchmark_store,
                "generated_at": timestamp,
                "overall_source_counts": dict(overall_source_counts),
                "anomalies": anomalies,
                "capabilities": capability_summaries,
            },
            indent=2,
            sort_keys=True,
        )
    )
    if not args.quiet:
        print(f"\nSnapshot written to {snapshot_path}")

    return 0 if not anomalies else 2


if __name__ == "__main__":
    sys.exit(main())
