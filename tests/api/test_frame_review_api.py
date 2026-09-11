"""/v1/run-history/{run_id}/review: answering the frame gate from the browser.

``review_frames`` is where this machine's work actually stops — 25 runs are parked at it right
now, with their drawings finished. Answering meant the CLI and a path on disk, so the pictures
that most needed a person were the hardest to reach.

These tests pin the two halves that make the browser a real reviewer rather than a viewer: the
gate arrives with every image it is asking about, and the rules that govern a verdict are the same
ones ``content-factory frames review`` enforces — an agent still cannot accept a batch it never
opened, whichever surface it reaches for.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from content_factory.api.app import create_app
from content_factory.config import load_settings
from content_factory.db.models import Role
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.review import FrameRecord, FrameReviewBatch
from content_factory.services import accounts as svc
from content_factory.services import run_history as history

pytestmark = pytest.mark.integration
PW = "correct horse battery staple"
DELIVERABLE = "dlv_short0000001"


def _png(shade: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (48, 32), (shade, shade, shade)).save(buf, "PNG")
    return buf.getvalue()


def write_gated_run(root: Path, name: str, *, frames: int = 3) -> Path:
    """A run left exactly as ``review_frames`` leaves one: drawings on disk, batch, no verdict."""
    run_dir = root / name
    deliverable = run_dir / "deliverables" / DELIVERABLE
    frames_dir = deliverable / "sequence" / "frames"
    frames_dir.mkdir(parents=True)
    records = []
    for index in range(frames):
        png = _png(40 + index * 40)
        (frames_dir / f"{index:04d}.png").write_bytes(png)
        records.append(FrameRecord(frame_id=f"frame:{index:04d}", png_sha256=sha256_hex(png)))
    sheet = deliverable / "reviews" / "frames" / "contact-sheet.png"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(_png(200))
    batch = FrameReviewBatch(
        deliverable_id=DELIVERABLE,
        contact_sheet_sha256=sha256_hex(sheet.read_bytes()),
        contact_sheet_path="reviews/frames/contact-sheet.png",
        frames=tuple(records),
        created_at=dt.datetime.now(dt.UTC),
    )
    (sheet.parent / "batch.json").write_text(batch.model_dump_json(indent=1))
    (deliverable / "run.json").write_text(
        json.dumps(
            {
                "project_dir": str(run_dir),
                "deliverable_id": DELIVERABLE,
                "workflow": "image-set",
                "stages": [
                    {"stage": "generate_keyframes", "ok": True, "seconds": 60.0},
                    {"stage": "review_frames", "ok": False, "seconds": 1.0},
                ],
                "passed": False,
            }
        )
    )
    return run_dir


def verdict_of(run_dir: Path) -> FrameReviewBatch | None:
    path = run_dir / "deliverables" / DELIVERABLE / "reviews" / "frames" / "verdict.json"
    return FrameReviewBatch.model_validate_json(path.read_text()) if path.exists() else None


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


async def sign_in(client, sessionmaker, *, role: Role | None = None) -> None:
    """Sign in as the owner, or as a member holding exactly ``role``."""
    async with sessionmaker() as db:
        owner = await svc.create_account(
            db, username="owner", display_name="Owner", password=PW, is_owner=True
        )
        ws = await svc.create_workspace(db, slug="acme", name="Acme", owner=owner)
        if role is not None:
            member = await svc.create_account(
                db, username="member", display_name="Member", password=PW
            )
            await svc.add_member(db, workspace=ws, account=member, role=role, actor=owner)
        await db.commit()
    who = "member" if role is not None else "owner"
    await client.post("/v1/session", json={"username": who, "password": PW})


async def test_the_gate_arrives_with_every_drawing_it_is_asking_about(
    client, sessionmaker, output_root
):
    """A review panel that lists filenames is a worse contact sheet. Each frame resolves to a
    file the same route serves, so the reviewer looks at the picture, not at its digest."""
    write_gated_run(output_root, "waiting")
    await sign_in(client, sessionmaker)

    body = (await client.get("/v1/run-history/waiting/review")).json()
    assert len(body["reviews"]) == 1
    review = body["reviews"][0]
    assert review["unreviewed"] == 3
    assert review["passed"] is False
    assert review["contact_sheet"].endswith("reviews/frames/contact-sheet.png")
    # A verdict unblocks the gate and makes nothing: the command that continues the run travels
    # with the question, or the reviewer waits for a film nothing is producing.
    assert "--from review_frames" in body["resume_command"]

    for frame in review["frames"]:
        assert frame["verdict"] == "unreviewed"
        assert frame["on_disk"] is True  # what is on disk is what the verdict will bind to
        served = await client.get(f"/v1/run-history/waiting/files/{frame['image']}")
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"

    # The list says which runs want a person, without opening any of them.
    row = next(r for r in (await client.get("/v1/run-history")).json() if r["run_id"] == "waiting")
    assert row["awaiting_review"] == 3
    assert row["outcome"] == "review"


async def test_a_person_accepts_the_batch_and_the_gate_reads_it_back(
    client, sessionmaker, output_root
):
    run_dir = write_gated_run(output_root, "accepted")
    await sign_in(client, sessionmaker)

    posted = await client.post(
        "/v1/run-history/accepted/review",
        json={"accept_rest": True, "note": "looked at the sheet; all three are the same pinecone"},
    )
    assert posted.status_code == 200
    review = posted.json()["reviews"][0]
    assert review["passed"] is True
    assert review["accepted"] == 3
    assert review["reviewer"] == "operator"

    written = verdict_of(run_dir)
    assert written is not None
    assert written.passed  # the gate's own contract, read back off disk
    assert written.notes.startswith("looked at the sheet")
    row = next(r for r in (await client.get("/v1/run-history")).json() if r["run_id"] == "accepted")
    assert row["awaiting_review"] == 0


async def test_one_bad_drawing_costs_one_drawing(client, sessionmaker, output_root):
    """A reviewer rejects frames, not batches: one wrong picture out of thirty should cost one."""
    run_dir = write_gated_run(output_root, "partly")
    await sign_in(client, sessionmaker)

    posted = await client.post(
        "/v1/run-history/partly/review",
        json={
            "accept": ["frame:0000", "frame:0002"],
            "reject": ["frame:0001"],
            "reason": "two figures where one was staged",
        },
    )
    assert posted.status_code == 200
    review = posted.json()["reviews"][0]
    assert review["accepted"] == 2
    assert review["rejected"] == 1
    assert review["passed"] is False
    rejected = next(f for f in review["frames"] if f["frame_id"] == "frame:0001")
    assert rejected["reason"] == "two figures where one was staged"

    written = verdict_of(run_dir)
    assert written is not None
    assert [f.verdict for f in written.frames] == ["accept", "reject", "accept"]


async def test_an_agent_cannot_accept_a_batch_it_did_not_open_from_here_either(
    client, sessionmaker, output_root
):
    """The rule that made the browser panel safe to add: it is enforced in one place
    (`qc.verdict`) and both surfaces ask that place, so a second door does not open a wider one."""
    run_dir = write_gated_run(output_root, "agentrun")
    await sign_in(client, sessionmaker)

    blanket = await client.post(
        "/v1/run-history/agentrun/review", json={"reviewer": "agent", "accept_rest": True}
    )
    assert blanket.status_code == 422
    assert blanket.json()["detail"]["kind"] == "agent_blanket"

    partial = await client.post(
        "/v1/run-history/agentrun/review",
        json={"reviewer": "agent", "accept": ["frame:0000"]},
    )
    assert partial.status_code == 422
    detail = partial.json()["detail"]
    assert detail["kind"] == "undecided"
    assert detail["frames"] == ["frame:0001", "frame:0002"]  # which ones, not "some of them"
    assert verdict_of(run_dir) is None  # and nothing was written by either attempt

    named = await client.post(
        "/v1/run-history/agentrun/review",
        json={
            "reviewer": "agent",
            "accept": ["frame:0000", "frame:0002"],
            "reject": ["frame:0001"],
            "reason": "the middle one is a different object",
        },
    )
    assert named.status_code == 200


async def test_a_verdict_the_contract_cannot_read_back_is_refused_before_it_is_written(
    client, sessionmaker, output_root
):
    run_dir = write_gated_run(output_root, "toolong")
    await sign_in(client, sessionmaker)

    unknown = await client.post("/v1/run-history/toolong/review", json={"accept": ["frame:0009"]})
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["kind"] == "unknown_frames"

    conflicting = await client.post(
        "/v1/run-history/toolong/review",
        json={"accept": ["frame:0000"], "reject": ["frame:0000"]},
    )
    assert conflicting.status_code == 422
    assert conflicting.json()["detail"]["kind"] == "conflicting"

    # Over `reason`'s 400 characters: refused by the body's own contract before it reaches disk.
    too_long = await client.post(
        "/v1/run-history/toolong/review",
        json={"reject": ["frame:0000"], "reason": "x" * 401},
    )
    assert too_long.status_code == 422
    assert verdict_of(run_dir) is None


async def test_recording_a_verdict_needs_the_reviewer_role_and_a_run_inside_the_output_root(
    client, sessionmaker, output_root
):
    run_dir = write_gated_run(output_root, "guarded")
    await sign_in(client, sessionmaker, role=Role.viewer)

    reading = await client.get("/v1/run-history/guarded/review")
    assert reading.status_code == 200  # a viewer may look

    refused = await client.post("/v1/run-history/guarded/review", json={"accept_rest": True})
    assert refused.status_code == 403
    assert verdict_of(run_dir) is None

    for run_id in ("..", "~etc", "does-not-exist"):
        assert (await client.get(f"/v1/run-history/{run_id}/review")).status_code == 404
