"""Always-on discovery-gap recorder.

Wraps :func:`discovery_gaps_store_for_path` with a small, defensive
interface so the ``/goal`` route can call it without worrying about
exceptions bubbling up. Demand recording proved its value: making it
silent on failure (record-best-effort, never raise) means the route
handler can call it once at the end of the request without an extra
``try/except`` wrapper.

Configuration (mirrors ``demand_recorder``):

* ``PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH`` — file path or postgres
  DSN. Defaults to ``<repo>/data/discovery_gap_events.jsonl``.
* ``PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED`` — set to ``false``
  to disable recording entirely (used by deterministic tests that
  don't want side-effects on disk / Postgres).

Recording is opt-out, not opt-in: gap signal is the *whole point* of
this surface, so silence by default would defeat the feature. The env
var exists so test runs can be hermetic.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from planmyagents_api.discovery.discovery_gaps_store import (
    DiscoveryGapSummary,
    build_discovery_gap_events,
    discovery_gaps_store_for_path,
)

_LOG = logging.getLogger(__name__)

DEFAULT_DISCOVERY_GAPS_STORE_RELATIVE_PATH = (
    Path("data") / "discovery_gap_events.jsonl"
)


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def discovery_gaps_recording_enabled() -> bool:
    """Default ON. Set
    ``PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED=false`` to
    suppress recording (used by deterministic tests).
    """

    raw = os.environ.get("PLANMYAGENTS_DISCOVERY_GAPS_RECORDING_ENABLED")
    if raw is None:
        return True
    return _is_truthy(raw)


def resolve_discovery_gaps_store_path() -> str:
    """Where do we persist gap events?

    Production: a Postgres DSN (typically the same one as the
    discovery store). Dev / tests: a JSONL file in ``data/``.

    We deliberately do NOT default to the discovery store URL because
    the gap log can grow much faster than the candidate table (one
    row per (request, missing-capability) tuple) and operators may
    want to keep it in a separate DB or even ship it to a warehouse
    pipeline. Forcing co-location would foreclose those options.
    """

    env_path = os.environ.get(
        "PLANMYAGENTS_DISCOVERY_GAPS_STORE_PATH", ""
    ).strip()
    if env_path:
        return env_path
    # parents[4] = repo root. See audit fix in demand_recorder.py for
    # the rationale; same off-by-one was present here.
    repo_root = Path(__file__).resolve().parents[4]
    return str(repo_root / DEFAULT_DISCOVERY_GAPS_STORE_RELATIVE_PATH)


def get_discovery_gaps_store():
    """Resolve and instantiate the configured gaps store.

    Returned store always exposes ``.append(events)`` and
    ``.summarize()``.
    """

    return discovery_gaps_store_for_path(resolve_discovery_gaps_store_path())


def record_discovery_gap(
    *,
    goal: str,
    missing_capabilities: list[str],
    judge_accepted_by_capability: dict[str, int],
    judge_evaluated_by_capability: dict[str, int],
    scout_dispatch_by_capability: dict[str, dict[str, int]] | None = None,
) -> int:
    """Persist one ``DiscoveryGapEvent`` per missing capability.

    Returns the number of events written. Returns 0 (and logs at INFO)
    if recording is disabled or if there are no missing capabilities.
    Catches every exception and never raises — gap recording is a
    best-effort side-effect that must never break the user's ``/goal``
    request, exactly like demand recording.
    """

    if not missing_capabilities:
        return 0
    if not discovery_gaps_recording_enabled():
        _LOG.info(
            "discovery gap recording disabled via env var; skipping %d events",
            len(missing_capabilities),
        )
        return 0

    events = build_discovery_gap_events(
        goal=goal,
        missing_capabilities=missing_capabilities,
        judge_accepted_by_capability=judge_accepted_by_capability,
        judge_evaluated_by_capability=judge_evaluated_by_capability,
        scout_dispatch_by_capability=scout_dispatch_by_capability,
    )
    if not events:
        return 0

    try:
        store = get_discovery_gaps_store()
        store.append(events)
        return len(events)
    except Exception as exc:  # noqa: BLE001 — must never block /goal
        _LOG.warning(
            "failed to record %d discovery gap events: %s", len(events), exc
        )
        return 0


def load_discovery_gaps_summary(
    *, sample_goals_per_capability: int = 3
) -> list[DiscoveryGapSummary]:
    """Read aggregated gaps from the configured store.

    Returns ``[]`` on any read failure (including missing file) so the
    public endpoint and the leaderboard tile degrade gracefully — an
    empty tile that says "no gap signal yet" is much better than a
    500 error from a missing file in a fresh install.
    """

    try:
        store = get_discovery_gaps_store()
        return store.summarize(
            sample_goals_per_capability=sample_goals_per_capability
        )
    except Exception as exc:  # noqa: BLE001 — read-side resilience
        _LOG.warning("failed to load discovery gaps summary: %s", exc)
        return []
