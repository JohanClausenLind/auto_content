# Prompting HiDream-O1 for image sequences

Guidance accumulated from measured outcomes on this host. Every rule below cost GPU hours to
learn and cites what was observed. **This file is only ever changed through an approved proposal**
— see `content-factory prompting --help`. Nothing edits it automatically.

## Rules

### 1. The style clause leads the prompt
Put the art direction first, before the subject and the staging. With the style clause trailing
three sentences of staging the model ignores it entirely and returns its default photoreal idiom
for every art direction; the identical words moved to the front produce the style asked for.
*Evidence: same skeleton, same seed, style last vs style first — one returned a heavy-outline
photoreal image for a ukiyo-e prompt, the other an actual woodblock.*

### 2. Name a process, never a surface
Directions phrased as a process land. Directions that name the medium as a physical object get the
object: "on cold-press paper" produced a hand holding a sheet of watercolour paper, and "sketchbook
paper" produced a drawing of a sketchbook page.
*Evidence: 2 of 2 presets naming a surface failed this way.*

### 3. Ask explicitly for tonal range
Illustration styles collapse the midtones unless told otherwise. One measured 31 % of pixels
crushed to near-black with only 36 % midtones, where a photograph runs 60-80 %. Add "full tonal
range with detail held in both the shadows and the highlights".

### 4. A monochrome instruction is applied only partly
"monochrome, no colour" left 39 % of pixels saturated — the result is neither a colour image nor a
grey one, which is what reads as *weird* rather than as a style. Either commit (state the single
ink colour) or drop the monochrome claim.

### 5. One reference means edit; more than one means reconstruct
The model edits only with exactly one reference image. Given an anchor plus a control pass it
leaves edit mode for subject-driven reconstruction: measurably more faithful to the anchor than
generating fresh (mean diff 23.8 vs 58.6) but it raises contrast 81 → 95 and brightness 84 → 99.

### 6. Distance in words is not a motion channel
Asking for "moving 35 cm inward" and "moving 80 cm inward" produced the same image (diff 2.41,
where a held frame is 0.03 and a cut is 29.8). The model ignores the quantity; movement has to
come from the skeleton.

### 7. The requested resolution is ignored
`PREDEFINED_RESOLUTIONS` are all ~4 MP and matched on aspect ratio alone, so 1024x576 returns
2560x1440. Budget ~5.5 min per drawing, and cut at the drawings' own size.

### 8. A skeleton must be big enough to read
Below roughly a fifth of the frame's short side the model draws the OpenPose joint dots as small
coloured objects instead of reading them as a pose.
