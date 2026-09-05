"""Styled identity sheets: the reference the identity slot could not previously be given.

`ImageSequenceSettings.anchor_references` has said, in its own comment, to add `identity` only once
a character has a *styled* sheet rather than a clay turnaround — and there was no way to make one,
so the slot was unusable and identity was carried by nothing at all. Both failures behind that
comment were measured, not guessed: HiDream-O1's IP pipeline treats every reference as subject
material, so the Blender clay render made it draw clay people and the untextured MPFB turnaround
made it draw a nude mannequin (STATUS 1339, 1379-1381). Across one thirty-anchor run it held one
world in every frame and changed the character's outfit four times inside it (STATUS 1627).

`_identity_reference` was sending exactly that clay front view.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from content_factory.config import get_settings
from content_factory.controls.identity_sheets import (
    SHEET_PROMPT_VERSION,
    SheetError,
    build_sheet,
    sheet_paths,
    sheet_prompt,
    sheet_sha_for_style,
    sheets_on_disk,
    style_slug,
)
from content_factory.schemas.base import sha256_hex


class FakeImageModel:
    """A backend that draws a distinct picture per prompt and records what it was shown."""

    name = "fake-sheet-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, prompt, conditioning, lock, *, seed):
        self.calls.append(
            {
                "prompt": prompt,
                "refs": len(conditioning.reference_pngs),
                "seed": seed,
                "size": (lock.width, lock.height),
                "style": lock.style_prompt,
            }
        )
        digest = sha256_hex(f"{prompt}|{seed}".encode())
        img = Image.new("RGB", (32, 32), (int(digest[0:2], 16), int(digest[2:4], 16), 90))
        import io

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


@pytest.fixture
def asset(tmp_path: Path) -> Path:
    """A built asset that passes the mesh review, so the sheet gate is the thing under test.

    The same shape ``test_reviews`` builds: every required turnaround view with its layout and
    skeleton sidecars, plus the ``right45`` three-quarter the sheet is conditioned on.
    """
    from content_factory.controls.asset_review import REQUIRED_VIEWS
    from tests.unit.test_reviews import JOINTS

    asset_dir = tmp_path / "characters" / "man_01"
    asset_dir.mkdir(parents=True)
    (asset_dir / "man_01.asset.json").write_text(
        json.dumps({"name": "man_01", "blend_sha256": "a" * 64, "height_m": 1.78})
    )
    for view in (*REQUIRED_VIEWS, "left45", "right45"):
        view_dir = asset_dir / "turnaround" / view
        frames = view_dir / "rough_rgb" / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (140, 140, 140)).save(frames / "0000.png")
        layout = view_dir / "layout" / "frames"
        layout.mkdir(parents=True, exist_ok=True)
        (layout / "0000.json").write_text(
            json.dumps({"objects": [{"object_id": "subject", "visible_fraction": 1.0}]})
        )
        joints = {
            joint: {
                "x": 0.5 - 0.15 if joint.startswith("l_") else (0.65 if joint[0] == "r" else 0.5),
                "y": 0.5,
                "z": 4.0,
                "in_frame": True,
                "visible": True,
            }
            for joint in JOINTS
        }
        skeleton = view_dir / "skeleton" / "frames"
        skeleton.mkdir(parents=True, exist_ok=True)
        (skeleton / "0000.json").write_text(
            json.dumps({"people": [{"character_id": "subject", "joints": joints}]})
        )
    return asset_dir


def test_the_slug_keeps_a_preset_readable_and_a_written_out_style_unique() -> None:
    from content_factory.sequences.styles import STYLE_PRESETS

    assert style_slug("watercolour") == "watercolour"
    assert style_slug(STYLE_PRESETS["watercolour"]) == "watercolour"
    written = "chalk pastel on black sugar paper, smudged, no outlines"
    slug = style_slug(written)
    assert slug != "watercolour" and slug.isascii() and " " not in slug
    # Two long styles that share a prefix must not share a filename.
    other = style_slug("chalk pastel on black sugar paper, crisp, hard outlines")
    assert slug != other
    assert style_slug(written) == slug  # stable


def test_the_prompt_leads_with_the_style_and_asks_for_two_clothed_views() -> None:
    prompt = sheet_prompt(style="watercolour and ink", appearance="a man in a grey work coat")
    assert prompt.startswith("watercolour and ink")
    assert "front view on the left" in prompt and "three-quarter view on the right" in prompt
    assert "grey work coat" in prompt
    assert "no text" in prompt and "no measurement lines" in prompt
    # Without an appearance it still asks for clothes rather than leaving it open.
    assert "fully clothed" in sheet_prompt(style="watercolour")


def test_a_sheet_is_built_from_the_assets_own_views_and_cached_by_the_mesh(asset: Path) -> None:
    backend = FakeImageModel()
    built = build_sheet(asset, style="watercolour", backend=backend, seed=7)
    assert built.cache_hit is False
    assert built.path == asset / "sheets" / "watercolour.png"
    assert built.path.exists()
    # The asset's OWN turnaround renders conditioned it, so it is the same body.
    assert backend.calls[0]["refs"] == 2 and backend.calls[0]["seed"] == 7
    assert built.sheet.views == ("front", "right45")
    assert built.sheet.blend_sha256 == "a" * 64
    assert built.sheet.prompt_version == SHEET_PROMPT_VERSION
    assert built.sheet.png_sha256 == sha256_hex(built.path.read_bytes())

    # Idempotent: nothing regenerated, and the same record comes back.
    again = build_sheet(asset, style="watercolour", backend=backend, seed=7)
    assert again.cache_hit is True and len(backend.calls) == 1
    assert again.sheet.png_sha256 == built.sheet.png_sha256

    # A different style, a different seed and a rebuilt mesh each rebuild it; nothing else does.
    build_sheet(asset, style="woodblock", backend=backend, seed=7)
    assert len(backend.calls) == 2
    build_sheet(asset, style="watercolour", backend=backend, seed=8)
    assert len(backend.calls) == 3
    (asset / "man_01.asset.json").write_text(
        json.dumps({"name": "man_01", "blend_sha256": "b" * 64, "height_m": 1.78})
    )
    rebuilt = build_sheet(asset, style="watercolour", backend=backend, seed=7)
    assert rebuilt.cache_hit is False and len(backend.calls) == 4


def test_a_missing_turnaround_view_is_named_rather_than_dropped(asset: Path) -> None:
    """A sheet built from the front alone is one the model had to invent the depth of, and it
    would be indistinguishable on disk from one that had both views."""
    import shutil

    shutil.rmtree(asset / "turnaround" / "right45")
    with pytest.raises(SheetError, match="right45"):
        build_sheet(asset, style="watercolour", backend=FakeImageModel())


def test_the_anchor_sends_the_sheet_and_never_the_clay_turnaround(
    asset: Path, tmp_path: Path, monkeypatch
) -> None:
    from content_factory.schemas.fixtures import sample_shot_plan
    from content_factory.workflows.stages import _identity_reference

    monkeypatch.setenv("CF__CONTROLS__ASSETS_ROOT", str(tmp_path))
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        built = build_sheet(asset, style="watercolour", backend=FakeImageModel(), seed=7)
        shot = sample_shot_plan().shots[0]
        assert shot.characters[0].asset == "man_01"

        # No digest on the plan: nothing is sent. The turnaround is on disk and is NOT a fallback.
        assert shot.characters[0].reference_image_sha256 == ()
        assert _identity_reference(shot, "man") is None
        clay = (asset / "turnaround" / "front" / "rough_rgb" / "frames" / "0000.png").read_bytes()

        planned = shot.model_copy(
            update={
                "characters": (
                    shot.characters[0].model_copy(
                        update={"reference_image_sha256": (built.sheet.png_sha256,)}
                    ),
                )
            }
        )
        found = _identity_reference(planned, "man")
        assert found is not None
        png, sha = found
        assert sha == built.sheet.png_sha256 and png != clay

        # A digest no sheet on disk matches sends nothing, rather than the nearest picture.
        stale = shot.model_copy(
            update={
                "characters": (
                    shot.characters[0].model_copy(update={"reference_image_sha256": ("c" * 64,)}),
                )
            }
        )
        assert _identity_reference(stale, "man") is None
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]


def test_plan_shots_names_the_sheet_and_review_assets_gates_it(
    asset: Path, tmp_path: Path, monkeypatch
) -> None:
    """The two ends of the same guarantee: the plan says which image, and the gate refuses a run
    that asks for an identity slot nothing approved can fill."""
    import datetime as dt

    from content_factory.runners.local import make_context
    from content_factory.schemas.assets import CharacterAssetReview
    from content_factory.workflows.stages import stage_plan_shots, stage_review_assets

    monkeypatch.setenv("CF__CONTROLS__ASSETS_ROOT", str(tmp_path))
    monkeypatch.setenv("CF__CONTROLS__ASSET_APPROVALS_DIR", str(tmp_path / "approvals"))
    monkeypatch.setenv("CF__SHOTS__PLANNER", "fixture")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        built = build_sheet(asset, style="watercolour", backend=FakeImageModel(), seed=7)
        assert sheet_sha_for_style(tmp_path, "man_01", "watercolour") == built.sheet.png_sha256
        assert [s.style_slug for s in sheets_on_disk(tmp_path, "man_01")] == ["watercolour"]

        ctx = make_context(project_dir=tmp_path / "run", brief={"topic": "a man on a bench"})
        object.__setattr__(ctx, "params", {"style": "watercolour"})
        out = stage_plan_shots(ctx)
        assert out.facts["identity_sheets"] == [built.sheet.png_sha256[:12]]
        plan = json.loads((ctx.ddir() / "shots" / "plan.json").read_text())
        assert plan["shots"][0]["characters"][0]["reference_image_sha256"] == [
            built.sheet.png_sha256
        ]

        # A lane that asks for an identity slot is blocked until someone has approved the sheet.
        object.__setattr__(ctx, "params", {"style": "watercolour", "references": "identity,depth"})
        with pytest.raises(RuntimeError, match="nobody has approved it"):
            stage_review_assets(ctx)

        review = CharacterAssetReview.model_validate_json(
            (ctx.project_dir / "reviews" / "assets" / "man_01.review.json").read_text()
        )
        assert [s.style_slug for s in review.sheets] == ["watercolour"]
        approvals = tmp_path / "approvals"
        approvals.mkdir(parents=True, exist_ok=True)
        (approvals / "man_01.json").write_text(
            review.model_copy(
                update={"approved_by": "operator", "approved_at": dt.datetime.now(dt.UTC)}
            ).model_dump_json(indent=1)
        )
        gate = stage_review_assets(ctx)
        assert gate.facts["identity_references"] is True
        assert gate.facts["identity_sheets"] == [built.sheet.png_sha256[:12]]

        # Redraw the sheet and the approval no longer covers it: a new picture is unreviewed.
        _png, marker = sheet_paths(tmp_path, "man_01", "watercolour")
        marker.unlink()
        build_sheet(asset, style="watercolour", backend=FakeImageModel(), seed=11)
        with pytest.raises(RuntimeError, match="not the one that was approved"):
            stage_review_assets(ctx)

        # And a lane that sends no identity reference does not care about any of this.
        object.__setattr__(ctx, "params", {"style": "watercolour", "references": "pose_skeleton"})
        assert stage_review_assets(ctx).facts["identity_references"] is False
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]
