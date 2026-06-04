#!/usr/bin/env bash
# One-shot Postgres bootstrap: bring up the container, apply migrations, refresh
# discovery candidates from curated sources, run verification, backfill
# embeddings, and run the benchmark scheduler. Idempotent; safe to re-run.
#
# Reads the standard PlanMyAgents environment variables from `.env` if present.
# Override POSTGRES_URL on the command line for hosted Postgres:
#
#   POSTGRES_URL=postgresql://USER:PASS@HOST:PORT/DB ./scripts/db/bootstrap_postgres.sh
#
# Example local run:
#
#   ./scripts/db/bootstrap_postgres.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

POSTGRES_URL="${POSTGRES_URL:-${PLANMYAGENTS_DISCOVERY_STORE_URL:-postgresql://planmyagents:planmyagents@localhost:55433/planmyagents}}"

export PLANMYAGENTS_DISCOVERY_STORE_URL="$POSTGRES_URL"
export PLANMYAGENTS_BENCHMARK_STORE_URL="${PLANMYAGENTS_BENCHMARK_STORE_URL:-$POSTGRES_URL}"
export PLANMYAGENTS_VERIFICATION_STORE_URL="${PLANMYAGENTS_VERIFICATION_STORE_URL:-$POSTGRES_URL}"

PYBIN="${PYTHON:-.venv/bin/python}"
if [[ ! -x "$PYBIN" ]]; then
  PYBIN="python3"
fi

run() {
  printf "\n===== %s =====\n" "$1"
  shift
  "$@"
}

is_local_postgres() {
  case "$POSTGRES_URL" in
    *localhost*|*127.0.0.1*) return 0 ;;
    *) return 1 ;;
  esac
}

if is_local_postgres; then
  if command -v docker >/dev/null 2>&1; then
    run "Ensure local Postgres + pgvector container is up" \
      docker compose up -d postgres
  else
    echo "warning: docker not found; assuming Postgres at $POSTGRES_URL is reachable"
  fi
else
  echo "Using remote Postgres at $POSTGRES_URL (skipping docker compose)."
fi

run "Apply schemas" \
  env PYTHONPATH=apps/api "$PYBIN" scripts/db/apply_migrations.py --url "$POSTGRES_URL"

run "Refresh discovery candidates from curated sources" \
  env PYTHONPATH=apps/api "$PYBIN" scripts/discovery/run_discovery_upkeep.py --store "$POSTGRES_URL"

run "Verify candidate evidence and persist verification history" \
  env PYTHONPATH=apps/api "$PYBIN" scripts/verification/run_candidate_verification.py \
    --store "$POSTGRES_URL" \
    --verification-store "$PLANMYAGENTS_VERIFICATION_STORE_URL" \
    --limit 200

run "Backfill embeddings (deterministic by default; semantic if PLANMYAGENTS_EMBEDDING_MODEL is set)" \
  env PYTHONPATH=apps/api "$PYBIN" scripts/discovery/embed_discovery_candidates.py \
    --store "$POSTGRES_URL" \
    --limit 1000

run "Run benchmark scheduler (synthetic fallback labelled honestly)" \
  env PYTHONPATH=apps/api "$PYBIN" scripts/benchmark/run_benchmark_scheduler.py \
    --discovery-store "$POSTGRES_URL" \
    --benchmark-store "$PLANMYAGENTS_BENCHMARK_STORE_URL" \
    --cases-per-capability 5

echo
echo "Bootstrap complete."
echo "Discovery store:    $PLANMYAGENTS_DISCOVERY_STORE_URL"
echo "Benchmark store:    $PLANMYAGENTS_BENCHMARK_STORE_URL"
echo "Verification store: $PLANMYAGENTS_VERIFICATION_STORE_URL"
echo
echo "Next steps:"
echo "  make api      # http://127.0.0.1:8000"
echo "  make web      # http://127.0.0.1:3000"
