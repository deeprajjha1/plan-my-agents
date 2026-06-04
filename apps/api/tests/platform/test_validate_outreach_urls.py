"""Tests for scripts/validate_outreach_urls.py.

Hits a local stub HTTP server rather than the public internet so the
suite stays hermetic. We assert:

  * Markdown URLs in `[text](https://...)` form get extracted.
  * Inline `https://...` URLs in body text get extracted.
  * `live_urls:` frontmatter list items get extracted.
  * Placeholders like `{first_name}` and `<your_calendly>` do not get
    extracted (they would always 404 the validator).
  * Trailing punctuation (`.`, `,`) gets stripped before fetching.
  * `--base-url http://...:port` rewrites `planmyagents.dev` references
    to that base so a draft can be sanity-checked against the local
    dev server.
  * Redirects (3xx) count as OK.
  * Hosts that block HEAD (return 405) are retried with GET.
  * Exit code is 0 when every URL responds with 2xx/3xx, 1 otherwise.

These are the behaviours that protect cold-email drafts from going
stale silently when the site layout changes.
"""

from __future__ import annotations

import http.server
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "verification" / "validate_outreach_urls.py"


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Return whatever status code the URL path requests."""

    # silence noisy http.server access log.
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: ARG002
        return

    def _respond(self) -> None:
        path = self.path
        if path.startswith("/notfound"):
            code = 404
        elif path.startswith("/redirect"):
            code = 302
        elif path.startswith("/servererror"):
            code = 500
        elif path == "/no-head":
            code = 405 if self.command == "HEAD" else 200
        else:
            code = 200
        self.send_response(code)
        self.send_header("Content-Length", "0")
        if code == 302:
            self.send_header("Location", "/ok/after-redirect")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802 - http.server API
        self._respond()

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self._respond()


def _start_stub_server() -> tuple[str, socketserver.TCPServer, threading.Thread]:
    server = socketserver.TCPServer(("127.0.0.1", 0), _StubHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    time.sleep(0.05)
    return base, server, thread


def _write_draft(folder: Path, name: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(body, encoding="utf-8")


def _run_validator(
    outreach_root: Path, base_url: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(outreach_root),
            "--base-url",
            base_url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


class ValidateOutreachUrlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_url, cls.server, cls.thread = _start_stub_server()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        self._tmpdir_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir_ctx.name)

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def test_extracts_and_validates_all_url_forms(self) -> None:
        outreach = self.tmp / "outreach"
        _write_draft(
            outreach / "vendors",
            "happy.md",
            f"""---
audience: vendor
target: Happy
live_urls:
  - {self.base_url}/ok/from-frontmatter
last_validated: 2026-01-01
---

# Subject

Body refers to {self.base_url}/ok/inline and to [a labeled link]({self.base_url}/ok/labeled).
Trailing punctuation should be stripped: see {self.base_url}/ok/punct.

Placeholders like {{first_name}} or <your_calendly> are NOT URLs and must
not be fetched.

PS — and {self.base_url}/no-head should pass via the GET fallback.
""",
        )

        result = _run_validator(outreach, self.base_url)
        self.assertEqual(
            result.returncode, 0, msg=result.stderr + result.stdout
        )
        out = result.stdout
        self.assertIn("from-frontmatter", out)
        self.assertIn("inline", out)
        self.assertIn("labeled", out)
        self.assertIn("punct", out)
        self.assertIn("no-head", out)
        self.assertNotIn("first_name", out)
        self.assertNotIn("FAIL", out)

    def test_fails_when_any_url_404s(self) -> None:
        outreach = self.tmp / "outreach"
        _write_draft(
            outreach / "vendors",
            "broken.md",
            (
                f"OK link: {self.base_url}/ok/ping\n\n"
                f"Broken: {self.base_url}/notfound/oops\n"
            ),
        )

        result = _run_validator(outreach, self.base_url)
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("notfound", result.stdout)

    def test_base_url_rewrites_production_planmyagents_dev_references(self) -> None:
        outreach = self.tmp / "outreach"
        _write_draft(
            outreach / "vendors",
            "prod-link.md",
            "Visit https://planmyagents.dev/partners and "
            "https://planmyagents.dev/agents/firecrawl.\n",
        )

        result = _run_validator(outreach, self.base_url)
        self.assertEqual(
            result.returncode, 0, msg=result.stdout + result.stderr
        )
        self.assertNotIn("planmyagents.dev", result.stdout)
        self.assertIn("/partners", result.stdout)
        self.assertIn("/agents/firecrawl", result.stdout)

    def test_redirects_count_as_ok(self) -> None:
        outreach = self.tmp / "outreach"
        _write_draft(
            outreach / "vendors",
            "redirect.md",
            f"Goto {self.base_url}/redirect/x\n",
        )
        result = _run_validator(outreach, self.base_url)
        self.assertEqual(result.returncode, 0, msg=result.stdout)

    def test_servererror_is_failure(self) -> None:
        outreach = self.tmp / "outreach"
        _write_draft(
            outreach / "vendors",
            "broken.md",
            f"Backend down: {self.base_url}/servererror/blah\n",
        )
        result = _run_validator(outreach, self.base_url)
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        self.assertIn("FAIL", result.stdout)

    def test_no_drafts_directory_returns_nonzero(self) -> None:
        result = _run_validator(self.tmp / "does-not-exist", "http://127.0.0.1:1")
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
