"""Phase-12 persistence: personas (revision-bound apply), brand hierarchy locks, portal briefs."""

from __future__ import annotations

import pytest

from content_factory.db.models import Role
from content_factory.services import accounts as svc

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"
SECRET = "portal-secret-for-tests-0123456789"


async def seed(sessionmaker):
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        alice = await svc.create_account(db, username="alice", display_name="Alice", password=PW)
        bob = await svc.create_account(db, username="bob", display_name="Bob", password=PW)
        ws_a = await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        ws_b = await svc.create_workspace(db, slug="globex", name="Globex", owner=owner)
        await svc.add_member(db, workspace=ws_a, account=alice, role=Role.editor, actor=owner)
        await svc.add_member(db, workspace=ws_b, account=bob, role=Role.editor, actor=owner)
        await db.commit()
        return {"ws_a": ws_a.id, "ws_b": ws_b.id}


async def login(c, username: str):
    r = await c.post("/v1/session", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()


PERSONA_DOC = {
    "identity": {"display_name": "Nova", "presented_age": 24},
    "voice": {"tone": ["warm", "formal"], "pet_names": ["hon"]},
    "backstory": {"bio": "Loves synthwave and houseplants."},
}


async def test_persona_lifecycle_revise_apply_and_stale_diff(client, sessionmaker) -> None:
    await seed(sessionmaker)
    await login(client, "alice")

    # Adults only is enforced in the type, not reviewable config.
    minor = {**PERSONA_DOC, "identity": {"display_name": "X", "presented_age": 17}}
    assert (await client.post("/v1/personas", json=minor)).status_code == 422

    r = await client.post("/v1/personas", json=PERSONA_DOC)
    assert r.status_code == 201, r.text
    created = r.json()
    pid = created["id"]
    assert created["revision"] == 1 and created["document"]["persona_id"] == pid

    # Revise is a preview: returns a typed diff, mutates nothing.
    r = await client.post(f"/v1/personas/{pid}/revise", json={"feedback": "too formal please"})
    assert r.status_code == 200, r.text
    diff = r.json()
    assert diff["base_revision"] == 1 and diff["changes"][0]["path"] == "voice.tone"
    assert (await client.get(f"/v1/personas/{pid}")).json()["revision"] == 1

    # Unmappable feedback is a 422 with guidance, not a silent no-op.
    r = await client.post(f"/v1/personas/{pid}/revise", json={"feedback": "be more mauve"})
    assert r.status_code == 422

    r = await client.post(f"/v1/personas/{pid}/apply", json=diff)
    assert r.status_code == 200, r.text
    applied = r.json()
    assert applied["revision"] == 2
    assert "formal" not in applied["document"]["voice"]["tone"]

    # The same diff again is stale (base_revision 1 vs persona at 2) → conflict, re-review.
    assert (await client.post(f"/v1/personas/{pid}/apply", json=diff)).status_code == 409


async def test_persona_workspace_isolation(make_client, sessionmaker) -> None:
    await seed(sessionmaker)
    async with make_client() as a, make_client() as b:
        await login(a, "alice")
        await login(b, "bob")
        pid = (await a.post("/v1/personas", json=PERSONA_DOC)).json()["id"]
        assert (await b.get(f"/v1/personas/{pid}")).status_code == 404
        assert (await b.get("/v1/personas")).json() == []


async def test_brand_hierarchy_locks_and_effective_merge(client, sessionmaker) -> None:
    await seed(sessionmaker)
    await login(client, "alice")
    root = await client.post(
        "/v1/brand-nodes",
        json={
            "name": "Parent Brand",
            "tokens": {"color.accent": "#0044cc", "font.body": "Inter"},
            "locked_tokens": ["color.accent"],
            "policies": {"disclosure": "always"},
            "locked_policies": ["disclosure"],
        },
    )
    assert root.status_code == 201, root.text
    root_id = root.json()["id"]

    # A child cannot even declare an override for a locked key.
    r = await client.post(
        "/v1/brand-nodes",
        json={"name": "Stockholm", "parent_id": root_id, "tokens": {"color.accent": "#ff0000"}},
    )
    assert r.status_code == 409 and "color.accent" in r.json()["detail"]

    child = await client.post(
        "/v1/brand-nodes",
        json={"name": "Stockholm", "parent_id": root_id, "tokens": {"font.body": "Sora"}},
    )
    assert child.status_code == 201, child.text
    eff = (await client.get(f"/v1/brand-nodes/{child.json()['id']}/effective")).json()
    assert eff["tokens"] == {"color.accent": "#0044cc", "font.body": "Sora"}
    assert eff["policies"] == {"disclosure": "always"}
    # Unknown parent and unknown node are refused.
    r = await client.post("/v1/brand-nodes", json={"name": "Orphan", "parent_id": "brand_missing"})
    assert r.status_code == 409
    assert (await client.get("/v1/brand-nodes/brand_missing00/effective")).status_code == 404


async def test_portal_link_public_submit_and_decision(make_client, sessionmaker, monkeypatch):
    await seed(sessionmaker)
    monkeypatch.setenv("CF_PORTAL_SECRET", SECRET)
    async with make_client() as editor, make_client() as public:
        await login(editor, "alice")
        r = await editor.post("/v1/portal-links", json={"label": "Q3 client", "ttl_days": 7})
        assert r.status_code == 201, r.text
        link = r.json()
        assert link["token"] and "token=" in link["submit_url"]
        # Listing never shows the token again.
        listed = (await editor.get("/v1/portal-links")).json()
        assert listed[0]["label"] == "Q3 client" and "token" not in listed[0]

        # The public client has NO session cookie.
        good = {
            "token": link["token"],
            "topic": "Launch video for the autumn line",
            "objective": "45s teaser for socials",
            "contact": "client@example.com",
        }
        r = await public.post("/portal/briefs", json=good)
        assert r.status_code == 201, r.text
        brief_id = r.json()["id"]

        # Invalid submissions and tampered tokens are refused.
        assert (
            await public.post("/portal/briefs", json={**good, "topic": "ab"})
        ).status_code == 403
        assert (
            await public.post("/portal/briefs", json={**good, "token": link["token"][:-2] + "xx"})
        ).status_code == 403

        # The brief shows up for the workspace with an ActionItem; the operator decides.
        briefs = (await editor.get("/v1/portal-briefs")).json()
        assert [b["id"] for b in briefs] == [brief_id] and briefs[0]["status"] == "new"
        items = (await editor.get("/v1/action-items")).json()
        assert any(i["kind"] == "portal_brief" for i in items)
        r = await editor.post(
            f"/v1/portal-briefs/{brief_id}/decision", json={"decision": "accepted"}
        )
        assert r.status_code == 200 and r.json()["status"] == "accepted"

        # Revocation kills the link immediately.
        assert (await editor.delete(f"/v1/portal-links/{link['id']}")).status_code == 204
        assert (await public.post("/portal/briefs", json=good)).status_code == 403


async def test_portal_secret_unset_is_503_not_a_default(make_client, sessionmaker, monkeypatch):
    await seed(sessionmaker)
    monkeypatch.delenv("CF_PORTAL_SECRET", raising=False)
    async with make_client() as editor:
        await login(editor, "alice")
        r = await editor.post("/v1/portal-links", json={"label": "x"})
        assert r.status_code == 503
