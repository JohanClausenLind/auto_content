# ADR 0003 — Render backends, artifact store, and cache strategy

- Status: accepted (2026-08-27)
- Research: docs/research/infra-and-frontend.md (Remotion license text, MinIO status)

## Context
Video, static, carousel, audio, and email renderers must be deterministic, cacheable at the
smallest stable unit, and verifiable with ffprobe. Object storage must be pluggable. Licensing
must be recorded.

## Decision
- **Remotion 4.0.518** is the video composition renderer behind a `RenderBackend` contract
  (`local_process` first). License (retrieved 2026-08-27 from remotion.dev/license): free for
  "an individual", "a for-profit organization with up to 3 employees", non-profits, and
  evaluation. This private single-operator project qualifies. **Trigger recorded in
  docs/licensing.md**: if the operator's organization exceeds 3 employees or the tool is offered
  commercially, a Remotion Company License is required — or the renderer is swapped to
  **Revideo (MIT, `@revideo/core` 0.11.0)** behind the same contract. Rendering rules: no
  `Math.random` (use `random(seed)`), no CSS transitions, no network fetches, local fonts via
  `@remotion/fonts` (pinned `@fontsource/inter`, SIL OFL 1.1), Chrome Headless Shell downloaded
  once by `ensureBrowser()` and pinned by the Remotion version.
- Static/carousel/cover renderers use the same React components rendered to PNG through the same
  headless browser; audio/email use FFmpeg and MJML respectively (later phases).
- Every output is verified by ffprobe assertions (codec, dimensions, fps, pix_fmt, frame count,
  moov-before-mdat fast start) — proven by the phase-0 smoke render.
- **ArtifactStore** interface: filesystem default (`./data/artifacts`), S3-compatible adapter.
  **MinIO is not used**: its GitHub repository was archived on 2026-04-25 with no further binaries
  or images. The optional compose profile uses **SeaweedFS 4.44 (Apache-2.0)**; AWS S3 or Garage
  work through the same adapter later.
- Content-addressed cache keyed by every output-affecting input (normalized input, template
  version, model/provider identity, generation parameters, schema version, code revision,
  design-system version, font/asset hashes, renderer version, TTS voice revision, destination
  profile revisions). Time-sensitive research has TTL/explicit refresh. Publishing is never a
  cacheable result.

## Consequences
- The first render downloads Chrome Headless Shell (~150 MB) into `node_modules/.remotion`;
  `setup.sh` performs this step explicitly and says so.
- Swapping renderers is a contract implementation, not a rewrite.
