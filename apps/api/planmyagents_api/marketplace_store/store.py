"""Storage backends for the Pro-tier marketplace_store schema.

Three backends, following the discovery / demand / verification store
pattern:

* :class:`InMemoryMarketplaceStore`  — process-local; default in tests and
  the recipe-export endpoint when no DSN is set.
* :class:`SqliteMarketplaceStore`    — single-process local dev.
* :class:`PostgresMarketplaceStore`  — production. Lives in its own schema
  (``marketplace_store``) so it shares blast radius with no other module.

The interface (:class:`MarketplaceStore`) is intentionally narrow for
Sprint 4 — only the verbs T1-B-2..T1-B-7 will call:

* user_for_clerk_id / upsert_user / set_plan        (T1-B-2, T1-B-5)
* default_workspace_for_user                        (T1-B-2 first-login)
* save_recipe / list_recipes / delete_recipe        (T1-B-3)

CRUD for full-marketplace tables (vendors, claims, listings, …) lands in
T3-A and gets added to this same store, NOT a parallel one — keeping the
file growing in-place keeps the firewall-lint surface small (T5-1 lints
that ``planmyagents_api.ranking`` and ``planmyagents_api.benchmark.runner``
do not import ``planmyagents_api.marketplace_store`` at all).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from planmyagents_api.marketplace_store.models import (
    SavedRecipe,
    User,
    Workspace,
    WorkspaceMember,
    _normalise_plan,
)


def _now_iso() -> str:
    # Mirror models._now_iso — microsecond precision keeps list ordering
    # stable. Kept as a local helper so this module never needs a circular
    # import of `marketplace_store.models` for timestamping fallbacks
    # (Postgres rows where created_at came back as `None`, etc.).
    return datetime.now(UTC).isoformat(timespec="microseconds")


class RecipeNotFoundError(LookupError):
    """Raised when delete/get is called for a recipe_id the caller does not own."""


class MarketplaceStore(Protocol):
    """Narrow interface — extend as T3-A tables come online."""

    def upsert_user(self, *, clerk_user_id: str, email: str) -> User: ...
    def user_for_clerk_id(self, clerk_user_id: str) -> User | None: ...
    def set_plan(self, *, user_id: str, plan: str) -> User: ...

    def default_workspace_for_user(self, *, user_id: str) -> Workspace: ...

    def save_recipe(
        self,
        *,
        user_id: str,
        workspace_id: str,
        goal: str,
        recipe_json: dict[str, Any],
        format: str,
        notes: str | None = None,
    ) -> SavedRecipe: ...

    def list_recipes(self, *, user_id: str) -> list[SavedRecipe]: ...
    def delete_recipe(self, *, user_id: str, recipe_id: str) -> None: ...


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class InMemoryMarketplaceStore:
    """Thread-safe in-memory store. Default for tests and single-process dev."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._users_by_id: dict[str, User] = {}
        self._users_by_clerk: dict[str, str] = {}  # clerk_user_id -> user_id
        self._workspaces: dict[str, Workspace] = {}
        self._members: dict[tuple[str, str], WorkspaceMember] = {}
        self._default_workspace_by_user: dict[str, str] = {}
        self._recipes: dict[str, SavedRecipe] = {}

    def upsert_user(self, *, clerk_user_id: str, email: str) -> User:
        clerk_user_id = clerk_user_id.strip()
        email = email.strip().lower()
        if not clerk_user_id:
            raise ValueError("clerk_user_id is required")
        if not email:
            raise ValueError("email is required")
        with self._lock:
            existing_id = self._users_by_clerk.get(clerk_user_id)
            if existing_id is not None:
                current = self._users_by_id[existing_id]
                if current.email == email:
                    return current
                refreshed = User(
                    user_id=current.user_id,
                    clerk_user_id=current.clerk_user_id,
                    email=email,
                    plan=current.plan,
                    created_at=current.created_at,
                )
                self._users_by_id[existing_id] = refreshed
                return refreshed
            user = User.new(clerk_user_id=clerk_user_id, email=email)
            self._users_by_id[user.user_id] = user
            self._users_by_clerk[user.clerk_user_id] = user.user_id
            return user

    def user_for_clerk_id(self, clerk_user_id: str) -> User | None:
        clerk_user_id = clerk_user_id.strip()
        if not clerk_user_id:
            return None
        with self._lock:
            uid = self._users_by_clerk.get(clerk_user_id)
            if uid is None:
                return None
            return self._users_by_id.get(uid)

    def set_plan(self, *, user_id: str, plan: str) -> User:
        plan = _normalise_plan(plan)
        with self._lock:
            current = self._users_by_id.get(user_id)
            if current is None:
                raise LookupError(f"user {user_id} not found")
            refreshed = User(
                user_id=current.user_id,
                clerk_user_id=current.clerk_user_id,
                email=current.email,
                plan=plan,
                created_at=current.created_at,
            )
            self._users_by_id[user_id] = refreshed
            return refreshed

    def default_workspace_for_user(self, *, user_id: str) -> Workspace:
        with self._lock:
            if user_id not in self._users_by_id:
                raise LookupError(f"user {user_id} not found")
            existing_ws_id = self._default_workspace_by_user.get(user_id)
            if existing_ws_id is not None:
                ws = self._workspaces.get(existing_ws_id)
                if ws is not None:
                    return ws
            user = self._users_by_id[user_id]
            ws = Workspace.new(name=f"{user.email}'s workspace", plan=user.plan)
            self._workspaces[ws.workspace_id] = ws
            self._members[(ws.workspace_id, user_id)] = WorkspaceMember.new(
                workspace_id=ws.workspace_id, user_id=user_id, role="owner"
            )
            self._default_workspace_by_user[user_id] = ws.workspace_id
            return ws

    def save_recipe(
        self,
        *,
        user_id: str,
        workspace_id: str,
        goal: str,
        recipe_json: dict[str, Any],
        format: str,
        notes: str | None = None,
    ) -> SavedRecipe:
        with self._lock:
            if (workspace_id, user_id) not in self._members:
                raise PermissionError(
                    f"user {user_id} is not a member of workspace {workspace_id}"
                )
            recipe = SavedRecipe.new(
                workspace_id=workspace_id,
                user_id=user_id,
                goal=goal,
                recipe_json=recipe_json,
                format=format,
                notes=notes,
            )
            self._recipes[recipe.recipe_id] = recipe
            return recipe

    def list_recipes(self, *, user_id: str) -> list[SavedRecipe]:
        with self._lock:
            return sorted(
                (r for r in self._recipes.values() if r.user_id == user_id),
                key=lambda r: r.created_at,
                reverse=True,
            )

    def delete_recipe(self, *, user_id: str, recipe_id: str) -> None:
        with self._lock:
            recipe = self._recipes.get(recipe_id)
            if recipe is None or recipe.user_id != user_id:
                raise RecipeNotFoundError(recipe_id)
            del self._recipes[recipe_id]


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

MARKETPLACE_STORE_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketplace_users (
  user_id        TEXT PRIMARY KEY,
  clerk_user_id  TEXT UNIQUE NOT NULL,
  email          TEXT NOT NULL,
  plan           TEXT NOT NULL DEFAULT 'free',
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS marketplace_workspaces (
  workspace_id   TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  plan           TEXT NOT NULL DEFAULT 'free',
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS marketplace_workspace_members (
  workspace_id   TEXT NOT NULL,
  user_id        TEXT NOT NULL,
  role           TEXT NOT NULL,
  PRIMARY KEY (workspace_id, user_id),
  FOREIGN KEY (workspace_id) REFERENCES marketplace_workspaces(workspace_id),
  FOREIGN KEY (user_id) REFERENCES marketplace_users(user_id)
);

CREATE TABLE IF NOT EXISTS marketplace_default_workspaces (
  user_id        TEXT PRIMARY KEY,
  workspace_id   TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES marketplace_users(user_id),
  FOREIGN KEY (workspace_id) REFERENCES marketplace_workspaces(workspace_id)
);

CREATE TABLE IF NOT EXISTS marketplace_saved_recipes (
  recipe_id      TEXT PRIMARY KEY,
  workspace_id   TEXT NOT NULL,
  user_id        TEXT NOT NULL,
  goal           TEXT NOT NULL,
  recipe_json    TEXT NOT NULL,
  format         TEXT NOT NULL,
  notes          TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  FOREIGN KEY (workspace_id) REFERENCES marketplace_workspaces(workspace_id),
  FOREIGN KEY (user_id) REFERENCES marketplace_users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_marketplace_recipes_user
  ON marketplace_saved_recipes(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_recipes_workspace
  ON marketplace_saved_recipes(workspace_id, created_at DESC);
"""


class SqliteMarketplaceStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(MARKETPLACE_STORE_SQLITE_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def upsert_user(self, *, clerk_user_id: str, email: str) -> User:
        clerk_user_id = clerk_user_id.strip()
        email = email.strip().lower()
        if not clerk_user_id:
            raise ValueError("clerk_user_id is required")
        if not email:
            raise ValueError("email is required")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, clerk_user_id, email, plan, created_at "
                "FROM marketplace_users WHERE clerk_user_id = ?",
                (clerk_user_id,),
            ).fetchone()
            if row is not None:
                if row[2] != email:
                    conn.execute(
                        "UPDATE marketplace_users SET email = ? WHERE user_id = ?",
                        (email, row[0]),
                    )
                    return User(
                        user_id=row[0],
                        clerk_user_id=row[1],
                        email=email,
                        plan=row[3],
                        created_at=row[4],
                    )
                return User(
                    user_id=row[0],
                    clerk_user_id=row[1],
                    email=row[2],
                    plan=row[3],
                    created_at=row[4],
                )
            user = User.new(clerk_user_id=clerk_user_id, email=email)
            conn.execute(
                "INSERT INTO marketplace_users "
                "(user_id, clerk_user_id, email, plan, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user.user_id, user.clerk_user_id, user.email, user.plan, user.created_at),
            )
            return user

    def user_for_clerk_id(self, clerk_user_id: str) -> User | None:
        clerk_user_id = clerk_user_id.strip()
        if not clerk_user_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, clerk_user_id, email, plan, created_at "
                "FROM marketplace_users WHERE clerk_user_id = ?",
                (clerk_user_id,),
            ).fetchone()
            if row is None:
                return None
            return User(
                user_id=row[0],
                clerk_user_id=row[1],
                email=row[2],
                plan=row[3],
                created_at=row[4],
            )

    def set_plan(self, *, user_id: str, plan: str) -> User:
        plan = _normalise_plan(plan)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE marketplace_users SET plan = ? WHERE user_id = ?",
                (plan, user_id),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"user {user_id} not found")
            row = conn.execute(
                "SELECT user_id, clerk_user_id, email, plan, created_at "
                "FROM marketplace_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return User(
                user_id=row[0],
                clerk_user_id=row[1],
                email=row[2],
                plan=row[3],
                created_at=row[4],
            )

    def default_workspace_for_user(self, *, user_id: str) -> Workspace:
        with self._connect() as conn:
            user_row = conn.execute(
                "SELECT user_id, email, plan FROM marketplace_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if user_row is None:
                raise LookupError(f"user {user_id} not found")
            ws_row = conn.execute(
                "SELECT w.workspace_id, w.name, w.plan, w.created_at "
                "FROM marketplace_default_workspaces d "
                "JOIN marketplace_workspaces w ON w.workspace_id = d.workspace_id "
                "WHERE d.user_id = ?",
                (user_id,),
            ).fetchone()
            if ws_row is not None:
                return Workspace(
                    workspace_id=ws_row[0],
                    name=ws_row[1],
                    plan=ws_row[2],
                    created_at=ws_row[3],
                )
            ws = Workspace.new(name=f"{user_row[1]}'s workspace", plan=user_row[2])
            conn.execute(
                "INSERT INTO marketplace_workspaces "
                "(workspace_id, name, plan, created_at) VALUES (?, ?, ?, ?)",
                (ws.workspace_id, ws.name, ws.plan, ws.created_at),
            )
            conn.execute(
                "INSERT INTO marketplace_workspace_members "
                "(workspace_id, user_id, role) VALUES (?, ?, 'owner')",
                (ws.workspace_id, user_id),
            )
            conn.execute(
                "INSERT INTO marketplace_default_workspaces "
                "(user_id, workspace_id) VALUES (?, ?)",
                (user_id, ws.workspace_id),
            )
            return ws

    def save_recipe(
        self,
        *,
        user_id: str,
        workspace_id: str,
        goal: str,
        recipe_json: dict[str, Any],
        format: str,
        notes: str | None = None,
    ) -> SavedRecipe:
        with self._connect() as conn:
            member = conn.execute(
                "SELECT 1 FROM marketplace_workspace_members "
                "WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            ).fetchone()
            if member is None:
                raise PermissionError(
                    f"user {user_id} is not a member of workspace {workspace_id}"
                )
            recipe = SavedRecipe.new(
                workspace_id=workspace_id,
                user_id=user_id,
                goal=goal,
                recipe_json=recipe_json,
                format=format,
                notes=notes,
            )
            conn.execute(
                "INSERT INTO marketplace_saved_recipes "
                "(recipe_id, workspace_id, user_id, goal, recipe_json, "
                " format, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    recipe.recipe_id,
                    recipe.workspace_id,
                    recipe.user_id,
                    recipe.goal,
                    json.dumps(recipe.recipe_json, sort_keys=True),
                    recipe.format,
                    recipe.notes,
                    recipe.created_at,
                    recipe.updated_at,
                ),
            )
            return recipe

    def list_recipes(self, *, user_id: str) -> list[SavedRecipe]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT recipe_id, workspace_id, user_id, goal, recipe_json, "
                "format, notes, created_at, updated_at "
                "FROM marketplace_saved_recipes "
                "WHERE user_id = ? "
                "ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [
            SavedRecipe(
                recipe_id=row[0],
                workspace_id=row[1],
                user_id=row[2],
                goal=row[3],
                recipe_json=json.loads(row[4]) if row[4] else {},
                format=row[5],
                notes=row[6],
                created_at=row[7],
                updated_at=row[8],
            )
            for row in rows
        ]

    def delete_recipe(self, *, user_id: str, recipe_id: str) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM marketplace_saved_recipes "
                "WHERE recipe_id = ? AND user_id = ?",
                (recipe_id, user_id),
            )
            if cursor.rowcount == 0:
                raise RecipeNotFoundError(recipe_id)


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------

MARKETPLACE_STORE_POSTGRES_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS marketplace_store;

CREATE TABLE IF NOT EXISTS marketplace_store.users (
  user_id        UUID PRIMARY KEY,
  clerk_user_id  TEXT UNIQUE NOT NULL,
  email          TEXT NOT NULL,
  plan           TEXT NOT NULL DEFAULT 'free',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (plan IN ('free', 'pro', 'enterprise'))
);

CREATE TABLE IF NOT EXISTS marketplace_store.workspaces (
  workspace_id   UUID PRIMARY KEY,
  name           TEXT NOT NULL,
  plan           TEXT NOT NULL DEFAULT 'free',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (plan IN ('free', 'pro', 'enterprise'))
);

CREATE TABLE IF NOT EXISTS marketplace_store.workspace_members (
  workspace_id   UUID NOT NULL REFERENCES marketplace_store.workspaces(workspace_id) ON DELETE CASCADE,
  user_id        UUID NOT NULL REFERENCES marketplace_store.users(user_id) ON DELETE CASCADE,
  role           TEXT NOT NULL,
  PRIMARY KEY (workspace_id, user_id),
  CHECK (role IN ('owner', 'admin', 'member'))
);

CREATE TABLE IF NOT EXISTS marketplace_store.default_workspaces (
  user_id        UUID PRIMARY KEY REFERENCES marketplace_store.users(user_id) ON DELETE CASCADE,
  workspace_id   UUID NOT NULL REFERENCES marketplace_store.workspaces(workspace_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS marketplace_store.saved_recipes (
  recipe_id      UUID PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES marketplace_store.workspaces(workspace_id) ON DELETE CASCADE,
  user_id        UUID NOT NULL REFERENCES marketplace_store.users(user_id) ON DELETE CASCADE,
  goal           TEXT NOT NULL,
  recipe_json    JSONB NOT NULL,
  format         TEXT NOT NULL,
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_marketplace_saved_recipes_user
  ON marketplace_store.saved_recipes(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_saved_recipes_workspace
  ON marketplace_store.saved_recipes(workspace_id, created_at DESC);
"""


class PostgresMarketplaceStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.apply_schema()

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn)

    def apply_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(MARKETPLACE_STORE_POSTGRES_SCHEMA)
            conn.commit()

    def upsert_user(self, *, clerk_user_id: str, email: str) -> User:
        clerk_user_id = clerk_user_id.strip()
        email = email.strip().lower()
        if not clerk_user_id:
            raise ValueError("clerk_user_id is required")
        if not email:
            raise ValueError("email is required")
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT user_id, clerk_user_id, email, plan, created_at "
                "FROM marketplace_store.users WHERE clerk_user_id = %s",
                (clerk_user_id,),
            )
            row = cursor.fetchone()
            if row is not None:
                if row[2] != email:
                    cursor.execute(
                        "UPDATE marketplace_store.users SET email = %s WHERE user_id = %s",
                        (email, row[0]),
                    )
                    conn.commit()
                    return User(
                        user_id=str(row[0]),
                        clerk_user_id=row[1],
                        email=email,
                        plan=row[3],
                        created_at=row[4].isoformat() if row[4] else _now_iso(),
                    )
                return User(
                    user_id=str(row[0]),
                    clerk_user_id=row[1],
                    email=row[2],
                    plan=row[3],
                    created_at=row[4].isoformat() if row[4] else _now_iso(),
                )
            user = User.new(clerk_user_id=clerk_user_id, email=email)
            cursor.execute(
                "INSERT INTO marketplace_store.users "
                "(user_id, clerk_user_id, email, plan) "
                "VALUES (%s, %s, %s, %s)",
                (user.user_id, user.clerk_user_id, user.email, user.plan),
            )
            conn.commit()
            return user

    def user_for_clerk_id(self, clerk_user_id: str) -> User | None:
        clerk_user_id = clerk_user_id.strip()
        if not clerk_user_id:
            return None
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT user_id, clerk_user_id, email, plan, created_at "
                "FROM marketplace_store.users WHERE clerk_user_id = %s",
                (clerk_user_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return User(
                user_id=str(row[0]),
                clerk_user_id=row[1],
                email=row[2],
                plan=row[3],
                created_at=row[4].isoformat() if row[4] else _now_iso(),
            )

    def set_plan(self, *, user_id: str, plan: str) -> User:
        plan = _normalise_plan(plan)
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "UPDATE marketplace_store.users SET plan = %s WHERE user_id = %s "
                "RETURNING user_id, clerk_user_id, email, plan, created_at",
                (plan, user_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise LookupError(f"user {user_id} not found")
            conn.commit()
            return User(
                user_id=str(row[0]),
                clerk_user_id=row[1],
                email=row[2],
                plan=row[3],
                created_at=row[4].isoformat() if row[4] else _now_iso(),
            )

    def default_workspace_for_user(self, *, user_id: str) -> Workspace:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT u.user_id, u.email, u.plan "
                "FROM marketplace_store.users u WHERE u.user_id = %s",
                (user_id,),
            )
            user_row = cursor.fetchone()
            if user_row is None:
                raise LookupError(f"user {user_id} not found")
            cursor.execute(
                "SELECT w.workspace_id, w.name, w.plan, w.created_at "
                "FROM marketplace_store.default_workspaces d "
                "JOIN marketplace_store.workspaces w "
                "  ON w.workspace_id = d.workspace_id "
                "WHERE d.user_id = %s",
                (user_id,),
            )
            ws_row = cursor.fetchone()
            if ws_row is not None:
                return Workspace(
                    workspace_id=str(ws_row[0]),
                    name=ws_row[1],
                    plan=ws_row[2],
                    created_at=ws_row[3].isoformat() if ws_row[3] else _now_iso(),
                )
            ws = Workspace.new(name=f"{user_row[1]}'s workspace", plan=user_row[2])
            cursor.execute(
                "INSERT INTO marketplace_store.workspaces "
                "(workspace_id, name, plan) VALUES (%s, %s, %s)",
                (ws.workspace_id, ws.name, ws.plan),
            )
            cursor.execute(
                "INSERT INTO marketplace_store.workspace_members "
                "(workspace_id, user_id, role) VALUES (%s, %s, 'owner')",
                (ws.workspace_id, user_id),
            )
            cursor.execute(
                "INSERT INTO marketplace_store.default_workspaces "
                "(user_id, workspace_id) VALUES (%s, %s)",
                (user_id, ws.workspace_id),
            )
            conn.commit()
            return ws

    def save_recipe(
        self,
        *,
        user_id: str,
        workspace_id: str,
        goal: str,
        recipe_json: dict[str, Any],
        format: str,
        notes: str | None = None,
    ) -> SavedRecipe:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM marketplace_store.workspace_members "
                "WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, user_id),
            )
            if cursor.fetchone() is None:
                raise PermissionError(
                    f"user {user_id} is not a member of workspace {workspace_id}"
                )
            recipe = SavedRecipe.new(
                workspace_id=workspace_id,
                user_id=user_id,
                goal=goal,
                recipe_json=recipe_json,
                format=format,
                notes=notes,
            )
            cursor.execute(
                "INSERT INTO marketplace_store.saved_recipes "
                "(recipe_id, workspace_id, user_id, goal, recipe_json, "
                " format, notes) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)",
                (
                    recipe.recipe_id,
                    recipe.workspace_id,
                    recipe.user_id,
                    recipe.goal,
                    json.dumps(recipe.recipe_json, sort_keys=True),
                    recipe.format,
                    recipe.notes,
                ),
            )
            conn.commit()
            return recipe

    def list_recipes(self, *, user_id: str) -> list[SavedRecipe]:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT recipe_id, workspace_id, user_id, goal, recipe_json, "
                "format, notes, created_at, updated_at "
                "FROM marketplace_store.saved_recipes "
                "WHERE user_id = %s "
                "ORDER BY created_at DESC",
                (user_id,),
            )
            rows = cursor.fetchall()
        return [
            SavedRecipe(
                recipe_id=str(row[0]),
                workspace_id=str(row[1]),
                user_id=str(row[2]),
                goal=row[3],
                recipe_json=row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
                format=row[5],
                notes=row[6],
                created_at=row[7].isoformat() if row[7] else _now_iso(),
                updated_at=row[8].isoformat() if row[8] else _now_iso(),
            )
            for row in rows
        ]

    def delete_recipe(self, *, user_id: str, recipe_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM marketplace_store.saved_recipes "
                "WHERE recipe_id = %s AND user_id = %s",
                (recipe_id, user_id),
            )
            if cursor.rowcount == 0:
                raise RecipeNotFoundError(recipe_id)
            conn.commit()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

MARKETPLACE_DSN_ENV = "PLANMYAGENTS_MARKETPLACE_STORE_URL"
MARKETPLACE_SQLITE_ENV = "PLANMYAGENTS_MARKETPLACE_STORE_PATH"


def marketplace_store_from_env() -> MarketplaceStore:
    """Pick a backend from env vars; default to in-memory for tests + dev.

    Resolution order:
      1. ``PLANMYAGENTS_MARKETPLACE_STORE_URL`` (postgres://… → Postgres)
      2. ``PLANMYAGENTS_MARKETPLACE_STORE_PATH`` (\\*.sqlite | path → SQLite)
      3. In-memory.
    """
    dsn = os.getenv(MARKETPLACE_DSN_ENV, "").strip()
    if dsn.startswith(("postgresql://", "postgres://")):
        return PostgresMarketplaceStore(dsn)
    sqlite_path = os.getenv(MARKETPLACE_SQLITE_ENV, "").strip()
    if sqlite_path:
        return SqliteMarketplaceStore(sqlite_path)
    return InMemoryMarketplaceStore()


@dataclass(frozen=True)
class _Sentinel:
    """Used by tests as a stand-in user_id when no Clerk wiring yet."""

    value: str = "anon"

    @classmethod
    def fresh(cls) -> _Sentinel:
        return cls(value=str(uuid4()))
