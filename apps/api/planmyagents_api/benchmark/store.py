"""Persistent benchmark run + ranking store."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from planmyagents_api.benchmark.models import BenchmarkRun
from planmyagents_api.benchmark.rankings import AgentRanking

BENCHMARK_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmark_runs (
  id BIGSERIAL PRIMARY KEY,
  provider_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  test_case_id TEXT NOT NULL,
  difficulty TEXT NOT NULL DEFAULT '',
  score DOUBLE PRECISION NOT NULL,
  succeeded BOOLEAN NOT NULL,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
  output JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT NOT NULL DEFAULT '',
  run_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE benchmark_runs ADD COLUMN IF NOT EXISTS difficulty TEXT NOT NULL DEFAULT '';
ALTER TABLE benchmark_runs ADD COLUMN IF NOT EXISTS error TEXT NOT NULL DEFAULT '';
ALTER TABLE benchmark_runs ADD COLUMN IF NOT EXISTS run_json JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_benchmark_runs_provider_capability
  ON benchmark_runs(provider_id, capability, created_at DESC);
CREATE TABLE IF NOT EXISTS agent_rankings (
  provider_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  sample_size INTEGER NOT NULL DEFAULT 0,
  success_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
  avg_quality_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  p50_latency_ms INTEGER NOT NULL DEFAULT 0,
  p95_latency_ms INTEGER NOT NULL DEFAULT 0,
  avg_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
  composite_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  last_run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  benchmark_status TEXT NOT NULL DEFAULT 'not_started',
  source TEXT NOT NULL DEFAULT 'synthetic',
  rank INTEGER NOT NULL DEFAULT 0,
  weights JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (provider_id, capability)
);
CREATE INDEX IF NOT EXISTS idx_agent_rankings_capability
  ON agent_rankings(capability, composite_score DESC);
"""


@dataclass(frozen=True)
class JsonBenchmarkStore:
    path: Path

    def save(self, runs: list[BenchmarkRun]) -> None:
        payload = self.load_payload()
        payload.setdefault("runs", []).extend([run.to_json() for run in runs])
        self._write(payload)

    def load_json(self) -> list[dict[str, Any]]:
        return self.load_payload().get("runs", [])

    def latest_statuses(self) -> dict[tuple[str, str], str]:
        return _statuses_from_rows(self.load_json())

    def save_rankings(self, rankings: list[AgentRanking]) -> None:
        payload = self.load_payload()
        existing = {
            (str(item.get("provider_id")), str(item.get("capability"))): item
            for item in payload.get("rankings", [])
            if isinstance(item, dict)
        }
        for ranking in rankings:
            existing[(ranking.provider_id, ranking.capability)] = ranking.to_json()
        payload["rankings"] = list(existing.values())
        self._write(payload)

    def load_rankings(self) -> list[dict[str, Any]]:
        return self.load_payload().get("rankings", [])

    def load_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"runs": [], "rankings": []}
        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return {"runs": [], "rankings": []}
        if isinstance(data, list):
            return {"runs": data, "rankings": []}
        if not isinstance(data, dict):
            return {"runs": [], "rankings": []}
        data.setdefault("runs", [])
        data.setdefault("rankings", [])
        return data

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


@dataclass(frozen=True)
class PostgresBenchmarkStore:
    dsn: str

    def save(self, runs: list[BenchmarkRun]) -> None:
        if not runs:
            return
        with self._connect() as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO benchmark_runs (
                      provider_id, capability, test_case_id, difficulty, score,
                      succeeded, latency_ms, cost_usd, output, error, run_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [_postgres_row(run) for run in runs],
                )

    def latest_statuses(self) -> dict[tuple[str, str], str]:
        with self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT provider_id, capability, AVG(score), BOOL_AND(succeeded), COUNT(*)
                FROM benchmark_runs
                GROUP BY provider_id, capability
                """
            )
            statuses = {}
            for provider_id, capability, average_score, all_succeeded, count in rows:
                statuses[(str(provider_id), str(capability))] = _status(
                    average_score=float(average_score or 0.0),
                    all_succeeded=bool(all_succeeded),
                    count=int(count or 0),
                )
            return statuses

    def save_rankings(self, rankings: list[AgentRanking]) -> None:
        if not rankings:
            return
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
            raise RuntimeError("Postgres benchmark stores require psycopg.") from exc
        with self._connect() as connection:
            self._ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO agent_rankings (
                      provider_id, capability, sample_size, success_rate,
                      avg_quality_score, p50_latency_ms, p95_latency_ms,
                      avg_cost_usd, composite_score, last_run_at,
                      benchmark_status, source, rank, weights
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (provider_id, capability) DO UPDATE SET
                      sample_size = EXCLUDED.sample_size,
                      success_rate = EXCLUDED.success_rate,
                      avg_quality_score = EXCLUDED.avg_quality_score,
                      p50_latency_ms = EXCLUDED.p50_latency_ms,
                      p95_latency_ms = EXCLUDED.p95_latency_ms,
                      avg_cost_usd = EXCLUDED.avg_cost_usd,
                      composite_score = EXCLUDED.composite_score,
                      last_run_at = EXCLUDED.last_run_at,
                      benchmark_status = EXCLUDED.benchmark_status,
                      source = EXCLUDED.source,
                      rank = EXCLUDED.rank,
                      weights = EXCLUDED.weights
                    """,
                    [
                        (
                            ranking.provider_id,
                            ranking.capability,
                            ranking.sample_size,
                            ranking.success_rate,
                            ranking.avg_quality_score,
                            ranking.p50_latency_ms,
                            ranking.p95_latency_ms,
                            ranking.avg_cost_usd,
                            ranking.composite_score,
                            ranking.last_run_at,
                            ranking.benchmark_status,
                            ranking.source,
                            ranking.rank,
                            Jsonb(ranking.weights),
                        )
                        for ranking in rankings
                    ],
                )

    def load_rankings(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT
                  provider_id, capability, sample_size, success_rate,
                  avg_quality_score, p50_latency_ms, p95_latency_ms,
                  avg_cost_usd, composite_score, last_run_at::text,
                  benchmark_status, source, rank, weights
                FROM agent_rankings
                ORDER BY capability, composite_score DESC, provider_id
                """
            )
            results = []
            for row in rows:
                weights = row[13]
                if isinstance(weights, str):
                    try:
                        weights = json.loads(weights)
                    except json.JSONDecodeError:
                        weights = {}
                results.append(
                    {
                        "provider_id": row[0],
                        "capability": row[1],
                        "sample_size": int(row[2] or 0),
                        "success_rate": float(row[3] or 0.0),
                        "avg_quality_score": float(row[4] or 0.0),
                        "p50_latency_ms": int(row[5] or 0),
                        "p95_latency_ms": int(row[6] or 0),
                        "avg_cost_usd": float(row[7] or 0.0),
                        "composite_score": float(row[8] or 0.0),
                        "last_run_at": row[9],
                        "benchmark_status": row[10],
                        "source": row[11],
                        "rank": int(row[12] or 0),
                        "weights": weights or {},
                    }
                )
            return results

    def load_json(self) -> list[dict[str, Any]]:
        """Return persisted benchmark runs as plain dicts.

        Mirrors :meth:`JsonBenchmarkStore.load_json` so the FastAPI app can
        treat both backends uniformly. Rows are ordered oldest-first to match
        the JSON store's append semantics; callers slice with `[-N:]` for the
        most recent N runs.
        """

        with self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT
                  provider_id, capability, test_case_id, difficulty,
                  score, succeeded, latency_ms, cost_usd,
                  output, error, run_json, created_at::text
                FROM benchmark_runs
                ORDER BY created_at ASC, id ASC
                """
            )
            results: list[dict[str, Any]] = []
            for row in rows:
                run_json = row[10]
                if isinstance(run_json, str):
                    try:
                        run_json = json.loads(run_json)
                    except json.JSONDecodeError:
                        run_json = {}
                if not isinstance(run_json, dict):
                    run_json = {}
                output = row[8]
                if isinstance(output, str):
                    try:
                        output = json.loads(output)
                    except json.JSONDecodeError:
                        output = {}
                merged = {
                    "provider_id": row[0],
                    "capability": row[1],
                    "test_case_id": row[2],
                    "difficulty": row[3],
                    "score": float(row[4] or 0.0),
                    "succeeded": bool(row[5]),
                    "latency_ms": int(row[6] or 0),
                    "cost_usd": float(row[7] or 0.0),
                    "output": output,
                    "error": row[9] or "",
                    "created_at": row[11],
                }
                # Prefer the structured payload from run_json (it has nested
                # `score` + `response` objects that the UI expects), but fall
                # back to the flattened columns above when run_json is empty.
                if run_json:
                    merged.update(run_json)
                results.append(merged)
            return results

    def apply_schema(self) -> None:
        """Idempotently create benchmark_runs + agent_rankings tables and indexes."""

        with self._connect() as connection:
            self._ensure_schema(connection)

    def _connect(self):
        try:
            from psycopg import connect
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Postgres benchmark stores require psycopg.") from exc
        return connect(self.dsn)

    def _ensure_schema(self, connection) -> None:
        for statement in BENCHMARK_STORE_SCHEMA.split(";"):
            statement = statement.strip()
            if statement:
                connection.execute(statement)


def benchmark_store_for_path(path: str | Path):
    location = str(path)
    if location.startswith(("postgresql://", "postgres://")):
        return PostgresBenchmarkStore(location)
    return JsonBenchmarkStore(Path(location))


def _postgres_row(run: BenchmarkRun):
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Postgres benchmark stores require psycopg.") from exc
    payload = run.to_json()
    return (
        run.provider_id,
        run.capability,
        run.test_case_id,
        run.difficulty,
        run.score.quality_score,
        run.score.succeeded,
        run.response.latency_ms,
        run.response.cost_usd,
        Jsonb(run.response.output or {}),
        run.response.error or "",
        Jsonb(payload),
        run.created_at,
    )


def _statuses_from_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("provider_id")), str(row.get("capability")))
        grouped.setdefault(key, []).append(row)
    statuses = {}
    for key, items in grouped.items():
        scores = [float(item.get("score", {}).get("quality_score", 0.0)) for item in items]
        succeeded = [bool(item.get("score", {}).get("succeeded")) for item in items]
        statuses[key] = _status(
            average_score=sum(scores) / len(scores) if scores else 0.0,
            all_succeeded=all(succeeded),
            count=len(items),
        )
    return statuses


def _status(*, average_score: float, all_succeeded: bool, count: int) -> str:
    if count == 0:
        return "not_started"
    if all_succeeded and average_score >= 0.8:
        return "passed"
    return "failed"
