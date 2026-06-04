from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.eval.models import EvalRunMode
from planmyagents_api.eval.safety import SafetyApproval, SafetyClass, SafetyClassifier


class SafetyClassifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clf = SafetyClassifier()

    def test_read_only_permits_all_modes(self) -> None:
        self.assertEqual(self.clf.classify("web_scraping"), SafetyClass.READ_ONLY)
        modes = self.clf.permitted_run_modes("web_scraping")
        self.assertEqual(
            modes, {EvalRunMode.DRY_RUN, EvalRunMode.SANDBOX, EvalRunMode.LIVE}
        )

    def test_unknown_capability_is_unclassified_dry_run_only(self) -> None:
        self.assertEqual(self.clf.classify("mystery_cap"), SafetyClass.UNCLASSIFIED)
        self.assertEqual(
            self.clf.permitted_run_modes("mystery_cap"), {EvalRunMode.DRY_RUN}
        )

    def test_side_effecting_without_approval_dry_run_only(self) -> None:
        self.assertEqual(self.clf.classify("email_send"), SafetyClass.SIDE_EFFECTING)
        self.assertEqual(
            self.clf.permitted_run_modes("email_send"), {EvalRunMode.DRY_RUN}
        )
        self.assertFalse(self.clf.is_permitted("email_send", EvalRunMode.LIVE))

    def test_side_effecting_with_fixture_allows_sandbox_not_live(self) -> None:
        clf = SafetyClassifier(
            approvals={
                "email_send": SafetyApproval(
                    capability="email_send",
                    fixture_spec={"recipient_domain": "resend.dev"},
                    approved_by="owner",
                    approved_at="2026-06-03",
                    allow_live=False,
                )
            }
        )
        modes = clf.permitted_run_modes("email_send")
        self.assertIn(EvalRunMode.SANDBOX, modes)
        self.assertNotIn(EvalRunMode.LIVE, modes)
        self.assertEqual(clf.fixture_for("email_send"), {"recipient_domain": "resend.dev"})

    def test_side_effecting_with_allow_live_permits_live(self) -> None:
        clf = SafetyClassifier(
            approvals={
                "payment_authorization": SafetyApproval(
                    capability="payment_authorization",
                    fixture_spec={"key_prefix": "rzp_test_"},
                    approved_by="owner",
                    approved_at="2026-06-03",
                    allow_live=True,
                )
            }
        )
        self.assertIn(EvalRunMode.LIVE, clf.permitted_run_modes("payment_authorization"))

    def test_llm_proposal_never_auto_promotes(self) -> None:
        effective = self.clf.record_proposal("mystery_cap", SafetyClass.READ_ONLY)
        # Proposal does not change the effective class.
        self.assertEqual(effective, SafetyClass.UNCLASSIFIED)
        self.assertEqual(self.clf.classify("mystery_cap"), SafetyClass.UNCLASSIFIED)

    def test_override_wins(self) -> None:
        clf = SafetyClassifier(overrides={"mystery_cap": SafetyClass.READ_ONLY})
        self.assertEqual(clf.classify("mystery_cap"), SafetyClass.READ_ONLY)


if __name__ == "__main__":
    unittest.main()
