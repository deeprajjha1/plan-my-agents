"""Always-on capability-label recorder.

Wraps :func:`capability_label_store_for_path` with a small, defensive
interface so the ``/goal`` route can persist newly-coined labels
without worrying about exceptions bubbling up. Mirrors
:mod:`planmyagents_api.discovery.discovery_gaps_recorder` to keep ops mental
model uniform across the three best-effort persistence layers
(demand, gaps, labels).

Configuration:

* ``PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH`` — file path or postgres
  DSN. Resolution order (highest priority first):

    1. Explicit value of this env var (a tempfile in tests, an
       overriding DSN in unusual prod topologies).
    2. ``planmyagents_api._config.discovery_store_url()`` if it
       resolves to a Postgres DSN — production typically points
       every store at the same DB, and labels MUST follow the
       evidence tables, otherwise the planner happily coins them
       into a sidecar JSON file the dashboard never reads (the
       2026-05-19 audit found 15 high-frequency labels orphaned
       this way; production reported ``capability_labels = 0``
       despite real /goal traffic).
    3. ``<repo>/data/capability_labels.json`` — dev/test fallback
       so a fresh clone with no Postgres still works.

  This deliberately replaces the older "default to JSON, opt in
  explicitly" policy, because that policy turned a missing env var
  into a silent data-orphaning bug. The new default routes labels
  wherever the rest of the evidence pipeline goes.

* ``PLANMYAGENTS_CAPABILITY_LABEL_RECORDING_ENABLED`` — set to
  ``false`` to disable label persistence entirely. The reconciler
  still runs (it's read-only against the catalog hint); only the
  upsert is skipped. Used by deterministic tests that don't want
  filesystem / DB side-effects.

Why opt-out, not opt-in
-----------------------
The label vocabulary is the *whole point* of slice 2. Defaulting to
"off" would mean the system can never grow its catalog from real
usage, which is the bug we're fixing. The env var exists so unit
tests can stay hermetic; production is expected to leave it on.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from planmyagents_api.planner.capability_label_store import (
    CapabilityLabel,
    capability_label_store_for_path,
)

_LOG = logging.getLogger(__name__)

DEFAULT_CAPABILITY_LABEL_STORE_RELATIVE_PATH = (
    Path("data") / "capability_labels.json"
)


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def capability_label_recording_enabled() -> bool:
    """Default ON. Set
    ``PLANMYAGENTS_CAPABILITY_LABEL_RECORDING_ENABLED=false`` to
    suppress persistence (used by deterministic tests).
    """

    raw = os.environ.get("PLANMYAGENTS_CAPABILITY_LABEL_RECORDING_ENABLED")
    if raw is None:
        return True
    return _is_truthy(raw)


def resolve_capability_label_store_path() -> str:
    """Where do we persist coined labels?

    Resolution order:

    1. ``PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH`` env var, if set.
    2. The discovery store DSN — if it resolves to a Postgres URL,
       labels follow it. This is the production path; without it,
       a missing env var silently orphaned labels into
       ``data/capability_labels.json`` (the 2026-05-19 audit bug
       where ``capability_labels`` was 0 in Postgres despite real
       /goal traffic; 15 real labels were rotting in the JSON
       file). See the module docstring for the full motivation.
    3. ``<repo>/data/capability_labels.json`` — dev/test fallback
       so a fresh clone with no Postgres still works.

    The label set is small (one row per distinct label) and read on
    the planner hot path, so the JSON file is fine for local dev
    even with hundreds of distinct labels.
    """

    env_path = os.environ.get(
        "PLANMYAGENTS_CAPABILITY_LABEL_STORE_PATH", ""
    ).strip()
    if env_path:
        return env_path

    # Production path: inherit from the discovery store URL when it
    # points at Postgres. Lazy import to avoid a hard dependency on
    # ``_config`` for unit tests that only exercise the JSON path.
    try:
        from planmyagents_api._config import discovery_store_url

        inherited = discovery_store_url().strip()
        if inherited.startswith(("postgresql://", "postgres://")):
            return inherited
    except Exception as exc:  # noqa: BLE001 — config errors must not
        # block label persistence; the JSON fallback below will at
        # least keep the planner functional.
        _LOG.warning(
            "could not inherit capability label store from discovery "
            "store URL: %s; falling back to JSON file",
            exc,
        )

    # parents[4] = repo root. See audit fix in demand_recorder.py for
    # the rationale; same off-by-one was present here.
    repo_root = Path(__file__).resolve().parents[4]
    return str(repo_root / DEFAULT_CAPABILITY_LABEL_STORE_RELATIVE_PATH)


def get_capability_label_store():
    """Resolve and instantiate the configured label store.

    Returned store always exposes ``.upsert(...)``, ``.get(...)``,
    ``.list_all()``, ``.list_ids()`` — see
    :class:`JsonCapabilityLabelStore` for the contract.
    """

    return capability_label_store_for_path(resolve_capability_label_store_path())


def record_coined_labels(
    *,
    coined_labels: dict[str, str],
    coined_from_goal_hash: str = "",
) -> int:
    """UPSERT each coined label. Returns the number of rows written.

    Args:
        coined_labels: ``{capability_id: description}``. The
            description is stored only on first insert (see
            :class:`CapabilityLabel` docstring).
        coined_from_goal_hash: Optional opaque dedupe token for the
            originating goal text. Stable across capitalisation /
            whitespace changes so two near-identical goals share a
            hash. Pass ``""`` to skip.

    Best-effort: catches every exception and logs at WARNING. Never
    raises — the user's ``/goal`` request must not fail because the
    label store is briefly unreachable.
    """

    if not coined_labels:
        return 0
    if not capability_label_recording_enabled():
        _LOG.info(
            "capability label recording disabled via env var; skipping %d labels",
            len(coined_labels),
        )
        return 0

    try:
        store = get_capability_label_store()
    except Exception as exc:  # noqa: BLE001 — must never block /goal
        _LOG.warning("failed to instantiate capability label store: %s", exc)
        return 0

    written = 0
    for cap_id, description in coined_labels.items():
        cleaned_id = (cap_id or "").strip()
        if not cleaned_id:
            continue
        try:
            store.upsert(
                id=cleaned_id,
                description=(description or "").strip(),
                coined_from_goal_hash=coined_from_goal_hash,
            )
            written += 1
        except Exception as exc:  # noqa: BLE001 — per-row resilience
            _LOG.warning(
                "failed to upsert capability label %r: %s", cleaned_id, exc
            )
    return written


def load_persisted_label_ids() -> set[str]:
    """Read the set of persisted label ids for the planner catalog
    hint. Returns an empty set on any read failure so the planner
    never crashes because the label store is unreachable on a
    fresh install.
    """

    try:
        store = get_capability_label_store()
        return store.list_ids()
    except Exception as exc:  # noqa: BLE001 — defensive read path
        _LOG.warning("failed to load persisted capability label ids: %s", exc)
        return set()


def load_persisted_labels() -> list[CapabilityLabel]:
    """Read the full list of persisted labels (for the inspection
    endpoint). Returns ``[]`` on any read failure for the same
    reason as :func:`load_persisted_label_ids`.
    """

    try:
        store = get_capability_label_store()
        return store.list_all()
    except Exception as exc:  # noqa: BLE001 — defensive read path
        _LOG.warning("failed to load persisted capability labels: %s", exc)
        return []


def labels_to_payload(labels: list[CapabilityLabel]) -> list[dict[str, Any]]:
    """Convenience: render labels as JSON-friendly dicts ready for
    direct return from a FastAPI route handler."""

    return [label.to_json() for label in labels]
