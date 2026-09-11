"""/v1/run-history/{run_id}/ai-review: a second opinion on the pictures, from the browser.

The gate's two existing reviewers are the deterministic checks and a person, and the gap between
them is the failure this machine actually produces — measurements cannot see whether the subject
is the same subject. A vision model can, and ``ReviewerKind`` has had ``vlm`` in it since the
contract was written.

What these tests pin is not the model. It is the boundary around the model:

* the review is an **opinion** — no verdict is written, and the batch is untouched afterwards;
* it is **served with the gate**, so a panel knows whether one already exists before offering to
  spend forty seconds of GPU asking for another;
* a **stale** review — the frames were redrawn after it was formed — is labelled rather than shown
  as if it were about the picture on screen;
* "cannot ask" and "asked and it failed" are **different answers**, because one is fixed by
  waiting for a run to finish and the other by fixing the model stack.

The model itself is stubbed: a real call is 42 s of GPU and a different sentence every time, which
is a live test (`-m live`), not a contract test.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from content_factory.api.app import create_app
from content_factory.api.routes import reviews as review_routes
from content_factory.config import load_settings
from content_factory.db.models import Role
from content_factory.qc.vlm_review import VlmReviewUnavailableError, load_review
from content_factory.schemas.review import FrameOpinion, SetOpinion, SetReview
from content_factory.services import accounts as svc
from content_factory.services import run_history as history
from tests.api.test_frame_review_api import (
    DELIVERABLE,
    PW,
    verdict_of,
    write_gated_run,
)

pytestmark = pytest.mark.integration


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


def _stub(monkeypatch: pytest.MonkeyPatch, **overrides):
    """Stand in for the vision model: write the same file a real review would, and record the call.

    Writing the file matters — the route reads the review back through the same loader the panel
    and the CLI use, so a stub that only returned an object would leave that path untested.
    """
    calls: list[tuple[Path, Path]] = []

    def fake(run_dir: Path, deliverable_dir: Path, **_kwargs) -> SetReview:
        calls.append((run_dir, deliverable_dir))
        from content_factory.services import frame_reviews

        batch = frame_reviews.current_batch(deliverable_dir)
        assert batch is not None
        review = SetReview(
            deliverable_id=batch.deliverable_id,
            reviewed_at=dt.datetime.now(dt.UTC),
            model_alias="local_structured_quality",
            model_id="qwen3.6-27b-heretic:Q4_K_M",
            intent="Subject: one small iceberg",
            frames=(
                FrameOpinion(
                    frame_id="frame:0000",
                    shows="a blue-white iceberg on dark water",
                    matches_intent=True,
                ),
                FrameOpinion(
                    frame_id="frame:0001",
                    shows="a much smaller iceberg from a higher angle",
                    matches_intent=False,
                    issues=("different camera angle and scale from the others",),
                    severity="wrong",
                ),
            ),
            set=SetOpinion(
                same_world=False,
                what_changes=("the camera rises and the iceberg shrinks",),
                drifting_frames=("frame:0001",),
                summary="frame:0001 is from a different setup",
            ),
            digests={f.frame_id: f.png_sha256 for f in batch.frames},
            **overrides,
        )
        from content_factory.qc import vlm_review

        path = vlm_review.review_path(deliverable_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(review.model_dump_json(indent=1))
        return review

    monkeypatch.setattr(review_routes, "review_batch", fake)
    return calls


async def test_an_ai_review_is_an_opinion_and_never_a_verdict(
    client, sessionmaker, output_root, monkeypatch
):
    run_dir = write_gated_run(output_root, "iceberg")
    calls = _stub(monkeypatch)
    await sign_in(client, sessionmaker)

    posted = await client.post("/v1/run-history/iceberg/ai-review", json={})
    assert posted.status_code == 200
    assert len(calls) == 1

    opinion = posted.json()["ai_reviews"][DELIVERABLE]
    assert opinion["set"]["same_world"] is False
    assert opinion["set"]["drifting_frames"] == ["frame:0001"]
    # `shows` is what makes the opinion checkable: a reader compares it with the picture.
    assert opinion["frames"][0]["shows"].startswith("a blue-white iceberg")
    # What the model would not pass, offered as what to look at first.
    assert opinion["flagged"] == ["frame:0001"]
    assert opinion["current"] is True

    # Nothing was decided. The gate is exactly as `review_frames` left it.
    assert verdict_of(run_dir) is None
    gate = posted.json()["reviews"][0]
    assert gate["unreviewed"] == 3 and gate["reviewer"] is None


async def test_the_stored_opinion_travels_with_the_gate(
    client, sessionmaker, output_root, monkeypatch
):
    """A panel must know a review exists before it offers to spend the GPU asking for one."""
    write_gated_run(output_root, "iceberg")
    _stub(monkeypatch)
    await sign_in(client, sessionmaker)

    before = (await client.get("/v1/run-history/iceberg/review")).json()
    assert before["ai_reviews"] == {}

    await client.post("/v1/run-history/iceberg/ai-review", json={})
    after = (await client.get("/v1/run-history/iceberg/review")).json()
    assert after["ai_reviews"][DELIVERABLE]["model_id"] == "qwen3.6-27b-heretic:Q4_K_M"


async def test_a_redrawn_frame_makes_the_opinion_stale_rather_than_wrong(
    client, sessionmaker, output_root, monkeypatch
):
    """The same rule a human verdict follows: an opinion binds to the digests it was formed about.

    It is kept and labelled rather than deleted — "this is what was wrong last time" is worth
    reading — but it must never sit under a picture it is not about.
    """
    run_dir = write_gated_run(output_root, "iceberg")
    _stub(monkeypatch)
    await sign_in(client, sessionmaker)
    await client.post("/v1/run-history/iceberg/ai-review", json={})
    assert (await client.get("/v1/run-history/iceberg/review")).json()["ai_reviews"][DELIVERABLE][
        "current"
    ] is True

    # One frame redrawn: the batch is rewritten with a new digest, as a regen would leave it.
    batch_path = run_dir / "deliverables" / DELIVERABLE / "reviews" / "frames" / "batch.json"
    batch = json.loads(batch_path.read_text())
    batch["frames"][0]["png_sha256"] = "f" * 64
    batch_path.write_text(json.dumps(batch))

    body = (await client.get("/v1/run-history/iceberg/review")).json()
    stored = body["ai_reviews"][DELIVERABLE]
    assert stored["current"] is False
    assert stored["set"]["summary"]  # still readable, still there


async def test_a_review_that_cannot_be_asked_for_and_one_that_failed_answer_differently(
    client, sessionmaker, output_root, monkeypatch
):
    write_gated_run(output_root, "iceberg")
    await sign_in(client, sessionmaker)

    def busy(*_args, **_kwargs):
        raise VlmReviewUnavailableError("the GPU is busy with image-set")

    monkeypatch.setattr(review_routes, "review_batch", busy)
    refused = await client.post("/v1/run-history/iceberg/ai-review", json={})
    # 409: the pictures and the gate are fine; wait for the run, or stop it.
    assert refused.status_code == 409
    assert "busy with image-set" in refused.json()["detail"]

    def broken(*_args, **_kwargs):
        raise RuntimeError("connection refused to 127.0.0.1:11434")

    monkeypatch.setattr(review_routes, "review_batch", broken)
    failed = await client.post("/v1/run-history/iceberg/ai-review", json={})
    # 502: the thing to fix is the model stack, not the run.
    assert failed.status_code == 502
    assert "did not answer" in failed.json()["detail"]


async def test_asking_for_a_review_needs_the_reviewer_role(
    client, sessionmaker, output_root, monkeypatch
):
    """It writes into the run and spends the card. A viewer may read one and not ask for one."""
    write_gated_run(output_root, "iceberg")
    _stub(monkeypatch)
    await sign_in(client, sessionmaker, role=Role.viewer)

    assert (await client.get("/v1/run-history/iceberg/review")).status_code == 200
    assert (await client.post("/v1/run-history/iceberg/ai-review", json={})).status_code == 403


async def test_a_run_with_no_gate_is_a_404_and_an_unknown_deliverable_too(
    client, sessionmaker, output_root, monkeypatch
):
    (output_root / "nogate").mkdir()
    (output_root / "nogate" / "run.json").write_text(
        json.dumps({"project_dir": str(output_root / "nogate"), "stages": [], "passed": True})
    )
    write_gated_run(output_root, "iceberg")
    _stub(monkeypatch)
    await sign_in(client, sessionmaker)

    assert (await client.post("/v1/run-history/nogate/ai-review", json={})).status_code == 404
    named = await client.post("/v1/run-history/iceberg/ai-review", json={"deliverable": "dlv_no"})
    assert named.status_code == 404


async def test_the_review_is_written_where_the_cli_reads_it(
    client, sessionmaker, output_root, monkeypatch
):
    """One file, two surfaces. The panel and `content-factory frames ai-review` must not be able
    to disagree about what the model said."""
    run_dir = write_gated_run(output_root, "iceberg")
    _stub(monkeypatch)
    await sign_in(client, sessionmaker)
    await client.post("/v1/run-history/iceberg/ai-review", json={})

    stored = load_review(run_dir / "deliverables" / DELIVERABLE)
    assert stored is not None
    assert stored.flagged == ("frame:0001",)
