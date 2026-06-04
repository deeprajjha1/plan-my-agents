"""Single source of truth for runtime store URLs and their defaults.

Before this module existed, three different files declared three
different fallback values for ``PLANMYAGENTS_DISCOVERY_STORE_URL``:

* :mod:`planmyagents_api.web.app` defaulted to a local SQLite path
  (``.planmyagents_runs/discovery-store.sqlite``).
* :mod:`planmyagents_api.web.planning` defaulted to a Postgres URL
  (``postgresql://planmyagents:...@localhost:55433/planmyagents``).
* :mod:`planmyagents_api.agents.router` used the empty string as a
  "skip if unset" sentinel.

In any environment where the env var was actually unset (a deploy
that forgot the secret, ``.env`` failing to load, a Docker `--env`
typo), the first two halves of the API silently read from different
stores — a true split-brain that no test would catch because tests
always set the var explicitly.

This module centralises the defaults. Every in-process consumer
(``web.app``, ``web.planning``, and any future module) must call
:func:`discovery_store_url`, :func:`benchmark_store_url`, or
:func:`verification_store_url` instead of inlining the env lookup.
The matching guard test in ``apps/api/tests/test_store_url_defaults.py``
walks the AST of every module under ``planmyagents_api`` and fails
if any file declares its own default for one of the three store env
vars.

Design choices worth flagging
-----------------------------

* **The default is Postgres, not SQLite.** Every operator-facing doc
  (``docs/operations.md``, ``docs/aws-hosting-guide.md``,
  ``infra/README.md``) and every operator-facing wrapper script
  (``scripts/bootstrap_postgres.sh``, ``scripts/upkeep_loop.sh``)
  already assumes Postgres in prod. Tests are unaffected because
  they always set the env var explicitly to a tmpdir SQLite path.
  A fresh developer who runs ``make api`` without setting up the
  database will get a clear connection error on the first request
  — which is more honest than silently writing to a SQLite file
  half the system never reads.
* **Startup logs a one-line WARNING when any store URL is the bare
  default.** Operators see "you're on the default Postgres DSN" in
  the very first log line and immediately know whether their secret
  was wired in or not. The warning is per-key so a partially
  configured environment lights up exactly the variables that are
  still unset. This mirrors the ``AGENTMANAGER_*`` → ``PLANMYAGENTS_*``
  alias warning in :mod:`planmyagents_api._env`.
* **The "empty string as skip sentinel" pattern in agents/router.py
  is preserved.** It's semantically different from a default
  (``router.py`` reads the env var only to decide whether to merge
  promoted-provider rows; an unset var means "don't merge", not
  "use the default DSN"). The guard test recognises the
  empty-string literal as a non-default and lets it pass.
"""

from __future__ import annotations

import logging
import os

DEFAULT_POSTGRES_DSN = (
    "postgresql://planmyagents:planmyagents@localhost:55433/planmyagents"
)
"""Fallback DSN for all three stores when the env var is unset.

Matches the local-dev Postgres that ``scripts/bootstrap_postgres.sh``
provisions and that every operator doc cites. In production this
should always be overridden by an explicit env var; the warning in
:func:`log_store_defaults_in_use` makes it loud if it isn't.
"""

DISCOVERY_STORE_ENV = "PLANMYAGENTS_DISCOVERY_STORE_URL"
BENCHMARK_STORE_ENV = "PLANMYAGENTS_BENCHMARK_STORE_URL"
VERIFICATION_STORE_ENV = "PLANMYAGENTS_VERIFICATION_STORE_URL"


def discovery_store_url() -> str:
    """Return the discovery store DSN, falling back to :data:`DEFAULT_POSTGRES_DSN`."""

    return os.getenv(DISCOVERY_STORE_ENV, DEFAULT_POSTGRES_DSN)


def benchmark_store_url() -> str:
    """Return the benchmark store DSN, falling back to :data:`DEFAULT_POSTGRES_DSN`."""

    return os.getenv(BENCHMARK_STORE_ENV, DEFAULT_POSTGRES_DSN)


def verification_store_url() -> str:
    """Return the verification store DSN, falling back to :data:`DEFAULT_POSTGRES_DSN`."""

    return os.getenv(VERIFICATION_STORE_ENV, DEFAULT_POSTGRES_DSN)


def log_store_defaults_in_use(logger: logging.Logger | None = None) -> list[str]:
    """Emit a one-line WARNING per store URL that's still on the bare default.

    Called once from the FastAPI app factory at startup. Returns the
    list of env var names that fell back to the default, so tests can
    assert the warning fires correctly without parsing log output.
    """

    log = logger or logging.getLogger("planmyagents.config")
    fallen_back: list[str] = []
    for env_var in (DISCOVERY_STORE_ENV, BENCHMARK_STORE_ENV, VERIFICATION_STORE_ENV):
        if env_var not in os.environ:
            fallen_back.append(env_var)
    if fallen_back:
        log.warning(
            "config: %s unset; falling back to DEFAULT_POSTGRES_DSN (%s). "
            "Set %s in .env or your deploy secrets to silence this.",
            ", ".join(fallen_back),
            DEFAULT_POSTGRES_DSN,
            " / ".join(fallen_back),
        )
    return fallen_back
