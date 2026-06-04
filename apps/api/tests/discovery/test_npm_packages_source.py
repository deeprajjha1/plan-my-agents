"""Tests for ``NpmMcpPackagesSource``.

We monkey-patch ``urllib.request.urlopen`` (via the source module's
``request`` binding) so the tests never touch the network. Fixtures
mirror the documented response shape from
``https://registry.npmjs.org/-/v1/search``, captured live on
2026-05-20 (see the source-module docstring for the verified payload).
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.sources import npm_packages as npm  # noqa: E402

from tests._capability_index_fixtures import extended_capability_index  # noqa: E402

_TEST_EXTRA_CAPS = ["payment_authorization", "ai_inference", "email_send"]


def _pkg_object(
    *,
    name: str,
    description: str,
    keywords: list[str] | None = None,
    repository: str | None = None,
    dependents: int = 0,
    weekly_downloads: int = 0,
    score_final: float = 0.0,
) -> dict:
    """Helper: synthesise one npm search ``objects[i]`` row.

    Mirrors the fields the source actually reads — kept small so the
    test fixtures stay readable even with several entries per page.
    """

    return {
        "package": {
            "name": name,
            "description": description,
            "keywords": keywords or [],
            "version": "1.0.0",
            "date": "2026-05-01T00:00:00.000Z",
            "links": {
                "homepage": "",
                "repository": (
                    f"git+{repository}.git" if repository else ""
                ),
                "npm": f"https://www.npmjs.com/package/{name}",
            },
        },
        "dependents": str(dependents) if dependents else "0",
        "downloads": {
            "weekly": weekly_downloads,
            "monthly": weekly_downloads * 4,
        },
        "score": {
            "final": score_final,
            "detail": {
                "popularity": 0.5,
                "quality": 0.5,
                "maintenance": 0.5,
            },
        },
    }


PAGE_1 = {
    "total": 5,
    "objects": [
        # High-signal entry: official MCP package, scoped, big
        # dependents count + downloads. Description anchored on
        # "email send" so it binds to the email_send capability
        # added in _TEST_EXTRA_CAPS (the live registry doesn't
        # carry a "memory" capability, so anchoring there would
        # require a separate cap extension that this fixture
        # doesn't need).
        _pkg_object(
            name="@modelcontextprotocol/server-memory",
            description="Send an email and store persistent memory of every send for AI agents.",
            keywords=["mcp", "model-context-protocol", "memory", "email"],
            repository="https://github.com/modelcontextprotocol/servers",
            dependents=120,
            weekly_downloads=50000,
            score_final=0.65,
        ),
        # Vendor MCP server: payment_authorization
        _pkg_object(
            name="@stripe/mcp-payments",
            description="Charge a card, verify a payment, refund a payment.",
            keywords=["mcp", "payments"],
            repository="https://github.com/stripe/mcp-payments",
            dependents=5,
            weekly_downloads=1200,
            score_final=0.35,
        ),
        # AI inference MCP, all corroboration via score + downloads
        # (no GitHub repo URL).
        _pkg_object(
            name="upstash-mcp-ai-inference",
            description="Run AI inference across image, video, audio models.",
            keywords=["mcp", "ai", "inference"],
            repository=None,
            dependents=0,
            weekly_downloads=800,
            score_final=0.12,
        ),
        # Junk by token: should be dropped pre-inference.
        _pkg_object(
            name="mcp-test-server-foo",
            description="A test server for development.",
            keywords=["mcp"],
            repository="https://github.com/foo/mcp-test-server-foo",
            dependents=1,
            weekly_downloads=10,
            score_final=0.05,
        ),
        # No corroboration: no repo, 0 dependents, 0 downloads, low
        # score, no useful capability description. Should be dropped
        # by the corroboration policy BEFORE capability inference.
        _pkg_object(
            name="published-but-untested",
            description="Just published; no usage yet.",
            keywords=[],
            repository=None,
            dependents=0,
            weekly_downloads=0,
            score_final=0.0,
        ),
    ],
}

# Second page — used to validate paging stops correctly when
# the cumulative results equal ``total``.
PAGE_2 = {
    "total": 5,
    "objects": [],
}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self) -> io.BytesIO:
        return self._buf

    def __exit__(self, *_: object) -> None:
        self._buf.close()


def _fake_urlopen_factory(pages: list[dict]):
    iterator = iter(pages)

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(next(iterator))

    return fake_urlopen


class NpmMcpPackagesSourceTests(unittest.TestCase):
    def test_normalises_real_shape_and_drops_junk(self) -> None:
        source = npm.NpmMcpPackagesSource(max_pages=1, page_size=100)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            npm.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(
                capabilities=set(),
                task_description="charge a card",
            )

        ids = sorted(c.id for c in candidates)
        self.assertIn("modelcontextprotocol-server-memory", ids)
        self.assertIn("stripe-mcp-payments", ids)
        self.assertIn("upstash-mcp-ai-inference", ids)
        # Junk-by-token name: dropped pre-inference (the shared MCP
        # publication-quality filter catches "test").
        self.assertNotIn("mcp-test-server-foo", ids)
        # No corroboration signal at all → dropped before
        # capability inference even runs.
        self.assertNotIn("published-but-untested", ids)

    def test_repository_url_is_unwrapped(self) -> None:
        """npm wraps ``links.repository`` as ``git+https://...git``;
        the source must strip the prefix/suffix so vendor_url is the
        clean HTTPS form the rest of the discovery layer assumes."""

        source = npm.NpmMcpPackagesSource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            npm.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )
        stripe = next(c for c in candidates if c.id == "stripe-mcp-payments")
        self.assertEqual(
            stripe.vendor_url, "https://github.com/stripe/mcp-payments"
        )

    def test_install_command_uses_npx(self) -> None:
        """Install commands must be ``npx -y <pkg>`` so the recipe
        export pipeline's npm-resolvability check (Sprint 2 P1-6) can
        actually resolve them. Asserted via ``docs.install_steps[0]``
        — the canonical place the normaliser reads ordered install
        steps from."""

        source = npm.NpmMcpPackagesSource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            npm.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )
        memory = next(
            c
            for c in candidates
            if c.id == "modelcontextprotocol-server-memory"
        )
        self.assertEqual(
            memory.docs.install_steps,
            ["npx -y @modelcontextprotocol/server-memory"],
        )

    def test_capability_filter_applied_when_requested(self) -> None:
        source = npm.NpmMcpPackagesSource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            npm.request, "urlopen", _fake_urlopen_factory([PAGE_1])
        ):
            candidates = source.search(
                capabilities={"payment_authorization"},
                task_description="charge a card",
            )
        ids = [c.id for c in candidates]
        self.assertIn("stripe-mcp-payments", ids)
        # AI inference entry → unrelated capability → dropped.
        self.assertNotIn("upstash-mcp-ai-inference", ids)

    def test_handles_empty_payload_gracefully(self) -> None:
        source = npm.NpmMcpPackagesSource(max_pages=1)
        with patch.object(
            npm.request, "urlopen", _fake_urlopen_factory([{}])
        ):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        self.assertEqual(candidates, [])

    def test_handles_network_error_gracefully(self) -> None:
        from urllib import error as urlerror

        def boom(req, timeout=None):  # noqa: ARG001
            raise urlerror.URLError("dns unhappy")

        source = npm.NpmMcpPackagesSource(max_pages=1)
        with patch.object(npm.request, "urlopen", boom):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        self.assertEqual(candidates, [])

    def test_corroboration_policy_dropdowns_zero_signal_entries(self) -> None:
        """A package with zero downloads, zero dependents, no repo,
        and a sub-threshold score is dropped before the (more
        expensive) capability inference runs.

        Calibrated thresholds:
        * ``min_dependents_for_signal`` = 1
        * ``min_weekly_downloads_for_signal`` = 25
        * ``min_score_for_signal`` = 0.05
        Plus the "has github/gitlab repo" gate. ANY one passes.
        """

        zero_signal = {
            "total": 1,
            "objects": [
                _pkg_object(
                    name="mcp-skeleton-x",
                    description="A real MCP server that does charge a card.",
                    keywords=["mcp"],
                    repository=None,
                    dependents=0,
                    weekly_downloads=0,
                    score_final=0.0,
                ),
            ],
        }
        source = npm.NpmMcpPackagesSource(max_pages=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            npm.request, "urlopen", _fake_urlopen_factory([zero_signal])
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
