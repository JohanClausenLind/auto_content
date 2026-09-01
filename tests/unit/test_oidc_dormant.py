"""Dormant OIDC module (3.2): fixture IdP only — discovery, PKCE begin, ID-token validation
(signature/iss/aud/exp/nonce), role mapping deny-by-default, module refuses when not enabled."""

from __future__ import annotations

import time

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt

from content_factory.auth.oidc import (
    OIDCConfig,
    OIDCDisabledError,
    OIDCError,
    begin_login,
    discover,
    map_identity,
    require_enabled,
    validate_id_token,
)

KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "fixture-key-1"})
JWKS = {"keys": [KEY.as_dict(is_private=False)]}
CONFIG = OIDCConfig(
    issuer="https://idp.example",
    client_id="cf-client",
    client_secret="cf-secret",
    redirect_uri="https://cf.example/auth/callback",
    role_mapping={"content-admins": "owner", "editors": "editor"},
)


def _token(claims_over: dict | None = None, *, kid: str = "fixture-key-1") -> str:
    now = int(time.time())
    claims = {
        "iss": "https://idp.example",
        "aud": "cf-client",
        "sub": "user-1",
        "email": "op@corp.example",
        "name": "Op Erator",
        "exp": now + 300,
        "iat": now,
        "nonce": "nonce-1",
        "groups": ["editors"],
    }
    claims.update(claims_over or {})
    return jwt.encode({"alg": "RS256", "kid": kid}, claims, KEY).decode()


def test_dormant_module_refuses_unless_enabled() -> None:
    with pytest.raises(OIDCDisabledError):
        require_enabled("local")
    require_enabled("oidc")


def test_discovery_and_pkce_begin() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/.well-known/openid-configuration"
        return httpx.Response(
            200,
            json={
                "issuer": "https://idp.example",
                "authorization_endpoint": "https://idp.example/authorize",
                "token_endpoint": "https://idp.example/token",
                "jwks_uri": "https://idp.example/jwks",
            },
        )

    doc = discover(CONFIG, transport=httpx.MockTransport(handler))
    url, session = begin_login(CONFIG, doc)
    assert url.startswith("https://idp.example/authorize?")
    assert "code_challenge_method=S256" in url and session["verifier"] and session["nonce"]

    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": "https://evil.example"})

    with pytest.raises(OIDCError, match="issuer mismatch"):
        discover(CONFIG, transport=httpx.MockTransport(bad))


def test_id_token_validation_and_attacks() -> None:
    claims = validate_id_token(_token(), config=CONFIG, jwks=JWKS, nonce="nonce-1")
    assert claims["sub"] == "user-1"
    with pytest.raises(OIDCError):
        validate_id_token(
            _token({"aud": "someone-else"}), config=CONFIG, jwks=JWKS, nonce="nonce-1"
        )
    with pytest.raises(OIDCError):
        validate_id_token(
            _token({"iss": "https://evil.example"}), config=CONFIG, jwks=JWKS, nonce="nonce-1"
        )
    with pytest.raises(OIDCError):
        validate_id_token(
            _token({"exp": int(time.time()) - 10}), config=CONFIG, jwks=JWKS, nonce="nonce-1"
        )
    with pytest.raises(OIDCError, match="nonce"):
        validate_id_token(_token(), config=CONFIG, jwks=JWKS, nonce="different-nonce")
    other = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "fixture-key-1"})
    forged = jwt.encode(
        {"alg": "RS256", "kid": "fixture-key-1"},
        {
            "iss": "https://idp.example",
            "aud": "cf-client",
            "sub": "user-1",
            "exp": int(time.time()) + 300,
            "nonce": "nonce-1",
        },
        other,
    ).decode()
    with pytest.raises(OIDCError):
        validate_id_token(forged, config=CONFIG, jwks=JWKS, nonce="nonce-1")


def test_role_mapping_is_deny_by_default() -> None:
    identity = map_identity(
        {"sub": "u1", "email": "e@corp.example", "groups": ["editors", "unknown-group"]}, CONFIG
    )
    assert identity.role == "editor"
    assert (
        map_identity({"sub": "u1", "email": "e@corp.example", "groups": ["random"]}, CONFIG).role
        == "viewer"
    )
    assert (
        map_identity(
            {"sub": "u1", "email": "e@corp.example", "groups": ["content-admins", "editors"]},
            CONFIG,
        ).role
        == "owner"
    )
    with pytest.raises(OIDCError, match="email"):
        map_identity({"sub": "u1", "groups": []}, CONFIG)
