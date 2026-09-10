# Third-party notices

Content Factory bundles no third-party source code. It depends on the packages listed in
`pyproject.toml`/`uv.lock` and `package.json`/`pnpm-lock.yaml`, each under its own license, and
talks to separately installed processes (ComfyUI, comfy-cli, SearXNG, Temporal, PostgreSQL,
SeaweedFS, ntfy) over documented APIs.

Patterns (not code) were adopted from: Odysseus (MIT) — five-token theme presets with derived
colours, localStorage-first theme persistence with server backup, "assistant as a pinned
session"; n8n, Temporal Web UI, Trigger.dev — run/timeline UX patterns only.

Fonts: Inter (Rasmus Andersson) via `@fontsource/inter`, SIL Open Font License 1.1.

Sound effects: `assets/sfx` draws on three outside origins, and every entry in
`assets/sfx/manifest.json` records which one, the delivered filename, its sha256 and its licence
text. Neither licence requires attribution; suppliers are recorded regardless.

- **Sonniss #GameAudioGDC Bundle 2026 (Part 9)**, royalty-free licensing agreement, 346 excerpts
  from 18 suppliers. Prohibits using the sounds to train or enhance AI, and prohibits
  redistributing them other than incorporated into a project.
- **Mixkit (Envato)**, Mixkit Sound Effects Free License, 295 sounds. Free for commercial and
  non-commercial End Products; prohibits redistributing an Item on its own, as stock, in a tool or
  template, and (Envato User Terms cl.9) aggregating Items on a stock or inventory basis.
- 19 sounds **rendered locally by the operator**, no third-party licence.

Because the halves are interchangeable in a mix, the strictest term governs the whole directory:
no sound in `assets/sfx` is redistributed, and none is ever an input to a model. See
`docs/licensing.md` and `docs/research/2026-09-09-mixkit-sfx-pack-licence.md`.

See `docs/licensing.md` for the license-trigger analysis (Remotion) and the copyleft
components that run as separate processes.
