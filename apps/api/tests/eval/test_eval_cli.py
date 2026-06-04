from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
VENV_PY = ROOT / ".venv" / "bin" / "python"
SCRIPT = ROOT / "scripts" / "benchmark" / "run_eval_scheduler.py"


def _write_cases(benchmarks_dir: Path, capability: str, n: int) -> None:
    cap_dir = benchmarks_dir / capability
    cap_dir.mkdir(parents=True, exist_ok=True)
    cases = [
        f"  - id: case-{i}\n"
        f"    capability: {capability}\n"
        "    difficulty: easy\n"
        "    inputs: {tool_name: scrape, arguments: {}}\n"
        "    expected: {text: {contains: ok}}\n"
        for i in range(n)
    ]
    (cap_dir / "cases.yaml").write_text("cases:\n" + "".join(cases))


def _candidate_json() -> dict:
    return {
        "id": "stub-mcp",
        "display_name": "Stub",
        "vendor": "Stub",
        "vendor_url": "https://stub.example.com",
        "provider_type": "mcp_server",
        "capabilities": [{"id": "web_scraping", "confidence": 0.95}],
        "verification_status": "registered_in_directory",
        "tools": [{"name": "scrape", "input_schema": {}}],
    }


class EvalCliTest(unittest.TestCase):
    def test_dry_run_default_makes_no_live_call(self) -> None:
        py = VENV_PY if VENV_PY.exists() else Path(sys.executable)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_cases(tmp_path, "web_scraping", 2)
            discovery = tmp_path / "discovery.json"
            discovery.write_text(json.dumps([_candidate_json()]))
            bench = tmp_path / "bench.json"

            result = subprocess.run(
                [
                    str(py),
                    str(SCRIPT),
                    "--discovery-store",
                    str(discovery),
                    "--benchmark-store",
                    str(bench),
                    "--benchmarks-dir",
                    str(tmp_path),
                    # no --run-mode → default dry_run
                ],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["run_mode"], "dry_run")
            # dry_run must produce no scored runs (no live call).
            self.assertEqual(payload["benchmark_runs"], 0)

    def test_help_lists_run_mode_choices(self) -> None:
        py = VENV_PY if VENV_PY.exists() else Path(sys.executable)
        result = subprocess.run(
            [str(py), str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry_run", result.stdout)
        self.assertIn("sandbox", result.stdout)


if __name__ == "__main__":
    unittest.main()
