"""First-run bootstrap and demo seeding (idempotent)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.db.base import new_id
from content_factory.db.models import Account, BrandKit, Role, Workspace
from content_factory.services import accounts as svc


@dataclass(frozen=True)
class BootstrapResult:
    owner_username: str
    generated_password: str | None
    workspaces: tuple[str, ...]


async def bootstrap(
    db: AsyncSession,
    *,
    owner_username: str = "operator",
    password: str | None = None,
    demo: bool = True,
) -> BootstrapResult:
    owner = await svc.get_account_by_username(db, owner_username)
    generated: str | None = None
    if owner is None:
        if password is None:
            password = generated = secrets.token_urlsafe(18)
        owner = await svc.create_account(
            db, username=owner_username, display_name="Operator", password=password, is_owner=True
        )
    created: list[str] = []
    if demo:
        for slug, name, kits in (
            ("demo-editorial", "Demo Editorial", [("house", "House style", {"accent": "#2f6f8f"})]),
            ("demo-brand", "Demo Brand", [("shop", "Shop", {"accent": "#b0413e"})]),
        ):
            ws = (
                await db.execute(select(Workspace).where(Workspace.slug == slug))
            ).scalar_one_or_none()
            if ws is None:
                ws = await svc.create_workspace(db, slug=slug, name=name, owner=owner)
                for kslug, kname, tokens in kits:
                    db.add(
                        BrandKit(
                            id=new_id("bk"),
                            workspace_id=ws.id,
                            slug=kslug,
                            name=kname,
                            tokens=tokens,
                        )
                    )
                created.append(slug)
    return BootstrapResult(owner.username, generated, tuple(created))


async def ensure_collaborator(
    db: AsyncSession,
    *,
    username: str,
    workspace_slug: str,
    role: Role,
    actor: Account,
    password: str,
) -> Account:
    acct = await svc.get_account_by_username(db, username)
    if acct is None:
        acct = await svc.create_account(
            db, username=username, display_name=username.title(), password=password
        )
    ws = (await db.execute(select(Workspace).where(Workspace.slug == workspace_slug))).scalar_one()
    if await svc.role_in(db, acct, ws.id) is None:
        await svc.add_member(db, workspace=ws, account=acct, role=role, actor=actor)
    return acct
