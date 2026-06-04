"""Persistent post-discovery outcome event store.

A *discovery gap event* is recorded after a ``/goal`` request finishes its
live-discovery + judge pass and we know how many routable candidates we
actually surfaced for each missing capability. Unlike
:mod:`planmyagents_api.discovery.demand_store` — which records the user's
*intent* (the missing capabilities derived from the planner) before
discovery runs — this store captures the *outcome*: of N scouts
dispatched and M cached candidates evaluated by the judge, how many
ended up surfaceable. ``judge_accepted == 0`` is the brutally-honest
"the world has not built this yet" signal that we want a leaderboard
tile to surface.

Why a separate table from ``capability_demand_events``
------------------------------------------------------
1. Different recording moment. Demand is recorded synchronously the
   moment the planner identifies missing capabilities; gap is recorded
   asynchronously after the live discovery + judge pass completes.
   Coupling them would force one to wait on the other or partially
   update a row mid-request, neither of which is desirable.
2. Different aggregation question. Demand answers "what do users keep
   asking for?". Gap answers "for the things they ask for, what do
   we keep failing to deliver routable agents for?". Both questions
   are valid; they share a capability dimension but differ on the
   Y axis (request count vs. zero-yield count).
3. Privacy contract. Demand events explicitly truncate goal text to a
   tweet-sized snippet to make the Open MCP Opportunities page safe
   to publish. Gap events follow the same rule but additionally hash
   the normalised goal so we can dedupe repeated requests for the
   same goal-text without storing the raw text twice.

Design choices echo ``demand_store.py``:

* Three backends (``JsonDiscoveryGapsStore`` for tests / local dev,
  ``SqliteDiscoveryGapsStore`` for single-process dev,
  ``PostgresDiscoveryGapsStore`` for production). The factory
  ``discovery_gaps_store_for_path`` switches on the path / DSN shape
  exactly the way the demand factory does.
* Append-only writes. Aggregation happens at read time via
  :func:`summarize_discovery_gaps`. We deliberately do not pre-aggregate
  because the leaderboard surface is small (top 20 capabilities) and
  the cost of grouping a few thousand rows in Python is negligible.
* No exceptions on the read side: a missing JSONL file or unreachable
  Postgres returns an empty list so the leaderboard tile degrades to
  "no gap signal yet" instead of crashing the page.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _truncate_goal(value: str, *, max_chars: int = 280) -> str:
    """Same 280-char cap as ``demand_store._truncate_goal`` so the two
    surfaces show goal samples of comparable length and the tile UI
    doesn't have to handle radically different text shapes."""

    cleaned = " ".join((value or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "\u2026"


def _hash_goal(value: str) -> str:
    """Stable opaque dedupe token for a goal.

    Uses the *normalised* (lower-cased, whitespace-collapsed) goal so
    two goal texts that differ only in capitalisation or whitespace
    (e.g. an extra space, a trailing newline, a copy-paste with
    smart-quote noise) hash to the same value — both are operationally
    the same request. Truncated to 16 chars; collision probability
    remains negligible at our scale (a single bucket per goal text).
    """

    cleaned = " ".join((value or "").lower().split())
    if not cleaned:
        return ""
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class DiscoveryGapEvent:
    """One observation that capability X yielded R routable candidates
    for goal G after live discovery + judge ran.

    A gap event is *not* synonymous with "zero results" — we record
    every observation (including ones that found candidates) so the
    leaderboard can rank capabilities by yield rate, not just absolute
    failures. ``judge_accepted == 0`` is the filter the leaderboard
    applies to ask "what do we keep failing on?"; the raw event log
    keeps both states so we can also answer "how often do scouts find
    something we end up surfacing?".
    """

    capability_id: str
    goal_excerpt: str
    goal_hash: str
    observed_at: str
    scouts_dispatched: int = 0
    scouts_returned_zero: int = 0
    judge_evaluated: int = 0
    judge_accepted: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "goal_excerpt": self.goal_excerpt,
            "goal_hash": self.goal_hash,
            "observed_at": self.observed_at,
            "scouts_dispatched": self.scouts_dispatched,
            "scouts_returned_zero": self.scouts_returned_zero,
            "judge_evaluated": self.judge_evaluated,
            "judge_accepted": self.judge_accepted,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> DiscoveryGapEvent:
        return cls(
            capability_id=str(payload.get("capability_id") or ""),
            goal_excerpt=str(payload.get("goal_excerpt") or ""),
            goal_hash=str(payload.get("goal_hash") or ""),
            observed_at=str(payload.get("observed_at") or _now_iso()),
            scouts_dispatched=int(payload.get("scouts_dispatched") or 0),
            scouts_returned_zero=int(payload.get("scouts_returned_zero") or 0),
            judge_evaluated=int(payload.get("judge_evaluated") or 0),
            judge_accepted=int(payload.get("judge_accepted") or 0),
        )


@dataclass
class DiscoveryGapSummary:
    """Per-capability rollup over the raw event log.

    The leaderboard tile renders one card per ``DiscoveryGapSummary``
    sorted by ``zero_yield_count`` desc, then by ``last_observed_at``
    desc. ``sample_goals`` is the most-recent few distinct goal
    excerpts so a vendor browsing the tile can see "people are asking
    for this in these specific shapes — is one I could build?".
    """

    capability_id: str
    observation_count: int
    distinct_goal_count: int
    zero_yield_count: int
    last_observed_at: str
    sample_goals: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "observation_count": self.observation_count,
            "distinct_goal_count": self.distinct_goal_count,
            "zero_yield_count": self.zero_yield_count,
            "last_observed_at": self.last_observed_at,
            "sample_goals": list(self.sample_goals),
        }


# ---------------------------------------------------------------------------
# Storage backends — same trio as demand_store
# ---------------------------------------------------------------------------


class JsonDiscoveryGapsStore:
    """Append-only JSONL file. The default for local dev and the only
    backend used in unit tests.

    Why JSONL and not JSON: the file is append-only, written one line
    at a time. JSONL means a partially-flushed write at process exit
    leaves the rest of the file readable; a top-level JSON array would
    corrupt on crash.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, events: list[DiscoveryGapEvent]) -> None:
        if not events:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.to_json(), sort_keys=True))
                handle.write("\n")

    def load_all(self) -> list[DiscoveryGapEvent]:
        if not self.path.exists():
            return []
        out: list[DiscoveryGapEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    # A partially-written line at the end of the file
                    # is fine to skip — we never crash on corrupted
                    # event-log data.
                    continue
                if isinstance(payload, dict):
                    out.append(DiscoveryGapEvent.from_json(payload))
        return out

    def summarize(
        self, *, sample_goals_per_capability: int = 3
    ) -> list[DiscoveryGapSummary]:
        return summarize_discovery_gaps(
            self.load_all(),
            sample_goals_per_capability=sample_goals_per_capability,
        )


SQLITE_DISCOVERY_GAPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_gap_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  capability_id TEXT NOT NULL,
  goal_excerpt TEXT NOT NULL,
  goal_hash TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  scouts_dispatched INTEGER NOT NULL DEFAULT 0,
  scouts_returned_zero INTEGER NOT NULL DEFAULT 0,
  judge_evaluated INTEGER NOT NULL DEFAULT 0,
  judge_accepted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_discovery_gap_events_capability
  ON discovery_gap_events(capability_id);
CREATE INDEX IF NOT EXISTS idx_discovery_gap_events_observed_at
  ON discovery_gap_events(observed_at);
"""


class SqliteDiscoveryGapsStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SQLITE_DISCOVERY_GAPS_SCHEMA)

    def append(self, events: list[DiscoveryGapEvent]) -> None:
        if not events:
            return
        rows = [
            (
                event.capability_id,
                event.goal_excerpt,
                event.goal_hash,
                event.observed_at,
                event.scouts_dispatched,
                event.scouts_returned_zero,
                event.judge_evaluated,
                event.judge_accepted,
            )
            for event in events
        ]
        with sqlite3.connect(self.path) as conn:
            conn.executemany(
                "INSERT INTO discovery_gap_events "
                "(capability_id, goal_excerpt, goal_hash, observed_at, "
                "scouts_dispatched, scouts_returned_zero, "
                "judge_evaluated, judge_accepted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def load_all(self) -> list[DiscoveryGapEvent]:
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                "SELECT capability_id, goal_excerpt, goal_hash, observed_at, "
                "scouts_dispatched, scouts_returned_zero, "
                "judge_evaluated, judge_accepted "
                "FROM discovery_gap_events ORDER BY id ASC"
            )
            return [
                DiscoveryGapEvent(
                    capability_id=row[0],
                    goal_excerpt=row[1],
                    goal_hash=row[2],
                    observed_at=row[3],
                    scouts_dispatched=int(row[4]),
                    scouts_returned_zero=int(row[5]),
                    judge_evaluated=int(row[6]),
                    judge_accepted=int(row[7]),
                )
                for row in cursor.fetchall()
            ]

    def summarize(
        self, *, sample_goals_per_capability: int = 3
    ) -> list[DiscoveryGapSummary]:
        return summarize_discovery_gaps(
            self.load_all(),
            sample_goals_per_capability=sample_goals_per_capability,
        )


POSTGRES_DISCOVERY_GAPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_gap_events (
  id BIGSERIAL PRIMARY KEY,
  capability_id TEXT NOT NULL,
  goal_excerpt TEXT NOT NULL,
  goal_hash TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  scouts_dispatched INTEGER NOT NULL DEFAULT 0,
  scouts_returned_zero INTEGER NOT NULL DEFAULT 0,
  judge_evaluated INTEGER NOT NULL DEFAULT 0,
  judge_accepted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_discovery_gap_events_capability
  ON discovery_gap_events(capability_id);
CREATE INDEX IF NOT EXISTS idx_discovery_gap_events_observed_at
  ON discovery_gap_events(observed_at);
"""


class PostgresDiscoveryGapsStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.apply_schema()

    def apply_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(POSTGRES_DISCOVERY_GAPS_SCHEMA)
            conn.commit()

    def _connect(self):
        # Lazy import so the module loads even if psycopg isn't
        # installed in the current shell (e.g. tests running against
        # the JSON or SQLite backend).
        import psycopg

        return psycopg.connect(self.dsn)

    def append(self, events: list[DiscoveryGapEvent]) -> None:
        if not events:
            return
        rows = [
            (
                event.capability_id,
                event.goal_excerpt,
                event.goal_hash,
                event.observed_at,
                event.scouts_dispatched,
                event.scouts_returned_zero,
                event.judge_evaluated,
                event.judge_accepted,
            )
            for event in events
        ]
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO discovery_gap_events "
                    "(capability_id, goal_excerpt, goal_hash, observed_at, "
                    "scouts_dispatched, scouts_returned_zero, "
                    "judge_evaluated, judge_accepted) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
            conn.commit()

    def load_all(self) -> list[DiscoveryGapEvent]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT capability_id, goal_excerpt, goal_hash, observed_at, "
                    "scouts_dispatched, scouts_returned_zero, "
                    "judge_evaluated, judge_accepted "
                    "FROM discovery_gap_events ORDER BY id ASC"
                )
                rows = cursor.fetchall()
        return [
            DiscoveryGapEvent(
                capability_id=row[0],
                goal_excerpt=row[1],
                goal_hash=row[2],
                observed_at=(
                    row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3])
                ),
                scouts_dispatched=int(row[4]),
                scouts_returned_zero=int(row[5]),
                judge_evaluated=int(row[6]),
                judge_accepted=int(row[7]),
            )
            for row in rows
        ]

    def summarize(
        self, *, sample_goals_per_capability: int = 3
    ) -> list[DiscoveryGapSummary]:
        return summarize_discovery_gaps(
            self.load_all(),
            sample_goals_per_capability=sample_goals_per_capability,
        )


def discovery_gaps_store_for_path(path: Path | str):
    """Pick a store implementation from the path/DSN shape.

    Mirrors :func:`demand_store_for_path` exactly so the two storage
    layers move together — if you switch one to a new backend, the
    other should switch too, and a single env var ought to suffice.
    """

    location = str(path)
    if location.startswith(("postgresql://", "postgres://")):
        return PostgresDiscoveryGapsStore(location)
    target = Path(location)
    if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return SqliteDiscoveryGapsStore(target)
    return JsonDiscoveryGapsStore(target)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def summarize_discovery_gaps(
    events: list[DiscoveryGapEvent],
    *,
    sample_goals_per_capability: int = 3,
) -> list[DiscoveryGapSummary]:
    """Group raw events into per-capability summaries.

    Sorting:
    1. ``zero_yield_count`` desc — capabilities that keep failing
       float to the top, which is what the leaderboard tile wants
       to surface.
    2. ``observation_count`` desc — among equally-failing capabilities,
       the more frequently-asked one wins. Demand and gap reinforce
       each other this way: a capability with one zero-yield event
       isn't as urgent as one with fifty.
    3. ``capability_id`` asc — alphabetical tiebreaker for stable
       ordering across runs (debuggable).

    ``sample_goals`` returns the most recent N distinct goal excerpts
    so the public surface shows fresh examples, not a fossilised list
    from week one.
    """

    by_capability: dict[str, list[DiscoveryGapEvent]] = {}
    for event in events:
        capability_id = event.capability_id.strip()
        if not capability_id:
            continue
        by_capability.setdefault(capability_id, []).append(event)

    summaries: list[DiscoveryGapSummary] = []
    for capability_id, capability_events in by_capability.items():
        ordered = sorted(capability_events, key=lambda e: e.observed_at)
        latest = ordered[-1].observed_at
        seen_goal_excerpts: set[str] = set()
        sample_goals: list[str] = []
        for event in reversed(ordered):
            text = event.goal_excerpt.strip()
            if text and text not in seen_goal_excerpts:
                seen_goal_excerpts.add(text)
                sample_goals.append(text)
            if len(sample_goals) >= sample_goals_per_capability:
                break
        distinct_goal_count = len(
            {event.goal_hash for event in capability_events if event.goal_hash}
        )
        zero_yield_count = sum(
            1 for event in capability_events if event.judge_accepted == 0
        )
        summaries.append(
            DiscoveryGapSummary(
                capability_id=capability_id,
                observation_count=len(capability_events),
                distinct_goal_count=distinct_goal_count,
                zero_yield_count=zero_yield_count,
                last_observed_at=latest,
                sample_goals=sample_goals,
            )
        )

    summaries.sort(
        key=lambda s: (
            -s.zero_yield_count,
            -s.observation_count,
            s.capability_id,
        )
    )
    return summaries


def build_discovery_gap_events(
    *,
    goal: str,
    missing_capabilities: list[str],
    judge_accepted_by_capability: dict[str, int],
    judge_evaluated_by_capability: dict[str, int],
    scout_dispatch_by_capability: dict[str, dict[str, int]] | None = None,
) -> list[DiscoveryGapEvent]:
    """Build one ``DiscoveryGapEvent`` per missing capability.

    Args:
        goal: The user's full goal text. Truncated and stored as
            ``goal_excerpt``; never persisted in full.
        missing_capabilities: The capability ids the planner / intent
            mapper said are unmet for this goal. We record one event
            per id even when the capability list is empty for one of
            them — the gap signal is "we tried and got zero", which
            is what the tile needs.
        judge_accepted_by_capability: How many candidates the LLM
            judge accepted as relevant for each capability id. Zero
            here is the brutally-honest "no routable agent" outcome.
        judge_evaluated_by_capability: How many candidates the judge
            actually inspected for each capability — accepted +
            rejected. Useful for distinguishing "judge ran but
            rejected everything" from "no candidates existed to judge
            at all".
        scout_dispatch_by_capability: Optional per-capability dict of
            ``{"dispatched": N, "returned_zero": M}`` from the live
            dispatcher. Defaults to zeros if not provided (e.g. live
            discovery was disabled for this request).
    """

    if not missing_capabilities:
        return []

    excerpt = _truncate_goal(goal)
    goal_hash = _hash_goal(goal)
    now = _now_iso()
    scout_data = scout_dispatch_by_capability or {}

    events: list[DiscoveryGapEvent] = []
    for capability in missing_capabilities:
        capability_id = str(capability or "").strip()
        if not capability_id:
            continue
        scout_block = scout_data.get(capability_id, {})
        events.append(
            DiscoveryGapEvent(
                capability_id=capability_id,
                goal_excerpt=excerpt,
                goal_hash=goal_hash,
                observed_at=now,
                scouts_dispatched=int(scout_block.get("dispatched", 0) or 0),
                scouts_returned_zero=int(scout_block.get("returned_zero", 0) or 0),
                judge_evaluated=int(
                    judge_evaluated_by_capability.get(capability_id, 0) or 0
                ),
                judge_accepted=int(
                    judge_accepted_by_capability.get(capability_id, 0) or 0
                ),
            )
        )
    return events
