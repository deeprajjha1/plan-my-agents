"""Migrate `data/capability_labels.json` rows into the Postgres
`capability_labels` table.

Why this exists
---------------
Before the 2026-05-19 resolver fix in
``planmyagents_api/planner/capability_label_recorder.py``, the
`/goal` route silently persisted newly-coined labels to
`<repo>/data/capability_labels.json` whenever
``PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH`` was unset — even when
the rest of the evidence pipeline (demand events, gap events, etc.)
was correctly routed to Postgres. The audit found 15 labels orphaned
this way; the most-used (``identity_verification``, 88 hits) had
weeks of demand evidence locked outside the dashboard.

This script imports those rows into Postgres preserving
``usage_count``, ``coined_at``, ``last_used_at``, and
``coined_from_goal_hash`` exactly as the recorder saw them. It uses
``ON CONFLICT (id) DO NOTHING`` so re-running it after the resolver
fix lands (when new labels start flowing into Postgres directly)
won't double-count anything.

Usage:
    python scripts/migrate_orphan_capability_labels.py
        # or:
    python scripts/migrate_orphan_capability_labels.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api._config import discovery_store_url  # noqa: E402


DEFAULT_JSON_PATH = ROOT / "data" / "capability_labels.json"


def _resolve_dsn(cli_dsn: str | None) -> str:
    if cli_dsn:
        return cli_dsn
    dsn = discovery_store_url()
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise SystemExit(
            f"discovery_store_url() returned {dsn!r} which is not a "
            "Postgres DSN; pass --dsn explicitly if you want to migrate "
            "into a non-default DB"
        )
    return dsn


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="Override the Postgres DSN.")
    parser.add_argument(
        "--json-path",
        default=str(DEFAULT_JSON_PATH),
        help=f"Source JSON file (default: {DEFAULT_JSON_PATH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without touching the database.",
    )
    args = parser.parse_args()

    source_path = Path(args.json_path)
    if not source_path.exists():
        print(f"[migrate-labels] source file not found: {source_path}")
        return 0
    try:
        labels = json.loads(source_path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"failed to parse {source_path}: {exc}") from exc
    if not isinstance(labels, list):
        raise SystemExit(
            f"{source_path}: expected a JSON array, got {type(labels).__name__}"
        )

    print(f"[migrate-labels] source file: {source_path}")
    print(f"[migrate-labels] candidate rows: {len(labels)}")
    if args.dry_run:
        for entry in labels:
            print(
                f"  - {entry.get('id'):<40s} usage={entry.get('usage_count'):>3} "
                f"last_used={entry.get('last_used_at')}"
            )
        print("[migrate-labels] dry-run; no DB writes performed")
        return 0

    dsn = _resolve_dsn(args.dsn)
    print(f"[migrate-labels] target dsn: {dsn}")

    import psycopg

    inserted = 0
    skipped = 0
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for entry in labels:
                cap_id = str(entry.get("id") or "").strip().lower()
                if not cap_id:
                    continue
                cur.execute(
                    """
                    INSERT INTO capability_labels (
                        id,
                        description,
                        coined_at,
                        coined_from_goal_hash,
                        usage_count,
                        last_used_at
                    ) VALUES (
                        %(id)s,
                        %(description)s,
                        COALESCE(%(coined_at)s::timestamptz, now()),
                        %(coined_from_goal_hash)s,
                        %(usage_count)s,
                        COALESCE(%(last_used_at)s::timestamptz, now())
                    )
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """,
                    {
                        "id": cap_id,
                        "description": str(entry.get("description") or "").strip(),
                        "coined_at": entry.get("coined_at") or None,
                        "coined_from_goal_hash": str(
                            entry.get("coined_from_goal_hash") or ""
                        ).strip(),
                        "usage_count": int(entry.get("usage_count") or 1),
                        "last_used_at": entry.get("last_used_at") or None,
                    },
                )
                if cur.fetchone():
                    inserted += 1
                else:
                    skipped += 1
        conn.commit()

    print(
        json.dumps(
            {"inserted": inserted, "skipped_already_present": skipped}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
