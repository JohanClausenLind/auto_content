"""/v1/brand-nodes: persisted brand hierarchy. Lock semantics come from brands.hierarchy — a
child cannot even declare an override for a key its ancestry locked, and `effective` is the
root-to-leaf merge where locked keys keep the locker's value."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, get_db, require_role
from content_factory.brands.hierarchy import BrandHierarchyError, BrandNode, BrandTree
from content_factory.db.base import new_id
from content_factory.db.models import BrandNodeRow, Role
from content_factory.services import audit

router = APIRouter(prefix="/v1/brand-nodes", tags=["brands"])
VIEWER = require_role(Role.viewer)
EDITOR = require_role(Role.editor)


def _as_dict(row: BrandNodeRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "parent_id": row.parent_id,
        "name": row.name,
        "tokens": row.tokens,
        "locked_tokens": sorted(row.locked_tokens),
        "policies": row.policies,
        "locked_policies": sorted(row.locked_policies),
    }


def _as_node(row: BrandNodeRow) -> BrandNode:
    return BrandNode(
        node_id=row.id,
        parent_id=row.parent_id,
        name=row.name,
        tokens=dict(row.tokens),
        locked_tokens=frozenset(row.locked_tokens),
        policies=dict(row.policies),
        locked_policies=frozenset(row.locked_policies),
    )


async def _load_tree(db: AsyncSession, workspace_id: str) -> BrandTree:
    rows = (
        (
            await db.execute(
                select(BrandNodeRow)
                .where(BrandNodeRow.workspace_id == workspace_id)
                .order_by(BrandNodeRow.created_at)
            )
        )
        .scalars()
        .all()
    )
    tree = BrandTree()
    pending = list(rows)
    while pending:  # parents were created first, but insertion order is defended anyway
        progressed = False
        remaining: list[BrandNodeRow] = []
        for row in pending:
            if row.parent_id is None or row.parent_id in tree.nodes:
                tree.add(_as_node(row))
                progressed = True
            else:
                remaining.append(row)
        if not progressed:  # pragma: no cover — orphan rows would need manual DB surgery
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "brand tree is inconsistent")
        pending = remaining
    return tree


@router.get("")
async def list_brand_nodes(
    p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(BrandNodeRow)
            .where(BrandNodeRow.workspace_id == p.workspace_id)
            .order_by(BrandNodeRow.created_at)
        )
    ).scalars()
    return [_as_dict(r) for r in rows]


class BrandNodeBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None
    tokens: dict[str, str] = Field(default_factory=dict)
    locked_tokens: list[str] = Field(default_factory=list)
    policies: dict[str, str] = Field(default_factory=dict)
    locked_policies: list[str] = Field(default_factory=list)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_brand_node(
    body: BrandNodeBody,
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tree = await _load_tree(db, p.workspace_id)
    node_id = new_id("brand")
    try:
        tree.add(
            BrandNode(
                node_id=node_id,
                parent_id=body.parent_id,
                name=body.name,
                tokens=dict(body.tokens),
                locked_tokens=frozenset(body.locked_tokens),
                policies=dict(body.policies),
                locked_policies=frozenset(body.locked_policies),
            )
        )
    except BrandHierarchyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    row = BrandNodeRow(
        id=node_id,
        workspace_id=p.workspace_id,
        parent_id=body.parent_id,
        name=body.name,
        tokens=dict(body.tokens),
        locked_tokens=sorted(body.locked_tokens),
        policies=dict(body.policies),
        locked_policies=sorted(body.locked_policies),
    )
    db.add(row)
    await audit.record(
        db,
        "brand_node.create",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="brand_node",
        target_id=node_id,
    )
    return _as_dict(row)


@router.get("/{node_id}/effective")
async def effective_brand(
    node_id: str, p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    tree = await _load_tree(db, p.workspace_id)
    try:
        return tree.effective(node_id)
    except BrandHierarchyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from exc
