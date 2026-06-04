"""Authentication package — Clerk-backed identity for the Pro tier.

T1-B-2 scope: just the verifier + the FastAPI dependency. T1-B-3 (saved
recipes) and T1-B-5 (Stripe webhook → plan upgrade) call into this
package; nothing in `discovery/`, `benchmark/`, or `ranking/` is allowed
to import from here (firewall lint will assert that in T5).

Design constraints
------------------

1. **Placeholder-safe** — when Clerk env vars are absent, the verifier
   refuses every request with a clear 401 *but does not crash the
   process*. This lets local dev `make api` boot before Clerk is wired,
   and lets the public read-only endpoints (`/leaderboards`, `/goal`)
   keep serving unauthenticated callers.

2. **Dev-mode bypass** — when ``PLANMYAGENTS_AUTH_MODE=dev`` the verifier
   trusts the ``X-Dev-Clerk-User-Id`` and ``X-Dev-Clerk-User-Email``
   headers. This is **never** active in production (refused by a runtime
   check against ``PLANMYAGENTS_ENVIRONMENT=production``).

3. **No PII in logs** — clerk_user_id and email are only ever logged in
   structured form via the request-id scoped logger; never via print().

4. **Per-request user upsert** — every authenticated request lazily
   upserts the Clerk identity into ``marketplace_store.users`` so the
   first /recipes/save call doesn't have to special-case "no user row
   yet". The upsert is keyed by ``clerk_user_id`` (idempotent).
"""

from __future__ import annotations

from planmyagents_api.auth.clerk import (
    AuthDisabledError,
    AuthMode,
    ClerkConfig,
    ClerkSession,
    ClerkVerificationError,
    ExpiredTokenError,
    InvalidTokenError,
    JwksCache,
    clerk_config_from_env,
    verify_clerk_token,
)
from planmyagents_api.auth.deps import (
    CurrentUser,
    current_user,
    optional_current_user,
    require_pro_user,
)

__all__ = [
    "AuthDisabledError",
    "AuthMode",
    "ClerkConfig",
    "ClerkSession",
    "ClerkVerificationError",
    "CurrentUser",
    "ExpiredTokenError",
    "InvalidTokenError",
    "JwksCache",
    "clerk_config_from_env",
    "current_user",
    "optional_current_user",
    "require_pro_user",
    "verify_clerk_token",
]
