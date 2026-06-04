"""Central logging configuration for the PlanMyAgents backend.

Why this module exists
----------------------
Before this module the codebase had ``logger = logging.getLogger(__name__)``
scattered across ~10 files, but **no handler was ever attached** to any
of those loggers — so every ``logger.info(...)`` call was silently
discarded. Operators looking at the ``make api`` terminal during a
5-minute ``/goal`` request saw only uvicorn's HTTP access log and had
no way to tell which stage was actually slow.

What this module does
---------------------
:func:`setup_logging` configures a single named logger tree (the
``planmyagents_api`` root) with:

1. A stdout :class:`StreamHandler` so logs interleave with uvicorn's
   own output and the operator sees per-stage progress in real time.
2. An optional :class:`RotatingFileHandler` writing to
   ``.planmyagents_runs/api.log`` (or wherever ``PLANMYAGENTS_LOG_FILE``
   points) so logs survive a terminal close and can be ``tail -f``'d
   from another window.
3. A compact, grep-friendly format with timestamp, level, logger
   short-name, and message — designed for skimming a 2-minute trace,
   not for structured ingestion. We can layer structured JSON later
   if we move to a hosted log sink.

The function is **idempotent**: calling it twice is a no-op so that
unit tests, the FastAPI module-level ``load_dotenv_once`` block, and
the various ``scripts/run_*.py`` entry points can all call it
liberally without producing duplicate handlers (the classic "every
log line printed 3 times" symptom).

Configuration
-------------
* ``PLANMYAGENTS_LOG_LEVEL`` — root level (default ``INFO``). Set to
  ``DEBUG`` for per-token LLM details; ``WARNING`` to quiet the
  scout/judge chatter when you only want failures.
* ``PLANMYAGENTS_LOG_FILE`` — explicit log file path. When unset, the
  default is ``<repo_root>/.planmyagents_runs/api.log``. Set to the empty
  string ``""`` to disable the file handler entirely (stdout only).
* ``PLANMYAGENTS_LOG_FILE_MAX_BYTES`` — rotate threshold (default
  10 MB). Keeps the last 5 rotations on disk.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_ROOT_NAME = "planmyagents_api"
_DONE = False

# Format engineered for "operator skimming a stuck request":
#   2026-05-12 13:15:25 INFO  goal | planner: complete in 3214ms
# Logger name is truncated to the right of the last "." so the column
# stays narrow (we already know we're inside planmyagents_api).
_DEFAULT_FORMAT = "%(asctime)s %(levelname)-5s %(name_short)s | %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _ShortNameFilter(logging.Filter):
    """Add a ``name_short`` attribute (last dot-segment of the logger
    name) so the formatter can show ``goal`` instead of the verbose
    ``planmyagents_api.web.app``."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.name_short = record.name.rsplit(".", 1)[-1]
        return True


def setup_logging(*, force: bool = False) -> None:
    """Configure the ``planmyagents_api`` logger tree. Idempotent.

    Args:
        force: re-apply config even if previously set up. Used by
            tests that want to swap handlers between cases.
    """

    global _DONE
    if _DONE and not force:
        return

    level_name = os.getenv("PLANMYAGENTS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(fmt=_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT)
    short_filter = _ShortNameFilter()

    root_logger = logging.getLogger(_ROOT_NAME)
    root_logger.setLevel(level)
    # Don't bubble up to the root Python logger — uvicorn / FastAPI
    # already manage the root logger and our messages would either be
    # duplicated or formatted in their (more verbose) shape.
    root_logger.propagate = False

    # Clear any existing handlers so re-setup with force=True is clean,
    # and so a stale reload state doesn't double-print.
    if force:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)

    if not root_logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(short_filter)
        root_logger.addHandler(stream_handler)

        file_path = _resolve_log_file_path()
        if file_path is not None:
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                max_bytes = int(
                    os.getenv("PLANMYAGENTS_LOG_FILE_MAX_BYTES", str(10 * 1024 * 1024))
                )
                file_handler = RotatingFileHandler(
                    file_path,
                    maxBytes=max_bytes,
                    backupCount=5,
                    encoding="utf-8",
                )
                file_handler.setFormatter(formatter)
                file_handler.addFilter(short_filter)
                root_logger.addHandler(file_handler)
            except OSError as exc:
                # File logging is a nice-to-have. If the disk path is
                # unwritable (read-only mount, sandboxed test runner,
                # etc.) we degrade to stdout-only rather than crash
                # the whole app on import.
                root_logger.warning(
                    "Could not open log file %s: %s. Logging to stdout only.",
                    file_path,
                    exc,
                )

    _DONE = True


def _resolve_log_file_path() -> Path | None:
    """Decide where the rotating log file should live.

    Returns ``None`` when the operator explicitly disabled file
    logging (``PLANMYAGENTS_LOG_FILE=""``)."""

    explicit = os.getenv("PLANMYAGENTS_LOG_FILE")
    if explicit is not None:
        stripped = explicit.strip()
        if stripped == "":
            return None
        return Path(stripped).expanduser()
    # Default: <repo_root>/.planmyagents_runs/api.log. Walks upward to find
    # the repo marker the same way ``_env.py`` does, so the path is
    # stable regardless of where the process is launched from.
    here = Path(__file__).resolve()
    for parent in [here, *here.parents][:6]:
        if (parent / "Makefile").exists() or (parent / "pyproject.toml").exists():
            return parent / ".planmyagents_runs" / "api.log"
    # Fallback: cwd-relative if we couldn't find a project marker
    # (e.g. when imported from a wholly external script).
    return Path(".planmyagents_runs") / "api.log"


def reset_for_tests() -> None:
    """Re-enable :func:`setup_logging` from scratch on the next call.

    Only used by the logging unit tests that need to swap handlers.
    Closes the existing handlers so the underlying file descriptors
    are released (otherwise Python 3.12+ emits a ``ResourceWarning``
    on tempfile cleanup and the test output gets noisy)."""

    global _DONE
    _DONE = False
    root_logger = logging.getLogger(_ROOT_NAME)
    for handler in list(root_logger.handlers):
        try:
            handler.close()
        except Exception:  # noqa: BLE001 - close() must not raise during teardown
            pass
        root_logger.removeHandler(handler)
