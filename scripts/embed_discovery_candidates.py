#!/usr/bin/env python3
"""Backfill embeddings for discovery candidates in a Postgres store.

Sprint 2 (S2-PAR-2):
    Idempotent + chunked. Re-running is safe — rows that already have
    a non-NULL embedding are skipped unless ``--force`` is passed.
    Chunked via ``--chunk-size`` so large stores don't materialise
    every candidate in memory or hold a single transaction open for
    the whole scan.

Backend selection follows the standard ``embedder_from_env()`` priority
documented in ``apps/api/planmyagents_api/discovery/embeddings.py``:

  1. ``PLANMYAGENTS_EMBEDDING_PROVIDER=openai`` + ``OPENAI_API_KEY`` -> OpenAI
  2. ``PLANMYAGENTS_EMBEDDING_MODEL`` set                            -> Ollama
  3. otherwise                                                       -> hash

CLI flags override the env in the usual way; the helper functions
below mutate ``os.environ`` so the env-driven factory still works.

Common usage:

    # Backfill everything new with whatever the env says.
    python scripts/embed_discovery_candidates.py \\
        --store postgresql://planmyagents:planmyagents@localhost:5432/planmyagents

    # Force re-encode (e.g. after switching embedders).
    python scripts/embed_discovery_candidates.py --force --chunk-size 50 ...

    # Dry-run to see how many candidates would be encoded.
    python scripts/embed_discovery_candidates.py --dry-run ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.discovery.embeddings import (  # noqa: E402
    DEFAULT_EMBEDDING_DIM,
    EmbedderError,
    embedder_from_env,
)
from planmyagents_api.discovery.service import (  # noqa: E402
    candidate_text_for_embedding,
)
from planmyagents_api.discovery.store import PostgresDiscoveryStore  # noqa: E402


def _apply_cli_env(args: argparse.Namespace) -> None:
    """Push CLI overrides into the env so ``embedder_from_env()`` sees them."""

    if args.provider:
        os.environ["PLANMYAGENTS_EMBEDDING_PROVIDER"] = args.provider
    if args.model:
        os.environ["PLANMYAGENTS_EMBEDDING_MODEL"] = args.model
    if args.openai_model:
        os.environ["PLANMYAGENTS_OPENAI_EMBEDDING_MODEL"] = args.openai_model
    if args.dimension:
        os.environ["PLANMYAGENTS_EMBEDDING_DIM"] = str(args.dimension)


def _list_candidate_ids_without_embedding(
    store: PostgresDiscoveryStore,
    *,
    force: bool,
    only_ids: set[str],
    limit: int,
) -> list[str]:
    """Pull only the provider_ids that need encoding.

    Avoids materialising every candidate row + raw_candidate JSON
    into memory just to decide which ones to encode. Honours
    ``--force`` (re-encode everything) and ``--candidate-id`` (encode
    a specific subset).
    """

    where = "1=1" if force else "embedding IS NULL"
    sql = f"""
        SELECT provider_id
        FROM discovery_candidates
        WHERE {where}
        ORDER BY provider_id
        LIMIT %s
    """
    with store._connect() as connection:  # noqa: SLF001 - script-only helper
        with connection.cursor() as cursor:
            cursor.execute(sql, (limit,))
            ids = [row[0] for row in cursor.fetchall()]
    if only_ids:
        ids = [pid for pid in ids if pid in only_ids]
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute and persist embeddings for discovery candidates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--store",
        default=os.getenv("PLANMYAGENTS_DISCOVERY_STORE_URL", ""),
        help="Postgres discovery store URL (postgresql://...).",
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("PLANMYAGENTS_EMBEDDING_PROVIDER", ""),
        help="`openai` to force OpenAI; otherwise leave empty to honour env.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("PLANMYAGENTS_EMBEDDING_MODEL", ""),
        help="Ollama model id (e.g. nomic-embed-text). "
        "Ignored when --provider=openai.",
    )
    parser.add_argument(
        "--openai-model",
        default=os.getenv(
            "PLANMYAGENTS_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
        ),
        help="OpenAI model id when --provider=openai.",
    )
    parser.add_argument(
        "--dimension",
        type=int,
        default=int(os.getenv("PLANMYAGENTS_EMBEDDING_DIM", str(DEFAULT_EMBEDDING_DIM))),
    )
    parser.add_argument(
        "--candidate-id",
        action="append",
        default=[],
        help="Optional: restrict encoding to one or more provider_ids.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10_000,
        help="Stop after at most this many candidates total.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=100,
        help="Encode + upsert in chunks of this size. Lower for slow embedders.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-encode even rows that already have an embedding (e.g. after "
        "switching embedders).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.store.startswith(("postgresql://", "postgres://")):
        parser.error("--store must be a postgresql:// URL.")

    _apply_cli_env(args)
    embedder = embedder_from_env()
    store = PostgresDiscoveryStore(args.store)

    only_ids = {item for item in args.candidate_id if item}
    target_ids = _list_candidate_ids_without_embedding(
        store, force=args.force, only_ids=only_ids, limit=args.limit
    )
    total = len(target_ids)

    encoded = 0
    written = 0
    skipped: list[str] = []
    failures: list[dict[str, str]] = []
    started = time.monotonic()

    for chunk_start in range(0, total, args.chunk_size):
        chunk_ids = target_ids[chunk_start : chunk_start + args.chunk_size]
        # Load the candidate rows for this chunk only.
        all_candidates = store.load()
        chunk_id_set = set(chunk_ids)
        chunk = [c for c in all_candidates if c.id in chunk_id_set]

        vectors: dict[str, list[float]] = {}
        for candidate in chunk:
            text = candidate_text_for_embedding(candidate.to_registry_json())
            if not text:
                skipped.append(candidate.id)
                continue
            try:
                vectors[candidate.id] = embedder.encode(text)
                encoded += 1
            except EmbedderError as exc:
                failures.append({"id": candidate.id, "error": str(exc)})

        if not args.dry_run and vectors:
            written += store.upsert_embeddings(vectors)

        elapsed = time.monotonic() - started
        rate = encoded / elapsed if elapsed > 0 else 0.0
        print(
            f"chunk {chunk_start // args.chunk_size + 1}/"
            f"{(total + args.chunk_size - 1) // args.chunk_size}: "
            f"encoded={encoded}/{total} written={written} "
            f"failures={len(failures)} rate={rate:.1f}/s",
            file=sys.stderr,
        )

    print(
        json.dumps(
            {
                "store": args.store,
                "embedder": embedder.name,
                "dimension": embedder.dimension,
                "selected": total,
                "encoded": encoded,
                "written": written,
                "skipped": skipped,
                "failures": failures,
                "force": args.force,
                "chunk_size": args.chunk_size,
                "dry_run": args.dry_run,
                "elapsed_seconds": round(time.monotonic() - started, 2),
            },
            indent=2,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
