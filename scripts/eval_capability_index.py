#!/usr/bin/env python3
"""Evaluate the CapabilityIndex against a labelled goal dataset.

Sprint 2 (S2-PAR-3):
    Sweeps similarity thresholds over a labelled goals file (default:
    ``apps/api/tests/eval/goals.jsonl``) and reports precision / recall
    / F1 per threshold so we can pick a defensible production cutoff
    without hand-eyeballing log lines.

Why this exists
---------------
With the swap from the deterministic hash embedder to nomic-embed-text
(or OpenAI), the score distribution shifted by an order of magnitude
and the old 0.30 threshold became meaningless. Picking 0.55 as a new
default was an educated guess from a handful of bios. This script
makes that guess auditable by:

  - Running the *currently configured* embedder (``embedder_from_env``)
    end-to-end against the labelled goals file, so the numbers you see
    are exactly what production will see when a planner submits the
    same goal text.
  - Sweeping thresholds 0.30..0.85 in 0.05 increments so the precision /
    recall curve is visible, not just one point.
  - Treating *out-of-domain* goals (those with ``expected_capabilities``
    = ``[]``) as a precision test — any inference on those counts as a
    false positive. Without this, a noisy embedder could trivially get
    100% recall by matching everything.

Metric definitions
------------------
  - True positive (TP):  capability slug appears in BOTH expected and
    inferred.
  - False positive (FP): capability slug appears in inferred but NOT
    in expected.
  - False negative (FN): capability slug appears in expected but NOT
    in inferred.

  - Precision = TP / (TP + FP)
  - Recall    = TP / (TP + FN)
  - F1        = 2 * P * R / (P + R)

Out-of-domain goals contribute only to FP (inferred slugs are wrong by
definition because the goal has no valid registry capability).

Usage
-----
    # Sweep with the env-configured embedder, default goals file.
    python scripts/eval_capability_index.py

    # Sweep over a specific goals file.
    python scripts/eval_capability_index.py --goals path/to/goals.jsonl

    # Force the deterministic hash embedder (CI-stable baseline).
    python scripts/eval_capability_index.py --embedder hash

    # Sweep at finer granularity.
    python scripts/eval_capability_index.py --threshold-start 0.40 \\
        --threshold-end 0.80 --threshold-step 0.02

    # Verbose per-goal breakdown (helpful when picking the threshold).
    python scripts/eval_capability_index.py --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.discovery.capability_index import (  # noqa: E402
    CapabilityIndex,
)
from planmyagents_api.discovery.embeddings import (  # noqa: E402
    DeterministicHashEmbedder,
    Embedder,
    embedder_from_env,
)
from planmyagents_api.registry.loader import load_registry  # noqa: E402

DEFAULT_GOALS_PATH = ROOT / "apps" / "api" / "tests" / "eval" / "goals.jsonl"
DEFAULT_REGISTRY_PATH = ROOT / "packages" / "registry" / "agents.json"


def _load_goals(path: Path) -> list[dict]:
    """Load JSONL goal records. Each record needs ``id``, ``goal``,
    ``expected_capabilities`` (list[str]).

    We're lax about extra fields (``notes`` etc.) but strict about the
    three required ones — a typo in the labels file should fail loud,
    not silently shrink the eval set.
    """

    if not path.exists():
        raise FileNotFoundError(f"Goals file not found: {path}")
    goals: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{lineno}: malformed JSON line: {exc}"
                ) from exc
            for field in ("id", "goal", "expected_capabilities"):
                if field not in record:
                    raise ValueError(
                        f"{path}:{lineno}: missing required field '{field}'"
                    )
            if not isinstance(record["expected_capabilities"], list):
                raise ValueError(
                    f"{path}:{lineno}: expected_capabilities must be a list"
                )
            goals.append(record)
    return goals


def _select_embedder(name: str) -> Embedder:
    """Resolve the ``--embedder`` CLI flag.

    ``env`` (default) defers to ``embedder_from_env()`` which honours
    the standard priority (OpenAI -> Ollama -> hash). ``hash`` is
    useful for a CI-stable baseline that doesn't depend on a running
    Ollama daemon or an OpenAI key.
    """

    name = (name or "env").strip().lower()
    if name == "env":
        return embedder_from_env()
    if name == "hash":
        return DeterministicHashEmbedder()
    raise ValueError(
        f"Unknown embedder selector: {name!r} (expected 'env' or 'hash')"
    )


def _frange(start: float, end: float, step: float) -> list[float]:
    """``range`` for floats, inclusive on the right within rounding noise.

    Standard ``numpy.arange`` would do this in one line but the script
    intentionally has zero non-stdlib deps so it can be run in any
    environment without a venv setup.
    """

    if step <= 0:
        raise ValueError("threshold step must be > 0")
    out: list[float] = []
    cur = start
    while cur <= end + 1e-9:
        out.append(round(cur, 4))
        cur += step
    return out


def _evaluate_at_threshold(
    *,
    index: CapabilityIndex,
    goals: list[dict],
    threshold: float,
    verbose: bool,
) -> dict:
    """Run the index against every goal at a single threshold and return
    aggregated TP / FP / FN counts plus per-goal breakdown.

    The per-goal breakdown is needed for the verbose output mode so
    operators tuning the threshold can see WHICH goals flipped from
    correct to wrong as the threshold moved.
    """

    tp = fp = fn = 0
    per_goal: list[dict] = []
    for goal in goals:
        expected: set[str] = set(goal["expected_capabilities"])
        inferred: set[str] = index.infer_from_text(
            goal["goal"], threshold=threshold
        )
        goal_tp = expected & inferred
        goal_fp = inferred - expected
        goal_fn = expected - inferred
        tp += len(goal_tp)
        fp += len(goal_fp)
        fn += len(goal_fn)
        if verbose:
            per_goal.append(
                {
                    "id": goal["id"],
                    "goal": goal["goal"],
                    "expected": sorted(expected),
                    "inferred": sorted(inferred),
                    "tp": sorted(goal_tp),
                    "fp": sorted(goal_fp),
                    "fn": sorted(goal_fn),
                }
            )

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        (2 * precision * recall) / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return {
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "per_goal": per_goal,
    }


def _print_summary_table(rows: list[dict]) -> None:
    """Pretty-print one row per threshold so the precision/recall curve
    fits on a single screen.

    Highlights the best F1 row with a ``*`` marker — that's the
    candidate for the new ``DEFAULT_MATCH_THRESHOLD``. We don't auto-
    update the constant; picking a threshold is a judgement call
    (a higher precision setting may be preferable when the downstream
    judge is expensive).
    """

    if not rows:
        print("(no thresholds evaluated)")
        return

    best_f1 = max(row["f1"] for row in rows)
    print(
        f"{'thr':>5}  {'TP':>4}  {'FP':>4}  {'FN':>4}  "
        f"{'precision':>10}  {'recall':>7}  {'F1':>6}"
    )
    print("-" * 55)
    for row in rows:
        marker = " *" if abs(row["f1"] - best_f1) < 1e-9 else "  "
        print(
            f"{row['threshold']:>5.2f}  "
            f"{row['tp']:>4d}  {row['fp']:>4d}  {row['fn']:>4d}  "
            f"{row['precision']:>10.3f}  {row['recall']:>7.3f}  "
            f"{row['f1']:>6.3f}{marker}"
        )
    print("-" * 55)
    print("(* = best F1 across the swept thresholds)")


def _print_verbose_breakdown(rows: list[dict], goals: list[dict]) -> None:
    """Per-goal correctness at each threshold. Volume can get large
    (30 goals * 12 thresholds = 360 lines), but it's the only way to
    see WHICH goals are robust vs flaky as the threshold moves.

    Also prints per-goal stability — how many of the swept thresholds
    produce the *correct* set for each goal. Stable wins (every
    threshold correct) and stable losses (no threshold ever correct)
    point at registry / labels gaps; unstable goals point at the
    threshold itself.
    """

    print()
    print("Per-goal correctness across thresholds (✓ = exact match):")
    print()
    header = "  goal_id      " + "  ".join(
        f"{row['threshold']:>4.2f}" for row in rows
    )
    print(header)
    print("-" * len(header))

    # Index per-goal entries by (goal_id, threshold) so the column
    # walk below is O(goals) per threshold instead of O(goals * rows).
    by_goal_id: dict[str, dict[float, dict]] = {}
    for row in rows:
        for entry in row["per_goal"]:
            by_goal_id.setdefault(entry["id"], {})[row["threshold"]] = entry

    for goal in goals:
        marks = []
        for row in rows:
            entry = by_goal_id.get(goal["id"], {}).get(row["threshold"])
            if not entry:
                marks.append("  -- ")
                continue
            expected = set(entry["expected"])
            inferred = set(entry["inferred"])
            if inferred == expected:
                marks.append("  ✓ ")
            elif expected.issubset(inferred):
                marks.append("  +R")
            elif expected & inferred:
                marks.append("  ~ ")
            else:
                marks.append("  ✗ ")
        print(f"  {goal['id']:<12} " + "  ".join(marks))
    print()
    print(
        "  legend: ✓ exact | +R recall-only (extra slugs leaked) | "
        "~ partial | ✗ no overlap"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sweep similarity thresholds for the CapabilityIndex."
    )
    parser.add_argument(
        "--goals",
        type=Path,
        default=DEFAULT_GOALS_PATH,
        help=f"Path to JSONL goals file (default: {DEFAULT_GOALS_PATH})",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY_PATH,
        help=(
            "Path to the registry JSON whose capability list is used to "
            "build the index (default: packages/registry/agents.json)"
        ),
    )
    parser.add_argument(
        "--embedder",
        choices=("env", "hash"),
        default="env",
        help=(
            "Which embedder to evaluate. 'env' (default) honours the "
            "standard env priority (OpenAI -> Ollama -> hash); 'hash' "
            "forces the deterministic hash embedder for a CI-stable "
            "baseline."
        ),
    )
    parser.add_argument(
        "--threshold-start", type=float, default=0.30, help="default 0.30"
    )
    parser.add_argument(
        "--threshold-end", type=float, default=0.85, help="default 0.85"
    )
    parser.add_argument(
        "--threshold-step", type=float, default=0.05, help="default 0.05"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-goal breakdown after the summary table.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emit the full evaluation as a single JSON document on "
            "stdout instead of the human-readable tables. Useful for "
            "feeding the CI/grafana pipeline."
        ),
    )
    args = parser.parse_args()

    goals = _load_goals(args.goals)
    registry = load_registry(args.registry)
    capability_ids = [str(c) for c in registry.get("capabilities", []) if c]

    # The eval is deliberately built fresh (no cache) per run so a
    # mid-session env flip (export PLANMYAGENTS_EMBEDDING_PROVIDER=...)
    # is reflected immediately without process restart.
    embedder = _select_embedder(args.embedder)
    index = CapabilityIndex(capability_ids, embedder=embedder)

    thresholds = _frange(
        args.threshold_start, args.threshold_end, args.threshold_step
    )
    rows = [
        _evaluate_at_threshold(
            index=index, goals=goals, threshold=thr, verbose=args.verbose
        )
        for thr in thresholds
    ]

    if args.json:
        payload = {
            "embedder": embedder.name,
            "registry_path": str(args.registry),
            "goals_path": str(args.goals),
            "n_goals": len(goals),
            "n_in_domain_goals": sum(
                1 for g in goals if g["expected_capabilities"]
            ),
            "n_out_of_domain_goals": sum(
                1 for g in goals if not g["expected_capabilities"]
            ),
            "results": rows,
        }
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    print(f"Embedder:      {embedder.name}")
    print(f"Registry:      {args.registry}")
    print(f"Goals file:    {args.goals}  ({len(goals)} goals)")
    print(
        f"  in-domain:   "
        f"{sum(1 for g in goals if g['expected_capabilities'])}"
    )
    print(
        f"  out-of-dom:  "
        f"{sum(1 for g in goals if not g['expected_capabilities'])}"
    )
    print(
        f"Capability set size: {len(capability_ids)} "
        f"(production threshold default: {index.match_threshold:.2f})"
    )
    print()
    _print_summary_table(rows)
    if args.verbose:
        _print_verbose_breakdown(rows, goals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
