"""Dataclasses for the Pro-tier marketplace_store tables.

Mirrors LLD §3.10 1:1 — keep field names identical so the schema can be
mechanically derived from the dataclasses if we ever need to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

PLAN_FREE = "free"
PLAN_PRO = "pro"
PLAN_ENTERPRISE = "enterprise"
ALLOWED_PLANS = frozenset({PLAN_FREE, PLAN_PRO, PLAN_ENTERPRISE})

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ALLOWED_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER})


def _now_iso() -> str:
    # Microsecond precision matters: /recipes list ordering uses created_at
    # as the primary sort key, and two saves in the same second (common in
    # tests and `Save All` flows) would otherwise tie. Postgres TIMESTAMPTZ
    # and SQLite TEXT both round-trip microseconds cleanly.
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _new_uuid() -> str:
    return str(uuid4())


def _normalise_plan(value: str | None) -> str:
    plan = (value or PLAN_FREE).strip().lower()
    if plan not in ALLOWED_PLANS:
        raise ValueError(f"Unknown plan {value!r}; allowed: {sorted(ALLOWED_PLANS)}")
    return plan


def _normalise_role(value: str | None) -> str:
    role = (value or ROLE_MEMBER).strip().lower()
    if role not in ALLOWED_ROLES:
        raise ValueError(f"Unknown role {value!r}; allowed: {sorted(ALLOWED_ROLES)}")
    return role


@dataclass(frozen=True)
class User:
    user_id: str
    clerk_user_id: str
    email: str
    plan: str = PLAN_FREE
    created_at: str = field(default_factory=_now_iso)

    def to_json(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "clerk_user_id": self.clerk_user_id,
            "email": self.email,
            "plan": self.plan,
            "created_at": self.created_at,
        }

    @classmethod
    def new(cls, *, clerk_user_id: str, email: str, plan: str = PLAN_FREE) -> User:
        if not clerk_user_id:
            raise ValueError("clerk_user_id is required")
        if not email:
            raise ValueError("email is required")
        return cls(
            user_id=_new_uuid(),
            clerk_user_id=clerk_user_id.strip(),
            email=email.strip().lower(),
            plan=_normalise_plan(plan),
        )


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    name: str
    plan: str = PLAN_FREE
    created_at: str = field(default_factory=_now_iso)

    def to_json(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "plan": self.plan,
            "created_at": self.created_at,
        }

    @classmethod
    def new(cls, *, name: str, plan: str = PLAN_FREE) -> Workspace:
        if not name:
            raise ValueError("name is required")
        return cls(
            workspace_id=_new_uuid(),
            name=name.strip(),
            plan=_normalise_plan(plan),
        )


@dataclass(frozen=True)
class WorkspaceMember:
    workspace_id: str
    user_id: str
    role: str = ROLE_MEMBER

    def to_json(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "role": self.role,
        }

    @classmethod
    def new(cls, *, workspace_id: str, user_id: str, role: str = ROLE_MEMBER) -> WorkspaceMember:
        return cls(
            workspace_id=workspace_id,
            user_id=user_id,
            role=_normalise_role(role),
        )


@dataclass(frozen=True)
class SavedRecipe:
    """A snapshot of a /recipe/export result the user chose to keep.

    ``recipe_json`` is the full ``plan_payload`` dict, not the rendered
    output: we re-render at download time so the user always gets the
    latest renderer fixes for a recipe they saved months ago.
    """

    recipe_id: str
    workspace_id: str
    user_id: str
    goal: str
    recipe_json: dict[str, Any]
    format: str
    notes: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_json(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "goal": self.goal,
            "recipe_json": self.recipe_json,
            "format": self.format,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def new(
        cls,
        *,
        workspace_id: str,
        user_id: str,
        goal: str,
        recipe_json: dict[str, Any],
        format: str,
        notes: str | None = None,
    ) -> SavedRecipe:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        if not user_id:
            raise ValueError("user_id is required")
        if not goal:
            raise ValueError("goal is required")
        if not isinstance(recipe_json, dict):
            raise ValueError("recipe_json must be a dict")
        if not format:
            raise ValueError("format is required")
        now = _now_iso()
        return cls(
            recipe_id=_new_uuid(),
            workspace_id=workspace_id,
            user_id=user_id,
            goal=goal.strip(),
            recipe_json=dict(recipe_json),
            format=format.strip().lower(),
            notes=notes,
            created_at=now,
            updated_at=now,
        )
