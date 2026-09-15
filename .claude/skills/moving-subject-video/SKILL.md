---
name: moving-subject-video
description: Make a video where one subject moves and the world behind it does not. Use when asked to animate a person or object in a picture, to make a walking/running/moving shot, to keep a background consistent across frames, or to build a long video out of Ideogram 4 keyframes with voice and sound effects. Covers subject location, the change map, LTX i2v and the consistency loop.
---

# A subject that moves, in a world that does not

**The whole trick is that the background is never regenerated.** Frame 2 is not a new picture of
the same place — it is frame 1 with only the subject's box released. Every other approach to
consistency in this repo measures drift after the fact; this one prevents most of it.

**But diffdiff anchors latents, not pixels.** The frame still goes through the VAE, and the
round-trip alone moved 13.5 % of the *frozen* background by more than 8/255 (measured 2026-09-15,
1024x576). That is why `scripts/ideogram_inpaint.py` composites the original back outside the map
by default: it takes the background to 0.2 % of pixels over 8/255 — effectively byte-identical.
`--no-composite` turns it off; do not, unless you are debugging the sampler.

Measured on the first real pair, vegaserv, 20 steps, 1024x576:

| | mean abs diff outside the box | pixels > 8/255 |
| --- | --- | --- |
| raw diffdiff output | 4.30 / 255 | 13.54 % |
| after composite | **0.125 / 255** | **0.20 %** |

## The loop, once per keyframe

```bash
# 1. the anchor: one structured JSON caption -> one frame (never prose)
uv run python scripts/ideogram_inpaint.py --help        # the inpaint half
# build the caption with content_factory.prompting.ideogram, not by hand

# 2. where is the subject?  -> a relative Box
uv run python -c "
from content_factory.sequences.subject_locate import locate_subject
print(locate_subject(open('frame_000.png','rb').read(), 'the walking man'))"

# 3. + 4. release the subject, freeze the world, repaint the next pose
uv run python scripts/ideogram_inpaint.py \
    --image frame_000.png --out frame_001.png \
    --box 0.42,0.18,0.24,0.70 --caption caption_001.json --seed 101
```

`--box` is what **moves**. `diffdiff_map` inverts it for you: black over the subject (released at
step 0, repainted freely), white everywhere else (held to the reference for the whole schedule).
Check a box before paying for it:

```bash
uv run python scripts/ideogram_inpaint.py --image f.png --out /dev/null \
    --box 0.42,0.18,0.24,0.70 --dry-run --save-map map.png
```

`regenerated` over ~0.6 means you are re-rolling the picture, not moving a subject. Tighten the box.

## Knobs that decide consistency

| Knob | Default | What it buys |
| --- | --- | --- |
| `--hold` | 0.0 | 0.25–0.35 keeps the subject's own layout and light while changing the pose — the single strongest consistency lever after the frozen background |
| `--feather-tokens` | 2.0 | cells of 16 px. Below 1 the seam shows; above 4 the background starts moving |
| `--outside` | 1.0 | leave at 1.0. Anything lower lets the background drift, which is the one thing this lane exists to prevent |
| `--seed` | 7 | vary per keyframe, or every frame is the same pose |

## Keyframes to motion

The keyframes are the skeleton; LTX-2.5 fills the motion between them.

* `media/ltx_packages.py::ltx_i2v_package` — one first frame plus a motion prompt to a short clip.
* `ltx_i2v_guided_package` — the same, plus up to **4 keyframe guides**. Use this: guiding the clip
  with the inpainted next pose is what stops the motion inventing its own drift.

A minute at 5 fps is 300 frames. Do not make 300 keyframes — make ~12 keyframes and let i2v produce
~5 s of motion between each pair, then set the output frame rate at assembly.

## Voice and sound effects

Do not rebuild them. `picture-story` (20 stages) and `narrated-video` (14) already do narration,
per-beat sound effects and the mix; `uv run content-factory workflows list` shows them. Feed this
lane's clips in as supplied material rather than starting a new audio path.

## Where it runs

Ideogram 4 SDNQ (`:8802`) fits vegaserv's 31 GB; the fp8 ComfyUI graph does not and is nova-only.
The vision model is a 16.6 GB Ollama tenant and the inpaint server holds a card, so they cannot be
resident together — locate every subject first, then start the image server. `services/local.py`
arbitrates, and `docs/gpu-hosts.md` is the routing table.

## Measured cost

vegaserv, SDNQ 4-bit, leaf-level offload of the two transformers only, 1024x576, 20 steps:
**150 s** for a text-to-image frame and **140 s** for an inpaint, peak **14.8 GiB** VRAM. That is
roughly 4x nova's 38-40 s for the fp8 ComfyUI path — the offloading is the cost of fitting 31 GB.
Budget accordingly: twelve keyframes is about half an hour before any i2v.

## What is not proven

The keyframe loop is measured; **the i2v half, the 60 s assembly and the audio are not**. Nothing
has produced a video yet. The vision locator is unit-tested against a stub but has never been run
against the real model.
