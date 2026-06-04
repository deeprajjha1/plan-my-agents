"""HTTP-level tests for the /recipe/export endpoint and the goal cache
that backs it.

Uses FastAPI's TestClient + monkey-patched goal-cache backend so the
test never reaches the discovery / LLM / store layer. The /goal
pipeline is exercised indirectly by inserting a synthetic plan into
the cache via the module-level cache singleton.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402
from planmyagents_api.planner.recipe_export.goal_cache import (  # noqa: E402
    DEFAULT_TTL_SECONDS,
    InMemoryGoalCache,
    JsonFileGoalCache,
    compute_goal_id,
    goal_cache_from_env,
)

SAMPLE_PLAN = {
    "status": "executable",
    "summary": "Verify the email then enrich.",
    "sub_tasks": [
        {
            "capability": "email_verification",
            "description": "Verify the address.",
            "inputs": {"email": "deepraj@example.com"},
        },
        {
            "capability": "contact_enrichment",
            "description": "Enrich the contact.",
            "inputs": {},
        },
    ],
    "discovery": {
        "agentic_results": [
            {
                "id": "hunter-mcp",
                "display_name": "Hunter MCP",
                "provider_type": "mcp_server",
                "docs_url": "https://hunter.io",
                "install_command": "npx -y @hunter/mcp",
                "required_env_vars": ["HUNTER_API_KEY"],
                "capabilities": [{"id": "email_verification"}],
            }
        ]
    },
}


def _build_client_with_cache(cache):
    """Build a FastAPI TestClient with a known cache backend injected."""
    from planmyagents_api.web import app as app_module

    app_module._GOAL_CACHE = cache
    app = app_module.create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Goal cache backends.
# ---------------------------------------------------------------------------


class ComputeGoalIdTest(unittest.TestCase):
    def test_deterministic_for_same_goal_and_plan(self) -> None:
        a = compute_goal_id("Verify email", SAMPLE_PLAN)
        b = compute_goal_id("Verify email", SAMPLE_PLAN)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("g_"))

    def test_differs_for_different_goal_text(self) -> None:
        a = compute_goal_id("Verify email", SAMPLE_PLAN)
        b = compute_goal_id("Enrich contact", SAMPLE_PLAN)
        self.assertNotEqual(a, b)

    def test_invariant_under_discovery_enrichment(self) -> None:
        # discovery field changing should NOT change the goal_id —
        # background discovery refresh would otherwise invalidate
        # share-able recipe URLs.
        plan_a = {**SAMPLE_PLAN, "discovery": {"agentic_results": []}}
        plan_b = SAMPLE_PLAN
        self.assertEqual(
            compute_goal_id("Verify email", plan_a),
            compute_goal_id("Verify email", plan_b),
        )


class InMemoryGoalCacheTest(unittest.TestCase):
    def test_save_then_load_round_trips(self) -> None:
        cache = InMemoryGoalCache()
        cache.save(
            goal_id="g_abc",
            goal_text="Verify email",
            plan_payload=SAMPLE_PLAN,
        )
        cached = cache.load("g_abc")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.goal_id, "g_abc")
        self.assertEqual(cached.plan_payload, SAMPLE_PLAN)

    def test_load_returns_none_for_unknown_id(self) -> None:
        self.assertIsNone(InMemoryGoalCache().load("g_missing"))

    def test_expired_entries_are_evicted(self) -> None:
        cache = InMemoryGoalCache(ttl_seconds=1)
        cache.save(goal_id="g_e", goal_text="t", plan_payload={})
        with patch("planmyagents_api.planner.recipe_export.goal_cache.time.time",
                   return_value=time.time() + 10):
            self.assertIsNone(cache.load("g_e"))


class JsonFileGoalCacheTest(unittest.TestCase):
    def test_persists_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "goal-cache.jsonl"
            first = JsonFileGoalCache(path)
            first.save(goal_id="g_p", goal_text="Verify", plan_payload=SAMPLE_PLAN)
            second = JsonFileGoalCache(path)
            cached = second.load("g_p")
            self.assertIsNotNone(cached)
            self.assertEqual(cached.plan_payload, SAMPLE_PLAN)

    def test_latest_entry_wins_on_repeated_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = JsonFileGoalCache(Path(tmp) / "c.jsonl")
            cache.save(goal_id="g_x", goal_text="v1", plan_payload={"v": 1})
            cache.save(goal_id="g_x", goal_text="v2", plan_payload={"v": 2})
            cached = cache.load("g_x")
            self.assertIsNotNone(cached)
            self.assertEqual(cached.plan_payload, {"v": 2})
            self.assertEqual(cached.goal_text, "v2")


class GoalCacheFromEnvTest(unittest.TestCase):
    def test_defaults_to_in_memory_when_env_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            cache = goal_cache_from_env()
        self.assertIsInstance(cache, InMemoryGoalCache)

    def test_returns_json_file_when_env_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.jsonl"
            with patch.dict(
                os.environ,
                {"PLANMYAGENTS_GOAL_CACHE_PATH": str(path)},
                clear=True,
            ):
                cache = goal_cache_from_env()
        self.assertIsInstance(cache, JsonFileGoalCache)


# ---------------------------------------------------------------------------
# /recipe/export HTTP behaviour.
# ---------------------------------------------------------------------------


class RecipeExportEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = InMemoryGoalCache(ttl_seconds=DEFAULT_TTL_SECONDS)
        self.cache.save(
            goal_id="g_known",
            goal_text="Verify the email then enrich.",
            plan_payload=SAMPLE_PLAN,
        )
        self.client = _build_client_with_cache(self.cache)

    def test_unknown_format_returns_400(self) -> None:
        resp = self.client.get(
            "/recipe/export",
            params={"goal_id": "g_known", "format": "totally_not_a_format"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["error"], "unknown_format")

    def test_deprecated_n8n_yaml_returns_410(self) -> None:
        resp = self.client.get(
            "/recipe/export",
            params={"goal_id": "g_known", "format": "n8n_yaml"},
        )
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["detail"]["replacement_format"], "n8n_json")

    def test_missing_goal_id_returns_404(self) -> None:
        resp = self.client.get(
            "/recipe/export",
            params={"goal_id": "g_missing", "format": "markdown"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"]["error"], "goal_not_found")

    def test_markdown_export_returns_markdown_body(self) -> None:
        resp = self.client.get(
            "/recipe/export",
            params={"goal_id": "g_known", "format": "markdown"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            resp.headers["content-type"].startswith("text/markdown"),
            resp.headers["content-type"],
        )
        body = resp.text
        self.assertIn("# Recipe for: Verify the email then enrich.", body)
        self.assertIn("Hunter MCP", body)
        self.assertIn(
            'attachment; filename="planmyagents-g_known.md"',
            resp.headers["content-disposition"],
        )
        self.assertEqual(resp.headers["x-planmyagents-recipe-status"], "partial")
        self.assertEqual(resp.headers["x-planmyagents-step-count"], "2")
        self.assertEqual(resp.headers["x-planmyagents-recommended-step-count"], "1")
        self.assertEqual(resp.headers["x-planmyagents-exportable-step-count"], "1")
        self.assertEqual(resp.headers["x-planmyagents-gap-count"], "1")

    def test_claude_desktop_json_export_is_valid_json(self) -> None:
        resp = self.client.get(
            "/recipe/export",
            params={"goal_id": "g_known", "format": "claude_desktop_json"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "application/json")
        payload = json.loads(resp.text)
        self.assertIn("mcpServers", payload)
        self.assertIn("hunter-mcp", payload["mcpServers"])

    def test_every_advertised_format_round_trips_via_http(self) -> None:
        for fmt in ["claude_desktop_json", "n8n_json", "cursor_prompt", "markdown", "cli"]:
            with self.subTest(format=fmt):
                resp = self.client.get(
                    "/recipe/export",
                    params={"goal_id": "g_known", "format": fmt},
                )
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertGreater(len(resp.content), 0)
                self.assertEqual(resp.headers["x-planmyagents-format"], fmt)
                self.assertEqual(resp.headers["x-planmyagents-goal-id"], "g_known")


if __name__ == "__main__":
    unittest.main()
