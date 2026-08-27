"""Phase-1 gate: auth flows work; two seeded workspaces cannot see each other's rows."""

from __future__ import annotations

import time

import pyotp
import pytest

from content_factory.db.models import Role
from content_factory.services import accounts as svc
from tests.helpers.soft_authenticator import SoftAuthenticator

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"


async def seed(sessionmaker):
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        alice = await svc.create_account(db, username="alice", display_name="Alice", password=PW)
        bob = await svc.create_account(db, username="bob", display_name="Bob", password=PW)
        viewer = await svc.create_account(db, username="viewer", display_name="Viewer", password=PW)
        ws_a = await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        ws_b = await svc.create_workspace(db, slug="globex", name="Globex", owner=owner)
        await svc.add_member(db, workspace=ws_a, account=alice, role=Role.editor, actor=owner)
        await svc.add_member(db, workspace=ws_b, account=bob, role=Role.editor, actor=owner)
        await svc.add_member(db, workspace=ws_a, account=viewer, role=Role.viewer, actor=owner)
        await db.commit()
        return {"ws_a": ws_a.id, "ws_b": ws_b.id}


async def login(c, username: str):
    r = await c.post("/v1/session", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()


async def test_password_login_logout_and_unauthenticated_access(client, sessionmaker) -> None:
    await seed(sessionmaker)
    assert (await client.get("/v1/session")).status_code == 401
    r = await client.post("/v1/session", json={"username": "alice", "password": "wrong password!!"})
    assert r.status_code == 401
    view = await login(client, "alice")
    assert view["account"]["username"] == "alice"
    assert [w["slug"] for w in view["workspaces"]] == ["acme"]
    assert view["current_workspace_id"] == view["workspaces"][0]["id"]
    assert "cf_session" in client.cookies
    assert (await client.get("/v1/session")).status_code == 200
    assert (await client.delete("/v1/session")).status_code == 204
    assert (await client.get("/v1/session")).status_code == 401


async def test_two_workspaces_cannot_see_each_others_rows(make_client, sessionmaker) -> None:
    ids = await seed(sessionmaker)
    async with make_client() as a, make_client() as b:
        await login(a, "alice")
        await login(b, "bob")
        ka = await a.post("/v1/brand-kits", json={"slug": "acme-main", "name": "Acme main"})
        kb = await b.post("/v1/brand-kits", json={"slug": "globex-main", "name": "Globex main"})
        assert ka.status_code == 201 and kb.status_code == 201
        assert ka.json()["workspace_id"] == ids["ws_a"] and kb.json()["workspace_id"] == ids["ws_b"]
        assert [k["slug"] for k in (await a.get("/v1/brand-kits")).json()] == ["acme-main"]
        assert [k["slug"] for k in (await b.get("/v1/brand-kits")).json()] == ["globex-main"]
        # Direct id access across workspaces is a 404 (no existence oracle), not a 403.
        assert (await a.get(f"/v1/brand-kits/{kb.json()['id']}")).status_code == 404
        assert (await b.get(f"/v1/brand-kits/{ka.json()['id']}")).status_code == 404
        # Switching to a workspace you are not a member of is refused.
        assert (
            await a.post("/v1/session/workspace", json={"workspace_id": ids["ws_b"]})
        ).status_code == 403
        assert (await a.get("/v1/workspaces")).json()[0]["id"] == ids["ws_a"]


async def test_rbac_viewer_cannot_create_and_owner_sees_all(make_client, sessionmaker) -> None:
    ids = await seed(sessionmaker)
    async with make_client() as v, make_client() as o:
        await login(v, "viewer")
        assert (await v.get("/v1/brand-kits")).status_code == 200
        assert (
            await v.post("/v1/brand-kits", json={"slug": "nope", "name": "Nope"})
        ).status_code == 403
        view = await login(o, "owner")
        assert {w["slug"] for w in view["workspaces"]} == {"acme", "globex"}
        assert (
            await o.post("/v1/session/workspace", json={"workspace_id": ids["ws_b"]})
        ).status_code == 200
        assert (
            await o.post("/v1/brand-kits", json={"slug": "g2", "name": "G2"})
        ).status_code == 201


async def test_preferences_roundtrip_per_account(make_client, sessionmaker) -> None:
    await seed(sessionmaker)
    async with make_client() as a, make_client() as b:
        await login(a, "alice")
        await login(b, "bob")
        assert (await a.get("/v1/prefs/theme")).json() == {"value": None}
        assert (
            await a.put("/v1/prefs/theme", json={"value": {"preset": "midnight", "scale": 1.1}})
        ).status_code == 204
        assert (await a.get("/v1/prefs/theme")).json()["value"]["preset"] == "midnight"
        assert (await b.get("/v1/prefs/theme")).json() == {"value": None}
        assert (await a.put("/v1/prefs/bad key!", json={"value": 1})).status_code == 400


async def test_totp_mfa_flow_and_step_up(make_client, sessionmaker) -> None:
    await seed(sessionmaker)
    async with sessionmaker() as db:
        alice = await svc.get_account_by_username(db, "alice")
        assert alice is not None
        secret, uri = await svc.enroll_totp_begin(alice)
        assert uri.startswith("otpauth://totp/")
        codes = await svc.enroll_totp_confirm(db, alice, secret, pyotp.TOTP(secret).now())
        assert len(codes) == 10
        await db.commit()
    async with make_client() as c:
        r = await c.post("/v1/session", json={"username": "alice", "password": PW})
        assert r.status_code == 202 and r.json() == {"mfa_required": True, "methods": ["totp"]}
        assert (await c.get("/v1/session")).status_code == 401  # pending MFA is not signed in
        assert (await c.post("/v1/session/totp", json={"code": "000000"})).status_code == 401
        # Enrolment consumed the current step; use the next step (verify accepts ±1 window).
        code = pyotp.TOTP(secret).at(int(time.time()) + 30)
        ok = await c.post("/v1/session/totp", json={"code": code})
        assert ok.status_code == 200 and ok.json()["account"]["username"] == "alice"
        # Replaying the same TOTP code in a fresh login is refused.
        async with make_client() as c2:
            await c2.post("/v1/session", json={"username": "alice", "password": PW})
            assert (await c2.post("/v1/session/totp", json={"code": code})).status_code == 401
        # Step-up with a wrong password fails; correct password extends step_up_until.
        assert (
            await c.post("/v1/session/step-up", json={"password": "nope nope nope"})
        ).status_code == 401
        assert (await c.post("/v1/session/step-up", json={"password": PW})).status_code == 200


async def test_passkey_registration_then_passwordless_login(make_client, sessionmaker) -> None:
    await seed(sessionmaker)
    authn = SoftAuthenticator(rp_id="localhost", origin="http://localhost:3000")
    async with make_client() as c:
        await login(c, "alice")
        opts = await c.post("/v1/session/passkey/register/options")
        assert opts.status_code == 200
        cred = authn.register(opts.json()["options"])
        reg = await c.post(
            "/v1/session/passkey/register/verify",
            json={
                "challenge_id": opts.json()["challenge_id"],
                "credential": cred,
                "label": "laptop",
            },
        )
        assert reg.status_code == 201, reg.text
        # A challenge is single-use.
        again = await c.post(
            "/v1/session/passkey/register/verify",
            json={"challenge_id": opts.json()["challenge_id"], "credential": cred},
        )
        assert again.status_code == 400
    async with make_client() as fresh:
        opts = await fresh.post("/v1/session/passkey/options")
        assert opts.status_code == 200
        assertion = authn.authenticate(opts.json()["options"])
        r = await fresh.post(
            "/v1/session/passkey/verify",
            json={"challenge_id": opts.json()["challenge_id"], "credential": assertion},
        )
        assert r.status_code == 200, r.text
        assert r.json()["account"]["username"] == "alice"
        assert (await fresh.get("/v1/session")).status_code == 200
        # Replayed assertion against a new challenge fails.
        opts2 = await fresh.post("/v1/session/passkey/options")
        r2 = await fresh.post(
            "/v1/session/passkey/verify",
            json={"challenge_id": opts2.json()["challenge_id"], "credential": assertion},
        )
        assert r2.status_code == 401
    # Password login now requires MFA because a passkey exists.
    async with make_client() as c3:
        r = await c3.post("/v1/session", json={"username": "alice", "password": PW})
        assert r.status_code == 202 and r.json()["methods"] == ["passkey"]
