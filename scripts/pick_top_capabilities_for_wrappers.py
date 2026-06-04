#!/usr/bin/env python3
"""Pick the top N capabilities to invest wrapper-engineering hours in.

Sprint 3 (S3a-1):
    Inputs the persisted demand store + the curated provider catalog
    + the registry, and outputs a ranked list of capabilities where:

      1. Real users have asked for it (request_count > 0)
      2. At least one credible commodity provider exists (so a
         wrapper is feasible without inventing a market)
      3. The capability is currently NOT routable (adapter_module=none
         in agents.json) — these are the gaps wrapping work would
         actually close.

Why this exists
---------------
Sprint 3a invests human engineering hours building executable
wrappers for paid third-party APIs (Stripe, Hunter, etc). Picking
"the right 5 capabilities" is the single highest-leverage decision
of that sprint — every wrapper takes ~6 hours, and a bad pick
means we spent a day building infrastructure for a capability
nobody needs. This script makes that decision auditable: the
output is a ranked report, not a single number.

Inputs
------
* ``PLANMYAGENTS_DEMAND_STORE_PATH`` (env, defaults to
  ``apps/data/capability_demand_events.jsonl``) — the persisted
  demand store. JSON or Postgres are both supported.
* ``packages/registry/agents.json`` — the canonical agent registry,
  used to identify which capabilities have routable agents today.
* ``packages/discovery/sources/curated_ai_agents.json`` — the
  curated commodity provider catalog, used to identify which
  capabilities have at least one wrappable provider.

Output
------
Plain text report on stdout. Pipe through ``head -n N`` if you
only want the top N rows.

Usage
-----
::

    .venv/bin/python scripts/pick_top_capabilities_for_wrappers.py
    .venv/bin/python scripts/pick_top_capabilities_for_wrappers.py --top 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))


def _load_registry(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_curated(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    return payload.get("ai_agents", [])


def _routable_capabilities(registry: dict[str, Any]) -> set[str]:
    """Capabilities that have at least one agent with a real adapter
    module wired up. Anything with ``adapter_module=none`` is
    considered NOT routable — it can't actually execute today."""
    routable: set[str] = set()
    for agent in registry.get("agents", []):
        adapter = str(agent.get("adapter_module") or "").strip().lower()
        if adapter in {"", "none"}:
            continue
        for cap in agent.get("capabilities", []):
            cap_id = str(cap if isinstance(cap, str) else cap.get("id") or "")
            if cap_id:
                routable.add(cap_id)
    return routable


def _wrappable_capabilities(curated: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map capability_id -> list of provider names that supply it
    via a verified third-party API. These are the only capabilities
    where 'build a wrapper' is actually a defined task."""
    out: dict[str, list[str]] = {}
    for entry in curated:
        provider = str(entry.get("name") or "").strip()
        for cap in entry.get("capabilities", []):
            out.setdefault(str(cap), []).append(provider)
    return out


def _load_demand_summary() -> list[dict[str, Any]]:
    """Load demand summaries from whatever store
    ``PLANMYAGENTS_DEMAND_STORE_PATH`` points to.

    Falls back to an empty list if the store is empty or missing —
    we still want a report, even if it ends up driven entirely by
    the wrappable-providers heuristic.
    """
    from planmyagents_api.discovery.demand_recorder import resolve_demand_store_path
    from planmyagents_api.discovery.demand_store import demand_store_for_path

    path = resolve_demand_store_path()
    try:
        store = demand_store_for_path(path)
    except Exception as exc:  # noqa: BLE001 — diagnostic script
        print(f"[demand_store] failed to open {path!r}: {exc}", file=sys.stderr)
        return []
    summaries = store.summarize(sample_goals_per_capability=2)
    return [s.to_json() for s in summaries]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pick top capabilities for wrapper investment (S3a-1).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="How many capabilities to print (default: 10).",
    )
    parser.add_argument(
        "--registry-path",
        default=str(ROOT / "packages" / "registry" / "agents.json"),
        help="Path to agents.json.",
    )
    parser.add_argument(
        "--curated-path",
        default=str(ROOT / "packages" / "discovery" / "sources" / "curated_ai_agents.json"),
        help="Path to curated commodity provider catalog.",
    )
    args = parser.parse_args()

    registry = _load_registry(Path(args.registry_path))
    routable = _routable_capabilities(registry)
    curated = _load_curated(Path(args.curated_path))
    wrappable = _wrappable_capabilities(curated)
    demand_summaries = _load_demand_summary()
    demand_by_capability: dict[str, dict[str, Any]] = {
        s["capability_id"]: s for s in demand_summaries
    }

    rows: list[dict[str, Any]] = []
    for capability_id, providers in wrappable.items():
        is_routable = capability_id in routable
        demand = demand_by_capability.get(capability_id, {})
        request_count = int(demand.get("request_count") or 0)
        distinct_requesters = int(demand.get("distinct_requester_count") or 0)
        # Score: rank capabilities that have BOTH demand AND wrappable
        # providers AND no routable agent today. Falls back to provider
        # count when demand is empty (early-stage prioritisation).
        score = 0
        if not is_routable:
            score += 1000
        score += request_count * 50
        score += distinct_requesters * 25
        score += len(providers)
        rows.append(
            {
                "capability_id": capability_id,
                "score": score,
                "request_count": request_count,
                "distinct_requesters": distinct_requesters,
                "wrappable_providers": providers,
                "routable_today": is_routable,
                "sample_goals": demand.get("sample_goals", []),
            }
        )

    rows.sort(key=lambda r: r["score"], reverse=True)

    # Header
    print()
    print("=" * 78)
    print(" S3a-1: Top capabilities for wrapper investment ")
    print("=" * 78)
    print()
    print(f"Source: demand store + {len(curated)} curated providers + agents.json")
    print(f"Routable capabilities today (have a real adapter): {len(routable)}")
    print(f"Wrappable capabilities total (curated provider exists): {len(wrappable)}")
    print(f"Demand events on file: {sum(r['request_count'] for r in rows)}")
    print()

    for rank, row in enumerate(rows[: args.top], start=1):
        marker = "[ROUTABLE]" if row["routable_today"] else "[GAP]"
        print(f"{rank:>2}. {marker} {row['capability_id']}")
        print(
            f"    score={row['score']}  "
            f"requests={row['request_count']}  "
            f"distinct_users={row['distinct_requesters']}  "
            f"wrappable_providers={len(row['wrappable_providers'])}"
        )
        if row["wrappable_providers"]:
            preview = ", ".join(row["wrappable_providers"][:5])
            extra = ""
            if len(row["wrappable_providers"]) > 5:
                extra = f" (+{len(row['wrappable_providers']) - 5} more)"
            print(f"    providers: {preview}{extra}")
        if row["sample_goals"]:
            for goal in row["sample_goals"][:1]:
                trimmed = goal[:90] + ("..." if len(goal) > 90 else "")
                print(f"    sample: {trimmed!r}")
        print()

    # Final pick callout — the whole point of the script.
    picks = [r for r in rows if not r["routable_today"]][:5]
    print("-" * 78)
    print("Recommended Sprint 3a wrapper picks (top 5 unrouted capabilities):")
    for pick in picks:
        print(f"  - {pick['capability_id']:30s}  "
              f"providers={len(pick['wrappable_providers'])}  "
              f"requests={pick['request_count']}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
