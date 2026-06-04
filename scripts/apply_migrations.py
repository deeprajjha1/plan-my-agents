#!/usr/bin/env python3
"""Apply Postgres schemas for discovery, benchmark, and verification stores.

The individual stores already auto-apply their schema on first write, but this
script lets ops/CI ensure all tables and indexes exist up-front against a fresh
database, without depending on any specific candidate or benchmark run.

Usage:
    PLANMYAGENTS_DISCOVERY_STORE_URL=postgresql://... \
      python3 scripts/apply_migrations.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.benchmark.store import (  # noqa: E402
    BENCHMARK_STORE_SCHEMA,
    PostgresBenchmarkStore,
)
from planmyagents_api.discovery.apis_without_agents_store import (  # noqa: E402
    POSTGRES_APIS_WITHOUT_AGENTS_STORE_SCHEMA,
    PostgresApisWithoutAgentsStore,
)
from planmyagents_api.discovery.demand_store import (  # noqa: E402
    POSTGRES_DEMAND_SCHEMA,
    PostgresDemandStore,
)
from planmyagents_api.discovery.discovery_gaps_store import (  # noqa: E402
    POSTGRES_DISCOVERY_GAPS_SCHEMA,
    PostgresDiscoveryGapsStore,
)
from planmyagents_api.discovery.run_log import (  # noqa: E402
    POSTGRES_RUN_EVENTS_SCHEMA,
    PostgresRunEventStore,
)
from planmyagents_api.discovery.store import (  # noqa: E402
    POSTGRES_DISCOVERY_STORE_SCHEMA,
    PostgresDiscoveryStore,
)
from planmyagents_api.discovery.verification_store import (  # noqa: E402
    VERIFICATION_STORE_SCHEMA,
    PostgresVerificationStore,
)
from planmyagents_api.marketplace_store import (  # noqa: E402
    MARKETPLACE_STORE_POSTGRES_SCHEMA,
    PostgresMarketplaceStore,
)
from planmyagents_api.planner.capability_label_store import (  # noqa: E402
    POSTGRES_CAPABILITY_LABELS_SCHEMA,
    PostgresCapabilityLabelStore,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Postgres schemas idempotently.")
    parser.add_argument(
        "--url",
        default=os.getenv("PLANMYAGENTS_DISCOVERY_STORE_URL", ""),
        help="Postgres URL (postgresql://user:pass@host:port/db).",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Print which schema strings would be applied without connecting.",
    )
    args = parser.parse_args()

    if args.check_only:
        print(
            json.dumps(
                {
                    "schemas": [
                        {"name": "discovery_candidates", "statements": _count(POSTGRES_DISCOVERY_STORE_SCHEMA)},
                        {
                            "name": "apis_without_agents",
                            "statements": _count(POSTGRES_APIS_WITHOUT_AGENTS_STORE_SCHEMA),
                        },
                        {"name": "discovery_run_events", "statements": _count(POSTGRES_RUN_EVENTS_SCHEMA)},
                        {"name": "capability_demand_events", "statements": _count(POSTGRES_DEMAND_SCHEMA)},
                        {"name": "discovery_gap_events", "statements": _count(POSTGRES_DISCOVERY_GAPS_SCHEMA)},
                        {"name": "capability_labels", "statements": _count(POSTGRES_CAPABILITY_LABELS_SCHEMA)},
                        {"name": "benchmark_runs+agent_rankings", "statements": _count(BENCHMARK_STORE_SCHEMA)},
                        {"name": "verification_records", "statements": _count(VERIFICATION_STORE_SCHEMA)},
                        {
                            "name": "marketplace_store (users+workspaces+saved_recipes)",
                            "statements": _count(MARKETPLACE_STORE_POSTGRES_SCHEMA),
                        },
                    ],
                },
                indent=2,
            )
        )
        return 0

    if not args.url or not args.url.startswith(("postgresql://", "postgres://")):
        parser.error(
            "A Postgres --url or PLANMYAGENTS_DISCOVERY_STORE_URL is required."
        )

    PostgresDiscoveryStore(args.url).apply_schema()
    # These three self-apply on construction (CREATE TABLE IF NOT EXISTS is
    # idempotent), so just instantiating them creates the tables. We keep
    # the explicit apply_schema() calls so this script remains the canonical
    # place to look for "what does make migrate run?".
    PostgresApisWithoutAgentsStore(args.url).apply_schema()
    PostgresRunEventStore(args.url).apply_schema()
    PostgresDemandStore(args.url)  # __init__ creates the table
    PostgresDiscoveryGapsStore(args.url).apply_schema()
    PostgresCapabilityLabelStore(args.url).apply_schema()
    PostgresBenchmarkStore(args.url).apply_schema()
    PostgresVerificationStore(args.url).apply_schema()
    # Pro-tier + (future) vendor schema. Lives in its own Postgres schema
    # (marketplace_store.*) per LLD §3.11 so blast-radius is isolated.
    PostgresMarketplaceStore(args.url).apply_schema()
    migrated = _migrate_known_provider_status(args.url)
    # sprint-pitch-align P3-4 — evidence_health view. The five evidence
    # tables it joins are all guaranteed to exist by the apply_schema()
    # calls above, so this is the last migration step.
    _apply_evidence_health_view(args.url)

    print(
        json.dumps(
            {"ok": True, "url": args.url, "applied": True, "data_migrations": migrated},
            indent=2,
        )
    )
    return 0


def _count(schema: str) -> int:
    return sum(1 for line in schema.split(";") if line.strip())


def _migrate_known_provider_status(url: str) -> dict[str, int]:
    """Rename the old provider trust tier to an honest canonical enum.

    ``provider_verified`` implied PlanMyAgents had verified the provider. The
    canonical value is now ``known_provider``: known provider identity/docs
    signal, not a capability test. This is safe to run repeatedly.
    """

    from psycopg import connect

    migrated: dict[str, int] = {}
    with connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE discovery_candidates
                SET verification_status = 'known_provider'
                WHERE verification_status = 'provider_verified'
                """
            )
            migrated["discovery_candidates"] = cursor.rowcount
            cursor.execute(
                """
                UPDATE discovery_candidates
                SET raw_candidate = jsonb_set(
                    raw_candidate,
                    '{verification_status}',
                    to_jsonb('known_provider'::text),
                    true
                )
                WHERE raw_candidate->>'verification_status' = 'provider_verified'
                """
            )
            migrated["discovery_candidates_raw_candidate"] = cursor.rowcount
            cursor.execute(
                """
                UPDATE verification_records
                SET status = 'known_provider'
                WHERE status = 'provider_verified'
                """
            )
            migrated["verification_records"] = cursor.rowcount
    return migrated


def _apply_evidence_health_view(url: str) -> None:
    """Apply the `evidence_health` view defined in
    `infra/postgres/init/002_evidence_health.sql` so dashboards can chart the
    cron health directly against Postgres."""

    from psycopg import connect

    sql_path = ROOT / "infra" / "postgres" / "init" / "002_evidence_health.sql"
    sql = sql_path.read_text()
    with connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)


if __name__ == "__main__":
    raise SystemExit(main())
