-- sprint-pitch-align P3-4 — Postgres view for the homepage evidence layer.
--
-- The FastAPI `/health/evidence` endpoint hand-rolls these counts in Python
-- (`apps/api/planmyagents_api/web/app.py:_compute_evidence_health`). This
-- view exposes the same shape so any Datadog / Grafana / dbt / metabase
-- dashboard can chart the cron's health directly against Postgres without
-- needing the API to be up — useful for alerting paths that need to fire
-- _because_ the API is down.
--
-- Keep the column names + semantics in lock-step with
-- `EvidenceHealthResponse` in `apps/api/planmyagents_api/web/models.py`.
-- If you change one without the other, the homepage and the dashboard
-- will silently disagree.

CREATE OR REPLACE VIEW evidence_health AS
  SELECT
    -- Activity counters (the four tiles on the homepage strip)
    (SELECT COUNT(*) FROM benchmark_runs
     WHERE created_at > now() - interval '24 hours')             AS benchmark_runs_24h,
    (SELECT COUNT(*) FROM verification_records
     WHERE created_at > now() - interval '7 days')               AS verification_records_7d,
    (SELECT COUNT(*) FROM discovery_run_events
     WHERE started_at > now() - interval '24 hours')             AS discovery_run_events_24h,
    (SELECT COUNT(*) FROM capability_demand_events
     WHERE requested_at > now() - interval '24 hours')           AS capability_demand_events_24h,
    (SELECT COUNT(*) FROM discovery_gap_events
     WHERE observed_at > now() - interval '24 hours')            AS discovery_gap_events_24h,

    -- "Routable" cell — count of distinct (provider, capability) pairs
    -- with at least one successful benchmark run in the last 30 days.
    -- Matches the deck's "routable today" semantics; do not collapse this
    -- into `discovery_candidates.route_status`, which is the promotion-
    -- pipeline state and is a different signal.
    (SELECT COUNT(DISTINCT (provider_id, capability))
     FROM benchmark_runs
     WHERE succeeded = true
       AND created_at > now() - interval '30 days')              AS route_status_routable_count,

    -- Totals so dashboards can show "all-time" alongside "last 24h".
    (SELECT COUNT(*) FROM benchmark_runs)                        AS benchmark_runs_total,
    (SELECT COUNT(*) FROM verification_records)                  AS verification_records_total,

    now()                                                        AS checked_at;

COMMENT ON VIEW evidence_health IS
  'sprint-pitch-align P3-4. Mirror of GET /health/evidence so dashboards '
  'can chart the cron health directly. Column semantics MUST match '
  'EvidenceHealthResponse in apps/api/planmyagents_api/web/models.py.';
