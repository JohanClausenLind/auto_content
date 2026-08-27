"""/v1/workspaces, /v1/prefs, /v1/brand-kits (workspace-scoped sample resource for isolation tests)."""  # noqa: E501

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, current_principal, get_db, require_role
from content_factory.db.base import new_id
from content_factory.db.models import AccountPreference, BrandKit, Role
from content_factory.services import accounts as svc
from content_factory.services import audit

router = APIRouter(prefix="/v1", tags=["workspaces"])
VIEWER = require_role(Role.viewer)
EDITOR = require_role(Role.editor)


@router.get("/workspaces")
async def list_workspaces(
    p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    return [
        {"id": ws.id, "slug": ws.slug, "name": ws.name, "role": role.value}
        for ws, role in await svc.memberships_for(db, p.account)
    ]


class PrefBody(BaseModel):
    value: Any = None


@router.get("/prefs/{key}")
async def get_pref(
    key: str, p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    row = (
        await db.execute(
            select(AccountPreference).where(
                AccountPreference.account_id == p.account.id, AccountPreference.key == key
            )
        )
    ).scalar_one_or_none()
    return {"value": row.value if row else None}


@router.put("/prefs/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def put_pref(
    key: str,
    body: PrefBody,
    p: Principal = Depends(current_principal),
    db: AsyncSession = Depends(get_db),
) -> None:
    if len(key) > 64 or not key.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid preference key")
    row = (
        await db.execute(
            select(AccountPreference).where(
                AccountPreference.account_id == p.account.id, AccountPreference.key == key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(
            AccountPreference(id=new_id("pref"), account_id=p.account.id, key=key, value=body.value)
        )
    else:
        row.value = body.value


class BrandKitBody(BaseModel):
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=200)
    tokens: dict[str, Any] = Field(default_factory=dict)


@router.get("/brand-kits")
async def list_brand_kits(
    p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(BrandKit).where(BrandKit.workspace_id == p.workspace_id).order_by(BrandKit.name)
        )
    ).scalars()
    return [
        {
            "id": b.id,
            "slug": b.slug,
            "name": b.name,
            "tokens": b.tokens,
            "workspace_id": b.workspace_id,
        }
        for b in rows
    ]


@router.post("/brand-kits", status_code=status.HTTP_201_CREATED)
async def create_brand_kit(
    body: BrandKitBody,
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    kit = BrandKit(
        id=new_id("bk"),
        workspace_id=p.workspace_id,
        slug=body.slug,
        name=body.name,
        tokens=body.tokens,
    )
    db.add(kit)
    await audit.record(
        db,
        "brand_kit.create",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="brand_kit",
        target_id=kit.id,
    )
    return {
        "id": kit.id,
        "slug": kit.slug,
        "name": kit.name,
        "tokens": kit.tokens,
        "workspace_id": kit.workspace_id,
    }


@router.get("/brand-kits/{kit_id}")
async def get_brand_kit(
    kit_id: str,
    p: Principal = Depends(VIEWER),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # Always filter by the principal's workspace: a foreign id yields 404, never 403 (no oracle).
    kit = (
        await db.execute(
            select(BrandKit).where(BrandKit.id == kit_id, BrandKit.workspace_id == p.workspace_id)
        )
    ).scalar_one_or_none()
    if kit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return {
        "id": kit.id,
        "slug": kit.slug,
        "name": kit.name,
        "tokens": kit.tokens,
        "workspace_id": kit.workspace_id,
    }
