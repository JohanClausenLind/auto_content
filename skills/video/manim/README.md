# Manim animation skill (opt-in)

Renders `AnimationSpec` contracts with Manim's mathematical typesetting. The pipeline's default
is the builtin Pillow renderer (`content_factory/animation/builtin.py`), which needs nothing;
switch with `CF__ANIMATION__EXECUTOR=manim` once this env exists.

## Setup (once)

```bash
uv sync --project skills/video/manim
# optional — real mathematical typesetting (MathTex); without it every glyph renders as Text:
sudo apt-get install texlive texlive-latex-extra
```

Done on vegaserv 2026-09-06 (manim 0.19.0, no texlive). `manim checkhealth` passes apart from the
LaTeX check. LaTeX is genuinely optional here: `render.py` probes for `latex` on PATH and routes
**every** glyph through `Text` when it is missing — including `DecimalNumber`, which typesets via
`MathTex` by default and would otherwise make `count_up` fail on a host without texlive.

## Run

```bash
uv run --project skills/video/manim python skills/video/manim/render.py spec.json out/
```

Output layout matches the builtin renderer exactly: `out/frames/0000.png…`.
