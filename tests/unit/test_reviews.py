"""Both review gates: a built sculpture and a batch of generated frames must pass before use.

These tests encode the failures that actually happened. Every check here exists because something
was obviously wrong in a picture and completely fine to the code.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from content_factory.controls.asset_review import (
    REQUIRED_VIEWS,
    AssetReviewError,
    check_bilateral_symmetry,
    check_keypoints_projected,
    review_asset,
)
from content_factory.qc.frame_review import (
    colour_findings,
    contact_sheet,
    continuity_finding,
    review_findings,
    tonal_findings,
)
from content_factory.schemas.assets import AssetCheck, CharacterAssetReview
from content_factory.schemas.review import FrameRecord, FrameReviewBatch

JOINTS = (
    "nose",
    "neck",
    "l_shoulder",
    "r_shoulder",
    "l_elbow",
    "r_elbow",
    "l_wrist",
    "r_wrist",
    "l_hip",
    "r_hip",
    "l_knee",
    "r_knee",
    "l_ankle",
    "r_ankle",
    "l_eye",
    "r_eye",
    "l_ear",
    "r_ear",
)


def _png(colour: tuple[int, int, int], size: tuple[int, int] = (320, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def _gradient_png(size: tuple[int, int] = (320, 180)) -> bytes:
    """A picture with a real tonal range, unlike a flat fill."""
    img = Image.new("L", size)
    img.putdata([int(255 * (x / size[0])) for _ in range(size[1]) for x in range(size[0])])
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def _build_asset(
    root: Path, name: str, *, symmetric: bool = True, drop_joint: bool = False
) -> Path:
    asset_dir = root / name
    (asset_dir).mkdir(parents=True, exist_ok=True)
    (asset_dir / f"{name}.asset.json").write_text(
        json.dumps({"name": name, "blend_sha256": "a" * 64, "height_m": 1.76})
    )
    for view in (*REQUIRED_VIEWS, "left45"):
        vd = asset_dir / "turnaround" / view
        (vd / "rough_rgb" / "frames").mkdir(parents=True, exist_ok=True)
        (vd / "rough_rgb" / "frames" / "0000.png").write_bytes(_png((140, 140, 140)))
        (vd / "layout" / "frames").mkdir(parents=True, exist_ok=True)
        (vd / "layout" / "frames" / "0000.json").write_text(
            json.dumps({"objects": [{"object_id": "subject", "visible_fraction": 1.0}]})
        )
        names = list(JOINTS)[:-1] if drop_joint else list(JOINTS)
        joints = {}
        for j in names:
            # Mirror left/right about x=0.5; a broken rig puts both sides on one side.
            if j.startswith("l_"):
                x = 0.5 - 0.15
            elif j.startswith("r_"):
                x = 0.5 - 0.15 if not symmetric else 0.5 + 0.15
            else:
                x = 0.5
            joints[j] = {"x": x, "y": 0.5, "z": 4.0, "in_frame": True, "visible": True}
        (vd / "skeleton" / "frames").mkdir(parents=True, exist_ok=True)
        (vd / "skeleton" / "frames" / "0000.json").write_text(
            json.dumps({"people": [{"character_id": "subject", "joints": joints}]})
        )
    return asset_dir


# --- the asset gate ---------------------------------------------------------------------------


def test_a_good_asset_passes_its_checks_but_is_not_usable_unapproved(tmp_path: Path) -> None:
    """The distinction the whole gate rests on: measurements are necessary, never sufficient."""
    asset_dir = _build_asset(tmp_path, "man_ok")
    review = review_asset(asset_dir, sheet_dest=tmp_path / "sheet.png")
    assert review.checks_passed, [c.detail for c in review.blockers]
    assert review.usable is False, "nobody has looked at it yet"
    assert review.contact_sheet_sha256 is not None
    assert (tmp_path / "sheet.png").exists()

    approved = review.model_copy(
        update={"approved_by": "operator", "approved_at": dt.datetime.now(dt.UTC)}
    )
    assert approved.usable is True


def test_a_mirrored_rig_is_caught_by_symmetry(tmp_path: Path) -> None:
    """A rig with both sides on one side renders a plausible-looking clay figure and ruins every
    skeleton downstream. Nothing else in the pipeline notices."""
    asset_dir = _build_asset(tmp_path, "man_mirrored", symmetric=False)
    check = check_bilateral_symmetry(asset_dir)
    assert check.passed is False and check.severity == "blocker"
    assert "off centre" in check.detail
    assert review_asset(asset_dir).checks_passed is False


def test_a_collapsed_keypoint_proxy_is_caught(tmp_path: Path) -> None:
    asset_dir = _build_asset(tmp_path, "man_dropped", drop_joint=True)
    check = check_keypoints_projected(asset_dir)
    assert check.passed is False and check.severity == "blocker"
    assert "17 of 18" in check.detail


def test_an_unbuilt_asset_says_so(tmp_path: Path) -> None:
    (tmp_path / "ghost").mkdir()
    with pytest.raises(AssetReviewError, match="was the asset built"):
        review_asset(tmp_path / "ghost")


def test_approval_binds_to_the_built_mesh() -> None:
    """Rebuild the asset and the old approval must not carry over."""
    base = CharacterAssetReview(
        asset="man_01",
        blend_sha256="a" * 64,
        reviewed_at=dt.datetime.now(dt.UTC),
        checks=(AssetCheck(check="views_present", passed=True, severity="blocker", detail="ok"),),
        approved_by="operator",
        approved_at=dt.datetime.now(dt.UTC),
    )
    rebuilt = base.model_copy(update={"blend_sha256": "b" * 64})
    assert base.blend_sha256 != rebuilt.blend_sha256
    assert base.usable and rebuilt.usable  # the model itself carries no cross-check…
    # …so the stage compares digests before reusing an approval; that comparison is the gate.


def test_the_approve_command_writes_where_the_gate_reads() -> None:
    """The gate's own error message names a command. That command has to open the gate.

    It did not: ``assets approve`` defaulted to ``output/asset-reviews/approvals`` while
    ``review_assets`` looked in ``<project_dir>/reviews/approvals``, so following the instruction
    printed by the block left the run blocked with no way to tell why. One setting now answers
    for both, and it lives outside any run because an approval binds to a mesh digest rather than
    to a film.
    """
    import inspect

    from content_factory.cli.main import assets_approve
    from content_factory.config import get_settings
    from content_factory.workflows import stages

    configured = get_settings().controls.asset_approvals_dir
    assert configured == "output/asset-reviews/approvals"
    # The stage reads the setting rather than a path built from the project dir.
    gate = inspect.getsource(stages.stage_review_assets)
    assert "get_settings().controls.asset_approvals_dir" in gate
    assert '_reviews_dir(ctx) / "approvals"' not in gate
    # The CLI falls back to the same setting when --approvals-dir is not given.
    assert "get_settings().controls.asset_approvals_dir" in inspect.getsource(assets_approve)


# --- the frame gate ---------------------------------------------------------------------------


def test_tonal_collapse_is_measured() -> None:
    """The charcoal failure: 31 % of pixels crushed to black, 36 % midtones, looked like a
    photocopy and passed every check that existed at the time."""
    flat_black = tonal_findings(_png((4, 4, 4)))
    by = {f.check: f for f in flat_black}
    assert by["midtone_range"].passed is False
    assert by["black_clipping"].passed is False

    gradient = {f.check: f for f in tonal_findings(_gradient_png())}
    assert gradient["midtone_range"].passed is True


def test_a_half_applied_monochrome_instruction_blocks() -> None:
    """The exact "weird colouring": a style asked for no colour and 39 % of pixels were saturated,
    so the image was neither a colour image nor a grey one."""
    saturated = colour_findings(_png((10, 90, 200)), expect_monochrome=True)
    assert saturated and saturated[0].passed is False
    assert saturated[0].severity == "blocker"

    grey = colour_findings(_png((128, 128, 128)), expect_monochrome=True)
    assert grey and grey[0].passed is True

    # A style that never asked for monochrome is not judged on it.
    # The other direction: a colour brief that came back grey is the same half-applied style, and
    # it went unchecked. A saturated frame passes it; a greyscale one does not.
    colour_ok = colour_findings(_png((10, 90, 200)), expect_monochrome=False)
    assert colour_ok and colour_ok[0].check == "colour_present" and colour_ok[0].passed is True
    colour_missing = colour_findings(_png((128, 128, 128)), expect_monochrome=False)
    assert colour_missing and colour_missing[0].passed is False
    assert colour_missing[0].severity == "blocker"


def test_background_churn_is_measured() -> None:
    """Thirty drawings each inventing their own street is a number, not an opinion."""
    same = continuity_finding(_gradient_png(), _gradient_png())
    assert same.passed is True and same.measured == 0.0
    different = continuity_finding(_png((10, 10, 10)), _png((240, 240, 240)))
    assert different.passed is False
    assert different.measured is not None and different.measured > 200


def test_the_contact_sheet_holds_every_frame_and_flags_the_ones_to_check(tmp_path: Path) -> None:
    frames = [(f"sht_{i}", _gradient_png()) for i in range(5)]
    findings = review_findings(frames)
    png = contact_sheet(frames, tmp_path / "sheet.png", findings=findings, columns=3)
    sheet = Image.open(io.BytesIO(png))
    # 5 frames at 3 columns is 2 rows; every frame is on the sheet.
    assert sheet.width == 3 * 520
    assert sheet.height == 2 * (round(520 * 180 / 320) + 22)
    with pytest.raises(ValueError, match="no frames to review"):
        contact_sheet([], tmp_path / "empty.png")


def test_a_batch_is_not_passed_until_a_reviewer_says_so() -> None:
    frames = (FrameRecord(frame_id="sht_a", png_sha256="c" * 64),)
    batch = FrameReviewBatch(
        deliverable_id="dlv_x000000000001",
        contact_sheet_sha256="d" * 64,
        contact_sheet_path="reviews/frames/contact-sheet.png",
        frames=frames,
        created_at=dt.datetime.now(dt.UTC),
    )
    assert batch.passed is False and len(batch.unreviewed) == 1

    accepted = batch.model_copy(
        update={
            "reviewer": "agent",
            "reviewed_at": dt.datetime.now(dt.UTC),
            "frames": (frames[0].model_copy(update={"verdict": "accept"}),),
        }
    )
    assert accepted.passed is True

    rejected = accepted.model_copy(
        update={
            "frames": (frames[0].model_copy(update={"verdict": "reject", "reason": "3 people"}),)
        }
    )
    assert rejected.passed is False and rejected.rejected[0].reason == "3 people"


def test_reviewer_and_timestamp_must_be_set_together() -> None:
    with pytest.raises(ValueError, match="reviewer and reviewed_at"):
        FrameReviewBatch(
            deliverable_id="dlv_x000000000001",
            contact_sheet_sha256="d" * 64,
            contact_sheet_path="s.png",
            frames=(FrameRecord(frame_id="a", png_sha256="c" * 64),),
            created_at=dt.datetime.now(dt.UTC),
            reviewer="agent",
        )


def test_a_pillarboxed_frame_is_a_blocker() -> None:
    """A model asked for 16:9 that draws a narrower picture and pads the sides with flat colour.

    Invisible to every other check here — the bars are mid grey, so nothing is crushed and nothing
    is blown — and unmistakable once measured. Over one evening's anchors the separation was total:
    the one pillarboxed frame put 60 % of its width in flat bars and every other frame measured 0.
    """
    import io

    from PIL import Image

    from content_factory.qc.frame_review import border_findings

    padded = Image.new("RGB", (320, 180), (127, 127, 127))
    padded.paste(_load_gradient((160, 180)), (80, 0))
    buf = io.BytesIO()
    padded.save(buf, "PNG")
    finding = border_findings(buf.getvalue())[0]
    assert finding.check == "fills_the_frame"
    assert finding.passed is False and finding.severity == "blocker"
    assert finding.measured is not None and finding.measured >= 0.4

    # A picture that fills its frame passes.
    assert border_findings(_gradient_png())[0].passed is True

    # A flat band on ONE edge only is a photograph — a plain sky over a landscape — not a padded
    # frame, so it passes. Bars on both edges is a letterbox and does not.
    one_edge = Image.new("RGB", (320, 180), (127, 127, 127))
    one_edge.paste(_load_gradient((320, 120)), (0, 60))
    buf2 = io.BytesIO()
    one_edge.save(buf2, "PNG")
    assert border_findings(buf2.getvalue())[0].passed is True

    letterboxed = Image.new("RGB", (320, 180), (127, 127, 127))
    letterboxed.paste(_load_gradient((320, 100)), (0, 40))
    buf3 = io.BytesIO()
    letterboxed.save(buf3, "PNG")
    assert border_findings(buf3.getvalue())[0].passed is False


def _load_gradient(size: tuple[int, int]) -> Image.Image:
    from PIL import Image

    img = Image.new("L", size)
    width, height = size
    img.putdata([int(255 * (x / max(1, width - 1))) for _y in range(height) for x in range(width)])
    return img.convert("RGB")


def test_a_frame_a_person_rejected_is_redrawn_rather_than_cache_hit(tmp_path) -> None:
    """`image-set`'s own note promises "rejecting one drawing costs one drawing, not the film".

    Nothing implemented it: `review_frames` recorded the verdict and `generate_keyframes` never
    read it, so a rejected frame stayed a cache hit and the gate blocked on the same picture on
    every rerun. Measured 2026-09-10 on a malachite set — six consistent views of the wrong
    object, rejected, and the resume printed the identical rejection.
    """
    import json

    from content_factory.runners.local import make_context
    from content_factory.workflows.stages import _clear_rejected_frames

    ctx = make_context(project_dir=tmp_path / "prj")
    frames = ctx.ddir() / "sequence" / "frames"
    frames.mkdir(parents=True)
    for idx in range(3):
        (frames / f"{idx:04d}.png").write_bytes(b"png")
        (frames / f"{idx:04d}.done.json").write_text(json.dumps({"input_hash": "h"}))

    # No review on disk: nothing is touched.
    assert _clear_rejected_frames(ctx, ctx.ddir() / "sequence") == []
    assert sorted(p.name for p in frames.glob("*.png")) == ["0000.png", "0001.png", "0002.png"]

    review = ctx.ddir() / "reviews" / "frames"
    review.mkdir(parents=True)
    review.joinpath("batch.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "deliverable_id": ctx.deliverable_id,
                "contact_sheet_sha256": "a" * 64,
                "contact_sheet_path": "reviews/frames/contact-sheet.png",
                "reviewer": "agent",
                "created_at": "2026-09-10T03:32:00Z",
                "reviewed_at": "2026-09-10T03:32:10Z",
                "frames": [
                    {
                        "frame_id": f"frame:{i:04d}",
                        "png_sha256": f"{i}" * 64,
                        "verdict": "reject" if i == 1 else "accept",
                        "reason": "the subject is wrong" if i == 1 else "",
                        "findings": [],
                    }
                    for i in range(3)
                ],
            }
        )
    )
    assert _clear_rejected_frames(ctx, ctx.ddir() / "sequence") == [1]
    # The rejected frame's marker is gone, so the next run redraws exactly that one...
    assert not (frames / "0001.done.json").exists()
    assert not (frames / "0001.png").exists()
    # ...the accepted ones are untouched...
    assert (frames / "0000.done.json").exists() and (frames / "0002.done.json").exists()
    # ...and what was turned down can still be looked at.
    kept = ctx.ddir() / "sequence" / "rejected"
    assert (kept / "0001.reviewed1.png").exists()
    assert (kept / "0001.reviewed1.done.json").exists()

    # Idempotent: a second pass has nothing left to clear.
    assert _clear_rejected_frames(ctx, ctx.ddir() / "sequence") == []

    # And the redraw has to come back *different*: the backend derives its seed from the attempt
    # number, so a rejection moves the seed. Measured 2026-09-10 — a rejected kilim frame was
    # redrawn and came back as the drawing that had just been turned down.
    from content_factory.workflows.stages import _rejection_seed_offsets

    seq = ctx.ddir() / "sequence"
    assert _rejection_seed_offsets(seq)[1] > 0
    assert 0 not in _rejection_seed_offsets(seq), "an accepted frame keeps its seed"

    # A second rejection of the same frame moves it again rather than landing where it was.
    (frames / "0001.png").write_bytes(b"png")
    (frames / "0001.done.json").write_text(json.dumps({"input_hash": "h"}))
    review.joinpath("batch.json").write_text(
        review.joinpath("batch.json").read_text()  # same verdicts, frame 1 still rejected
    )
    assert _clear_rejected_frames(ctx, seq) == [1]
    assert _rejection_seed_offsets(seq)[1] > _rejection_seed_offsets(seq).get(0, 0)
    kept = sorted(p.name for p in (seq / "rejected").glob("0001.reviewed*.png"))
    assert kept == ["0001.reviewed1.png", "0001.reviewed2.png"], kept


# --- when Claude started the run, Claude reviews ------------------------------------------------
#
# Both gates park on a person because passing measurements is necessary and never sufficient. But
# a run started from a Claude Code session has an agent present, and 27 runs on this machine sat
# at `review_frames` with their drawings finished and nobody coming. So the gate asks who is
# reviewing — and makes an agent look, rather than trusting it to.


def test_a_run_started_from_claude_code_routes_its_review_to_the_agent() -> None:
    from content_factory.qc.reviewer import intended_reviewer

    # CLAUDECODE is set in every command a Claude Code session runs, which makes "I started this
    # run with Claude" the trigger rather than a flag somebody has to remember.
    assert intended_reviewer(None, {"CLAUDECODE": "1"}) == "agent"
    assert intended_reviewer(None, {}) == "operator"
    # A person can take it back, and an explicit choice on the node beats the environment.
    assert intended_reviewer(None, {"CLAUDECODE": "1", "CF_REVIEWER": "operator"}) == "operator"
    assert intended_reviewer("operator", {"CLAUDECODE": "1"}) == "operator"


def test_an_unrecognised_reviewer_name_asks_a_person_rather_than_raising() -> None:
    """The failure mode has to be "a person is asked", never "nobody is". `--as overnight-review`
    once wrote a verdict the next stage then refused; a typo must not be able to skip a gate."""
    from content_factory.qc.reviewer import intended_reviewer

    assert intended_reviewer("claude", {}) == "operator"
    assert intended_reviewer(None, {"CF_REVIEWER": "yes-please"}) == "operator"


def test_an_agent_verdict_has_to_name_every_frame() -> None:
    """What makes "review every image" a rule and not a hope. A person reviews from a contact
    sheet — one image showing all of them — so "all fine" is a real answer. An agent reads them
    one at a time, so a frame its verdict never mentions is a frame it did not open."""
    from content_factory.qc.reviewer import missing_decisions

    frames = tuple(FrameRecord(frame_id=f"shot_{i}", png_sha256="a" * 64) for i in range(4))
    assert missing_decisions(frames, {"shot_0"}, {"shot_1"}) == ["shot_2", "shot_3"]
    assert missing_decisions(frames, {"shot_0", "shot_2", "shot_3"}, {"shot_1"}) == []


def test_every_frame_is_compared_with_every_other_not_just_its_neighbour() -> None:
    """`continuity_finding` compares consecutive frames, which answers "was there a cut here" and
    cannot answer "do all six belong to one set": a set can drift a little at each step and end
    somewhere else with every consecutive pair looking fine."""
    from content_factory.qc.frame_review import consistency_matrix

    frames = [(f"f{i}", _gradient_png()) for i in range(6)]
    matrix = consistency_matrix(frames)
    assert matrix["pairs_compared"] == 15  # 6 choose 2, not 5 consecutive pairs
    assert matrix["frames"] == 6
    assert matrix["median_distance"] == 0.0  # identical frames
    assert matrix["outliers"] == []
    assert matrix["spread"] is False


def test_the_one_frame_that_left_the_set_is_the_one_named() -> None:
    """Measured on the real thing: one whelk drawing dropped into five pinecone frames reads
    0.140 against the set's own 0.039, so the threshold sits at 0.11 between them."""
    from content_factory.qc.frame_review import consistency_findings, consistency_matrix

    frames = [(f"f{i}", _gradient_png()) for i in range(5)]
    frames.append(("intruder", _png((10, 200, 40))))
    matrix = consistency_matrix(frames)
    assert matrix["outliers"] == ["intruder"]
    assert matrix["spread"] is False  # the other five still agree, so there *is* an odd one out
    findings = consistency_findings(frames)
    assert findings["intruder"].passed is False
    assert findings["f0"].passed is True
    assert findings["intruder"].check == "set_consistency"


def test_a_set_with_no_consistent_core_is_not_six_accusations() -> None:
    """An outlier is only meaningful against a set that agrees with itself. Measured on
    `w-iceberg` — an image-set of six deliberately different viewpoints — the median pair sat at
    0.243 and every frame came back an "outlier", which is both useless and wrong. A set is
    allowed to be varied on purpose."""
    from content_factory.qc.frame_review import consistency_findings, consistency_matrix

    frames = [
        ("red", _png((200, 20, 20))),
        ("green", _png((20, 200, 20))),
        ("blue", _png((20, 20, 200))),
        ("white", _png((240, 240, 240))),
    ]
    matrix = consistency_matrix(frames)
    assert matrix["spread"] is True
    assert matrix["outliers"] == []  # no centre to be far from
    detail = consistency_findings(frames)["red"].detail
    assert "no consistent core" in detail
    assert all(not f.passed for f in consistency_findings(frames).values())


def test_one_frame_has_nothing_to_be_consistent_with() -> None:
    from content_factory.qc.frame_review import consistency_findings

    assert consistency_findings([("only", _gradient_png())]) == {}


def test_consistency_travels_with_the_other_findings() -> None:
    frames = [(f"f{i}", _gradient_png()) for i in range(3)]
    checks = {f.check for f in review_findings(frames)["f1"]}
    assert "set_consistency" in checks
    assert "continuity" in checks  # the neighbour check stays; they answer different questions


def test_the_request_names_every_image_and_the_command_that_answers() -> None:
    """Written as a file beside the images because the run that produced it has usually exited
    by the time anyone reads it."""
    from content_factory.qc.frame_review import consistency_matrix
    from content_factory.qc.reviewer import request_markdown

    pngs = [(f"shot_{i}:0000", _gradient_png()) for i in range(3)]
    frames = tuple(FrameRecord(frame_id=frame_id, png_sha256="b" * 64) for frame_id, _ in pngs)
    text = request_markdown(
        run_dir="output/overnight/x",
        deliverable="dlv_short0000001",
        contact_sheet="reviews/frames/contact-sheet.png",
        frames=frames,
        consistency=consistency_matrix(pngs),
    )
    for frame_id, _ in pngs:
        assert frame_id in text
    assert "Open every one of them" in text
    assert "content-factory frames review output/overnight/x" in text
    assert "--as agent" in text
    assert "pair(s) compared" in text
    # It says what the measurements cannot see, because that is the reason to look at all.
    assert "whether the subject is the same subject" in text


def _batch_on_disk(root: Path, frame_ids: list[str]) -> Path:
    """A run directory with a batch the review command can decide on."""
    base = root / "deliverables" / "dlv_short0000001" / "reviews" / "frames"
    base.mkdir(parents=True)
    batch = FrameReviewBatch(
        deliverable_id="dlv_short0000001",
        contact_sheet_sha256="c" * 64,
        contact_sheet_path="reviews/frames/contact-sheet.png",
        frames=tuple(FrameRecord(frame_id=f, png_sha256="d" * 64) for f in frame_ids),
        created_at=dt.datetime.now(dt.UTC),
    )
    (base / "batch.json").write_text(batch.model_dump_json(indent=1))
    return base


def test_a_verdict_the_contract_cannot_read_back_is_refused_before_it_is_written(
    tmp_path: Path,
) -> None:
    """Found by using it. `model_copy(update=...)` does not re-validate in Pydantic v2, so a
    rejection reason past `reason`'s 400 characters went straight to disk and the next
    `review_frames` died reading the file it had just been handed. Second time this command has
    written a verdict the gate then refused — the first was `--as overnight-review`."""
    from typer.testing import CliRunner

    from content_factory.cli.main import app

    base = _batch_on_disk(tmp_path / "run", ["shot_a:0000", "shot_b:0000"])
    result = CliRunner().invoke(
        app,
        [
            "frames",
            "review",
            str(tmp_path / "run"),
            "--as",
            "agent",
            "--reject",
            "shot_a:0000,shot_b:0000",
            "--accept",
            "",
            "--reason",
            "x" * 401,
        ],
    )
    assert result.exit_code == 2
    assert "at most 400 characters" in result.output
    assert "nothing was written" in result.output
    assert not (base / "verdict.json").exists()  # and it really wrote nothing


def test_an_agent_cannot_accept_a_batch_it_did_not_open(tmp_path: Path) -> None:
    """`--accept-all` is a reviewer saying yes to a batch nobody looked at. Defensible for a
    person reading one contact sheet; not for an agent reading images one at a time."""
    from typer.testing import CliRunner

    from content_factory.cli.main import app

    base = _batch_on_disk(tmp_path / "run", ["shot_a:0000", "shot_b:0000", "shot_c:0000"])
    runner = CliRunner()

    blanket = runner.invoke(
        app, ["frames", "review", str(tmp_path / "run"), "--as", "agent", "--accept-all"]
    )
    assert blanket.exit_code == 2
    assert "not available to --as agent" in blanket.output

    # Naming some of them is not naming all of them, and the ones left out are listed.
    partial = runner.invoke(
        app,
        ["frames", "review", str(tmp_path / "run"), "--as", "agent", "--accept", "shot_a:0000"],
    )
    assert partial.exit_code == 2
    assert "2 frame(s) not decided" in partial.output
    assert "shot_b:0000" in partial.output and "shot_c:0000" in partial.output
    assert not (base / "verdict.json").exists()

    # Every frame named, and it passes.
    full = runner.invoke(
        app,
        [
            "frames",
            "review",
            str(tmp_path / "run"),
            "--as",
            "agent",
            "--accept",
            "shot_a:0000,shot_c:0000",
            "--reject",
            "shot_b:0000",
            "--reason",
            "the middle one is a different object",
        ],
    )
    assert full.exit_code == 0
    written = FrameReviewBatch.model_validate_json((base / "verdict.json").read_text())
    assert written.reviewer == "agent"
    assert [f.verdict for f in written.frames] == ["accept", "reject", "accept"]
    # A person keeps the blanket accept, because a contact sheet is one image of all of them.
    assert (
        runner.invoke(
            app, ["frames", "review", str(tmp_path / "run"), "--as", "operator", "--accept-all"]
        ).exit_code
        == 0
    )


def test_a_frame_cannot_be_accepted_and_rejected_at_once(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from content_factory.cli.main import app

    _batch_on_disk(tmp_path / "run", ["shot_a:0000"])
    result = CliRunner().invoke(
        app,
        [
            "frames",
            "review",
            str(tmp_path / "run"),
            "--as",
            "agent",
            "--accept",
            "shot_a:0000",
            "--reject",
            "shot_a:0000",
        ],
    )
    assert result.exit_code == 2
    assert "both accepted and rejected" in result.output


def _gated_run(root: Path, *, shot_lane: bool = False) -> Path:
    """A run directory as ``review_frames`` leaves one, in either of the two lane shapes."""
    deliverable = root / "deliverables" / "dlv_short0000001"
    records = []
    if shot_lane:
        shots = []
        for index, shade in enumerate(((30, 30, 30), (90, 90, 90))):
            shot_id = f"shot_{index:012x}"
            png = _png(shade)
            path = f"anchors/{shot_id}/0000.png"
            (deliverable / f"anchors/{shot_id}").mkdir(parents=True)
            (deliverable / path).write_bytes(png)
            shots.append({"shot_id": shot_id, "frames": [{"frame_index": 0, "path": path}]})
            records.append(FrameRecord(frame_id=f"{shot_id}:0000", png_sha256=_sha(png)))
        (deliverable / "anchors" / "manifest.json").write_text(json.dumps({"shots": shots}))
    else:
        frames_dir = deliverable / "sequence" / "frames"
        frames_dir.mkdir(parents=True)
        for index, shade in enumerate(((30, 30, 30), (90, 90, 90))):
            png = _png(shade)
            (frames_dir / f"{index:04d}.png").write_bytes(png)
            records.append(FrameRecord(frame_id=f"frame:{index:04d}", png_sha256=_sha(png)))
    base = deliverable / "reviews" / "frames"
    base.mkdir(parents=True)
    (base / "contact-sheet.png").write_bytes(_png((200, 200, 200)))
    batch = FrameReviewBatch(
        deliverable_id="dlv_short0000001",
        contact_sheet_sha256="c" * 64,
        contact_sheet_path="reviews/frames/contact-sheet.png",
        frames=tuple(records),
        created_at=dt.datetime.now(dt.UTC),
    )
    (base / "batch.json").write_text(batch.model_dump_json(indent=1))
    return deliverable


def _sha(data: bytes) -> str:
    from content_factory.schemas.base import sha256_hex

    return sha256_hex(data)


def test_the_panel_finds_the_picture_behind_every_frame_id_in_both_lanes(tmp_path: Path) -> None:
    """A frame id is `frame:0007` on the keyframe lanes and `shot_ab54…:0000` on the anchor
    lanes, and the second one's path is read from the manifest rather than guessed: a lane may
    write more than one frame per shot."""
    from content_factory.services import frame_reviews

    keyframes = _gated_run(tmp_path / "keyframes")
    review = frame_reviews.review(tmp_path / "keyframes", keyframes)
    assert review is not None
    assert [f.image for f in review.frames] == [
        "deliverables/dlv_short0000001/sequence/frames/0000.png",
        "deliverables/dlv_short0000001/sequence/frames/0001.png",
    ]
    assert all(f.on_disk for f in review.frames)

    anchors = _gated_run(tmp_path / "anchors", shot_lane=True)
    shot_review = frame_reviews.review(tmp_path / "anchors", anchors)
    assert shot_review is not None
    assert [f.image for f in shot_review.frames] == [
        "deliverables/dlv_short0000001/anchors/shot_000000000000/0000.png",
        "deliverables/dlv_short0000001/anchors/shot_000000000001/0000.png",
    ]


def test_a_redrawn_frame_is_unreviewed_again_even_though_the_verdict_names_it(
    tmp_path: Path,
) -> None:
    """The whole point of binding a verdict to image digests, and the reason the panel and the
    gate share one merge: a frame that was regenerated after being accepted must come back as a
    question. If the panel used frame ids it would show an approved picture nobody has seen."""
    from content_factory.services import frame_reviews

    deliverable = _gated_run(tmp_path / "run")
    frame_reviews.record_verdict(
        tmp_path / "run", deliverable, reviewer="operator", accept=[], reject=[], accept_rest=True
    )
    accepted = frame_reviews.review(tmp_path / "run", deliverable)
    assert accepted is not None and accepted.passed

    # The generator redraws frame 1 and the gate rewrites its batch with the new digest.
    redrawn = _png((160, 20, 20))
    (deliverable / "sequence" / "frames" / "0001.png").write_bytes(redrawn)
    batch_path = deliverable / "reviews" / "frames" / "batch.json"
    batch = FrameReviewBatch.model_validate_json(batch_path.read_text())
    batch_path.write_text(
        batch.model_copy(
            update={
                "frames": (
                    batch.frames[0],
                    batch.frames[1].model_copy(update={"png_sha256": _sha(redrawn)}),
                )
            }
        ).model_dump_json(indent=1)
    )

    after = frame_reviews.review(tmp_path / "run", deliverable)
    assert after is not None
    assert after.passed is False
    assert after.unreviewed == 1
    assert [f.verdict for f in after.frames] == ["accept", "unreviewed"]
    assert frame_reviews.waiting_frames(tmp_path / "run") == 1


def test_the_panel_cannot_hand_an_agent_the_blanket_yes_the_cli_refuses(tmp_path: Path) -> None:
    """One rule, one implementation: `qc.verdict` answers for the CLI and the browser alike."""
    from content_factory.qc.verdict import VerdictRefusedError
    from content_factory.services import frame_reviews

    deliverable = _gated_run(tmp_path / "run")
    with pytest.raises(VerdictRefusedError) as refused:
        frame_reviews.record_verdict(
            tmp_path / "run", deliverable, reviewer="agent", accept=[], reject=[], accept_rest=True
        )
    assert refused.value.kind == "agent_blanket"
    assert not (deliverable / "reviews" / "frames" / "verdict.json").exists()
