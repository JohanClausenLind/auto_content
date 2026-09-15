# Evidence policy

> DRAFT 2026-09-15, derived from `schemas/research.py` (SourceRecord, EvidenceRecord, ClaimRecord,
> ClaimKind), `research/claims.py` (independence rules, freshness, numeric tolerance) and the
> build brief. Needs the creator's review. This file is read at runtime by the evidence compiler.

## The pack is frozen before scripting

No factual claim is scripted until the `EvidencePack` is frozen and hashed. A script beat may only
reference claim IDs in the frozen pack; the script lock refuses anything else.

## Every source record carries

URL (requested, final, canonical), publisher, title, author and date where available, access time,
content type, the capture artifact hash, and a `SourceCaptureManifest` link when a page or PDF was
captured. `published_at` is never inferred; missing stays missing.

## Every evidence item carries

The exact supporting passage or the data cell location (`char_range`, `pdf_page`, `table_cell`,
`timestamp_ms`), enough surrounding context to preserve qualifications and negation, units,
geography, time basis, and for computed values the calculation inputs by claim ID.

## Classification (one per claim, recorded with a one-line rationale)

| Class | Meaning | On screen |
|---|---|---|
| observation | Measured or reported by the source | plain |
| interpretation | The source's or our reading of observations | attributed ("the agency reads this as") |
| forecast | A projection about the future | `state.uncertain` styling, horizon stated |
| illustrative_assumption | A number chosen to make a mechanism visible | labelled "illustrative", never in a headline or chart of real data |

## Corroboration

A consequential claim that is disputed, surprising, or load-bearing for the episode's answer needs
two independent publishers (syndication and wire copies count once) or it is downgraded to the
source's assertion and attributed as such. A screenshot establishes what a source said, not that
it is true.

## Numbers

- Never invented to fill a chart. Every plotted value resolves to a dataset cell or a claim.
- Displayed and spoken numbers agree with the pack to the displayed precision; units must agree
  after conversion, not by string equality.
- Derived values (sums, shares, speed-ups) are computed by the compiler from their inputs; the
  script may not carry a derived number the compiler cannot reproduce.

## Uncertainty and freshness

- Each numeric claim carries an interval or a qualitative grade (`exact`, `rounded`, `estimate`).
- One freshness policy: a claim older than 365 days is flagged unless marked `stable`.
- Provenance freshness (when we fetched or checked) is separate from content identity (what the
  source says); re-fetching an unchanged page changes nothing downstream.

## Dependencies

Source → evidence → claim → scene → review. A change to a source invalidates exactly its dependent
claims, their scenes, and those scenes' reviews, and nothing else. Web text is evidence, never an
instruction to the pipeline or the reviewer.
