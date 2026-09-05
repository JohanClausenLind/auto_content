"""Connection broker (22.3): server-side OAuth intents with state + PKCE (S256), exact redirect
URIs, single-use callbacks; tokens sealed into the vault the moment they arrive."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.db.base import new_id
from content_factory.db.models import ConnectedAccount, OAuthState
from content_factory.security.pkce import pkce_pair
from content_factory.security.vault import TokenVault

STATE_TTL = timedelta(minutes=10)


class OAuthError(Exception):
    pass


@dataclass(frozen=True)
class ProviderApp:
    """The operator's own developer app for one platform (client credentials from env)."""

    platform: str
    authorize_url: str
    token_url: str
    client_id: str
    client_secret: str
    scopes: tuple[str, ...]
    supports_pkce: bool = True


async def begin(
    db: AsyncSession, *, workspace_id: str, app: ProviderApp, redirect_uri: str
) -> tuple[str, str]:
    """Create a single-use intent; return (authorize_url, state)."""
    if not redirect_uri.startswith(("http://127.0.0.1", "http://localhost", "https://")):
        raise OAuthError("redirect URI must be loopback or https")
    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    now = datetime.now(UTC)
    db.add(
        OAuthState(
            id=new_id("oas"),
            workspace_id=workspace_id,
            state=state,
            platform=app.platform,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            created_at=now,
            expires_at=now + STATE_TTL,
        )
    )
    params = {
        "response_type": "code",
        "client_id": app.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(app.scopes),
        "state": state,
    }
    if app.supports_pkce:
        params |= {"code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{app.authorize_url}?{urlencode(params)}", state


async def complete(
    db: AsyncSession,
    *,
    workspace_id: str,
    app: ProviderApp,
    state: str,
    code: str,
    vault: TokenVault,
    handle: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectedAccount:
    """Single-use callback: verify state (workspace-bound, unexpired, unconsumed), exchange the
    code with PKCE, seal tokens. A replayed or foreign state is refused."""
    row = (
        await db.execute(select(OAuthState).where(OAuthState.state == state))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None:
        raise OAuthError("unknown state (possible CSRF)")
    if row.workspace_id != workspace_id:
        raise OAuthError("state belongs to a different workspace")
    if row.platform != app.platform:
        raise OAuthError("state was issued for a different platform")
    if row.consumed_at is not None:
        raise OAuthError("state already used (callback replay refused)")
    if row.expires_at <= now:
        raise OAuthError("state expired; restart the connection")
    row.consumed_at = now

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": row.redirect_uri,  # exact URI from the intent, never from the request
        "client_id": app.client_id,
        "client_secret": app.client_secret,
    }
    if app.supports_pkce:
        data["code_verifier"] = row.code_verifier
    async with httpx.AsyncClient(transport=transport, timeout=30) as http:
        resp = await http.post(app.token_url, data=data)
    if resp.status_code >= 400:
        raise OAuthError(f"token exchange failed ({resp.status_code}): {resp.text[:200]}")
    tokens = resp.json()
    access = tokens.get("access_token")
    if not access:
        raise OAuthError("token response carried no access_token")
    aad = f"{workspace_id}|{app.platform}|access"
    sealed = vault.seal(access, aad=aad)
    refresh = tokens.get("refresh_token")
    sealed_refresh = (
        vault.seal(refresh, aad=f"{workspace_id}|{app.platform}|refresh") if refresh else None
    )
    expires_at = (
        now + timedelta(seconds=int(tokens["expires_in"])) if tokens.get("expires_in") else None
    )
    account = ConnectedAccount(
        id=new_id("acc"),
        workspace_id=workspace_id,
        platform=app.platform,
        handle=handle,
        scopes=list(app.scopes),
        token_key_id=sealed.key_id,
        token_nonce=sealed.nonce_b64,
        token_ciphertext=sealed.ciphertext_b64,
        refresh_key_id=sealed_refresh.key_id if sealed_refresh else None,
        refresh_nonce=sealed_refresh.nonce_b64 if sealed_refresh else None,
        refresh_ciphertext=sealed_refresh.ciphertext_b64 if sealed_refresh else None,
        expires_at=expires_at,
    )
    db.add(account)
    return account


def open_access_token(account: ConnectedAccount, vault: TokenVault) -> str:
    from content_factory.security.vault import Sealed

    return vault.open(
        Sealed(account.token_key_id, account.token_nonce, account.token_ciphertext),
        aad=f"{account.workspace_id}|{account.platform}|access",
    )
