"""/v1/run-history: the day's actual work, made reachable from the product that made it.

Every film in `videos/` came from a local run that writes no `production_runs` row, so the web
app's run list could not see any of it. These tests pin the two things that make the new route
safe and useful: it finds runs and classifies them honestly (a review gate is not a failure), and
it serves files from **inside one run directory and nowhere else**.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from content_factory.api.app import create_app
from content_factory.config import load_settings
from content_factory.services import accounts as svc
from content_factory.services import run_history as history

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"

PNG = (
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6300010000050001"
    )
    + b"\x0d\x0a\x2d\xb4\x00\x00\x00\x00IEND\xaeB\x60\x82"
)


def write_run(
    root: Path,
    name: str,
    *,
    stages: list[tuple[str, bool]],
    passed: bool,
    workflow: str | None = "image-set",
) -> Path:
    """A run directory shaped the way the local runner leaves one."""
    run_dir = root / name
    deliverable = run_dir / "deliverables" / "dlv_short0000001"
    (deliverable / "anchors").mkdir(parents=True, exist_ok=True)
    (deliverable / "anchors" / "anchor.png").write_bytes(PNG)
    (deliverable / "controls").mkdir(parents=True, exist_ok=True)
    (deliverable / "controls" / "pose.png").write_bytes(PNG)
    report: dict = {
        "project_dir": str(run_dir),
        "deliverable_id": "dlv_short0000001",
        "stages": [{"stage": s, "ok": ok, "seconds": 12.0} for s, ok in stages],
        "passed": passed,
    }
    if workflow:
        report["workflow"] = workflow
    (deliverable / "run.json").write_text(json.dumps(report))
    return run_dir


@pytest.fixture
def output_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "output"
    root.mkdir()
    monkeypatch.setattr(history, "history_root", lambda: root)
    return root


@pytest.fixture
async def client(sessionmaker):
    app = create_app(load_settings())
    app.state.sessionmaker = sessionmaker
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3000") as c:
        yield c


async def sign_in(client, sessionmaker) -> None:
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        await db.commit()
    await client.post("/v1/session", json={"username": "owner", "password": PW})


async def test_history_needs_a_session(client, output_root):
    assert (await client.get("/v1/run-history")).status_code == 401


async def test_it_lists_local_runs_and_calls_a_review_gate_what_it_is(
    client, sessionmaker, output_root
):
    """A run parked at `review_frames` has its drawings finished and is waiting for somebody to
    look. 27 real runs on this machine read as "failed" before this distinction existed."""
    write_run(output_root, "done", stages=[("generate_anchor", True)], passed=True)
    write_run(
        output_root,
        "waiting",
        stages=[("generate_anchor", True), ("review_frames", False)],
        passed=False,
    )
    write_run(output_root, "broke", stages=[("generate_anchor", False)], passed=False)
    await sign_in(client, sessionmaker)

    rows = (await client.get("/v1/run-history")).json()
    by_id = {r["run_id"]: r for r in rows}
    assert by_id["done"]["outcome"] == "complete"
    assert by_id["waiting"]["outcome"] == "review"
    assert by_id["broke"]["outcome"] == "failed"
    # The cost of each run travels with it: this is the evidence every ETA is a median of.
    assert by_id["done"]["seconds"] == 12.0
    assert by_id["done"]["workflow"] == "image-set"
    # The list is deliberately without outputs — scanning every run's files to draw a list means
    # walking the whole output tree.
    assert "outputs" not in by_id["done"]


async def test_a_run_detail_carries_its_outputs_with_debug_output_ranked_last(
    client, sessionmaker, output_root
):
    write_run(output_root, "shown", stages=[("generate_anchor", True)], passed=True)
    await sign_in(client, sessionmaker)

    run = (await client.get("/v1/run-history/shown")).json()
    paths = [o["path"] for o in run["outputs"]]
    assert "deliverables/dlv_short0000001/anchors/anchor.png" in paths
    # A control map is kept and labelled — it is what you want when a drawing came out wrong —
    # but it is never the image chosen to represent the run.
    control = next(o for o in run["outputs"] if o["path"].endswith("controls/pose.png"))
    assert control["role"] == "control"
    assert run["poster"] == "deliverables/dlv_short0000001/anchors/anchor.png"
    assert paths.index(run["poster"]) < paths.index(control["path"])
    assert run["outputs_total"] == len(run["outputs"])


async def test_it_serves_a_file_from_the_run_and_refuses_everything_outside_it(
    client, sessionmaker, output_root, tmp_path
):
    """The half that must not be wrong. Every refusal is a 404, never a 403: distinguishing
    "exists but forbidden" from "does not exist" hands the caller a filesystem oracle."""
    write_run(output_root, "served", stages=[("generate_anchor", True)], passed=True)
    (tmp_path / "secret.png").write_bytes(PNG)
    (output_root / "served" / "escape.png").symlink_to(tmp_path / "secret.png")
    await sign_in(client, sessionmaker)

    ok = await client.get(
        "/v1/run-history/served/files/deliverables/dlv_short0000001/anchors/anchor.png"
    )
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/png"
    assert "immutable" in ok.headers.get("cache-control", "")

    for path in (
        "../../../etc/passwd",  # traversal out of the run
        "..%2F..%2Fetc%2Fpasswd",
        "escape.png",  # a symlink pointing outside the output tree
        "deliverables/dlv_short0000001/run.json/../../../../etc/hosts",
    ):
        refused = await client.get(f"/v1/run-history/served/files/{path}")
        assert refused.status_code == 404, path

    # An id naming somewhere else on disk is not a run.
    for run_id in ("..", "~etc", ".ssh", "does-not-exist"):
        assert (await client.get(f"/v1/run-history/{run_id}")).status_code == 404, run_id


async def test_an_extension_that_is_not_on_the_allowlist_is_not_served(
    client, sessionmaker, output_root
):
    """An allowlist, because "everything except the dangerous ones" is a list nobody finishes."""
    write_run(output_root, "mixed", stages=[("generate_anchor", True)], passed=True)
    (output_root / "mixed" / "notes.sh").write_text("#!/bin/sh\necho no\n")
    (output_root / "mixed" / "weights.safetensors").write_bytes(b"\x00" * 8)
    await sign_in(client, sessionmaker)

    assert (await client.get("/v1/run-history/mixed/files/notes.sh")).status_code == 404
    assert (await client.get("/v1/run-history/mixed/files/weights.safetensors")).status_code == 404
    run = (await client.get("/v1/run-history/mixed")).json()
    assert not [o for o in run["outputs"] if o["path"].endswith((".sh", ".safetensors"))]
