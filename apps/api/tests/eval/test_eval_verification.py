from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.models import CandidateCapability, DiscoveryCandidate
from planmyagents_api.eval.verification import VerificationProber


def _candidate(*, status: str = "unverified", vendor_url: str = "") -> DiscoveryCandidate:
    return DiscoveryCandidate(
        id="mcp-1",
        display_name="MCP One",
        vendor="Vendor",
        vendor_url=vendor_url,
        provider_type="mcp_server",
        capabilities=[CandidateCapability(id="web_scraping", confidence=0.9)],
        verification_status=status,
    )


class VerificationProberTest(unittest.TestCase):
    def test_gate_off_uses_catalogue_status_no_network(self) -> None:
        prober = VerificationProber(enable_live_probe=False)
        # vendor_url present but probe disabled → must NOT hit network; uses
        # the candidate's existing catalogue status.
        outcome = prober.probe(
            _candidate(status="registered_in_directory", vendor_url="https://example.com")
        )
        self.assertEqual(outcome.verification_status, "registered_in_directory")
        self.assertTrue(outcome.passed)
        self.assertIn("catalogue_status_no_live_probe", outcome.record["notes"])

    def test_no_evidence_url_yields_unverified(self) -> None:
        prober = VerificationProber(enable_live_probe=True)
        outcome = prober.probe(_candidate(status="unverified", vendor_url=""))
        self.assertEqual(outcome.verification_status, "unverified")
        self.assertFalse(outcome.passed)

    def test_outcome_record_is_serializable_shape(self) -> None:
        prober = VerificationProber(enable_live_probe=False)
        outcome = prober.probe(_candidate(status="known_provider"))
        self.assertEqual(outcome.record["provider_id"], "mcp-1")
        self.assertEqual(outcome.record["status"], "known_provider")
        self.assertTrue(outcome.passed)


if __name__ == "__main__":
    unittest.main()
