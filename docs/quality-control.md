# Quality control

Four layers (section 17); phase 2 implements the deterministic post-render layer and the static
legibility report, with the pass policy fixed from the start:

1. **Contract and factual QC (blocking)** — schema validity (Ajv/Pydantic, unknown fields are
   errors), dataset lineage for every on-screen number (`RenderBundle` refuses unresolved
   `DataRef`s), chart semantics (`ChartScene`: zero baseline or explicit truncation disclosure,
   single-series donuts, dual axes off by default). Claims/citations arrive in phase 4.
2. **Pre-export static/visual QC** — `packages/content-ui` `legibilityReport`: minimum font px at
   1080, line counts vs `max_lines`, role-colour contrast ≥ 4.5; safe areas from the artboard.
3. **Post-render deterministic QC** — `python/content_factory/qc/media.py`: ffprobe assertions,
   black/frozen frame sampling, PNG integrity/blank detection. Only relevant checks run per
   deliverable type.
4. **Multimodal critic (optional)** — phase 7; advisory findings with allowed fix operations only.

Pass policy: PASS requires all deterministic gates green, zero blocker/critical findings, no
unresolved major finding in factual accuracy, legibility, synchronization, or rights. Severities:
blocker, critical, major, minor, advisory. A model score is never the pass decision.

## What layer 4 has to be for, measured

An overnight run of the whole catalogue on real backends (2026-09-09/10, `output/overnight/`)
put a number on the gap the multimodal critic is meant to close. Every check in
`qc/frame_review.py` was run over five frames scored by eye beforehand — the best of the night
(9/10) down to six consistent views of the wrong object (2/10). **Five checks each, zero flagged,
on all of them.** The checks are not broken; they catch a frame crushed to two tones, a greyscale
return, letterbox bars and a subject jammed into the frame edge, and none of that happened.

What went wrong instead was always the same shape: the picture is *well made and of the wrong
thing*. A prompt naming a structure the model does not hold comes back as the nearest structure it
does — a conchoidal fracture as a seashell, a pottery sherd as a whole pot, a fern fiddlehead as a
sea urchin, malachite as tartan — rendered with correct light, believable materials and a
plausible ground. Six of twenty-one delivered films were five "missing asset" placeholder cards
end to end and passed every gate that existed at the time. A frame with an unrequested person in
it measured *better* on midtone spread than the night's best frame.

It is not that the deterministic checks are the wrong ones. Fifteen stills scored by eye first,
from 9/10 to 3/10, separate by **1.4 %** on luminance contrast, **1.3 %** on dynamic range,
11.6 % on edge energy *in the wrong direction*, and 26.4 % on mean saturation with total overlap
— the 8/10 frame is the least saturated in the set and a 4/10 frame has the highest contrast. A
picture of the wrong object is still a detailed, well-exposed picture, so no threshold over the
pixels will find it.

So the layer-4 question is not "is this frame well formed" — the deterministic layers answer that,
and answered it correctly all night. It is **"is this a picture of the thing that was asked
for?"**, which needs the subject sentence and the frame side by side. Until something answers it,
`review_frames` is the only thing that does, and the `--as vlm` reviewer slot in
`content-factory frames review` is where the answer would plug in. Two properties any candidate
has to have: it must be able to fail a technically perfect frame, and its verdict must bind to the
digest of the exact image it looked at, the way an operator's does.
