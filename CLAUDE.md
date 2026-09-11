# Content Factory — repo conventions for coding agents

Read `STATUS.md` first (216 lines): it holds the current phase, what passed, and the next
smallest task. The proof behind each verdict — commands actually run, and the session-by-session
story — lives in `docs/journal/<year>-<month>.md`. Read a journal only when you need the
detail behind a specific claim; it is 630 KB and grows every session.

## Layout
- `python/content_factory/` — control plane (FastAPI, Typer CLI, Temporal workflows, schemas).
  `apps/api`, `apps/cli`, `apps/workers` are thin entry points into it.
- `packages/content-schema-ts` — generated TS types + Ajv validators from the Pydantic source of
  truth. Never edit `generated/` or `schema/` by hand: run `just schemas`.
- `packages/editor-core` — typed reversible edit operations (TS, shared browser/server).
- `apps/renderer` — Remotion compositions and the render scripts.
- `external/`, `models/`, `datasets/`, `output/`, `.venvs/`, `sandbox/` — the git-ignored local AI
  stack, laid out ComfyUI-style (upstream checkouts, category-sorted weight index, category-sorted
  *data* index, generated media, tool venvs, scratch). See docs/setup.md "Local AI stack".
  `datasets/` is built by `just datasets link` from `content_factory.libraries`; `docs/datasets.md`
  says what each library gives, what it cannot, and what reads it. Note the name collision:
  `content_factory.datasets` compiles an uploaded CSV into a typed table and is unrelated.
- `docs/adr/` — twelve ADRs; do not add more without a real decision. `docs/research/` — dated
  official-doc research with URLs.

## Commands
- `./setup.sh` (idempotent) · `just doctor` · `just up` / `just down`
- `just stop` — one button: the active run (local and durable), the worker, API/web, the GPU
  servers, compose. `just stop-run` stops only the run; `--dry-run` explains without touching.
- `just schemas` (regenerate contracts) · `just schemas-check` (drift)
- `just fmt` · `just lint` · `just typecheck` (ruff, oxlint, pyright, tsc). The JS/TS half is
  oxlint over `.oxlintrc.json`: `correctness` is an error, and every demoted rule carries its
  reason in that file. Add rules there, not per package — `pnpm -r run lint` cannot reach the
  workspace root and matched nothing for the repo's first 28k lines of TypeScript.
- `just test` — core suites: no internet, keys, GPU, or live accounts
  (`uv run pytest` + `pnpm -r test`; the marker expression is defined once in
  `pyproject.toml` `[tool.pytest.ini_options] addopts`)
- `just test-integration` — needs compose (postgres, temporal). **Run this before closing any
  phase that touches a stage executor or a contract a stage writes.** The core suite cannot reach
  `ProductionWorkflow`, so a stage that no longer finds its own output stays green in `just test`.
- `just render-smoke` — Remotion clip + ffprobe assertions

## Rules that are not negotiable
- Never publish/push/upload externally during development; mocks and fixtures by default.
- No billing/subscription/entitlement code anywhere (docs/scale-later.md lists the gap).
- Contracts start as Pydantic models; unknown fields are errors; IDs are opaque strings.
- Workflow code is deterministic; every activity is idempotent and safe to run twice.
- Safety guardrails (PersonaFirewall, minor safety, crisis protocol, disclosure) are code, not config.
- Pin dependencies exactly; resolve versions from the registry, never from memory.
- Every phase ends with commands actually run and their results appended to the current
  `docs/journal/<year>-<month>.md`, and the one-line verdict updated in `STATUS.md`'s phase
  checklist. Session write-ups go in the journal, never in STATUS.md — that is how STATUS.md
  reached 8,032 lines and stopped being readable as a first file.
