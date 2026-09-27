# Explainer planner prompt

The planning prompt for mechanism-first technical explainer videos, and the loop that improves it.

| File | What it is |
|---|---|
| `prompt.md` | The system prompt the planner model receives. |
| `schema.json` | The JSON Schema its output must satisfy (used for constrained decoding). |
| `rubric.md` | The fixed scoring rubric. The target is 9.5/10. |
| `test-requests.md` | Requests the reviewer plans against, one per round. |
| `review-log.md` | One entry per round: scores, top issues, and what changed. |
| `../../../scripts/check_explainer_plan.py` | Deterministic checks on a produced plan. |

The prompt isn't wired into the pipeline yet. `plan_story` still uses `models/scriptwriter.py`.

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
- Local models (8B to 27B, through Ollama with constrained decoding) have produced only about
  1,000 output tokens reliably. A 420 s plan is much larger than that.
