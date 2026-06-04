"""Tests for the central logging configuration.

These tests cover the behaviors the operator depends on when running
the API server:

* :func:`setup_logging` is **idempotent** — repeated calls do not stack
  duplicate handlers (which would cause every log line to be printed
  multiple times, the classic "noisy log" symptom).
* The configured logger emits to both stdout and to a rotating file
  handler at the path resolved from ``PLANMYAGENTS_LOG_FILE`` (or the
  default :file:`.planmyagents_runs/api.log`).
* The level honors ``PLANMYAGENTS_LOG_LEVEL``.
* Setting ``PLANMYAGENTS_LOG_FILE=""`` disables the file handler.

We deliberately do **not** test the exact format string — that's
considered a presentation detail subject to future change without
breaking the observability contract.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api import _logging  # noqa: E402


class _EnvSandbox:
    """Tiny helper that snapshots and restores the env vars we touch.

    Inlined rather than depending on the unittest-builtin
    :class:`unittest.mock.patch.dict` so the test reads cleanly
    side-by-side with the env_loader tests."""

    KEYS = (
        "PLANMYAGENTS_LOG_LEVEL",
        "PLANMYAGENTS_LOG_FILE",
        "PLANMYAGENTS_LOG_FILE_MAX_BYTES",
    )

    def __enter__(self) -> _EnvSandbox:
        self._snapshot: dict[str, str | None] = {
            k: os.environ.pop(k, None) for k in self.KEYS
        }
        return self

    def __exit__(self, *_: object) -> None:
        for k, v in self._snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class SetupLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        _logging.reset_for_tests()

    def tearDown(self) -> None:
        _logging.reset_for_tests()

    def test_first_call_attaches_stream_and_file_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _EnvSandbox():
            log_file = Path(tmp) / "api.log"
            os.environ["PLANMYAGENTS_LOG_FILE"] = str(log_file)
            os.environ["PLANMYAGENTS_LOG_LEVEL"] = "INFO"

            _logging.setup_logging()

            root_logger = logging.getLogger("planmyagents_api")
            handler_types = {type(h).__name__ for h in root_logger.handlers}
            self.assertIn("StreamHandler", handler_types)
            self.assertIn("RotatingFileHandler", handler_types)
            self.assertEqual(root_logger.level, logging.INFO)

    def test_setup_is_idempotent(self) -> None:
        """Repeated calls must not stack duplicate handlers — that's
        the bug that would cause every log line to be printed twice
        on every reload."""

        with tempfile.TemporaryDirectory() as tmp, _EnvSandbox():
            os.environ["PLANMYAGENTS_LOG_FILE"] = str(Path(tmp) / "api.log")

            _logging.setup_logging()
            handler_count = len(logging.getLogger("planmyagents_api").handlers)

            _logging.setup_logging()
            _logging.setup_logging()
            _logging.setup_logging()

            self.assertEqual(
                handler_count,
                len(logging.getLogger("planmyagents_api").handlers),
                "setup_logging should be a no-op after the first call",
            )

    def test_force_resets_handlers(self) -> None:
        """``force=True`` is the escape hatch for tests that want to
        swap handlers between cases. Without it, the idempotent guard
        would prevent picking up a different log file path."""

        with tempfile.TemporaryDirectory() as tmp, _EnvSandbox():
            first = Path(tmp) / "first.log"
            second = Path(tmp) / "second.log"
            os.environ["PLANMYAGENTS_LOG_FILE"] = str(first)
            _logging.setup_logging()
            self._assert_file_handler_points_at(first)

            os.environ["PLANMYAGENTS_LOG_FILE"] = str(second)
            _logging.setup_logging(force=True)
            self._assert_file_handler_points_at(second)

    def test_empty_log_file_disables_file_handler(self) -> None:
        """Operators who explicitly want stdout-only logging set
        ``PLANMYAGENTS_LOG_FILE=""``. We must respect that even though
        the env var is *present* (just empty)."""

        with _EnvSandbox():
            os.environ["PLANMYAGENTS_LOG_FILE"] = ""
            _logging.setup_logging()

            root_logger = logging.getLogger("planmyagents_api")
            file_handlers = [
                h
                for h in root_logger.handlers
                if isinstance(h, RotatingFileHandler)
            ]
            self.assertEqual(
                file_handlers,
                [],
                "empty PLANMYAGENTS_LOG_FILE should disable file logging",
            )

    def test_messages_reach_the_log_file(self) -> None:
        """End-to-end smoke check: a call to ``logger.info`` must
        actually land in the configured file. This is the property the
        operator cares about; the rest of the suite is plumbing."""

        with tempfile.TemporaryDirectory() as tmp, _EnvSandbox():
            log_file = Path(tmp) / "api.log"
            os.environ["PLANMYAGENTS_LOG_FILE"] = str(log_file)
            _logging.setup_logging()

            logger = logging.getLogger("planmyagents_api.tests.smoke")
            logger.info("hello-from-test-canary-29384")

            for handler in logging.getLogger("planmyagents_api").handlers:
                handler.flush()

            self.assertTrue(log_file.exists(), "log file should be created")
            self.assertIn(
                "hello-from-test-canary-29384",
                log_file.read_text(encoding="utf-8"),
            )

    def test_log_level_honors_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _EnvSandbox():
            os.environ["PLANMYAGENTS_LOG_FILE"] = str(Path(tmp) / "api.log")
            os.environ["PLANMYAGENTS_LOG_LEVEL"] = "WARNING"
            _logging.setup_logging()
            self.assertEqual(
                logging.getLogger("planmyagents_api").level, logging.WARNING
            )

    def test_short_name_filter_truncates_logger_name(self) -> None:
        """The custom formatter relies on a ``name_short`` attribute
        injected by a filter so the message column stays narrow."""

        record = logging.LogRecord(
            name="planmyagents_api.discovery.scouts",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="",
            args=(),
            exc_info=None,
        )
        filt = _logging._ShortNameFilter()
        self.assertTrue(filt.filter(record))
        self.assertEqual(record.name_short, "scouts")  # type: ignore[attr-defined]

    def _assert_file_handler_points_at(self, expected: Path) -> None:
        root_logger = logging.getLogger("planmyagents_api")
        file_handlers = [
            h for h in root_logger.handlers if isinstance(h, RotatingFileHandler)
        ]
        self.assertEqual(len(file_handlers), 1)
        # baseFilename is the canonical attribute on FileHandler/RotatingFileHandler
        self.assertEqual(
            Path(file_handlers[0].baseFilename).resolve(),
            expected.resolve(),
        )


if __name__ == "__main__":
    unittest.main()
