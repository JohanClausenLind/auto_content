# Design system

> DRAFT 2026-09-15, derived from the editorial kit in `style/editorial.py` (dark navy-black
> field, warm off-white materials, cyan and amber semantic accents, coral only for failure), the
> existing Inter/Sora fonts and Okabe-Ito set in `packages/content-ui`, and the colour rules in the
> build brief. Needs the creator's review. The fenced `yaml` block is machine-readable and is the
> source the token generator reads; prose explains intent only.

## Colour

Three token families that never mix: **UI/ink**, **data**, **state**. OKLCH source values,
converted to sRGB at render time. No pure black. Hue never carries meaning alone at small sizes:
pair it with position, a label or luminance. Large gradients get 1–2 % dither. Entity colour is
bound to a stable `entity_id` at compile time by an allocator that checks the whole episode and
fails on confusable co-visible pairs.

```yaml
design_system_version: 1
color:
  ui:
    - { id: ui.surface.0, oklch: [0.18, 0.010, 260], role: canvas }
    - { id: ui.surface.1, oklch: [0.22, 0.012, 260], role: panel }
    - { id: ui.surface.2, oklch: [0.26, 0.014, 260], role: raised }
    - { id: ui.ink.primary, oklch: [0.96, 0.010, 80], role: read text }
    - { id: ui.ink.secondary, oklch: [0.82, 0.012, 80], role: labels }
    - { id: ui.ink.muted, oklch: [0.64, 0.012, 80], role: axes, captions }
    - { id: ui.stroke.soft, oklch: [0.34, 0.012, 260] }
    - { id: ui.stroke.strong, oklch: [0.48, 0.012, 260] }
  data:
    categorical:          # Okabe-Ito, at most six co-visible; order is allocation preference
      - { id: data.cat.blue, oklch: [0.532, 0.131, 244.0], srgb_origin: "#0072B2" }
      - { id: data.cat.orange, oklch: [0.753, 0.158, 76.8], srgb_origin: "#E69F00" }
      - { id: data.cat.green, oklch: [0.620, 0.130, 165.5], srgb_origin: "#009E73" }
      - { id: data.cat.purple, oklch: [0.679, 0.118, 346.3], srgb_origin: "#CC79A7" }
      - { id: data.cat.vermillion, oklch: [0.621, 0.170, 47.5], srgb_origin: "#D55E00" }
      - { id: data.cat.sky, oklch: [0.735, 0.117, 236.2], srgb_origin: "#56B4E9" }
    sequential:           # cividis (Nuñez, Anderton, Renslow 2018), nine anchors, interpolate in OKLCH
      - { t: 0.000, oklch: [0.252, 0.090, 257.1], srgb_origin: "#00204C" }
      - { t: 0.125, oklch: [0.331, 0.116, 256.6], srgb_origin: "#00336F" }
      - { t: 0.250, oklch: [0.405, 0.062, 266.0], srgb_origin: "#39486B" }
      - { t: 0.375, oklch: [0.477, 0.028, 272.4], srgb_origin: "#575C6D" }
      - { t: 0.500, oklch: [0.548, 0.003, 264.5], srgb_origin: "#707173" }
      - { t: 0.625, oklch: [0.622, 0.021, 97.0], srgb_origin: "#8A8779" }
      - { t: 0.750, oklch: [0.693, 0.056, 96.9], srgb_origin: "#A69D75" }
      - { t: 0.875, oklch: [0.770, 0.095, 98.0], srgb_origin: "#C4B56C" }
      - { t: 1.000, oklch: [0.927, 0.174, 101.8], srgb_origin: "#FFEA46" }
    diverging:            # blue – neutral – amber, symmetric in lightness
      - { t: 0.0, oklch: [0.45, 0.13, 244] }
      - { t: 0.5, oklch: [0.72, 0.005, 260] }
      - { t: 1.0, oklch: [0.75, 0.15, 77] }
  state:
    - { id: state.emphasis, oklch: [0.84, 0.150, 78], role: the one thing being talked about }
    - { id: state.deemphasis, alpha: 0.35, role: everything else while emphasis is active }
    - { id: state.uncertain, oklch: [0.72, 0.050, 80], pattern: hatch, role: estimated or forecast values }
    - { id: state.quote_highlight, rule: contrast, role: source-quote overlay, see below }
    - { id: state.error, oklch: [0.65, 0.180, 25], role: failure states only }
typography:
  faces: { display: Sora, text: Inter }     # bundled, pinned via @fontsource 5.3.0; no host fonts
  scale_px_at_1080:
    display: 72
    h1: 56
    h2: 40
    body: 34
    label: 28
    caption: 26
  floors_px_at_1080:
    read_text: 34        # narration-paired text a viewer must read
    label: 26            # axis ticks, entity labels, captions
  line_height: 1.25
  max_lines: { body: 4, label: 2 }
layout:
  canvas: { width: 1920, height: 1080, fps: 30 }
  safe_area_px: { x: 96, y: 54 }
  grid: { columns: 12, gutter: 24, unit: 8 }
  label_distance_max_px: 48      # a label is "near" its referent within this
lines:
  data_stroke_px: 4
  data_stroke_min_px_at_360: 3   # Gate C3 measures edge contrast at this width
  axis_stroke_px: 2
  grid_stroke_px: 1
motion:
  easing:
    standard: [0.2, 0.0, 0.0, 1.0]       # decelerate; entrances, highlights
    move: [0.4, 0.0, 0.2, 1.0]           # in-out; pans, zooms, position changes
  duration_ms:
    reveal: 400
    hide: 250
    highlight: 200
    move_short: 600
    move_long: 1200
    zoom: 900
  rules:
    - no overshoot or spring on data values
    - one camera move at a time; zoom factor at most 2.5
    - a value change is a linear interpolation with the standard easing, or a hard cut
    - hold at least the reading-time floor before the next action on the same target
```

## Quote highlight

The source-quote overlay colour is not a fixed hex. The compiler samples the pixels under the
highlight rectangle in the actual capture and picks the overlay lightness and alpha that keep the
highlighted text at APCA Lc ≥ 75 while marking the passage; it fails the scene if no value in the
amber hue family achieves that.

## Charts and diagrams

- Zero baseline for bars; truncated axes are disclosed on screen. Missing data is a gap.
- Axis and domain changes animate visibly or cut with a label; values never ease with overshoot.
- Diagrams: layered layout (ELK), orthogonal or spline edges, labels inside or beside nodes within
  `label_distance_max_px`, one flow direction per diagram.
- Repetition across episodes is detected by template and layout hash; a layout used in the last
  three episodes at the same position is flagged for variation.

## Legibility

- Text is never shrunk below its floor to fit. The compiler uses a shorter verified label, another
  layout variant, or splits the scene; otherwise the build fails naming the alternative.
- Read text must reach APCA Lc ≥ 75 against its actual background after encoding at a realistic
  YouTube bitrate, checked on decoded frames, and stay legible at 360 px wide.
