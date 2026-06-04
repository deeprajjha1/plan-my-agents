"""Persistence for candidate verification history.

`DiscoveryCandidate.verification_status` carries the *current* verification
state. This store records each verification check as a row so the agent detail
page can show an evidence + blocker history without re-fetching upstream.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from planmyagents_api.discovery.verification import VerificationResult

VERIFICATION_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS verification_records (
  id BIGSERIAL PRIMARY KEY,
  provider_id TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence_url TEXT NOT NULL DEFAULT '',
  verified_capabilities TEXT[] NOT NULL DEFAULT '{}',
  blockers JSONB NOT NULL DEFAULT '[]'::jsonb,
  notes JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_verification_records_provider
  ON verification_records(provider_id, created_at DESC);
"""


@dataclass(frozen=True)
class VerificationRecord:
    """A single, persisted verification check."""

    provider_id: str
    status: str
    evidence_url: str
    verified_capabilities: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @classmethod
    def from_result(cls, result: VerificationResult) -> VerificationRecord:
        return cls(
            provider_id=result.provider_id,
            status=result.status,
            evidence_url=result.evidence_url,
            verified_capabilities=list(result.verified_capabilities),
            blockers=list(result.blockers),
            notes=list(result.notes),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "status": self.status,
            "evidence_url": self.evidence_url,
            "verified_capabilities": list(self.verified_capabilities),
            "blockers": list(self.blockers),
            "notes": list(self.notes),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class JsonVerificationStore:
    path: Path

    def append(self, records: list[VerificationRecord]) -> None:
        if not records:
            return
        existing = self._load_payload()
        existing.extend(record.to_json() for record in records)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")

    def history_for(self, provider_id: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self._load_payload()
            if str(row.get("provider_id")) == provider_id
        ]

    def latest_for(self, provider_id: str) -> dict[str, Any] | None:
        history = self.history_for(provider_id)
        if not history:
            return None
        return max(history, key=lambda row: str(row.get("created_at") or ""))

    def _load_payload(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []


@dataclass(frozen=True)
class PostgresVerificationStore:
    dsn: str

    def append(self, records: list[VerificationRecord]) -> None:
        if not records:
            return
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
            raise RuntimeError(
                "Postgres verification stores require psycopg."
            ) from exc
        with self._connect() as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO verification_records (
                      provider_id, status, evidence_url, verified_capabilities,
                      blockers, notes, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            record.provider_id,
                            record.status,
                            record.evidence_url,
                            list(record.verified_capabilities),
                            Jsonb(list(record.blockers)),
                            Jsonb(list(record.notes)),
                            record.created_at,
                        )
                        for record in records
                    ],
                )

    def history_for(self, provider_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT provider_id, status, evidence_url, verified_capabilities,
                       blockers, notes, created_at::text
                FROM verification_records
                WHERE provider_id = %s
                ORDER BY created_at DESC
                """,
                (provider_id,),
            )
            return [_pg_row_to_record(row) for row in rows]

    def latest_for(self, provider_id: str) -> dict[str, Any] | None:
        history = self.history_for(provider_id)
        return history[0] if history else None

    def apply_schema(self) -> None:
        """Idempotently create verification_records and indexes."""

        with self._connect() as connection:
            self._ensure_schema(connection)

    def _connect(self):
        try:
            from psycopg import connect
        except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
            raise RuntimeError(
                "Postgres verification stores require psycopg."
            ) from exc
        return connect(self.dsn)

    def _ensure_schema(self, connection) -> None:
        for statement in VERIFICATION_STORE_SCHEMA.split(";"):
            statement = statement.strip()
            if statement:
                connection.execute(statement)


def verification_store_for_path(path: str | Path):
    location = str(path)
    if location.startswith(("postgresql://", "postgres://")):
        return PostgresVerificationStore(location)
    return JsonVerificationStore(Path(location))


def _pg_row_to_record(row) -> dict[str, Any]:
    provider_id, status, evidence_url, verified_capabilities, blockers, notes, created_at = row
    if isinstance(blockers, str):
        try:
            blockers = json.loads(blockers)
        except json.JSONDecodeError:
            blockers = []
    if isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except json.JSONDecodeError:
            notes = []
    return {
        "provider_id": provider_id,
        "status": status,
        "evidence_url": evidence_url,
        "verified_capabilities": list(verified_capabilities or []),
        "blockers": list(blockers or []),
        "notes": list(notes or []),
        "created_at": created_at,
    }
