#!/usr/bin/env python3
"""LLM-backed pass over candidate docs pages to fill auth/install/usage info.

This script reads the configured discovery store and asks a chat model to
extract structured ``docs`` for any candidate that has an ``evidence_url`` but
no ``docs`` populated yet. It writes back to the store. Run on a small
``--limit`` first to sanity-check model output before unleashing it on the
whole store.

Default model: whatever ``OllamaQwenClient`` resolves to (usually local Qwen).
A Groq client lands in the planner milestone and can be selected by setting
``PLANMYAGENTS_DOCS_CHAT_BACKEND=groq``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.discovery.enrichers.docs_extractor import (  # noqa: E402
    DocsExtractorEnricher,
)
from planmyagents_api.discovery.store import discovery_store_for_path  # noqa: E402
from planmyagents_api.planner.local_qwen import OllamaQwenClient  # noqa: E402

LOGGER = logging.getLogger("docs_extractor")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        default=os.getenv(
            "PLANMYAGENTS_DISCOVERY_STORE_URL",
            str(ROOT / ".planmyagents_runs" / "discovery-store.sqlite"),
        ),
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument(
        "--reextract-populated",
        action="store_true",
        help="Re-extract docs for candidates that already have docs populated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without persisting changes.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    store = discovery_store_for_path(args.store)
    candidates = store.load()
    targets = [c for c in candidates if c.evidence_url or c.vendor_url]
    if not args.reextract_populated:
        targets = [c for c in targets if c.docs.is_empty]
    if args.limit > 0:
        targets = targets[: args.limit]
    if not targets:
        LOGGER.info("nothing to extract (0 candidates without docs)")
        return 0
    LOGGER.info("extracting docs for %d candidates", len(targets))

    enricher = DocsExtractorEnricher(
        chat_client=OllamaQwenClient(),
        timeout_seconds=args.timeout,
        skip_when_already_populated=not args.reextract_populated,
    )
    enriched = enricher.enrich(targets)

    populated = sum(1 for c in enriched if not c.docs.is_empty)
    LOGGER.info("docs extraction complete: %d/%d gained docs", populated, len(targets))

    if args.dry_run:
        for candidate in enriched:
            if candidate.docs.is_empty:
                continue
            LOGGER.info(
                "would update %s -> auth=%s steps=%d examples=%d",
                candidate.id,
                candidate.docs.auth_method or "<none>",
                len(candidate.docs.install_steps),
                len(candidate.docs.usage_examples),
            )
        return 0

    enriched_by_id = {c.id: c for c in enriched}
    rebuilt = [enriched_by_id.get(c.id, c) for c in candidates]
    store.save(rebuilt)
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
