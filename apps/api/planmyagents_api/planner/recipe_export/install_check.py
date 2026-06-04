"""npm install-command resolvability check (B6).

The Claude Desktop and Cursor renderers can emit MCP entries whose
``install_command`` is ``npx -y <package>``. If ``<package>`` does not
exist on the public npm registry, the user merges the recipe into their
host config and gets a cryptic failure when ``npx`` cannot resolve the
package. ``manual_recipe_roundtrip.py`` originally hit this with a
fictional ``@modelcontextprotocol/server-fetch``; ``docs/manual-test-
log.md`` tracks the bug as B6.

This module provides ``is_install_resolvable_via_npm(install_command)``
which returns:

* ``True``  — package is published on npm (``npm view <pkg> version`` exits 0)
* ``False`` — package is not found (``npm view`` exits non-zero with a
  ``404`` / ``E404`` style message)
* ``None``  — could not check (npm not installed, command not parseable,
  network timeout). In this case the renderer should NOT refuse the
  recipe; "unknown" is treated as "allow" so we never block on a flaky
  network.

The check is opt-in via ``PLANMYAGENTS_CHECK_NPM_RESOLVABILITY`` env var
(``true`` / ``1`` / ``yes`` enables) AND a per-renderer flag.

Default-off so the existing 25-test recipe suite keeps running without
network calls.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache

_NPM_TIMEOUT_S = 5.0


def is_check_enabled() -> bool:
    raw = os.environ.get("PLANMYAGENTS_CHECK_NPM_RESOLVABILITY", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _parse_npm_package(install_command: str) -> str | None:
    """Extract the package spec from ``npx -y <package> [args...]``.

    Returns the package spec (e.g. ``@scope/name`` or ``name@1.2.3``)
    or ``None`` when the command is not an ``npx`` invocation we can
    confidently parse.
    """
    tokens = install_command.strip().split()
    if not tokens or tokens[0] != "npx":
        return None
    skip_flags = {"-y", "--yes", "-q", "--quiet"}
    for token in tokens[1:]:
        if token in skip_flags:
            continue
        if token.startswith("-"):
            continue
        return token
    return None


@lru_cache(maxsize=256)
def _npm_view(package: str) -> bool | None:
    """Run ``npm view <package> version``; cache results for the process."""
    if shutil.which("npm") is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 — npm path resolved above
            ["npm", "view", package, "version"],
            capture_output=True,
            text=True,
            timeout=_NPM_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode == 0:
        return True
    stderr = proc.stderr.lower()
    if "404" in stderr or "e404" in stderr or "not found" in stderr:
        return False
    return None


def is_install_resolvable_via_npm(install_command: str) -> bool | None:
    """Return True / False / None for an MCP ``install_command``.

    See module docstring for semantics. Falls back to ``None`` (allow)
    on every failure mode so a flaky network does not silently start
    refusing valid recipes.
    """
    package = _parse_npm_package(install_command)
    if package is None:
        return None
    return _npm_view(package)
