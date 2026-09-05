# ADR 0011 — Workspace graphs execute by compiling onto the production DAG

- Status: accepted (2026-09-03)

## Context
The workspace is a node-graph editor (ComfyUI-style) whose documents are the `WorkspaceGraph`
contract. Runs must keep every invariant the campaign path proved on Temporal (ADR 0001):
deterministic workflow code, idempotent input-hashed activities, the approval gate bound to the
exact preflight revision, exactly-one execution per stage. The tempting alternative — a second
executor that walks editor graphs directly — would fork those guarantees.

## Decision
- One engine, two front doors. `workspace/compile.py` maps a graph onto the SAME
  `DeliverableDAG` + `ContentCampaign` the campaign compiler emits; `ProductionWorkflow` accepts
  the precompiled DAG (`ProductionInput.dag_json`, re-validated inside the activity) and runs it
  unchanged. Stage resource classes/executors come from `dag_compiler.stage_defaults()` so a
  hand-drawn node runs identically to its campaign twin.
- Every graph node gets a typed disposition: `executes`, `skipped` with a reason (brief → campaign
  brief; notes; muted/bypassed; publish → the gated distribution flow), or `blocks` with a reason
  (unknown type; a stage with no registered executor). A graph with any blocking node is refused —
  never silently pruned.
- The approval gate is an invariant, not an operator habit: a graph without a Preflight Gate node
  gets one injected (depending on every shared stage), reported as a disposition.
- The one synthetic deliverable's type is inferred from the most final stage present
  (compose/generate video → short_video; mix → audio_clip; render_static → single_image_post; …).

## Alternatives considered
- Direct execution of editor graphs by a new engine: forfeits replay-tested durability and the
  approval/idempotency proofs; two engines drift.
- Deriving a campaign and recompiling (ignoring the drawn topology): dishonest to the canvas —
  extra nodes silently dropped, missing ones silently added.

## Consequences
- The editor's node vocabulary is pinned to the contract: the TS catalogue is
  `Record<Stage, NodeDefinition>`, so a new pipeline stage fails the web typecheck until it has a
  node, and a schema-enum test guards the reverse direction.
- Stages without executors (article/newsletter/sequence branches today) are visible refusal
  reasons in the UI rather than runtime failures; landing their executors makes the matching
  templates runnable with no compiler change.
- Run views expose the compiled DAG's real edges (`run_view.edges` from the project's dag.json),
  so the pipeline canvas draws truth for fan-out graphs instead of the legacy chain heuristic.
