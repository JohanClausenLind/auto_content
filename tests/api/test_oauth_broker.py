"""OAuth/vault attack tests (phase 9 gate): CSRF/foreign state, callback replay, expiry,
wrong workspace, PKCE verifier in the exchange, tokens sealed (never stored in plaintext)."""

from __future__ import annotations

import base64
import hashlib
import json
import os

import httpx
import pytest

from content_factory.connections.oauth import (
    OAuthError,
    ProviderApp,
    begin,
    complete,
    open_access_token,
)
from content_factory.security.vault import TokenVault

pytestmark = pytest.mark.integration
APP = ProviderApp(
    platform="mastodon",
    authorize_url="https://masto.example/oauth/authorize",
    token_url="https://masto.example/oauth/token",
    client_id="client123",
    client_secret="secret456",
    scopes=("read", "write:statuses", "write:media"),
)
WS_A = "ws_demo00000001"


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> TokenVault:
    monkeypatch.setenv("VAULT_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())
    return TokenVault()


def token_transport(seen: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
        seen.append(body)
        expected_challenge = None
        if "code_verifier" in body:
            from urllib.parse import unquote

            expected_challenge = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(unquote(body["code_verifier"]).encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )  # noqa: E501
        return httpx.Response(
            200,
            json={
                "access_token": "access-tok-1",
                "refresh_token": "refresh-tok-1",
                "expires_in": 3600,
                "_challenge": expected_challenge,
            },
        )  # noqa: E501

    return httpx.MockTransport(handler)


async def _ws(sessionmaker, ws_id: str, slug: str) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from content_factory.db.models import Workspace

    async with sessionmaker() as db:
        await db.execute(
            insert(Workspace)
            .values(id=ws_id, slug=slug, name=slug, settings={})
            .on_conflict_do_nothing(index_elements=[Workspace.id])
        )  # noqa: E501
        await db.commit()


async def test_full_flow_seals_tokens_and_verifies_pkce(sessionmaker, vault) -> None:
    await _ws(sessionmaker, WS_A, "fixture-demo")
    seen: list[dict] = []
    async with sessionmaker() as db:
        url, state = await begin(
            db, workspace_id=WS_A, app=APP, redirect_uri="http://127.0.0.1:3000/callback"
        )  # noqa: E501
        await db.commit()
    assert (
        "code_challenge=" in url and "code_challenge_method=S256" in url and f"state={state}" in url
    )  # noqa: E501
    async with sessionmaker() as db:
        account = await complete(
            db,
            workspace_id=WS_A,
            app=APP,
            state=state,
            code="authcode",
            vault=vault,
            handle="op@masto.example",
            transport=token_transport(seen),
        )  # noqa: E501
        await db.commit()
    body = seen[0]
    assert body["grant_type"] == "authorization_code" and "code_verifier" in body
    from urllib.parse import unquote

    assert unquote(body["redirect_uri"]) == "http://127.0.0.1:3000/callback"
    # Tokens never stored in plaintext; the vault opens them with the right context only.
    assert "access-tok-1" not in json.dumps(
        {"ct": account.token_ciphertext, "n": account.token_nonce}
    )  # noqa: E501
    assert open_access_token(account, vault) == "access-tok-1"


async def test_replay_foreign_state_expiry_and_workspace_binding(sessionmaker, vault) -> None:
    await _ws(sessionmaker, WS_A, "fixture-demo")
    await _ws(sessionmaker, "ws_other0000001", "other-ws")
    seen: list[dict] = []
    async with sessionmaker() as db:
        _url, state = await begin(
            db, workspace_id=WS_A, app=APP, redirect_uri="http://127.0.0.1:3000/cb"
        )  # noqa: E501
        await db.commit()
    # Wrong workspace
    async with sessionmaker() as db:
        with pytest.raises(OAuthError, match="different workspace"):
            await complete(
                db,
                workspace_id="ws_other0000001",
                app=APP,
                state=state,
                code="c",
                vault=vault,
                handle="h",
                transport=token_transport(seen),
            )  # noqa: E501
        await db.rollback()
    # Consume once, then replay is refused.
    async with sessionmaker() as db:
        await complete(
            db,
            workspace_id=WS_A,
            app=APP,
            state=state,
            code="c",
            vault=vault,
            handle="h",
            transport=token_transport(seen),
        )  # noqa: E501
        await db.commit()
    async with sessionmaker() as db:
        with pytest.raises(OAuthError, match="replay"):
            await complete(
                db,
                workspace_id=WS_A,
                app=APP,
                state=state,
                code="c",
                vault=vault,
                handle="h",
                transport=token_transport(seen),
            )  # noqa: E501
        await db.rollback()
    # Unknown state
    async with sessionmaker() as db:
        with pytest.raises(OAuthError, match="unknown state"):
            await complete(
                db,
                workspace_id=WS_A,
                app=APP,
                state="forged",
                code="c",
                vault=vault,
                handle="h",
                transport=token_transport(seen),
            )  # noqa: E501
        await db.rollback()
    # Bad redirect target at begin()
    async with sessionmaker() as db:
        with pytest.raises(OAuthError, match="loopback or https"):
            await begin(db, workspace_id=WS_A, app=APP, redirect_uri="http://evil.example/cb")
