# Development

- `just fmt` / `just lint` / `just typecheck` before committing; CI runs the same.
- Python: ruff (format + lint), pyright standard mode, pytest with `asyncio_mode=auto`.
  Markers: `integration` (compose services), `gpu`, `live` (never in core CI).
- Node: strict TypeScript 7, vitest 4; each package owns `typecheck` and `test` scripts.
- Contracts: add a Pydantic model to `schemas/registry.py`, run `just schemas`, commit
  `packages/content-schema-ts/{schema,generated}` and `fixtures/schema`.
- Migrations: edit `db/models.py`, run `uv run alembic revision --autogenerate -m "..."`, review
  the generated file, `just migrate`. `uv run alembic check` must report no drift.
- API tests use the compose test database (`DATABASE_URL_TEST`) and an in-process ASGI client;
  they truncate all tables per test.
- Never commit `.env`, keys, or anything under `data/`, `projects/`, `.comfy/`.
