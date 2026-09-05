"""Render an AnimationSpec with Manim (opt-in skill; the builtin Pillow renderer needs nothing).

Usage:
    uv run --project skills/video/manim python skills/video/manim/render.py <spec.json> <out_dir>

Writes the same frames/0000.png… layout as the builtin renderer, so downstream packaging is
identical whichever renderer produced the frames. Equations use MathTex when LaTeX is installed
and fall back to Text otherwise.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text())
    out_dir = Path(sys.argv[2])
    from manim import DOWN, UP, WHITE, Create, FadeIn, MathTex, Scene, Text, VGroup, config

    config.pixel_width = spec["width"]
    config.pixel_height = spec["height"]
    config.frame_rate = spec["fps"]
    config.output_file = "animation"
    config.media_dir = str(out_dir / "manim-media")
    run_time = spec["duration_ms"] / 1000
    # Every glyph route must degrade to Text when the host has no LaTeX, or the skill is
    # unusable without a texlive install (DecimalNumber typesets through MathTex by default).
    has_latex = shutil.which("latex") is not None

    class AnimationScene(Scene):
        def construct(self) -> None:
            title = Text(spec["title"], font_size=28, color=WHITE).to_edge(UP)
            self.add(title)
            if spec["kind"] == "count_up":
                from manim import DecimalNumber, ValueTracker

                tracker = ValueTracker(0)
                number = DecimalNumber(
                    0,
                    num_decimal_places=1,
                    font_size=96,
                    **({} if has_latex else {"mob_class": Text}),
                )
                number.add_updater(lambda m: m.set_value(tracker.get_value()))
                self.add(number)
                self.play(tracker.animate.set_value(spec["value"]), run_time=run_time)
            else:
                items = []
                use_math = spec["kind"] == "equation" and has_latex
                for step in spec["steps"]:
                    try:
                        items.append(MathTex(step) if use_math else Text(step))
                    except Exception:  # LaTeX present but this expression failed to typeset
                        items.append(Text(step))
                group = VGroup(*items).arrange(direction=DOWN, buff=0.5)
                per = run_time / max(1, len(items))
                for item in group:
                    self.play(
                        FadeIn(item) if spec["kind"] == "equation" else Create(item), run_time=per
                    )

    AnimationScene().render()
    # Manim writes an mp4; explode it into the shared frames/ layout for parity.
    import subprocess

    frames = out_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    movie = next((out_dir / "manim-media").rglob("animation.mp4"))
    subprocess.run(  # noqa: S603
        # -start_number 0: the builtin renderer writes frames/0000.png…, and the two layouts
        # must stay indistinguishable downstream (ffmpeg would otherwise start at 0001.png).
        ["ffmpeg", "-y", "-i", str(movie), "-start_number", "0", str(frames / "%04d.png")],
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
