#!/usr/bin/env python3
"""Mark a verified discovery candidate as ready for registry promotion."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.discovery.models import DiscoveryCandidate  # noqa: E402
from planmyagents_api.discovery.store import discovery_store_for_path  # noqa: E402
from planmyagents_api.registry.promotions import (  # noqa: E402
    adapter_module_for,
    candidate_to_agent,
    is_promotion_ready,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote a verified DB discovery candidate into ready-for-promotion state."
    )
    parser.add_argument(
        "--store",
        default=os.getenv("PLANMYAGENTS_DISCOVERY_STORE_URL", ""),
        help="Discovery store URL/path.",
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument(
        "--adapter-module",
        default="",
        help="Import path in module:Class form, e.g. planmyagents_api.agents.hunter:HunterEmailVerifier.",
    )
    parser.add_argument("--env-var", action="append", default=[])
    parser.add_argument("--verification-status", default="capability_verified")
    parser.add_argument("--benchmark-status", default="passed")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.store:
        parser.error("--store or PLANMYAGENTS_DISCOVERY_STORE_URL is required")

    store = discovery_store_for_path(args.store)
    candidates = store.load()
    promoted: DiscoveryCandidate | None = None
    updated: list[DiscoveryCandidate] = []
    for candidate in candidates:
        if candidate.id != args.candidate_id:
            updated.append(candidate)
            continue
        adapter_module = args.adapter_module or adapter_module_for(candidate)
        promoted = DiscoveryCandidate(
            **{
                **candidate.__dict__,
                "required_env_vars": sorted(set(args.env_var or candidate.required_env_vars)),
                "verification_status": args.verification_status,
                "lifecycle_status": "configured",
                "route_status": "ready_for_promotion",
                "will_fail": False,
                "will_fail_reasons": [],
                "adapter_module": adapter_module,
                "benchmark_status": args.benchmark_status,
            }
        )
        updated.append(promoted)

    if not promoted:
        print(json.dumps({"ok": False, "error": f"candidate not found: {args.candidate_id}"}, indent=2))
        return 1

    ready = is_promotion_ready(promoted)
    payload = {
        "ok": ready,
        "candidate_id": promoted.id,
        "ready_for_promotion": ready,
        "candidate": promoted.to_registry_json(),
        "generated_agent": candidate_to_agent(promoted) if ready else None,
    }
    if ready and not args.dry_run:
        store.save(updated)
        payload["saved"] = True
    else:
        payload["saved"] = False
    print(json.dumps(payload, indent=2))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
