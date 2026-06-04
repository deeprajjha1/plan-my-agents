#!/usr/bin/env python3
"""Audit existing vendor_rss + hacker_news candidates with the smart classifier.

Why this exists:
- The vendor_rss and hacker_news scouts emit `provider_type: ai_agent`
  for every keyword-matching RSS/HN item, plus a `semantic_search`
  catch-all when no specific capability matched. That polluted the
  discovery_candidates table with reddit threads, opinion pieces, and
  corporate blog posts that aren't agents at all.
- The classifier in `planmyagents_api.discovery.agent_classifier` is the same
  filter the ingestion path now uses to block new junk. This script
  runs it across rows that were already saved before the filter
  existed.

Workflow:

    # Dry-run (default): writes a report. Nothing in the DB changes.
    PYTHONPATH=apps/api python3 scripts/audit_rss_hn_candidates.py \
      --store "$PLANMYAGENTS_DISCOVERY_STORE_URL"

    # Review .planmyagents_runs/rss-hn-audit-{date}.json. Then:
    PYTHONPATH=apps/api python3 scripts/audit_rss_hn_candidates.py \
      --store "$PLANMYAGENTS_DISCOVERY_STORE_URL" \
      --apply

`--apply` flips `lifecycle_status` to `rejected` on the failing rows.
The rows stay in the table (auditable) but no surface that filters on
`lifecycle_status != 'rejected'` will show them.

LLM arbitration is on by default and uses local Qwen via Ollama. Pass
`--no-llm` to skip the LLM layer entirely (faster, but the classifier
will reject all heuristic-uncertain items).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.agent_classifier import (  # noqa: E402
    classify_candidate,
)
from planmyagents_api.discovery.models import DiscoveryCandidate  # noqa: E402
from planmyagents_api.discovery.store import (  # noqa: E402
    RoutingDiscoveryStore,
    discovery_store_for_path,
)

AUDITED_SOURCES = frozenset({"vendor_rss", "hacker_news_agent_watch"})
REJECTED_LIFECYCLE = "rejected"
REJECTED_ROUTE_STATUS = "rejected_not_an_agent"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-classify existing vendor_rss + HN rows; report or reject.",
    )
    parser.add_argument(
        "--store",
        default=os.getenv(
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            ".planmyagents_runs/discovery-store.sqlite",
        ),
        help="Discovery store path or Postgres URL. Defaults to "
        "PLANMYAGENTS_DISCOVERY_STORE_URL, then the local SQLite store.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mutate the rejected rows in the store. Without this flag, "
        "the script only writes a dry-run report.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the LLM arbiter for ambiguous items. Conservative — "
        "the classifier rejects anything the heuristic couldn't decide.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Output path for the JSON report. Defaults to "
        ".planmyagents_runs/rss-hn-audit-YYYYMMDD-HHMMSS.json.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after N candidates (0 = no limit). Useful when smoke-"
        "testing the classifier against a noisy LLM.",
    )
    args = parser.parse_args()

    client = None if args.no_llm else _build_llm_client_or_none()

    print(f"Loading candidates from {args.store} …", file=sys.stderr)
    store = discovery_store_for_path(args.store)
    candidates = _load_audit_targets(store)
    print(
        f"  found {len(candidates)} candidates from "
        f"{sorted(AUDITED_SOURCES)}",
        file=sys.stderr,
    )

    if args.limit and args.limit > 0:
        candidates = candidates[: args.limit]

    rejected: list[dict] = []
    kept: list[dict] = []
    skipped_already: list[dict] = []

    for index, candidate in enumerate(candidates, start=1):
        if candidate.lifecycle_status == REJECTED_LIFECYCLE:
            skipped_already.append(_summarize(candidate, reason="already rejected"))
            continue

        verdict = classify_candidate(
            title=candidate.display_name,
            url=candidate.vendor_url or candidate.evidence_url,
            description=_description_from_candidate(candidate),
            llm_client=client,
        )

        entry = _summarize(
            candidate,
            reason=verdict.reason,
            verdict=verdict.verdict,
            confidence=verdict.confidence,
            classifier_source=verdict.source,
        )

        if verdict.verdict == "agent":
            kept.append(entry)
        else:
            rejected.append(entry)

        if index % 25 == 0:
            print(
                f"  {index}/{len(candidates)} processed "
                f"({len(rejected)} rejects, {len(kept)} kept)",
                file=sys.stderr,
            )

    report = {
        "store": args.store,
        "audited_sources": sorted(AUDITED_SOURCES),
        "generated_at": datetime.now(UTC).isoformat(),
        "totals": {
            "scanned": len(candidates),
            "kept": len(kept),
            "rejected": len(rejected),
            "skipped_already_rejected": len(skipped_already),
        },
        "llm_used": client is not None,
        "applied": args.apply,
        "kept": kept,
        "rejected": rejected,
        "skipped_already_rejected": skipped_already,
    }

    report_path = args.report or _default_report_path()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"Wrote report → {report_path}", file=sys.stderr)

    if not args.apply:
        print(
            "Dry run. Re-run with --apply to flip lifecycle_status to "
            f"'{REJECTED_LIFECYCLE}' on the {len(rejected)} flagged rows.",
            file=sys.stderr,
        )
        return 0

    if not rejected:
        print("Nothing to update — every audited row is an agent.", file=sys.stderr)
        return 0

    rejected_ids = sorted({entry["id"] for entry in rejected})
    print(
        f"Marking {len(rejected_ids)} rows lifecycle_status='{REJECTED_LIFECYCLE}' …",
        file=sys.stderr,
    )
    updated = _mark_rejected_in_store(store, args.store, rejected_ids)
    print(f"Updated {updated} rows.", file=sys.stderr)
    return 0


def _mark_rejected_in_store(
    store: RoutingDiscoveryStore, store_url: str, provider_ids: list[str]
) -> int:
    """Idempotently flip `lifecycle_status` to 'rejected' for the given ids.

    Bypasses `save_merge` because `merge_candidates` preserves the
    existing lifecycle_status (it's designed to absorb new sightings of
    a known candidate, not to demote it). For postgres we issue a
    targeted UPDATE; for other backends we mutate in-memory and call
    the underlying store's replace-semantic `.save()`.
    """

    if not provider_ids:
        return 0

    if store_url.startswith(("postgresql://", "postgres://")):
        return _mark_rejected_postgres(store_url, provider_ids)

    return _mark_rejected_generic(store, provider_ids)


def _mark_rejected_postgres(dsn: str, provider_ids: list[str]) -> int:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE discovery_candidates
                   SET lifecycle_status = %s,
                       route_status    = %s,
                       will_fail       = TRUE
                 WHERE provider_id = ANY(%s)
                """,
                (REJECTED_LIFECYCLE, REJECTED_ROUTE_STATUS, provider_ids),
            )
            updated = cursor.rowcount
        conn.commit()
    return updated


def _mark_rejected_generic(
    store: RoutingDiscoveryStore, provider_ids: list[str]
) -> int:
    """SQLite + JSON path: load all rows (including rejected), mutate
    the matching ones, save the whole set back via the underlying
    agentic store (replace-semantic)."""

    targets = set(provider_ids)
    all_rows = store.load(include_rejected=True)
    mutated = 0
    next_rows: list[DiscoveryCandidate] = []
    for candidate in all_rows:
        if candidate.id in targets:
            next_rows.append(
                dataclasses.replace(
                    candidate,
                    lifecycle_status=REJECTED_LIFECYCLE,
                    route_status=REJECTED_ROUTE_STATUS,
                    will_fail=True,
                )
            )
            mutated += 1
        else:
            next_rows.append(candidate)
    store.agentic_store.save(next_rows)
    return mutated


def _build_llm_client_or_none():
    try:
        from planmyagents_api.planner.local_qwen import OllamaQwenClient

        return OllamaQwenClient()
    except Exception:  # noqa: BLE001 — never block the audit on LLM setup
        return None


def _load_audit_targets(store: RoutingDiscoveryStore) -> list[DiscoveryCandidate]:
    """Pull every candidate (including already-rejected ones, so reruns
    surface what was previously flagged), then keep only the ones from
    the noisy sources."""

    all_candidates = store.load(include_rejected=True)
    return [c for c in all_candidates if c.source in AUDITED_SOURCES]


def _description_from_candidate(candidate: DiscoveryCandidate) -> str:
    """Best-effort description used to feed the classifier. We don't
    persist the raw RSS description today, so fall back to capability
    notes when present."""

    bits: list[str] = []
    for cap in candidate.capabilities:
        note = getattr(cap, "note", None) or getattr(cap, "notes", None) or ""
        if note:
            bits.append(str(note))
    return " ".join(bits)[:1000]


def _summarize(candidate: DiscoveryCandidate, **extra) -> dict:
    return {
        "id": candidate.id,
        "display_name": candidate.display_name[:200],
        "vendor": candidate.vendor,
        "vendor_url": candidate.vendor_url,
        "provider_type": candidate.provider_type,
        "source": candidate.source,
        "lifecycle_status": candidate.lifecycle_status,
        "capabilities": [c.id for c in candidate.capabilities],
        **extra,
    }


def _default_report_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return Path(".planmyagents_runs") / f"rss-hn-audit-{stamp}.json"


if __name__ == "__main__":
    raise SystemExit(main())
