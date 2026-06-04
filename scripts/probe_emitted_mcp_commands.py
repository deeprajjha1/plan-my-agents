"""Probe every ``mcpServers`` entry in an emitted Claude Desktop config
by launching the subprocess and completing the MCP ``initialize``
handshake over stdio.

This is the closest software-side equivalent to "merge the JSON into
``claude_desktop_config.json`` and restart Claude Desktop" — Claude
Desktop literally spawns the configured ``command + args`` and speaks
JSON-RPC 2.0 over stdin/stdout. If our entry handshakes cleanly here
it will handshake cleanly in Claude Desktop too.

Usage:
    python scripts/probe_emitted_mcp_commands.py <path-to-claude-desktop-recipe.json>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


INITIALIZE_TIMEOUT_SECONDS = 60.0


def probe_one(name: str, entry: dict) -> dict:
    command = entry.get("command")
    args = entry.get("args", [])
    env_extras = entry.get("env", {})
    if not command:
        return {"name": name, "ok": False, "reason": "missing command"}
    proc_env = os.environ.copy()
    proc_env.update({k: v for k, v in env_extras.items() if not v.startswith("YOUR_")})
    full = [command, *args]
    start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            full,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=proc_env,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        return {"name": name, "ok": False, "reason": f"command not found: {exc}"}

    initialize_msg = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "planmyagents-roundtrip-probe",
                        "version": "0.1",
                    },
                },
            }
        )
        + "\n"
    )

    try:
        assert proc.stdin is not None
        proc.stdin.write(initialize_msg)
        proc.stdin.flush()
    except BrokenPipeError as exc:
        proc.kill()
        return {"name": name, "ok": False, "reason": f"broken pipe: {exc}"}

    deadline = time.perf_counter() + INITIALIZE_TIMEOUT_SECONDS
    stdout_buf = ""
    handshake_resp = None
    assert proc.stdout is not None
    while time.perf_counter() < deadline:
        if proc.poll() is not None:
            break
        try:
            line = proc.stdout.readline()
        except Exception as exc:
            return {"name": name, "ok": False, "reason": f"read failed: {exc}"}
        if not line:
            time.sleep(0.05)
            continue
        stdout_buf += line
        try:
            parsed = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("id") == 1:
            handshake_resp = parsed
            break
    elapsed = time.perf_counter() - start

    stderr_tail = ""
    try:
        proc.terminate()
        try:
            _, stderr_tail = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr_tail = proc.communicate(timeout=2)
    except Exception:
        pass

    if handshake_resp is None:
        return {
            "name": name,
            "ok": False,
            "reason": "no initialize response within timeout",
            "elapsed_seconds": round(elapsed, 2),
            "stdout_seen_bytes": len(stdout_buf),
            "stderr_tail": stderr_tail[:500] if stderr_tail else "",
        }

    server_info = (handshake_resp.get("result") or {}).get("serverInfo") or {}
    protocol_version = (handshake_resp.get("result") or {}).get("protocolVersion")
    return {
        "name": name,
        "ok": True,
        "elapsed_seconds": round(elapsed, 2),
        "server_info": server_info,
        "protocol_version": protocol_version,
        "stderr_tail": stderr_tail[:200] if stderr_tail else "",
    }


def main(path: str) -> int:
    data = json.loads(Path(path).read_text())
    servers = data.get("mcpServers", {})
    if not servers:
        print("No mcpServers entries to probe.")
        return 1
    results = []
    for name, entry in servers.items():
        print(f"Probing {name} -> {entry.get('command')} {' '.join(entry.get('args', []))}")
        result = probe_one(name, entry)
        results.append(result)
        print(f"  -> {json.dumps(result, indent=2)}")
        print()
    ok_count = sum(1 for r in results if r["ok"])
    print(f"Summary: {ok_count}/{len(results)} mcpServers entries handshake-OK.")
    return 0 if ok_count == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
