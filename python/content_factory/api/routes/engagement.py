"""/v1/engagement: the fan-message inbox. Read adapters pull mentions/replies; classification is
deterministic; the answer-everything ledger requires a reason to skip. Replies are governed
elsewhere (personas) — this surface never sends anything."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, get_db, require_role
from content_factory.config import Settings, get_settings
from content_factory.connections.oauth import open_access_token
from content_factory.db.base import utcnow
from content_factory.db.models import ConnectedAccount, EngagementMessage, MessageDisposition, Role
from content_factory.engagement.adapters import (
    DiscordEngagement,
    EngagementProvider,
    EngagementReadError,
    MastodonEngagement,
)
from content_factory.engagement.sync import sync_provider
from content_factory.services import audit

router = APIRouter(prefix="/v1/engagement", tags=["engagement"])
VIEWER = require_role(Role.viewer)
EDITOR = require_role(Role.editor)


def _settings(request: Request) -> Settings:
    stored = getattr(request.app.state, "settings", None)
    return stored if isinstance(stored, Settings) else get_settings()


def _as_dict(m: EngagementMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "platform": m.platform,
        "account": m.account_handle,
        "fan_id": m.fan_id,
        "text": m.text,
        "received_at": m.received_at,
        "message_class": m.message_class,
        "vip": m.vip,
        "disposition": m.disposition.value,
        "skip_reason": m.skip_reason,
    }


@router.get("/inbox")
async def list_inbox(
    disposition: str = "pending",
    p: Principal = Depends(VIEWER),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    if disposition not in {"pending", "answered", "skipped", "all"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown disposition filter")
    query = select(EngagementMessage).where(EngagementMessage.workspace_id == p.workspace_id)
    if disposition != "all":
        query = query.where(EngagementMessage.disposition == MessageDisposition(disposition))
    rows = (await db.execute(query.order_by(EngagementMessage.received_at.desc()))).scalars()
    return [_as_dict(m) for m in rows]


class DispositionBody(BaseModel):
    disposition: Literal["answered", "skipped"]
    reason: str | None = Field(default=None, max_length=300)


@router.post("/inbox/{item_id}/disposition")
async def set_disposition(
    item_id: str,
    body: DispositionBody,
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if body.disposition == "skipped" and not (body.reason or "").strip():
        # The answer-everything ledger: skipping is allowed, silent skipping is not.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "skipping requires a reason")
    row = (
        await db.execute(
            select(EngagementMessage).where(
                EngagementMessage.id == item_id,
                EngagementMessage.workspace_id == p.workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    row.disposition = MessageDisposition(body.disposition)
    row.skip_reason = body.reason.strip() if body.reason else None
    row.decided_at = utcnow()
    await audit.record(
        db,
        f"engagement.{body.disposition}",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="engagement_message",
        target_id=row.id,
        detail={"reason": row.skip_reason} if row.skip_reason else None,
    )
    return _as_dict(row)


async def _providers(
    request: Request, db: AsyncSession, workspace_id: str
) -> list[tuple[str, EngagementProvider]]:
    """Injected via app.state in tests/dev; built from connected accounts + vault otherwise."""
    injected = getattr(request.app.state, "engagement_providers", None)
    if injected is not None:
        return list(injected(workspace_id))
    from content_factory.security.vault import TokenVault

    vault = TokenVault()
    out: list[tuple[str, EngagementProvider]] = []
    rows = (
        await db.execute(
            select(ConnectedAccount).where(ConnectedAccount.workspace_id == workspace_id)
        )
    ).scalars()
    for acct in rows:
        token = open_access_token(acct, vault)
        if acct.platform == "mastodon":
            base = acct.handle.rsplit("@", 1)[-1]
            out.append(
                (
                    acct.handle,
                    MastodonEngagement(base_url=f"https://{base}", token_getter=lambda t=token: t),
                )
            )
        elif acct.platform == "discord":
            out.extend(
                (
                    acct.handle,
                    DiscordEngagement(channel_id=channel, bot_token_getter=lambda t=token: t),
                )
                for channel in acct.scopes  # scopes carry the readable channel ids
            )
    return out


@router.post("/sync")
async def sync_inbox(
    request: Request,
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not _settings(request).engagement.enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "engagement is disabled (settings.engagement.enabled)",
        )
    providers = await _providers(request, db, p.workspace_id)
    if not providers:
        return {"fetched": 0, "stored": 0, "escalated": 0, "accounts": 0}
    fetched = stored = escalated = 0
    for handle, provider in providers:
        try:
            result = await sync_provider(
                db, workspace_id=p.workspace_id, account_handle=handle, provider=provider
            )
        except EngagementReadError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        fetched += result.fetched
        stored += result.stored
        escalated += result.escalated
    await audit.record(
        db,
        "engagement.sync",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        detail={"fetched": fetched, "stored": stored, "escalated": escalated},
    )
    return {
        "fetched": fetched,
        "stored": stored,
        "escalated": escalated,
        "accounts": len(providers),
    }
