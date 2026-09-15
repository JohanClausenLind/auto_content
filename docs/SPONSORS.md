# Sponsors

> DRAFT 2026-09-15. The repository has no sponsor tooling (deferred in `docs/scale-later.md`) and
> the demo brief says "We are not selling anything". This is the policy an eventual sponsor segment
> must follow; the creator should confirm or replace it before any sponsor is accepted.

## Category fit

- A sponsor fits when its product is something this audience plausibly uses to understand or
  build things. Sponsor category fit is a secondary factor in topic ranking, never the primary.
- Excluded: personal finance, gambling, health and supplements, legal services, political
  advertising, anything requiring the presenter to claim personal use they did not have.

## Disclosure

- Spoken disclosure at the start of the segment ("This episode is sponsored by …") and an
  on-screen label for the whole segment.
- The YouTube "includes paid promotion" flag is set in the export metadata for any episode with a
  sponsor segment; the pipeline cannot publish, so this travels as metadata in the bundle.
- Never fabricate an endorsement, a test, or a personal experience. Sponsor claims are the
  sponsor's own statements, attributed as such, and are not evidence for the episode.

## Segment isolation

- A sponsor segment is its own scene group with `sponsored: true`; it never shares a scene with
  evidence, charts of real data, or source documents.
- Sponsor copy enters through the script as a separate `ScriptPlan.sponsor_segment`, is excluded
  from the evidence gate, and is never quoted by the episode-level review as content.
- Sponsor visuals use the UI/ink family only; the data and state families are reserved for the
  explanation.
