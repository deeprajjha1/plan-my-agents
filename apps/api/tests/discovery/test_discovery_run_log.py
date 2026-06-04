"""Unit tests for the discovery run audit log.

Covers:

* `DiscoveryRunEvent` round-trips JSON.
* `JsonRunEventStore` is append-only (multiple appends extend the file).
* `SqliteRunEventStore` round-trips events via the schema-managed table.
* Factory dispatch (postgres URL, sqlite extension, default JSON).
* `DiscoveryRunLogger.record(...)` context manager:
  - records `status='ok'`, elapsed_ms, candidates_returned on success.
  - records `status='error'` on raised exceptions and re-raises.
  - records `status='skipped'` when observation.skipped is set.
* `DiscoveryRunLogger.append_event(...)` direct-write path.
* Logger swallows store-failure (audit log must never break discovery).
* `DiscoveryRunLogger.default()` honours `PLANMYAGENTS_RUN_LOG_ENABLED=false`.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from planmyagents_api.discovery.run_log import (  # noqa: E402
    DiscoveryRunEvent,
    DiscoveryRunLogger,
    JsonRunEventStore,
    PostgresRunEventStore,
    SqliteRunEventStore,
    run_event_store_for_path,
)


def _event(
    *,
    source_id: str = "apis_guru",
    status: str = "ok",
    candidates_returned: int = 5,
    elapsed_ms: int = 123,
    error: str = "",
) -> DiscoveryRunEvent:
    return DiscoveryRunEvent(
        source_id=source_id,
        source_type="ApisGuruSource",
        status=status,
        error=error,
        candidates_returned=candidates_returned,
        elapsed_ms=elapsed_ms,
        query="payment_authorization",
        searched_capabilities=("payment_authorization",),
        trigger="batch",
        started_at="2026-05-01T00:00:00+00:00",
        completed_at="2026-05-01T00:00:01+00:00",
    )


class DiscoveryRunEventTests(unittest.TestCase):
    def test_to_json_round_trips(self) -> None:
        original = _event()
        round_tripped = DiscoveryRunEvent.from_json(original.to_json())
        self.assertEqual(original, round_tripped)


class JsonStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name) / "run_events.jsonl"
        self.store = JsonRunEventStore(self.path)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_append_is_additive(self) -> None:
        self.store.append([_event(source_id="apis_guru")])
        self.store.append([_event(source_id="hacker_news_agent_watch")])
        loaded = self.store.load_all()
        self.assertEqual(
            sorted(e.source_id for e in loaded),
            ["apis_guru", "hacker_news_agent_watch"],
        )

    def test_recent_per_source_buckets_and_caps(self) -> None:
        for i in range(5):
            self.store.append([
                _event(source_id="apis_guru", candidates_returned=i)
            ])
        recent = self.store.recent_per_source(limit_per_source=2)
        self.assertEqual(len(recent["apis_guru"]), 2)


class SqliteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name) / "run_events.sqlite"
        self.store = SqliteRunEventStore(self.path)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_append_and_load_round_trips(self) -> None:
        self.store.append([_event(), _event(source_id="hn")])
        loaded = self.store.load_all()
        self.assertEqual(len(loaded), 2)


class FactoryTests(unittest.TestCase):
    def test_postgres_url_returns_postgres_backend(self) -> None:
        store = run_event_store_for_path("postgresql://x:y@localhost:5432/db")
        self.assertIsInstance(store, PostgresRunEventStore)

    def test_sqlite_path_returns_sqlite_backend(self) -> None:
        store = run_event_store_for_path(Path("/tmp/run.sqlite"))
        self.assertIsInstance(store, SqliteRunEventStore)

    def test_json_path_returns_json_backend(self) -> None:
        store = run_event_store_for_path(Path("/tmp/run.jsonl"))
        self.assertIsInstance(store, JsonRunEventStore)


class DiscoveryRunLoggerContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name) / "run_events.jsonl"
        self.logger = DiscoveryRunLogger(JsonRunEventStore(self.path), enabled=True)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_records_ok_with_candidate_count(self) -> None:
        with self.logger.record(
            source_id="apis_guru",
            source_type="ApisGuruSource",
            query="payment",
            searched_capabilities=["payment_authorization"],
        ) as observation:
            observation.candidates_returned = 7

        loaded = JsonRunEventStore(self.path).load_all()
        self.assertEqual(len(loaded), 1)
        event = loaded[0]
        self.assertEqual(event.status, "ok")
        self.assertEqual(event.candidates_returned, 7)
        self.assertEqual(event.source_id, "apis_guru")
        self.assertGreaterEqual(event.elapsed_ms, 0)

    def test_records_error_and_reraises(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.logger.record(source_id="bad", source_type="X"):
                raise RuntimeError("boom")
        loaded = JsonRunEventStore(self.path).load_all()
        self.assertEqual(loaded[0].status, "error")
        self.assertIn("boom", loaded[0].error)

    def test_records_skipped_when_observation_set(self) -> None:
        with self.logger.record(source_id="x", source_type="Y") as observation:
            observation.skipped = True
            observation.skip_reason = "no token"
        loaded = JsonRunEventStore(self.path).load_all()
        self.assertEqual(loaded[0].status, "skipped")
        self.assertEqual(loaded[0].error, "no token")


class DiscoveryRunLoggerDirectAppendTests(unittest.TestCase):
    def test_append_event_writes_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            logger = DiscoveryRunLogger(JsonRunEventStore(path), enabled=True)
            logger.append_event(_event(source_id="manual"))
            loaded = JsonRunEventStore(path).load_all()
            self.assertEqual([e.source_id for e in loaded], ["manual"])

    def test_disabled_logger_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            logger = DiscoveryRunLogger(JsonRunEventStore(path), enabled=False)
            logger.append_event(_event())
            with logger.record(source_id="x", source_type="Y"):
                pass
            self.assertFalse(path.exists())


class _BrokenStore:
    """Always raises on append. Used to assert the logger swallows
    failures so the discovery pipeline never breaks because the audit
    log is broken."""

    def append(self, events) -> None:  # noqa: ARG002
        raise OSError("disk full")


class DiscoveryRunLoggerErrorSafetyTests(unittest.TestCase):
    def test_logger_swallows_store_exceptions(self) -> None:
        logger = DiscoveryRunLogger(_BrokenStore(), enabled=True)
        # Must NOT raise.
        logger.append_event(_event())
        with logger.record(source_id="x", source_type="Y") as observation:
            observation.candidates_returned = 1


class DiscoveryRunLoggerDefaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_enabled = os.environ.get("PLANMYAGENTS_RUN_LOG_ENABLED")
        self._original_path = os.environ.get("PLANMYAGENTS_RUN_LOG_STORE_PATH")

    def tearDown(self) -> None:
        if self._original_enabled is None:
            os.environ.pop("PLANMYAGENTS_RUN_LOG_ENABLED", None)
        else:
            os.environ["PLANMYAGENTS_RUN_LOG_ENABLED"] = self._original_enabled
        if self._original_path is None:
            os.environ.pop("PLANMYAGENTS_RUN_LOG_STORE_PATH", None)
        else:
            os.environ["PLANMYAGENTS_RUN_LOG_STORE_PATH"] = self._original_path

    def test_disabled_via_env_var(self) -> None:
        os.environ["PLANMYAGENTS_RUN_LOG_ENABLED"] = "false"
        logger = DiscoveryRunLogger.default()
        # The disabled logger must still expose the same surface.
        logger.append_event(_event())
        with logger.record(source_id="x", source_type="Y"):
            pass
        # No crash, no file created.

    def test_path_resolution_uses_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            os.environ["PLANMYAGENTS_RUN_LOG_ENABLED"] = "true"
            os.environ["PLANMYAGENTS_RUN_LOG_STORE_PATH"] = str(path)
            logger = DiscoveryRunLogger.default()
            logger.append_event(_event())
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
