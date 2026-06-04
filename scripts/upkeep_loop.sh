#!/usr/bin/env bash
# Recurring upkeep loop. Designed for `cron` or `launchd`:
#
#   # crontab -e
#   0 * * * * cd /path/to/agent-manager && ./scripts/upkeep_loop.sh >> .planmyagents_runs/upkeep.log 2>&1
#
# Each tick:
#   1. Refresh discovery candidates from curated sources.
#   2. Re-verify any candidate whose evidence is stale or missing.
#   3. Top up embeddings for any new candidates.
#   4. Re-run the benchmark scheduler (verified candidates first).
#
# Idempotent and safe to run while the API + web app are serving traffic.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
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

mkdir -p .planmyagents_runs

stamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

step() {
  printf "\n[%s] %s\n" "$(stamp)" "$1"
  shift
  "$@"
}

step "Refresh discovery candidates" \
  env PYTHONPATH=apps/api "$PYBIN" scripts/run_discovery_upkeep.py --store "$POSTGRES_URL"

step "Verify candidate evidence" \
  env PYTHONPATH=apps/api "$PYBIN" scripts/run_candidate_verification.py \
    --store "$POSTGRES_URL" \
    --verification-store "$PLANMYAGENTS_VERIFICATION_STORE_URL" \
    --limit 100

step "Top up embeddings" \
  env PYTHONPATH=apps/api "$PYBIN" scripts/embed_discovery_candidates.py \
    --store "$POSTGRES_URL" \
    --limit 500

step "Run benchmark scheduler" \
  env PYTHONPATH=apps/api "$PYBIN" scripts/run_benchmark_scheduler.py \
    --discovery-store "$POSTGRES_URL" \
    --benchmark-store "$PLANMYAGENTS_BENCHMARK_STORE_URL" \
    --gated-from-registry \
    --cases-per-capability 5
# `--gated-from-registry` added 2026-05-20 sweep: without it the cron
# ran the FULL capability matrix every hour instead of just the
# capabilities with at least one `requires_benchmark_gate=true` provider
# in packages/registry/agents.json, silently burning Groq/Ollama tokens
# on capabilities that have nothing to gate. Mirrors what `make
# benchmark-cron` already does (Makefile:282-287).

printf "\n[%s] Upkeep complete.\n" "$(stamp)"
