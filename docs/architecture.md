# Architecture

Modular monolith, one repository, four deployable roles:

| Role | Entry point | Talks to |
|---|---|---|
| API (stateless) | `content_factory.api.app` (FastAPI) | Postgres, ArtifactStore, Temporal client |
| Workflow workers | `apps/workers` (Temporal workers by resource class) | Temporal, Postgres, ArtifactStore, providers |
| Render workers | `apps/renderer` (Node: Remotion, static, audio, email) | ArtifactStore |
| Web app (PWA) | `apps/web` (React/Vite) | API only (cookie session, signed artifact URLs) |

Invariants kept from day one (see docs/scale-later.md for what is deliberately absent):
- Postgres is transactional truth; every domain row has `workspace_id`; queries filter by the
  principal's current workspace (foreign ids → 404).
- Object storage behind `ArtifactStore` (filesystem default, S3-compatible optional); content
  addressed, immutable, workspace-prefixed keys; browsers get short-lived signed URLs.
- Temporal owns durability; workflow code is deterministic; activities are idempotent.
- Contracts are Pydantic → JSON Schema → TypeScript + Ajv; EditorCore (TS) is the one edit reducer.
- Immutable safety policy lives in code; config narrows, never weakens.

Directories: `python/content_factory/<domain>/` (one package per domain: api, auth, audio, cli,
comfyui, controls, distribution, editor, models, qc, reference, runners, schemas, sequences,
services, shots, workflows, workspace, and the rest), `packages/{content-schema-ts,editor-core,
node-graph,pipeline-canvas,content-ui,video-ui,web-ui}`, `apps/{api,cli,web,renderer,workers}`,
`skills/` (isolated tool environments), `workflows/` (one YAML per lane), `docs/adr`, `docs/research`.
