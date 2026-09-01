"""Typer CLI entry point: ``content-factory``.

Commands are added phase by phase; each one calls the same service layer as the API.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console

from content_factory import __version__

app = typer.Typer(
    name="content-factory",
    help="Content Factory — local-first content production and distribution.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Print version and exit."),
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@app.command()
def doctor(as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")) -> None:
    """Check services, runtimes, GPU, configuration, and explain problems in plain language."""
    from content_factory.doctor import run_doctor

    report = run_doctor()
    if as_json:
        console.print_json(json.dumps(report.model_dump(mode="json")))
    else:
        report.render(console)
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind address (default from config; loopback)."),
    port: int | None = typer.Option(None, help="Port (default from config)."),
    reload: bool = typer.Option(False, help="Auto-reload for development."),
) -> None:
    """Run the API server (loopback by default; use `tailscale serve` for tailnet access)."""
    import uvicorn

    from content_factory.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "content_factory.api.app:app",
        host=host or settings.bind,
        port=port or settings.port,
        reload=reload,
        log_level="info",
    )


@app.command()
def bootstrap(
    owner: str = typer.Option("operator", help="Owner account username."),
    password: str | None = typer.Option(None, help="Owner password (generated if omitted)."),
    demo: bool = typer.Option(True, help="Seed two demo workspaces."),
) -> None:
    """Create the owner account and demo workspaces (idempotent). Prints a generated password once."""  # noqa: E501
    import asyncio

    from content_factory.db.session import session_scope
    from content_factory.services.bootstrap import bootstrap as _bootstrap

    async def _run() -> None:
        async with session_scope() as db:
            res = await _bootstrap(db, owner_username=owner, password=password, demo=demo)
        console.print(f"owner account: [bold]{res.owner_username}[/]")
        if res.generated_password:
            console.print(f"generated password (shown once): [bold red]{res.generated_password}[/]")
        console.print(f"workspaces created: {list(res.workspaces) or 'none (already present)'}")

    asyncio.run(_run())


@app.command()
def login(
    username: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
    api_url: str | None = typer.Option(None, help="API base URL (default from context)."),
) -> None:
    """Sign in to the API and store the session in ~/.config/content-factory/context.json."""
    from content_factory.cli.client import ApiClient, ApiError
    from content_factory.cli.context import CliContext

    ctx = CliContext.load()
    if api_url:
        ctx.api_url = api_url
    client = ApiClient(ctx)
    try:
        view = client.login(username, password)
        if view.get("mfa_required"):
            code = typer.prompt("One-time code")
            view = client.totp(code)
    except ApiError as exc:
        console.print(f"[red]login failed:[/] {exc.detail}")
        raise typer.Exit(code=1) from exc
    ctx.workspace_id = view.get("current_workspace_id")
    ctx.save()
    console.print(
        f"signed in as [bold]{view['account']['username']}[/]; workspaces: {[w['slug'] for w in view['workspaces']]}"  # noqa: E501
    )


@app.command()
def whoami() -> None:
    """Show the current session and workspace."""
    from content_factory.cli.client import ApiClient, ApiError
    from content_factory.cli.context import CliContext

    ctx = CliContext.load()
    try:
        view = ApiClient(ctx).session()
    except ApiError as exc:
        console.print(f"[yellow]not signed in[/] ({exc.status}); run `content-factory login`")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(view))


@app.command()
def logout() -> None:
    """Revoke the CLI session."""
    from content_factory.cli.client import ApiClient, ApiError
    from content_factory.cli.context import CliContext

    ctx = CliContext.load()
    try:
        ApiClient(ctx).logout()
    except ApiError:
        pass
    ctx.save()
    console.print("signed out")


workspaces_app = typer.Typer(help="Workspaces")
app.add_typer(workspaces_app, name="workspaces")


@workspaces_app.command("list")
def workspaces_list() -> None:
    from content_factory.cli.client import ApiClient
    from content_factory.cli.context import CliContext

    for w in ApiClient(CliContext.load()).workspaces():
        console.print(f"{w['id']}  {w['slug']:<20} {w['name']:<30} {w['role']}")


@workspaces_app.command("use")
def workspaces_use(workspace_id: str) -> None:
    from content_factory.cli.client import ApiClient
    from content_factory.cli.context import CliContext

    ctx = CliContext.load()
    view = ApiClient(ctx).switch_workspace(workspace_id)
    ctx.workspace_id = view["current_workspace_id"]
    ctx.save()
    console.print(f"using workspace {ctx.workspace_id}")


@app.command()
def worker(
    queue: str = typer.Option("control", help="Task queue / resource class to serve."),
) -> None:
    """Run a Temporal worker for one task queue."""
    from content_factory.workflows.worker import main as run

    run(queue)


@app.command()
def demo(quality: str = typer.Option("smoke", help="smoke (one scene) | demo (full clip)")) -> None:
    """Offline demo: fixture campaign → DAG → single-image PNG + short MP4 → QC → run report."""
    from content_factory.runners.demo import run_demo

    if quality not in {"smoke", "demo"}:
        raise typer.BadParameter("quality must be smoke or demo")
    report = run_demo(quality=quality)
    for did, d in report["deliverables"].items():
        status = "[green]PASS[/]" if d["qc_passed"] else "[red]FAIL[/]"
        console.print(f"{status} {d['type']:<18} {did}  {d['artifact']['key']}")
        for f in d["qc"]:
            console.print(f"     - {f['severity']}: {f['check']}: {f['message']}")
    console.print(
        f"run report: projects/{report['project_id']}/final/run-report.json ({report['elapsed_s']} s)"  # noqa: E501
    )
    raise typer.Exit(code=0 if report["passed"] else 1)


runs_app = typer.Typer(help="Production runs")
app.add_typer(runs_app, name="runs")


@runs_app.command("start")
def runs_start(quality: str = typer.Option("demo")) -> None:
    """Start the fixture campaign as a durable production run (requires worker + temporal)."""
    import asyncio

    from content_factory.schemas.fixtures import sample_campaign
    from content_factory.services.runs import start_run

    run_id = asyncio.run(start_run(sample_campaign(), quality=quality))
    console.print(
        f"started [bold]{run_id}[/] — approve with: content-factory runs approve {run_id}"
    )


@runs_app.command("status")
def runs_status(run_id: str) -> None:
    import asyncio

    from content_factory.db.session import session_scope
    from content_factory.schemas.fixtures import WS
    from content_factory.services.runs import run_view

    async def _run() -> None:
        async with session_scope() as db:
            view = await run_view(db, WS, run_id)
        if view is None:
            console.print("[red]not found[/]")
            raise typer.Exit(1)
        console.print(f"[bold]{view['state']}[/] {view['run_id']}  project={view['project_id']}")
        for n in view["nodes"]:
            mark = {
                "complete": "[green]✔[/]",
                "running": "[yellow]…[/]",
                "failed": "[red]✘[/]",
                "queued": "·",
            }.get(n["state"], n["state"])
            cache = " (cache)" if n["cache_hit"] else ""
            console.print(f"  {mark} {n['node_id']}{cache}")

    asyncio.run(_run())


@runs_app.command("approve")
def runs_approve(
    run_id: str, reject: bool = typer.Option(False), reason: str = typer.Option("")
) -> None:
    """Approve (or reject) the waiting preflight revision of a run."""
    import asyncio

    from content_factory.db.session import session_scope
    from content_factory.schemas.fixtures import WS
    from content_factory.services.runs import approve_run, run_view

    async def _run() -> None:
        async with session_scope() as db:
            view = await run_view(db, WS, run_id)
        if view is None or not view["preflight_revision_hash"]:
            console.print("[red]run not found or not waiting for approval[/]")
            raise typer.Exit(1)
        await approve_run(
            run_id,
            actor="cli-operator",
            revision_hash=view["preflight_revision_hash"],
            decision="reject" if reject else "approve",
            reason=reason,
        )
        console.print(
            "rejected" if reject else f"approved revision {view['preflight_revision_hash'][:12]}"
        )

    asyncio.run(_run())


distribution_app = typer.Typer(help="Distribution safety controls")
app.add_typer(distribution_app, name="distribution")


@distribution_app.command("kill-switch")
def distribution_kill_switch(
    state: str = typer.Argument(..., help="on | off"),
    confirm: bool = typer.Option(False, "--confirm", help="Required."),
) -> None:
    """Flip the global distribution kill switch for the fixture workspace (also in the UI/PWA)."""
    if state not in {"on", "off"}:
        raise typer.BadParameter("state must be on or off")
    if not confirm:
        console.print("[red]refusing without --confirm[/]")
        raise typer.Exit(1)
    import asyncio

    from content_factory.db.base import utcnow
    from content_factory.db.models import DistributionState
    from content_factory.db.session import session_scope
    from content_factory.schemas.fixtures import WS

    async def _run() -> None:
        async with session_scope() as db:
            row = await db.get(DistributionState, WS)
            if row is None:
                row = DistributionState(workspace_id=WS, kill_switch=state == "on")
                db.add(row)
            else:
                row.kill_switch = state == "on"
            row.updated_by = "cli-operator"
            row.updated_at = utcnow()
        console.print(f"kill switch is now [bold]{state.upper()}[/]")

    asyncio.run(_run())


audit_app = typer.Typer(help="Audit log")
app.add_typer(audit_app, name="audit")


@audit_app.command("export")
def audit_export(
    since: str = typer.Option("1970-01-01", help="ISO date lower bound."),
    out: str = typer.Option("-", help="Output file (JSONL) or - for stdout."),
) -> None:
    """SIEM-shaped JSONL export of the append-only audit log."""
    import asyncio
    import json as _json
    import sys
    from datetime import date

    from sqlalchemy import select

    from content_factory.db.models import AuditEvent
    from content_factory.db.session import session_scope

    lower = date.fromisoformat(since)

    async def _run() -> None:
        async with session_scope() as db:
            rows = (
                (await db.execute(select(AuditEvent).order_by(AuditEvent.created_at)))
                .scalars()
                .all()
            )
        stream = sys.stdout if out == "-" else open(out, "w", encoding="utf-8")  # noqa: ASYNC230
        try:
            n = 0
            for a in rows:
                if a.created_at.date() < lower:
                    continue
                stream.write(
                    _json.dumps(
                        {
                            "ts": a.created_at.isoformat(),
                            "event.id": a.id,
                            "event.action": a.action,
                            "actor.id": a.actor_account_id,
                            "workspace.id": a.workspace_id,
                            "target.type": a.target_type,
                            "target.id": a.target_id,
                            "source.ip": a.ip,
                            "event.detail": a.detail,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                n += 1
        finally:
            if stream is not sys.stdout:
                stream.close()
        if out != "-":
            console.print(f"wrote {n} events to {out}")

    asyncio.run(_run())


@app.command()
def mcp() -> None:
    """Run the MCP server on stdio (the only external-agent surface)."""
    from content_factory.mcp_server import main as run_mcp

    run_mcp()


@app.command()
def config(
    show_defaults: bool = typer.Option(False, help="Print the effective configuration as YAML."),
) -> None:
    """Validate and print the effective configuration (secrets are never printed)."""
    from content_factory.config import get_settings

    settings = get_settings()
    console.print_json(json.dumps(settings.model_dump(mode="json"), indent=2))
    _ = show_defaults
