"""Unit tests for the Phase 5 cell backfill (sprint-pitch-align P5-1+P5-2).

The Postgres-bound paths (`_seed_candidate`, `_backfill_cell`,
`_routable_count_summary`) are exercised end-to-end via the Makefile
smoke test (`make backfill-phase5-cells`); this suite covers the
in-memory pieces that don't need a database:

* `_run_fixture_benchmark` actually returns 5 passing runs per cell.
* Each returned run is re-tagged with the cell's canonical provider_id
  (so the homepage routable-count tile credits Resend / Firecrawl, not
  the `mock-*` provider id the mock adapter exposes).
* Each run's `response.output` carries the
  `response_fixture_pending_live_key` provenance marker so DD can tell
  these rows apart from Razorpay's `_replayed_from` live row.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))

from backfill_phase5_cells import (  # noqa: E402
    CELLS,
    FIXTURE_PROVENANCE,
    _run_fixture_benchmark,
)


class FixtureBenchmarkTest(unittest.TestCase):
    def test_each_cell_returns_five_passing_runs_retagged_with_canonical_id(self) -> None:
        """Phase 5 acceptance: each cell must land 5 rows tagged with the
        canonical `provider_id` (so `route_status_routable_count` credits
        the right vendor)."""

        for cell in CELLS:
            with self.subTest(provider_id=cell["provider_id"]):
                runs = _run_fixture_benchmark(cell)
                self.assertEqual(
                    len(runs),
                    5,
                    msg=f"{cell['provider_id']} expected 5 runs, got {len(runs)}",
                )
                self.assertTrue(
                    all(r.response.succeeded for r in runs),
                    msg=f"{cell['provider_id']} not all runs succeeded",
                )
                self.assertTrue(
                    all(r.score.succeeded for r in runs),
                    msg=f"{cell['provider_id']} not all scores succeeded",
                )
                self.assertTrue(
                    all(r.score.quality_score >= 0.9 for r in runs),
                    msg=f"{cell['provider_id']} a case scored < 0.9",
                )
                self.assertTrue(
                    all(r.provider_id == cell["provider_id"] for r in runs),
                    msg=(
                        f"{cell['provider_id']} expected every run to be tagged with "
                        f"the canonical id, got {[r.provider_id for r in runs]}"
                    ),
                )
                self.assertTrue(
                    all(r.capability == cell["capability"] for r in runs),
                )

    def test_provenance_marker_is_attached_to_every_run(self) -> None:
        """The deck's honesty claim depends on these rows being
        distinguishable from Razorpay's live replay. The marker MUST be
        present on every fixture row."""

        for cell in CELLS:
            with self.subTest(provider_id=cell["provider_id"]):
                runs = _run_fixture_benchmark(cell)
                for run in runs:
                    self.assertIsInstance(
                        run.response.output,
                        dict,
                        msg=f"{cell['provider_id']}/{run.test_case_id} output not dict",
                    )
                    self.assertEqual(
                        run.response.output.get("_provenance"),
                        FIXTURE_PROVENANCE,
                    )
                    self.assertEqual(
                        run.response.output.get("_canonical_provider_id"),
                        cell["provider_id"],
                    )

    def test_each_cell_covers_at_least_one_adversarial_case(self) -> None:
        """The benchmark suites include an adversarial case per capability;
        ensure the fixture path doesn't accidentally drop them."""

        for cell in CELLS:
            with self.subTest(provider_id=cell["provider_id"]):
                runs = _run_fixture_benchmark(cell)
                difficulties = {r.difficulty for r in runs}
                self.assertIn(
                    "adversarial",
                    difficulties,
                    msg=(
                        f"{cell['provider_id']} fixture missed the adversarial "
                        f"case; got difficulties: {difficulties}"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
