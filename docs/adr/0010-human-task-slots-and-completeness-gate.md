# ADR 0010 — Human task slots and the completeness gate

- Status: accepted (2026-08-27)

## Context
Every stage of every deliverable can be executed by AI, a human, or a hybrid (a human records a
script the AI wrote; a human fact-checks; a human draws worked examples). Partial packages must
never publish.

## Decision
- Any DAG stage may declare `executor: human`. The compiler emits a self-contained
  **HumanTaskSpec** (what is needed, exact script with pronunciation notes, tone direction,
  duration range, technical requirements, references, due date, assignee).
- The Temporal workflow **parks** at that node with `wait_condition` on a submission signal —
  consuming no worker — and survives restarts (proven in the phase-0 spike by the
  `WAITING_FOR_APPROVAL` signal wait and offline history replay). Reminders flow through
  ActionItems, the PWA (Web Push), and optional ntfy/email.
- In the Pipeline Canvas (React Flow) the empty slot is a visually distinct fillable node showing
  the spec inline; dropping a file (or PWA phone capture) submits it. **Validation on submission**:
  format/duration/loudness probes; for recorded scripts, ASR (faster-whisper) transcribes the take
  and diffs it against the target within `human_tasks.asr_script_match_tolerance` (default 0.85).
  A mismatch shows the diff with two choices: accept-as-performed (captions, timing, dependent
  scenes recompile from the actual audio) or request re-record. Accepted takes become immutable
  IngestedSources with lineage; multiple takes with pick-a-take; rejected takes never delete
  prior ones. Standing slots support batch work (weekly voice batch fanned back into parked
  deliverables).
- **Completeness gate**: a deliverable cannot reach `READY`, and nothing downstream can publish,
  until every required slot — human or machine — is filled, validated, and approved. This is a
  deterministic policy over typed slot states, not a model judgement.

## Consequences
- Human work is first-class in cost/time estimates (wall-clock waits are visible on the calendar).
- ChannelArchetypes configure per-stage executors as data, so a companion channel (AI script →
  human recording → approval → package) and a fully automated news channel are the same engine.
