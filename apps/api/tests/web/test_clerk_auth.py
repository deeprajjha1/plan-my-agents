"""Unit tests for the Clerk JWT verifier + FastAPI dependencies.

Strategy
--------
We never talk to Clerk in tests. Instead:

* Generate a throwaway RSA keypair in :meth:`setUp`.
* Build a JWKS dict from the public key and inject it via the
  :class:`JwksCache` ``fetcher`` hook.
* Sign tokens with PyJWT against the same keypair.

This lets us exercise the full RS256 verification path (the only path
that runs in production) without needing the network or a Clerk dev
instance.
"""

from __future__ import annotations

import base64
import sys
import time
import unittest
from pathlib import Path
from typing import Annotated, Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.auth.clerk import (
    AuthDisabledError,
    AuthMode,
    ClerkConfig,
    ExpiredTokenError,
    InvalidTokenError,
    JwksCache,
    clerk_config_from_env,
    verify_clerk_token,
)
from planmyagents_api.auth.deps import (
    CurrentUser,
    configure_auth_dependencies,
    current_user,
    optional_current_user,
    require_pro_user,
    reset_auth_dependencies,
)
from planmyagents_api.marketplace_store import InMemoryMarketplaceStore

ISSUER = "https://wise-koala-12.clerk.accounts.dev"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
KID = "ins_2N4kCl3rkT3stKidF1xtur3"


def _b64url_uint(value: int) -> str:
    """Encode a big-endian int as base64url (no padding) per RFC7518."""
    byte_length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(byte_length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwks_for_public_key(public_key: rsa.RSAPublicKey, *, kid: str) -> dict[str, Any]:
    public_numbers = public_key.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": kid,
                "n": _b64url_uint(public_numbers.n),
                "e": _b64url_uint(public_numbers.e),
            }
        ]
    }


def _make_keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign(
    private_key: rsa.RSAPrivateKey,
    *,
    claims: dict[str, Any],
    kid: str = KID,
) -> str:
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


class ClerkConfigFromEnvTests(unittest.TestCase):
    def test_disabled_when_no_issuer_set(self) -> None:
        config = clerk_config_from_env(env={})
        self.assertIs(config.mode, AuthMode.DISABLED)
        self.assertFalse(config.is_active())

    def test_dev_mode_picked_up(self) -> None:
        config = clerk_config_from_env(env={"PLANMYAGENTS_AUTH_MODE": "DEV"})
        self.assertIs(config.mode, AuthMode.DEV)

    def test_dev_mode_refused_in_production(self) -> None:
        with self.assertRaises(RuntimeError):
            clerk_config_from_env(
                env={
                    "PLANMYAGENTS_AUTH_MODE": "dev",
                    "PLANMYAGENTS_ENVIRONMENT": "production",
                }
            )

    def test_clerk_mode_derives_jwks_url_from_issuer(self) -> None:
        config = clerk_config_from_env(env={"PLANMYAGENTS_CLERK_ISSUER": ISSUER})
        self.assertIs(config.mode, AuthMode.CLERK)
        self.assertEqual(config.jwks_url, JWKS_URL)
        self.assertEqual(config.issuer, ISSUER)

    def test_authorized_parties_parsed_csv(self) -> None:
        config = clerk_config_from_env(
            env={
                "PLANMYAGENTS_CLERK_ISSUER": ISSUER,
                "PLANMYAGENTS_CLERK_AUTHORIZED_PARTIES": "https://app.example.com, https://app2.example.com",
            }
        )
        self.assertEqual(
            config.authorized_parties,
            ("https://app.example.com", "https://app2.example.com"),
        )


class VerifyClerkTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key, self.public_key = _make_keypair()
        self.jwks = _jwks_for_public_key(self.public_key, kid=KID)
        self.config = ClerkConfig(
            mode=AuthMode.CLERK,
            issuer=ISSUER,
            jwks_url=JWKS_URL,
            authorized_parties=("https://app.example.com",),
        )
        self.cache = JwksCache(JWKS_URL, fetcher=lambda _url: self.jwks)

    def test_disabled_config_refuses(self) -> None:
        disabled = ClerkConfig(mode=AuthMode.DISABLED)
        with self.assertRaises(AuthDisabledError):
            verify_clerk_token("doesnt-matter", config=disabled)

    def test_dev_mode_config_refuses_via_this_function(self) -> None:
        dev = ClerkConfig(mode=AuthMode.DEV)
        with self.assertRaises(InvalidTokenError):
            verify_clerk_token("x", config=dev)

    def test_empty_token_refused(self) -> None:
        with self.assertRaises(InvalidTokenError):
            verify_clerk_token("", config=self.config, jwks_cache=self.cache)

    def test_valid_token_resolves_session(self) -> None:
        now = int(time.time())
        token = _sign(
            self.private_key,
            claims={
                "iss": ISSUER,
                "sub": "user_2NclerkUserId",
                "email": "alice@example.com",
                "azp": "https://app.example.com",
                "iat": now,
                "exp": now + 600,
            },
        )
        session = verify_clerk_token(token, config=self.config, jwks_cache=self.cache)
        self.assertEqual(session.clerk_user_id, "user_2NclerkUserId")
        self.assertEqual(session.email, "alice@example.com")
        self.assertEqual(session.expires_at, now + 600)

    def test_expired_token_raises_expired_error(self) -> None:
        now = int(time.time())
        token = _sign(
            self.private_key,
            claims={
                "iss": ISSUER,
                "sub": "u",
                "email": "a@b.com",
                "iat": now - 600,
                "exp": now - 60,
            },
        )
        with self.assertRaises(ExpiredTokenError):
            verify_clerk_token(token, config=self.config, jwks_cache=self.cache)

    def test_wrong_issuer_refused(self) -> None:
        now = int(time.time())
        token = _sign(
            self.private_key,
            claims={
                "iss": "https://malicious.clerk.dev",
                "sub": "u",
                "email": "a@b.com",
                "iat": now,
                "exp": now + 600,
            },
        )
        with self.assertRaises(InvalidTokenError):
            verify_clerk_token(token, config=self.config, jwks_cache=self.cache)

    def test_unauthorized_party_refused(self) -> None:
        now = int(time.time())
        token = _sign(
            self.private_key,
            claims={
                "iss": ISSUER,
                "sub": "u",
                "email": "a@b.com",
                "azp": "https://attacker.example.com",
                "iat": now,
                "exp": now + 600,
            },
        )
        with self.assertRaises(InvalidTokenError):
            verify_clerk_token(token, config=self.config, jwks_cache=self.cache)

    def test_missing_email_refused(self) -> None:
        now = int(time.time())
        token = _sign(
            self.private_key,
            claims={
                "iss": ISSUER,
                "sub": "u",
                "iat": now,
                "exp": now + 600,
            },
        )
        with self.assertRaises(InvalidTokenError):
            verify_clerk_token(token, config=self.config, jwks_cache=self.cache)

    def test_unknown_kid_refused(self) -> None:
        now = int(time.time())
        token = _sign(
            self.private_key,
            claims={
                "iss": ISSUER,
                "sub": "u",
                "email": "a@b.com",
                "iat": now,
                "exp": now + 600,
            },
            kid="kid-that-does-not-exist",
        )
        with self.assertRaises(InvalidTokenError):
            verify_clerk_token(token, config=self.config, jwks_cache=self.cache)

    def test_tampered_signature_refused(self) -> None:
        now = int(time.time())
        token = _sign(
            self.private_key,
            claims={
                "iss": ISSUER,
                "sub": "u",
                "email": "a@b.com",
                "iat": now,
                "exp": now + 600,
            },
        )
        # Sign with a *different* private key; cache still holds the first
        # public key, so verification must reject.
        other_private, _ = _make_keypair()
        tampered = _sign(other_private, claims={"sub": "u", "iss": ISSUER, "email": "a@b.com", "exp": now + 600})
        with self.assertRaises(InvalidTokenError):
            verify_clerk_token(tampered, config=self.config, jwks_cache=self.cache)
        # The valid token under the right key still works after the attack.
        ok = verify_clerk_token(token, config=self.config, jwks_cache=self.cache)
        self.assertEqual(ok.clerk_user_id, "u")

    def test_alg_confusion_attack_refused(self) -> None:
        """A 'none' or HS256 token must NOT be accepted under RS256 config."""
        now = int(time.time())
        hs_token = jwt.encode(
            {
                "iss": ISSUER,
                "sub": "u",
                "email": "a@b.com",
                "iat": now,
                "exp": now + 600,
            },
            "shared-secret",
            algorithm="HS256",
            headers={"kid": KID},
        )
        with self.assertRaises(InvalidTokenError):
            verify_clerk_token(hs_token, config=self.config, jwks_cache=self.cache)

    def test_email_extracted_from_email_addresses_array(self) -> None:
        now = int(time.time())
        token = _sign(
            self.private_key,
            claims={
                "iss": ISSUER,
                "sub": "u",
                "email_addresses": [
                    {"email_address": "bob@example.com", "verification": {"status": "verified"}}
                ],
                "iat": now,
                "exp": now + 600,
            },
        )
        session = verify_clerk_token(token, config=self.config, jwks_cache=self.cache)
        self.assertEqual(session.email, "bob@example.com")


class JwksCacheTests(unittest.TestCase):
    def test_caches_and_force_refresh(self) -> None:
        calls = []

        def fetcher(url: str) -> dict[str, Any]:
            calls.append(url)
            return {"keys": [{"kid": f"k{len(calls)}"}]}

        cache = JwksCache("https://x/.well-known/jwks.json", fetcher=fetcher)
        first = cache.keyset()
        second = cache.keyset()
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)  # second call hits cache
        forced = cache.keyset(force_refresh=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(forced["keys"][0]["kid"], "k2")

    def test_kid_miss_triggers_refresh(self) -> None:
        keyset_versions = [
            {"keys": [{"kid": "old"}]},
            {"keys": [{"kid": "new"}]},
        ]
        idx = {"i": 0}

        def fetcher(_url: str) -> dict[str, Any]:
            ks = keyset_versions[idx["i"]]
            idx["i"] = min(idx["i"] + 1, len(keyset_versions) - 1)
            return ks

        cache = JwksCache("https://x/jwks", fetcher=fetcher)
        cache.keyset()  # fetches version 0 -> kid=old
        # Now ask for "new" — should trigger one forced refresh.
        key = cache.key_for_kid("new")
        self.assertEqual(key["kid"], "new")

    def test_kid_miss_after_refresh_raises(self) -> None:
        cache = JwksCache(
            "https://x/jwks",
            fetcher=lambda _u: {"keys": [{"kid": "only-one"}]},
        )
        with self.assertRaises(InvalidTokenError):
            cache.key_for_kid("missing")

    def test_empty_jwks_response_raises(self) -> None:
        cache = JwksCache("https://x/jwks", fetcher=lambda _u: {"keys": []})
        with self.assertRaises(InvalidTokenError):
            cache.keyset()


# ---------------------------------------------------------------------------
# FastAPI dependency integration
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/me")
    async def me(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> dict[str, Any]:
        return {
            "user_id": user.user.user_id,
            "clerk_user_id": user.clerk_user_id,
            "email": user.email,
            "plan": user.plan,
        }

    @app.get("/me-optional")
    async def me_optional(
        user: Annotated[CurrentUser | None, Depends(optional_current_user)],
    ) -> dict[str, Any]:
        if user is None:
            return {"anonymous": True}
        return {"anonymous": False, "user_id": user.user.user_id}

    @app.get("/pro-only")
    async def pro_only(
        user: Annotated[CurrentUser, Depends(require_pro_user)],
    ) -> dict[str, Any]:
        return {"plan": user.plan}

    return app


class CurrentUserDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryMarketplaceStore()
        self.private_key, self.public_key = _make_keypair()
        self.jwks = _jwks_for_public_key(self.public_key, kid=KID)
        self.cache = JwksCache(JWKS_URL, fetcher=lambda _url: self.jwks)
        self.config = ClerkConfig(
            mode=AuthMode.CLERK,
            issuer=ISSUER,
            jwks_url=JWKS_URL,
        )
        configure_auth_dependencies(
            config=self.config, jwks_cache=self.cache, store=self.store
        )
        self.client = TestClient(_make_app())

    def tearDown(self) -> None:
        reset_auth_dependencies()

    def _bearer_token(self, *, sub: str, email: str, **extra: Any) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "sub": sub,
            "email": email,
            "iat": now,
            "exp": now + 600,
        }
        claims.update(extra)
        return _sign(self.private_key, claims=claims)

    def test_missing_authorization_returns_401(self) -> None:
        resp = self.client.get("/me")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["code"], "invalid_token")

    def test_valid_token_returns_user_and_upserts(self) -> None:
        token = self._bearer_token(sub="user_abc", email="x@y.com")
        resp = self.client.get(
            "/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["clerk_user_id"], "user_abc")
        self.assertEqual(body["email"], "x@y.com")
        self.assertEqual(body["plan"], "free")
        # Subsequent call returns the same user_id (idempotent upsert).
        resp2 = self.client.get(
            "/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(resp2.json()["user_id"], body["user_id"])

    def test_expired_token_returns_401_with_correct_code(self) -> None:
        now = int(time.time())
        token = _sign(
            self.private_key,
            claims={
                "iss": ISSUER,
                "sub": "u",
                "email": "x@y.com",
                "iat": now - 1000,
                "exp": now - 100,
            },
        )
        resp = self.client.get(
            "/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["code"], "token_expired")

    def test_malformed_authorization_header_refused(self) -> None:
        for header_value in ("", "Bearer", "Token abc", "  ", "Bearer    "):
            resp = self.client.get("/me", headers={"Authorization": header_value})
            self.assertEqual(
                resp.status_code,
                401,
                msg=f"unexpected status for header {header_value!r}",
            )

    def test_optional_current_user_returns_anon_when_missing(self) -> None:
        resp = self.client.get("/me-optional")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"anonymous": True})

    def test_require_pro_user_402_for_free(self) -> None:
        token = self._bearer_token(sub="freeuser", email="free@x.com")
        resp = self.client.get(
            "/pro-only", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(resp.status_code, 402)
        self.assertEqual(resp.json()["detail"]["code"], "pro_required")

    def test_require_pro_user_200_after_upgrade(self) -> None:
        token = self._bearer_token(sub="prouser", email="pro@x.com")
        resp = self.client.get(
            "/me", headers={"Authorization": f"Bearer {token}"}
        )
        user_id = resp.json()["user_id"]
        self.store.set_plan(user_id=user_id, plan="pro")
        resp = self.client.get(
            "/pro-only", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["plan"], "pro")


class DevModeDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryMarketplaceStore()
        configure_auth_dependencies(
            config=ClerkConfig(mode=AuthMode.DEV),
            store=self.store,
        )
        self.client = TestClient(_make_app())

    def tearDown(self) -> None:
        reset_auth_dependencies()

    def test_dev_headers_resolve_user(self) -> None:
        resp = self.client.get(
            "/me",
            headers={
                "X-Dev-Clerk-User-Id": "dev_user_1",
                "X-Dev-Clerk-User-Email": "dev@example.com",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["clerk_user_id"], "dev_user_1")
        self.assertEqual(body["email"], "dev@example.com")

    def test_dev_mode_without_headers_refused(self) -> None:
        resp = self.client.get("/me")
        self.assertEqual(resp.status_code, 401)
        # Dev-mode without headers surfaces as invalid_token.
        self.assertEqual(resp.json()["detail"]["code"], "invalid_token")


class DisabledModeDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        configure_auth_dependencies(
            config=ClerkConfig(mode=AuthMode.DISABLED),
            store=InMemoryMarketplaceStore(),
        )
        self.client = TestClient(_make_app())

    def tearDown(self) -> None:
        reset_auth_dependencies()

    def test_current_user_returns_401_auth_disabled(self) -> None:
        resp = self.client.get("/me")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["code"], "auth_disabled")

    def test_optional_current_user_returns_anonymous(self) -> None:
        resp = self.client.get("/me-optional")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"anonymous": True})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
