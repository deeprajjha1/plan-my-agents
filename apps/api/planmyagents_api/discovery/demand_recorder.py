"""Always-on capability demand recorder.

Wraps `demand_store_for_path` with a small, defensive interface so the
`/goal` refusal path can call it without worrying about exceptions
bubbling up. We never want demand-recording failures to break a user
request.

Configuration (mirrors discovery store env vars):

* `PLANMYAGENTS_DEMAND_STORE_PATH` — file path or postgres DSN.
  Defaults to `<repo>/data/capability_demand_events.jsonl`.
* `PLANMYAGENTS_DEMAND_RECORDING_ENABLED` — set to `false` to disable
  recording entirely (used by tests that don't want side-effects).

Recording is opt-out, not opt-in. The user explicitly asked for the
gap signal to be honest and persistent — silence by default would
defeat that.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from planmyagents_api.discovery.demand_store import (
    DemandSummary,
    build_demand_events_for_request,
    demand_store_for_path,
)

_LOG = logging.getLogger(__name__)

DEFAULT_DEMAND_STORE_RELATIVE_PATH = Path("data") / "capability_demand_events.jsonl"


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def demand_recording_enabled() -> bool:
    """Default ON. Set PLANMYAGENTS_DEMAND_RECORDING_ENABLED=false to
    suppress recording (used by deterministic tests)."""

    raw = os.environ.get("PLANMYAGENTS_DEMAND_RECORDING_ENABLED")
    if raw is None:
        return True
    return _is_truthy(raw)


def resolve_demand_store_path() -> str:
    """Where do we persist demand events?

    Production: a postgres DSN.
    Dev / tests: a JSONL file in `data/`.
    """

    env_path = os.environ.get("PLANMYAGENTS_DEMAND_STORE_PATH", "").strip()
    if env_path:
        return env_path
    # parents[4] = repo root (this file lives at apps/api/planmyagents_api/
    # discovery/demand_recorder.py, so 4 hops up gets us above `apps/`).
    # The previous parents[3] resolved to `apps/`, which silently dropped
    # default-path demand events into a sibling of the repo and broke the
    # dashboard read-side. Audit fix.
    repo_root = Path(__file__).resolve().parents[4]
    return str(repo_root / DEFAULT_DEMAND_STORE_RELATIVE_PATH)


def get_demand_store():
    """Resolve and instantiate the configured demand store.

    Returned store always exposes `.append(events)` and `.summarize()`.
    """

    return demand_store_for_path(resolve_demand_store_path())


def record_refusal_demand(
    *,
    goal: str,
    missing_capabilities: list[str],
    candidates_by_capability: dict[str, list[dict[str, Any]]],
    apis_without_agents_by_capability: dict[str, list[dict[str, Any]]],
    requester: str | None = None,
) -> int:
    """Persist one DemandEvent per missing capability for this request.

    Returns the number of events written. Returns 0 (and logs at INFO)
    if recording is disabled or if there are no missing capabilities.
    Catches every exception and never raises — demand recording is a
    best-effort side-effect that must never break the user's /goal
    request.
    """

    if not missing_capabilities:
        return 0
    if not demand_recording_enabled():
        _LOG.info("demand recording disabled via env var; skipping %d events", len(missing_capabilities))
        return 0

    events = build_demand_events_for_request(
        goal=goal,
        missing_capabilities=missing_capabilities,
        candidates_by_capability=candidates_by_capability,
        apis_without_agents_by_capability=apis_without_agents_by_capability,
        requester=requester,
    )
    if not events:
        return 0

    try:
        store = get_demand_store()
        store.append(events)
        return len(events)
    except Exception as exc:  # noqa: BLE001 — must never block /goal
        _LOG.warning("failed to record %d demand events: %s", len(events), exc)
        return 0


def load_demand_summary(*, sample_goals_per_capability: int = 3) -> list[DemandSummary]:
    """Read aggregated demand from the configured store. Returns [] on
    any read failure (including missing file) so the public endpoint
    degrades gracefully."""

    try:
        store = get_demand_store()
        return store.summarize(sample_goals_per_capability=sample_goals_per_capability)
    except Exception as exc:  # noqa: BLE001 — read-side resilience
        _LOG.warning("failed to load demand summary: %s", exc)
        return []
