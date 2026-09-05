"""Deterministic checks over a built character asset's own turnaround renders.

A character asset is generated, and nothing downstream can tell a good one from a broken one. The
turnaround the build already renders carries everything needed to catch the faults that matter,
because those renders include the layout boxes and the projected OpenPose joints, not just RGB:

* the required views exist at all
* the figure fits inside its own turnaround frame, fully visible
* all eighteen keypoints projected — a collapsed keypoint proxy silently ruins every skeleton
* the t-pose is bilaterally symmetric, which is how a mirrored or broken rig announces itself
* the mesh is a plausible human height

None of that judges whether the sculpture *looks* like a person. That is what the contact sheet
and an approval are for; see :mod:`content_factory.schemas.assets`.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

from content_factory.schemas.assets import AssetCheck, CharacterAssetReview
from content_factory.schemas.base import sha256_hex

REQUIRED_VIEWS = ("front", "back", "left90", "right45", "right90", "t_pose")
"""The views the checks need, all of which the builder renders. ``right90`` was added to both after
:func:`check_profile_coverage` reported the coverage as one-sided on every asset: a sculpt can be
wrong on a side nobody rendered and still pass the t-pose symmetry check, which only compares
keypoints. It is a blocker here rather than an advisory because the builder now makes it, so a
missing one means the turnaround is stale rather than that the tool cannot produce it."""

MIRRORED_VIEWS = (("left90", "right90"), ("left45", "right45"))
OPENPOSE18_COUNT = 18
HEIGHT_RANGE_M = (1.40, 2.10)
SYMMETRY_TOLERANCE = 0.035
"""Left/right keypoints in a t-pose must mirror about the body's centre within this, in normalised
image width. Loose enough for an asymmetric sculpt, tight enough to catch a mirrored rig."""


class AssetReviewError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _view_dir(asset_dir: Path, view: str) -> Path:
    return asset_dir / "turnaround" / view


def check_views_present(asset_dir: Path) -> AssetCheck:
    missing = [
        v for v in REQUIRED_VIEWS if not (_view_dir(asset_dir, v) / "rough_rgb" / "frames").is_dir()
    ]
    return AssetCheck(
        check="views_present",
        passed=not missing,
        severity="blocker",
        detail=(
            f"all {len(REQUIRED_VIEWS)} required views rendered"
            if not missing
            else f"missing views: {', '.join(missing)}"
        ),
        measured=float(len(REQUIRED_VIEWS) - len(missing)),
        threshold=float(len(REQUIRED_VIEWS)),
    )


def check_figure_in_frame(asset_dir: Path) -> AssetCheck:
    """Fully visible, and not jammed against its own frame edge."""
    worst = 1.0
    worst_view = ""
    for view in REQUIRED_VIEWS:
        layout = _view_dir(asset_dir, view) / "layout" / "frames" / "0000.json"
        if not layout.exists():
            continue
        for obj in _read_json(layout)["objects"]:
            visible = float(obj.get("visible_fraction", 0.0))
            if visible < worst:
                worst, worst_view = visible, view
    return AssetCheck(
        check="figure_in_frame",
        passed=worst >= 0.999,
        severity="blocker",
        detail=(
            "the figure is fully inside every turnaround frame"
            if worst >= 0.999
            else f"only {worst:.0%} of the figure is in frame in the {worst_view} view"
        ),
        measured=round(worst, 4),
        threshold=0.999,
    )


def check_keypoints_projected(asset_dir: Path) -> AssetCheck:
    """A collapsed keypoint proxy is the fault that ruins every skeleton downstream in silence."""
    fewest = OPENPOSE18_COUNT
    worst_view = ""
    for view in REQUIRED_VIEWS:
        skeleton = _view_dir(asset_dir, view) / "skeleton" / "frames" / "0000.json"
        if not skeleton.exists():
            continue
        for person in _read_json(skeleton).get("people", []):
            count = sum(1 for j in person.get("joints", {}).values() if j.get("in_frame"))
            if count < fewest:
                fewest, worst_view = count, view
    return AssetCheck(
        check="keypoints_projected",
        passed=fewest >= OPENPOSE18_COUNT,
        severity="blocker",
        detail=(
            f"all {OPENPOSE18_COUNT} keypoints project in every view"
            if fewest >= OPENPOSE18_COUNT
            else f"only {fewest} of {OPENPOSE18_COUNT} keypoints project in the {worst_view} view"
        ),
        measured=float(fewest),
        threshold=float(OPENPOSE18_COUNT),
    )


PAIRS = (
    ("l_shoulder", "r_shoulder"),
    ("l_elbow", "r_elbow"),
    ("l_wrist", "r_wrist"),
    ("l_hip", "r_hip"),
    ("l_knee", "r_knee"),
    ("l_ankle", "r_ankle"),
)


def check_bilateral_symmetry(asset_dir: Path) -> AssetCheck:
    """In a t-pose the left and right of a body mirror about its own centre. A rig whose sides are
    swapped, or whose weights are broken on one side, fails here and nowhere else."""
    skeleton = _view_dir(asset_dir, "t_pose") / "skeleton" / "frames" / "0000.json"
    if not skeleton.exists():
        return AssetCheck(
            check="bilateral_symmetry",
            passed=False,
            severity="blocker",
            detail="no t_pose skeleton to check symmetry against",
        )
    people = _read_json(skeleton).get("people", [])
    if not people:
        return AssetCheck(
            check="bilateral_symmetry",
            passed=False,
            severity="blocker",
            detail="the t_pose view contains no skeleton",
        )
    joints = people[0]["joints"]
    centre = joints.get("neck", {}).get("x")
    if centre is None:
        return AssetCheck(
            check="bilateral_symmetry",
            passed=False,
            severity="blocker",
            detail="no neck keypoint to measure symmetry about",
        )
    worst = 0.0
    worst_pair = ""
    for left, right in PAIRS:
        if left not in joints or right not in joints:
            continue
        offset = abs((joints[left]["x"] - centre) + (joints[right]["x"] - centre))
        if offset > worst:
            worst, worst_pair = offset, f"{left}/{right}"
    return AssetCheck(
        check="bilateral_symmetry",
        passed=worst <= SYMMETRY_TOLERANCE,
        severity="blocker",
        detail=(
            "the t-pose is bilaterally symmetric"
            if worst <= SYMMETRY_TOLERANCE
            else f"{worst_pair} are off centre by {worst:.3f} of frame width"
        ),
        measured=round(worst, 4),
        threshold=SYMMETRY_TOLERANCE,
    )


def check_profile_coverage(asset_dir: Path) -> AssetCheck:
    """Both sides of the body, or only one? An identity reference drawn from one-sided profiles
    gives the image model nothing about the other side of a face or a coat."""
    missing = [
        right
        for left, right in MIRRORED_VIEWS
        if (_view_dir(asset_dir, left) / "rough_rgb" / "frames").is_dir()
        and not (_view_dir(asset_dir, right) / "rough_rgb" / "frames").is_dir()
    ]
    return AssetCheck(
        check="profile_coverage",
        passed=not missing,
        severity="advisory",
        detail=(
            "both sides of the body are covered"
            if not missing
            else f"only one side is covered: {', '.join(missing)} was never rendered"
        ),
    )


def check_height_plausible(index: dict) -> AssetCheck:
    height = float(index.get("height_m", 0.0))
    lo, hi = HEIGHT_RANGE_M
    return AssetCheck(
        check="height_plausible",
        passed=lo <= height <= hi,
        severity="blocker",
        detail=f"the mesh is {height:.2f} m tall (expected {lo:.2f}-{hi:.2f})",
        measured=round(height, 4),
        threshold=hi,
    )


def asset_contact_sheet(asset_dir: Path, dest: Path, *, tile_width: int = 420) -> bytes:
    """The views tiled, for a person to look at. Deterministic for identical input."""
    tiles: list[tuple[str, Image.Image]] = []
    for view in REQUIRED_VIEWS:
        png = _view_dir(asset_dir, view) / "rough_rgb" / "frames" / "0000.png"
        if png.exists():
            tiles.append((view, Image.open(png).convert("RGB")))
    if not tiles:
        msg = f"no turnaround renders under {asset_dir}"
        raise AssetReviewError(msg)
    tile_h = round(tile_width * tiles[0][1].height / tiles[0][1].width)
    label_h = 20
    sheet = Image.new("RGB", (tile_width * len(tiles), tile_h + label_h), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)
    for i, (view, img) in enumerate(tiles):
        sheet.paste(
            img.resize((tile_width, tile_h), Image.Resampling.LANCZOS), (i * tile_width, label_h)
        )
        draw.text((i * tile_width + 6, 4), view, fill=(240, 240, 240))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())
    return buf.getvalue()


def check_identity_sheets(asset_dir: Path, sheets) -> AssetCheck:
    """Are this asset's styled sheets still the ones its current mesh was drawn from?

    Advisory, not a blocker, and the distinction is deliberate. A film whose ``anchor_references``
    does not include ``identity`` needs no sheet at all, so "none built" cannot fail an asset. What
    *would* be wrong is a sheet drawn from a mesh that has since been rebuilt: the reference would
    be a different body from the one the shot stages. ``stage_review_assets`` is where a lane that
    actually asks for an identity slot turns the absence into a block.
    """
    built = _read_json(next(asset_dir.glob("*.asset.json")))
    stale = sorted(s.style_slug for s in sheets if s.blend_sha256 != built["blend_sha256"])
    return AssetCheck(
        check="identity_sheets",
        passed=not stale,
        severity="advisory",
        detail=(
            f"{len(sheets)} styled sheet(s) on disk"
            + (f"; drawn from an older mesh: {', '.join(stale)}" if stale else "")
        ),
        measured=float(len(sheets)),
    )


def review_asset(asset_dir: Path, *, sheet_dest: Path | None = None) -> CharacterAssetReview:
    """Every check, plus the contact sheet and the styled identity sheets, bound to the mesh."""
    index_path = asset_dir / "turnaround" / "index.json"
    asset_json = next(asset_dir.glob("*.asset.json"), None)
    if asset_json is None:
        msg = f"{asset_dir} has no <name>.asset.json — was the asset built?"
        raise AssetReviewError(msg)
    built = _read_json(asset_json)
    index = _read_json(index_path) if index_path.exists() else built
    from content_factory.controls.identity_sheets import sheets_on_disk

    # ``characters/<asset>`` -> the assets root two levels up, which is what sheets_on_disk takes.
    sheets = sheets_on_disk(asset_dir.parent.parent, str(built["name"]))
    checks = [
        check_views_present(asset_dir),
        check_figure_in_frame(asset_dir),
        check_keypoints_projected(asset_dir),
        check_bilateral_symmetry(asset_dir),
        check_profile_coverage(asset_dir),
        check_height_plausible(built),
        check_identity_sheets(asset_dir, sheets),
    ]
    sheet_sha: str | None = None
    if sheet_dest is not None:
        sheet_sha = sha256_hex(asset_contact_sheet(asset_dir, sheet_dest))
    return CharacterAssetReview(
        asset=built["name"],
        blend_sha256=built["blend_sha256"],
        reviewed_at=dt.datetime.now(dt.UTC),
        checks=tuple(checks),
        contact_sheet_sha256=sheet_sha,
        sheets=sheets,
        notes=f"turnaround {index.get('schema', 'unknown')}",
    )
