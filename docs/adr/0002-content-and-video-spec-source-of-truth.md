# ADR 0002 — Content/video spec source of truth and the EditorCore

- Status: accepted (2026-08-27)

## Context
Contracts cross three runtimes: Python (control plane, validators), TypeScript in Node (renderer,
edit service), and TypeScript in the browser (preview, editor). Drift between them silently breaks
rendering and editing. Every edit must be typed, reversible, and replayable to the same revision
hash.

## Decision
- **Pydantic models are the single source of truth** (`python/content_factory/schemas`). A script
  exports **JSON Schema 2020-12** (serialization mode, normalized so every field of a closed
  object is `required`, and `discriminator` carries only `propertyName` for Ajv strict mode).
- `packages/content-schema-ts` generates one bundled TypeScript declaration module
  (json-schema-to-typescript) and compiles Ajv 2020 validators from the same JSON. Python emits
  valid and invalid fixture instances; the TS test suite must accept/reject them identically.
  CI regenerates and fails on drift (`just schemas-check`).
- Canonical JSON (sorted keys, no whitespace, no NaN) + SHA-256 defines revision hashes in both
  languages (`canonical_dumps` / `canonicalJson`).
- **EditorCore** (`packages/editor-core`, TypeScript) is the one reducer for typed
  `EditOperation`s: preconditions, pure application, exact inverse steps, dependency impact
  (a claim-linked text edit reopens evidence validation; layout-only edits do not). The browser
  runs it for preview; the server runs the identical package in the Node edit/render service for
  validation and application. Python never re-implements the reducer — it validates operation
  batches against the shared schema and delegates application.
- Time bases: audio in integer milliseconds, video in integer frames; no floating-point seconds
  as renderer truth. Scene specs are a discriminated union; models supply semantic parameters and
  deterministic code compiles layout and timing.

## Consequences
- Adding a contract = add a Pydantic model to the registry, run `just schemas`, commit generated
  output. Unknown fields are rejected everywhere.
- The Node edit service is a required runtime dependency of the API for edit application (not
  for read paths).
