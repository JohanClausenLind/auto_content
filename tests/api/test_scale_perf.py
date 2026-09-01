"""Perf pass (API side): hundreds of runs and action items stay fast and paginated."""

from __future__ import annotations

import random
import time

import pytest

from content_factory.db.base import new_id, utcnow
from content_factory.db.models import ActionItem, NodeState, ProductionRun, Role, RunNode, RunState
from content_factory.services import accounts as svc

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"


async def test_api_lists_stay_fast_with_hundreds_of_runs(client, sessionmaker) -> None:
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        alice = await svc.create_account(db, username="alice", display_name="Alice", password=PW)
        ws = await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        await svc.add_member(db, workspace=ws, account=alice, role=Role.editor, actor=owner)
        rng = random.Random(7)
        now = utcnow()
        for i in range(400):
            run_id = f"run_scale_{i:04d}"
            db.add(
                ProductionRun(
                    id=run_id,
                    workspace_id=ws.id,
                    campaign_id=f"cmp_scale{i:08d}",
                    project_id=f"prj_scale{i:08d}",
                    state=rng.choice([RunState.complete, RunState.failed, RunState.producing]),
                    quality="demo",
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add_all(
                RunNode(
                    id=new_id("rn"),
                    workspace_id=ws.id,
                    run_id=run_id,
                    node_id=f"n{j}",
                    stage="research",
                    state=NodeState.complete,
                    created_at=now,
                    updated_at=now,
                )
                for j in range(3)
            )
        db.add_all(
            ActionItem(
                id=new_id("ai"),
                workspace_id=ws.id,
                kind="run_failed",
                severity="normal",
                title=f"Run {i} failed",
                body="",
                dedupe_key=f"scale:{i}",
                created_at=now,
                updated_at=now,
            )
            for i in range(300)
        )
        await db.commit()

    r = await client.post("/v1/session", json={"username": "alice", "password": PW})
    assert r.status_code == 200

    started = time.perf_counter()
    runs = (await client.get("/v1/runs")).json()
    runs_seconds = time.perf_counter() - started
    assert len(runs) == 50  # paginated, never the whole table
    assert runs_seconds < 1.0, f"/v1/runs took {runs_seconds:.2f}s with 400 runs / 1200 nodes"

    started = time.perf_counter()
    items = (await client.get("/v1/action-items")).json()
    items_seconds = time.perf_counter() - started
    assert len(items) >= 300
    assert items_seconds < 1.0, f"/v1/action-items took {items_seconds:.2f}s with 300 open items"
