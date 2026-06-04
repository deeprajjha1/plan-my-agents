"""Evidence-health CI guard (sprint-pitch-align P3-5).

Reads either the live `/health/evidence` HTTP endpoint or the underlying
`evidence_health` Postgres view, then fails the build (exit 1) when any of
the configured counters is below its required minimum. This is the
regression guard that catches the case where someone ships code that
breaks the cron — without it, the homepage strip can silently render
zeros for a week before anyone notices.

Designed to be called from CI:

    python scripts/check_evidence_health.py \
        --dsn "$PLANMYAGENTS_DISCOVERY_STORE_URL" \
        --min-benchmark-runs-24h 1 \
        --main-only

* `--main-only` no-ops on every branch except `main` (reads `GITHUB_REF`).
* `--dsn` or `--url` selects the data source. With `--dsn` we query the
  `evidence_health` view directly; with `--url` we GET /health/evidence.
* Each minimum is a separate flag so different jobs can enforce different
  guarantees (e.g. nightly cron enforces all five, every-commit CI only
  enforces benchmark_runs_24h ≥ 1).

Exit codes:
  0 — all enforced counters meet their minimum (or guard skipped).
  1 — at least one counter is below its minimum.
  2 — failed to read the data source (treat as a CI infrastructure error).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

EXIT_OK = 0
EXIT_GUARD_FAILED = 1
EXIT_INFRASTRUCTURE = 2

DEFAULT_DSN_ENV = (
    "PLANMYAGENTS_DISCOVERY_STORE_URL",
    "PLANMYAGENTS_VERIFICATION_STORE_URL",
    "PLANMYAGENTS_BENCHMARK_STORE_URL",
)


def _resolve_dsn(cli_value: str | None) -> str | None:
    if cli_value:
        return cli_value
    for env_var in DEFAULT_DSN_ENV:
        value = os.environ.get(env_var)
        if value and value.startswith(("postgresql://", "postgres://")):
            return value
    return None


def _fetch_from_url(url: str, timeout: float) -> dict[str, int | str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError(f"unexpected /health/evidence body shape: {type(payload).__name__}")
    return payload


def _fetch_from_dsn(dsn: str) -> dict[str, int | str]:
    try:
        from psycopg import connect
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg required to query evidence_health view") from exc
    with connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT benchmark_runs_24h, verification_records_7d,
                       discovery_run_events_24h, capability_demand_events_24h,
                       discovery_gap_events_24h, route_status_routable_count,
                       benchmark_runs_total, verification_records_total,
                       checked_at::text
                FROM evidence_health
                """
            )
            row = cursor.fetchone()
    if not row:
        raise RuntimeError("evidence_health view returned no rows")
    keys = (
        "benchmark_runs_24h",
        "verification_records_7d",
        "discovery_run_events_24h",
        "capability_demand_events_24h",
        "discovery_gap_events_24h",
        "route_status_routable_count",
        "benchmark_runs_total",
        "verification_records_total",
        "checked_at",
    )
    return dict(zip(keys, row, strict=True))


def _is_main_branch() -> bool:
    ref = os.environ.get("GITHUB_REF", "") or os.environ.get("CI_COMMIT_REF_NAME", "")
    return ref in {"refs/heads/main", "refs/heads/master", "main", "master"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="Postgres DSN; queries the evidence_health view.")
    parser.add_argument(
        "--url",
        help="HTTP URL of the /health/evidence endpoint (e.g. http://localhost:8000/health/evidence).",
    )
    parser.add_argument(
        "--main-only",
        action="store_true",
        help="No-op when not on the main/master branch (reads GITHUB_REF).",
    )
    parser.add_argument("--timeout", type=float, default=10.0)

    # Per-counter minimums. Default to 0 (no enforcement) so each CI job
    # only enforces what it needs. Setting any of these to ≥1 turns it into
    # a hard CI gate.
    parser.add_argument("--min-benchmark-runs-24h", type=int, default=0)
    parser.add_argument("--min-verification-records-7d", type=int, default=0)
    parser.add_argument("--min-discovery-run-events-24h", type=int, default=0)
    parser.add_argument("--min-routable-cells", type=int, default=0)
    parser.add_argument("--min-benchmark-runs-total", type=int, default=0)
    parser.add_argument("--min-verification-records-total", type=int, default=0)

    args = parser.parse_args()

    if args.main_only and not _is_main_branch():
        print(
            json.dumps(
                {
                    "skipped": True,
                    "reason": "--main-only and current branch is not main/master",
                    "github_ref": os.environ.get("GITHUB_REF", ""),
                }
            )
        )
        return EXIT_OK

    try:
        if args.url:
            payload = _fetch_from_url(args.url, args.timeout)
        else:
            dsn = _resolve_dsn(args.dsn)
            if not dsn:
                print(
                    json.dumps(
                        {
                            "error": "no_data_source",
                            "message": (
                                "Pass --dsn / --url or set "
                                "PLANMYAGENTS_DISCOVERY_STORE_URL."
                            ),
                        }
                    ),
                    file=sys.stderr,
                )
                return EXIT_INFRASTRUCTURE
            payload = _fetch_from_dsn(dsn)
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
        print(
            json.dumps(
                {
                    "error": "fetch_failed",
                    "exception": f"{type(exc).__name__}: {exc}",
                }
            ),
            file=sys.stderr,
        )
        return EXIT_INFRASTRUCTURE

    checks = (
        ("benchmark_runs_24h", args.min_benchmark_runs_24h),
        ("verification_records_7d", args.min_verification_records_7d),
        ("discovery_run_events_24h", args.min_discovery_run_events_24h),
        ("route_status_routable_count", args.min_routable_cells),
        ("benchmark_runs_total", args.min_benchmark_runs_total),
        ("verification_records_total", args.min_verification_records_total),
    )

    failures: list[dict[str, int | str]] = []
    for key, minimum in checks:
        if minimum <= 0:
            continue
        value = int(payload.get(key) or 0)
        if value < minimum:
            failures.append(
                {
                    "metric": key,
                    "observed": value,
                    "minimum": minimum,
                }
            )

    result = {
        "payload": {k: payload.get(k) for k, _ in checks},
        "checked_at": payload.get("checked_at"),
        "failures": failures,
        "ok": not failures,
    }
    print(json.dumps(result, indent=2, default=str))
    return EXIT_OK if not failures else EXIT_GUARD_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
