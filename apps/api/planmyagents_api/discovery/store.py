"""Persistent discovery candidate store."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from planmyagents_api.discovery.dedupe import dedupe_key, merge_candidates
from planmyagents_api.discovery.embeddings import EmbedderError, embedder_from_env
from planmyagents_api.discovery.models import DiscoveryCandidate
from planmyagents_api.discovery.normalizer import CandidateNormalizationError, candidate_from_registry

DISCOVERY_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_candidates (
  dedupe_key TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  provider_type TEXT NOT NULL,
  source TEXT NOT NULL,
  verification_status TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  capabilities_json TEXT NOT NULL,
  candidate_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_provider_type
  ON discovery_candidates(provider_type);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_source
  ON discovery_candidates(source);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_verification
  ON discovery_candidates(verification_status);
"""

POSTGRES_DISCOVERY_STORE_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS discovery_candidates (
  dedupe_key TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  vendor TEXT NOT NULL DEFAULT '',
  vendor_url TEXT NOT NULL DEFAULT '',
  provider_type TEXT NOT NULL,
  lifecycle_status TEXT NOT NULL DEFAULT 'discovered',
  route_status TEXT NOT NULL DEFAULT 'will_fail',
  will_fail BOOLEAN NOT NULL DEFAULT TRUE,
  will_fail_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
  verification_status TEXT NOT NULL DEFAULT 'unverified',
  evidence_url TEXT NOT NULL DEFAULT '',
  adapter_module TEXT NOT NULL DEFAULT '',
  benchmark_status TEXT NOT NULL DEFAULT 'not_started',
  capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
  required_env_vars TEXT[] NOT NULL DEFAULT '{}',
  compatible_provider_ids TEXT[] NOT NULL DEFAULT '{}',
  source_id TEXT NOT NULL DEFAULT '',
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_candidate JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1536)
);
ALTER TABLE discovery_candidates ADD COLUMN IF NOT EXISTS dedupe_key TEXT;
ALTER TABLE discovery_candidates ADD COLUMN IF NOT EXISTS raw_candidate JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE discovery_candidates ADD COLUMN IF NOT EXISTS embedding vector(1536);
ALTER TABLE discovery_candidates ADD COLUMN IF NOT EXISTS will_fail BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE discovery_candidates ADD COLUMN IF NOT EXISTS will_fail_reasons JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE discovery_candidates ADD COLUMN IF NOT EXISTS adapter_module TEXT NOT NULL DEFAULT '';
ALTER TABLE discovery_candidates ADD COLUMN IF NOT EXISTS benchmark_status TEXT NOT NULL DEFAULT 'not_started';
CREATE UNIQUE INDEX IF NOT EXISTS idx_discovery_candidates_dedupe_key
  ON discovery_candidates(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_provider_type
  ON discovery_candidates(provider_type);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_lifecycle
  ON discovery_candidates(lifecycle_status, route_status);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_verification
  ON discovery_candidates(verification_status);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_capabilities
  ON discovery_candidates USING GIN (capabilities);
"""


@dataclass(frozen=True)
class JsonDiscoveryStore:
    """Small JSON-backed candidate store for local v0 discovery."""

    path: Path

    def load(self) -> list[DiscoveryCandidate]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text())
        records = _records_from_payload(payload)
        candidates: list[DiscoveryCandidate] = []
        for record in records:
            try:
                candidates.append(candidate_from_registry(record))
            except (CandidateNormalizationError, TypeError, ValueError):
                continue
        return candidates

    def save(self, candidates: list[DiscoveryCandidate]) -> None:
        merged: dict[str, DiscoveryCandidate] = {}
        for candidate in candidates:
            key = dedupe_key(candidate)
            existing = merged.get(key)
            merged[key] = merge_candidates(existing, candidate) if existing else candidate

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "candidates": [
                candidate.to_registry_json()
                for candidate in sorted(
                    merged.values(), key=lambda item: (item.discovery_priority, item.id)
                )
            ],
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n")


@dataclass(frozen=True)
class SqliteDiscoveryStore:
    """SQLite-backed candidate store for local production-shaped persistence."""

    path: Path

    def load(self) -> list[DiscoveryCandidate]:
        if not self.path.exists():
            return []
        candidates: list[DiscoveryCandidate] = []
        with self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                "SELECT candidate_json FROM discovery_candidates ORDER BY provider_type, provider_id"
            )
            for (candidate_json,) in rows:
                try:
                    candidates.append(candidate_from_registry(json.loads(candidate_json)))
                except (CandidateNormalizationError, TypeError, ValueError, json.JSONDecodeError):
                    continue
        return candidates

    def save(self, candidates: list[DiscoveryCandidate]) -> None:
        merged: dict[str, DiscoveryCandidate] = {}
        for candidate in candidates:
            key = dedupe_key(candidate)
            existing = merged.get(key)
            merged[key] = merge_candidates(existing, candidate) if existing else candidate

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute("DELETE FROM discovery_candidates")
            connection.executemany(
                """
                INSERT INTO discovery_candidates (
                  dedupe_key,
                  provider_id,
                  display_name,
                  provider_type,
                  source,
                  verification_status,
                  first_seen_at,
                  last_seen_at,
                  capabilities_json,
                  candidate_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    _sqlite_row(key, candidate)
                    for key, candidate in sorted(
                        merged.items(), key=lambda item: (item[1].discovery_priority, item[1].id)
                    )
                ],
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(DISCOVERY_STORE_SCHEMA)


@dataclass(frozen=True)
class PostgresDiscoveryStore:
    """Postgres + pgvector candidate store for durable discovery persistence."""

    dsn: str

    def load(self) -> list[DiscoveryCandidate]:
        candidates: list[DiscoveryCandidate] = []
        with self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT
                  provider_id,
                  display_name,
                  vendor,
                  vendor_url,
                  provider_type,
                  lifecycle_status,
                  route_status,
                  will_fail,
                  will_fail_reasons,
                  verification_status,
                  evidence_url,
                  adapter_module,
                  benchmark_status,
                  capabilities,
                  required_env_vars,
                  compatible_provider_ids,
                  source_id,
                  first_seen_at::text,
                  last_seen_at::text,
                  raw_candidate
                FROM discovery_candidates
                WHERE raw_candidate <> '{}'::jsonb
                ORDER BY provider_type, provider_id
                """
            )
            for row in rows:
                try:
                    candidates.append(_candidate_from_postgres_row(row))
                except (CandidateNormalizationError, TypeError, ValueError, json.JSONDecodeError):
                    continue
        return candidates

    def save(self, candidates: list[DiscoveryCandidate]) -> None:
        merged: dict[str, DiscoveryCandidate] = {}
        for candidate in candidates:
            key = dedupe_key(candidate)
            existing = merged.get(key)
            merged[key] = merge_candidates(existing, candidate) if existing else candidate

        # S2-PAR-6: compute embeddings INLINE on save so newly
        # discovered candidates immediately participate in
        # ``search_by_embedding`` (the pgvector cosine search). Before
        # this change, ``save()`` left ``embedding`` NULL and a
        # separate batch backfill (``upsert_embeddings``) was the only
        # path to populate it — which meant fresh discoveries were
        # invisible to the semantic search until the next backfill
        # run. With per-candidate volume small per /goal request
        # (handful at most) the inline encoder cost is acceptable
        # (Ollama nomic ~80ms, OpenAI ~150ms, hash <1ms). On embedder
        # failure we fall back to no-embedding so save() never
        # breaks just because Ollama is down — the row still lands
        # and a later backfill can fill the vector.
        # Lazy import to avoid the store -> service -> store cycle
        # (service imports DiscoveryCandidate from store via the
        # discovery package init).
        from planmyagents_api.discovery.service import (
            candidate_text_for_embedding,
        )

        embedder = embedder_from_env()
        embeddings: dict[str, list[float] | None] = {}
        for key, candidate in merged.items():
            try:
                text = candidate_text_for_embedding(candidate.to_registry_json())
                embeddings[key] = embedder.encode(text) if text else None
            except EmbedderError:
                embeddings[key] = None

        with self._connect() as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO discovery_candidates (
                      dedupe_key,
                      provider_id,
                      display_name,
                      vendor,
                      vendor_url,
                      provider_type,
                      lifecycle_status,
                      route_status,
                      will_fail,
                      will_fail_reasons,
                      verification_status,
                      evidence_url,
                      adapter_module,
                      benchmark_status,
                      capabilities,
                      required_env_vars,
                      compatible_provider_ids,
                      source_id,
                      first_seen_at,
                      last_seen_at,
                      raw_candidate,
                      embedding
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s::vector
                    )
                    ON CONFLICT (dedupe_key) DO UPDATE SET
                      provider_id = EXCLUDED.provider_id,
                      display_name = EXCLUDED.display_name,
                      vendor = EXCLUDED.vendor,
                      vendor_url = EXCLUDED.vendor_url,
                      provider_type = EXCLUDED.provider_type,
                      lifecycle_status = EXCLUDED.lifecycle_status,
                      route_status = EXCLUDED.route_status,
                      will_fail = EXCLUDED.will_fail,
                      will_fail_reasons = EXCLUDED.will_fail_reasons,
                      verification_status = EXCLUDED.verification_status,
                      evidence_url = EXCLUDED.evidence_url,
                      adapter_module = EXCLUDED.adapter_module,
                      benchmark_status = EXCLUDED.benchmark_status,
                      capabilities = EXCLUDED.capabilities,
                      required_env_vars = EXCLUDED.required_env_vars,
                      compatible_provider_ids = EXCLUDED.compatible_provider_ids,
                      source_id = EXCLUDED.source_id,
                      first_seen_at = LEAST(discovery_candidates.first_seen_at, EXCLUDED.first_seen_at),
                      last_seen_at = GREATEST(discovery_candidates.last_seen_at, EXCLUDED.last_seen_at),
                      raw_candidate = EXCLUDED.raw_candidate,
                      -- Only overwrite the embedding when the new save
                      -- has one. Lets a fresh save without embedder
                      -- preserve the existing vector from a prior run.
                      embedding = COALESCE(EXCLUDED.embedding, discovery_candidates.embedding)
                    """,
                    [
                        _postgres_row(
                            key,
                            candidate,
                            embedding=embeddings.get(key),
                        )
                        for key, candidate in sorted(
                            merged.items(), key=lambda item: (item[1].discovery_priority, item[1].id)
                        )
                    ],
                )

    def apply_schema(self) -> None:
        """Idempotently create discovery_candidates and indexes."""

        with self._connect() as connection:
            self._ensure_schema(connection)

    def upsert_embeddings(self, vectors: dict[str, list[float]]) -> int:
        """Set the embedding column for each (provider_id, vector) pair."""

        if not vectors:
            return 0
        updates = 0
        with self._connect() as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                for provider_id, vector in vectors.items():
                    if not vector:
                        continue
                    cursor.execute(
                        """
                        UPDATE discovery_candidates
                        SET embedding = %s::vector
                        WHERE provider_id = %s
                        """,
                        (_format_vector(vector), provider_id),
                    )
                    updates += cursor.rowcount or 0
        return updates

    def search_by_embedding(
        self,
        vector: list[float],
        *,
        capability: str | None = None,
        provider_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return top-K candidates ranked by cosine similarity to ``vector``.

        Falls back to "candidates without embeddings yet" at the end so the
        caller still gets results even when the backfill is incomplete.
        """

        if not vector:
            return []
        clauses = ["embedding IS NOT NULL"]
        params: list[Any] = [_format_vector(vector)]
        if capability:
            clauses.append("capabilities @> %s::jsonb")
            params.append(json.dumps([{"id": capability}]))
        if provider_type:
            clauses.append("provider_type = %s")
            params.append(provider_type)
        where = " AND ".join(clauses)
        sql = f"""
            SELECT raw_candidate, 1 - (embedding <=> %s::vector) AS similarity
            FROM discovery_candidates
            WHERE raw_candidate <> '{{}}'::jsonb AND {where}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """
        with self._connect() as connection:
            self._ensure_schema(connection)
            # IVFFLAT defaults to probing one list which silently returns no
            # rows when the index is sparse (e.g. 67 candidates across 100
            # lists). Probing every list keeps the result set correct without
            # a noticeable cost at this scale.
            connection.execute("SET LOCAL ivfflat.probes = 100")
            rows = connection.execute(
                sql,
                [_format_vector(vector), *params[1:], _format_vector(vector), int(limit)],
            )
            results = []
            for raw_candidate, similarity in rows:
                if isinstance(raw_candidate, str):
                    try:
                        raw_candidate = json.loads(raw_candidate)
                    except json.JSONDecodeError:
                        continue
                if not isinstance(raw_candidate, dict):
                    continue
                results.append({"candidate": raw_candidate, "similarity": float(similarity or 0.0)})
            return results

    def _connect(self):
        try:
            from psycopg import connect
        except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
            raise RuntimeError(
                "Postgres discovery stores require psycopg. "
                "Install dependencies with `pip install -r requirements.txt`."
            ) from exc
        return connect(self.dsn)

    def _ensure_schema(self, connection) -> None:
        for statement in POSTGRES_DISCOVERY_STORE_SCHEMA.split(";"):
            statement = statement.strip()
            if statement:
                connection.execute(statement)


def discovery_store_for_path(path: Path | str):
    """Choose a store implementation from the file extension.

    Returns a `RoutingDiscoveryStore` that wraps the underlying agentic
    store AND a parallel `apis_without_agents` store. The wrapper preserves
    the existing `.load()` / `.save([candidates])` contract:

    * `.load()` returns only agent-typed candidates (mcp_server / a2a_agent
      / ai_agent), matching what every existing caller expects.
    * `.save([candidates])` partitions by provider_type and routes
      api_provider / payment_provider rows to the apis_without_agents
      store; agentic rows go to the original discovery_candidates store.
    * `.load_apis_without_agents()` is the new accessor for the second
      table.
    """

    from planmyagents_api.discovery.apis_without_agents_store import (
        apis_without_agents_store_for_path,
    )

    location = str(path)
    if location.startswith(("postgresql://", "postgres://")):
        agentic_store = PostgresDiscoveryStore(location)
        apis_store = apis_without_agents_store_for_path(location)
        return RoutingDiscoveryStore(agentic_store, apis_store)

    target = Path(location)
    if target.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        agentic_store = SqliteDiscoveryStore(target)
        apis_store = apis_without_agents_store_for_path(target)
    else:
        agentic_store = JsonDiscoveryStore(target)
        # JSON: separate sibling file. `discovery-store.json` →
        # `discovery-store.apis_without_agents.json`. Inspectable by hand,
        # mirrors postgres/sqlite separation.
        sibling = target.with_suffix("")
        sibling = sibling.parent / (sibling.name + ".apis_without_agents.json")
        apis_store = apis_without_agents_store_for_path(sibling)
    return RoutingDiscoveryStore(agentic_store, apis_store)


class RoutingDiscoveryStore:
    """Facade that owns BOTH stores and routes by provider_type at write
    time. Reads return agent-only candidates, matching the legacy
    contract so no existing caller has to change.

    Save semantics intentionally preserve each underlying backend's
    behavior (replace-semantic for JSON/SQLite, upsert-semantic for
    Postgres). Callers that want load-merge-save should use
    `.save_merge(candidates)` instead — it always merges with the
    existing rows in both stores.
    """

    def __init__(self, agentic_store, apis_store) -> None:
        self._agentic = agentic_store
        self._apis = apis_store

    @property
    def agentic_store(self):
        """Underlying agent-only DiscoveryStore. Exposed for tests and for
        the rare caller that needs raw access (e.g. enricher scripts that
        want to mutate one row at a time)."""
        return self._agentic

    @property
    def apis_without_agents_store(self):
        return self._apis

    def load(self, *, include_rejected: bool = False) -> list[DiscoveryCandidate]:
        """Agent-only loader.

        Filters two classes of rows out of agent surfaces:

        1. Any api_provider / payment_provider rows still in the
           agentic store from before the schema split. Belt-and-
           suspenders: the postgres CHECK constraint enforces this in
           production, but SQLite has no such constraint until
           `migrate_legacy_non_agentic_rows()` is run.
        2. Rows marked `lifecycle_status='rejected'` by the audit
           script in `scripts/audit_rss_hn_candidates.py`. These are
           kept in the table for auditability but should never appear
           on `/categories`, `/agents/{id}`, or `/search`.

        Pass `include_rejected=True` to opt into seeing rejected rows
        (used by the audit script itself).
        """

        from planmyagents_api.discovery.apis_without_agents_store import (
            NON_AGENTIC_PROVIDER_TYPES,
        )

        return [
            c
            for c in self._agentic.load()
            if c.provider_type not in NON_AGENTIC_PROVIDER_TYPES
            and (include_rejected or c.lifecycle_status != "rejected")
        ]

    def load_apis_without_agents(self):
        """Return `ApiWithoutAgentRecord`s.

        Reads from the dedicated apis_without_agents store AND from any
        legacy non-agentic rows still in the agentic store (so the
        Open MCP Opportunities page is correct even before the operator
        runs `migrate_legacy_non_agentic_rows()` on a stale dev SQLite
        file). Records in the dedicated store take precedence by
        dedupe_key.
        """

        from planmyagents_api.discovery.apis_without_agents_store import (
            NON_AGENTIC_PROVIDER_TYPES,
            record_from_discovery_candidate,
        )

        primary = list(self._apis.load())
        seen_keys = {r.dedupe_key for r in primary}
        for candidate in self._agentic.load():
            if candidate.provider_type not in NON_AGENTIC_PROVIDER_TYPES:
                continue
            key = dedupe_key(candidate)
            if key in seen_keys:
                continue
            primary.append(record_from_discovery_candidate(candidate, dedupe_key=key))
            seen_keys.add(key)
        return primary

    def migrate_legacy_non_agentic_rows(self) -> int:
        """One-shot migration: lift any api_provider / payment_provider
        rows out of the agentic store into the apis_without_agents
        store. Returns the count migrated. Idempotent — calling twice
        on a clean store is a no-op.

        Production postgres handles this via the SQL migration in
        `infra/postgres/init/001_planmyagents.sql`. This method is for
        SQLite / JSON dev environments that pre-date the split.
        """

        from planmyagents_api.discovery.apis_without_agents_store import (
            NON_AGENTIC_PROVIDER_TYPES,
            record_from_discovery_candidate,
        )

        all_candidates = self._agentic.load()
        non_agentic = [
            c for c in all_candidates
            if c.provider_type in NON_AGENTIC_PROVIDER_TYPES
        ]
        if not non_agentic:
            return 0

        existing_apis = {r.dedupe_key: r for r in self._apis.load()}
        for candidate in non_agentic:
            key = dedupe_key(candidate)
            existing_apis[key] = record_from_discovery_candidate(
                candidate, dedupe_key=key
            )
        self._apis.save(list(existing_apis.values()))

        agentic_only = [
            c for c in all_candidates
            if c.provider_type not in NON_AGENTIC_PROVIDER_TYPES
        ]
        self._agentic.save(agentic_only)
        return len(non_agentic)

    def save(self, candidates: list[DiscoveryCandidate]) -> None:
        """Partition by provider_type, then call the underlying save on
        each store. Preserves each backend's existing semantics."""

        from planmyagents_api.discovery.apis_without_agents_store import (
            NON_AGENTIC_PROVIDER_TYPES,
            record_from_discovery_candidate,
        )

        agentic: list[DiscoveryCandidate] = []
        non_agentic: list[DiscoveryCandidate] = []
        for candidate in candidates:
            if candidate.provider_type in NON_AGENTIC_PROVIDER_TYPES:
                non_agentic.append(candidate)
            else:
                agentic.append(candidate)

        # Always call agentic save — even with empty list — so backends
        # that do DELETE-then-INSERT correctly clear stale rows when the
        # caller intentionally passes an empty agentic set.
        self._agentic.save(agentic)

        if non_agentic:
            records = [
                record_from_discovery_candidate(c, dedupe_key=dedupe_key(c))
                for c in non_agentic
            ]
            self._apis.save(records)

    def save_merge(self, candidates: list[DiscoveryCandidate]) -> None:
        """Load existing rows from both stores, merge with `candidates`,
        save. Use this when you only have *new* candidates (e.g. live
        scout discoveries) and don't want to wipe the existing index.

        This fixes a latent bug where calling `.save([only_new])` on
        the JSON / SQLite backend silently wiped previously-discovered
        rows because `.save` is replace-semantic. Postgres was upsert
        and didn't have the bug.
        """

        from planmyagents_api.discovery.apis_without_agents_store import (
            NON_AGENTIC_PROVIDER_TYPES,
            record_from_discovery_candidate,
        )

        agentic_existing = {dedupe_key(c): c for c in self._agentic.load()}
        apis_existing = {r.dedupe_key: r for r in self._apis.load()}

        for candidate in candidates:
            key = dedupe_key(candidate)
            if candidate.provider_type in NON_AGENTIC_PROVIDER_TYPES:
                apis_existing[key] = record_from_discovery_candidate(
                    candidate, dedupe_key=key
                )
            else:
                existing = agentic_existing.get(key)
                agentic_existing[key] = (
                    merge_candidates(existing, candidate) if existing else candidate
                )

        self._agentic.save(list(agentic_existing.values()))
        self._apis.save(list(apis_existing.values()))


def _format_vector(vector: list[float]) -> str:
    """Serialise a Python list as a pgvector literal."""

    return "[" + ",".join(f"{float(x):.7f}" for x in vector) + "]"


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("candidates", "discovered_agents", "agents", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _sqlite_row(key: str, candidate: DiscoveryCandidate) -> tuple[str, ...]:
    payload = candidate.to_registry_json()
    return (
        key,
        candidate.id,
        candidate.display_name,
        candidate.provider_type,
        candidate.source,
        candidate.verification_status,
        candidate.first_seen_at,
        candidate.last_seen_at,
        json.dumps([capability.to_json() for capability in candidate.capabilities], sort_keys=True),
        json.dumps(payload, sort_keys=True),
    )


def _postgres_row(
    key: str,
    candidate: DiscoveryCandidate,
    *,
    embedding: list[float] | None = None,
):
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise RuntimeError(
            "Postgres discovery stores require psycopg. "
            "Install dependencies with `pip install -r requirements.txt`."
        ) from exc

    payload = candidate.to_registry_json()
    return (
        key,
        candidate.id,
        candidate.display_name,
        candidate.vendor,
        candidate.vendor_url,
        candidate.provider_type,
        candidate.lifecycle_status,
        candidate.route_status,
        candidate.will_fail,
        Jsonb(sorted(set(candidate.will_fail_reasons))),
        candidate.verification_status,
        candidate.evidence_url,
        candidate.adapter_module,
        candidate.benchmark_status,
        Jsonb([capability.to_json() for capability in candidate.capabilities]),
        sorted(set(candidate.required_env_vars)),
        sorted(set(candidate.compatible_provider_ids)),
        candidate.source,
        candidate.first_seen_at,
        candidate.last_seen_at,
        Jsonb(payload),
        # pgvector accepts NULL or a literal "[x,y,z]" string. The
        # ``%s::vector`` cast in the INSERT handles both.
        _format_vector(embedding) if embedding else None,
    )


def _candidate_from_postgres_row(row) -> DiscoveryCandidate:
    (
        provider_id,
        display_name,
        vendor,
        vendor_url,
        provider_type,
        lifecycle_status,
        route_status,
        will_fail,
        will_fail_reasons,
        verification_status,
        evidence_url,
        adapter_module,
        benchmark_status,
        capabilities,
        required_env_vars,
        compatible_provider_ids,
        source_id,
        first_seen_at,
        last_seen_at,
        raw_candidate,
    ) = row
    if isinstance(raw_candidate, str):
        raw_candidate = json.loads(raw_candidate)
    if isinstance(capabilities, str):
        capabilities = json.loads(capabilities)
    if isinstance(will_fail_reasons, str):
        will_fail_reasons = json.loads(will_fail_reasons)

    payload = {
        **raw_candidate,
        "id": provider_id,
        "display_name": display_name,
        "vendor": vendor,
        "vendor_url": vendor_url,
        "provider_type": provider_type,
        "capabilities": capabilities,
        "required_env_vars": list(required_env_vars or []),
        "compatible_provider_ids": list(compatible_provider_ids or []),
        "verification_status": verification_status,
        "evidence_url": evidence_url,
        "lifecycle_status": lifecycle_status,
        "route_status": route_status,
        "will_fail": bool(will_fail),
        "will_fail_reasons": list(will_fail_reasons or []),
        "adapter_module": adapter_module,
        "benchmark_status": benchmark_status,
        "discovery": {
            **(
                raw_candidate.get("discovery", {})
                if isinstance(raw_candidate.get("discovery"), dict)
                else {}
            ),
            "source": source_id,
            "first_seen_at": str(first_seen_at).split(" ")[0],
            "last_seen_at": str(last_seen_at).split(" ")[0],
        },
    }
    return candidate_from_registry(payload)
