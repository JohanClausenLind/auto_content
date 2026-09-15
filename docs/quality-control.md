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

An overnight run of the whole catalogue on real backends (journal 2026-09-09/10) put a number on
the gap: every deterministic check in `qc/frame_review.py` passed frames scored 2/10 by eye,
because the failure was always a well-made picture *of the wrong thing*, and no threshold over the
pixels separates a 9/10 frame from a 3/10 one (contrast within 1.4 %, saturation overlapping).

So layer 4 answers "is this a picture of the thing that was asked for?", with the subject sentence
and the frame side by side. `qc/vlm_review.py` fills that slot at `review_frames`: it must be able
to fail a technically perfect frame, and its verdict binds to the digest of the exact image it
looked at, the way an operator's does.
