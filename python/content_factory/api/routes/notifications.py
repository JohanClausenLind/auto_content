"""/v1/notifications: PushSubscription registration + a test push. Deep links only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, current_principal, get_db
from content_factory.config import get_settings
from content_factory.db.base import new_id
from content_factory.db.models import PushSubscription
from content_factory.notifications.push import action_item_payload, load_vapid_keys, send_web_push

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


@router.get("/vapid-public-key")
async def vapid_public_key() -> dict[str, str]:
    keys = load_vapid_keys(get_settings().notifications.web_push.vapid_keys_env)
    if not keys:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "web push is not configured (run setup)"
        )
    return {"public_key": keys["public"]}


class SubscriptionBody(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)
    keys: dict[str, str]
    user_agent: str | None = Field(default=None, max_length=400)


@router.post("/subscriptions", status_code=status.HTTP_201_CREATED)
async def register(
    body: SubscriptionBody,
    p: Principal = Depends(current_principal),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not body.endpoint.startswith("https://"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "push endpoints must be https")
    if {"p256dh", "auth"} - set(body.keys):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "subscription keys must include p256dh and auth"
        )
    existing = (
        await db.execute(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    ).scalar_one_or_none()
    if existing:
        existing.account_id = p.account.id
        existing.keys = body.keys
        return {"id": existing.id}
    sub = PushSubscription(
        id=new_id("push"),
        account_id=p.account.id,
        endpoint=body.endpoint,
        keys=body.keys,
        user_agent=body.user_agent,
    )
    db.add(sub)
    return {"id": sub.id}


@router.delete("/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def unregister(
    endpoint: str, p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
) -> None:
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == endpoint, PushSubscription.account_id == p.account.id
        )
    )


@router.post("/test")
async def send_test(
    p: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    settings = get_settings()
    keys = load_vapid_keys(settings.notifications.web_push.vapid_keys_env)
    if not keys:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "web push is not configured")
    subs = (
        (
            await db.execute(
                select(PushSubscription).where(PushSubscription.account_id == p.account.id)
            )
        )
        .scalars()
        .all()
    )
    payload = action_item_payload(
        {
            "id": "test",
            "title": "Test notification",
            "body": "Push works. This never approves anything.",
            "kind": "test",
            "deep_link": "/",
        },
        settings.public_base_url,
    )
    results = [
        send_web_push({"endpoint": s.endpoint, "keys": s.keys}, payload, vapid=keys) for s in subs
    ]
    for s, r in zip(subs, results, strict=True):
        if r.gone:
            await db.delete(s)
    return {"sent": sum(1 for r in results if r.ok), "total": len(results)}
