"""Tests for ``MoltbookSource``.

Moltbook is a social-network discovery source rather than a structured
registry, so the test fixtures need to mirror two endpoint shapes:
the search response (``/api/v1/search``) and the per-author profile
response (``/api/v1/agents/profile``). We monkey-patch ``urllib.request.urlopen``
via the source module's ``request`` binding to keep tests offline.

Coverage:

* Empty / whitespace token short-circuits before any HTTP call.
* The two-step fetch (search → profiles) wires together correctly:
  unique authors are extracted from the search results, each gets one
  profile fetch, and matching capability-bound candidates are returned.
* Authors are ranked by post count desc; first author with 3 posts
  beats one with 1 post even if the 1-post author appeared first in
  the search results (Moltbook's similarity ranking is preserved on
  ties).
* The ``max_author_profiles`` cap is honoured (we never fetch more
  profiles than configured even when the search returns N > cap
  authors).
* The shared junk filter (``is_obvious_junk``) drops test/copy moltys.
* Capability filter applied when ``capabilities`` set is non-empty.
* Profile responses with empty/short descriptions are skipped (no
  capability inference attempted).
* The default profile shape (``agent`` wrapper) and the bare-object
  shape both work — defensive parser since the docs aren't 100%
  explicit on which is canonical.
* Network/auth failures degrade to ``[]`` without raising.

Corroboration-or-drop policy (added 2026-05):

* A profile with no external artifact reference (no GitHub/npm/PyPI/
  MCP/homepage URL) is dropped before capability inference. Moltbook
  only verifies the human owner's X account, NOT the agent's
  executable surface, so requiring an artifact link is the only way
  to avoid surfacing phantom agents.
* When corroboration IS found, the strongest reference (GitHub > npm/
  PyPI > MCP > homepage) is promoted to ``vendor_url`` and
  ``evidence_url`` so the user lands on the real artifact, and the
  Moltbook profile URL is preserved in ``docs.setup_url`` for
  traceability.
* The candidate is tagged with ``verification_status="community_listed"``
  so the UI renders an honest "Self-listed (community)" pill rather
  than implying the platform validated anything.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from collections.abc import Iterable
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.sources import moltbook as mb  # noqa: E402

from tests._capability_index_fixtures import extended_capability_index  # noqa: E402

_TEST_EXTRA_CAPS = ["payment_authorization"]


SEARCH_PAGE = {
    "success": True,
    "query": "charge a card",
    "type": "all",
    "results": [
        {
            "id": "p1",
            "type": "post",
            "title": "Stripe MCP integration",
            "content": "How I wired Stripe payments...",
            "author": {"name": "PaymentMolty"},
            "similarity": 0.91,
        },
        {
            # PaymentMolty appears twice — drives them to top of the
            # ranking by post-count.
            "id": "p2",
            "type": "comment",
            "content": "Charge a card with Stripe via MCP.",
            "author": {"name": "PaymentMolty"},
            "similarity": 0.83,
        },
        {
            "id": "p3",
            "type": "post",
            "title": "MCP best practices",
            "content": "Some general MCP advice.",
            "author": {"name": "MCPNerd"},
            "similarity": 0.74,
        },
        {
            # No author payload — should be skipped without crashing.
            "id": "p4",
            "type": "comment",
            "content": "...",
            "similarity": 0.62,
        },
        {
            "id": "p5",
            "type": "post",
            "title": "test bot doing nothing",
            "content": "test test test",
            # Junk-named molty — must be dropped pre-inference even
            # if a profile lookup happens.
            "author": {"name": "test-bot-3"},
            "similarity": 0.40,
        },
    ],
    "count": 5,
    "has_more": False,
}


PROFILE_PAYMENT_MOLTY = {
    "success": True,
    "agent": {
        "name": "PaymentMolty",
        # Description includes a GitHub repo so the corroboration
        # filter accepts this molty. Without an artifact link the
        # source would (correctly) drop it as a phantom — see
        # ``test_drops_when_no_corroboration``.
        "description": (
            "Specialised AI agent that handles charge a card flows, "
            "verifies a payment, and processes refunds via Stripe MCP. "
            "Source: https://github.com/payment-molty/agent."
        ),
        "karma": 420,
        "is_claimed": True,
        "owner": {"x_handle": "PaymentMoltyOwner"},
    },
}

PROFILE_MCPNERD = {
    "success": True,
    "agent": {
        "name": "MCPNerd",
        # Description doesn't bind to any test capability; expect this
        # candidate to be dropped at the inference step.
        "description": "Loves to talk about MCP servers in the abstract.",
        "karma": 12,
        "is_claimed": False,
    },
}

PROFILE_TEST_BOT = {
    "success": True,
    "agent": {
        "name": "test-bot-3",
        "description": "Test bot for development.",
        "karma": 0,
        "is_claimed": False,
    },
}


class _FakeResponse:
    def __init__(self, payload: dict | None) -> None:
        body = json.dumps(payload or {}).encode("utf-8")
        self._buf = io.BytesIO(body)

    def __enter__(self) -> io.BytesIO:
        return self._buf

    def __exit__(self, *_: object) -> None:
        self._buf.close()


def _fake_urlopen_factory(payloads_by_substring: Iterable[tuple[str, dict | None]]):
    """Route fake urlopen by URL substring → payload.

    Tests need different responses for /search vs /agents/profile?name=X.
    Mapping by substring keeps the spec readable.
    """

    mapping = list(payloads_by_substring)
    fetched_urls: list[str] = []

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        url = getattr(req, "full_url", str(req))
        fetched_urls.append(url)
        for substr, payload in mapping:
            if substr in url:
                return _FakeResponse(payload)
        # Default 404-shape — return empty payload so source returns [].
        return _FakeResponse(None)

    fake_urlopen.fetched_urls = fetched_urls  # type: ignore[attr-defined]
    return fake_urlopen


class MoltbookSourceTests(unittest.TestCase):
    def test_skips_when_token_is_empty(self) -> None:
        source = mb.MoltbookSource(token="")
        candidates = source.search(
            capabilities=set(), task_description="anything"
        )
        self.assertEqual(candidates, [])

    def test_skips_when_token_is_whitespace_only(self) -> None:
        source = mb.MoltbookSource(token="   ")
        candidates = source.search(
            capabilities=set(), task_description="anything"
        )
        self.assertEqual(candidates, [])

    def test_skips_when_query_is_empty_and_no_capabilities(self) -> None:
        source = mb.MoltbookSource(token="fake-token")
        candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(candidates, [])

    def test_two_step_fetch_returns_validated_candidates(self) -> None:
        source = mb.MoltbookSource(
            token="moltbook_fake_deadbeef", max_author_profiles=8
        )
        fake = _fake_urlopen_factory(
            [
                ("/search", SEARCH_PAGE),
                ("name=PaymentMolty", PROFILE_PAYMENT_MOLTY),
                ("name=MCPNerd", PROFILE_MCPNERD),
                ("name=test-bot-3", PROFILE_TEST_BOT),
            ]
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mb.request, "urlopen", fake
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card with stripe"
            )

        ids = [c.id for c in candidates]
        # PaymentMolty is the only profile that binds to a real
        # capability AND clears the junk filter AND has a long-enough
        # description. MCPNerd's description doesn't bind. test-bot-3
        # is dropped by the junk filter.
        self.assertIn("moltbook-paymentmolty", ids)
        self.assertNotIn("moltbook-mcpnerd", ids)
        self.assertNotIn("moltbook-test-bot-3", ids)

        molty = next(c for c in candidates if c.id == "moltbook-paymentmolty")
        self.assertEqual(molty.provider_type, "ai_agent")
        self.assertEqual(molty.source, "moltbook")
        self.assertEqual(molty.vendor, "moltbook")
        # evidence_url and vendor_url now point to the corroborating
        # artifact (GitHub repo from the fixture description), NOT the
        # Moltbook profile. The Moltbook profile is preserved as
        # docs.setup_url for traceability — see corroboration policy.
        self.assertEqual(
            molty.evidence_url, "https://github.com/payment-molty/agent"
        )
        self.assertEqual(
            molty.vendor_url, "https://github.com/payment-molty/agent"
        )
        self.assertEqual(
            molty.docs.setup_url, "https://www.moltbook.com/u/PaymentMolty"
        )
        # Verification status must be the new "community_listed" value
        # so the UI renders "Self-listed (community)" instead of the
        # neutral default. See apps/web/src/lib/tags.ts for the mapper.
        self.assertEqual(molty.verification_status, "community_listed")

    def test_authors_ranked_by_post_count_desc(self) -> None:
        # PaymentMolty has 2 posts; MCPNerd has 1; junk has 1 (dropped).
        # Cap profile fetches at 1 to verify PaymentMolty wins the
        # single available slot — proves the rank-by-count ordering.
        source = mb.MoltbookSource(
            token="fake-token", max_author_profiles=1
        )
        fake = _fake_urlopen_factory(
            [
                ("/search", SEARCH_PAGE),
                ("name=PaymentMolty", PROFILE_PAYMENT_MOLTY),
                ("name=MCPNerd", PROFILE_MCPNERD),
            ]
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mb.request, "urlopen", fake
        ):
            source.search(capabilities=set(), task_description="charge a card")

        # Profile fetch must have been for PaymentMolty (the top-ranked
        # author), not MCPNerd or test-bot-3.
        profile_urls = [
            url for url in fake.fetched_urls if "/agents/profile" in url  # type: ignore[attr-defined]
        ]
        self.assertEqual(len(profile_urls), 1)
        self.assertIn("name=PaymentMolty", profile_urls[0])

    def test_capability_filter_applied(self) -> None:
        source = mb.MoltbookSource(
            token="fake-token", max_author_profiles=8
        )
        fake = _fake_urlopen_factory(
            [
                ("/search", SEARCH_PAGE),
                ("name=PaymentMolty", PROFILE_PAYMENT_MOLTY),
                ("name=MCPNerd", PROFILE_MCPNERD),
                ("name=test-bot-3", PROFILE_TEST_BOT),
            ]
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mb.request, "urlopen", fake
        ):
            candidates = source.search(
                capabilities={"payment_authorization"},
                task_description="charge a card",
            )
        ids = [c.id for c in candidates]
        self.assertIn("moltbook-paymentmolty", ids)
        self.assertEqual(len(candidates), 1)

        # Now request a different capability — PaymentMolty supports
        # payment_authorization, not email_verification, so it should
        # be filtered out.
        fake2 = _fake_urlopen_factory(
            [
                ("/search", SEARCH_PAGE),
                ("name=PaymentMolty", PROFILE_PAYMENT_MOLTY),
                ("name=MCPNerd", PROFILE_MCPNERD),
                ("name=test-bot-3", PROFILE_TEST_BOT),
            ]
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mb.request, "urlopen", fake2
        ):
            candidates = source.search(
                capabilities={"email_verification"},
                task_description="charge a card",
            )
        self.assertEqual(candidates, [])

    def test_short_description_skips_inference(self) -> None:
        short_profile = {
            "agent": {
                "name": "ShortMolty",
                "description": "hi",  # below MIN_DESCRIPTION_LENGTH
            }
        }
        search = {
            "results": [
                {
                    "id": "p1",
                    "type": "post",
                    "content": "anything",
                    "author": {"name": "ShortMolty"},
                }
            ]
        }
        source = mb.MoltbookSource(token="fake")
        fake = _fake_urlopen_factory(
            [("/search", search), ("name=ShortMolty", short_profile)]
        )
        with patch.object(mb.request, "urlopen", fake):
            candidates = source.search(
                capabilities=set(), task_description="anything"
            )
        self.assertEqual(candidates, [])

    def test_bare_profile_shape_works(self) -> None:
        """Defensive parser: some Moltbook deployments may return the
        agent object at the top level instead of under ``agent``."""

        bare_profile = PROFILE_PAYMENT_MOLTY["agent"]
        search = {
            "results": [
                {
                    "id": "p1",
                    "type": "post",
                    "content": "...",
                    "author": {"name": "PaymentMolty"},
                }
            ]
        }
        source = mb.MoltbookSource(token="fake")
        fake = _fake_urlopen_factory(
            [("/search", search), ("name=PaymentMolty", bare_profile)]
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mb.request, "urlopen", fake
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].id, "moltbook-paymentmolty")

    def test_search_failure_returns_empty(self) -> None:
        source = mb.MoltbookSource(token="fake")

        def boom(req, timeout=None):  # noqa: ARG001
            raise OSError("network down")

        with patch.object(mb.request, "urlopen", boom):
            candidates = source.search(
                capabilities=set(), task_description="x"
            )
        self.assertEqual(candidates, [])

    def test_profile_failure_skips_that_author_only(self) -> None:
        # Search succeeds; PaymentMolty profile fetch raises; the
        # source must move on to MCPNerd rather than failing the
        # whole search.
        source = mb.MoltbookSource(token="fake", max_author_profiles=8)

        call_log: list[str] = []

        def selective(req, timeout=None):  # noqa: ARG001
            url = getattr(req, "full_url", str(req))
            call_log.append(url)
            if "/search" in url:
                return _FakeResponse(SEARCH_PAGE)
            if "name=PaymentMolty" in url:
                raise OSError("upstream 500")
            if "name=MCPNerd" in url:
                return _FakeResponse(PROFILE_MCPNERD)
            return _FakeResponse(None)

        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mb.request, "urlopen", selective
        ):
            candidates = source.search(
                capabilities=set(), task_description="charge a card"
            )

        # MCPNerd's description doesn't bind to any test capability so
        # the candidates list is empty, but the key invariant is that
        # the search itself didn't blow up despite the per-profile
        # OSError.
        self.assertEqual(candidates, [])
        self.assertTrue(any("name=MCPNerd" in url for url in call_log))


# --- Corroboration filter tests ---------------------------------------
#
# The cases below isolate the corroboration-or-drop policy
# (``_extract_corroborations`` / ``Corroboration``) added to mitigate
# the Moltbook "phantom agent" risk: an X-verified human can register
# a profile whose description is anything they want, with no
# guarantee any executable surface exists. We require at least one
# external artifact reference and promote the strongest one to the
# evidence URL.


def _profile_with(description: str, **extra: object) -> dict:
    """Build an `agent`-shaped profile payload with a single tweak.
    Keeps the corroboration tests focused on the description-vs-link
    behaviour without re-stating the full molty shape each time.

    The default name (``PaymentMolty``) is intentional: the
    deterministic-hash embedder used in tests needs literal token /
    trigram overlap between the candidate text and the capability
    slug to bind. ``PaymentMolty`` carries the ``pay``/``aym``/etc.
    trigrams that overlap with ``payment_authorization`` (see
    ``_TEST_EXTRA_CAPS``); a generic name like ``TargetMolty``
    doesn't, and the inference would falsely classify as no
    capabilities — masking the corroboration behaviour these tests
    are actually exercising.
    """

    base = {
        "name": "PaymentMolty",
        "description": description,
        "karma": 7,
        "is_claimed": True,
    }
    base.update(extra)
    return {"success": True, "agent": base}


def _build_search(author_name: str = "PaymentMolty") -> dict:
    return {
        "results": [
            {
                "id": "p1",
                "type": "post",
                "content": "anything",
                "author": {"name": author_name},
            }
        ]
    }


def _capability_seed_text() -> str:
    """Return text known to bind to the test capability index so a
    candidate would otherwise pass through capability inference. We
    re-use the same `payment_authorization` synonyms the rest of the
    fixtures rely on (see `_TEST_EXTRA_CAPS`).
    """

    return (
        "Specialised AI agent that handles charge a card flows, "
        "verifies a payment, and processes refunds via Stripe MCP."
    )


class CorroborationFilterTests(unittest.TestCase):
    """Tests for the Moltbook phantom-agent mitigation.

    Each test seeds the description with the same capability text so
    the only variable being exercised is the corroboration outcome.
    """

    def _run_with_profile(self, profile: dict) -> list:
        source = mb.MoltbookSource(token="fake", max_author_profiles=4)
        fake = _fake_urlopen_factory(
            [
                ("/search", _build_search()),
                ("name=PaymentMolty", profile),
            ]
        )
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            mb.request, "urlopen", fake
        ):
            return source.search(
                capabilities=set(),
                task_description="charge a card with stripe",
            )

    def test_drops_when_no_corroboration(self) -> None:
        """Description has the right capability text but no external
        URL → must be dropped. This is the core phantom-agent guard.
        """

        profile = _profile_with(_capability_seed_text())
        candidates = self._run_with_profile(profile)
        self.assertEqual(candidates, [])

    def test_keeps_when_github_link_present(self) -> None:
        profile = _profile_with(
            _capability_seed_text()
            + " Source: https://github.com/example-org/payment-agent."
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].evidence_url,
            "https://github.com/example-org/payment-agent",
        )
        self.assertEqual(
            candidates[0].verification_status, "community_listed"
        )

    def test_keeps_when_npm_link_present(self) -> None:
        profile = _profile_with(
            _capability_seed_text()
            + " Install: https://www.npmjs.com/package/payment-agent"
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].evidence_url,
            "https://www.npmjs.com/package/payment-agent",
        )

    def test_keeps_when_pypi_link_present(self) -> None:
        profile = _profile_with(
            _capability_seed_text()
            + " Get it: https://pypi.org/project/payment-agent/"
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(len(candidates), 1)
        # Trailing slash is part of the PyPI canonical URL — preserved
        # exactly as written.
        self.assertEqual(
            candidates[0].evidence_url,
            "https://pypi.org/project/payment-agent/",
        )

    def test_keeps_when_mcp_so_link_present(self) -> None:
        profile = _profile_with(
            _capability_seed_text()
            + " Listed at https://mcp.so/server/payment-stripe."
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].evidence_url,
            "https://mcp.so/server/payment-stripe",
        )

    def test_keeps_when_third_party_homepage_present(self) -> None:
        """A plain non-Moltbook https homepage is the weakest accepted
        signal but still better than no link at all."""

        profile = _profile_with(
            _capability_seed_text()
            + " Homepage: https://payment-agent.example/"
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].evidence_url, "https://payment-agent.example/"
        )

    def test_drops_when_only_self_referential_moltbook_link(self) -> None:
        """A profile description that links back to its own Moltbook
        page provides zero corroboration — must be dropped.
        """

        profile = _profile_with(
            _capability_seed_text()
            + " More: https://www.moltbook.com/u/PaymentMolty"
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(candidates, [])

    def test_drops_when_only_url_shortener(self) -> None:
        """We can't see what a shortener resolves to without expanding
        it inline (which we won't). Treat as no corroboration.
        """

        profile = _profile_with(
            _capability_seed_text() + " Link: https://bit.ly/payment-agent"
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(candidates, [])

    def test_drops_when_only_x_handle_link(self) -> None:
        """A link to the owner's Twitter/X profile proves nothing about
        the agent — same as the X-handle owner verification. Drop.
        """

        profile = _profile_with(
            _capability_seed_text() + " Owner: https://x.com/some-owner"
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(candidates, [])

    def test_drops_when_only_http_homepage(self) -> None:
        """Plain http (not https) homepages are almost always parked
        domains in 2026; require the cheap-to-get https upgrade as an
        ambient quality filter.
        """

        profile = _profile_with(
            _capability_seed_text() + " Homepage: http://payment-agent.example/"
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(candidates, [])

    def test_strongest_corroboration_wins_when_multiple_links(self) -> None:
        """When a description carries multiple candidate links, the
        promotion to evidence_url must follow the documented priority
        (github > npm/pypi > mcp > homepage). This guarantees we
        never accidentally show users a weaker reference (e.g. a
        homepage) when a stronger one (e.g. the GitHub repo) is right
        there.
        """

        profile = _profile_with(
            _capability_seed_text()
            + " Homepage: https://payment-agent.example/. "
            + "Source: https://github.com/example-org/payment-agent. "
            + "Install: https://www.npmjs.com/package/payment-agent."
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].evidence_url,
            "https://github.com/example-org/payment-agent",
        )

    def test_corroboration_from_structured_homepage_field(self) -> None:
        """If/when Moltbook ships a structured ``homepage_url`` field on
        profiles, we must accept it as corroboration too — not require
        the URL to be in free-text only.
        """

        profile = _profile_with(
            _capability_seed_text(),
            homepage_url="https://github.com/example-org/payment-agent",
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].evidence_url,
            "https://github.com/example-org/payment-agent",
        )

    def test_corroboration_from_links_array(self) -> None:
        """Same idea for a structured ``links: [{url}]`` array. Defensive
        because the Moltbook schema is iterating and the docs are
        prescriptive about a few fields and silent about others.
        """

        profile = _profile_with(
            _capability_seed_text(),
            links=[
                {"url": "https://github.com/example-org/payment-agent"},
                "https://payment-agent.example/",
            ],
        )
        candidates = self._run_with_profile(profile)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].evidence_url,
            "https://github.com/example-org/payment-agent",
        )

    def test_corroboration_from_recent_posts(self) -> None:
        """Real-world Moltbook bios are Twitter-style vibe statements
        with no URLs (live diagnostic 2026-05-14). Technical moltys
        link to their actual artifacts in their *posts*. The
        ``/agents/profile`` envelope returns ``recentPosts`` /
        ``recentComments`` arrays in the same response — scanning
        them is what makes the corroboration filter actually catch
        genuine technical moltys vs. drop them with the phantoms.
        """

        # Bare bio with no link, like real moltys (h1up, ModelT800,
        # auroras_happycapy from the live diagnostic). The seed text
        # binds the capability; corroboration must come from posts.
        envelope = {
            "success": True,
            "agent": {
                "name": "PaymentMolty",
                "description": _capability_seed_text(),
                "karma": 1234,
                "is_claimed": True,
            },
            "recentPosts": [
                {
                    "id": "p1",
                    "type": "post",
                    "title": "Built a stripe MCP server this week",
                    "content": (
                        "Source on GitHub: https://github.com/payment-molty/agent. "
                        "Demos and docs in the README."
                    ),
                },
            ],
        }
        candidates = self._run_with_profile(envelope)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].evidence_url,
            "https://github.com/payment-molty/agent",
        )
        self.assertEqual(
            candidates[0].verification_status, "community_listed"
        )

    def test_corroboration_from_recent_comments(self) -> None:
        """Same for ``recentComments`` — the search ``type=comment``
        results route here, and a comment can be where a molty
        replies to someone with their own GitHub link.
        """

        envelope = {
            "success": True,
            "agent": {
                "name": "PaymentMolty",
                "description": _capability_seed_text(),
                "karma": 200,
                "is_claimed": True,
            },
            "recentComments": [
                {
                    "id": "c1",
                    "type": "comment",
                    "content": (
                        "Yeah I solved this exact problem — see my repo at "
                        "https://github.com/payment-molty/stripe-mcp"
                    ),
                },
            ],
        }
        candidates = self._run_with_profile(envelope)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].evidence_url,
            "https://github.com/payment-molty/stripe-mcp",
        )

    def test_recent_posts_scan_ignores_search_highlighting_tags(self) -> None:
        """Moltbook search results wrap matched *words* in ``<mark>``
        tags (verified against live API 2026-05-14: the markup wraps
        keywords like ``<mark>stripe</mark>`` but never wraps URL
        substrings). The URL regex anchored on ``http(s)://`` walks
        right past the surrounding word-level markup.
        """

        envelope = {
            "agent": {
                "name": "PaymentMolty",
                "description": _capability_seed_text(),
            },
            "recentPosts": [
                {
                    "title": "<mark>stripe</mark> MCP server",
                    "content": (
                        "Built a <mark>stripe</mark> MCP server. "
                        "Repo at https://github.com/payment-molty/agent "
                        "and live at https://payment-molty.example/"
                    ),
                },
            ],
        }
        candidates = self._run_with_profile(envelope)
        self.assertEqual(len(candidates), 1)
        # GitHub repo wins the priority sort over the homepage URL.
        self.assertEqual(
            candidates[0].evidence_url,
            "https://github.com/payment-molty/agent",
        )

    def test_bare_codehost_root_url_does_not_corroborate(self) -> None:
        """A link to ``https://github.com/`` (no owner/repo) proves
        nothing — it's a link to GitHub the company, not to a repo.
        Same for the npm/PyPI/etc. landing pages. Without a path the
        kind regexes don't match, and the homepage fallback now
        explicitly denies these bare-host roots so we don't
        accidentally accept them as weak corroboration.
        """

        envelope = {
            "agent": {
                "name": "PaymentMolty",
                "description": _capability_seed_text(),
            },
            "recentPosts": [
                {
                    "content": (
                        "Check https://github.com/ and https://npmjs.com/"
                    ),
                },
            ],
        }
        candidates = self._run_with_profile(envelope)
        self.assertEqual(candidates, [])

    def test_recent_posts_self_referential_links_dont_corroborate(self) -> None:
        """A molty whose recent posts link only to other Moltbook
        posts (e.g. quoting another submol) provides no external
        corroboration — must be dropped, same rule as bio links.
        """

        envelope = {
            "agent": {
                "name": "PaymentMolty",
                "description": _capability_seed_text(),
            },
            "recentPosts": [
                {
                    "content": (
                        "Replying to https://www.moltbook.com/p/abc and "
                        "https://www.moltbook.com/u/SomeOther"
                    ),
                },
            ],
        }
        candidates = self._run_with_profile(envelope)
        self.assertEqual(candidates, [])

    def test_recent_posts_malformed_entries_dont_crash(self) -> None:
        """``recentPosts`` from a future schema iteration may include
        non-dict entries or be missing entirely. The scanner must
        return ``[]`` rather than raise so the caller can fall back
        to the bio/links path cleanly.
        """

        # Mix of malformed entries: string, None, dict-without-fields.
        # Plus a valid one at the end so we know the scanner kept
        # going past the bad ones.
        envelope = {
            "agent": {
                "name": "PaymentMolty",
                "description": _capability_seed_text(),
            },
            "recentPosts": [
                "this is not a dict",
                None,
                {"unrelated_field": 42},
                {
                    "content": "Valid: https://github.com/payment-molty/agent"
                },
            ],
        }
        candidates = self._run_with_profile(envelope)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].evidence_url,
            "https://github.com/payment-molty/agent",
        )


if __name__ == "__main__":
    unittest.main()
