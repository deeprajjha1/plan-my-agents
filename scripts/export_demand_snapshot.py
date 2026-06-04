"""Sprint-6 design-partner demand snapshot exporter.

Produces a single CSV file you can paste straight into a design-partner
conversation, RevOps email to a vendor, or investor follow-up. Each row
is one capability the world has asked PlanMyAgents about in the lookback
window, joined to:

* total request count and distinct-requester count from
  ``capability_demand_events``
* zero-yield-discovery count and routable status from
  ``discovery_gap_events`` (the "we tried, found nothing" signal)
* whether a routable benchmark cell currently exists for the capability
  in ``benchmark_runs`` (last 30 days, succeeded=true) — turns "asked
  for and not supplied" into a sharp X mark instead of a vague tag
* up to three sample goal excerpts so the partner can read what real
  user intent looks like

The CSV header is deliberately stable: vendor outreach + design-partner
reports both copy-paste it without column re-mapping. New columns get
appended on the right, never inserted in the middle.

Designed to be safe to run in a cron (no side effects, read-only) and
in a Slack-paste flow (default `--lookback-days 30` → bounded output).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

DEFAULT_DSN = "postgresql://planmyagents:planmyagents@localhost:55433/planmyagents"
CSV_HEADER = [
    "capability_id",
    "request_count",
    "distinct_requester_count",
    "last_requested_at",
    "discovery_attempts",
    "zero_yield_count",
    "judge_accepted_total",
    "routable_today",  # bool — true if any succeeded benchmark_runs row in last 30d
    "routable_provider_ids",  # comma-separated providers
    "sample_goal_1",
    "sample_goal_2",
    "sample_goal_3",
]


def _resolve_dsn(arg_dsn: str | None) -> str:
    return (
        arg_dsn
        or os.environ.get("PLANMYAGENTS_DEMAND_STORE_URL")
        or os.environ.get("PLANMYAGENTS_DISCOVERY_STORE_URL")
        or os.environ.get("PLANMYAGENTS_BENCHMARK_STORE_URL")
        or DEFAULT_DSN
    )


def _fetch_demand(dsn: str, since_iso: str) -> dict[str, dict[str, Any]]:
    """Aggregate capability_demand_events to per-capability totals."""
    import psycopg

    out: dict[str, dict[str, Any]] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT capability_id,
                       COUNT(*),
                       COUNT(DISTINCT requester_hash) FILTER (WHERE requester_hash <> ''),
                       MAX(requested_at)
                FROM capability_demand_events
                WHERE requested_at >= %s
                GROUP BY capability_id
                """,
                (since_iso,),
            )
            for cap, count, distinct_req, last in cur.fetchall():
                if not cap:
                    continue
                out[cap] = {
                    "request_count": int(count or 0),
                    "distinct_requester_count": int(distinct_req or 0),
                    "last_requested_at": (
                        last.isoformat() if hasattr(last, "isoformat") else str(last or "")
                    ),
                }
            cur.execute(
                """
                SELECT capability_id, goal_excerpt, requested_at
                FROM capability_demand_events
                WHERE requested_at >= %s
                  AND goal_excerpt <> ''
                ORDER BY requested_at DESC
                """,
                (since_iso,),
            )
            seen: dict[str, set[str]] = {}
            for cap, excerpt, _requested_at in cur.fetchall():
                if not cap:
                    continue
                row = out.setdefault(cap, {
                    "request_count": 0,
                    "distinct_requester_count": 0,
                    "last_requested_at": "",
                })
                samples_seen = seen.setdefault(cap, set())
                samples = row.setdefault("sample_goals", [])
                excerpt_clean = (excerpt or "").strip()
                if excerpt_clean and excerpt_clean not in samples_seen and len(samples) < 3:
                    samples_seen.add(excerpt_clean)
                    samples.append(excerpt_clean)
    return out


def _fetch_gaps(dsn: str, since_iso: str) -> dict[str, dict[str, Any]]:
    """Aggregate discovery_gap_events to per-capability totals."""
    import psycopg

    out: dict[str, dict[str, Any]] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT capability_id,
                       COUNT(*),
                       SUM(CASE WHEN judge_accepted = 0 THEN 1 ELSE 0 END),
                       COALESCE(SUM(judge_accepted), 0)
                FROM discovery_gap_events
                WHERE observed_at >= %s
                GROUP BY capability_id
                """,
                (since_iso,),
            )
            for cap, attempts, zero_yield, accepted in cur.fetchall():
                if not cap:
                    continue
                out[cap] = {
                    "discovery_attempts": int(attempts or 0),
                    "zero_yield_count": int(zero_yield or 0),
                    "judge_accepted_total": int(accepted or 0),
                }
    return out


def _fetch_routable(dsn: str) -> dict[str, list[str]]:
    """Per-capability list of providers with succeeded benchmark runs in
    the last 30 days (same window the homepage strip uses)."""
    import psycopg

    out: dict[str, list[str]] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT capability, provider_id
                FROM benchmark_runs
                WHERE succeeded = true
                  AND created_at > now() - interval '30 days'
                GROUP BY capability, provider_id
                ORDER BY capability, provider_id
                """
            )
            for cap, pid in cur.fetchall():
                out.setdefault(str(cap), []).append(str(pid))
    return out


def _build_rows(
    demand: dict[str, dict[str, Any]],
    gaps: dict[str, dict[str, Any]],
    routable: dict[str, list[str]],
) -> list[dict[str, Any]]:
    capabilities = sorted(
        set(demand) | set(gaps),
        key=lambda c: (
            -(demand.get(c, {}).get("request_count") or 0),
            -(gaps.get(c, {}).get("zero_yield_count") or 0),
            c,
        ),
    )
    rows: list[dict[str, Any]] = []
    for cap in capabilities:
        d = demand.get(cap, {})
        g = gaps.get(cap, {})
        routable_providers = routable.get(cap, [])
        samples = list(d.get("sample_goals", []))
        samples += [""] * (3 - len(samples))
        rows.append({
            "capability_id": cap,
            "request_count": int(d.get("request_count") or 0),
            "distinct_requester_count": int(d.get("distinct_requester_count") or 0),
            "last_requested_at": d.get("last_requested_at") or "",
            "discovery_attempts": int(g.get("discovery_attempts") or 0),
            "zero_yield_count": int(g.get("zero_yield_count") or 0),
            "judge_accepted_total": int(g.get("judge_accepted_total") or 0),
            "routable_today": bool(routable_providers),
            "routable_provider_ids": ",".join(routable_providers),
            "sample_goal_1": samples[0],
            "sample_goal_2": samples[1],
            "sample_goal_3": samples[2],
        })
    return rows


def _write_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _summary_line(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "demand-snapshot: 0 capabilities (no demand or gap events in window)"
    total_requests = sum(r["request_count"] for r in rows)
    routable = sum(1 for r in rows if r["routable_today"])
    unmet = sum(1 for r in rows if r["request_count"] > 0 and not r["routable_today"])
    top = rows[0]
    return (
        f"demand-snapshot: {len(rows)} capabilities, "
        f"{total_requests} requests total, "
        f"{routable} routable today, {unmet} asked-for-but-unmet | "
        f"top: {top['capability_id']} "
        f"({top['request_count']} req, {top['zero_yield_count']} zero-yield)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="Postgres DSN (defaults to env or local).")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="Lookback window for demand + gap events (default 30).",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "demand_snapshot.csv"),
        help="Output CSV path (default: data/demand_snapshot.csv).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print CSV to stdout instead of writing a file. Suppresses the file write.",
    )
    args = parser.parse_args()

    dsn = _resolve_dsn(args.dsn)
    since_dt = datetime.now(UTC) - timedelta(days=args.lookback_days)
    since_iso = since_dt.isoformat()

    demand = _fetch_demand(dsn, since_iso)
    gaps = _fetch_gaps(dsn, since_iso)
    routable = _fetch_routable(dsn)
    rows = _build_rows(demand, gaps, routable)

    if args.stdout:
        writer = csv.DictWriter(
            sys.stdout, fieldnames=CSV_HEADER, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)
    else:
        output_path = Path(args.output).resolve()
        _write_csv(rows, output_path)
        print(_summary_line(rows))
        print(f"demand-snapshot: wrote {len(rows)} rows to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
