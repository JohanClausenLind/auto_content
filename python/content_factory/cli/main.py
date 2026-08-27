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


@app.command()
def config(
    show_defaults: bool = typer.Option(False, help="Print the effective configuration as YAML."),
) -> None:
    """Validate and print the effective configuration (secrets are never printed)."""
    from content_factory.config import get_settings

    settings = get_settings()
    console.print_json(json.dumps(settings.model_dump(mode="json"), indent=2))
    _ = show_defaults
