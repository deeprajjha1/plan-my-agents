"""Rebuild ``agent_rankings`` from raw ``benchmark_runs``.

Why this module exists
----------------------
The `/leaderboards` endpoint reads from ``agent_rankings`` — a pre-
aggregated table — not from the raw ``benchmark_runs`` table. Most
write paths (the live :class:`BenchmarkScheduler`) update both
tables atomically, but **operational backfills do not**:

* ``scripts/backfill_phase5_cells.py`` inserts response-fixture rows
  directly into ``benchmark_runs`` via ``BenchmarkStore.save(runs)``,
  bypassing the ``save_rankings`` call the scheduler would have made.
* Any future "replay a recorded live run into the store" script will
  do the same.

When that happens, the leaderboard tile shows
``Bench-passed 0 · Routable 0 · Real runs No`` for a cell that
genuinely has 5 succeeded real-shape runs — silently misrepresenting
the strongest evidence on the deck. The fix is to make
"rebuild rankings from runs" a single canonical, deterministic,
idempotent function that any writer can call after a bulk insert,
plus a ``make rebuild-rankings`` target for operators.

Source classification
---------------------
The leaderboard's ``has_real_adapter_runs`` check is
``source not in {"", "synthetic"}``. We tag each ranking with the
strongest source observed across its runs, with this priority
(strongest → weakest):

1. ``real_adapter`` — ``run_json.response.output._replayed_from`` is
   set, or ``_provenance`` matches a "real" pattern. Used for
   recorded live-run replays (e.g. Razorpay's 2026-05-15 snapshot).
2. ``response_fixture`` — ``_provenance`` is
   ``response_fixture_pending_live_key`` (Phase 5 Resend / Firecrawl
   cells: real wrapper code path, deterministic transport).
3. ``synthetic`` — everything else.

Both ``real_adapter`` and ``response_fixture`` count as "real" for
the leaderboard's purposes, by design — the operator distinction
matters for credibility-report copy but not for the boolean badge.

Idempotency
-----------
Calling :func:`rebuild_rankings_from_runs` twice in a row produces
the same ``agent_rankings`` content as calling it once. The Postgres
store's :meth:`save_rankings` truncates the per-cell rows it owns
before re-inserting (see
``apps/api/planmyagents_api/benchmark/store.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from planmyagents_api.benchmark.models import (
    BenchmarkRun,
    FieldScore,
    ProviderResponse,
    ScoreResult,
)
from planmyagents_api.benchmark.rankings import AgentRanking, compute_rankings

logger = logging.getLogger(__name__)


# Provenance markers we treat as real-adapter-equivalent (live runs that
# were recorded once and are replayed deterministically). Substring
# match — keep this list short and high-signal so we don't accidentally
# promote a synthetic fixture by tag soup.
_REAL_ADAPTER_PROVENANCE_MARKERS: tuple[str, ...] = (
    "agents.json:benchmark_status_evidence",
    "replayed_live_run",
)

# Provenance markers we treat as "response fixture": real wrapper code
# path with a deterministic transport, pending a live API key. The cell
# still counts as "real runs: yes" on the leaderboard, but is reported
# separately in the credibility detail page.
_RESPONSE_FIXTURE_PROVENANCE_MARKERS: tuple[str, ...] = (
    "response_fixture_pending_live_key",
)


class _BenchmarkStoreLike(Protocol):
    """The narrow surface area we need from a benchmark store.

    Defined here as a Protocol (not imported) so this module has zero
    dependency on the Postgres/JSON store implementations and stays
    trivially unit-testable with a fake.
    """

    def load_json(self) -> list[dict[str, Any]]: ...
    def save_rankings(self, rankings: list[AgentRanking]) -> None: ...


@dataclass(frozen=True)
class RebuildReport:
    """Summary of one rebuild call.

    Exposed so operators / tests can assert on the outcome without
    having to re-query the store. ``cells_real_adapter`` and
    ``cells_response_fixture`` together with ``cells_synthetic``
    always sum to ``cells_total``.
    """

    runs_loaded: int
    cells_total: int
    cells_real_adapter: int
    cells_response_fixture: int
    cells_synthetic: int
    rankings_persisted: int


def rebuild_rankings_from_runs(
    store: _BenchmarkStoreLike,
    *,
    weights: dict[str, float] | None = None,
) -> RebuildReport:
    """Recompute and persist ``agent_rankings`` from ``benchmark_runs``.

    The function is the single canonical implementation for the
    "derive rankings from runs" step. Every operational writer that
    bypasses :class:`BenchmarkScheduler` should call this after its
    bulk insert (currently only :mod:`scripts.backfill_phase5_cells`).
    """

    raw_rows = list(store.load_json())
    if not raw_rows:
        logger.info("rebuild_rankings: no benchmark_runs rows; rankings cleared")
        store.save_rankings([])
        return RebuildReport(
            runs_loaded=0,
            cells_total=0,
            cells_real_adapter=0,
            cells_response_fixture=0,
            cells_synthetic=0,
            rankings_persisted=0,
        )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in raw_rows:
        key = (str(row.get("provider_id") or ""), str(row.get("capability") or ""))
        if not key[0] or not key[1]:
            continue
        grouped.setdefault(key, []).append(row)

    all_rankings: list[AgentRanking] = []
    cells_real = 0
    cells_fixture = 0
    cells_synth = 0
    for (provider_id, capability), rows in grouped.items():
        runs = [_run_from_raw(row) for row in rows]
        source = _classify_source(rows)
        if source == "real_adapter":
            cells_real += 1
        elif source == "response_fixture":
            cells_fixture += 1
        else:
            cells_synth += 1
        rankings_for_cell = compute_rankings(runs, weights=weights, source=source)
        all_rankings.extend(rankings_for_cell)

    store.save_rankings(all_rankings)
    logger.info(
        "rebuild_rankings: runs=%d cells=%d real=%d fixture=%d synthetic=%d rankings=%d",
        len(raw_rows),
        len(grouped),
        cells_real,
        cells_fixture,
        cells_synth,
        len(all_rankings),
    )
    return RebuildReport(
        runs_loaded=len(raw_rows),
        cells_total=len(grouped),
        cells_real_adapter=cells_real,
        cells_response_fixture=cells_fixture,
        cells_synthetic=cells_synth,
        rankings_persisted=len(all_rankings),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _classify_source(rows: list[dict[str, Any]]) -> str:
    """Return the strongest source observed across the cell's runs.

    Priority: real_adapter > response_fixture > synthetic. We promote
    on the strongest single run rather than the modal one — if even
    one run is a real replay, the cell is "real" for the user-facing
    badge.
    """

    saw_real = False
    saw_fixture = False
    for row in rows:
        output = _nested_output(row)
        replayed_from = str(output.get("_replayed_from") or "")
        provenance = str(output.get("_provenance") or "")
        if replayed_from:
            saw_real = True
            continue
        if any(marker in provenance for marker in _REAL_ADAPTER_PROVENANCE_MARKERS):
            saw_real = True
            continue
        if any(marker in provenance for marker in _RESPONSE_FIXTURE_PROVENANCE_MARKERS):
            saw_fixture = True
    if saw_real:
        return "real_adapter"
    if saw_fixture:
        return "response_fixture"
    return "synthetic"


def _nested_output(row: dict[str, Any]) -> dict[str, Any]:
    """Reach ``run_json.response.output`` whether the row is flat or nested.

    ``BenchmarkStore.load_json`` merges the flattened columns with the
    nested ``run_json`` overlay, so either path may carry the
    provenance markers. We prefer the nested shape because that's
    where new writers put it, but fall back to the flattened
    ``output`` column for older rows.
    """

    response = row.get("response")
    if isinstance(response, dict):
        nested = response.get("output")
        if isinstance(nested, dict):
            return nested
    flat = row.get("output")
    if isinstance(flat, dict):
        return flat
    return {}


def _run_from_raw(row: dict[str, Any]) -> BenchmarkRun:
    """Reconstruct a :class:`BenchmarkRun` from one ``load_json`` row.

    We only need the fields :func:`compute_rankings` looks at —
    ``response.succeeded``, ``response.cost_usd``, ``response.latency_ms``,
    ``score.quality_score``, ``score.succeeded``, plus the identifying
    quartet. Other fields (``raw_response``, ``field_scores``) are
    populated with empty defaults; the ranking aggregator never reads
    them.
    """

    response_payload = row.get("response") if isinstance(row.get("response"), dict) else {}
    score_payload = row.get("score") if isinstance(row.get("score"), dict) else {}
    response = ProviderResponse(
        succeeded=bool(response_payload.get("succeeded", row.get("succeeded", False))),
        output=response_payload.get("output") if isinstance(response_payload.get("output"), dict) else None,
        cost_usd=float(response_payload.get("cost_usd", row.get("cost_usd", 0.0)) or 0.0),
        latency_ms=int(response_payload.get("latency_ms", row.get("latency_ms", 0)) or 0),
        error=response_payload.get("error") or row.get("error") or None,
        raw_response={},
    )
    field_scores: list[FieldScore] = []
    raw_field_scores = score_payload.get("field_scores")
    if isinstance(raw_field_scores, Iterable):
        for entry in raw_field_scores:
            if not isinstance(entry, dict):
                continue
            try:
                field_scores.append(
                    FieldScore(
                        field=str(entry.get("field", "")),
                        weight=float(entry.get("weight", 0.0) or 0.0),
                        score=float(entry.get("score", 0.0) or 0.0),
                        reason=str(entry.get("reason", "")),
                    )
                )
            except (TypeError, ValueError):
                # One malformed field score should not kill the entire
                # rebuild — log-and-skip is the right posture here.
                logger.debug("rebuild_rankings: skipping malformed field_score %r", entry)
    score = ScoreResult(
        quality_score=float(
            score_payload.get("quality_score", row.get("score", 0.0)) or 0.0
        ),
        succeeded=bool(score_payload.get("succeeded", row.get("succeeded", False))),
        field_scores=field_scores,
        reason=str(score_payload.get("reason", "")),
    )
    return BenchmarkRun(
        test_case_id=str(row.get("test_case_id") or ""),
        provider_id=str(row.get("provider_id") or ""),
        capability=str(row.get("capability") or ""),
        difficulty=str(row.get("difficulty") or ""),
        response=response,
        score=score,
        created_at=str(row.get("created_at") or ""),
    )
