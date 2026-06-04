"""Persistent vocabulary store for capability labels coined by the decomposer.

The goal decomposer in :mod:`planmyagents_api.planner.goal_decomposer` is
deliberately free to coin a new ``suggested_capability_id`` whenever
nothing in the catalog hint genuinely matches a sub-task. Without
persistence those coined labels stay scoped to the single request that
emitted them: the next request decomposing the same goal sees the
*original* catalog hint (only labels derived from already-indexed
candidates) and may coin a *different but semantically equivalent*
slug. The result is the same demand surface fragmented across dozens
of one-off labels — useless for the demand and gap leaderboards.

This module fixes that by giving coined labels a persistent home:

1. After decomposition, the post-hoc :mod:`label_reconciler` either
   matches the coined slug to an existing catalog id (preferred — best
   aggregation) **or** writes it to this store as a brand-new label.
2. On every subsequent request, ``_build_default_capability_catalog``
   in :mod:`planmyagents_api.web.planning` UNIONs the persisted-label set
   into the catalog hint passed to the decomposer. Newly-coined
   labels therefore become reusable on the very next request.
3. Each persisted label tracks ``usage_count`` and ``last_used_at`` so
   ops/UI surfaces can see "labels the decomposer actually coined and
   then reused" vs "labels that were coined once and never seen again"
   — the second class is a vocabulary smell worth manual review.

Why a separate table from ``discovery_candidates``
--------------------------------------------------
Discovery candidates carry capability tags as a *side-effect* of
indexing real-world agents. A label can only show up in
:func:`build_capability_catalog`'s output if SOMEONE indexed an agent
that happened to be tagged with it. That coupling is what motivated
the whole decomposer rewrite — we don't want the LLM's vocabulary
capped at "what we happened to crawl".

A separate ``capability_labels`` table breaks that coupling. Labels
can exist *purely from user demand*, with zero matching candidates.
That's exactly the signal the discovery-gap leaderboard wants: "the
vocabulary keeps growing in this direction, but the index hasn't
caught up". The table is intentionally tiny — one row per
distinct label, not per usage event — so listing it is cheap.

Three backends, identical interface:

* :class:`JsonCapabilityLabelStore` — append-style JSONL for tests
  and local dev; UPSERTs by reading-modifying-writing the whole file.
  Acceptable because the file is bounded by the count of distinct
  labels ever coined (hundreds at most).
* :class:`SqliteCapabilityLabelStore` — single-process dev with
  ``ON CONFLICT(id) DO UPDATE`` for atomic UPSERTs.
* :class:`PostgresCapabilityLabelStore` — production. Same UPSERT
  semantics, plus an index on ``last_used_at`` for the inspection
  endpoint to range-scan recent activity.

The factory :func:`capability_label_store_for_path` mirrors
:func:`discovery_gaps_store_for_path` exactly so the same env var
shape (``postgresql://…`` vs ``*.sqlite`` vs anything-else=JSON) picks
the matching backend.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CapabilityLabel:
    """One persisted capability label coined by the decomposer.

    ``description`` is the decomposer's own one-sentence sub-task
    description from the moment the label was coined. We keep it so
    downstream consumers (the reconciler on later requests, the
    inspection endpoint, future analytics) have a stable definition
    of what the label *means* without having to re-derive it from
    candidate metadata that may not exist yet.
    """

    id: str
    description: str
    coined_at: str
    coined_from_goal_hash: str
    usage_count: int
    last_used_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "coined_at": self.coined_at,
            "coined_from_goal_hash": self.coined_from_goal_hash,
            "usage_count": self.usage_count,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> CapabilityLabel:
        return cls(
            id=str(payload.get("id") or "").strip(),
            description=str(payload.get("description") or "").strip(),
            coined_at=str(payload.get("coined_at") or _now_iso()),
            coined_from_goal_hash=str(payload.get("coined_from_goal_hash") or ""),
            usage_count=int(payload.get("usage_count") or 1),
            last_used_at=str(payload.get("last_used_at") or _now_iso()),
        )


# ---------------------------------------------------------------------------
# JSON backend — append-style with UPSERT semantics via read-modify-write.
# ---------------------------------------------------------------------------


class JsonCapabilityLabelStore:
    """Whole-file UPSERT JSON store.

    Unlike the gap and demand stores (which are append-only event
    logs), the label store is a *small set of distinct rows* keyed by
    id. We rewrite the file on every UPSERT because the row count is
    bounded — even a long-running production system is unlikely to
    coin more than a few thousand distinct labels. JSON-array layout
    is therefore cheap to read in full and trivial to UPSERT.

    A future migration to JSONL append-then-compact is possible if the
    label set ever grows large enough to matter; for now JSON-array
    keeps the file human-readable for ops debugging and avoids the
    "two rows for the same id, which one wins?" ambiguity that JSONL
    would force us to handle on read.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def upsert(
        self,
        *,
        id: str,
        description: str,
        coined_from_goal_hash: str = "",
    ) -> CapabilityLabel:
        """Insert ``id`` if absent; on conflict bump usage_count and
        last_used_at. Returns the resulting row.

        ``description`` and ``coined_from_goal_hash`` are recorded
        only on insert — once a label has been coined the *first*
        request to coin it owns its definition, so subsequent reuses
        don't overwrite that history. Operators editing the label set
        manually (rare but supported) get a stable record of how each
        label entered the vocabulary.
        """

        normalised_id = (id or "").strip().lower()
        if not normalised_id:
            raise ValueError("CapabilityLabel id must be non-empty.")
        rows = self._load_dict()
        existing = rows.get(normalised_id)
        now = _now_iso()
        if existing is None:
            row = CapabilityLabel(
                id=normalised_id,
                description=(description or "").strip(),
                coined_at=now,
                coined_from_goal_hash=(coined_from_goal_hash or "").strip(),
                usage_count=1,
                last_used_at=now,
            )
        else:
            row = CapabilityLabel(
                id=existing.id,
                description=existing.description,
                coined_at=existing.coined_at,
                coined_from_goal_hash=existing.coined_from_goal_hash,
                usage_count=existing.usage_count + 1,
                last_used_at=now,
            )
        rows[normalised_id] = row
        self._dump_dict(rows)
        return row

    def get(self, id: str) -> CapabilityLabel | None:
        return self._load_dict().get((id or "").strip().lower())

    def list_all(self) -> list[CapabilityLabel]:
        return list(self._load_dict().values())

    def list_ids(self) -> set[str]:
        """Cheap helper used by the planner catalog builder. Returns
        an empty set on any read failure so a missing/corrupt file
        never blocks ``/goal``."""

        try:
            return set(self._load_dict().keys())
        except Exception:  # noqa: BLE001 — defensive, callers must not crash
            return set()

    def _load_dict(self) -> dict[str, CapabilityLabel]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            # Corrupt or truncated file — treat as empty rather than
            # crashing /goal. The next UPSERT will rewrite cleanly.
            return {}
        if not isinstance(payload, list):
            return {}
        out: dict[str, CapabilityLabel] = {}
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            label = CapabilityLabel.from_json(entry)
            if label.id:
                out[label.id] = label
        return out

    def _dump_dict(self, rows: dict[str, CapabilityLabel]) -> None:
        ordered = sorted(rows.values(), key=lambda r: r.id)
        payload = [row.to_json() for row in ordered]
        # Atomic-ish write: dump to tmp, rename. Avoids leaving a
        # partial file on crash mid-write that the next UPSERT would
        # see as corrupt and silently discard.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp.replace(self.path)


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------


SQLITE_CAPABILITY_LABELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_labels (
  id TEXT PRIMARY KEY,
  description TEXT NOT NULL DEFAULT '',
  coined_at TEXT NOT NULL,
  coined_from_goal_hash TEXT NOT NULL DEFAULT '',
  usage_count INTEGER NOT NULL DEFAULT 1,
  last_used_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capability_labels_last_used_at
  ON capability_labels(last_used_at);
"""


class SqliteCapabilityLabelStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SQLITE_CAPABILITY_LABELS_SCHEMA)

    def upsert(
        self,
        *,
        id: str,
        description: str,
        coined_from_goal_hash: str = "",
    ) -> CapabilityLabel:
        normalised_id = (id or "").strip().lower()
        if not normalised_id:
            raise ValueError("CapabilityLabel id must be non-empty.")
        now = _now_iso()
        with sqlite3.connect(self.path) as conn:
            # ON CONFLICT preserves the original definition fields
            # (description, coined_at, coined_from_goal_hash) and only
            # bumps usage_count + last_used_at — same contract as the
            # JSON backend.
            conn.execute(
                """
                INSERT INTO capability_labels
                  (id, description, coined_at, coined_from_goal_hash,
                   usage_count, last_used_at)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(id) DO UPDATE SET
                  usage_count = capability_labels.usage_count + 1,
                  last_used_at = excluded.last_used_at
                """,
                (
                    normalised_id,
                    (description or "").strip(),
                    now,
                    (coined_from_goal_hash or "").strip(),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id, description, coined_at, coined_from_goal_hash, "
                "usage_count, last_used_at FROM capability_labels WHERE id = ?",
                (normalised_id,),
            ).fetchone()
        return _row_to_label(row)

    def get(self, id: str) -> CapabilityLabel | None:
        normalised_id = (id or "").strip().lower()
        if not normalised_id:
            return None
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT id, description, coined_at, coined_from_goal_hash, "
                "usage_count, last_used_at FROM capability_labels WHERE id = ?",
                (normalised_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_label(row)

    def list_all(self) -> list[CapabilityLabel]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT id, description, coined_at, coined_from_goal_hash, "
                "usage_count, last_used_at FROM capability_labels "
                "ORDER BY id ASC"
            ).fetchall()
        return [_row_to_label(row) for row in rows]

    def list_ids(self) -> set[str]:
        try:
            with sqlite3.connect(self.path) as conn:
                rows = conn.execute(
                    "SELECT id FROM capability_labels"
                ).fetchall()
            return {row[0] for row in rows}
        except Exception:  # noqa: BLE001 — defensive
            return set()


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------


POSTGRES_CAPABILITY_LABELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_labels (
  id TEXT PRIMARY KEY,
  description TEXT NOT NULL DEFAULT '',
  coined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  coined_from_goal_hash TEXT NOT NULL DEFAULT '',
  usage_count BIGINT NOT NULL DEFAULT 1,
  last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_capability_labels_last_used_at
  ON capability_labels(last_used_at);
"""


class PostgresCapabilityLabelStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.apply_schema()

    def apply_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(POSTGRES_CAPABILITY_LABELS_SCHEMA)
            conn.commit()

    def _connect(self):
        # Lazy import — same pattern as the gap and demand stores so
        # this module loads without psycopg installed (e.g. in a unit
        # test that only exercises the JSON backend).
        import psycopg

        return psycopg.connect(self.dsn)

    def upsert(
        self,
        *,
        id: str,
        description: str,
        coined_from_goal_hash: str = "",
    ) -> CapabilityLabel:
        normalised_id = (id or "").strip().lower()
        if not normalised_id:
            raise ValueError("CapabilityLabel id must be non-empty.")
        with self._connect() as conn:
            with conn.cursor() as cursor:
                # Same UPSERT contract as the SQLite backend. ``now()``
                # is server-side so concurrent UPSERTs serialize on a
                # single clock and ``last_used_at`` is monotonic per
                # row from the database's perspective.
                cursor.execute(
                    """
                    INSERT INTO capability_labels
                      (id, description, coined_from_goal_hash)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                      usage_count = capability_labels.usage_count + 1,
                      last_used_at = now()
                    RETURNING id, description, coined_at,
                              coined_from_goal_hash, usage_count, last_used_at
                    """,
                    (
                        normalised_id,
                        (description or "").strip(),
                        (coined_from_goal_hash or "").strip(),
                    ),
                )
                row = cursor.fetchone()
            conn.commit()
        return _row_to_label(row)

    def get(self, id: str) -> CapabilityLabel | None:
        normalised_id = (id or "").strip().lower()
        if not normalised_id:
            return None
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, description, coined_at, coined_from_goal_hash, "
                    "usage_count, last_used_at FROM capability_labels "
                    "WHERE id = %s",
                    (normalised_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_label(row)

    def list_all(self) -> list[CapabilityLabel]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, description, coined_at, coined_from_goal_hash, "
                    "usage_count, last_used_at FROM capability_labels "
                    "ORDER BY id ASC"
                )
                rows = cursor.fetchall()
        return [_row_to_label(row) for row in rows]

    def list_ids(self) -> set[str]:
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT id FROM capability_labels")
                    rows = cursor.fetchall()
            return {row[0] for row in rows}
        except Exception:  # noqa: BLE001 — defensive on the planner hot path
            return set()


def _row_to_label(row: Any) -> CapabilityLabel:
    """Coerce a DB-API row (sqlite tuple or psycopg tuple with
    ``datetime`` columns) into a :class:`CapabilityLabel`. Both
    backends return columns in the same order; we just have to
    string-coerce the timestamp columns when Postgres hands back a
    timezone-aware ``datetime`` object."""

    coined_at = row[2]
    last_used_at = row[5]
    return CapabilityLabel(
        id=str(row[0]),
        description=str(row[1] or ""),
        coined_at=coined_at.isoformat() if hasattr(coined_at, "isoformat") else str(coined_at),
        coined_from_goal_hash=str(row[3] or ""),
        usage_count=int(row[4] or 0),
        last_used_at=(
            last_used_at.isoformat()
            if hasattr(last_used_at, "isoformat")
            else str(last_used_at)
        ),
    )


def capability_label_store_for_path(path: Path | str):
    """Pick a backend by URL/path shape — same dispatch as
    :func:`discovery_gaps_store_for_path` so a single env var can
    point both stores at the same backend type.
    """

    location = str(path)
    if location.startswith(("postgresql://", "postgres://")):
        return PostgresCapabilityLabelStore(location)
    target = Path(location)
    if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return SqliteCapabilityLabelStore(target)
    return JsonCapabilityLabelStore(target)
