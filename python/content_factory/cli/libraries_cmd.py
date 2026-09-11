"""``content-factory datasets …``: what downloaded data is on this host, and what reads it.

Named ``datasets`` at the command line because that is what an operator calls them, and
:mod:`content_factory.libraries` in code because ``content_factory.datasets`` already means
something else here (an uploaded CSV compiled into a typed table).

Three questions, three commands. ``list`` says what is declared and whether it is present; ``link``
builds the ``datasets/<category>/<Name>`` tree that makes it discoverable from the repo; ``check``
is the one that earns the module — it says which libraries nothing reads, which is the fact that
took grepping 236 runs to establish the first time.
"""

from __future__ import annotations

import json

import typer

app = typer.Typer(help="Downloaded data libraries: where they are, and what reads them.")


@app.command("list")
def list_libraries(
    category: str = typer.Option("", help="Only this category"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable"),
) -> None:
    """Every declared library, grouped by category, with presence on this host."""
    from content_factory.libraries import by_category

    groups = by_category()
    if category:
        groups = {k: v for k, v in groups.items() if k == category}
        if not groups:
            typer.echo(f"no category {category!r}", err=True)
            raise typer.Exit(2)
    if as_json:
        rows = [
            {**lib.model_dump(mode="json"), "present": lib.present()}
            for items in groups.values()
            for lib in items
        ]
        typer.echo(json.dumps(rows, indent=1))
        return
    for name, items in groups.items():
        typer.echo(f"\n{name}")
        for lib in items:
            mark = "  " if lib.present() else "! "
            reach = "unread" if not lib.read_by else f"{len(lib.read_by)} reader(s)"
            typer.echo(f"  {mark}{lib.name:24s} {lib.approx_size:>8s}  {reach}")
            typer.echo(f"      {lib.summary}")


@app.command("link")
def link_libraries(
    dry_run: bool = typer.Option(False, "--dry-run", help="Say what would change, touch nothing"),
) -> None:
    """Build the ``datasets/<category>/<Name>`` symlink tree. Idempotent."""
    from content_factory.libraries.index import apply, plan

    outcomes = plan() if dry_run else apply()
    for outcome in outcomes:
        detail = f"  ({outcome.detail})" if outcome.detail else ""
        typer.echo(f"{outcome.action:10s} {outcome.key:24s} -> {outcome.target}{detail}")
    blocked = [o for o in outcomes if o.action == "blocked"]
    if blocked:
        typer.echo(f"\n{len(blocked)} blocked: something real is in the way", err=True)
        raise typer.Exit(1)


@app.command("check")
def check_libraries(as_json: bool = typer.Option(False, "--json")) -> None:
    """Report what is absent and what nothing reads.

    Exit code is 0 in both cases on purpose. A machine without the 19 GB reference library runs
    every lane, and a library nobody reads is a fact to act on rather than a broken build.
    """
    from content_factory.libraries import LIBRARIES, unreached

    absent = [lib for lib in LIBRARIES if not lib.present()]
    unread = unreached()
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "declared": len(LIBRARIES),
                    "absent": [lib.key for lib in absent],
                    "unread": [lib.key for lib in unread],
                },
                indent=1,
            )
        )
        return
    typer.echo(f"{len(LIBRARIES)} declared, {len(LIBRARIES) - len(absent)} present on this host")
    for lib in absent:
        typer.echo(f"  absent  {lib.key:24s} {lib.path()}")
    for lib in unread:
        typer.echo(f"  unread  {lib.key:24s} {lib.summary}")
    if not absent and not unread:
        typer.echo("  every declared library is present and has at least one reader")
