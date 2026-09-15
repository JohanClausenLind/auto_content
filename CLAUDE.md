# Content Factory — repo conventions for coding agents

Read `STATUS.md` first (under 200 lines): the current phase, what passed, the next smallest task.
The proof behind each verdict lives in `docs/journal/<year>-<month>.md`, one entry of at most
60 lines per session; read the entry for the date behind a claim, not the whole file.

Style is a skill: load `style` (`.claude/skills/style/SKILL.md`) before writing or editing code.
One-line docstrings, two-line comments, no prose that the code or the journal already carries.
`just lint` enforces the mechanical half through `scripts/style_check.py`.

## Layout
- `python/content_factory/` — control plane (FastAPI, Typer CLI, Temporal workflows, schemas).
  `apps/api`, `apps/cli`, `apps/workers` are thin entry points into it.
- `packages/content-schema-ts` — generated TS types + Ajv validators from the Pydantic source of
  truth. Never edit `generated/` or `schema/` by hand: run `just schemas`.
- `packages/editor-core` — typed reversible edit operations (TS, shared browser/server).
- `apps/renderer` — Remotion compositions and the render scripts.
- `external/`, `models/`, `datasets/`, `output/`, `.venvs/`, `sandbox/` — the git-ignored local AI
  stack, laid out ComfyUI-style. See docs/setup.md "Local AI stack". `datasets/` is built by
  `just datasets link` from `content_factory.libraries` (`docs/datasets.md`); the unrelated
  `content_factory.datasets` compiles an uploaded CSV into a typed table.
- `docs/adr/` — twelve ADRs; do not add more without a real decision. `docs/research/` — dated
  official-doc research with URLs.

## Commands
- `./setup.sh` (idempotent) · `just doctor` · `just up` / `just down`
- `just stop` — one button: the active run (local and durable), the worker, API/web, the GPU
  servers, compose. `just stop-run` stops only the run; `--dry-run` explains without touching.
- `just schemas` (regenerate contracts) · `just schemas-check` (drift)
- `just fmt` · `just lint` · `just typecheck` (ruff, style_check, oxlint, pyright, tsc). Add
  oxlint rules in `.oxlintrc.json` with a reason, never per package.
- `just test` — core suites: no internet, keys, GPU, or live accounts (marker expression defined
  once in `pyproject.toml` `addopts`).
- `just test-integration` — needs compose (postgres, temporal). **Run it before closing any phase
  that touches a stage executor or a contract a stage writes**; the core suite cannot reach
  `ProductionWorkflow`.
- `just render-smoke` — Remotion clip + ffprobe assertions.
- Contracts: add a Pydantic model to `schemas/registry.py`, run `just schemas`, commit
  `packages/content-schema-ts/{schema,generated}` and `fixtures/schema`.
- Migrations: edit `db/models.py`, `uv run alembic revision --autogenerate -m "..."`, review the
  file, `just migrate`; `uv run alembic check` must report no drift. API tests use the compose test
  database (`DATABASE_URL_TEST`) and truncate all tables per test.

## Rules that are not negotiable
- Never publish/push/upload externally during development; mocks and fixtures by default.
- No billing/subscription/entitlement code anywhere (docs/scale-later.md lists the gap).
- Contracts start as Pydantic models; unknown fields are errors; IDs are opaque strings.
- Workflow code is deterministic; every activity is idempotent and safe to run twice.
- Safety guardrails (PersonaFirewall, minor safety, crisis protocol, disclosure) are code, not config.
- Pin dependencies exactly; resolve versions from the registry, never from memory.
- Never commit `.env`, keys, or anything under `data/`, `projects/`, `.comfy/`.
- Every phase ends with the commands actually run and their results appended to the current
  `docs/journal/<year>-<month>.md`, and the one-line verdict updated in `STATUS.md`. Session
  write-ups go in the journal, never in STATUS.md.
- Another session may share this checkout: prefer targeted edits over whole-file writes, and
  re-check `git status` before committing.
