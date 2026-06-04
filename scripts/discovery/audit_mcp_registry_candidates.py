#!/usr/bin/env python3
"""Retroactive cleanup pass over the MCP registry source.

Why this exists
---------------
The ``official_mcp_registry`` source historically accepted *every*
``isLatest`` server with a substring-matched capability. The MCP
registry is open-publish, so it accumulated obvious junk:

* school / tutorial / homework projects
  (``ai.smithery/aicastle-school-openai-api-agent-project``)
* test/demo/sandbox/playground servers
  (``vendor/server-test-001``, ``vendor/sandbox-mcp``)
* numeric-suffix copies that look like "I bumped the test until it
  shipped" entries (``...-project123123123``)
* ``-copy`` / ``-fork`` / ``-vN`` clones

The source now applies an ``_is_obvious_junk`` regex pre-filter at
ingest time, but rows already in the index from before the filter
existed are still surfaced to /goal until something demotes them. This
script applies the same regex pre-filter to every persisted MCP
registry row and (with ``--apply``) flips ``lifecycle_status`` to
``rejected`` so the same surfaces that hide rejected vendor_rss / HN
junk also hide registry junk.

Workflow
--------

    # Dry run (default): writes a report. Nothing in the DB changes.
    PYTHONPATH=apps/api python3 scripts/audit_mcp_registry_candidates.py \\
      --store "$PLANMYAGENTS_DISCOVERY_STORE_URL"

    # Review .planmyagents_runs/mcp-registry-audit-{stamp}.json. Then:
    PYTHONPATH=apps/api python3 scripts/audit_mcp_registry_candidates.py \\
      --store "$PLANMYAGENTS_DISCOVERY_STORE_URL" \\
      --apply

``--apply`` flips ``lifecycle_status='rejected'`` on the failing rows.
The rows stay in the table (auditable) but no surface that filters on
``lifecycle_status != 'rejected'`` will show them. The route-level
``CandidateJudge`` will catch any false negatives at retrieval time —
this script is purely about cleaning up obvious junk so the judge has
less noise to filter through.

Goal-independence
-----------------
Unlike the request-time ``CandidateJudge``, this script is
*goal-independent*: it judges names, not relevance. Use the judge for
"does X serve goal Y?" and use this script for "is X obvious junk that
should never have been ingested?".

Dead-URL detection (``--verify``)
----------------------------------
Pass ``--verify`` to run the existing ``verify_candidate()`` pipeline
(``planmyagents_api.discovery.verification``) against every kept entry.
That function fetches the candidate's ``evidence_url`` / ``vendor_url``
and returns ``status="unverified"`` with blocker ``"evidence_fetch_failed"``
when the URL is unreachable (including non-existent GitHub repos that
return 404). Entries that come back ``unverified`` are collected in a
``failed_verification`` section of the report and, with ``--apply``,
are also flipped to ``lifecycle_status='rejected'``.

The check is opt-in because it makes one network round-trip per kept
entry. Run it periodically (e.g. weekly) rather than on every audit.
The verification logic is centralised in ``verification.py`` — this
script does not duplicate it.
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

from planmyagents_api.discovery.models import DiscoveryCandidate  # noqa: E402
from planmyagents_api.discovery.sources.official_mcp_registry import (  # noqa: E402
    _is_obvious_junk,
)
from planmyagents_api.discovery.store import (  # noqa: E402
    RoutingDiscoveryStore,
    discovery_store_for_path,
)
from planmyagents_api.discovery.verification import verify_candidate  # noqa: E402

AUDITED_SOURCE = "official_mcp_registry"
REJECTED_LIFECYCLE = "rejected"
REJECTED_ROUTE_STATUS = "rejected_obvious_junk"
REJECTED_ROUTE_STATUS_UNVERIFIED = "rejected_unverified_url"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-classify existing MCP registry rows by name shape; "
            "report or reject obvious junk."
        )
    )
    parser.add_argument(
        "--store",
        default=os.getenv(
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            ".planmyagents_runs/discovery-store.sqlite",
        ),
        help=(
            "Discovery store path or Postgres URL. Defaults to "
            "PLANMYAGENTS_DISCOVERY_STORE_URL, then the local SQLite store."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Mutate the rejected rows in the store. Without this flag, "
            "the script only writes a dry-run report."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help=(
            "Output path for the JSON report. Defaults to "
            ".planmyagents_runs/mcp-registry-audit-YYYYMMDD-HHMMSS.json."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help=(
            "Stop after N candidates (0 = no limit). Useful for "
            "smoke-testing the regex filter on a sample."
        ),
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Run verify_candidate() against each kept entry to check that "
            "its evidence_url / vendor_url is reachable. Entries that come "
            "back unverified (e.g. 404 GitHub repos) are flagged in a "
            "'failed_verification' section and rejected with --apply. "
            "Opt-in because it makes one network round-trip per kept entry."
        ),
    )
    args = parser.parse_args()

    print(f"Loading candidates from {args.store} …", file=sys.stderr)
    store = discovery_store_for_path(args.store)
    candidates = _load_audit_targets(store)
    print(
        f"  found {len(candidates)} candidates from source={AUDITED_SOURCE}",
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

        # The display_name preserves the original namespaced server
        # name (e.g. "ai.smithery/aicastle-school-..."); the provider
        # id has been sanitized for storage. We feed the regex both,
        # so a clean display_name with a junk-shaped id still matches.
        haystacks = [candidate.display_name, candidate.id]
        is_junk = any(_is_obvious_junk(h) for h in haystacks if h)
        entry = _summarize(
            candidate,
            reason=(
                "name matches registry-junk regex"
                if is_junk
                else "name passes regex pre-filter"
            ),
            verdict="junk" if is_junk else "ok",
        )

        if is_junk:
            rejected.append(entry)
        else:
            kept.append(entry)

        if index % 50 == 0:
            print(
                f"  {index}/{len(candidates)} processed "
                f"({len(rejected)} junk, {len(kept)} kept)",
                file=sys.stderr,
            )

    # --- Optional verification pass (uses existing verify_candidate()) ---
    failed_verification: list[dict] = []
    if args.verify:
        print(
            f"Running verify_candidate() for {len(kept)} kept entries …",
            file=sys.stderr,
        )
        surviving_kept: list[dict] = []
        for idx, entry in enumerate(kept, start=1):
            # Re-load the full DiscoveryCandidate so verify_candidate()
            # has access to evidence_url, vendor_url, provider_type, etc.
            candidate = _candidate_by_id(store, entry["id"])
            if candidate is None:
                surviving_kept.append(entry)
                continue
            result = verify_candidate(candidate)
            if result.status == "unverified":
                failed_verification.append({
                    **entry,
                    "reason": f"verify_candidate: {', '.join(result.blockers) or 'unverified'}",
                    "verdict": "unverified_url",
                    "verification_blockers": result.blockers,
                })
            else:
                surviving_kept.append({**entry, "verification_status": result.status})
            if idx % 20 == 0:
                print(
                    f"  {idx}/{len(kept)} verified "
                    f"({len(failed_verification)} failed so far)",
                    file=sys.stderr,
                )
        kept = surviving_kept
        print(
            f"Verification complete: {len(failed_verification)} entries failed.",
            file=sys.stderr,
        )

    report = {
        "store": args.store,
        "audited_source": AUDITED_SOURCE,
        "generated_at": datetime.now(UTC).isoformat(),
        "totals": {
            "scanned": len(candidates),
            "kept": len(kept),
            "rejected": len(rejected),
            "failed_verification": len(failed_verification),
            "skipped_already_rejected": len(skipped_already),
        },
        "applied": args.apply,
        "kept_sample": kept[:50],
        "rejected": rejected,
        "failed_verification": failed_verification,
        "skipped_already_rejected_sample": skipped_already[:50],
    }

    report_path = args.report or _default_report_path()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"Wrote report → {report_path}", file=sys.stderr)

    if not args.apply:
        print(
            "Dry run. Re-run with --apply to flip "
            f"lifecycle_status to '{REJECTED_LIFECYCLE}' on the "
            f"{len(rejected)} flagged rows"
            + (
                f" and {len(failed_verification)} failed-verification rows"
                if failed_verification
                else ""
            )
            + ".",
            file=sys.stderr,
        )
        return 0

    all_junk_ids = sorted({entry["id"] for entry in rejected})
    all_unverified_ids = sorted({entry["id"] for entry in failed_verification})

    if not all_junk_ids and not all_unverified_ids:
        print(
            "Nothing to update — every audited row passes the regex"
            + (" and verification checks" if args.verify else "")
            + ".",
            file=sys.stderr,
        )
        return 0

    if all_junk_ids:
        print(
            f"Marking {len(all_junk_ids)} junk rows "
            f"lifecycle_status='{REJECTED_LIFECYCLE}' …",
            file=sys.stderr,
        )
        updated = _mark_rejected_in_store(
            store, args.store, all_junk_ids, route_status=REJECTED_ROUTE_STATUS
        )
        print(f"Updated {updated} junk rows.", file=sys.stderr)

    if all_unverified_ids:
        print(
            f"Marking {len(all_unverified_ids)} unverified rows "
            f"lifecycle_status='{REJECTED_LIFECYCLE}' …",
            file=sys.stderr,
        )
        updated = _mark_rejected_in_store(
            store,
            args.store,
            all_unverified_ids,
            route_status=REJECTED_ROUTE_STATUS_UNVERIFIED,
        )
        print(f"Updated {updated} unverified rows.", file=sys.stderr)

    return 0


def _mark_rejected_in_store(
    store: RoutingDiscoveryStore,
    store_url: str,
    provider_ids: list[str],
    *,
    route_status: str = REJECTED_ROUTE_STATUS,
) -> int:
    """Idempotently flip ``lifecycle_status`` to 'rejected'.

    For Postgres we issue a targeted UPDATE; other backends mutate
    in-memory and replace via the store's save-semantic API.
    """

    if not provider_ids:
        return 0
    if store_url.startswith(("postgresql://", "postgres://")):
        return _mark_rejected_postgres(store_url, provider_ids, route_status=route_status)
    return _mark_rejected_generic(store, provider_ids, route_status=route_status)


def _mark_rejected_postgres(
    dsn: str, provider_ids: list[str], *, route_status: str
) -> int:
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
                (REJECTED_LIFECYCLE, route_status, provider_ids),
            )
            updated = cursor.rowcount
        conn.commit()
    return updated


def _mark_rejected_generic(
    store: RoutingDiscoveryStore,
    provider_ids: list[str],
    *,
    route_status: str,
) -> int:
    targets = set(provider_ids)
    all_rows = store.load(include_rejected=True)
    next_rows: list[DiscoveryCandidate] = []
    mutated = 0
    for candidate in all_rows:
        if candidate.id in targets:
            next_rows.append(
                dataclasses.replace(
                    candidate,
                    lifecycle_status=REJECTED_LIFECYCLE,
                    route_status=route_status,
                    will_fail=True,
                )
            )
            mutated += 1
        else:
            next_rows.append(candidate)
    store.agentic_store.save(next_rows)
    return mutated


def _load_audit_targets(store: RoutingDiscoveryStore) -> list[DiscoveryCandidate]:
    all_candidates = store.load(include_rejected=True)
    return [c for c in all_candidates if c.source == AUDITED_SOURCE]


def _candidate_by_id(
    store: RoutingDiscoveryStore, provider_id: str
) -> DiscoveryCandidate | None:
    """Look up a single candidate by id from the store."""
    all_rows = store.load(include_rejected=True)
    for candidate in all_rows:
        if candidate.id == provider_id:
            return candidate
    return None


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
    return Path(".planmyagents_runs") / f"mcp-registry-audit-{stamp}.json"


if __name__ == "__main__":
    raise SystemExit(main())
