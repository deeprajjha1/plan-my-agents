#!/usr/bin/env python3
"""Lift any pre-existing api_provider / payment_provider rows out of
the agentic `discovery_candidates` table into the new
`apis_without_agents` table.

Production postgres handles this via the SQL migration in
`infra/postgres/init/001_planmyagents.sql` (run automatically by the
docker-compose stack on first boot). This script is for local
SQLite / JSON dev environments that pre-date the schema split — it's
the one-time "lift my old discovery-store.sqlite into the new shape"
helper.

Idempotent: running twice on a clean store is a no-op.

Usage:
    python scripts/migrate_apis_without_agents.py \\
        --store .planmyagents_runs/discovery-store.sqlite

Defaults to the path in `PLANMYAGENTS_DISCOVERY_STORE_URL` if set.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from planmyagents_api.discovery.store import discovery_store_for_path  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        type=str,
        default=os.environ.get("PLANMYAGENTS_DISCOVERY_STORE_URL", ""),
        help=(
            "Discovery store path (SQLite file, JSON file, or Postgres "
            "DSN). Defaults to $PLANMYAGENTS_DISCOVERY_STORE_URL."
        ),
    )
    args = parser.parse_args()

    if not args.store:
        parser.error(
            "no store specified — pass --store or set "
            "PLANMYAGENTS_DISCOVERY_STORE_URL"
        )

    facade = discovery_store_for_path(args.store)
    migrated = facade.migrate_legacy_non_agentic_rows()
    if migrated == 0:
        print(f"already-clean: 0 rows to migrate from {args.store}")
    else:
        print(
            f"migrated {migrated} api_provider/payment_provider rows from "
            f"{args.store} into apis_without_agents"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
