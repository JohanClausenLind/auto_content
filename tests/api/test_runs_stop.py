"""POST /v1/runs/{id}/stop: the product's stop button."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from content_factory.api.app import create_app
from content_factory.config import load_settings
from content_factory.db.models import AuditEvent, ProductionRun, RunState
from content_factory.services import accounts as svc

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"


@pytest.fixture
async def client(sessionmaker):
    app = create_app(load_settings())
    app.state.sessionmaker = sessionmaker
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3000") as c:
        yield c


async def seed(sessionmaker) -> str:
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        workspace = await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        db.add(
            ProductionRun(
                id="run-stopme000001",
                workspace_id=workspace.id,
                campaign_id="cmp_stop00000001",
                project_id="prj_stop00000001",
                state=RunState.producing,
                quality="demo",
            )
        )
        await db.commit()
        return workspace.id


async def test_stopping_needs_a_session_and_a_run_that_exists(client, sessionmaker):
    assert (await client.post("/v1/runs/run-stopme000001/stop", json={})).status_code == 401
    await seed(sessionmaker)
    await client.post("/v1/session", json={"username": "owner", "password": PW})
    missing = await client.post("/v1/runs/run-nosuchrun0001/stop", json={})
    assert missing.status_code == 404


async def test_a_run_the_engine_no_longer_has_is_reported_not_raised(client, sessionmaker):
    """The common case for an operator: the run already ended, or the worker is gone."""
    await seed(sessionmaker)
    await client.post("/v1/session", json={"username": "owner", "password": PW})
    response = await client.post(
        "/v1/runs/run-stopme000001/stop", json={"reason": "changed my mind"}
    )
    if response.status_code == 503:
        pytest.skip(f"temporal unavailable: {response.json()['detail']}")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "run_id": "run-stopme000001",
        "outcome": "not running",
        "detail": "no open workflow",
    }
    async with sessionmaker() as db:
        events = (await db.execute(select(AuditEvent))).scalars().all()
    stops = [e for e in events if e.action == "run.stop"]
    assert len(stops) == 1 and stops[0].target_id == "run-stopme000001"
    assert stops[0].detail["reason"] == "changed my mind"


async def test_the_run_view_carries_the_estimate_over_the_wire(client, sessionmaker, monkeypatch):
    """GET /v1/runs/{id} is what the canvas polls every two seconds."""
    from datetime import datetime

    from content_factory.db.models import NodeState, RunNode
    from content_factory.services import durations

    workspace_id = await seed(sessionmaker)
    async with sessionmaker() as db:
        db.add_all(
            [
                RunNode(
                    id="rn_eta00000000001",
                    run_id="run-stopme000001",
                    workspace_id=workspace_id,
                    node_id="plan_story",
                    stage="plan_story",
                    state=NodeState.complete,
                    duration_ms=400,
                ),
                RunNode(
                    id="rn_eta00000000002",
                    run_id="run-stopme000001",
                    workspace_id=workspace_id,
                    node_id="generate_anchor",
                    stage="generate_anchor",
                    state=NodeState.queued,
                ),
            ]
        )
        await db.commit()

    # A fixed history, so the assertion is about the route rather than about this machine's runs.
    monkeypatch.setattr(
        durations,
        "estimate_stage",
        lambda stage, workflow=None, **_: (
            durations.Estimate(120.0, 7) if stage == "generate_anchor" else None
        ),
    )
    await client.post("/v1/session", json={"username": "owner", "password": PW})
    view = (await client.get("/v1/runs/run-stopme000001")).json()

    eta = view["eta"]
    assert eta["remaining_seconds"] == 120.0
    assert eta["samples"] == 7 and eta["confident"] is True and eta["overdue"] is False
    assert datetime.fromisoformat(eta["finish_at"]).tzinfo is not None  # aware, for the browser
    by_node = {n["node_id"]: n for n in view["nodes"]}
    assert by_node["generate_anchor"]["eta_seconds"] == 120.0
    assert by_node["generate_anchor"]["eta_samples"] == 7
    # The finished node keeps its measurement and is given no estimate to compete with it.
    assert by_node["plan_story"]["duration_ms"] == 400
    assert by_node["plan_story"]["eta_seconds"] is None
