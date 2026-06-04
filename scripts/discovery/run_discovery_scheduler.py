#!/usr/bin/env python3
"""Run discovery research refreshes on a local interval."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Schedule local discovery research upkeep.")
    parser.add_argument("--interval-minutes", type=float, default=60.0)
    parser.add_argument("--once", action="store_true", help="Run one refresh and exit.")
    parser.add_argument(
        "--store",
        type=Path,
        default=ROOT / ".planmyagents_runs" / "discovery-store.sqlite",
        help="Discovery store path passed to run_discovery_research.py.",
    )
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--query", default="scheduled broad MCP A2A AI-agent discovery refresh")
    parser.add_argument("--no-github-live", action="store_true")
    parser.add_argument("--github-token", default="")
    parser.add_argument("--research-url", action="append", default=[])
    args = parser.parse_args()

    while True:
        command = [
            sys.executable,
            str(ROOT / "scripts" / "run_discovery_research.py"),
            "--store",
            str(args.store),
            "--query",
            args.query,
        ]
        for capability in args.capability:
            command.extend(["--capability", capability])
        for url in args.research_url:
            command.extend(["--research-url", url])
        if args.no_github_live:
            command.append("--no-github-live")
        if args.github_token:
            command.extend(["--github-token", args.github_token])

        subprocess.run(command, check=False, cwd=ROOT)  # noqa: S603
        if args.once:
            return 0
        time.sleep(max(1.0, args.interval_minutes * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())
