PYTHON ?= $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)
RUFF ?= .venv/bin/ruff

.PHONY: install test test-fast compile json-validate lint discovery-smoke discovery-research-smoke generalization-eval quality demo infra-up infra-down candidate-verify-smoke hunter-report dev-bench-report benchmark-schedule benchmark-schedule-smoke migrate migrate-apis-without-agents api web web-install web-typecheck web-test-config web-build rebuild-rankings bootstrap-postgres upkeep embed-candidates verify-candidates discovery-refresh stack-up stack-down deploy-build-api mcp-tool-probe openapi-enricher docs-extractor recipe-roundtrip evidence-backfill verify-top-candidates evidence-cron health-evidence web-e2e-install web-e2e deck-pdf benchmark-cron benchmark-cron-smoke discovery-verify-cron discovery-verify-cron-dry check-evidence-health backfill-phase5-cells demand-snapshot audit-baseline-firewall outreach-validate-urls outreach-validate-urls-local audit-ranking-sources index-pass discovery-research growth-pass benchmark-credibility-report env-check eval-schedule eval-cron eval-schedule-smoke card-ingest

install:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r requirements.txt

test:
	PYTHONPATH=apps/api $(PYTHON) -m unittest discover -s apps/api/tests -p 'test_*.py'

# Inner-loop fast subset: skips the live-LLM `FastAPIAppTest` class
# (29 tests that each fire a real Ollama call against qwen3.6:27b
# and external HTTP for scout discovery; ~10+ minutes wall clock).
# Covers the other ~1,189 unit tests in well under a minute. See
# `scripts/dev/run_fast_tests.py` for the rationale + when to fall back
# to the full `make test`.
test-fast:
	PYTHONPATH=apps/api PLANMYAGENTS_LOAD_PROMOTED_PROVIDERS_FROM_DB=false PLANMYAGENTS_PROMOTED_PROVIDER_STORE_URL= $(PYTHON) scripts/dev/run_fast_tests.py

compile:
	PYTHONPATH=apps/api $(PYTHON) -m compileall -q apps/api/planmyagents_api scripts

json-validate:
	$(PYTHON) -m json.tool packages/registry/agents.json >/dev/null
	$(PYTHON) -m json.tool packages/registry/agents.schema.json >/dev/null
	$(PYTHON) -m json.tool packages/discovery/sources/curated_mcp_catalog.json >/dev/null
	$(PYTHON) -m json.tool packages/discovery/sources/curated_a2a_cards.json >/dev/null
	$(PYTHON) -m json.tool packages/discovery/sources/curated_ai_agents.json >/dev/null
	$(PYTHON) -m json.tool packages/discovery/sources/curated_web_docs.json >/dev/null
	$(PYTHON) -m json.tool packages/evals/generalization/scenarios.json >/dev/null

lint:
	$(RUFF) check apps scripts

discovery-smoke:
	PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_discovery_upkeep.py --store .planmyagents_runs/discovery-store.sqlite
	PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_discovery.py --query "find flight booking agents" --store .planmyagents_runs/discovery-store.sqlite --exclude-stale --stale-after-days 30 --limit 5 --no-persist >/dev/null

discovery-research-smoke:
	PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_discovery_research.py --store .planmyagents_runs/discovery-store.sqlite --no-github-live --limit 25 >/dev/null

generalization-eval:
	PYTHONPATH=apps/api $(PYTHON) scripts/dev/run_generalization_eval.py >/dev/null

candidate-verify-smoke:
	PYTHONPATH=apps/api $(PYTHON) scripts/verification/run_candidate_verification.py --store .planmyagents_runs/discovery-store.sqlite --limit 3 --dry-run >/dev/null

# Run the benchmark scheduler for every capability that has at least one
# `requires_benchmark_gate=true` agent in the registry. Override the
# capability set explicitly with CAPABILITY=<id> (e.g.
# `make benchmark-schedule CAPABILITY=payment_authorization`). The legacy
# behaviour of hardcoding `email_verification` is gone — that bug was
# flagged in sprint-3.md:175 and closed in sprint-pitch-align Phase 2-2.
benchmark-schedule:
	PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/run_benchmark_scheduler.py \
	  --discovery-store .planmyagents_runs/discovery-store.sqlite \
	  --benchmark-store .planmyagents_runs/benchmark-store.json \
	  $(if $(CAPABILITY),--capability $(CAPABILITY),--gated-from-registry) \
	  --cases-per-capability 5

benchmark-schedule-smoke:
	PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/run_benchmark_scheduler.py \
	  --discovery-store .planmyagents_runs/discovery-store.sqlite \
	  --benchmark-store .planmyagents_runs/benchmark-store.json \
	  --gated-from-registry --cases-per-capability 2 >/dev/null

mcp-tool-probe:
	PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_mcp_tool_probe.py \
	  --store .planmyagents_runs/discovery-store.sqlite

openapi-enricher:
	PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_openapi_enricher.py \
	  --store .planmyagents_runs/discovery-store.sqlite

docs-extractor:
	PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_docs_extractor.py \
	  --store .planmyagents_runs/discovery-store.sqlite --limit 5

audit-ranking-sources:
	PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/audit_ranking_sources.py \
	  --benchmark-store .planmyagents_runs/benchmark-store.json

# Single-shot index growth pass. Runs the configured-source upkeep,
# OpenAPI enrichment, docs extraction, and verification. Live research
# (Brave/Tavily/GitHub) is intentionally NOT chained here; it's opt-in via
# `make discovery-research` (or the bigger `make growth-pass`) with the
# appropriate env keys. This is the "Track A" loop from sprint.md.
index-pass: discovery-refresh openapi-enricher docs-extractor verify-candidates
	@echo "Index pass complete. Run 'make benchmark-credibility-report' to refresh credibility verdicts."

# Live-research pass. Pulls new agents from GitHub code search + web search
# (Tavily / Brave) using the keys configured in `.env`. Each connector
# silently no-ops if its key is missing, so this is safe to run even with
# only one key set.
discovery-research:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_discovery_research.py \
	    --store "$${PLANMYAGENTS_DISCOVERY_STORE_URL:-.planmyagents_runs/discovery-store.sqlite}"

# Full growth pass: live research first, then the curated index-pass loop.
# Use this when you've just added new keys to `.env` and want to grow the
# index in one shot. Run `make env-check` first to confirm what's wired.
growth-pass: discovery-research index-pass
	@echo "Growth pass complete. Run 'make env-check' to see what's still unset."

benchmark-credibility-report:
	PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/benchmark_credibility_report.py \
	  --discovery-store .planmyagents_runs/discovery-store.sqlite \
	  --benchmark-store .planmyagents_runs/benchmark-store.json \
	  --also-json

migrate:
	PYTHONPATH=apps/api $(PYTHON) scripts/db/apply_migrations.py

# One-shot migration for local SQLite/JSON dev stores: lift any
# api_provider / payment_provider rows out of `discovery_candidates`
# and into `apis_without_agents`. Idempotent. Production postgres is
# handled by the SQL migration in infra/postgres/init/.
migrate-apis-without-agents:
	PYTHONPATH=apps/api $(PYTHON) scripts/db/migrate_apis_without_agents.py

quality: test compile json-validate lint discovery-smoke discovery-research-smoke generalization-eval candidate-verify-smoke benchmark-schedule-smoke

dev-bench-report:
	PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/planmyagents_bench.py run --capability email_verification --provider mock-email --output .planmyagents_runs/email_mock.json
	PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/planmyagents_bench.py report --input .planmyagents_runs/email_mock.json --output .planmyagents_runs/email_mock.md

hunter-report:
	PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/planmyagents_bench.py run --capability email_verification --provider hunter --output .planmyagents_runs/hunter.json
	PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/planmyagents_bench.py report --input .planmyagents_runs/hunter.json --output .planmyagents_runs/hunter.md

demo:
	PYTHONPATH=apps/api $(PYTHON) scripts/dev/serve_demo.py

api:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api .venv/bin/uvicorn planmyagents_api.web.app:app --reload --port 8000

env-check:
	PYTHONPATH=apps/api $(PYTHON) scripts/dev/env_check.py

web-install:
	cd apps/web && npm install

web:
	cd apps/web && npm run dev

web-typecheck:
	cd apps/web && npm run typecheck

# Lightweight, zero-spinup regression test for next.config.mjs.
# Specifically guards the `allowedDevOrigins` allow-list that, when
# missing, silently breaks hydration on every "use client" component
# (including the LiveEvidenceStrip on /). See
# apps/web/tests/config/next-config.test.mjs for the full backstory.
web-test-config:
	cd apps/web && npm run test:config

# Validate every URL cited in docs/outreach/*.md still returns 2xx/3xx.
# Default target hits production. Use the -local variant when the dev
# server is up and you want to sanity-check a fresh draft without
# pushing a deploy.
outreach-validate-urls:
	$(PYTHON) scripts/verification/validate_outreach_urls.py

outreach-validate-urls-local:
	$(PYTHON) scripts/verification/validate_outreach_urls.py --base-url http://127.0.0.1:3000

web-build:
	cd apps/web && npm run build

# Recompute `agent_rankings` from `benchmark_runs` so /leaderboards
# stops silently rendering "Bench-passed 0 · Real runs No" for cells
# that have succeeded runs in the raw table. Idempotent; safe to run
# any time. Also invoked automatically at the end of
# `scripts/benchmark/backfill_phase5_cells.py` — the only writer that bypasses
# the live scheduler. See
# `apps/api/planmyagents_api/benchmark/rebuild.py` for full rationale.
rebuild-rankings:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/rebuild_agent_rankings.py

infra-up:
	docker compose up -d postgres

infra-down:
	docker compose down

# ---- Operational targets (Postgres-backed stack) ---------------------------
# All of these load .env if it exists, fall back to the local docker compose
# Postgres URL otherwise, and are safe to re-run.

bootstrap-postgres:
	./scripts/db/bootstrap_postgres.sh

upkeep:
	./scripts/dev/upkeep_loop.sh

discovery-refresh:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_discovery_upkeep.py \
	    --store "$${PLANMYAGENTS_DISCOVERY_STORE_URL:-.planmyagents_runs/discovery-store.sqlite}"

verify-candidates:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/verification/run_candidate_verification.py \
	    --store "$${PLANMYAGENTS_DISCOVERY_STORE_URL:-.planmyagents_runs/discovery-store.sqlite}" \
	    --verification-store "$${PLANMYAGENTS_VERIFICATION_STORE_URL:-.planmyagents_runs/verification-store.json}" \
	    --limit 200

embed-candidates:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/discovery/embed_discovery_candidates.py \
	    --store "$${PLANMYAGENTS_DISCOVERY_STORE_URL}" --limit 1000

stack-up: infra-up bootstrap-postgres
	@echo
	@echo "Stack ready."
	@echo "  Run the API with:  make api    # http://127.0.0.1:8000"
	@echo "  Run the web with:  make web    # http://127.0.0.1:3000"

stack-down: infra-down

deploy-build-api:
	docker build -f infra/api/Dockerfile -t planmyagents-api:local .

# End-to-end manual round-trip: render all five recipe exports against
# real npm-published MCP servers and a real public HTTP API, then
# launch each emitted mcpServers entry as a subprocess and complete
# the MCP `initialize` handshake over stdio (the exact handshake
# Claude Desktop performs on startup). Findings are captured in
# docs/manual-test-log.md.
recipe-roundtrip:
	PYTHONPATH=apps/api $(PYTHON) scripts/dev/manual_recipe_roundtrip.py
	$(PYTHON) scripts/verification/probe_emitted_mcp_commands.py \
	  .planmyagents_runs/manual-roundtrip/synthetic/recipe_claude_desktop_json.json

# ---------------------------------------------------------------------------
# sprint-pitch-align Phase 2 + Phase 3 — keep the evidence store non-empty.
# ---------------------------------------------------------------------------

# One-shot backfill: Razorpay live cell + JSONL → Postgres for demand /
# runs / gaps. Idempotent on re-run (skips when target tables non-empty).
# Add --force to override.
evidence-backfill:
	PYTHONPATH=apps/api $(PYTHON) scripts/evidence/backfill_evidence_store.py

# Run verification on the top 10 known_provider / registered_in_directory
# candidates and persist results to verification_records. Cheap (only
# fetches evidence URL, no side effects against vendor APIs). Tunable
# with LIMIT=20 etc.
verify-top-candidates:
	PYTHONPATH=apps/api $(PYTHON) scripts/verification/verify_top_candidates.py --limit $${LIMIT:-10}

# Composite target a cron job runs every N hours. Re-verifies top
# candidates AND re-runs the benchmark scheduler for every gated capability,
# AND runs the eval framework over discovered agentic candidates, so
# `verification_records`, `benchmark_runs`, and eval rankings all get fresh
# rows on every cron tick. Wired together by sprint-pitch-align Phase 3 +
# the agent-eval-framework spec.
evidence-cron: verify-top-candidates benchmark-cron eval-cron

# Convenience: pretty-print the live counts the homepage reads.
health-evidence:
	@curl -sS http://127.0.0.1:8000/health/evidence | $(PYTHON) -m json.tool

# ---------------------------------------------------------------------------
# sprint-pitch-align Phase 3 — scheduled evidence + observability.
#
# `benchmark-cron`            (P3-1): runs every requires_benchmark_gate=true
#                                     capability in agents.json once, lands
#                                     rows in benchmark_runs.
# `benchmark-cron-smoke`      (P3-1): hermetic variant for every-commit CI.
# `discovery-verify-cron`     (P3-2): re-verifies known_provider (7d) and
#                                     registered_in_directory (30d)
#                                     candidates, auto-demotes stale tiers.
# `discovery-verify-cron-dry` (P3-2): dry-run that prints the due list.
# `check-evidence-health`     (P3-5): fails the build if /health/evidence
#                                     drops below its required minimums.
# ---------------------------------------------------------------------------

benchmark-cron:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/run_benchmark_scheduler.py \
	    --discovery-store "$${PLANMYAGENTS_DISCOVERY_STORE_URL:-.planmyagents_runs/discovery-store.sqlite}" \
	    --benchmark-store "$${PLANMYAGENTS_BENCHMARK_STORE_URL:-.planmyagents_runs/benchmark-store.json}" \
	    --gated-from-registry --cases-per-capability $${CASES_PER_CAPABILITY:-5}

benchmark-cron-smoke:
	PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/run_benchmark_scheduler.py \
	  --discovery-store .planmyagents_runs/discovery-store.sqlite \
	  --benchmark-store .planmyagents_runs/benchmark-store.json \
	  --gated-from-registry --cases-per-capability 1 >/dev/null

# ---------------------------------------------------------------------------
# agent-eval-framework — evaluate discovered agentic candidates.
#
# `eval-schedule`       : ad-hoc run; default --run-mode dry_run (no live call).
#                         Override RUN_MODE=sandbox|live for real scoring (also
#                         needs PLANMYAGENTS_ENABLE_PROTOCOL_ADAPTER_EXECUTION=true).
# `eval-cron`           : cron tick; chained into `evidence-cron`. dry_run unless
#                         RUN_MODE is overridden, so a stray cron is safe.
# `eval-schedule-smoke` : hermetic every-commit variant (dry_run, no network).
# ---------------------------------------------------------------------------
eval-schedule:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/run_eval_scheduler.py \
	    --discovery-store "$${PLANMYAGENTS_DISCOVERY_STORE_URL:-.planmyagents_runs/discovery-store.sqlite}" \
	    --benchmark-store "$${PLANMYAGENTS_BENCHMARK_STORE_URL:-.planmyagents_runs/benchmark-store.json}" \
	    --run-mode $${RUN_MODE:-dry_run} --cases-per-capability $${CASES_PER_CAPABILITY:-30}

eval-cron:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/run_eval_scheduler.py \
	    --discovery-store "$${PLANMYAGENTS_DISCOVERY_STORE_URL:-.planmyagents_runs/discovery-store.sqlite}" \
	    --benchmark-store "$${PLANMYAGENTS_BENCHMARK_STORE_URL:-.planmyagents_runs/benchmark-store.json}" \
	    --run-mode $${RUN_MODE:-dry_run} --cases-per-capability $${CASES_PER_CAPABILITY:-5}

eval-schedule-smoke:
	PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/run_eval_scheduler.py \
	  --discovery-store .planmyagents_runs/discovery-store.sqlite \
	  --benchmark-store .planmyagents_runs/benchmark-store.json \
	  --run-mode dry_run --cases-per-capability 1 >/dev/null

discovery-verify-cron:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_discovery_verify_cron.py \
	    --dsn "$${PLANMYAGENTS_VERIFICATION_STORE_URL:-$${PLANMYAGENTS_DISCOVERY_STORE_URL:-postgresql://planmyagents:planmyagents@localhost:55433/planmyagents}}" \
	    --max-candidates $${MAX_CANDIDATES:-50}

# Ingest a submitted Agent Card URL (any domain) into the discovery index.
# Registration/resolution path — NOT a crawler. Existence + claim verification
# only; A2A quality attestation is blocked-on-invocation.
#   make card-ingest CARD_URL=https://policycheck.tools/.well-known/agent.json
card-ingest:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_card_ingestion.py \
	    --card-url "$${CARD_URL:?set CARD_URL=https://domain/.well-known/agent.json}" \
	    --discovery-store "$${PLANMYAGENTS_DISCOVERY_STORE_URL:-.planmyagents_runs/discovery-store.sqlite}" \
	    --verification-store "$${PLANMYAGENTS_VERIFICATION_STORE_URL:-.planmyagents_runs/verification-store.json}"

discovery-verify-cron-dry:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/discovery/run_discovery_verify_cron.py \
	    --dsn "$${PLANMYAGENTS_VERIFICATION_STORE_URL:-$${PLANMYAGENTS_DISCOVERY_STORE_URL:-postgresql://planmyagents:planmyagents@localhost:55433/planmyagents}}" \
	    --dry-run

check-evidence-health:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/evidence/check_evidence_health.py \
	    --dsn "$${PLANMYAGENTS_DISCOVERY_STORE_URL:-postgresql://planmyagents:planmyagents@localhost:55433/planmyagents}" \
	    --min-benchmark-runs-total $${MIN_BENCHMARK_RUNS_TOTAL:-1} \
	    --min-verification-records-total $${MIN_VERIFICATION_RECORDS_TOTAL:-1} \
	    --min-routable-cells $${MIN_ROUTABLE_CELLS:-1}

# ---------------------------------------------------------------------------
# sprint-pitch-align Phase 5 — second + third routable cell.
#
# Seeds discovery_candidates rows for Resend (email_send) and Firecrawl
# (web_scraping), then runs each capability's 5-case benchmark suite
# against the matching MockResend / MockFirecrawl adapter (these produce
# the same response shape the real wrappers produce). Rows land in
# benchmark_runs tagged with `output._provenance =
# "response_fixture_pending_live_key"` so DD can tell them apart from
# Razorpay's `_replayed_from` live row. Idempotent on (provider, capability)
# within a 30-day window; pass FORCE=1 to insert a fresh batch.
# ---------------------------------------------------------------------------
backfill-phase5-cells:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/benchmark/backfill_phase5_cells.py \
	    --dsn "$${PLANMYAGENTS_BENCHMARK_STORE_URL:-$${PLANMYAGENTS_DISCOVERY_STORE_URL:-postgresql://planmyagents:planmyagents@localhost:55433/planmyagents}}" \
	    $(if $(FORCE),--force,)

# ---------------------------------------------------------------------------
# Sprint 6 — design-partner demand snapshot.
#
# Joins capability_demand_events (what users asked for), discovery_gap_events
# (what we tried to fulfil and surfaced zero routable agents for), and the
# routable cells in benchmark_runs into a single stable CSV. Designed to be
# pasted straight into a partner conversation or a vendor outreach email.
# Default lookback 30d; override with LOOKBACK_DAYS=N.
# ---------------------------------------------------------------------------
demand-snapshot:
	@set -a; . ./.env 2>/dev/null || true; set +a; \
	  PYTHONPATH=apps/api $(PYTHON) scripts/evidence/export_demand_snapshot.py \
	    --dsn "$${PLANMYAGENTS_DEMAND_STORE_URL:-$${PLANMYAGENTS_DISCOVERY_STORE_URL:-postgresql://planmyagents:planmyagents@localhost:55433/planmyagents}}" \
	    --lookback-days $${LOOKBACK_DAYS:-30} \
	    --output $${OUTPUT:-data/demand_snapshot.csv}

# ---------------------------------------------------------------------------
# sprint-6 / B — baseline-firewall audit.
#
# Fails if any code under apps/api/planmyagents_api/agents/, planner/,
# workflows/, or web/ imports or references a module under
# `planmyagents_api.benchmark.baselines.*`. CI runs this on every push;
# this target lets a dev run the same check locally before pushing.
# ---------------------------------------------------------------------------
audit-baseline-firewall:
	$(PYTHON) scripts/benchmark/audit_baseline_firewall.py

# ---------------------------------------------------------------------------
# sprint-pitch-align Phase 4 — Playwright proofs of the four pitch flows.
# Run `make web-e2e-install` once (installs @playwright/test + chromium),
# then `make web-e2e` to re-run the spec. Assumes the API is live on :8000
# (run `make api` separately) and that evidence has been backfilled.
# ---------------------------------------------------------------------------
web-e2e-install:
	cd apps/web && npm install --save-dev @playwright/test && npm run e2e:install

web-e2e:
	cd apps/web && npm run e2e

# ---------------------------------------------------------------------------
# Pitch deck PDF regenerate. Uses Marp + system Chrome (Puppeteer's bundled
# Chromium is heavy). Requires --no-stdin because Marp 4 otherwise blocks
# waiting on stdin in non-TTY shells.
# ---------------------------------------------------------------------------
deck-pdf:
	PUPPETEER_EXECUTABLE_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
	  npx --yes @marp-team/marp-cli@latest --no-stdin --pdf --allow-local-files PITCH_DECK.md --output PITCH_DECK.pdf

