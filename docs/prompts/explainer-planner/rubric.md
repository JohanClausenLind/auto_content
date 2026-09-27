# Explainer planner prompt: review rubric

The reviewer scores `prompt.md` and `schema.json` together, as one deliverable, out of 10. The
target is **9.5**. The rubric stays fixed between rounds, so a score only moves when the prompt
changes.

## Evidence first, then opinion

Every review starts with a **test run**. The reviewer takes one request from `test-requests.md`,
acts as the planner model, follows `prompt.md` exactly as written (no outside knowledge of what the
prompt "meant"), writes the plan, and runs:

    uv run python scripts/check_explainer_plan.py <plan.json>

Checker errors are evidence about the prompt. If a careful model following the text makes the
error, the text is at fault: it was unclear, contradictory or impossible to satisfy. A plain slip
the text clearly forbids is recorded as a slip and does not count against the prompt.

## Dimensions (weights sum to 9.5)

| # | Dimension | Weight | 10 means |
|---|---|---|---|
| 1 | Teaching quality | 1.5 | A model following the prompt reliably writes an *explanation* (mechanism, contract, edge case, why) and not a description. The test plan's narration shows it. |
| 2 | Output contract | 1.5 | Every field is defined once, the prompt and schema agree on names, enums, required fields and meanings, and the example validates against the schema. |
| 3 | Renderability | 1.5 | A deterministic renderer can draw the plan without guessing: every op has the data it needs (for example, the new value for `update`), layout inside containers is specified, dim/undim state is well defined, and colours are roles a theme can map. |
| 4 | Internal consistency | 1.0 | No two rules conflict, no term is used before it's defined, and every number (words, seconds, counts) is consistent everywhere it appears. |
| 5 | Checkability | 1.0 | Every hard rule is stated precisely enough to check in code: which sentences count, where counting starts, what "a change" is. |
| 6 | Factual safety | 1.0 | Uncertain claims are captured in a form a verification stage can act on (anchored to beat and sentence), on-screen facts are covered too, and source notes are handled. |
| 7 | Executability | 1.0 | A mid-size model can follow it: sensible length and order, the output-size risk is managed, the example teaches the format without leaking content, and the planning steps are concrete. |
| 8 | Pipeline fit | 1.0 | Fits this repo (see `docs/prompts/explainer-planner/README.md` "Pipeline facts"): TTS narration and measured timing, captions, renderer constraints passed in by code, and defaults that match the repo. |

**Score** = the weighted sum of the dimension scores (each 0–10) ÷ 9.5 (the total weight), rounded to
one decimal. That makes the score the weighted average on the 0–10 scale. Rounds 1–6 divided by 10
by mistake, which understated them by 5%; `review-log.md` gives both figures for those rounds.

## Caps (applied after the weighted sum)

- Any **blocker** (a downstream stage can't consume the output, or two rules can't both be
  satisfied): at most **8.5**.
- Any **major** issue (a real, likely failure a competent model would hit): at most **9.2**.
- Checker errors in the test run that are the prompt's fault: at most **9.0**.
- **9.5 or above requires** zero blockers, zero majors, at most three minors, and a test plan that
  passes the checker with zero errors.

## Severity

- **blocker**: the output can't be used, or the rules contradict each other.
- **major**: a likely failure, an ambiguity that changes the output, or a missing piece of data a
  downstream stage needs.
- **minor**: wording, redundancy, or an edge case unlikely to matter.
- **nit**: style only. Nits never lower the score.

## Honesty rules for the reviewer

- Score what's written, not what was intended. Don't give credit for a fix that's described in the
  review log but isn't in the text.
- Don't inflate. A 9.5 means you'd ship the prompt as-is.
- Each issue names the exact section or field, quotes the text, says what goes wrong, and proposes a
  concrete edit.
- Don't propose changes that make the prompt longer without a matching gain. Length has a cost
  (dimension 7).
