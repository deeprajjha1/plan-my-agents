"""Rebuild `agent_rankings` from `benchmark_runs`.

Wraps :func:`planmyagents_api.benchmark.rebuild.rebuild_rankings_from_runs`
in a CLI so operators can run it without writing Python:

    make rebuild-rankings

Use it whenever ``benchmark_runs`` has rows the leaderboard doesn't
reflect — the canonical symptom is "/leaderboards shows
``Bench-passed 0 · Real runs No`` for a capability that has succeeded
runs in the raw table". This script is the supported recovery path
and is also called automatically by
``scripts/backfill_phase5_cells.py`` after its bulk insert.

DSN resolution (in order):
1. ``--dsn`` command-line flag
2. ``PLANMYAGENTS_BENCHMARK_STORE_URL`` env var
3. ``PLANMYAGENTS_DISCOVERY_STORE_URL`` env var (legacy shared-DSN deploys)
4. The canonical default in ``planmyagents_api._config`` (which is
   the same DSN ``.env`` and ``docker-compose.yml`` set up).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api._config import benchmark_store_url  # noqa: E402
from planmyagents_api.benchmark.rebuild import (  # noqa: E402
    rebuild_rankings_from_runs,
)
from planmyagents_api.benchmark.store import benchmark_store_for_path  # noqa: E402


def _resolve_dsn(cli_dsn: str | None) -> str:
    if cli_dsn:
        return cli_dsn
    return benchmark_store_url()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="Postgres DSN (defaults to env or _config).")
    args = parser.parse_args()

    dsn = _resolve_dsn(args.dsn)
    store = benchmark_store_for_path(dsn)
    report = rebuild_rankings_from_runs(store)

    print(
        json.dumps(
            {
                "dsn": dsn,
                "runs_loaded": report.runs_loaded,
                "cells_total": report.cells_total,
                "cells_real_adapter": report.cells_real_adapter,
                "cells_response_fixture": report.cells_response_fixture,
                "cells_synthetic": report.cells_synthetic,
                "rankings_persisted": report.rankings_persisted,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
