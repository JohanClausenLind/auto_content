# Content Factory — repo conventions for coding agents

Read `STATUS.md` first: it holds the current phase, what passed, and the next smallest task.

## Layout
- `python/content_factory/` — control plane (FastAPI, Typer CLI, Temporal workflows, schemas).
  `apps/api`, `apps/cli`, `apps/workers` are thin entry points into it.
- `packages/content-schema-ts` — generated TS types + Ajv validators from the Pydantic source of
  truth. Never edit `generated/` or `schema/` by hand: run `just schemas`.
- `packages/editor-core` — typed reversible edit operations (TS, shared browser/server).
- `apps/renderer` — Remotion compositions and the render scripts.
- `docs/adr/` — ten ADRs; do not add more without a real decision. `docs/research/` — dated
  official-doc research with URLs.

## Commands
- `./setup.sh` (idempotent) · `just doctor` · `just up` / `just down`
- `just schemas` (regenerate contracts) · `just schemas-check` (drift)
- `just fmt` · `just lint` · `just typecheck` (ruff, pyright, tsc)
- `just test` — core suites: no internet, keys, GPU, or live accounts
  (`uv run pytest -m "not integration and not gpu and not live"` + `pnpm -r test`)
- `just test-integration` — needs compose (postgres, temporal)
- `just render-smoke` — Remotion clip + ffprobe assertions

## Rules that are not negotiable
- Never publish/push/upload externally during development; mocks and fixtures by default.
- No billing/subscription/entitlement code anywhere (docs/scale-later.md lists the gap).
- Contracts start as Pydantic models; unknown fields are errors; IDs are opaque strings.
- Workflow code is deterministic; every activity is idempotent and safe to run twice.
- Safety guardrails (PersonaFirewall, minor safety, crisis protocol, disclosure) are code, not config.
- Pin dependencies exactly; resolve versions from the registry, never from memory.
- Every phase ends with commands actually run and their results in STATUS.md.
