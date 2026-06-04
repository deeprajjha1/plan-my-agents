#!/usr/bin/env python3
"""Validate every URL referenced in docs/outreach/ drafts is reachable.

Why this exists:
    Cold-email drafts are only useful if every link they cite still
    returns 200 from the live site. When the homepage layout changes
    or an agent slug gets renamed, the drafts go silently stale and
    the next batch of cold sends embarrasses the founder.

    This script walks every Markdown file under docs/outreach/,
    extracts every URL (whether inline `<https://…>`, plain `https://…`,
    Markdown `[text](https://…)`, or a frontmatter `live_urls:` block),
    deduplicates them, and HEAD-checks each. Non-200 responses (or
    network failures) print a clear diff against the file that cited
    the URL.

Behaviour:
    * Defaults to the production host `https://planmyagents.dev`. Pass
      `--base-url http://127.0.0.1:3000` to validate against the
      local dev server instead (useful during a sprint).
    * Local-only / mailto / placeholder URLs (`{first_name}`, etc.)
      are skipped.
    * GET fallback is attempted automatically when a HEAD returns 405
      or 501 (some hosts block HEAD).
    * Exit code is 0 only if every URL responds with 2xx or 3xx; any
      failure flips the exit to 1 so this slots cleanly into CI / a
      pre-send hook.

Run:
    python3 scripts/validate_outreach_urls.py
    python3 scripts/validate_outreach_urls.py --base-url http://127.0.0.1:3000
    make outreach-validate-urls

Output (truncated):
    OK   https://planmyagents.dev/                      (homepage)
    OK   https://planmyagents.dev/agents/firecrawl      (vendor:firecrawl)
    FAIL https://planmyagents.dev/agents/exa-search     (vendor:exa)
         cited in: docs/outreach/vendors/exa.md
         reason:   404 Not Found
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUTREACH_ROOT = REPO_ROOT / "docs" / "outreach"

DEFAULT_BASE_URL = "https://planmyagents.dev"

URL_REGEX = re.compile(r"https?://[^\s)>\]'\"<]+")
MARKDOWN_LINK_REGEX = re.compile(r"\]\((https?://[^\s)]+)\)")
FRONTMATTER_URL_REGEX = re.compile(r"^\s*-\s+(https?://\S+)\s*$", re.MULTILINE)

PLACEHOLDER_MARKERS = ("{", "<", ">", "}")
SKIP_HOSTS = {"calendly.com"}


def _looks_like_placeholder(url: str) -> bool:
    return any(marker in url for marker in PLACEHOLDER_MARKERS)


def _normalize_url(url: str, base_url: str) -> str:
    """Rewrite production URLs to the configured base if asked.

    Lets the same drafts be validated against a local dev server
    without editing every link.
    """
    parsed = urlparse(url)
    base_parsed = urlparse(base_url)
    if parsed.hostname == "planmyagents.dev" and base_parsed.hostname not in {
        "planmyagents.dev",
        None,
    }:
        return f"{base_url.rstrip('/')}{parsed.path or '/'}"
    return url


def _strip_trailing_punctuation(url: str) -> str:
    # Markdown often wraps URLs in inline-code backticks (`url`) or
    # quotes ("url") — the regex sees the trailing delimiter as part
    # of the URL because there's no whitespace before it. Strip them
    # along with the usual sentence-end punctuation.
    while url and url[-1] in ".,;:!?`\"'":
        url = url[:-1]
    return url


def _extract_urls(text: str) -> set[str]:
    raw: set[str] = set()
    raw.update(_strip_trailing_punctuation(u) for u in URL_REGEX.findall(text))
    raw.update(MARKDOWN_LINK_REGEX.findall(text))
    raw.update(FRONTMATTER_URL_REGEX.findall(text))
    return {u for u in raw if not _looks_like_placeholder(u)}


def _iter_drafts(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file())


def _check_url(url: str, *, timeout_s: float = 6.0) -> tuple[bool, str]:
    """Return (ok, reason).

    Tries HEAD first; falls back to GET on 405 / 501 / connection-level
    error because some hosts (notably Resend's marketing site) block
    HEAD entirely.
    """
    headers = {
        "User-Agent": "planmyagents-outreach-url-validator/1.0",
        "Accept": "*/*",
    }
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                code = resp.getcode()
                if 200 <= code < 400:
                    return True, f"{code} {resp.reason}"
                if method == "HEAD" and code in (405, 501):
                    continue
                return False, f"{code} {resp.reason}"
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (405, 501):
                continue
            return False, f"{exc.code} {exc.reason}"
        except (urllib.error.URLError, TimeoutError) as exc:  # type: ignore[arg-type]
            if method == "HEAD":
                continue
            return False, f"network error: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"unexpected: {exc}"
    return False, "no successful HEAD or GET"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=(
            "Replace planmyagents.dev with this base URL when checking "
            "(default: https://planmyagents.dev). Useful for dev: "
            "--base-url http://127.0.0.1:3000."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=OUTREACH_ROOT,
        help="Root of the outreach drafts (default: docs/outreach).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=6.0,
        help="Per-URL HTTP timeout in seconds (default: 6).",
    )
    args = parser.parse_args()

    drafts = list(_iter_drafts(args.root))
    if not drafts:
        print(f"no outreach drafts found under {args.root}", file=sys.stderr)
        return 1

    url_to_files: dict[str, list[Path]] = defaultdict(list)
    for draft in drafts:
        text = draft.read_text(encoding="utf-8")
        for url in _extract_urls(text):
            if urlparse(url).hostname in SKIP_HOSTS:
                continue
            normalized = _normalize_url(url, args.base_url)
            url_to_files[normalized].append(draft)

    if not url_to_files:
        print("no external URLs found to validate", file=sys.stderr)
        return 0

    def _relativize(path: Path) -> str:
        # When the validator is exercised against a synthetic tmp tree
        # (unit tests), the draft paths live outside REPO_ROOT and the
        # bare `relative_to(REPO_ROOT)` call raises ValueError. Fall
        # back to the absolute path string so the report still prints.
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return str(path)

    failures: list[tuple[str, str, list[str]]] = []
    for url in sorted(url_to_files):
        ok, reason = _check_url(url, timeout_s=args.timeout)
        files = sorted({_relativize(p) for p in url_to_files[url]})
        tag = "OK  " if ok else "FAIL"
        suffix = "  ".join(files)
        print(f"{tag} {url}    [{suffix}]    {reason}")
        if not ok:
            failures.append((url, reason, files))

    if failures:
        print(
            f"\n{len(failures)} URL(s) failed validation. "
            "Fix the drafts or the live site before sending.",
            file=sys.stderr,
        )
        return 1

    print(f"\nAll {len(url_to_files)} URL(s) validated OK against {args.base_url}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
