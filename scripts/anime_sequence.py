"""A one-minute limited-animation film at 5 fps, drawn entirely by Ideogram 4."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

FPS = 5
WIDTH, HEIGHT = 1024, 576
BOX_SCALE = 1000  # Ideogram boxes are 0-1000 on both axes, never pixels
SLOTS_PER_SHOT = 25  # 5 s a shot; 12 shots = 300 slots = 60.0 s
HOLDS = (3, 2, 3, 2, 3, 2, 3, 2, 3, 2)  # 25 slots from 10 drawings: anime on twos and threes

# The character sheet, repeated verbatim in every caption: a named hair shape and one saturated
# garment colour each survive a redraw. CAPITALISED names get lettered in-frame at a low rate.
KAEDE = (
    "KAEDE, a young woman with shoulder-length copper-red hair held back by a small brass clip, "
    "in a rust-orange knee-length coat over a cream high-neck top, dark navy trousers and tan "
    "ankle boots"
)
TORU = (
    "TORU, a young man of the same age with short black hair and a straight fringe, in a "
    "charcoal-grey jacket over a pale grey shirt, dark trousers and brown boots"
)
CAST = (
    f"{KAEDE}. {TORU}. Both are drawn in the same anime style, with the same proportions and the "
    "same faces."
)

PALETTE = (
    "a small fixed palette: sea blue, sky blue, cream cloud white, meadow green, warm sand, "
    "rust orange, charcoal grey"
)
# Scenic, and one world across all twelve shots: the same coast seen from different points along
# one path, so a cut changes the view without changing the place.
WORLD = (
    "a grassy clifftop path high above a calm blue sea, distant green headlands and small islands "
    "on the horizon, tall soft cumulus clouds, wildflowers and long grass along the path"
)

# `solo` is what ONE character's body is doing. Say where a hand IS, never how far it reaches: an
# outstretched-limb posture cost shot02 and shot03 7 cels of 10 each (journal 2026-09-13).
SHOTS: list[dict] = [
    {
        "clip": "cmu_18_19_09",
        "solo": (
            "walking, one hand raised near her own shoulder",
            "walking, head turned a little to one side",
        ),
        "act": "walking together, talking quietly",
        "bg": f"{WORLD}; the path running along the clifftop, sea filling the distance",
        "az": 35.0,
    },
    {
        "clip": "cmu_20_21_04",
        "solo": ("walking, arms relaxed at her sides", "walking, arms relaxed at his sides"),
        "act": "walking side by side in step along the path",
        "bg": f"{WORLD}; a long straight stretch of path, grass bending in the wind",
        "az": 100.0,
    },
    {
        "clip": "cmu_18_19_01",
        "solo": (
            "standing, right hand held at waist height",
            "standing, right hand held at waist height",
        ),
        "act": "meeting and shaking hands",
        "bg": f"{WORLD}; a wide grassy overlook, the sea far below",
        "az": 35.0,
    },
    {
        "clip": "cmu_20_21_11",
        "solo": ("standing, one hand up beside her head", "standing, one hand up beside his head"),
        "act": "a high five, hands meeting above their heads",
        "bg": f"{WORLD}; the path beside a weathered wooden fence",
        "az": -40.0,
    },
    {
        "clip": "cmu_18_19_07",
        "solo": ("walking, near hand held low", "walking, near hand held low"),
        "act": "walking close together along the path",
        "bg": f"{WORLD}; the path narrowing between banks of long grass",
        "az": 60.0,
    },
    {
        "clip": "cmu_20_21_02",
        "solo": (
            "walking, near elbow bent close to her side",
            "walking, near elbow bent close to his side",
        ),
        "act": "arms linked, walking together",
        "bg": f"{WORLD}; a bend in the path with a white lighthouse small on a far headland",
        "az": 115.0,
    },
    {
        "clip": "cmu_22_23_08",
        "solo": ("walking, near hand held low", "walking, near hand held low"),
        "act": "walking hand in hand along the path",
        "bg": f"{WORLD}; wooden steps set into the slope, sea beyond",
        "az": -25.0,
    },
    {
        "clip": "cmu_22_23_12",
        "solo": ("stepping sideways, shoulders turned", "standing firm, shoulders turned"),
        "act": "one stumbling into the other, both catching their balance",
        "bg": f"{WORLD}; a rocky patch where the path dips",
        "az": 20.0,
    },
    {
        "clip": "cmu_20_21_05",
        "solo": ("kneeling in the grass, hands in her lap", "crouching, weight back on his heels"),
        "act": "standing together, looking out at the sea",
        "bg": f"{WORLD}; a flat grassy shelf above the cliff edge",
        "az": -60.0,
    },
    {
        "clip": "cmu_22_23_04",
        "solo": (
            "standing, head lowered, hands clasped low",
            "standing, one hand raised to shoulder height",
        ),
        "act": "one resting a hand on the other's shoulder to comfort them",
        "bg": f"{WORLD}; the overlook at the end of the path, sun low over the water",
        "az": -60.0,
    },
    {
        "clip": "cmu_22_23_10",
        "solo": (
            "standing, shoulders hunched, head down",
            "standing close, coat lifting in the wind",
        ),
        "act": "one sheltering the other from the wind",
        "bg": f"{WORLD}; the exposed cliff edge, clouds piling up behind",
        "az": 75.0,
    },
    {
        "clip": "cmu_20_21_10",
        "solo": (
            "turning on the spot, coat swinging out",
            "turning on the spot, coat swinging out",
        ),
        "act": "spinning round on the spot, laughing",
        "bg": f"{WORLD}; the wide grassy overlook again, sea and sky open behind",
        "az": 0.0,
    },
]


def root() -> Path:
    return REPO / "output" / "anime"


def shot_dir(i: int) -> Path:
    return root() / f"shot{i:02d}"


def action_at(shot: dict, t: float) -> str:
    """The action clause at position ``t`` (0-1) through the take."""
    stage_words = ("just starting", "part-way through", "at the height of it", "finishing")
    where = stage_words[min(int(t * len(stage_words)), len(stage_words) - 1)]
    return f"{shot['act']}, {where}"


def stage() -> None:
    """Blender once per shot, for the layout boxes only."""
    import make_mocap_shot_plan as mmsp

    from content_factory.config import get_settings
    from content_factory.controls.blender import run_blender_scene
    from content_factory.controls.bundle import build_control_bundle
    from content_factory.schemas.shots import ShotSpec

    cfg = get_settings().controls
    drawings = len(HOLDS)
    for i, shot in enumerate(SHOTS):
        work = shot_dir(i)
        if (work / "boxes.json").exists():
            print(f"[{i:02d}] cached", flush=True)
            continue
        clip = mmsp.load(shot["clip"])
        total = int(clip["frame_count"])
        picked = [round(k * (total - 1) / (drawings - 1)) for k in range(drawings)]
        camera = mmsp.solve_camera(
            clip,
            {
                "azimuth_deg": shot["az"],
                "elevation_deg": 6.0,
                # Large on purpose: at 0.62 body height Ideogram drew 4, 7 and 10 figures for two
                # boxes (2026-09-13). It cannot count, so leave it nowhere to put the extras.
                "body_fraction": 0.82,
                "lens_mm": 45.0,
            },
        )
        doc = {
            "schema_version": 1,
            "shot_id": f"sht_anime{i:02d}000001",
            "order": i,
            "beat_id": f"bea_anime{i:02d}000001",
            "seed": 4242 + i,
            "frame_count": total,
            "fps": int(clip.get("fps", 24)),
            "width": WIDTH,
            "height": HEIGHT,
            "camera": camera,
            "characters": [
                {
                    "id": cid,
                    "asset": asset,
                    "appearance": mmsp.APPEARANCE[asset],
                    "transform": {"position": [0.0, 0.0, 0.0], "yaw_deg": 0.0, "scale": 1.0},
                    "pose": {
                        "kind": "segments",
                        "name": shot["clip"],
                        "actor": actor,
                        "speed": 1.0,
                        "offset_frames": 0,
                        "loop": False,
                    },
                    "seg_id": n + 1,
                    "reference_image_sha256": [],
                }
                for n, (cid, asset, actor) in enumerate(
                    [("man", "man_01", "a"), ("woman", "woman_01", "b")]
                )
            ],
            "props": [],
            "environment": {
                "background_color": [0.07, 0.08, 0.11],
                "ground": {"enabled": True, "color": [0.3, 0.3, 0.33], "size": 60.0, "seg": False},
                "walls": None,
            },
            "lighting": {
                "preset": "exterior_dusk",
                "key_azimuth_deg": round(shot["az"] + 140.0, 1),
                "key_elevation_deg": 22.0,
                "intensity": 1.0,
                "shadows": True,
            },
            "render": {
                "engine": "workbench",
                "passes": ["layout_boxes"],
                "depth_range": "auto",
                "canny": {"low": 100, "high": 200},
                "frames": picked,
            },
            "anchor_frames": [picked[0]],
            "motion_prompt": shot["act"],
        }
        spec = ShotSpec.model_validate(doc)
        work.mkdir(parents=True, exist_ok=True)
        spec_path = work / "shot_spec.json"
        spec_path.write_text(spec.model_dump_json(indent=1))
        run_blender_scene(
            spec_path,
            work,
            blender_bin=cfg.blender_bin,
            engine=cfg.engine,
            assets_root=Path(cfg.assets_root),
            timeout_s=cfg.timeout_s,
        )
        bundle = build_control_bundle(spec, work)
        # ONE BOX PER CHARACTER, never a union box: the union shape came back with seven and ten
        # figures (2026-09-13). Two named elements are the only count Ideogram has ever obeyed.
        boxes = []
        for frame in picked:
            per = {
                track.subject_id: box
                for track in bundle.subjects
                if frame < len(track.layouts) and (box := track.layouts[frame]) is not None
            }
            frame_boxes = {}
            for who, b in per.items():
                frame_boxes[who] = [round(v * BOX_SCALE) for v in (b.x, b.y, b.x + b.w, b.y + b.h)]
            boxes.append(
                frame_boxes or {"woman": [250, 150, 450, 950], "man": [550, 150, 750, 950]}
            )
        (work / "boxes.json").write_text(json.dumps(boxes, indent=1))
        print(f"[{i:02d}] {shot['clip']}: {len(boxes)} boxes", flush=True)


def caption(shot: dict, index: int, boxes: dict[str, list[int]]) -> str:
    """One JSON caption, in the only shape measured to hold the count reasonably often."""
    from content_factory.sequences.styles import STYLE_PRESETS

    act = action_at(shot, index / max(len(HOLDS) - 1, 1))
    woman_solo, man_solo = shot["solo"]
    elements = []
    for who, sheet, solo, side in (
        ("woman", KAEDE, woman_solo, "on the left"),
        ("man", TORU, man_solo, "on the right"),
    ):
        box = boxes.get(who)
        if box:
            elements.append(
                {"description": f"{sheet}, standing close {side}, {solo}", "bounding_box": box}
            )
    return json.dumps(
        {
            "high_level_description": (
                "A single frame of a cel-animated anime film. Exactly two people, close together "
                f"in the middle of the frame, {act}. There is nobody else in the picture."
            ),
            "style_description": {
                "medium": "cel animation frame from an anime film",
                "aesthetics": STYLE_PRESETS["anime_limited"],
                "lighting": "flat daylight, one soft shadow shape per figure, bright open sky",
                "palette": PALETTE,
            },
            "compositional_deconstruction": {
                "background": shot["bg"],
                "elements": elements,
            },
        },
        indent=1,
    )


def draw(endpoint: str, *, offset: int = 0) -> None:
    """Every unique drawing, once, cached by file."""
    from consistency_probe import IDEOGRAM_SEEDS

    from content_factory.qc.frame_review import blank_panel_fraction, is_refusal_frame
    from content_factory.schemas.sequences import GenerationLock
    from content_factory.sequences.ideogram_backend import Ideogram4Backend

    backend = Ideogram4Backend(workdir=root() / "_work", endpoint=endpoint)
    began_all = time.monotonic()
    drawn = 0
    for i, shot in enumerate(SHOTS):
        boxes = json.loads((shot_dir(i) / "boxes.json").read_text())
        cels = shot_dir(i) / "cels"
        cels.mkdir(parents=True, exist_ok=True)
        for k in range(len(HOLDS)):
            dest = cels / f"{k:02d}.png"
            if dest.exists():
                continue
            text = caption(shot, k, boxes[k])
            for seed in IDEOGRAM_SEEDS[offset:] + IDEOGRAM_SEEDS[:offset]:
                lock = GenerationLock(
                    workflow_package_id="anm_minute0001",
                    workflow_package_version="1.0.0",
                    model_revision="ideogram4",
                    width=WIDTH,
                    height=HEIGHT,
                    seed=seed,
                    sampler="euler",
                    steps=20,
                    guidance=7.0,
                    style_prompt="anime_limited",
                    camera_prompt="side view, eye level",
                    lighting_prompt="flat daylight",
                    background_prompt=shot["bg"][:200],
                    reference_asset_sha256="0" * 64,
                )
                began = time.monotonic()
                png = backend.text_to_image(text, lock)
                bad = is_refusal_frame(png) or blank_panel_fraction(png) > 0.30
                print(
                    f"  [{i:02d}/{k:02d}] seed {seed:>9} {time.monotonic() - began:5.0f}s"
                    f" {'REJECTED' if bad else 'ok'}",
                    flush=True,
                )
                if not bad:
                    dest.write_bytes(png)
                    drawn += 1
                    break
        print(
            f"[{i:02d}] {shot['clip']}: {len(list(cels.glob('*.png')))}/{len(HOLDS)} cels"
            f"  ({time.monotonic() - began_all:.0f}s elapsed)",
            flush=True,
        )
    print(f"drew {drawn} cels in {time.monotonic() - began_all:.0f}s", flush=True)


def sheets(cols: int = 5, cell: int = 390) -> list[Path]:
    """One contact sheet per shot, five across and two down."""
    from PIL import Image, ImageDraw

    made: list[Path] = []
    for i, shot in enumerate(SHOTS):
        paths = sorted((shot_dir(i) / "cels").glob("*.png"))
        if not paths:
            continue
        first = Image.open(paths[0])
        ch = round(cell * first.size[1] / first.size[0])
        rows = (len(paths) + cols - 1) // cols
        pad, head = 4, 15
        sheet = Image.new(
            "RGB", (cols * (cell + pad) + pad, rows * (ch + pad) + head + pad), (250, 249, 246)
        )
        draw = ImageDraw.Draw(sheet)
        draw.text((pad, 2), f"shot{i:02d}  {shot['clip']}  -  {shot['act']}", fill=(15, 15, 15))
        for n, path in enumerate(paths):
            x = pad + (n % cols) * (cell + pad)
            y = head + pad + (n // cols) * (ch + pad)
            sheet.paste(Image.open(path).convert("RGB").resize((cell, ch)), (x, y))
            draw.text((x + 3, y + 2), path.stem, fill=(255, 240, 0))
        dest = root() / f"review-shot{i:02d}.png"
        sheet.save(dest)
        made.append(dest)
    print(f"{len(made)} sheets -> {root()}/review-shot*.png")
    return made


def redraw(endpoint: str, targets: list[str]) -> None:
    """Replace named cels (``03:07`` = shot 3, cel 7) with a different seed."""
    for spec in targets:
        shot_s, cel_s = spec.split(":")
        dest = shot_dir(int(shot_s)) / "cels" / f"{int(cel_s):02d}.png"
        if dest.exists():
            dest.unlink()
    draw(endpoint, offset=3)


def _locked_palette(colours: int = 64, sample_every: int = 6):
    """The film's palette, taken from a composite of cels sampled across all twelve shots."""
    from PIL import Image

    cels = sorted(root().glob("shot*/cels/*.png"))
    if not cels:
        raise NothingDrawnError("no cels drawn yet")
    picked = cels[::sample_every][:20]
    cols = 5
    rows = (len(picked) + cols - 1) // cols
    grid = Image.new("RGB", (256 * cols, 144 * max(rows, 1)))
    for i, path in enumerate(picked):
        grid.paste(
            Image.open(path).convert("RGB").resize((256, 144)),
            ((i % cols) * 256, (i // cols) * 144),
        )
    return grid.quantize(colors=colours, method=Image.Quantize.MEDIANCUT)


class NothingDrawnError(RuntimeError):
    """Nothing has been drawn yet."""


def assemble(*, lock_palette: bool = True) -> Path:
    """300 slots at 5 fps: each drawing held 2 or 3, in shot order."""
    from PIL import Image

    out = root() / "frames"
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.png"):
        stale.unlink()
    reference = _locked_palette() if lock_palette else None
    slot = 0
    for i, _shot in enumerate(SHOTS):
        # A rejected cel is simply absent and its slots go to the drawing before it: a held drawing
        # is limited animation's own vocabulary, and the film stays exactly 60.0 s either way.
        held: Image.Image | None = None
        for k, hold in enumerate(HOLDS):
            src = shot_dir(i) / "cels" / f"{k:02d}.png"
            if src.exists():
                img = Image.open(src).convert("RGB")
                if reference is not None:
                    img = img.quantize(palette=reference, dither=Image.Dither.NONE).convert("RGB")
                held = img
            if held is None:
                continue  # nothing drawn yet in this shot; its slots are taken up by the next cel
            for _ in range(hold):
                held.save(out / f"{slot:04d}.png")
                slot += 1
        # If the shot opened with rejected cels, the slots it skipped are given back at the end so
        # every shot still runs its full five seconds.
        while held is not None and slot % SLOTS_PER_SHOT:
            held.save(out / f"{slot:04d}.png")
            slot += 1
    clip = root() / "anime-minute.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(out / "%04d.png"),
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            # 5 fps is the *look*; a 30 fps container with each frame repeated six times is what
            # every player and platform actually handles well.
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(clip),
        ],
        check=True,
    )
    print(f"{slot} slots at {FPS} fps = {slot / FPS:.1f}s -> {clip}")
    return clip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["stage", "draw", "sheets", "redraw", "assemble"])
    ap.add_argument("--endpoint", default="http://100.82.150.94:8188")
    ap.add_argument("--no-palette-lock", action="store_true")
    ap.add_argument("--cels", nargs="*", default=[], help="redraw targets like 03:07")
    args = ap.parse_args()
    if args.command == "stage":
        stage()
    elif args.command == "draw":
        draw(args.endpoint)
    elif args.command == "sheets":
        sheets()
    elif args.command == "redraw":
        redraw(args.endpoint, args.cels)
    else:
        assemble(lock_palette=not args.no_palette_lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
