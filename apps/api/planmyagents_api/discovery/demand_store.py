"""Persistent capability-demand event store.

Every time a `/goal` request decomposes into one or more `missing_capabilities`,
we record one event per capability so we can answer:

* "Which capabilities are users asking for that no agent supports yet?"
* "Which capabilities are getting more requests month-over-month?"
* "How big is the gap for capability X — N requests against M agent matches?"

Without this, the brutally-honest gap signal (the reason the user picked the
agent-only positioning) is invisible across requests.

Three backends mirror the discovery store:

* `JsonDemandStore` for local prototypes,
* `SqliteDemandStore` for single-process dev,
* `PostgresDemandStore` for production.

The storage shape is intentionally minimal: an append-only log of events,
with aggregation done on read. We keep the goal text truncated and hashed
the requester so we never accidentally leak user PII into a public surface.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _hash_requester(value: str | None) -> str:
    """Hash the requester (e.g., IP, session id) to a short opaque token.
    Never store raw IPs or session ids — this surface might end up public."""

    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _truncate_goal(value: str, *, max_chars: int = 280) -> str:
    """Truncate the goal text so it fits in a tweet-sized snippet on the
    public Open MCP Opportunities page."""

    cleaned = " ".join((value or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "\u2026"


@dataclass(frozen=True)
class DemandEvent:
    """One observation that someone asked for `capability_id`."""

    capability_id: str
    goal_excerpt: str
    requester_hash: str
    requested_at: str
    has_local_match: bool
    has_apis_without_agents: bool
    sub_task_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "goal_excerpt": self.goal_excerpt,
            "requester_hash": self.requester_hash,
            "requested_at": self.requested_at,
            "has_local_match": self.has_local_match,
            "has_apis_without_agents": self.has_apis_without_agents,
            "sub_task_id": self.sub_task_id,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> DemandEvent:
        return cls(
            capability_id=str(payload.get("capability_id") or ""),
            goal_excerpt=str(payload.get("goal_excerpt") or ""),
            requester_hash=str(payload.get("requester_hash") or ""),
            requested_at=str(payload.get("requested_at") or _now()),
            has_local_match=bool(payload.get("has_local_match")),
            has_apis_without_agents=bool(payload.get("has_apis_without_agents")),
            sub_task_id=payload.get("sub_task_id"),
        )


@dataclass
class DemandSummary:
    """Aggregated demand for one capability id."""

    capability_id: str
    request_count: int
    distinct_requester_count: int
    last_requested_at: str
    sample_goals: list[str] = field(default_factory=list)
    has_local_match_count: int = 0
    has_apis_without_agents_count: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "request_count": self.request_count,
            "distinct_requester_count": self.distinct_requester_count,
            "last_requested_at": self.last_requested_at,
            "sample_goals": list(self.sample_goals),
            "has_local_match_count": self.has_local_match_count,
            "has_apis_without_agents_count": self.has_apis_without_agents_count,
        }


# ---------------------------------------------------------------------------
# Storage backends
# ---------------------------------------------------------------------------


class JsonDemandStore:
    """Append-only JSONL file. Good for local dev and tests."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, events: list[DemandEvent]) -> None:
        if not events:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.to_json(), sort_keys=True))
                handle.write("\n")

    def load_all(self) -> list[DemandEvent]:
        if not self.path.exists():
            return []
        out: list[DemandEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    out.append(DemandEvent.from_json(payload))
        return out

    def summarize(self, *, sample_goals_per_capability: int = 3) -> list[DemandSummary]:
        return _summarize(self.load_all(), sample_goals_per_capability=sample_goals_per_capability)


SQLITE_DEMAND_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_demand_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  capability_id TEXT NOT NULL,
  goal_excerpt TEXT NOT NULL,
  requester_hash TEXT NOT NULL DEFAULT '',
  requested_at TEXT NOT NULL,
  has_local_match INTEGER NOT NULL DEFAULT 0,
  has_apis_without_agents INTEGER NOT NULL DEFAULT 0,
  sub_task_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_demand_events_capability
  ON capability_demand_events(capability_id);
CREATE INDEX IF NOT EXISTS idx_demand_events_requested_at
  ON capability_demand_events(requested_at);
"""


class SqliteDemandStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SQLITE_DEMAND_SCHEMA)

    def append(self, events: list[DemandEvent]) -> None:
        if not events:
            return
        rows = [
            (
                event.capability_id,
                event.goal_excerpt,
                event.requester_hash,
                event.requested_at,
                int(event.has_local_match),
                int(event.has_apis_without_agents),
                event.sub_task_id,
            )
            for event in events
        ]
        with sqlite3.connect(self.path) as conn:
            conn.executemany(
                "INSERT INTO capability_demand_events "
                "(capability_id, goal_excerpt, requester_hash, requested_at, "
                "has_local_match, has_apis_without_agents, sub_task_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def load_all(self) -> list[DemandEvent]:
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                "SELECT capability_id, goal_excerpt, requester_hash, requested_at, "
                "has_local_match, has_apis_without_agents, sub_task_id "
                "FROM capability_demand_events ORDER BY id ASC"
            )
            return [
                DemandEvent(
                    capability_id=row[0],
                    goal_excerpt=row[1],
                    requester_hash=row[2],
                    requested_at=row[3],
                    has_local_match=bool(row[4]),
                    has_apis_without_agents=bool(row[5]),
                    sub_task_id=row[6],
                )
                for row in cursor.fetchall()
            ]

    def summarize(self, *, sample_goals_per_capability: int = 3) -> list[DemandSummary]:
        return _summarize(self.load_all(), sample_goals_per_capability=sample_goals_per_capability)


POSTGRES_DEMAND_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_demand_events (
  id BIGSERIAL PRIMARY KEY,
  capability_id TEXT NOT NULL,
  goal_excerpt TEXT NOT NULL,
  requester_hash TEXT NOT NULL DEFAULT '',
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  has_local_match BOOLEAN NOT NULL DEFAULT FALSE,
  has_apis_without_agents BOOLEAN NOT NULL DEFAULT FALSE,
  sub_task_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_demand_events_capability
  ON capability_demand_events(capability_id);
CREATE INDEX IF NOT EXISTS idx_demand_events_requested_at
  ON capability_demand_events(requested_at);
"""


class PostgresDemandStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(POSTGRES_DEMAND_SCHEMA)
            conn.commit()

    def _connect(self):
        # Lazy import so the module loads even if psycopg isn't installed
        # (tests / dev shells running against the JSON or SQLite backend).
        import psycopg

        return psycopg.connect(self.dsn)

    def append(self, events: list[DemandEvent]) -> None:
        if not events:
            return
        rows = [
            (
                event.capability_id,
                event.goal_excerpt,
                event.requester_hash,
                event.requested_at,
                event.has_local_match,
                event.has_apis_without_agents,
                event.sub_task_id,
            )
            for event in events
        ]
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO capability_demand_events "
                    "(capability_id, goal_excerpt, requester_hash, requested_at, "
                    "has_local_match, has_apis_without_agents, sub_task_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
            conn.commit()

    def load_all(self) -> list[DemandEvent]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT capability_id, goal_excerpt, requester_hash, requested_at, "
                    "has_local_match, has_apis_without_agents, sub_task_id "
                    "FROM capability_demand_events ORDER BY id ASC"
                )
                rows = cursor.fetchall()
        return [
            DemandEvent(
                capability_id=row[0],
                goal_excerpt=row[1],
                requester_hash=row[2],
                # psycopg returns datetime; normalize to ISO string.
                requested_at=row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
                has_local_match=bool(row[4]),
                has_apis_without_agents=bool(row[5]),
                sub_task_id=row[6],
            )
            for row in rows
        ]

    def summarize(self, *, sample_goals_per_capability: int = 3) -> list[DemandSummary]:
        return _summarize(self.load_all(), sample_goals_per_capability=sample_goals_per_capability)


def demand_store_for_path(path: Path | str):
    """Pick a store implementation from the path/DSN. Mirrors the discovery
    store factory so the two storage layers age together."""

    location = str(path)
    if location.startswith(("postgresql://", "postgres://")):
        return PostgresDemandStore(location)
    target = Path(location)
    if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return SqliteDemandStore(target)
    return JsonDemandStore(target)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _summarize(
    events: list[DemandEvent], *, sample_goals_per_capability: int = 3
) -> list[DemandSummary]:
    """Group raw events into per-capability summaries. Sorted by total
    request count (desc) — the highest-demand capabilities float to the
    top, which is exactly what the Open MCP Opportunities page should
    rank by."""

    by_capability: dict[str, list[DemandEvent]] = {}
    for event in events:
        capability_id = event.capability_id.strip()
        if not capability_id:
            continue
        by_capability.setdefault(capability_id, []).append(event)

    summaries: list[DemandSummary] = []
    for capability_id, capability_events in by_capability.items():
        ordered = sorted(capability_events, key=lambda e: e.requested_at)
        latest = ordered[-1].requested_at
        # Take the most recent N goal excerpts so the public surface
        # shows fresh examples, not a fossilized list from week 1.
        sample_goals_seen: set[str] = set()
        sample_goals: list[str] = []
        for event in reversed(ordered):
            text = event.goal_excerpt.strip()
            if text and text not in sample_goals_seen:
                sample_goals_seen.add(text)
                sample_goals.append(text)
            if len(sample_goals) >= sample_goals_per_capability:
                break
        distinct_requesters = len(
            {event.requester_hash for event in capability_events if event.requester_hash}
        )
        summaries.append(
            DemandSummary(
                capability_id=capability_id,
                request_count=len(capability_events),
                distinct_requester_count=distinct_requesters,
                last_requested_at=latest,
                sample_goals=sample_goals,
                has_local_match_count=sum(
                    1 for e in capability_events if e.has_local_match
                ),
                has_apis_without_agents_count=sum(
                    1 for e in capability_events if e.has_apis_without_agents
                ),
            )
        )

    summaries.sort(
        key=lambda s: (-s.request_count, -s.distinct_requester_count, s.capability_id)
    )
    return summaries


def build_demand_events_for_request(
    *,
    goal: str,
    missing_capabilities: list[str],
    candidates_by_capability: dict[str, list[dict[str, Any]]],
    apis_without_agents_by_capability: dict[str, list[dict[str, Any]]],
    requester: str | None,
) -> list[DemandEvent]:
    """Turn one /goal refusal into one DemandEvent per missing capability.

    Args:
        goal: The user's full goal text. Truncated and stored as
            `goal_excerpt`; never persisted in full to avoid leaking
            sensitive PII into a public-facing surface.
        missing_capabilities: The capability ids the planner says are
            unmet for this goal.
        candidates_by_capability: Map capability_id -> list of agentic
            candidate payloads that match it. Used to set
            `has_local_match=True` when at least one MCP/A2A/AI-agent is
            already in the index.
        apis_without_agents_by_capability: Map capability_id -> list of
            api_provider candidate payloads (vendors with an OpenAPI
            spec but no MCP wrapper). Used to set
            `has_apis_without_agents=True` so we can later compute
            "capabilities where APIs exist but nobody has shipped an
            MCP" — that's the Open MCP Opportunities ranking.
        requester: Optional requester identifier (IP, session id). Hashed
            to a short opaque token before storage. Pass `None` to
            anonymize entirely.
    """

    if not missing_capabilities:
        return []
    excerpt = _truncate_goal(goal)
    requester_hash = _hash_requester(requester)
    now = _now()
    return [
        DemandEvent(
            capability_id=str(capability),
            goal_excerpt=excerpt,
            requester_hash=requester_hash,
            requested_at=now,
            has_local_match=bool(candidates_by_capability.get(capability)),
            has_apis_without_agents=bool(
                apis_without_agents_by_capability.get(capability)
            ),
        )
        for capability in missing_capabilities
        if str(capability).strip()
    ]
