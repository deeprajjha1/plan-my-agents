"""HTTP tests for the saved-recipes CRUD endpoints (T1-B-3).

Strategy
--------
We bring up a real FastAPI app via :func:`planmyagents_api.web.app.create_app`
and use :class:`fastapi.testclient.TestClient` so the wiring (auth
dep, marketplace store singleton, goal-cache lookup) is exercised
end-to-end. The Clerk verifier is put into dev-mode so we can sign in
by passing X-Dev-Clerk-* headers instead of forging a JWT.

The goal cache is seeded directly so the tests don't depend on the
upstream /goal pipeline. The marketplace store is swapped to
in-memory.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.auth.clerk import AuthMode, ClerkConfig
from planmyagents_api.auth.deps import (
    configure_auth_dependencies,
    reset_auth_dependencies,
)
from planmyagents_api.marketplace_store import InMemoryMarketplaceStore
from planmyagents_api.planner.recipe_export.goal_cache import InMemoryGoalCache
from planmyagents_api.web import app as web_app


def _seed_goal(cache: InMemoryGoalCache, *, goal_id: str, goal_text: str) -> None:
    cache.save(
        goal_id=goal_id,
        goal_text=goal_text,
        plan_payload={
            "status": "ok",
            "sub_tasks": [
                {
                    "capability": "email_verification",
                    "description": "Verify the sales-csv emails.",
                }
            ],
            "workflow_options": [
                {
                    "steps": [
                        {
                            "capability": "email_verification",
                            "recommended_provider": {
                                "provider_id": "hunter",
                                "display_name": "Hunter",
                                "provider_type": "api_provider",
                            },
                        }
                    ]
                }
            ],
        },
    )


class _RecipesApiTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryMarketplaceStore()
        self.goal_cache = InMemoryGoalCache()
        web_app.set_marketplace_store_for_tests(self.store)
        web_app._GOAL_CACHE = self.goal_cache  # type: ignore[attr-defined]
        configure_auth_dependencies(
            config=ClerkConfig(mode=AuthMode.DEV),
            store=self.store,
        )
        self.app = web_app.create_app()
        self.client = TestClient(self.app)
        _seed_goal(
            self.goal_cache, goal_id="g_test_001", goal_text="Verify sales CSV"
        )

    def tearDown(self) -> None:
        web_app.set_marketplace_store_for_tests(None)
        web_app._GOAL_CACHE = None  # type: ignore[attr-defined]
        reset_auth_dependencies()

    def _dev_headers(self, *, sub: str, email: str) -> dict[str, str]:
        return {
            "X-Dev-Clerk-User-Id": sub,
            "X-Dev-Clerk-User-Email": email,
        }


class SaveRecipeEndpointTests(_RecipesApiTestBase):
    def test_authentication_required(self) -> None:
        resp = self.client.post(
            "/recipes",
            json={"goal_id": "g_test_001", "format": "markdown"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_happy_path_returns_201_with_download_url(self) -> None:
        resp = self.client.post(
            "/recipes",
            json={"goal_id": "g_test_001", "format": "markdown", "notes": "from-csv"},
            headers=self._dev_headers(sub="user_1", email="a@b.com"),
        )
        self.assertEqual(resp.status_code, 201, msg=resp.text)
        body = resp.json()
        self.assertEqual(body["goal"], "Verify sales CSV")
        self.assertEqual(body["format"], "markdown")
        self.assertEqual(body["notes"], "from-csv")
        self.assertIn("recipe_id", body)
        self.assertIn("user_id", body)
        self.assertEqual(
            body["download_url"], "/recipe/export?goal_id=g_test_001&format=markdown"
        )

    def test_unknown_format_returns_400(self) -> None:
        resp = self.client.post(
            "/recipes",
            json={"goal_id": "g_test_001", "format": "powerpoint"},
            headers=self._dev_headers(sub="user_1", email="a@b.com"),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["code"], "unknown_format")

    def test_expired_goal_returns_410(self) -> None:
        resp = self.client.post(
            "/recipes",
            json={"goal_id": "g_does_not_exist", "format": "markdown"},
            headers=self._dev_headers(sub="user_1", email="a@b.com"),
        )
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["detail"]["code"], "goal_expired")

    def test_save_again_creates_distinct_row(self) -> None:
        headers = self._dev_headers(sub="user_1", email="a@b.com")
        first = self.client.post(
            "/recipes",
            json={"goal_id": "g_test_001", "format": "markdown"},
            headers=headers,
        )
        second = self.client.post(
            "/recipes",
            json={"goal_id": "g_test_001", "format": "cli"},
            headers=headers,
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(
            first.json()["recipe_id"],
            second.json()["recipe_id"],
        )


class ListRecipesEndpointTests(_RecipesApiTestBase):
    def test_authentication_required(self) -> None:
        resp = self.client.get("/recipes")
        self.assertEqual(resp.status_code, 401)

    def test_returns_empty_when_no_recipes_saved(self) -> None:
        resp = self.client.get(
            "/recipes",
            headers=self._dev_headers(sub="user_1", email="a@b.com"),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"total": 0, "recipes": []})

    def test_returns_recipes_in_newest_first_order(self) -> None:
        headers = self._dev_headers(sub="user_1", email="a@b.com")
        self.client.post(
            "/recipes",
            json={"goal_id": "g_test_001", "format": "markdown"},
            headers=headers,
        )
        _seed_goal(self.goal_cache, goal_id="g_test_002", goal_text="second goal")
        self.client.post(
            "/recipes",
            json={"goal_id": "g_test_002", "format": "cli"},
            headers=headers,
        )
        resp = self.client.get("/recipes", headers=headers)
        body = resp.json()
        self.assertEqual(body["total"], 2)
        goals = [r["goal"] for r in body["recipes"]]
        # Newest first — second goal saved most recently.
        self.assertEqual(goals[0], "second goal")
        self.assertEqual(goals[1], "Verify sales CSV")

    def test_isolates_recipes_between_users(self) -> None:
        alice = self._dev_headers(sub="user_alice", email="alice@x.com")
        bob = self._dev_headers(sub="user_bob", email="bob@x.com")
        self.client.post(
            "/recipes",
            json={"goal_id": "g_test_001", "format": "markdown"},
            headers=alice,
        )
        bob_resp = self.client.get("/recipes", headers=bob)
        alice_resp = self.client.get("/recipes", headers=alice)
        self.assertEqual(bob_resp.json()["total"], 0)
        self.assertEqual(alice_resp.json()["total"], 1)


class DeleteRecipeEndpointTests(_RecipesApiTestBase):
    def test_authentication_required(self) -> None:
        resp = self.client.delete("/recipes/anything")
        self.assertEqual(resp.status_code, 401)

    def test_owner_can_delete_their_recipe(self) -> None:
        headers = self._dev_headers(sub="user_1", email="a@b.com")
        created = self.client.post(
            "/recipes",
            json={"goal_id": "g_test_001", "format": "markdown"},
            headers=headers,
        ).json()
        recipe_id = created["recipe_id"]
        del_resp = self.client.delete(f"/recipes/{recipe_id}", headers=headers)
        self.assertEqual(del_resp.status_code, 204)
        list_resp = self.client.get("/recipes", headers=headers)
        self.assertEqual(list_resp.json()["total"], 0)

    def test_non_owner_gets_404_not_403(self) -> None:
        # Privacy invariant: we never reveal a recipe id to a non-owner,
        # even via a 403 vs 404 oracle.
        alice = self._dev_headers(sub="user_alice", email="alice@x.com")
        bob = self._dev_headers(sub="user_bob", email="bob@x.com")
        created = self.client.post(
            "/recipes",
            json={"goal_id": "g_test_001", "format": "markdown"},
            headers=alice,
        ).json()
        recipe_id = created["recipe_id"]
        del_resp = self.client.delete(f"/recipes/{recipe_id}", headers=bob)
        self.assertEqual(del_resp.status_code, 404)
        # Alice's recipe is still there.
        list_resp = self.client.get("/recipes", headers=alice)
        self.assertEqual(list_resp.json()["total"], 1)

    def test_unknown_recipe_id_returns_404(self) -> None:
        headers = self._dev_headers(sub="user_1", email="a@b.com")
        resp = self.client.delete(
            "/recipes/00000000-0000-0000-0000-000000000000", headers=headers
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"]["code"], "recipe_not_found")


class AccountMeEndpointTests(_RecipesApiTestBase):
    def test_anonymous_caller_sees_authenticated_false(self) -> None:
        resp = self.client.get("/account/me")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"authenticated": False})

    def test_signed_in_caller_sees_plan_and_clerk_id(self) -> None:
        resp = self.client.get(
            "/account/me",
            headers=self._dev_headers(sub="user_1", email="a@b.com"),
        )
        body: dict[str, Any] = resp.json()
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["clerk_user_id"], "user_1")
        self.assertEqual(body["email"], "a@b.com")
        self.assertEqual(body["plan"], "free")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
