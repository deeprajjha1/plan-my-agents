"""Unit tests for the evidence-health CI guard (sprint-pitch-align P3-5).

These tests cover the branch logic that doesn't need a live Postgres:

* the `--main-only` skip behaviour
* the per-counter minimum comparison

We monkey-patch `_fetch_from_dsn` / `_fetch_from_url` to feed canned
payloads — the Postgres-bound code paths are exercised by the integration
smoke tests in `Makefile:check-evidence-health`.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts" / "evidence"))

import check_evidence_health  # noqa: E402


def _run(argv: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with patch.object(sys, "argv", ["check_evidence_health.py", *argv]):
        with redirect_stdout(buf):
            code = check_evidence_health.main()
    text = buf.getvalue().strip()
    payload = json.loads(text) if text else {}
    return code, payload


class MainOnlySkipTest(unittest.TestCase):
    def test_skips_when_not_on_main(self) -> None:
        with patch.dict(os.environ, {"GITHUB_REF": "refs/heads/feature/foo"}, clear=False):
            code, payload = _run(
                ["--main-only", "--dsn", "postgresql://ignored/test"]
            )
        self.assertEqual(code, check_evidence_health.EXIT_OK)
        self.assertTrue(payload.get("skipped"))

    def test_runs_when_on_main(self) -> None:
        with patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main"}, clear=False):
            with patch.object(
                check_evidence_health,
                "_fetch_from_dsn",
                return_value={
                    "benchmark_runs_24h": 5,
                    "verification_records_7d": 12,
                    "discovery_run_events_24h": 1,
                    "capability_demand_events_24h": 0,
                    "discovery_gap_events_24h": 0,
                    "route_status_routable_count": 1,
                    "benchmark_runs_total": 30,
                    "verification_records_total": 60,
                    "checked_at": "2026-05-18T11:00:00+00:00",
                },
            ):
                code, payload = _run(
                    [
                        "--main-only",
                        "--dsn",
                        "postgresql://ignored/test",
                        "--min-benchmark-runs-24h",
                        "1",
                    ]
                )
        self.assertEqual(code, check_evidence_health.EXIT_OK)
        self.assertTrue(payload.get("ok"))


class MinimumComparisonTest(unittest.TestCase):
    def _payload(self, **overrides) -> dict:
        base = {
            "benchmark_runs_24h": 0,
            "verification_records_7d": 0,
            "discovery_run_events_24h": 0,
            "capability_demand_events_24h": 0,
            "discovery_gap_events_24h": 0,
            "route_status_routable_count": 0,
            "benchmark_runs_total": 0,
            "verification_records_total": 0,
            "checked_at": "2026-05-18T11:00:00+00:00",
        }
        base.update(overrides)
        return base

    def test_zero_counter_with_zero_minimum_passes(self) -> None:
        with patch.object(check_evidence_health, "_fetch_from_dsn", return_value=self._payload()):
            code, _ = _run(["--dsn", "postgresql://ignored/test"])
        self.assertEqual(code, check_evidence_health.EXIT_OK)

    def test_zero_counter_with_positive_minimum_fails(self) -> None:
        with patch.object(check_evidence_health, "_fetch_from_dsn", return_value=self._payload()):
            code, payload = _run(
                [
                    "--dsn",
                    "postgresql://ignored/test",
                    "--min-benchmark-runs-24h",
                    "1",
                ]
            )
        self.assertEqual(code, check_evidence_health.EXIT_GUARD_FAILED)
        self.assertEqual(payload["failures"][0]["metric"], "benchmark_runs_24h")
        self.assertEqual(payload["failures"][0]["observed"], 0)
        self.assertEqual(payload["failures"][0]["minimum"], 1)

    def test_multi_counter_minimum_reports_all_failures(self) -> None:
        with patch.object(
            check_evidence_health,
            "_fetch_from_dsn",
            return_value=self._payload(benchmark_runs_24h=2),
        ):
            code, payload = _run(
                [
                    "--dsn",
                    "postgresql://ignored/test",
                    "--min-benchmark-runs-24h",
                    "1",
                    "--min-routable-cells",
                    "3",
                ]
            )
        self.assertEqual(code, check_evidence_health.EXIT_GUARD_FAILED)
        metrics_in_failures = {f["metric"] for f in payload["failures"]}
        self.assertEqual(metrics_in_failures, {"route_status_routable_count"})

    def test_missing_data_source_returns_infrastructure_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            buf = io.StringIO()
            err = io.StringIO()
            old_stderr = sys.stderr
            sys.stderr = err
            try:
                with patch.object(sys, "argv", ["check_evidence_health.py"]):
                    with redirect_stdout(buf):
                        code = check_evidence_health.main()
            finally:
                sys.stderr = old_stderr
        self.assertEqual(code, check_evidence_health.EXIT_INFRASTRUCTURE)
        self.assertIn("no_data_source", err.getvalue())


if __name__ == "__main__":
    unittest.main()
