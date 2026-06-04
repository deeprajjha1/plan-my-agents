"""Tests for `GitHubRecentlyPushedSource`. HTTP is mocked end-to-end."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.sources import github_recently_pushed as grp  # noqa: E402

REPO_FIXTURES = {
    "stripe-mcp": {
        "full_name": "stripe-org/stripe-mcp",
        "name": "stripe-mcp",
        "html_url": "https://github.com/stripe-org/stripe-mcp",
        "description": "MCP server for Stripe payments. Charge cards, refunds, payouts.",
        "owner": {"login": "stripe-org"},
        "topics": ["mcp", "mcp-server", "payments"],
        "stargazers_count": 240,
    },
    "agent-framework": {
        "full_name": "frontiermind/agent-framework",
        "name": "agent-framework",
        "html_url": "https://github.com/frontiermind/agent-framework",
        "description": "Agent platform with tool use and function calling.",
        "owner": {"login": "frontiermind"},
        "topics": ["agent-framework", "ai-agent"],
        "stargazers_count": 88,
    },
    "low-stars": {
        "full_name": "hobbyist/tiny-mcp",
        "name": "tiny-mcp",
        "html_url": "https://github.com/hobbyist/tiny-mcp",
        "description": "MCP toy.",
        "owner": {"login": "hobbyist"},
        "topics": [],
        "stargazers_count": 0,
    },
    "off-topic": {
        "full_name": "totally/unrelated",
        "name": "unrelated",
        "html_url": "https://github.com/totally/unrelated",
        "description": "Just a vim plugin",
        "owner": {"login": "totally"},
        "topics": [],
        "stargazers_count": 50,
    },
}


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self._buf

    def __exit__(self, *_):
        self._buf.close()


def _build_fake_urlopen(per_query_items: dict[str, list[str]]):
    """`per_query_items` maps a substring of the query → list of repo keys to
    return for that query."""

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        url = req.full_url
        for query_substring, keys in per_query_items.items():
            if query_substring in url:
                return _FakeResponse(
                    {"items": [REPO_FIXTURES[k] for k in keys if k in REPO_FIXTURES]}
                )
        return _FakeResponse({"items": []})

    return fake_urlopen


class GitHubRecentlyPushedSourceTests(unittest.TestCase):
    def test_emits_repos_with_capability_match(self) -> None:
        source = grp.GitHubRecentlyPushedSource(
            token="dummy",
            queries=("mcp in:description", "topic:agent-framework"),
        )
        per_query = {
            "mcp+in%3Adescription": ["stripe-mcp", "low-stars"],
            "topic%3Aagent-framework": ["agent-framework"],
        }
        with patch.object(grp.request, "urlopen", _build_fake_urlopen(per_query)):
            candidates = source.search(capabilities=set(), task_description="")

        # The normalizer's _slug collapses non-alphanumeric runs to a single
        # `-`, so `stripe-org/stripe-mcp` → `stripe-org-stripe-mcp`.
        ids = sorted(c.id for c in candidates)
        self.assertIn("stripe-org-stripe-mcp", ids)
        self.assertIn("frontiermind-agent-framework", ids)
        # `low-stars` is filtered out because stargazers_count=0 < min_stars.
        self.assertNotIn("hobbyist-tiny-mcp", ids)

    def test_no_token_means_no_op(self) -> None:
        source = grp.GitHubRecentlyPushedSource(token="")
        # If we no-op'd correctly, urlopen is never called.
        with patch.object(grp.request, "urlopen", lambda *_, **__: 1 / 0):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(candidates, [])

    def test_dedupes_same_repo_across_queries(self) -> None:
        source = grp.GitHubRecentlyPushedSource(
            token="dummy",
            queries=("mcp in:description", "topic:mcp"),
        )
        per_query = {
            "mcp+in%3Adescription": ["stripe-mcp"],
            "topic%3Amcp": ["stripe-mcp"],  # same repo
        }
        with patch.object(grp.request, "urlopen", _build_fake_urlopen(per_query)):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(len(candidates), 1)

    def test_provider_type_inferred_from_topics(self) -> None:
        source = grp.GitHubRecentlyPushedSource(token="dummy", queries=("anything",))
        with patch.object(
            grp.request,
            "urlopen",
            _build_fake_urlopen({"anything": ["stripe-mcp", "agent-framework"]}),
        ):
            candidates = source.search(capabilities=set(), task_description="")

        types = {c.id: c.provider_type for c in candidates}
        self.assertEqual(types["stripe-org-stripe-mcp"], "mcp_server")
        self.assertEqual(types["frontiermind-agent-framework"], "ai_agent")

    def test_capability_filter_applied(self) -> None:
        # See _capability_index_fixtures.py — payment_authorization
        # isn't in the shipped registry, so we extend the index for
        # this source-mechanics test only. We also seed the
        # ``extra_capability_synonyms`` keyword override on the source
        # itself: the deterministic-hash embedder shipped as default
        # doesn't have enough semantic signal on "Stripe payments" →
        # "payment_authorization" (cosine ≈ 0.24, below the 0.30
        # threshold), and ``extra_capability_synonyms`` is the
        # documented escape hatch for exactly this case where a source
        # operator wants a per-source keyword fallback. Production with
        # an Ollama embedder wouldn't need this.
        from tests._capability_index_fixtures import extended_capability_index

        source = grp.GitHubRecentlyPushedSource(
            token="dummy",
            queries=("mcp in:description",),
            extra_capability_synonyms={"payment_authorization": {"payment", "stripe"}},
        )
        with extended_capability_index(["payment_authorization"]), patch.object(
            grp.request,
            "urlopen",
            _build_fake_urlopen({"mcp+in%3Adescription": ["stripe-mcp", "agent-framework"]}),
        ):
            candidates = source.search(
                capabilities={"payment_authorization"}, task_description=""
            )
        ids = [c.id for c in candidates]
        self.assertIn("stripe-org-stripe-mcp", ids)
        self.assertNotIn("frontiermind-agent-framework", ids)

    def test_handles_network_error_silently(self) -> None:
        source = grp.GitHubRecentlyPushedSource(token="dummy")

        def boom(req, timeout=None):  # noqa: ARG001
            raise OSError("dns failure")

        with patch.object(grp.request, "urlopen", boom):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
