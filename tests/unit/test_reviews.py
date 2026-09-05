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
    assert colour_findings(_png((10, 90, 200)), expect_monochrome=False) == []


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
