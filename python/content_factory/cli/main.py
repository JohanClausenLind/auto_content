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
def config(
    show_defaults: bool = typer.Option(False, help="Print the effective configuration as YAML."),
) -> None:
    """Validate and print the effective configuration (secrets are never printed)."""
    from content_factory.config import get_settings

    settings = get_settings()
    console.print_json(json.dumps(settings.model_dump(mode="json"), indent=2))
    _ = show_defaults
