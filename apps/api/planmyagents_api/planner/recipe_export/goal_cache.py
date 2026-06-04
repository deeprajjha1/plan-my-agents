"""Goal cache for /recipe/export.

The /goal endpoint is stateless — it computes a plan and returns
it. /recipe/export?goal_id=... needs to find that plan later (a
user clicks "Download recipe" minutes or hours after seeing the
plan), so we cache the plan_payload + the goal_text by deterministic
goal_id.

Sprint 4 T1-A scope: JSON-file + in-memory backends only. Postgres
backing lands in Sprint 5 alongside Pro-tier saved-recipes (LLD
§3.10).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 days
CACHE_PATH_ENV = "PLANMYAGENTS_GOAL_CACHE_PATH"


def compute_goal_id(goal_text: str, plan_payload: dict[str, Any]) -> str:
    """Deterministic goal_id from goal_text + canonical plan summary.

    Using a deterministic hash means a refresh of /goal with the
    same input yields the same id — so the user can re-share a
    recipe URL even after a re-plan, as long as the plan is
    structurally identical. We hash a *summary* of the plan rather
    than the whole payload because discovery enrichment is
    background work that should not invalidate the goal_id.
    """
    summary = {
        "goal_text": goal_text.strip(),
        "status": plan_payload.get("status"),
        "sub_tasks": [
            {
                "capability": str(item.get("capability") or "").strip(),
                "description": str(item.get("description") or "").strip(),
            }
            for item in (plan_payload.get("sub_tasks") or [])
            if isinstance(item, dict)
        ],
    }
    digest = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"g_{digest[:16]}"


@dataclass(frozen=True)
class CachedGoal:
    goal_id: str
    goal_text: str
    plan_payload: dict[str, Any]
    created_at: float

    def expired(self, *, now: float, ttl_seconds: int) -> bool:
        return (now - self.created_at) > ttl_seconds


class GoalCache(Protocol):
    def save(self, *, goal_id: str, goal_text: str, plan_payload: dict[str, Any]) -> None:
        ...

    def load(self, goal_id: str) -> CachedGoal | None:
        ...


class InMemoryGoalCache:
    """Process-local cache; default for tests and single-process dev."""

    def __init__(self, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, CachedGoal] = {}

    def save(
        self,
        *,
        goal_id: str,
        goal_text: str,
        plan_payload: dict[str, Any],
    ) -> None:
        with self._lock:
            self._entries[goal_id] = CachedGoal(
                goal_id=goal_id,
                goal_text=goal_text,
                plan_payload=plan_payload,
                created_at=time.time(),
            )

    def load(self, goal_id: str) -> CachedGoal | None:
        with self._lock:
            entry = self._entries.get(goal_id)
            if entry is None:
                return None
            if entry.expired(now=time.time(), ttl_seconds=self._ttl_seconds):
                self._entries.pop(goal_id, None)
                return None
            return entry


class JsonFileGoalCache:
    """Append-only JSON-lines cache; survives API restarts.

    Each line is one CachedGoal serialised as JSON. On load we scan
    backwards so the latest entry wins. Old / expired entries are
    silently skipped on load and the file is rotated when it grows
    past 10 MB (a goal_payload is typically <50 KB, so this fits
    ~200 plans before rotation).
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._path = Path(path)
        self._ttl_seconds = ttl_seconds
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        *,
        goal_id: str,
        goal_text: str,
        plan_payload: dict[str, Any],
    ) -> None:
        line = json.dumps(
            {
                "goal_id": goal_id,
                "goal_text": goal_text,
                "plan_payload": plan_payload,
                "created_at": time.time(),
            },
            separators=(",", ":"),
        )
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._maybe_rotate()

    def load(self, goal_id: str) -> CachedGoal | None:
        if not self._path.exists():
            return None
        now = time.time()
        with self._lock:
            # Read backwards so the latest entry wins on repeated saves.
            with self._path.open("rb") as handle:
                lines = handle.read().splitlines()
        for raw in reversed(lines):
            if not raw.strip():
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if payload.get("goal_id") != goal_id:
                continue
            entry = CachedGoal(
                goal_id=str(payload["goal_id"]),
                goal_text=str(payload.get("goal_text") or ""),
                plan_payload=payload.get("plan_payload") or {},
                created_at=float(payload.get("created_at") or 0.0),
            )
            if entry.expired(now=now, ttl_seconds=self._ttl_seconds):
                return None
            return entry
        return None

    def _maybe_rotate(self) -> None:
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return
        if size <= self._max_bytes:
            return
        # Replace with a compacted file keeping only non-expired entries.
        now = time.time()
        latest_by_id: dict[str, dict[str, Any]] = {}
        with self._path.open("rb") as handle:
            for raw in handle.read().splitlines():
                if not raw.strip():
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                goal_id = str(payload.get("goal_id") or "")
                if not goal_id:
                    continue
                created_at = float(payload.get("created_at") or 0.0)
                if (now - created_at) > self._ttl_seconds:
                    latest_by_id.pop(goal_id, None)
                    continue
                latest_by_id[goal_id] = payload
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for payload in latest_by_id.values():
                handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
        tmp.replace(self._path)


def goal_cache_from_env() -> GoalCache:
    """Build the goal cache backend per env config.

    * ``PLANMYAGENTS_GOAL_CACHE_PATH`` set → JSON-file backend at that path.
    * unset → in-memory backend (single-process, tests + dev).
    """
    path = os.getenv(CACHE_PATH_ENV, "").strip()
    if path:
        return JsonFileGoalCache(path)
    return InMemoryGoalCache()
