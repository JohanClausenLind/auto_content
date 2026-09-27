# Explainer planner prompt

The planning prompt for mechanism-first technical explainer videos, and the loop that improves it.

| File | What it is |
|---|---|
| `prompt.md` | The system prompt the planner model receives. |
| `schema.json` | The JSON Schema its output must satisfy (used for constrained decoding). |
| `rubric.md` | The fixed scoring rubric. The target is 9.5/10. |
| `test-requests.md` | Requests the reviewer plans against, one per round. |
| `review-log.md` | One entry per round: scores, top issues, and what changed. |
| `python/content_factory/explainer/check.py` | Deterministic checks on a produced plan (CLI: `scripts/check_explainer_plan.py`). |

## In the pipeline

The `mechanism-explainer` workflow runs this prompt. Its `plan_story` node has two widgets:

- `planner: explainer` drafts a plan for the brief's question. `models/explainer_planner.py` sends
  this prompt as the system message and a `<request>` built from the brief. Verified claims from
  `research/claims.json` and the brief's pasted copy become `<source_notes>`. The checker's
  errors go back to the model as a repair turn, up to two times. A plan that still fails is a
  named failure, and the failing plan and report are written to `story/`.
- `explainer: <path>` uses a checked plan from disk instead, for example one you corrected by
  hand. The lane ships pointing at `fixtures/explainer/ssd_nearly_full.json`, so it runs offline.

Either way, `story/` gets `explainer.json` (the full plan), `explainer-check.json`,
`claims-to-verify.json`, and `plan.json`. `plan.json` is the StoryPlan the existing renderer draws,
built by `explainer/bridge.py`: a card for the question, the assumption, the answer and the
takeaway, and a flow diagram of what's on screen for each mechanism beat. The narration is kept word
for word. Zooms, traces and fault marks become cuts, because the one-diagram renderer the plan is
written for doesn't exist yet.

Not wired yet:
- **Staged mode:** skeleton, then batches with `<state>`.
- **Claim verification:** `claims-to-verify.json` is written but not checked.
- **Renderer constraints:** nothing passes `<renderer_constraints>` from code yet.

## The review loop

Two agents live in `.claude/agents/`:

- **`explainer-prompt-reviewer`**: runs the prompt on a test request, checks the plan, and scores
  the prompt against `rubric.md`. It's read-only for the prompt files.
- **`explainer-prompt-reviser`**: applies a review's fixes to `prompt.md` and `schema.json`, keeps
  them consistent, and logs the round.

Each round:

1. Two reviewers run independently, each on its own test request. Neither sees the other's review
   or the earlier scores.
2. The round score is the **lower** of the two.
3. If it's 9.5 or above, stop. Otherwise the reviser applies the union of both reviews' blocker,
   major and minor issues, and a new round starts.

Ask Claude Code to "run the explainer prompt review loop" to start it again. It stops at 9.5, or
after 8 rounds with the reason recorded in the log.

## Pipeline facts the prompt has to fit

These are facts about this repo, and rubric dimension 8 scores against them.

- Narration is synthesized by TTS (`synthesize_narration`), then word timings are measured
  (`align_words`). Visual timing comes from the measured words, not from the estimates.
  `timeline/compiler.py` already produces a frame for every word. A beat's sentence boundaries
  can therefore be found from its narration array.
- Captions are built from the spoken narration, so narration must be readable as captions too.
- The renderer is Remotion and deterministic. Layout, camera and timing are computed by code from
  the plan; the model supplies meaning and anchors, not coordinates.
- Themes (light, dark, brand) own the actual colours, so the plan should name colour roles.
- Code decides which primitives and operations the renderer supports, and passes them in as
  `<renderer_constraints>`, the same way `scenes/kinds.py` limits the script writer today.
- Factual claims go through `research`, then `verify_claims`, then `lock_script`'s claim gate. A
  claim the gate can't anchor to a sentence can't be dropped precisely.
- The shared system instruction (`prompting/templates.py`) is sent before every role's prompt. It
  forbids inventing figures, and forbids filler questions to the reader. As of prompting 1.1.0, a
  question the piece goes on to answer is explicitly allowed.
- The documentary lane plans at 145 words per minute, and its default episode is 600 s.
- Output size is measured, not a hard ceiling: the one recorded local script-writer run (STATUS,
  2026-09-08) produced 1,027 output tokens in 30 s, and larger single outputs are untested. So a
  full plan in one call is a risk for a local model, and a staged mode is the fallback. A staged
  call has to be given the state it needs (objects on screen, running word and sentence counts),
  and the pieces should stay large enough to plan well. Which model runs this role is a routing
  decision outside the prompt.
