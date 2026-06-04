"""Storage for `apis_without_agents` — the second-class table for vendors
that expose an API but no MCP/A2A/AI-agent wrapper exists yet.

Why a separate table from `discovery_candidates`?

* Product positioning. We tell users only MCP/A2A/AI-agent rows count
  as "candidates" they can route to. Mixing OpenAPI specs in the same
  table led to the past bug where total_candidates inflated and the
  refusal payload listed unrunnable rows next to real agents.

* Schema fit. Rows like `will_fail=true` / `benchmark_status=not_started`
  / `adapter_module=''` are permanent for an unwrapped API and just add
  noise to every row. The slim shape here drops them.

* Lifecycle clarity. When somebody finally ships an MCP for one of these
  APIs, we want to know. The `superseded_by_provider_id` column captures
  the relationship without complicating the agentic table.

Three backends (mirrors discovery_store + demand_store):
* `JsonApisWithoutAgentsStore` — atomic JSON file for local dev / tests.
* `SqliteApisWithoutAgentsStore` — single-process dev.
* `PostgresApisWithoutAgentsStore` — production.

Conversion from the existing `DiscoveryCandidate` model is via
`record_from_discovery_candidate(candidate)` so existing source adapters
(ApisGuruSource, etc.) don't need to change in this pass — the storage
facade routes based on `provider_type`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from planmyagents_api.discovery.models import DiscoveryCandidate

# Provider types that belong in this table. Everything else (mcp_server,
# a2a_agent, ai_agent) belongs in discovery_candidates.
NON_AGENTIC_PROVIDER_TYPES = frozenset({"api_provider", "payment_provider"})


@dataclass(frozen=True)
class ApiWithoutAgentRecord:
    """Slim record for a vendor with an API but no agent wrapper.

    Fields that exist in DiscoveryCandidate but NOT here, with reasons:

    * will_fail / will_fail_reasons — would always be True / API_FALLBACK
    * verification_status — not applicable; we never benchmark these
    * lifecycle_status / route_status — not routable, period
    * benchmark_status — never benchmarked
    * adapter_module — there's no adapter to point at
    * required_env_vars / compatible_provider_ids — agent-routing concepts
    * embedding — we don't rank these by semantic similarity
    """

    dedupe_key: str
    provider_id: str
    display_name: str
    vendor: str
    vendor_url: str
    provider_type: str
    openapi_url: str
    capabilities: tuple[str, ...]
    source_id: str
    first_seen_at: str
    last_seen_at: str
    raw_record: dict[str, Any] = field(default_factory=dict)
    superseded_by_provider_id: str = ""

    def __post_init__(self) -> None:
        if self.provider_type not in NON_AGENTIC_PROVIDER_TYPES:
            raise ValueError(
                f"ApiWithoutAgentRecord rejects provider_type={self.provider_type!r}; "
                f"only {sorted(NON_AGENTIC_PROVIDER_TYPES)} belong in this table."
            )
        if not self.provider_id.strip():
            raise ValueError("provider_id is required")
        if not self.dedupe_key.strip():
            raise ValueError("dedupe_key is required")

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = list(self.capabilities)
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> ApiWithoutAgentRecord:
        capabilities = payload.get("capabilities") or []
        if not isinstance(capabilities, list):
            capabilities = []
        return cls(
            dedupe_key=str(payload.get("dedupe_key") or ""),
            provider_id=str(payload.get("provider_id") or ""),
            display_name=str(payload.get("display_name") or ""),
            vendor=str(payload.get("vendor") or ""),
            vendor_url=str(payload.get("vendor_url") or ""),
            provider_type=str(payload.get("provider_type") or "api_provider"),
            openapi_url=str(payload.get("openapi_url") or ""),
            capabilities=tuple(str(c) for c in capabilities),
            source_id=str(payload.get("source_id") or ""),
            first_seen_at=str(payload.get("first_seen_at") or date.today().isoformat()),
            last_seen_at=str(payload.get("last_seen_at") or date.today().isoformat()),
            raw_record=dict(payload.get("raw_record") or {}),
            superseded_by_provider_id=str(payload.get("superseded_by_provider_id") or ""),
        )


def record_from_discovery_candidate(
    candidate: DiscoveryCandidate, *, dedupe_key: str
) -> ApiWithoutAgentRecord:
    """Convert a non-agentic DiscoveryCandidate into the slim record.

    Called by the storage facade when it sees an api_provider /
    payment_provider candidate during save. Existing sources (ApisGuruSource
    etc.) don't need to change — they keep emitting DiscoveryCandidate; the
    facade does the routing.
    """

    if candidate.provider_type not in NON_AGENTIC_PROVIDER_TYPES:
        raise ValueError(
            f"record_from_discovery_candidate refuses provider_type={candidate.provider_type!r} "
            f"— route agentic candidates to discovery_candidates instead."
        )
    capability_ids = tuple(c.id for c in candidate.capabilities if c.id)
    return ApiWithoutAgentRecord(
        dedupe_key=dedupe_key,
        provider_id=candidate.id,
        display_name=candidate.display_name,
        vendor=candidate.vendor,
        vendor_url=candidate.vendor_url,
        provider_type=candidate.provider_type,
        openapi_url=candidate.evidence_url or candidate.openapi_url or "",
        capabilities=capability_ids,
        source_id=candidate.source,
        first_seen_at=candidate.first_seen_at,
        last_seen_at=candidate.last_seen_at,
        raw_record=candidate.to_registry_json(),
    )


# ---------------------------------------------------------------------------
# Storage backends
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JsonApisWithoutAgentsStore:
    """JSON file backend. Local dev / tests."""

    path: Path

    def load(self) -> list[ApiWithoutAgentRecord]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            try:
                payload = json.load(handle)
            except json.JSONDecodeError:
                return []
        if not isinstance(payload, list):
            return []
        out: list[ApiWithoutAgentRecord] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            try:
                out.append(ApiWithoutAgentRecord.from_json(entry))
            except (TypeError, ValueError):
                continue
        return out

    def save(self, records: list[ApiWithoutAgentRecord]) -> None:
        deduped = _dedupe_records(records)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump([r.to_json() for r in deduped], handle, indent=2, sort_keys=True)


SQLITE_APIS_WITHOUT_AGENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS apis_without_agents (
  dedupe_key TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  vendor TEXT NOT NULL DEFAULT '',
  vendor_url TEXT NOT NULL DEFAULT '',
  provider_type TEXT NOT NULL,
  openapi_url TEXT NOT NULL DEFAULT '',
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  source_id TEXT NOT NULL DEFAULT '',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  raw_record_json TEXT NOT NULL DEFAULT '{}',
  superseded_by_provider_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_apis_without_agents_provider_type
  ON apis_without_agents(provider_type);
"""


@dataclass(frozen=True)
class SqliteApisWithoutAgentsStore:
    path: Path

    def load(self) -> list[ApiWithoutAgentRecord]:
        if not self.path.exists():
            return []
        out: list[ApiWithoutAgentRecord] = []
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT dedupe_key, provider_id, display_name, vendor, vendor_url, "
                "provider_type, openapi_url, capabilities_json, source_id, "
                "first_seen_at, last_seen_at, raw_record_json, superseded_by_provider_id "
                "FROM apis_without_agents ORDER BY provider_type, provider_id"
            )
            for row in rows.fetchall():
                try:
                    capabilities = json.loads(row[7] or "[]")
                except json.JSONDecodeError:
                    capabilities = []
                try:
                    raw_record = json.loads(row[11] or "{}")
                except json.JSONDecodeError:
                    raw_record = {}
                out.append(
                    ApiWithoutAgentRecord(
                        dedupe_key=row[0],
                        provider_id=row[1],
                        display_name=row[2],
                        vendor=row[3] or "",
                        vendor_url=row[4] or "",
                        provider_type=row[5],
                        openapi_url=row[6] or "",
                        capabilities=tuple(str(c) for c in capabilities),
                        source_id=row[8] or "",
                        first_seen_at=row[9],
                        last_seen_at=row[10],
                        raw_record=raw_record if isinstance(raw_record, dict) else {},
                        superseded_by_provider_id=row[12] or "",
                    )
                )
        return out

    def save(self, records: list[ApiWithoutAgentRecord]) -> None:
        deduped = _dedupe_records(records)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("DELETE FROM apis_without_agents")
            conn.executemany(
                "INSERT INTO apis_without_agents "
                "(dedupe_key, provider_id, display_name, vendor, vendor_url, "
                "provider_type, openapi_url, capabilities_json, source_id, "
                "first_seen_at, last_seen_at, raw_record_json, superseded_by_provider_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r.dedupe_key,
                        r.provider_id,
                        r.display_name,
                        r.vendor,
                        r.vendor_url,
                        r.provider_type,
                        r.openapi_url,
                        json.dumps(list(r.capabilities), sort_keys=True),
                        r.source_id,
                        r.first_seen_at,
                        r.last_seen_at,
                        json.dumps(r.raw_record, sort_keys=True),
                        r.superseded_by_provider_id,
                    )
                    for r in deduped
                ],
            )

    def mark_superseded(self, dedupe_key: str, agent_provider_id: str) -> None:
        """Record that an agent now wraps this API. Best-effort; a missing
        row is a no-op."""

        if not (dedupe_key and agent_provider_id):
            return
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute(
                "UPDATE apis_without_agents SET superseded_by_provider_id = ? "
                "WHERE dedupe_key = ?",
                (agent_provider_id, dedupe_key),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SQLITE_APIS_WITHOUT_AGENTS_SCHEMA)


POSTGRES_APIS_WITHOUT_AGENTS_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS apis_without_agents (
  id BIGSERIAL PRIMARY KEY,
  dedupe_key TEXT NOT NULL,
  provider_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  vendor TEXT NOT NULL DEFAULT '',
  vendor_url TEXT NOT NULL DEFAULT '',
  provider_type TEXT NOT NULL,
  openapi_url TEXT NOT NULL DEFAULT '',
  capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_id TEXT NOT NULL DEFAULT '',
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_record JSONB NOT NULL DEFAULT '{}'::jsonb,
  superseded_by_provider_id TEXT NOT NULL DEFAULT '',
  UNIQUE (dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_apis_without_agents_provider_type
  ON apis_without_agents(provider_type);
CREATE INDEX IF NOT EXISTS idx_apis_without_agents_capabilities
  ON apis_without_agents USING GIN (capabilities);
CREATE INDEX IF NOT EXISTS idx_apis_without_agents_superseded
  ON apis_without_agents(superseded_by_provider_id)
  WHERE superseded_by_provider_id <> '';
"""


@dataclass(frozen=True)
class PostgresApisWithoutAgentsStore:
    """Postgres-backed `apis_without_agents`.

    Self-heals: applies its schema lazily on the first read or write, so a DB
    that predates the schema split (or any fresh Postgres that never had
    `001_planmyagents.sql` run against it) gets the table created
    automatically. `CREATE TABLE IF NOT EXISTS` makes this idempotent. We
    apply lazily (not in `__post_init__`) so constructing a store with a
    fake DSN — as factory tests do — doesn't hit the network.
    """

    dsn: str

    def apply_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(POSTGRES_APIS_WITHOUT_AGENTS_STORE_SCHEMA)
            conn.commit()
        object.__setattr__(self, "_schema_applied", True)

    def _ensure_schema(self) -> None:
        if getattr(self, "_schema_applied", False):
            return
        self.apply_schema()

    def load(self) -> list[ApiWithoutAgentRecord]:
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT dedupe_key, provider_id, display_name, vendor, vendor_url, "
                    "provider_type, openapi_url, capabilities, source_id, "
                    "first_seen_at, last_seen_at, raw_record, superseded_by_provider_id "
                    "FROM apis_without_agents ORDER BY provider_type, provider_id"
                )
                rows = cursor.fetchall()
        return [
            ApiWithoutAgentRecord(
                dedupe_key=row[0],
                provider_id=row[1],
                display_name=row[2],
                vendor=row[3] or "",
                vendor_url=row[4] or "",
                provider_type=row[5],
                openapi_url=row[6] or "",
                capabilities=tuple(str(c) for c in (row[7] or [])),
                source_id=row[8] or "",
                first_seen_at=row[9].isoformat() if hasattr(row[9], "isoformat") else str(row[9]),
                last_seen_at=row[10].isoformat() if hasattr(row[10], "isoformat") else str(row[10]),
                raw_record=row[11] if isinstance(row[11], dict) else {},
                superseded_by_provider_id=row[12] or "",
            )
            for row in rows
        ]

    def save(self, records: list[ApiWithoutAgentRecord]) -> None:
        deduped = _dedupe_records(records)
        if not deduped:
            return
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("psycopg is required for PostgresApisWithoutAgentsStore") from exc
        self._ensure_schema()

        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO apis_without_agents (
                      dedupe_key, provider_id, display_name, vendor, vendor_url,
                      provider_type, openapi_url, capabilities, source_id,
                      first_seen_at, last_seen_at, raw_record
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (dedupe_key) DO UPDATE SET
                      display_name = EXCLUDED.display_name,
                      vendor = EXCLUDED.vendor,
                      vendor_url = EXCLUDED.vendor_url,
                      openapi_url = EXCLUDED.openapi_url,
                      capabilities = EXCLUDED.capabilities,
                      source_id = EXCLUDED.source_id,
                      last_seen_at = EXCLUDED.last_seen_at,
                      raw_record = EXCLUDED.raw_record
                    """,
                    [
                        (
                            r.dedupe_key,
                            r.provider_id,
                            r.display_name,
                            r.vendor,
                            r.vendor_url,
                            r.provider_type,
                            r.openapi_url,
                            Jsonb(list(r.capabilities)),
                            r.source_id,
                            r.first_seen_at,
                            r.last_seen_at,
                            Jsonb(r.raw_record),
                        )
                        for r in deduped
                    ],
                )
            conn.commit()

    def mark_superseded(self, dedupe_key: str, agent_provider_id: str) -> None:
        if not (dedupe_key and agent_provider_id):
            return
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE apis_without_agents SET superseded_by_provider_id = %s "
                    "WHERE dedupe_key = %s",
                    (agent_provider_id, dedupe_key),
                )
            conn.commit()

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn)


def apis_without_agents_store_for_path(path: Path | str):
    """Pick a store implementation. Mirrors discovery_store_for_path."""

    location = str(path)
    if location.startswith(("postgresql://", "postgres://")):
        return PostgresApisWithoutAgentsStore(location)
    target = Path(location)
    if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return SqliteApisWithoutAgentsStore(target)
    return JsonApisWithoutAgentsStore(target)


def _dedupe_records(records: list[ApiWithoutAgentRecord]) -> list[ApiWithoutAgentRecord]:
    """De-duplicate by dedupe_key. Last write wins, preserving the most
    recent `last_seen_at` and `raw_record`."""

    by_key: dict[str, ApiWithoutAgentRecord] = {}
    for record in records:
        existing = by_key.get(record.dedupe_key)
        if existing is None:
            by_key[record.dedupe_key] = record
            continue
        merged = ApiWithoutAgentRecord(
            dedupe_key=record.dedupe_key,
            provider_id=record.provider_id or existing.provider_id,
            display_name=record.display_name or existing.display_name,
            vendor=record.vendor or existing.vendor,
            vendor_url=record.vendor_url or existing.vendor_url,
            provider_type=record.provider_type or existing.provider_type,
            openapi_url=record.openapi_url or existing.openapi_url,
            capabilities=tuple(sorted({*existing.capabilities, *record.capabilities})),
            source_id=record.source_id or existing.source_id,
            first_seen_at=min(existing.first_seen_at, record.first_seen_at),
            last_seen_at=max(existing.last_seen_at, record.last_seen_at),
            raw_record=record.raw_record or existing.raw_record,
            superseded_by_provider_id=(
                record.superseded_by_provider_id or existing.superseded_by_provider_id
            ),
        )
        by_key[record.dedupe_key] = merged
    return sorted(by_key.values(), key=lambda r: (r.provider_type, r.provider_id))
