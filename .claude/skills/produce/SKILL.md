---
name: produce
description: Make video, images, or audio with this repo's production pipeline. Use when asked to produce, render, generate or finish a film, a picture, a picture story, a narrated video, a voice track, or to upscale or clean up existing media. Also use to list or add a workflow.
---

# Producing content

One command runs a whole production. Do not orchestrate stages by hand: the runner already does
that, and driving it stage by stage costs a transcript and gains nothing.

## Pick a workflow, then run it

```bash
uv run content-factory workflows list          # one line each; "todo" means it cannot run yet
uv run content-factory make <id> --subject "one sentence naming the world"
```

`make` preflights the lane, runs every stage, prints one line per stage, and ends with a single
JSON object holding `passed`, `project_dir` and the path to `run.json`. Read that last line. Read
`run.json` only if something failed.

Useful flags, all optional:

| Flag | For |
|---|---|
| `--plan` | Print the steps and exit. Use this first when you are unsure a lane does what you want. |
| `--story fixtures/story/<name>.json` | A written script rather than a drafted one. |
| `--shots fixtures/shots/<name>.json` | Hand-staged shots. `two_hander_mocap.json` stages real captured two-person contact. |
| `--style <preset or prompt>` | Art direction for every frame. |
| `--from <node>` / `--until <node>` | Resume or stop. Takes a node key or a stage name. |
| `--set node.key=value` | Override one widget value, e.g. `--set motion.motion=hold`. |
| `--force` | Run despite a preflight gap. |

## What the lanes are for

Ask `workflows list` rather than guessing, but in outline: `picture-story` is drawings cut into a
film with a voice; `narrated-video` is typeset cards with narration and no generative picture, so
it is the cheapest and most reliable; `scene-controlled-video` is 3D-staged cinematography without
sound; `hybrid-video` splices data scenes and staged scenes into one narrated timeline;
`silent-video` has music and effects but no voice; `single-clip-post` finishes one short clip for
posting; `single-image` and `image-set` make stills, the second one consistent with itself;
`photo-sequence-video` cuts stills into a video; `image-to-video` moves one still;
`voice-over-track` and `audio-restore` deliver audio; `image-upscale` and `video-finish` clean up
what already exists.

Two lanes ask a person to look before they continue, at `review_assets` and `review_frames`. That
is deliberate, and the output distinguishes it: a review gate prints `GATE`, exits **4**, and its
summary says `waiting_for_review`, where a real failure prints `FAIL`, exits 1 and says `failed_at`.

When a run parks at a gate, show the contact sheet it names and ask. Do not pass `--force`: that is
the one response a gate must not get, because the gate exists for the judgements measurements cannot
make, such as whether these are the same two people as the last frame.

## The reference library

`picture-story` searches a library of real human interaction before it stages anything, so the
contact on screen is contact that was captured rather than guessed. Its `find_reference` step
reports what it selected in the run facts, including `absent_terms`: words the library understood
and has nothing for. Treat that list as the operator's shooting list, not as an error.

With no library on disk the step selects nothing, says so, and the lane still runs on preset
staging. Build one with:

```bash
uv run python -m content_factory.reference.build
```

`--shots fixtures/shots/<plan>.json` overrides retrieval entirely with a hand-authored plan.

## Adding or changing a workflow

A workflow is one file: `workflows/<id>.yaml`. Nothing else defines it, and the canvas is generated
from it.

```bash
uv run content-factory workflows new <id> --from narrated-video
# edit workflows/<id>.yaml
uv run python scripts/export_workflows.py
uv run content-factory workflows validate
```

Rules the validator enforces, so you do not have to remember them: every widget value must be a
widget the node declares, every wire must connect slots that exist and whose types are compatible,
and `order` must be a topological order of `wires`. Read `fixtures/schema/node_catalog.json` for
what a node accepts. Name a workflow for what it does to the material, never for one subject.

If a lane uses a stage that has no executor yet, the file must say so in `caveat`. The catalogue is
allowed to describe an unfinished lane; it is not allowed to pretend one works.

## When not to use this

Editing a stage's behaviour, adding a stage, or changing a contract is ordinary work in
`python/content_factory/`. Read `STATUS.md` first and follow `CLAUDE.md`.
