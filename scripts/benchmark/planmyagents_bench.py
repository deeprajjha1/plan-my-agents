#!/usr/bin/env python3
"""Repo-local wrapper for the PlanMyAgents benchmark CLI.

Usage:
    python3 scripts/planmyagents_bench.py run --capability email_verification --provider mock-email
    python3 scripts/planmyagents_bench.py report
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_SRC = ROOT / "apps" / "api"
sys.path.insert(0, str(API_SRC))

from planmyagents_api.cli.bench import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
