"""Accounts, sessions, MFA, and workspace membership (3.1)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.auth import passwords, totp
from content_factory.db.base import new_id, utcnow
from content_factory.db.models import (
    Account,
    AuthChallenge,
    Passkey,
    RecoveryCode,
    Role,
    Session,
    Workspace,
    WorkspaceMembership,
)
from content_factory.services import audit

SESSION_TTL = timedelta(days=14)
STEP_UP_TTL = timedelta(minutes=10)
CHALLENGE_TTL = timedelta(minutes=5)


class AuthError(Exception):
    pass


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    session: Session
    raw_token: str
    mfa_methods: tuple[str, ...] = ()

    @property
    def mfa_required(self) -> bool:
        return bool(self.mfa_methods)


# --- accounts -----------------------------------------------------------------------------------
async def create_account(
    db: AsyncSession,
    *,
    username: str,
    display_name: str,
    password: str | None,
    is_owner: bool = False,
) -> Account:
    acct = Account(
        id=new_id("acct"),
        username=username.lower().strip(),
        display_name=display_name,
        password_hash=passwords.hash_password(password) if password else None,
        is_owner=is_owner,
    )
    db.add(acct)
    await db.flush()
    await audit.record(
        db,
        "account.create",
        actor_account_id=None,
        target_type="account",
        target_id=acct.id,
        detail={"username": acct.username, "is_owner": is_owner},
    )
    return acct


async def get_account_by_username(db: AsyncSession, username: str) -> Account | None:
    return (
        await db.execute(select(Account).where(Account.username == username.lower().strip()))
    ).scalar_one_or_none()


# --- workspaces ---------------------------------------------------------------------------------
async def create_workspace(db: AsyncSession, *, slug: str, name: str, owner: Account) -> Workspace:
    ws = Workspace(id=new_id("ws"), slug=slug, name=name)
    db.add(ws)
    await db.flush()
    db.add(
        WorkspaceMembership(
            id=new_id("mem"), workspace_id=ws.id, account_id=owner.id, role=Role.owner
        )
    )
    await audit.record(
        db,
        "workspace.create",
        actor_account_id=owner.id,
        workspace_id=ws.id,
        target_type="workspace",
        target_id=ws.id,
        detail={"slug": slug},
    )
    return ws


async def add_member(
    db: AsyncSession, *, workspace: Workspace, account: Account, role: Role, actor: Account
) -> WorkspaceMembership:
    mem = WorkspaceMembership(
        id=new_id("mem"), workspace_id=workspace.id, account_id=account.id, role=role
    )
    db.add(mem)
    await audit.record(
        db,
        "workspace.member.add",
        actor_account_id=actor.id,
        workspace_id=workspace.id,
        target_type="account",
        target_id=account.id,
        detail={"role": role.value},
    )
    return mem


async def memberships_for(db: AsyncSession, account: Account) -> list[tuple[Workspace, Role]]:
    rows = (
        await db.execute(
            select(Workspace, WorkspaceMembership.role)
            .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where(WorkspaceMembership.account_id == account.id)
            .order_by(Workspace.name)
        )
    ).all()
    return [(ws, role) for ws, role in rows]


async def role_in(db: AsyncSession, account: Account, workspace_id: str) -> Role | None:
    if account.is_owner:
        return Role.owner
    return (
        await db.execute(
            select(WorkspaceMembership.role).where(
                WorkspaceMembership.account_id == account.id,
                WorkspaceMembership.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()


# --- sessions -----------------------------------------------------------------------------------
async def issue_session(
    db: AsyncSession, account: Account, *, mfa_pending: bool, ip: str | None, user_agent: str | None
) -> IssuedSession:
    raw = secrets.token_urlsafe(32)
    now = utcnow()
    memberships = await memberships_for(db, account)
    sess = Session(
        id=new_id("sess"),
        account_id=account.id,
        token_hash=_hash_token(raw),
        current_workspace_id=memberships[0][0].id if memberships else None,
        created_at=now,
        expires_at=now + SESSION_TTL,
        last_seen_at=now,
        step_up_until=None if mfa_pending else now + STEP_UP_TTL,
        mfa_pending=mfa_pending,
        ip=ip,
        user_agent=(user_agent or "")[:400] or None,
    )
    db.add(sess)
    await db.flush()
    return IssuedSession(sess, raw)


async def login_password(
    db: AsyncSession, *, username: str, password: str, ip: str | None, user_agent: str | None
) -> IssuedSession:
    acct = await get_account_by_username(db, username)
    if (
        acct is None
        or acct.disabled
        or not acct.password_hash
        or not passwords.verify_password(acct.password_hash, password)
    ):
        await audit.record(
            db,
            "session.login.failed",
            actor_account_id=None,
            detail={"username": username.lower().strip()[:64]},
            ip=ip,
        )
        raise AuthError("invalid username or password")
    has_passkeys = bool(
        (await db.execute(select(Passkey.id).where(Passkey.account_id == acct.id).limit(1))).first()
    )
    mfa_methods = [
        m for m, on in (("totp", bool(acct.totp_secret)), ("passkey", has_passkeys)) if on
    ]
    issued = await issue_session(
        db, acct, mfa_pending=bool(mfa_methods), ip=ip, user_agent=user_agent
    )
    await audit.record(
        db,
        "session.login",
        actor_account_id=acct.id,
        detail={"mfa_pending": bool(mfa_methods)},
        ip=ip,
    )
    return IssuedSession(issued.session, issued.raw_token, tuple(mfa_methods))


async def complete_totp(db: AsyncSession, session: Session, code: str) -> Session:
    acct = await db.get(Account, session.account_id)
    if acct is None or not acct.totp_secret:
        raise AuthError("totp not enrolled")
    counter = totp.verify_totp(acct.totp_secret, code, last_used_counter=acct.totp_last_counter)
    if counter is None:
        await audit.record(
            db, "session.mfa.failed", actor_account_id=acct.id, detail={"method": "totp"}
        )
        raise AuthError("invalid code")
    acct.totp_last_counter = counter
    session.mfa_pending = False
    session.step_up_until = utcnow() + STEP_UP_TTL
    await audit.record(db, "session.mfa", actor_account_id=acct.id, detail={"method": "totp"})
    return session


async def resolve_session(db: AsyncSession, raw_token: str) -> Session | None:
    sess = (
        await db.execute(select(Session).where(Session.token_hash == _hash_token(raw_token)))
    ).scalar_one_or_none()
    if sess is None or sess.revoked_at is not None or sess.expires_at <= utcnow():
        return None
    sess.last_seen_at = utcnow()
    return sess


async def revoke_session(db: AsyncSession, session: Session) -> None:
    session.revoked_at = utcnow()
    await audit.record(db, "session.logout", actor_account_id=session.account_id)


async def switch_workspace(
    db: AsyncSession, session: Session, account: Account, workspace_id: str
) -> Session:
    if await role_in(db, account, workspace_id) is None:
        raise AuthError("not a member of that workspace")
    session.current_workspace_id = workspace_id
    return session


def step_up_valid(session: Session) -> bool:
    return session.step_up_until is not None and session.step_up_until > utcnow()


async def step_up_with_password(
    db: AsyncSession, session: Session, account: Account, password: str
) -> None:
    if not account.password_hash or not passwords.verify_password(account.password_hash, password):
        await audit.record(db, "session.step_up.failed", actor_account_id=account.id)
        raise AuthError("invalid password")
    session.step_up_until = utcnow() + STEP_UP_TTL
    await audit.record(db, "session.step_up", actor_account_id=account.id)


# --- TOTP enrolment ------------------------------------------------------------------------------
async def enroll_totp_begin(account: Account) -> tuple[str, str]:
    secret = totp.new_totp_secret()
    return secret, totp.provisioning_uri(secret, account.username)


async def enroll_totp_confirm(
    db: AsyncSession, account: Account, secret: str, code: str
) -> list[str]:
    counter = totp.verify_totp(secret, code)
    if counter is None:
        raise AuthError("invalid code")
    account.totp_secret = secret
    account.totp_last_counter = counter
    codes = totp.new_recovery_codes()
    for c in codes:
        db.add(
            RecoveryCode(
                id=new_id("rc"), account_id=account.id, code_hash=totp.hash_recovery_code(c)
            )
        )
    await audit.record(db, "account.totp.enrolled", actor_account_id=account.id)
    return codes


# --- passkeys ------------------------------------------------------------------------------------
async def new_challenge(
    db: AsyncSession, *, account_id: str | None, kind: str, challenge: bytes
) -> AuthChallenge:
    now = utcnow()
    ch = AuthChallenge(
        id=new_id("chal"),
        account_id=account_id,
        kind=kind,
        challenge=challenge,
        created_at=now,
        expires_at=now + CHALLENGE_TTL,
    )
    db.add(ch)
    await db.flush()
    return ch


async def consume_challenge(db: AsyncSession, challenge_id: str, kind: str) -> AuthChallenge:
    ch = await db.get(AuthChallenge, challenge_id)
    if ch is None or ch.kind != kind or ch.consumed_at is not None or ch.expires_at <= utcnow():
        raise AuthError("challenge invalid or expired")
    ch.consumed_at = utcnow()
    return ch


async def passkeys_for(db: AsyncSession, account_id: str) -> list[Passkey]:
    return list(
        (await db.execute(select(Passkey).where(Passkey.account_id == account_id))).scalars()
    )


async def find_passkey(db: AsyncSession, credential_id: bytes) -> Passkey | None:
    return (
        await db.execute(select(Passkey).where(Passkey.credential_id == credential_id))
    ).scalar_one_or_none()
