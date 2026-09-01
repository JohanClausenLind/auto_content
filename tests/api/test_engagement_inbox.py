"""Engagement inbox API: idempotent sync via a read adapter, escalation, skip-needs-a-reason."""

from __future__ import annotations

import httpx
import pytest

from content_factory.api.app import create_app
from content_factory.config import load_settings
from content_factory.db.models import Role
from content_factory.engagement.adapters import EngagementProvider
from content_factory.engagement.inbox import InboundMessage
from content_factory.services import accounts as svc

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"


class FakeProvider(EngagementProvider):
    platform = "mastodon"
    supports_dms = True

    def __init__(self) -> None:
        self.calls = 0

    def fetch_inbound(self, *, since_id=None, limit=40):
        self.calls += 1
        mk = lambda i, text: InboundMessage(  # noqa: E731
            message_id=f"m{i}",
            platform="mastodon",
            thread_id=f"t{i}",
            fan_id=f"fan{i}",
            text=text,
            received_at=f"2026-09-01T08:0{i}:00Z",
        )
        return [
            mk(1, "How do you plan your videos?"),
            mk(2, "love this, so good"),
            mk(3, "i'm 14 and my parents don't know i watch"),
        ]


async def seed(sessionmaker):
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        alice = await svc.create_account(db, username="alice", display_name="Alice", password=PW)
        ws = await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        await svc.add_member(db, workspace=ws, account=alice, role=Role.editor, actor=owner)
        await db.commit()


@pytest.fixture
async def engagement_client(sessionmaker):
    app = create_app(load_settings(engagement={"enabled": True}))
    app.state.sessionmaker = sessionmaker
    provider = FakeProvider()
    app.state.engagement_providers = lambda workspace_id: [("@nova@example.social", provider)]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3000") as c:
        yield c, provider


async def login(c, username: str):
    r = await c.post("/v1/session", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()


async def test_sync_classifies_escalates_and_is_idempotent(engagement_client, sessionmaker):
    await seed(sessionmaker)
    client, provider = engagement_client
    await login(client, "alice")

    r = await client.post("/v1/engagement/sync")
    assert r.status_code == 200, r.text
    assert r.json() == {"fetched": 3, "stored": 3, "escalated": 1, "accounts": 1}

    inbox = (await client.get("/v1/engagement/inbox")).json()
    by_class = {m["message_class"] for m in inbox}
    assert {"question", "compliment", "safety_relevant"} == by_class
    assert all(m["disposition"] == "pending" for m in inbox)

    # The safety-relevant message opened a critical ActionItem for a human.
    items = (await client.get("/v1/action-items")).json()
    escalations = [i for i in items if i["kind"] == "engagement_escalation"]
    assert len(escalations) == 1 and escalations[0]["severity"] == "critical"

    # Second sync fetches the same messages and stores none — safe to run twice.
    r = await client.post("/v1/engagement/sync")
    assert r.json()["stored"] == 0 and provider.calls == 2
    assert len((await client.get("/v1/engagement/inbox")).json()) == 3


async def test_ledger_answered_and_skip_requires_reason(engagement_client, sessionmaker):
    await seed(sessionmaker)
    client, _ = engagement_client
    await login(client, "alice")
    await client.post("/v1/engagement/sync")
    inbox = (await client.get("/v1/engagement/inbox")).json()
    question = next(m for m in inbox if m["message_class"] == "question")
    spammy = next(m for m in inbox if m["message_class"] == "compliment")

    r = await client.post(
        f"/v1/engagement/inbox/{question['id']}/disposition", json={"disposition": "answered"}
    )
    assert r.status_code == 200 and r.json()["disposition"] == "answered"

    # Silent skipping is refused; a reason makes it a deliberate decision.
    r = await client.post(
        f"/v1/engagement/inbox/{spammy['id']}/disposition", json={"disposition": "skipped"}
    )
    assert r.status_code == 422
    r = await client.post(
        f"/v1/engagement/inbox/{spammy['id']}/disposition",
        json={"disposition": "skipped", "reason": "heart-reacted on platform instead"},
    )
    assert r.status_code == 200 and r.json()["skip_reason"].startswith("heart-reacted")

    pending = (await client.get("/v1/engagement/inbox?disposition=pending")).json()
    assert len(pending) == 1  # only the safety escalation is left


async def test_sync_is_refused_when_engagement_disabled(client, sessionmaker):
    """The default app (engagement.enabled=False) refuses sync but still lists the inbox."""
    await seed(sessionmaker)
    await login(client, "alice")
    assert (await client.get("/v1/engagement/inbox")).json() == []
    r = await client.post("/v1/engagement/sync")
    assert r.status_code == 503 and "disabled" in r.json()["detail"]
