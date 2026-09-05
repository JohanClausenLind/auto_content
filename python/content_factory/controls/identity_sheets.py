"""Styled identity sheets: the same person, in the film's own style, clothed.

The mesh is not a usable identity reference and that was measured rather than guessed. HiDream-O1's
IP pipeline treats *every* reference as subject material, so:

* the Blender clay render as an identity reference makes it draw clay people;
* an untextured MPFB turnaround makes it draw a nude mannequin.

Which is why ``ImageSequenceSettings.anchor_references`` says, in its own comment, to add
``identity`` only once the character has a styled sheet — and until this module there was no way to
make one, so the slot was unusable and identity was carried by nothing at all. Across one
thirty-anchor run HiDream held one world in every frame and changed the character's outfit four
times inside it (STATUS 1339, 1379-1381, 1627).

A sheet is one image per character per style: front and three-quarter views of the same clothed
figure on a plain ground, generated from the asset's **own** turnaround renders so it is the same
body rather than a plausible stranger. It lives beside the asset it belongs to
(``<assets>/characters/<asset>/sheets/<style>.png``) rather than inside a run, for the same reason
the ``.blend`` approval does: it is a standing statement about that character, not a fact about one
film. It is cached by ``(blend sha256, style, seed, backend, prompt version)`` and reviewed through
``review_assets`` exactly like the mesh.

Nothing here is a lane node. Building a sheet is an asset build step — one call per character per
style, run when the asset is built or the film's style changes, not once per run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from content_factory.schemas.assets import IdentitySheet
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.sequences import GenerationLock

SHEET_PROMPT_VERSION = "0.1.0"
"""Bumped when :func:`sheet_prompt` changes wording. It is inside the cache key, so a reworded
prompt rebuilds the sheets it changes and nothing else."""

DEFAULT_VIEWS: tuple[str, ...] = ("front", "right45")
"""Front plus three-quarter. One view gives the model no way to know the body's depth, and the
turnaround already renders both — ``right90`` adds a profile that mostly costs reference budget."""

SHEET_SIZE = (1024, 1024)
"""Square, because the sheet holds two views side by side and neither should be letterboxed."""


class SheetError(RuntimeError):
    """The sheet cannot be built. The message always names the asset and what is missing."""


class TextToImage(Protocol):
    """What a sheet builder needs from a backend: text plus reference images in, one PNG out.

    Deliberately narrower than ``ReferenceEditBackend``: a sheet is not an edit of an anchor and it
    is not part of a sequence, so nothing here should be able to reach the sequence engine.
    """

    name: str

    def generate(self, prompt: str, conditioning, lock: GenerationLock, *, seed: int) -> bytes: ...


def style_slug(style: str) -> str:
    """A stable filename stem for a style prompt.

    A preset name passes through as itself, which keeps the common case readable on disk
    (``sheets/watercolour.png``). Anything longer — a style written out in full — becomes a digest
    prefix, because a 200-character prompt is not a filename and truncating it would collide.
    """
    from content_factory.sequences.styles import STYLE_PRESETS

    cleaned = " ".join(style.split())
    for name, prompt in STYLE_PRESETS.items():
        if cleaned in (name, prompt):
            return name
    slug = "".join(c if c.isalnum() else "_" for c in cleaned.lower())[:24].strip("_")
    digest = hashlib.sha256(cleaned.encode()).hexdigest()[:8]
    return f"{slug}_{digest}" if slug else f"style_{digest}"


def sheets_dir(assets_root: Path | str, asset: str) -> Path:
    return Path(assets_root) / "characters" / asset / "sheets"


MARKER_SUFFIX = ".done.json"


def sheet_paths(assets_root: Path | str, asset: str, style: str) -> tuple[Path, Path]:
    """``(png, marker)`` for one asset's sheet in one style.

    Built by name rather than with ``with_suffix``: a slug ending in a digest has no dot in it, but
    ``Path("watercolour.done.json").with_suffix(".png")`` is ``watercolour.done.png``, which is how
    the reverse lookup silently found nothing the first time this was written.
    """
    base = sheets_dir(assets_root, asset)
    slug = style_slug(style)
    return base / f"{slug}.png", base / f"{slug}{MARKER_SUFFIX}"


def sheet_prompt(*, style: str, appearance: str | None = None) -> str:
    """What the sheet is asked for. The style leads, as it does for every anchor.

    Position is not cosmetic: with the style clause behind the subject the image model returned its
    default idiom for every art direction (STATUS, ``_anchor_prompt``). The rest is deliberately
    dull — a reference image wants no drama, no camera move and no scene, because everything in it
    that is not the person is something the anchor will inherit.
    """
    parts = [
        " ".join(style.split()),
        "character reference sheet of one person, two views side by side:"
        " front view on the left, three-quarter view on the right",
        " ".join(appearance.split()) if appearance else "fully clothed in plain everyday clothes",
        "standing straight, arms relaxed at the sides, neutral expression",
        "plain flat background, even frontal light, no shadows on the ground",
        "the same person in both views, identical clothing and hair",
        "no text, no labels, no measurement lines, no borders, no grid",
    ]
    return ". ".join(p.rstrip(".") for p in parts if p) + "."


@dataclass(frozen=True)
class SheetBuild:
    sheet: IdentitySheet
    path: Path
    cache_hit: bool


def _asset_facts(asset_dir: Path) -> dict:
    built = next(asset_dir.glob("*.asset.json"), None)
    if built is None:
        msg = f"{asset_dir} has no <name>.asset.json — was the asset built?"
        raise SheetError(msg)
    return json.loads(built.read_text())


def _view_pngs(asset_dir: Path, views: tuple[str, ...]) -> list[bytes]:
    """The turnaround's own rough RGB renders for ``views``, in order.

    The sheet is conditioned on the asset's renders so it is the same body. Missing a view is an
    error rather than a silent drop: a sheet built from the front alone is a sheet the model had to
    invent the depth of, and it would be indistinguishable on disk from one that had both.
    """
    out: list[bytes] = []
    for view in views:
        png = asset_dir / "turnaround" / view / "rough_rgb" / "frames" / "0000.png"
        if not png.is_file():
            msg = (
                f"{asset_dir.name} has no {view} turnaround render at {png} —"
                " rebuild the asset (assets_build/render_turnaround.py) before its sheet"
            )
            raise SheetError(msg)
        out.append(png.read_bytes())
    return out


def build_sheet(
    asset_dir: Path,
    *,
    style: str,
    backend: TextToImage,
    seed: int = 0,
    appearance: str | None = None,
    views: tuple[str, ...] = DEFAULT_VIEWS,
    size: tuple[int, int] = SHEET_SIZE,
) -> SheetBuild:
    """Build (or return from cache) one asset's identity sheet in one style.

    Cached by ``(blend sha256, style, seed, backend, views, prompt version)``: rebuilding the mesh
    or changing the film's style rebuilds the sheet, and nothing else does. Idempotent and safe to
    run twice, like every other cached step here.
    """
    from content_factory.sequences.engine import ControlConditioning

    facts = _asset_facts(asset_dir)
    asset = str(facts["name"])
    blend_sha = str(facts["blend_sha256"])
    png_path, marker = sheet_paths(asset_dir.parent.parent, asset, style)
    input_hash = sha256_hex(
        json.dumps(
            {
                "blend": blend_sha,
                "style": " ".join(style.split()),
                "seed": seed,
                "backend": backend.name,
                "views": list(views),
                "appearance": appearance or "",
                "prompt_version": SHEET_PROMPT_VERSION,
                "size": list(size),
            },
            sort_keys=True,
        ).encode()
    )
    if marker.exists() and png_path.exists():
        record = json.loads(marker.read_text())
        if record.get("input_hash") == input_hash:
            return SheetBuild(IdentitySheet.model_validate(record["sheet"]), png_path, True)

    prompt = sheet_prompt(style=style, appearance=appearance)
    lock = GenerationLock(
        workflow_package_id="identity.sheet",
        workflow_package_version="1.0.0",
        model_revision=backend.name,
        width=size[0],
        height=size[1],
        seed=seed,
        sampler="flow_match",
        steps=28,
        guidance=0.0,
        style_prompt=" ".join(style.split()),
        camera_prompt="flat orthographic reference views",
        lighting_prompt="even frontal light",
        background_prompt="plain flat background",
        reference_asset_sha256=blend_sha,
    )
    png = backend.generate(
        prompt,
        ControlConditioning(reference_pngs=tuple(_view_pngs(asset_dir, views))),
        lock,
        seed=seed,
    )
    sheet = IdentitySheet(
        style=" ".join(style.split()),
        style_slug=style_slug(style),
        png_sha256=sha256_hex(png),
        blend_sha256=blend_sha,
        seed=seed,
        backend=backend.name,
        prompt_version=SHEET_PROMPT_VERSION,
        views=views,
    )
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.write_bytes(png)
    marker.write_text(
        json.dumps(
            {"input_hash": input_hash, "prompt": prompt, "sheet": sheet.model_dump(mode="json")},
            indent=1,
            sort_keys=True,
        )
    )
    return SheetBuild(sheet, png_path, False)


def sheets_on_disk(assets_root: Path | str, asset: str) -> tuple[IdentitySheet, ...]:
    """Every sheet built for one asset, in slug order. Unreadable markers are skipped."""
    found: list[IdentitySheet] = []
    for marker in sorted(sheets_dir(assets_root, asset).glob(f"*{MARKER_SUFFIX}")):
        try:
            record = json.loads(marker.read_text())
            sheet = IdentitySheet.model_validate(record["sheet"])
        except (ValueError, KeyError, TypeError):
            continue
        png = marker.with_name(marker.name.removesuffix(MARKER_SUFFIX) + ".png")
        if png.exists():
            found.append(sheet)
    return tuple(found)


def load_sheet_by_sha(assets_root: Path | str, asset: str, sha256: str) -> bytes | None:
    """The sheet bytes with this digest, or None.

    Addressed by digest rather than by style so a ``ShotSpec`` names the exact image it was planned
    against: a sheet rebuilt in the same style is a different picture and must not be served for a
    plan that was made from the old one.
    """
    for sheet in sheets_on_disk(assets_root, asset):
        if sheet.png_sha256 == sha256:
            png = sheets_dir(assets_root, asset) / f"{sheet.style_slug}.png"
            data = png.read_bytes()
            if sha256_hex(data) == sha256:
                return data
    return None


def sheet_sha_for_style(assets_root: Path | str, asset: str, style: str) -> str | None:
    """The digest of the sheet for ``(asset, style)``, or None when none has been built."""
    slug = style_slug(style)
    return next(
        (s.png_sha256 for s in sheets_on_disk(assets_root, asset) if s.style_slug == slug), None
    )
