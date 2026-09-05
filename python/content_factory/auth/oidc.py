"""Dormant enterprise identity: auth.mode=oidc (3.2). Ships disabled; local mode never needs it.

Standard OIDC authorization-code flow with PKCE against a discovery document; ID tokens are
verified against the IdP's JWKS (signature, iss, aud, exp, nonce) via Authlib. Role mapping and
just-in-time provisioning are pure functions. SAML and SCIM are separate standards and separate
(schema-only) modules — never conflated with this one."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx
from authlib.jose import (
    JsonWebKey,
    JsonWebToken,
)
from authlib.jose.errors import JoseError

from content_factory.security.pkce import pkce_pair


class OIDCError(Exception):
    pass


class OIDCDisabledError(OIDCError):
    pass


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    role_claim: str = "groups"
    role_mapping: dict[str, str] | None = None  # IdP group -> owner|editor|reviewer|viewer


@dataclass(frozen=True)
class OIDCIdentity:
    subject: str
    email: str
    display_name: str
    role: str


def require_enabled(auth_mode: str) -> None:
    if auth_mode != "oidc":
        raise OIDCDisabledError(
            "auth.mode is not 'oidc' — the enterprise identity module is dormant"
        )


def discover(config: OIDCConfig, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    with httpx.Client(transport=transport, timeout=15) as http:
        resp = http.get(config.issuer.rstrip("/") + "/.well-known/openid-configuration")
        resp.raise_for_status()
        doc = resp.json()
    if doc.get("issuer") != config.issuer:
        raise OIDCError("discovery document issuer mismatch")
    return doc


def begin_login(config: OIDCConfig, discovery: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Returns (authorize_url, session_state{state,nonce,verifier}) to keep server-side."""
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    from urllib.parse import urlencode

    url = (
        discovery["authorization_endpoint"]
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": config.client_id,
                "redirect_uri": config.redirect_uri,
                "scope": " ".join(config.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    return url, {"state": state, "nonce": nonce, "verifier": verifier}


def validate_id_token(
    id_token: str, *, config: OIDCConfig, jwks: dict[str, Any], nonce: str, now: int | None = None
) -> dict[str, Any]:
    jwt = JsonWebToken(["RS256", "ES256"])
    try:
        claims = jwt.decode(
            id_token,
            JsonWebKey.import_key_set(jwks),
            claims_options={
                "iss": {"essential": True, "value": config.issuer},
                "aud": {"essential": True, "value": config.client_id},
                "exp": {"essential": True},
            },
        )
        claims.validate(now=now or int(time.time()))
    except JoseError as exc:
        raise OIDCError(f"id_token failed validation: {exc}") from exc
    if claims.get("nonce") != nonce:
        raise OIDCError("id_token nonce mismatch (replay?)")
    return dict(claims)


def map_identity(claims: dict[str, Any], config: OIDCConfig) -> OIDCIdentity:
    """JIT provisioning input: unknown groups become the least-privileged role (deny-by-default)."""
    mapping = config.role_mapping or {}
    role = "viewer"
    for group in claims.get(config.role_claim, []) or []:
        candidate = mapping.get(group)
        rank = {"viewer": 0, "reviewer": 1, "editor": 2, "owner": 3}
        if candidate and rank[candidate] > rank[role]:
            role = candidate
    email = claims.get("email") or ""
    if not email:
        raise OIDCError("id_token carries no email claim; cannot provision an account")
    return OIDCIdentity(
        subject=str(claims["sub"]),
        email=email,
        display_name=str(claims.get("name") or email.split("@")[0]),
        role=role,
    )
