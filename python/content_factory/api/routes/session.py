"""/v1/session: password login, MFA (TOTP/passkey), logout, workspace switch, step-up."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import (
    COOKIE_NAME,
    Principal,
    current_principal,
    get_db,
    optional_principal,
    pending_mfa_principal,
)
from content_factory.auth import passkeys
from content_factory.config import get_settings
from content_factory.db.base import utcnow
from content_factory.db.models import Account, Passkey
from content_factory.services import accounts as svc
from content_factory.services import audit

router = APIRouter(prefix="/v1/session", tags=["session"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class TotpBody(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class WorkspaceBody(BaseModel):
    workspace_id: str


class StepUpBody(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class PasskeyVerifyBody(BaseModel):
    challenge_id: str
    credential: dict[str, Any]


class PasskeyRegisterBody(BaseModel):
    challenge_id: str
    credential: dict[str, Any]
    label: str = Field(default="passkey", max_length=100)


def _set_cookie(response: Response, raw: str) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        raw,
        httponly=True,
        samesite="lax",
        secure=s.public_base_url.startswith("https://"),
        max_age=int(svc.SESSION_TTL.total_seconds()),
        path="/",
    )


async def _session_view(db: AsyncSession, p: Principal) -> dict[str, Any]:
    memberships = await svc.memberships_for(db, p.account)
    return {
        "account": {
            "id": p.account.id,
            "username": p.account.username,
            "display_name": p.account.display_name,
            "is_owner": p.account.is_owner,
        },
        "workspaces": [
            {"id": ws.id, "slug": ws.slug, "name": ws.name, "role": role.value}
            for ws, role in memberships
        ],
        "current_workspace_id": p.session.current_workspace_id,
        "step_up_until": p.session.step_up_until.isoformat() if p.session.step_up_until else None,
    }


@router.get("")
async def get_session(
    p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    return await _session_view(db, p)


@router.post("")
async def login(
    body: LoginBody, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> Any:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    try:
        issued = await svc.login_password(
            db, username=body.username, password=body.password, ip=ip, user_agent=ua
        )
    except svc.AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    _set_cookie(response, issued.raw_token)
    if issued.mfa_required:
        response.status_code = status.HTTP_202_ACCEPTED
        return {"mfa_required": True, "methods": list(issued.mfa_methods)}
    acct = await db.get(Account, issued.session.account_id)
    assert acct is not None
    role = (
        await svc.role_in(db, acct, issued.session.current_workspace_id)
        if issued.session.current_workspace_id
        else None
    )
    return await _session_view(db, Principal(issued.session, acct, role))


@router.post("/totp")
async def totp_step(
    body: TotpBody,
    p: Principal = Depends(pending_mfa_principal),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not p.session.mfa_pending:
        return await _session_view(db, p)
    try:
        await svc.complete_totp(db, p.session, body.code)
    except svc.AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    return await _session_view(db, p)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    p: Principal | None = Depends(optional_principal),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if p is not None:
        await svc.revoke_session(db, p.session)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.post("/workspace")
async def switch_workspace(
    body: WorkspaceBody,
    p: Principal = Depends(current_principal),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        await svc.switch_workspace(db, p.session, p.account, body.workspace_id)
    except svc.AuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return await _session_view(db, p)


@router.post("/step-up")
async def step_up(
    body: StepUpBody, p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        await svc.step_up_with_password(db, p.session, p.account, body.password)
    except svc.AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    return await _session_view(db, p)


# --- passkeys ------------------------------------------------------------------------------------
def _rp() -> tuple[str, str, str]:
    s = get_settings()
    return s.auth.rp_id, s.auth.rp_name, s.public_base_url.rstrip("/")


@router.post("/passkey/options")
async def passkey_options(
    p: Principal | None = Depends(optional_principal), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    rp_id, _, _ = _rp()
    allowed: list[passkeys.StoredPasskey] = []
    account_id = None
    if p is not None:
        account_id = p.account.id
        allowed = [
            passkeys.StoredPasskey(
                k.credential_id, k.public_key, k.sign_count, tuple(k.transports), k.label
            )
            for k in await svc.passkeys_for(db, p.account.id)
        ]
    ch = passkeys.begin_authentication(rp_id=rp_id, allowed=allowed)
    row = await svc.new_challenge(
        db, account_id=account_id, kind="authenticate", challenge=ch.challenge
    )
    return {"options": json.loads(ch.options_json), "challenge_id": row.id}


@router.post("/passkey/verify")
async def passkey_verify(
    body: PasskeyVerifyBody,
    request: Request,
    response: Response,
    p: Principal | None = Depends(optional_principal),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rp_id, _, origin = _rp()
    try:
        row = await svc.consume_challenge(db, body.challenge_id, "authenticate")
    except svc.AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    from webauthn.helpers import base64url_to_bytes

    cred_id = base64url_to_bytes(str(body.credential.get("rawId") or body.credential.get("id")))
    key = await svc.find_passkey(db, cred_id)
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown passkey")
    stored = passkeys.StoredPasskey(
        key.credential_id, key.public_key, key.sign_count, tuple(key.transports), key.label
    )
    try:
        updated = passkeys.finish_authentication(
            credential_json=body.credential,
            challenge=row.challenge,
            rp_id=rp_id,
            origin=origin,
            passkey=stored,
        )
    except Exception as exc:  # webauthn raises InvalidAuthenticationResponse
        await audit.record(
            db, "session.mfa.failed", actor_account_id=key.account_id, detail={"method": "passkey"}
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "passkey verification failed") from exc
    key.sign_count = updated.sign_count
    key.last_used_at = utcnow()
    acct = await db.get(Account, key.account_id)
    if acct is None or acct.disabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account unavailable")
    if p is not None and p.session.account_id == acct.id:
        # Completing MFA (or step-up) for the existing session.
        p.session.mfa_pending = False
        p.session.step_up_until = utcnow() + svc.STEP_UP_TTL
        await audit.record(
            db, "session.mfa", actor_account_id=acct.id, detail={"method": "passkey"}
        )
        return await _session_view(db, p)
    ip = request.client.host if request.client else None
    issued = await svc.issue_session(
        db, acct, mfa_pending=False, ip=ip, user_agent=request.headers.get("user-agent")
    )
    await audit.record(
        db, "session.login", actor_account_id=acct.id, detail={"method": "passkey"}, ip=ip
    )
    _set_cookie(response, issued.raw_token)
    role = (
        await svc.role_in(db, acct, issued.session.current_workspace_id)
        if issued.session.current_workspace_id
        else None
    )
    return await _session_view(db, Principal(issued.session, acct, role))


@router.post("/passkey/register/options")
async def passkey_register_options(
    p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    rp_id, rp_name, _ = _rp()
    existing = [
        passkeys.StoredPasskey(
            k.credential_id, k.public_key, k.sign_count, tuple(k.transports), k.label
        )
        for k in await svc.passkeys_for(db, p.account.id)
    ]
    ch = passkeys.begin_registration(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=p.account.id.encode(),
        user_name=p.account.username,
        display_name=p.account.display_name,
        existing=existing,
    )
    row = await svc.new_challenge(
        db, account_id=p.account.id, kind="register", challenge=ch.challenge
    )
    return {"options": json.loads(ch.options_json), "challenge_id": row.id}


@router.post("/passkey/register/verify", status_code=status.HTTP_201_CREATED)
async def passkey_register_verify(
    body: PasskeyRegisterBody,
    p: Principal = Depends(current_principal),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rp_id, _, origin = _rp()
    try:
        row = await svc.consume_challenge(db, body.challenge_id, "register")
    except svc.AuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if row.account_id != p.account.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "challenge does not belong to this account"
        )
    try:
        stored = passkeys.finish_registration(
            credential_json=body.credential,
            challenge=row.challenge,
            rp_id=rp_id,
            origin=origin,
            label=body.label,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "passkey registration failed") from exc
    from content_factory.db.base import new_id

    db.add(
        Passkey(
            id=new_id("pk"),
            account_id=p.account.id,
            credential_id=stored.credential_id,
            public_key=stored.public_key,
            sign_count=stored.sign_count,
            transports=list(stored.transports),
            label=stored.label,
            created_at=utcnow(),
        )
    )
    await audit.record(
        db,
        "account.passkey.registered",
        actor_account_id=p.account.id,
        detail={"label": stored.label},
    )
    return {"label": stored.label}
