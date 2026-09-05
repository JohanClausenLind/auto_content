# ADR 0001 — Control plane and durable workflow engine

- Status: accepted (2026-08-27)
- Research: docs/research/infra-and-frontend.md, docs/research/cms-email-analytics-and-python-libs.md

## Context
Content Factory is a single-operator, local-first platform that must still keep the invariants of
a multi-workspace system (stateless API, Postgres as transactional truth, durable workflows,
worker pools, `workspace_id` on every domain row). Production runs span hours, wait on humans
(approval, human task slots), call paid providers, and must survive process restarts without
duplicating side effects.

## Decision
- **Python 3.12 control plane**: FastAPI + Uvicorn (API), Typer + Rich (CLI), Pydantic v2 +
  pydantic-settings (contracts and typed configuration), SQLAlchemy 2 + Alembic on **PostgreSQL 18**
  (psycopg 3 driver), HTTPX, structlog. All pinned exactly in `pyproject.toml` / `uv.lock`
  (versions resolved from PyPI on 2026-08-27, e.g. temporalio 1.32.0, FastAPI 0.141.1,
  SQLAlchemy 2.0.52, pydantic 2.13.4).
- **Temporal** is the only durable orchestrator: production runs, distribution campaigns, program
  ticks (Temporal Schedules), analytics syncs, imports, Radar sweeps. Locally the **Temporal CLI
  dev server** (`temporalio/temporal:1.8.2`, MIT, bundled MIT Web UI) runs from compose with a
  persisted SQLite file; Temporal's history store stays separate from the application database.
- Workflow code is deterministic; every activity has typed IO, an idempotency key derived from
  `workspace | project | stage | revision | input hash`, bounded retries, and is safe to execute
  twice. Waiting states (`WAITING_FOR_APPROVAL`, parked human task slots) are `wait_condition`
  on signals and hold no worker slot. Replay tests run offline against recorded histories.
- Modular monolith: API, workflow workers, render workers, and the web app deploy separately but
  live in one repository and one Python package. Splitting further needs a measured reason.

## Alternatives considered
- Celery/RQ/Dramatiq: queues, not durable workflows; no replay, signals, or long timers.
- Prefect/Dagster: batch-data oriented; weak on multi-day human-in-the-loop waits.
- Hand-rolled state machines in Postgres: exactly the code Temporal removes; error-prone.
- Hatchet (evaluated 2026-09-03, after acceptance): MIT, Postgres-backed engine with built-in
  per-key concurrency/rate limits — the lighter choice for stateless fan-out job queues, and the
  credible re-evaluation if a self-hosted Temporal *cluster* ever feels too heavy. Rejected here
  because the core workload is signal-driven multi-day runs whose invariants are proven by
  deterministic replay of recorded histories and by Temporal Schedule policies (overlap SKIP,
  catch-up); Hatchet's durable execution (v1, ~2025) is younger, has no offline replay-test
  story, and migrating would re-prove exactly-once publishing and stale-approval handling to
  save roughly one container.

## Consequences
- Operators run one extra container (Temporal dev server). Its UI is the low-level ops view; the
  Pipeline Canvas is the product view.
- `temporalio.testing.WorkflowEnvironment.start_time_skipping()` downloads a test-server binary,
  so core CI uses recorded-history replay instead; time-skipping tests are opt-in.
