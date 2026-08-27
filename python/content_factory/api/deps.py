"""FastAPI dependencies: DB session, current session/account, workspace scope, RBAC, step-up."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.db.models import Account, Role, Session
from content_factory.db.session import get_sessionmaker
from content_factory.services import accounts as svc

COOKIE_NAME = "cf_session"
_ROLE_RANK = {Role.viewer: 0, Role.reviewer: 1, Role.editor: 2, Role.owner: 3}


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    maker = (
        request.app.state.sessionmaker
        if hasattr(request.app.state, "sessionmaker")
        else get_sessionmaker()
    )
    async with maker() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise


@dataclass
class Principal:
    session: Session
    account: Account
    role: Role | None  # role in the current workspace (None when no workspace selected)

    @property
    def workspace_id(self) -> str:
        if not self.session.current_workspace_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no workspace selected")
        return self.session.current_workspace_id


async def optional_principal(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Principal | None:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    sess = await svc.resolve_session(db, raw)
    if sess is None:
        return None
    acct = await db.get(Account, sess.account_id)
    if acct is None or acct.disabled:
        return None
    role = (
        await svc.role_in(db, acct, sess.current_workspace_id)
        if sess.current_workspace_id
        else None
    )
    return Principal(sess, acct, role)


async def pending_mfa_principal(p: Principal | None = Depends(optional_principal)) -> Principal:
    if p is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    return p


async def current_principal(p: Principal | None = Depends(optional_principal)) -> Principal:
    if p is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    if p.session.mfa_pending:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "mfa required")
    return p


def require_role(minimum: Role):
    async def _dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.role is None or _ROLE_RANK[p.role] < _ROLE_RANK[minimum]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"requires role {minimum.value} in this workspace"
            )
        return p

    return _dep


async def require_step_up(p: Principal = Depends(current_principal)) -> Principal:
    if not svc.step_up_valid(p.session):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "step-up authentication required",
            headers={"X-Step-Up": "required"},
        )
    return p


async def require_owner(p: Principal = Depends(current_principal)) -> Principal:
    if not p.account.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner only")
    return p
