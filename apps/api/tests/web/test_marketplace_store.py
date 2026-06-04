"""Unit tests for the Pro-tier marketplace_store.

Covers the in-memory and SQLite backends. Postgres is exercised via the
`apply_migrations.py` check-only mode plus a smoke test that asserts the
postgres-schema DDL is syntactically what we expect (CHECK constraints
present, schema isolated, indexes named correctly) — the schema string
itself is the contract that ops/CI runs against a fresh Postgres.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.marketplace_store import (
    MARKETPLACE_STORE_POSTGRES_SCHEMA,
    InMemoryMarketplaceStore,
    SqliteMarketplaceStore,
    marketplace_store_from_env,
)
from planmyagents_api.marketplace_store.models import (
    ALLOWED_PLANS,
    PLAN_FREE,
    PLAN_PRO,
    SavedRecipe,
    User,
    Workspace,
    WorkspaceMember,
    _normalise_plan,
    _normalise_role,
)
from planmyagents_api.marketplace_store.store import RecipeNotFoundError


class ModelTests(unittest.TestCase):
    def test_user_new_normalises_email_and_clerk_id(self) -> None:
        user = User.new(clerk_user_id="  user_abc ", email=" Foo@Example.com ")
        self.assertEqual(user.clerk_user_id, "user_abc")
        self.assertEqual(user.email, "foo@example.com")
        self.assertEqual(user.plan, PLAN_FREE)

    def test_user_rejects_blank_clerk_id_and_email(self) -> None:
        with self.assertRaises(ValueError):
            User.new(clerk_user_id="", email="a@b.com")
        with self.assertRaises(ValueError):
            User.new(clerk_user_id="x", email="")

    def test_plan_normalisation_rejects_unknown_plan(self) -> None:
        with self.assertRaises(ValueError):
            _normalise_plan("god_tier")
        for ok in ALLOWED_PLANS:
            self.assertEqual(_normalise_plan(ok), ok)

    def test_role_normalisation(self) -> None:
        self.assertEqual(_normalise_role("OWNER"), "owner")
        with self.assertRaises(ValueError):
            _normalise_role("super_admin")

    def test_saved_recipe_new_rejects_non_dict_recipe_json(self) -> None:
        with self.assertRaises(ValueError):
            SavedRecipe.new(
                workspace_id="ws",
                user_id="u",
                goal="x",
                recipe_json="not-a-dict",  # type: ignore[arg-type]
                format="markdown",
            )

    def test_workspace_member_normalises_role(self) -> None:
        member = WorkspaceMember.new(workspace_id="ws", user_id="u", role="ADMIN")
        self.assertEqual(member.role, "admin")


# ---------------------------------------------------------------------------
# Backend-agnostic suite — runs against in-memory AND sqlite.
# ---------------------------------------------------------------------------


class _BackendSuite:
    """Mixin run by both InMemory and SQLite test classes."""

    def make_store(self):  # pragma: no cover - provided by subclass
        raise NotImplementedError

    # --- users -------------------------------------------------------------

    def test_upsert_user_is_idempotent_by_clerk_id(self) -> None:
        store = self.make_store()
        first = store.upsert_user(clerk_user_id="user_1", email="a@b.com")
        second = store.upsert_user(clerk_user_id="user_1", email="a@b.com")
        self.assertEqual(first.user_id, second.user_id)
        self.assertEqual(first.plan, PLAN_FREE)

    def test_upsert_user_updates_email_when_clerk_id_matches(self) -> None:
        store = self.make_store()
        first = store.upsert_user(clerk_user_id="user_1", email="old@b.com")
        second = store.upsert_user(clerk_user_id="user_1", email="new@b.com")
        self.assertEqual(first.user_id, second.user_id)
        self.assertEqual(second.email, "new@b.com")
        fresh = store.user_for_clerk_id("user_1")
        assert fresh is not None
        self.assertEqual(fresh.email, "new@b.com")

    def test_user_for_clerk_id_returns_none_when_missing(self) -> None:
        store = self.make_store()
        self.assertIsNone(store.user_for_clerk_id("ghost"))
        self.assertIsNone(store.user_for_clerk_id("   "))

    def test_set_plan_promotes_to_pro(self) -> None:
        store = self.make_store()
        user = store.upsert_user(clerk_user_id="user_1", email="a@b.com")
        upgraded = store.set_plan(user_id=user.user_id, plan="pro")
        self.assertEqual(upgraded.plan, PLAN_PRO)
        again = store.user_for_clerk_id("user_1")
        assert again is not None
        self.assertEqual(again.plan, PLAN_PRO)

    def test_set_plan_unknown_user_raises(self) -> None:
        store = self.make_store()
        with self.assertRaises(LookupError):
            store.set_plan(user_id="00000000-0000-0000-0000-000000000000", plan="pro")

    # --- workspaces --------------------------------------------------------

    def test_default_workspace_is_created_lazily_and_reused(self) -> None:
        store = self.make_store()
        user = store.upsert_user(clerk_user_id="user_1", email="a@b.com")
        ws1 = store.default_workspace_for_user(user_id=user.user_id)
        ws2 = store.default_workspace_for_user(user_id=user.user_id)
        self.assertEqual(ws1.workspace_id, ws2.workspace_id)
        self.assertIn(user.email, ws1.name)

    def test_default_workspace_for_unknown_user_raises(self) -> None:
        store = self.make_store()
        with self.assertRaises(LookupError):
            store.default_workspace_for_user(
                user_id="00000000-0000-0000-0000-000000000000"
            )

    # --- saved recipes -----------------------------------------------------

    def _make_user_with_workspace(self, store) -> tuple[User, Workspace]:
        user = store.upsert_user(clerk_user_id="user_1", email="a@b.com")
        ws = store.default_workspace_for_user(user_id=user.user_id)
        return user, ws

    def test_save_and_list_recipes_round_trip(self) -> None:
        store = self.make_store()
        user, ws = self._make_user_with_workspace(store)
        first = store.save_recipe(
            user_id=user.user_id,
            workspace_id=ws.workspace_id,
            goal="Verify emails for my CSV",
            recipe_json={"status": "ok", "sub_tasks": [{"capability": "email_verification"}]},
            format="markdown",
            notes="From sales-csv import",
        )
        second = store.save_recipe(
            user_id=user.user_id,
            workspace_id=ws.workspace_id,
            goal="Find shipping rates",
            recipe_json={"status": "ok", "sub_tasks": []},
            format="cli",
        )
        recipes = store.list_recipes(user_id=user.user_id)
        self.assertEqual(len(recipes), 2)
        recipe_ids = {r.recipe_id for r in recipes}
        self.assertIn(first.recipe_id, recipe_ids)
        self.assertIn(second.recipe_id, recipe_ids)
        # recipe_json is round-tripped intact (dict equality, not string).
        retrieved_first = next(r for r in recipes if r.recipe_id == first.recipe_id)
        self.assertEqual(retrieved_first.recipe_json["status"], "ok")
        self.assertEqual(
            retrieved_first.recipe_json["sub_tasks"][0]["capability"],
            "email_verification",
        )
        # Most recent first.
        self.assertGreaterEqual(recipes[0].created_at, recipes[1].created_at)

    def test_save_recipe_refuses_non_member_user(self) -> None:
        store = self.make_store()
        user_a = store.upsert_user(clerk_user_id="a", email="a@b.com")
        user_b = store.upsert_user(clerk_user_id="b", email="b@c.com")
        ws_a = store.default_workspace_for_user(user_id=user_a.user_id)
        with self.assertRaises(PermissionError):
            store.save_recipe(
                user_id=user_b.user_id,
                workspace_id=ws_a.workspace_id,
                goal="evil",
                recipe_json={"status": "ok"},
                format="markdown",
            )

    def test_list_recipes_isolates_users(self) -> None:
        store = self.make_store()
        user_a = store.upsert_user(clerk_user_id="a", email="a@b.com")
        user_b = store.upsert_user(clerk_user_id="b", email="b@c.com")
        ws_a = store.default_workspace_for_user(user_id=user_a.user_id)
        ws_b = store.default_workspace_for_user(user_id=user_b.user_id)
        store.save_recipe(
            user_id=user_a.user_id,
            workspace_id=ws_a.workspace_id,
            goal="a-only",
            recipe_json={"x": 1},
            format="cli",
        )
        store.save_recipe(
            user_id=user_b.user_id,
            workspace_id=ws_b.workspace_id,
            goal="b-only",
            recipe_json={"y": 2},
            format="cli",
        )
        a_recipes = store.list_recipes(user_id=user_a.user_id)
        b_recipes = store.list_recipes(user_id=user_b.user_id)
        self.assertEqual({r.goal for r in a_recipes}, {"a-only"})
        self.assertEqual({r.goal for r in b_recipes}, {"b-only"})

    def test_delete_recipe_owner_only(self) -> None:
        store = self.make_store()
        user_a = store.upsert_user(clerk_user_id="a", email="a@b.com")
        user_b = store.upsert_user(clerk_user_id="b", email="b@c.com")
        ws_a = store.default_workspace_for_user(user_id=user_a.user_id)
        store.default_workspace_for_user(user_id=user_b.user_id)
        recipe = store.save_recipe(
            user_id=user_a.user_id,
            workspace_id=ws_a.workspace_id,
            goal="a-only",
            recipe_json={"x": 1},
            format="cli",
        )
        # Other user cannot delete A's recipe — surfaces as missing, not as
        # PermissionError (do not reveal existence to non-owners).
        with self.assertRaises(RecipeNotFoundError):
            store.delete_recipe(user_id=user_b.user_id, recipe_id=recipe.recipe_id)
        # Owner deletion works.
        store.delete_recipe(user_id=user_a.user_id, recipe_id=recipe.recipe_id)
        self.assertEqual(store.list_recipes(user_id=user_a.user_id), [])
        # Idempotency: a second delete also raises (the row is gone).
        with self.assertRaises(RecipeNotFoundError):
            store.delete_recipe(user_id=user_a.user_id, recipe_id=recipe.recipe_id)


class InMemoryMarketplaceStoreTests(_BackendSuite, unittest.TestCase):
    def make_store(self) -> InMemoryMarketplaceStore:
        return InMemoryMarketplaceStore()


class SqliteMarketplaceStoreTests(_BackendSuite, unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)

    def make_store(self) -> SqliteMarketplaceStore:
        return SqliteMarketplaceStore(Path(self._tempdir.name) / "marketplace.sqlite")

    def test_sqlite_persists_across_store_instances(self) -> None:
        path = Path(self._tempdir.name) / "persistence.sqlite"
        first = SqliteMarketplaceStore(path)
        user = first.upsert_user(clerk_user_id="u", email="a@b.com")
        ws = first.default_workspace_for_user(user_id=user.user_id)
        first.save_recipe(
            user_id=user.user_id,
            workspace_id=ws.workspace_id,
            goal="across-instances",
            recipe_json={"k": "v"},
            format="markdown",
        )
        second = SqliteMarketplaceStore(path)
        recipes = second.list_recipes(user_id=user.user_id)
        self.assertEqual(len(recipes), 1)
        self.assertEqual(recipes[0].goal, "across-instances")


# ---------------------------------------------------------------------------
# Postgres DDL invariants — string-level checks, no live DB required.
# ---------------------------------------------------------------------------


class PostgresSchemaInvariantsTests(unittest.TestCase):
    def test_schema_is_namespaced(self) -> None:
        self.assertIn(
            "CREATE SCHEMA IF NOT EXISTS marketplace_store",
            MARKETPLACE_STORE_POSTGRES_SCHEMA,
        )
        for table in ("users", "workspaces", "workspace_members", "saved_recipes"):
            self.assertIn(f"marketplace_store.{table}", MARKETPLACE_STORE_POSTGRES_SCHEMA)

    def test_schema_enforces_plan_check_constraint(self) -> None:
        self.assertIn(
            "CHECK (plan IN ('free', 'pro', 'enterprise'))",
            MARKETPLACE_STORE_POSTGRES_SCHEMA,
        )

    def test_schema_enforces_role_check_constraint(self) -> None:
        self.assertIn(
            "CHECK (role IN ('owner', 'admin', 'member'))",
            MARKETPLACE_STORE_POSTGRES_SCHEMA,
        )

    def test_schema_cascades_recipe_deletion_on_user_delete(self) -> None:
        # Recipes follow user lifecycle — GDPR-style account deletion must
        # leave no orphaned recipes behind.
        self.assertIn(
            "REFERENCES marketplace_store.users(user_id) ON DELETE CASCADE",
            MARKETPLACE_STORE_POSTGRES_SCHEMA,
        )

    def test_schema_indexes_recipes_by_user(self) -> None:
        # /recipes list endpoint queries WHERE user_id=? ORDER BY created_at —
        # the composite index is required to keep that fast at scale.
        self.assertIn("idx_marketplace_saved_recipes_user", MARKETPLACE_STORE_POSTGRES_SCHEMA)
        self.assertIn(
            "ON marketplace_store.saved_recipes(user_id, created_at DESC)",
            MARKETPLACE_STORE_POSTGRES_SCHEMA,
        )


# ---------------------------------------------------------------------------
# Factory wiring
# ---------------------------------------------------------------------------


class MarketplaceStoreFactoryTests(unittest.TestCase):
    def test_in_memory_by_default(self) -> None:
        store = marketplace_store_from_env()
        self.assertIsInstance(store, InMemoryMarketplaceStore)

    def test_sqlite_path_picked_up_from_env(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as tempdir:
            os.environ["PLANMYAGENTS_MARKETPLACE_STORE_PATH"] = str(
                Path(tempdir) / "marketplace.sqlite"
            )
            try:
                store = marketplace_store_from_env()
                self.assertIsInstance(store, SqliteMarketplaceStore)
            finally:
                del os.environ["PLANMYAGENTS_MARKETPLACE_STORE_PATH"]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
