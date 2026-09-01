"""Pull inbound messages through a read adapter, classify, and store idempotently (24.1).
Safety-relevant and harassment messages open an ActionItem — they are never auto-handled."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.db.base import new_id
from content_factory.db.models import ActionItem, EngagementMessage
from content_factory.engagement.adapters import EngagementProvider
from content_factory.engagement.inbox import MessageClass, classify

_ESCALATE = {MessageClass.safety_relevant, MessageClass.harassment}


@dataclass(frozen=True)
class SyncResult:
    fetched: int
    stored: int
    escalated: int


async def sync_provider(
    db: AsyncSession,
    *,
    workspace_id: str,
    account_handle: str,
    provider: EngagementProvider,
    limit: int = 40,
) -> SyncResult:
    inbound = await asyncio.to_thread(provider.fetch_inbound, limit=limit)
    stored = escalated = 0
    for msg in inbound:
        exists = (
            await db.execute(
                select(EngagementMessage.id).where(
                    EngagementMessage.workspace_id == workspace_id,
                    EngagementMessage.platform == provider.platform,
                    EngagementMessage.message_id == msg.message_id,
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        decision = classify(msg)
        db.add(
            EngagementMessage(
                id=new_id("emsg"),
                workspace_id=workspace_id,
                platform=provider.platform,
                account_handle=account_handle,
                message_id=msg.message_id,
                thread_id=msg.thread_id,
                fan_id=msg.fan_id,
                text=msg.text,
                received_at=msg.received_at,
                message_class=decision.message_class.value,
                vip=decision.vip,
            )
        )
        stored += 1
        if decision.message_class in _ESCALATE:
            escalated += 1
            db.add(
                ActionItem(
                    id=new_id("ai"),
                    workspace_id=workspace_id,
                    kind="engagement_escalation",
                    severity="critical",
                    title=f"{decision.message_class.value.replace('_', ' ')} message needs you",
                    body=f"{provider.platform} · {msg.fan_id}: {msg.text[:300]}",
                    dedupe_key=f"engagement:{provider.platform}:{msg.message_id}",
                    deep_link="/inbox",
                )
            )
    return SyncResult(fetched=len(inbound), stored=stored, escalated=escalated)
