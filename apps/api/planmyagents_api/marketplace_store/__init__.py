"""Marketplace store package — Pro-tier + vendor identity tables.

Sprint 4 T1-B-1 scope (this commit)
-----------------------------------
Only the *Pro-tier subset* lands here:

* ``users``                — Clerk-authenticated end-users + plan flag.
* ``workspaces``           — Lightweight tenancy boundary; every user gets a
  default solo workspace at first sign-in.
* ``workspace_members``    — Many-to-many link with role.
* ``saved_recipes``        — A serialised plan payload + format the user
  asked to download. The plan_payload column intentionally mirrors the
  shape produced by :mod:`planmyagents_api.planner.recipe_export.goal_cache`
  so we can hand it straight back to the renderers without translation.

The remaining T3-A tables (``vendors``, ``listings``, ``claims``,
``vendor_audit_events``, ``execution_fee_subscriptions``,
``execution_fee_charges``, …) are intentionally **NOT** created here —
that schema lands in Sprint 5 / T3-A so the Pro tier (T1-B) can ship
without coupling release cadence to the marketplace product.

Why a separate package (not inside ``discovery/``)
--------------------------------------------------
Per HLD §10.2 and LLD §3.11 the marketplace lives in a *physically
separate Postgres schema* (``marketplace_store``) so that:

1. A marketplace bug can never corrupt discovery / benchmark data.
2. RBAC for vendor users (Sprint 5) is isolated to one schema.
3. The vendor-neutrality firewall (P11, T5) can lint that no module in
   ``planmyagents_api.ranking`` / ``planmyagents_api.benchmark`` imports
   from this package.

This file ONLY re-exports the public surface; the actual schema strings
and store classes live in :mod:`planmyagents_api.marketplace_store.store`.
"""

from __future__ import annotations

from planmyagents_api.marketplace_store.models import (
    SavedRecipe,
    User,
    Workspace,
    WorkspaceMember,
)
from planmyagents_api.marketplace_store.store import (
    MARKETPLACE_STORE_POSTGRES_SCHEMA,
    MARKETPLACE_STORE_SQLITE_SCHEMA,
    InMemoryMarketplaceStore,
    MarketplaceStore,
    PostgresMarketplaceStore,
    SqliteMarketplaceStore,
    marketplace_store_from_env,
)

__all__ = [
    "MARKETPLACE_STORE_POSTGRES_SCHEMA",
    "MARKETPLACE_STORE_SQLITE_SCHEMA",
    "InMemoryMarketplaceStore",
    "MarketplaceStore",
    "PostgresMarketplaceStore",
    "SavedRecipe",
    "SqliteMarketplaceStore",
    "User",
    "Workspace",
    "WorkspaceMember",
    "marketplace_store_from_env",
]
