from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
VENV_PY = ROOT / ".venv" / "bin" / "python"
SCRIPT = ROOT / "scripts" / "discovery" / "run_card_ingestion.py"


def _py() -> Path:
    return VENV_PY if VENV_PY.exists() else Path(sys.executable)


class CardIngestionCliTest(unittest.TestCase):
    def test_help_lists_card_url(self) -> None:
        result = subprocess.run(
            [str(_py()), str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--card-url", result.stdout)

    def test_non_https_url_reports_structured_error_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = subprocess.run(
                [
                    str(_py()),
                    str(SCRIPT),
                    "--card-url",
                    "http://policycheck.tools/.well-known/agent.json",
                    "--discovery-store",
                    str(tmp_path / "d.json"),
                    "--verification-store",
                    str(tmp_path / "v.json"),
                ],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["resolved"])
            self.assertEqual(payload["error"], "non_https_url")

    def test_unreachable_url_reports_structured_error_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = subprocess.run(
                [
                    str(_py()),
                    str(SCRIPT),
                    "--card-url",
                    "https://127.0.0.1.nonexistent.invalid/.well-known/agent.json",
                    "--discovery-store",
                    str(tmp_path / "d.json"),
                    "--verification-store",
                    str(tmp_path / "v.json"),
                    "--timeout",
                    "3",
                ],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["resolved"])
            self.assertIn("resolution_failed", payload["error"])


if __name__ == "__main__":
    unittest.main()
