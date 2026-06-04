"""Tiny zero-dependency `.env` loader.

We deliberately avoid `python-dotenv` to keep the prototype's
no-extra-dependency posture. The loader:

* reads `<repo_root>/.env` once per process (idempotent)
* parses simple ``KEY=value`` and ``KEY="value"`` lines
* skips blank lines and ``#`` comments
* **never overrides values already in ``os.environ``** — so explicit shell
  exports, CI secrets, and Docker `--env` flags always win over the file.
* applies a one-shot **legacy env var alias** pass after parsing: any
  variable whose name starts with the legacy ``AGENTMANAGER_`` prefix is
  also exposed under the new ``PLANMYAGENTS_`` prefix when the new key
  is not already set. This keeps existing operator-managed ``.env``
  files working through the rebrand without the operator having to
  rename every key the same day. A loud one-time WARNING is logged
  when an alias is applied so the operator knows to migrate.

Call :func:`load_dotenv_once` at the top of any entry point (FastAPI app,
script, CLI) before reading env vars. Safe to call repeatedly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_LOADED = False

LEGACY_ENV_PREFIX = "AGENTMANAGER_"
NEW_ENV_PREFIX = "PLANMYAGENTS_"


def load_dotenv_once(env_path: Path | str | None = None) -> Path | None:
    """Load `.env` into ``os.environ`` if it hasn't been loaded yet.

    Returns the path that was loaded (or ``None`` if no file was found).
    """

    global _LOADED
    if _LOADED:
        return None

    path = _resolve_env_path(env_path)
    if path is None or not path.exists():
        # Even if there is no .env file, still apply the legacy alias
        # pass — the operator may have set ``AGENTMANAGER_*`` directly
        # in their shell environment (CI, Docker --env, etc.).
        _apply_legacy_env_aliases()
        _LOADED = True
        return None

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key.startswith("#"):
            continue
        value = _strip_quotes(value.strip())
        # Shell-exported values always win over the file.
        if key not in os.environ:
            os.environ[key] = value

    _apply_legacy_env_aliases()
    _LOADED = True
    return path


def _apply_legacy_env_aliases() -> None:
    """Mirror legacy ``AGENTMANAGER_*`` env vars onto the new
    ``PLANMYAGENTS_*`` names so an unmigrated ``.env`` keeps working.

    Rules:

    * If both the legacy and the new key are set, the new key wins
      (deliberate — operator has explicitly migrated this one).
    * If only the legacy key is set, copy its value to the new key
      and emit a one-time deprecation warning naming the legacy key.
    * If only the new key is set, do nothing — already migrated.

    The warning is per-key so a partially migrated ``.env`` lights
    up exactly the variables that still need attention. We use the
    ``planmyagents.env`` logger so operators can filter or silence
    it via standard logging configuration.
    """

    logger = logging.getLogger("planmyagents.env")
    aliased: list[str] = []
    for key, value in list(os.environ.items()):
        if not key.startswith(LEGACY_ENV_PREFIX):
            continue
        new_key = NEW_ENV_PREFIX + key[len(LEGACY_ENV_PREFIX):]
        if new_key in os.environ:
            # New key already set — operator has migrated this one,
            # respect their choice and don't overwrite it.
            continue
        os.environ[new_key] = value
        aliased.append(key)
    if aliased:
        # One log line per legacy key so the operator can grep their
        # ``.env`` for everything that still needs renaming. We
        # deliberately keep the message short — log spam is the
        # second-worst failure mode of a deprecation system, right
        # after silent ignores.
        for legacy in sorted(aliased):
            new_key = NEW_ENV_PREFIX + legacy[len(LEGACY_ENV_PREFIX):]
            logger.warning(
                "env: legacy %s is deprecated; please rename to %s in .env",
                legacy,
                new_key,
            )


def _resolve_env_path(env_path: Path | str | None) -> Path | None:
    if env_path is not None:
        return Path(env_path)
    # Walk upward from this file until we find a `.env` next to a project
    # marker (Makefile / pyproject.toml). Caps the walk so we never escape
    # the repo into the user's home directory.
    here = Path(__file__).resolve()
    for parent in [here, *here.parents][:6]:
        candidate = parent / ".env"
        if candidate.exists() and (
            (parent / "Makefile").exists() or (parent / "pyproject.toml").exists()
        ):
            return candidate
    return None


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
