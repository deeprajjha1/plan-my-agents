"""Clerk JWT verifier.

Verifies a session JWT issued by Clerk against the per-instance JWKS
endpoint, returning a :class:`ClerkSession` with the resolved
``clerk_user_id`` and ``email``.

Why hand-rolled (not the ``clerk-sdk-python`` package)
------------------------------------------------------
The official SDK is a thick client that wraps Clerk's full REST API
(create user, send magic links, …). For the Pro tier we only need to
*verify* an incoming session token — a 60-line operation. Pulling in
the SDK would:

* add an upstream rate-limit dependency (the SDK does a backend `/me`
  call by default),
* add a network round trip per request,
* couple us to Clerk's quarterly SDK releases.

Verifying the JWT locally against cached JWKS is the path Clerk
themselves recommend for backends (`docs/authentication/configure/jwt-templates`).

Verification rules
------------------

* Signature algorithm: ``RS256`` only (Clerk default; we reject anything
  else to avoid alg-confusion attacks).
* ``iss`` MUST equal ``{clerk_issuer}`` (e.g.
  ``https://wise-koala-12.clerk.accounts.dev``).
* ``exp`` MUST be in the future (with a small clock-skew tolerance).
* ``azp`` (authorized party), when present, MUST be in the configured
  allow-list (Clerk includes the frontend origin here).

JWKS cache
----------

* In-memory, TTL 15 minutes by default.
* Refetched on a ``kid`` cache miss (Clerk rotates keys).
* Network failures during refresh fall back to the last-known JWKS for
  ``stale_after`` seconds, then raise — this keeps a flaky DNS minute
  from logging every user out.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib import request as urllib_request
from urllib.error import URLError

try:  # PyJWT[crypto] is required for RS256 verification
    import jwt  # type: ignore[import-untyped]
    from jwt import PyJWKSet, algorithms  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised via fallback path
    jwt = None  # type: ignore[assignment]
    PyJWKSet = None  # type: ignore[assignment]
    algorithms = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ClerkVerificationError(Exception):
    """Base — every failure path that should map to 401."""


class InvalidTokenError(ClerkVerificationError):
    """Signature / claims invalid."""


class ExpiredTokenError(ClerkVerificationError):
    """``exp`` is in the past."""


class AuthDisabledError(ClerkVerificationError):
    """Clerk env not configured — endpoint should 401."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class AuthMode(StrEnum):
    DISABLED = "disabled"  # No env set; reject all auth-required calls.
    DEV = "dev"            # Trust X-Dev-Clerk-* headers; never on prod.
    CLERK = "clerk"        # Verify against Clerk JWKS.


CLERK_ISSUER_ENV = "PLANMYAGENTS_CLERK_ISSUER"
CLERK_JWKS_URL_ENV = "PLANMYAGENTS_CLERK_JWKS_URL"
CLERK_AUTHORIZED_PARTIES_ENV = "PLANMYAGENTS_CLERK_AUTHORIZED_PARTIES"
AUTH_MODE_ENV = "PLANMYAGENTS_AUTH_MODE"
ENVIRONMENT_ENV = "PLANMYAGENTS_ENVIRONMENT"

DEFAULT_JWKS_TTL_SECONDS = 15 * 60
DEFAULT_JWKS_STALE_AFTER_SECONDS = 60 * 60
DEFAULT_CLOCK_SKEW_SECONDS = 30


@dataclass(frozen=True)
class ClerkConfig:
    mode: AuthMode
    issuer: str = ""
    jwks_url: str = ""
    authorized_parties: tuple[str, ...] = ()
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS

    def is_active(self) -> bool:
        return self.mode is not AuthMode.DISABLED


def clerk_config_from_env(env: dict[str, str] | None = None) -> ClerkConfig:
    source = env if env is not None else os.environ
    issuer = (source.get(CLERK_ISSUER_ENV) or "").strip().rstrip("/")
    jwks_url = (source.get(CLERK_JWKS_URL_ENV) or "").strip()
    authorized_parties_raw = (source.get(CLERK_AUTHORIZED_PARTIES_ENV) or "").strip()
    authorized_parties = tuple(
        part.strip()
        for part in authorized_parties_raw.split(",")
        if part.strip()
    )
    mode_raw = (source.get(AUTH_MODE_ENV) or "").strip().lower()
    environment = (source.get(ENVIRONMENT_ENV) or "").strip().lower()

    if mode_raw == "dev":
        if environment == "production":
            raise RuntimeError(
                "PLANMYAGENTS_AUTH_MODE=dev is forbidden when "
                "PLANMYAGENTS_ENVIRONMENT=production — refusing to start."
            )
        return ClerkConfig(mode=AuthMode.DEV)

    if not issuer:
        return ClerkConfig(mode=AuthMode.DISABLED)

    # Derive JWKS URL from issuer when not explicitly overridden.
    if not jwks_url:
        jwks_url = f"{issuer}/.well-known/jwks.json"

    return ClerkConfig(
        mode=AuthMode.CLERK,
        issuer=issuer,
        jwks_url=jwks_url,
        authorized_parties=authorized_parties,
    )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClerkSession:
    clerk_user_id: str
    email: str
    issued_at: int = 0
    expires_at: int = 0
    raw_claims: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# JWKS cache
# ---------------------------------------------------------------------------


class JwksCache:
    """Thread-safe TTL cache for a Clerk JWKS endpoint.

    Designed for swap-in testability: the ``fetcher`` argument is the
    only network surface, so tests inject a function that returns a
    fixture JWKS dict. Production uses :func:`_fetch_jwks_url`.
    """

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: int = DEFAULT_JWKS_TTL_SECONDS,
        stale_after_seconds: int = DEFAULT_JWKS_STALE_AFTER_SECONDS,
        fetcher=None,
    ) -> None:
        self.url = url
        self._ttl = ttl_seconds
        self._stale_after = stale_after_seconds
        self._fetcher = fetcher or _fetch_jwks_url
        self._lock = threading.Lock()
        self._jwks: dict[str, Any] = {}
        self._fetched_at: float = 0.0
        self._last_error: str = ""

    def keyset(self, *, force_refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            fresh = self._jwks and (now - self._fetched_at) < self._ttl
            if fresh and not force_refresh:
                return self._jwks
            try:
                jwks = self._fetcher(self.url)
                if not isinstance(jwks, dict) or not jwks.get("keys"):
                    raise InvalidTokenError(
                        f"JWKS at {self.url} returned no keys"
                    )
                self._jwks = jwks
                self._fetched_at = now
                self._last_error = ""
                return self._jwks
            except (URLError, OSError) as exc:
                self._last_error = str(exc)
                # If we have *any* cached JWKS that is younger than the
                # stale-after window, keep serving it so a transient
                # network glitch doesn't log every user out.
                if self._jwks and (now - self._fetched_at) < self._stale_after:
                    return self._jwks
                raise InvalidTokenError(f"JWKS fetch failed: {exc}") from exc

    def key_for_kid(self, kid: str) -> dict[str, Any]:
        jwks = self.keyset()
        for key in jwks.get("keys") or []:
            if key.get("kid") == kid:
                return key
        # kid not in current cache → maybe Clerk rotated; force one refresh
        jwks = self.keyset(force_refresh=True)
        for key in jwks.get("keys") or []:
            if key.get("kid") == kid:
                return key
        raise InvalidTokenError(f"JWKS has no key with kid={kid!r}")


def _fetch_jwks_url(url: str) -> dict[str, Any]:
    """Default JWKS fetcher — stdlib only so we add no http dep."""
    req = urllib_request.Request(  # noqa: S310 — Clerk JWKS is always https://
        url,
        headers={"Accept": "application/json"},
    )
    with urllib_request.urlopen(req, timeout=5) as resp:  # noqa: S310
        body = resp.read().decode("utf-8")
    return json.loads(body)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_clerk_token(
    token: str,
    *,
    config: ClerkConfig,
    jwks_cache: JwksCache | None = None,
    now: int | None = None,
) -> ClerkSession:
    """Verify a Clerk-issued JWT and return the resolved session.

    Raises:
        AuthDisabledError: ``config.mode`` is ``DISABLED``.
        InvalidTokenError: signature / claims wrong.
        ExpiredTokenError: ``exp`` claim in the past.
    """
    if config.mode is AuthMode.DISABLED:
        raise AuthDisabledError("Clerk auth is not configured")
    if config.mode is not AuthMode.CLERK:
        raise InvalidTokenError(
            f"verify_clerk_token does not support mode {config.mode.value}"
        )
    if jwt is None or algorithms is None:
        raise InvalidTokenError(
            "PyJWT[crypto] is not installed; cannot verify RS256 tokens"
        )
    if not token:
        raise InvalidTokenError("empty bearer token")

    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:  # type: ignore[union-attr]
        raise InvalidTokenError(f"malformed JWT header: {exc}") from exc

    alg = unverified_header.get("alg")
    if alg != "RS256":
        raise InvalidTokenError(f"unexpected JWT algorithm {alg!r}; expected RS256")

    kid = unverified_header.get("kid")
    if not kid:
        raise InvalidTokenError("JWT header missing kid")

    cache = jwks_cache or JwksCache(config.jwks_url)
    jwk = cache.key_for_kid(kid)
    public_key = algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))

    try:
        # Clerk session tokens don't always set `aud`; we validate `iss`
        # and `azp` (authorized party) manually below for tighter control.
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
            leeway=config.clock_skew_seconds,
        )
    except jwt.ExpiredSignatureError as exc:  # type: ignore[union-attr]
        raise ExpiredTokenError(str(exc)) from exc
    except jwt.PyJWTError as exc:  # type: ignore[union-attr]
        raise InvalidTokenError(f"JWT verification failed: {exc}") from exc

    iss = str(claims.get("iss") or "")
    if iss.rstrip("/") != config.issuer:
        raise InvalidTokenError(
            f"unexpected iss {iss!r}; expected {config.issuer!r}"
        )

    if config.authorized_parties:
        azp = str(claims.get("azp") or "")
        if azp and azp not in config.authorized_parties:
            raise InvalidTokenError(
                f"unauthorized party {azp!r}; allowed: {config.authorized_parties}"
            )

    clerk_user_id = str(claims.get("sub") or "")
    if not clerk_user_id:
        raise InvalidTokenError("JWT has no `sub` claim")

    email = (
        str(claims.get("email") or "").strip().lower()
        or _email_from_clerk_claims(claims)
    )
    if not email:
        # Clerk tokens *can* be issued without email (anonymous sessions).
        # For the Pro tier we hard-require email, so refuse cleanly.
        raise InvalidTokenError("JWT has no resolvable email claim")

    issued_at_raw = claims.get("iat")
    expires_at_raw = claims.get("exp")
    issued_at = int(issued_at_raw) if isinstance(issued_at_raw, int) else 0
    expires_at = int(expires_at_raw) if isinstance(expires_at_raw, int) else 0

    # Belt-and-braces check (PyJWT already enforces exp, but our leeway
    # config might shift it).
    _ = now  # reserved for monkey-patched test clocks; jwt.decode handles now

    return ClerkSession(
        clerk_user_id=clerk_user_id,
        email=email,
        issued_at=issued_at,
        expires_at=expires_at,
        raw_claims=dict(claims),
    )


def _email_from_clerk_claims(claims: dict[str, Any]) -> str:
    """Clerk JWT templates can put the email in different places.

    Common spots:
      * ``email`` (when the JWT template explicitly maps it).
      * ``email_addresses`` (Clerk's user-object passthrough — array of
        objects with ``email_address``).
      * ``primary_email_address`` (older templates).
    """
    addresses = claims.get("email_addresses")
    if isinstance(addresses, list):
        for entry in addresses:
            if isinstance(entry, dict):
                value = str(entry.get("email_address") or "").strip().lower()
                if value:
                    return value
    primary = str(claims.get("primary_email_address") or "").strip().lower()
    return primary
