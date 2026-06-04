"""Append-only ledger of paid provider calls.

Persistence model
-----------------
Every paid provider call writes one :class:`SpendEvent` to the
ledger. The event captures (UTC date, capability, provider, cost,
goal hash, idempotency key). The ledger is append-only on purpose
— a daily roll-up should always be reconstructable from the raw
events for auditing and dispute resolution.

Two backends are provided, mirroring the demand store pattern:

* :class:`JsonSpendLedger` — append-only JSONL on disk. Used in
  dev, tests, and any deployment where Postgres isn't configured.
* :class:`PostgresSpendLedger` — INSERT-only into the
  ``spend_events`` table. Used in production where the daily cap
  needs to survive process restarts and concurrent goal requests.

The factory :func:`spend_ledger_for_path` picks the backend from
a string the way :func:`demand_store_for_path` does — anything
starting with ``postgresql://`` or ``postgres://`` is treated as
a DSN; everything else is a filesystem path.

Concurrency
-----------
* JSON backend: writes are O(1) appends. The os-level append is
  atomic for short writes, which is enough for our event size.
  Reads are full-file scans, which is acceptable at the volume we
  generate (one event per paid call, ledger rotated daily by ops
  if needed).
* Postgres backend: relies on Postgres' own MVCC. The aggregate
  query inside ``spend_today_usd`` is plain SUM with a date
  predicate, indexed by ``(occurred_date, capability)``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _today_iso() -> str:
    return date.today().isoformat()


def _hash_goal(value: str | None) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SpendEvent:
    """One paid provider call.

    Attributes:
        occurred_at: ISO timestamp the call completed (UTC).
        occurred_date: ISO date (UTC) used for daily roll-ups. We
            denormalise this so the daily-cap query is a cheap
            equality predicate, not a date_trunc cast.
        goal_hash: SHA256(goal_text)[:16] — opaque per-goal id
            used to attribute spend back to the request that
            triggered it without storing the raw goal.
        capability: The capability the call satisfied
            (registry slug).
        provider_id: The adapter id that executed the call.
        cost_usd: Actual cost charged for this call.
        idempotency_key: The workflow-level idempotency key used
            for the underlying provider request, so a replay can
            be detected by tooling.
    """

    goal_hash: str
    capability: str
    provider_id: str
    cost_usd: float
    idempotency_key: str = ""
    occurred_at: str = field(default_factory=_now_iso)
    occurred_date: str = field(default_factory=_today_iso)

    def __post_init__(self) -> None:
        if self.cost_usd < 0:
            raise ValueError("SpendEvent.cost_usd must be non-negative")
        if not self.capability.strip():
            raise ValueError("SpendEvent.capability is required")
        if not self.provider_id.strip():
            raise ValueError("SpendEvent.provider_id is required")

    def to_json(self) -> dict[str, Any]:
        return {
            "occurred_at": self.occurred_at,
            "occurred_date": self.occurred_date,
            "goal_hash": self.goal_hash,
            "capability": self.capability,
            "provider_id": self.provider_id,
            "cost_usd": self.cost_usd,
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> SpendEvent:
        return cls(
            goal_hash=str(payload.get("goal_hash") or ""),
            capability=str(payload.get("capability") or ""),
            provider_id=str(payload.get("provider_id") or ""),
            cost_usd=float(payload.get("cost_usd") or 0.0),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            occurred_at=str(payload.get("occurred_at") or _now_iso()),
            occurred_date=str(payload.get("occurred_date") or _today_iso()),
        )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class JsonSpendLedger:
    """Append-only JSONL ledger.

    One event per line. ``spend_today_usd`` sums the ``cost_usd``
    of events whose ``occurred_date`` matches today's UTC date.
    Reads are unindexed full-file scans — acceptable at the
    volume we generate, and trivially auditable.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, events: list[SpendEvent]) -> None:
        if not events:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.to_json(), sort_keys=True))
                handle.write("\n")

    def load_all(self) -> list[SpendEvent]:
        if not self.path.exists():
            return []
        out: list[SpendEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                with contextlib.suppress(ValueError, TypeError):
                    out.append(SpendEvent.from_json(payload))
        return out

    def spend_today_usd(self, *, today: str | None = None) -> float:
        target = today or _today_iso()
        return sum(
            event.cost_usd
            for event in self.load_all()
            if event.occurred_date == target
        )


class PostgresSpendLedger:
    """Postgres-backed ledger.

    Schema is created lazily on first append (so this class is safe
    to instantiate in tests that point at a sandbox DSN). The table
    is INSERT-only; daily roll-ups are SUM queries.
    """

    SCHEMA_DDL = """
    CREATE TABLE IF NOT EXISTS spend_events (
        id BIGSERIAL PRIMARY KEY,
        occurred_at TIMESTAMPTZ NOT NULL,
        occurred_date DATE NOT NULL,
        goal_hash TEXT NOT NULL DEFAULT '',
        capability TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        cost_usd DOUBLE PRECISION NOT NULL CHECK (cost_usd >= 0),
        idempotency_key TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS spend_events_date_idx
        ON spend_events (occurred_date);
    CREATE INDEX IF NOT EXISTS spend_events_capability_idx
        ON spend_events (capability);
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._schema_initialized = False

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn)

    def _ensure_schema(self, conn) -> None:
        if self._schema_initialized:
            return
        with conn.cursor() as cur:
            cur.execute(self.SCHEMA_DDL)
        conn.commit()
        self._schema_initialized = True

    def append(self, events: list[SpendEvent]) -> None:
        if not events:
            return
        with self._connect() as conn:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO spend_events
                        (occurred_at, occurred_date, goal_hash,
                         capability, provider_id, cost_usd, idempotency_key)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            e.occurred_at,
                            e.occurred_date,
                            e.goal_hash,
                            e.capability,
                            e.provider_id,
                            e.cost_usd,
                            e.idempotency_key,
                        )
                        for e in events
                    ],
                )
            conn.commit()

    def load_all(self) -> list[SpendEvent]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT occurred_at, occurred_date, goal_hash,
                           capability, provider_id, cost_usd, idempotency_key
                      FROM spend_events
                     ORDER BY occurred_at ASC
                    """
                )
                rows = cur.fetchall()
        return [
            SpendEvent(
                goal_hash=row[2] or "",
                capability=row[3] or "",
                provider_id=row[4] or "",
                cost_usd=float(row[5] or 0.0),
                idempotency_key=row[6] or "",
                occurred_at=str(row[0]),
                occurred_date=str(row[1]),
            )
            for row in rows
        ]

    def spend_today_usd(self, *, today: str | None = None) -> float:
        target = today or _today_iso()
        with self._connect() as conn:
            self._ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(SUM(cost_usd), 0)
                      FROM spend_events
                     WHERE occurred_date = %s
                    """,
                    (target,),
                )
                row = cur.fetchone()
        return float(row[0] if row else 0.0)


# ---------------------------------------------------------------------------
# Façade + factory
# ---------------------------------------------------------------------------


# Type alias for the ledger protocol — both backends implement
# ``append``, ``load_all``, ``spend_today_usd``. We don't bother
# with a ``Protocol`` here because the surface is small enough to
# duck-type and the explicit alias would just add ceremony.
SpendLedger = JsonSpendLedger | PostgresSpendLedger


def spend_ledger_for_path(path: Path | str) -> SpendLedger:
    target = str(path)
    if target.startswith(("postgres://", "postgresql://")):
        return PostgresSpendLedger(target)
    return JsonSpendLedger(target)


DEFAULT_SPEND_LEDGER_RELATIVE_PATH = Path("data") / "spend_events.jsonl"


def resolve_spend_ledger_path() -> str:
    """Resolve the spend ledger location.

    Honors ``PLANMYAGENTS_SPEND_LEDGER_PATH`` first; falls back to
    ``data/spend_events.jsonl`` relative to the repo root. The
    repo-root discovery walks up from this file so the script
    works regardless of cwd.
    """

    env_path = os.environ.get("PLANMYAGENTS_SPEND_LEDGER_PATH", "").strip()
    if env_path:
        return env_path
    repo_root = Path(__file__).resolve().parents[4]
    return str(repo_root / DEFAULT_SPEND_LEDGER_RELATIVE_PATH)


def spend_ledger_from_env() -> SpendLedger:
    """Return the spend ledger backed by the env-configured path."""

    return spend_ledger_for_path(resolve_spend_ledger_path())


def total_spend(events: Iterable[SpendEvent]) -> float:
    return sum(e.cost_usd for e in events)
