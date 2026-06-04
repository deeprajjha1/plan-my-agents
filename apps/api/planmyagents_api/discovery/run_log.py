"""Discovery run audit log.

Replaces the previously-unused `discovery_sources` table (which had a
schema in the postgres init script but zero application code reading or
writing it). One row per source invocation:

* What ran (source_id + source_type — e.g. "apis_guru" / "DiscoverySource",
  "hacker_news_agent_watch" / "Scout").
* When it ran and how long it took (started_at, elapsed_ms).
* What it returned (candidates_returned).
* Whether it succeeded (status: 'ok' | 'error' | 'skipped' | 'timeout').
* Why it failed if it didn't (error string).
* What triggered it (trigger: 'batch' | 'scout' | 'manual').

Used by the operator dashboard to see which sources are slow / broken /
starved without trawling logs. Records are append-only — the operator
can rebuild a per-source health view by GROUP BY source_id ORDER BY
started_at DESC LIMIT N.

Three backends mirror discovery_store + apis_without_agents_store +
demand_store:
* `JsonRunEventStore` — JSONL file for local dev / tests.
* `SqliteRunEventStore` — single-process dev.
* `PostgresRunEventStore` — production.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class DiscoveryRunEvent:
    source_id: str
    source_type: str
    status: str = "ok"
    error: str = ""
    candidates_returned: int = 0
    elapsed_ms: int = 0
    query: str = ""
    searched_capabilities: tuple[str, ...] = ()
    trigger: str = "batch"
    started_at: str = field(default_factory=_now)
    completed_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "status": self.status,
            "error": self.error,
            "candidates_returned": self.candidates_returned,
            "elapsed_ms": self.elapsed_ms,
            "query": self.query,
            "searched_capabilities": list(self.searched_capabilities),
            "trigger": self.trigger,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> DiscoveryRunEvent:
        caps = payload.get("searched_capabilities") or []
        if not isinstance(caps, list):
            caps = []
        return cls(
            source_id=str(payload.get("source_id") or ""),
            source_type=str(payload.get("source_type") or ""),
            status=str(payload.get("status") or "ok"),
            error=str(payload.get("error") or ""),
            candidates_returned=int(payload.get("candidates_returned") or 0),
            elapsed_ms=int(payload.get("elapsed_ms") or 0),
            query=str(payload.get("query") or ""),
            searched_capabilities=tuple(str(c) for c in caps),
            trigger=str(payload.get("trigger") or "batch"),
            started_at=str(payload.get("started_at") or _now()),
            completed_at=str(payload.get("completed_at") or ""),
        )


@dataclass(frozen=True)
class JsonRunEventStore:
    """Append-only JSONL file. Local dev / tests."""

    path: Path

    def append(self, events: list[DiscoveryRunEvent]) -> None:
        if not events:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.to_json(), sort_keys=True))
                handle.write("\n")

    def load_all(self) -> list[DiscoveryRunEvent]:
        if not self.path.exists():
            return []
        out: list[DiscoveryRunEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    out.append(DiscoveryRunEvent.from_json(payload))
        return out

    def recent_per_source(self, *, limit_per_source: int = 5) -> dict[str, list[DiscoveryRunEvent]]:
        return _aggregate_recent_per_source(
            self.load_all(), limit_per_source=limit_per_source
        )


SQLITE_RUN_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ok',
  error TEXT NOT NULL DEFAULT '',
  candidates_returned INTEGER NOT NULL DEFAULT 0,
  elapsed_ms INTEGER NOT NULL DEFAULT 0,
  query TEXT NOT NULL DEFAULT '',
  searched_capabilities_json TEXT NOT NULL DEFAULT '[]',
  trigger TEXT NOT NULL DEFAULT 'batch',
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_discovery_run_events_source_started
  ON discovery_run_events(source_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_discovery_run_events_status
  ON discovery_run_events(status);
"""


@dataclass(frozen=True)
class SqliteRunEventStore:
    path: Path

    def append(self, events: list[DiscoveryRunEvent]) -> None:
        if not events:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SQLITE_RUN_EVENTS_SCHEMA)
            conn.executemany(
                "INSERT INTO discovery_run_events "
                "(source_id, source_type, status, error, candidates_returned, "
                "elapsed_ms, query, searched_capabilities_json, trigger, "
                "started_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        e.source_id,
                        e.source_type,
                        e.status,
                        e.error,
                        e.candidates_returned,
                        e.elapsed_ms,
                        e.query,
                        json.dumps(list(e.searched_capabilities), sort_keys=True),
                        e.trigger,
                        e.started_at,
                        e.completed_at,
                    )
                    for e in events
                ],
            )

    def load_all(self) -> list[DiscoveryRunEvent]:
        if not self.path.exists():
            return []
        out: list[DiscoveryRunEvent] = []
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SQLITE_RUN_EVENTS_SCHEMA)
            rows = conn.execute(
                "SELECT source_id, source_type, status, error, candidates_returned, "
                "elapsed_ms, query, searched_capabilities_json, trigger, "
                "started_at, completed_at "
                "FROM discovery_run_events ORDER BY id ASC"
            )
            for row in rows.fetchall():
                try:
                    caps = json.loads(row[7] or "[]")
                except json.JSONDecodeError:
                    caps = []
                out.append(
                    DiscoveryRunEvent(
                        source_id=row[0],
                        source_type=row[1],
                        status=row[2],
                        error=row[3] or "",
                        candidates_returned=int(row[4] or 0),
                        elapsed_ms=int(row[5] or 0),
                        query=row[6] or "",
                        searched_capabilities=tuple(str(c) for c in caps),
                        trigger=row[8] or "batch",
                        started_at=row[9],
                        completed_at=row[10] or "",
                    )
                )
        return out

    def recent_per_source(self, *, limit_per_source: int = 5) -> dict[str, list[DiscoveryRunEvent]]:
        return _aggregate_recent_per_source(
            self.load_all(), limit_per_source=limit_per_source
        )


POSTGRES_RUN_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_run_events (
  id BIGSERIAL PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  query TEXT NOT NULL DEFAULT '',
  searched_capabilities TEXT[] NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'ok',
  error TEXT NOT NULL DEFAULT '',
  candidates_returned INTEGER NOT NULL DEFAULT 0,
  elapsed_ms INTEGER NOT NULL DEFAULT 0,
  trigger TEXT NOT NULL DEFAULT 'batch',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_discovery_run_events_source_started
  ON discovery_run_events(source_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_discovery_run_events_status
  ON discovery_run_events(status, started_at DESC);
"""


@dataclass(frozen=True)
class PostgresRunEventStore:
    """Postgres-backed `discovery_run_events`.

    Self-heals: applies its schema lazily on the first read or write. We do
    it lazily (not in `__post_init__`) so factory tests that construct with
    a fake DSN don't hit the network. `CREATE TABLE IF NOT EXISTS` keeps
    this idempotent across boots.
    """

    dsn: str

    def apply_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(POSTGRES_RUN_EVENTS_SCHEMA)
            conn.commit()
        object.__setattr__(self, "_schema_applied", True)

    def _ensure_schema(self) -> None:
        if getattr(self, "_schema_applied", False):
            return
        self.apply_schema()

    def append(self, events: list[DiscoveryRunEvent]) -> None:
        if not events:
            return
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO discovery_run_events "
                    "(source_id, source_type, status, error, candidates_returned, "
                    "elapsed_ms, query, searched_capabilities, trigger, "
                    "started_at, completed_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            e.source_id,
                            e.source_type,
                            e.status,
                            e.error,
                            e.candidates_returned,
                            e.elapsed_ms,
                            e.query,
                            list(e.searched_capabilities),
                            e.trigger,
                            e.started_at,
                            e.completed_at or None,
                        )
                        for e in events
                    ],
                )
            conn.commit()

    def load_all(self) -> list[DiscoveryRunEvent]:
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT source_id, source_type, status, error, candidates_returned, "
                    "elapsed_ms, query, searched_capabilities, trigger, "
                    "started_at, completed_at "
                    "FROM discovery_run_events ORDER BY id ASC"
                )
                rows = cursor.fetchall()
        out: list[DiscoveryRunEvent] = []
        for row in rows:
            started = row[9].isoformat() if hasattr(row[9], "isoformat") else str(row[9])
            completed = (
                row[10].isoformat() if hasattr(row[10], "isoformat") and row[10] else ""
            )
            out.append(
                DiscoveryRunEvent(
                    source_id=row[0],
                    source_type=row[1],
                    status=row[2],
                    error=row[3] or "",
                    candidates_returned=int(row[4] or 0),
                    elapsed_ms=int(row[5] or 0),
                    query=row[6] or "",
                    searched_capabilities=tuple(str(c) for c in (row[7] or [])),
                    trigger=row[8] or "batch",
                    started_at=started,
                    completed_at=completed,
                )
            )
        return out

    def recent_per_source(self, *, limit_per_source: int = 5) -> dict[str, list[DiscoveryRunEvent]]:
        return _aggregate_recent_per_source(
            self.load_all(), limit_per_source=limit_per_source
        )

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn)


def run_event_store_for_path(path: Path | str):
    """Pick a backend by extension/DSN. Mirrors the other store factories."""

    location = str(path)
    if location.startswith(("postgresql://", "postgres://")):
        return PostgresRunEventStore(location)
    target = Path(location)
    if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return SqliteRunEventStore(target)
    return JsonRunEventStore(target)


def _aggregate_recent_per_source(
    events: list[DiscoveryRunEvent], *, limit_per_source: int
) -> dict[str, list[DiscoveryRunEvent]]:
    """Bucket events by source_id and return the most-recent N per
    source. Sorted descending by started_at."""

    by_source: dict[str, list[DiscoveryRunEvent]] = {}
    for event in events:
        by_source.setdefault(event.source_id, []).append(event)
    for events_list in by_source.values():
        events_list.sort(key=lambda e: e.started_at, reverse=True)
    return {sid: events_list[:limit_per_source] for sid, events_list in by_source.items()}


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class DiscoveryRunLogger:
    """Buffered run-event logger.

    Use it as:

        logger = DiscoveryRunLogger.default()
        with logger.record(source_id="apis_guru", source_type="DiscoverySource",
                           query="...", searched_capabilities=["..."],
                           trigger="batch") as observation:
            results = source.search(...)
            observation.candidates_returned = len(results)

    On exit the wrapper records elapsed_ms, status, and any exception's
    string representation. Failures inside the source body are re-raised
    AFTER the event is recorded so callers see them. Logger failures are
    swallowed (audit log must never break the discovery loop).
    """

    def __init__(self, store, *, enabled: bool = True) -> None:
        self._store = store
        self._enabled = enabled

    @classmethod
    def default(cls, *, store_url: str | None = None, enabled: bool | None = None):
        """Pick a store from `PLANMYAGENTS_RUN_LOG_STORE_PATH` (or fall
        back to `data/discovery_run_events.jsonl`). Set
        `PLANMYAGENTS_RUN_LOG_ENABLED=false` to disable logging
        entirely (used by tests that don't want side-effects)."""

        import os
        from pathlib import Path

        if enabled is None:
            raw = os.environ.get("PLANMYAGENTS_RUN_LOG_ENABLED")
            enabled = True if raw is None else raw.strip().lower() in {
                "1", "true", "yes", "on"
            }
        if not enabled:
            return cls(store=None, enabled=False)

        path = store_url or os.environ.get("PLANMYAGENTS_RUN_LOG_STORE_PATH", "")
        if not path:
            # parents[4] = repo root. See audit fix in demand_recorder.py
            # for the rationale; same off-by-one was present here.
            repo_root = Path(__file__).resolve().parents[4]
            path = str(repo_root / "data" / "discovery_run_events.jsonl")
        return cls(store=run_event_store_for_path(path), enabled=True)

    @contextmanager
    def record(
        self,
        *,
        source_id: str,
        source_type: str,
        query: str = "",
        searched_capabilities: list[str] | tuple[str, ...] = (),
        trigger: str = "batch",
    ):
        """Time a source invocation and record one DiscoveryRunEvent."""

        observation = _RunObservation()
        started_wall = time.monotonic()
        started_at = _now()
        error_text = ""
        status = "ok"
        try:
            yield observation
        except Exception as exc:  # noqa: BLE001 — record then re-raise
            status = "error"
            error_text = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            elapsed_ms = int((time.monotonic() - started_wall) * 1000)
            if observation.skipped:
                status = "skipped"
                error_text = observation.skip_reason or error_text
            elif observation.timeout:
                status = "timeout"
            self._safe_append(
                DiscoveryRunEvent(
                    source_id=source_id,
                    source_type=source_type,
                    status=status,
                    error=error_text,
                    candidates_returned=observation.candidates_returned,
                    elapsed_ms=elapsed_ms,
                    query=query,
                    searched_capabilities=tuple(searched_capabilities),
                    trigger=trigger,
                    started_at=started_at,
                    completed_at=_now(),
                )
            )

    def append_event(self, event: DiscoveryRunEvent) -> None:
        """Bypass the context manager. Useful when the caller already has
        timing info (e.g. ScoutDispatcher computes its own elapsed_ms)."""

        self._safe_append(event)

    def _safe_append(self, event: DiscoveryRunEvent) -> None:
        if not self._enabled or self._store is None:
            return
        try:
            self._store.append([event])
        except Exception as exc:  # noqa: BLE001 — never break discovery on log failure
            _LOG.warning("failed to append discovery run event: %s", exc)


@dataclass
class _RunObservation:
    """Mutable record handed to the `with` block. Callers set
    `candidates_returned` (and optionally `skipped` / `timeout`)
    so the logger captures correct metadata."""

    candidates_returned: int = 0
    skipped: bool = False
    skip_reason: str = ""
    timeout: bool = False
