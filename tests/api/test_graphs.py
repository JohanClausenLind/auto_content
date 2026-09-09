"""Workspace graph API: validated persistence, compile preview, gated run start."""

from __future__ import annotations

import httpx
import pytest

from content_factory.api.app import create_app
from content_factory.config import load_settings
from content_factory.services import accounts as svc

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"


async def seed(sessionmaker):
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        await db.commit()


@pytest.fixture
async def client(sessionmaker):
    app = create_app(load_settings())
    app.state.sessionmaker = sessionmaker
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3000") as c:
        yield c


def graph_doc(topic: str = "dragons") -> dict:
    return {
        "schema_version": 1,
        "graph_id": "graph_api_test_1",
        "name": "API test graph",
        "nodes": [
            {
                "id": "b",
                "type": "input.brief",
                "title": None,
                "x": 0,
                "y": 0,
                "width": None,
                "collapsed": False,
                "note": "",
                "values": {"topic": topic},
                "mode": "always",
            },
            {
                "id": "s",
                "type": "plan_story",
                "title": None,
                "x": 100,
                "y": 0,
                "width": None,
                "collapsed": False,
                "note": "",
                "values": {"beats": 5},
                "mode": "always",
            },
            {
                "id": "t",
                "type": "compile_timeline",
                "title": None,
                "x": 200,
                "y": 0,
                "width": None,
                "collapsed": False,
                "note": "",
                "values": {"fps": 30},
                "mode": "always",
            },
            {
                "id": "r",
                "type": "render_scenes",
                "title": None,
                "x": 300,
                "y": 0,
                "width": None,
                "collapsed": False,
                "note": "",
                "values": {},
                "mode": "always",
            },
            {
                "id": "v",
                "type": "compose_video",
                "title": None,
                "x": 400,
                "y": 0,
                "width": None,
                "collapsed": False,
                "note": "",
                "values": {"codec": "h264", "fps": 30},
                "mode": "always",
            },
        ],
        "links": [
            {
                "id": "l1",
                "from_node": "b",
                "from_slot": "brief",
                "to_node": "s",
                "to_slot": "brief",
            },
            {
                "id": "l2",
                "from_node": "s",
                "from_slot": "story",
                "to_node": "t",
                "to_slot": "story",
            },
            {
                "id": "l3",
                "from_node": "t",
                "from_slot": "timeline",
                "to_node": "r",
                "to_slot": "timeline",
            },
            {
                "id": "l4",
                "from_node": "r",
                "from_slot": "frames",
                "to_node": "v",
                "to_slot": "frames",
            },
        ],
    }


async def login(c):
    r = await c.post("/v1/session", json={"username": "owner", "password": PW})
    assert r.status_code == 200, r.text


async def test_crud_validates_and_scopes(client, sessionmaker):
    assert (await client.get("/v1/graphs")).status_code == 401
    await seed(sessionmaker)
    await login(client)

    doc = graph_doc()
    r = await client.put(f"/v1/graphs/{doc['graph_id']}", json=doc)
    assert r.status_code == 200, r.text
    assert r.json()["nodes"] == 5

    # unknown fields are errors, exactly like every other contract
    bad = {**doc, "surprise": True}
    assert (await client.put(f"/v1/graphs/{doc['graph_id']}", json=bad)).status_code == 422

    listing = (await client.get("/v1/graphs")).json()
    assert [g["graph_id"] for g in listing] == [doc["graph_id"]]
    fetched = (await client.get(f"/v1/graphs/{doc['graph_id']}")).json()
    # Byte-for-byte what was stored, plus the fields the contract fills in. `groups` is one: a
    # document written before folded groups existed — this one, and every graph already in a
    # browser's localStorage — comes back with an empty list rather than being refused.
    assert fetched == {**doc, "groups": []}

    assert (await client.delete(f"/v1/graphs/{doc['graph_id']}")).status_code == 204
    assert (await client.get(f"/v1/graphs/{doc['graph_id']}")).status_code == 404


async def test_a_folded_graph_round_trips_and_a_broken_group_is_refused(client, sessionmaker):
    """A group is how the canvas draws a lane, and the canvas writes its document here on every
    edit — so a document with folded groups has to survive the trip, and one whose group names a
    node that is not in it has to be refused rather than stored for the editor to choke on."""
    await seed(sessionmaker)
    await login(client)
    doc = graph_doc()
    doc["groups"] = [
        {
            "id": "gr1",
            "name": "Render and check",
            "template": "check-deliver",
            "collapsed": True,
            "members": ["r", "v"],
            "x": 600.0,
            "y": 0.0,
        }
    ]
    r = await client.put(f"/v1/graphs/{doc['graph_id']}", json=doc)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/v1/graphs/{doc['graph_id']}")).json() == doc

    # Folding changes nothing about what would run.
    body = (await client.post(f"/v1/graphs/{doc['graph_id']}/compile")).json()
    assert body["ok"] is True and body["dag_nodes"] == 5

    ghost = {**doc, "groups": [{**doc["groups"][0], "members": ["nope"]}]}
    assert (await client.put(f"/v1/graphs/{doc['graph_id']}", json=ghost)).status_code == 422
    twice = {
        **doc,
        "groups": [
            {**doc["groups"][0], "id": "gr1", "members": ["r"]},
            {**doc["groups"][0], "id": "gr2", "members": ["r"]},
        ],
    }
    assert (await client.put(f"/v1/graphs/{doc['graph_id']}", json=twice)).status_code == 422


async def test_compile_preview_reports_dispositions(client, sessionmaker):
    await seed(sessionmaker)
    await login(client)
    doc = graph_doc()
    await client.put(f"/v1/graphs/{doc['graph_id']}", json=doc)

    body = (await client.post(f"/v1/graphs/{doc['graph_id']}/compile")).json()
    assert body["ok"] is True and body["deliverable_type"] == "short_video"
    kinds = {d["node_id"]: d["kind"] for d in body["dispositions"]}
    assert kinds["b"] == "skipped" and kinds["v"] == "executes"
    assert body["dag_nodes"] == 5  # 4 drawn stages + the injected preflight gate

    # an empty topic blocks with a reason instead of starting anything
    empty = graph_doc(topic=" ")
    await client.put(f"/v1/graphs/{doc['graph_id']}", json=empty)
    body = (await client.post(f"/v1/graphs/{doc['graph_id']}/compile")).json()
    assert body["ok"] is False and any("topic is empty" in p for p in body["problems"])
    r = await client.post(f"/v1/graphs/{doc['graph_id']}/runs", json={"quality": "smoke"})
    assert r.status_code == 422


async def test_run_start_launches_the_production_workflow(client, sessionmaker):
    await seed(sessionmaker)
    await login(client)
    doc = graph_doc()
    await client.put(f"/v1/graphs/{doc['graph_id']}", json=doc)
    r = await client.post(f"/v1/graphs/{doc['graph_id']}/runs", json={"quality": "smoke"})
    if r.status_code == 503:
        pytest.skip(f"temporal unavailable: {r.json()['detail']}")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["run_id"].startswith("run-") and body["ok"] is True
    # Leave nothing parked on the dev Temporal: this test only proves the start path.
    from content_factory.services.runs import temporal_client

    handle = (await temporal_client()).get_workflow_handle(body["run_id"])
    await handle.terminate(reason="test cleanup: start path verified")
