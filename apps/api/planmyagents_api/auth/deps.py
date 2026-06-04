"""FastAPI dependencies for Clerk-backed authentication.

Three dependencies:

* :func:`current_user`           — hard-require a verified Clerk session.
                                    Resolves the request to a
                                    :class:`CurrentUser` (Clerk identity +
                                    upserted marketplace_store row).
                                    Raises 401 on failure.
* :func:`optional_current_user`  — returns ``None`` instead of raising
                                    when auth is missing or invalid. Use
                                    on endpoints that have a public
                                    read-only mode but enrich the
                                    response for signed-in users.
* :func:`require_pro_user`       — like :func:`current_user` but also
                                    asserts ``plan in {pro, enterprise}``,
                                    returning 402 (Payment Required) for
                                    free-tier users.

Why one dependency object instead of two
----------------------------------------
The clerk session and the persisted user row are conceptually distinct
(verifying a JWT does not require a DB write). We bundle them on
:class:`CurrentUser` because every authenticated endpoint we ship in
T1-B-3..T1-B-7 needs BOTH: the clerk_user_id to scope ownership checks
and the marketplace_store user_id (a UUID, not a Clerk id) to FK-link
to ``saved_recipes``. Combining them in a single dataclass keeps every
endpoint signature single-Depends.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from planmyagents_api.auth.clerk import (
    AuthDisabledError,
    AuthMode,
    ClerkConfig,
    ClerkSession,
    ClerkVerificationError,
    ExpiredTokenError,
    JwksCache,
    clerk_config_from_env,
    verify_clerk_token,
)
from planmyagents_api.marketplace_store import (
    MarketplaceStore,
    User,
    marketplace_store_from_env,
)


@dataclass(frozen=True)
class CurrentUser:
    """Resolved per-request identity.

    Holds BOTH the raw Clerk session (for downstream RBAC checks /
    auditing) and the persisted marketplace_store ``User`` row (whose
    ``user_id`` is the FK every saved_recipes row references).
    """

    session: ClerkSession
    user: User

    @property
    def user_id(self) -> str:
        return self.user.user_id

    @property
    def clerk_user_id(self) -> str:
        return self.user.clerk_user_id

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def plan(self) -> str:
        return self.user.plan


# ---------------------------------------------------------------------------
# Module-level singletons (overridable for tests)
# ---------------------------------------------------------------------------

_CLERK_CONFIG: ClerkConfig | None = None
_JWKS_CACHE: JwksCache | None = None
_MARKETPLACE_STORE: MarketplaceStore | None = None


def _get_clerk_config() -> ClerkConfig:
    global _CLERK_CONFIG
    if _CLERK_CONFIG is None:
        _CLERK_CONFIG = clerk_config_from_env()
    return _CLERK_CONFIG


def _get_jwks_cache(config: ClerkConfig) -> JwksCache | None:
    global _JWKS_CACHE
    if config.mode is not AuthMode.CLERK:
        return None
    if _JWKS_CACHE is None or _JWKS_CACHE.url != config.jwks_url:
        _JWKS_CACHE = JwksCache(config.jwks_url)
    return _JWKS_CACHE


def _get_marketplace_store() -> MarketplaceStore:
    global _MARKETPLACE_STORE
    if _MARKETPLACE_STORE is None:
        _MARKETPLACE_STORE = marketplace_store_from_env()
    return _MARKETPLACE_STORE


def configure_auth_dependencies(
    *,
    config: ClerkConfig | None = None,
    jwks_cache: JwksCache | None = None,
    store: MarketplaceStore | None = None,
) -> None:
    """Test-only hook: swap singletons before the first request.

    Production callers MUST NOT use this — the singletons are seeded
    lazily from env on first request, which is exactly what production
    wants. Tests use this to inject fixtures (stub JWKS, in-memory
    store, dev-mode config) without touching env vars.
    """
    global _CLERK_CONFIG, _JWKS_CACHE, _MARKETPLACE_STORE
    if config is not None:
        _CLERK_CONFIG = config
    if jwks_cache is not None:
        _JWKS_CACHE = jwks_cache
    if store is not None:
        _MARKETPLACE_STORE = store


def reset_auth_dependencies() -> None:
    """Test-only hook: clear cached singletons so the next call re-reads env."""
    global _CLERK_CONFIG, _JWKS_CACHE, _MARKETPLACE_STORE
    _CLERK_CONFIG = None
    _JWKS_CACHE = None
    _MARKETPLACE_STORE = None


# ---------------------------------------------------------------------------
# Bearer token extraction
# ---------------------------------------------------------------------------


def _bearer_token_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


# ---------------------------------------------------------------------------
# Dev-mode resolver
# ---------------------------------------------------------------------------


def _resolve_dev_mode(
    *,
    dev_user_id: str | None,
    dev_email: str | None,
) -> ClerkSession:
    clerk_user_id = (dev_user_id or "").strip()
    email = (dev_email or "").strip().lower()
    if not clerk_user_id:
        raise ClerkVerificationError(
            "PLANMYAGENTS_AUTH_MODE=dev requires X-Dev-Clerk-User-Id header"
        )
    if not email:
        raise ClerkVerificationError(
            "PLANMYAGENTS_AUTH_MODE=dev requires X-Dev-Clerk-User-Email header"
        )
    return ClerkSession(
        clerk_user_id=clerk_user_id,
        email=email,
        raw_claims={"sub": clerk_user_id, "email": email, "_dev_mode": True},
    )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_dev_clerk_user_id: Annotated[
        str | None, Header(alias="X-Dev-Clerk-User-Id")
    ] = None,
    x_dev_clerk_user_email: Annotated[
        str | None, Header(alias="X-Dev-Clerk-User-Email")
    ] = None,
) -> CurrentUser:
    """Hard-require an authenticated Clerk session.

    Returns the resolved :class:`CurrentUser` (clerk session + upserted
    marketplace_store user row). Raises HTTPException 401 on every
    failure path with a stable error code in the body.
    """
    config = _get_clerk_config()
    store = _get_marketplace_store()

    if config.mode is AuthMode.DISABLED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "auth_disabled",
                "message": "Authentication is not configured on this server.",
            },
        )

    try:
        if config.mode is AuthMode.DEV:
            session = _resolve_dev_mode(
                dev_user_id=x_dev_clerk_user_id,
                dev_email=x_dev_clerk_user_email,
            )
        else:
            token = _bearer_token_from_header(authorization)
            if not token:
                raise ClerkVerificationError(
                    "missing or malformed Authorization header"
                )
            session = verify_clerk_token(
                token,
                config=config,
                jwks_cache=_get_jwks_cache(config),
            )
    except AuthDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "auth_disabled", "message": str(exc)},
        ) from exc
    except ExpiredTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "token_expired", "message": str(exc)},
        ) from exc
    except ClerkVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": str(exc)},
        ) from exc

    user = store.upsert_user(
        clerk_user_id=session.clerk_user_id,
        email=session.email,
    )
    # Ensure the user has a default workspace so /recipes/save can run
    # without a separate provisioning step.
    store.default_workspace_for_user(user_id=user.user_id)
    return CurrentUser(session=session, user=user)


async def optional_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_dev_clerk_user_id: Annotated[
        str | None, Header(alias="X-Dev-Clerk-User-Id")
    ] = None,
    x_dev_clerk_user_email: Annotated[
        str | None, Header(alias="X-Dev-Clerk-User-Email")
    ] = None,
) -> CurrentUser | None:
    """Same as :func:`current_user` but returns ``None`` on every failure."""
    try:
        return await current_user(
            authorization=authorization,
            x_dev_clerk_user_id=x_dev_clerk_user_id,
            x_dev_clerk_user_email=x_dev_clerk_user_email,
        )
    except HTTPException:
        return None


async def require_pro_user(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    """Like :func:`current_user`, plus a 402 if the plan is `free`."""
    if user.plan not in {"pro", "enterprise"}:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "pro_required",
                "message": (
                    "This action requires a Pro plan. Visit /account to upgrade."
                ),
                "plan": user.plan,
            },
        )
    return user
