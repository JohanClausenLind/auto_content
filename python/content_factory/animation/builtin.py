"""Builtin animation renderer: deterministic Pillow frames from an AnimationSpec.

Covers the explainer basics offline — a counting number, an equation revealed line by line, a
diagram built box by box — with zero dependencies beyond Pillow. The opt-in Manim skill
(skills/video/manim) renders the same contract with real mathematical typesetting; both write
identical frame layouts (frames/0000.png…) so downstream packaging cannot tell them apart.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from content_factory.schemas.animation import AnimationSpec

BG = (16, 16, 18)
FG = (235, 235, 235)
MUTED = (150, 150, 155)
ACCENT = (86, 156, 233)


def _font(size: int):
    from PIL import ImageFont

    try:  # a deterministic bundled font beats system lookup, but default always exists
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow: no size kwarg
        return ImageFont.load_default()


def _center_text(draw: ImageDraw.ImageDraw, text: str, y: int, width: int, size: int, fill) -> None:
    font = _font(size)
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)


def _ease(t: float) -> float:
    return 1 - (1 - t) ** 3


def _draw_count_up(draw: ImageDraw.ImageDraw, spec: AnimationSpec, t: float) -> None:
    assert spec.value is not None
    shown = spec.value * _ease(t)
    decimals = 0 if float(spec.value).is_integer() else 1
    _center_text(draw, spec.title, int(spec.height * 0.18), spec.width, spec.height // 16, MUTED)
    _center_text(
        draw,
        f"{shown:,.{decimals}f}{(' ' + spec.unit) if spec.unit else ''}",
        int(spec.height * 0.4),
        spec.width,
        spec.height // 5,
        FG,
    )


def _draw_steps(draw: ImageDraw.ImageDraw, spec: AnimationSpec, t: float, boxed: bool) -> None:
    _center_text(draw, spec.title, int(spec.height * 0.1), spec.width, spec.height // 18, MUTED)
    revealed = max(1, min(len(spec.steps), int(_ease(t) * len(spec.steps)) + 1))
    top = int(spec.height * 0.24)
    row = int(spec.height * 0.62 / max(1, len(spec.steps)))
    for index, step in enumerate(spec.steps[:revealed]):
        y = top + index * row
        if boxed:
            margin = spec.width // 6
            draw.rounded_rectangle(
                (margin, y, spec.width - margin, y + int(row * 0.8)),
                radius=10,
                outline=ACCENT,
                width=3,
            )
            _center_text(draw, step, y + int(row * 0.22), spec.width, spec.height // 24, FG)
            if index > 0:
                x = spec.width // 2
                draw.line((x, y - int(row * 0.2), x, y), fill=MUTED, width=3)
        else:
            _center_text(draw, step, y, spec.width, spec.height // 20, FG)


def render_animation_frames(spec: AnimationSpec, out_dir: Path) -> list[Path]:
    """Render every frame as PNG (frames/0000.png…). Pure function of the spec."""
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    total = max(1, round(spec.duration_ms * spec.fps / 1000))
    paths: list[Path] = []
    for index in range(total):
        t = index / max(1, total - 1)
        image = Image.new("RGB", (spec.width, spec.height), BG)
        draw = ImageDraw.Draw(image)
        if spec.kind == "count_up":
            _draw_count_up(draw, spec, t)
        elif spec.kind == "equation":
            _draw_steps(draw, spec, t, boxed=False)
        else:
            _draw_steps(draw, spec, t, boxed=True)
        path = frames_dir / f"{index:04d}.png"
        image.save(path, "PNG")
        paths.append(path)
    return paths
