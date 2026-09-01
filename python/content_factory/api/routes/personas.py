"""/v1/personas: governed, versioned persona assets. The PersonaFirewall is code and has no
representation here; the Persona schema itself refuses presented_age < 18. `revise` previews a
typed diff; `apply` is revision-bound (stale diffs are refused, never merged)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, get_db, require_role
from content_factory.db.base import new_id, utcnow
from content_factory.db.models import PersonaRow, Role
from content_factory.personas.revise import ReviseError, apply_diff, map_persona_feedback
from content_factory.schemas.personas import Persona, PersonaRevisionDiff
from content_factory.services import audit

router = APIRouter(prefix="/v1/personas", tags=["personas"])
VIEWER = require_role(Role.viewer)
EDITOR = require_role(Role.editor)


def _summary(row: PersonaRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "revision": row.revision,
        "archived": row.archived_at is not None,
    }


async def _get_row(db: AsyncSession, p: Principal, persona_id: str) -> PersonaRow:
    row = (
        await db.execute(
            select(PersonaRow).where(
                PersonaRow.id == persona_id, PersonaRow.workspace_id == p.workspace_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return row


@router.get("")
async def list_personas(
    p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(PersonaRow)
            .where(PersonaRow.workspace_id == p.workspace_id)
            .order_by(PersonaRow.name)
        )
    ).scalars()
    return [_summary(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_persona(
    body: dict[str, Any],
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # ids, workspace binding, and the starting revision are server-assigned, never client-supplied.
    body.pop("persona_id", None)
    body.pop("workspace_id", None)
    body.pop("revision", None)
    try:
        persona = Persona.model_validate(
            {
                **body,
                "persona_id": new_id("psn"),
                "workspace_id": p.workspace_id,
                "revision": 1,
            }
        )
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors(include_url=False)
        ) from exc
    row = PersonaRow(
        id=persona.persona_id,
        workspace_id=p.workspace_id,
        name=persona.identity.display_name,
        revision=1,
        document=persona.model_dump(mode="json"),
    )
    db.add(row)
    await audit.record(
        db,
        "persona.create",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="persona",
        target_id=row.id,
    )
    return {**_summary(row), "document": row.document}


@router.get("/{persona_id}")
async def get_persona(
    persona_id: str, p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    row = await _get_row(db, p, persona_id)
    return {**_summary(row), "document": row.document}


class ReviseBody(BaseModel):
    feedback: str = Field(min_length=3, max_length=2000)


@router.post("/{persona_id}/revise")
async def revise_persona(
    persona_id: str,
    body: ReviseBody,
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Preview only: maps plain language to a typed diff. Nothing changes until apply."""
    row = await _get_row(db, p, persona_id)
    persona = Persona.model_validate(row.document)
    try:
        diff = map_persona_feedback(body.feedback, persona)
    except ReviseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return diff.model_dump(mode="json")


@router.post("/{persona_id}/apply")
async def apply_persona_diff(
    persona_id: str,
    body: dict[str, Any],
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_row(db, p, persona_id)
    try:
        diff = PersonaRevisionDiff.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors(include_url=False)
        ) from exc
    if diff.persona_id != row.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "diff is for another persona")
    persona = Persona.model_validate(row.document)
    try:
        updated = apply_diff(persona, diff)
    except ReviseError as exc:  # stale base_revision → conflict, the operator must re-review
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    row.document = updated.model_dump(mode="json")
    row.revision = updated.revision
    row.name = updated.identity.display_name
    await audit.record(
        db,
        "persona.apply",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="persona",
        target_id=row.id,
        detail={"base_revision": diff.base_revision, "diff": diff.model_dump(mode="json")},
    )
    return {**_summary(row), "document": row.document}


@router.post("/{persona_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
async def archive_persona(
    persona_id: str, p: Principal = Depends(EDITOR), db: AsyncSession = Depends(get_db)
) -> None:
    row = await _get_row(db, p, persona_id)
    row.archived_at = utcnow()
    await audit.record(
        db,
        "persona.archive",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="persona",
        target_id=row.id,
    )
