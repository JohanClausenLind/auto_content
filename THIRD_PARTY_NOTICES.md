# Third-party notices

Content Factory bundles no third-party source code. It depends on the packages listed in
`pyproject.toml`/`uv.lock` and `package.json`/`pnpm-lock.yaml`, each under its own license, and
talks to separately installed processes (ComfyUI, comfy-cli, SearXNG, Temporal, PostgreSQL,
SeaweedFS, ntfy) over documented APIs.

Patterns (not code) were adopted from: Odysseus (MIT) — five-token theme presets with derived
colours, localStorage-first theme persistence with server backup, "assistant as a pinned
session"; n8n, Temporal Web UI, Trigger.dev — run/timeline UX patterns only.

Fonts: Inter (Rasmus Andersson) via `@fontsource/inter`, SIL Open Font License 1.1.

Sound effects: the recorded half of `assets/sfx` is excerpted from the Sonniss #GameAudioGDC
Bundle 2026 (Part 9) under its royalty-free licensing agreement — no attribution is required, and
the per-file supplier, library and URL are recorded in `assets/sfx/manifest.json` regardless.
That agreement prohibits using the sounds to train or enhance AI, and prohibits redistributing
them other than incorporated into a project; see `docs/licensing.md`.

See `docs/licensing.md` for the license-trigger analysis (Remotion) and the copyleft
components that run as separate processes.
