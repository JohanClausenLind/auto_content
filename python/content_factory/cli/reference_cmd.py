"""``content-factory reference …``: build the reference library, and ask it questions in words.

Terse on purpose, like ``make``. Asking the library something should cost one command and a handful
of lines, because the answer is usually a list of clip ids and the point of the list is to be acted
on rather than read.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(help="The reference library: real human interaction, queryable in words.")


@app.command("build")
def build_library(
    root: str = typer.Option("", help="Reference root (default: the configured one)"),
    check: bool = typer.Option(False, "--check", help="Report whether the index is stale"),
    only: str = typer.Option("", help="Comma-separated sources, for iterating on one ingester"),
) -> None:
    """Ingest every source and build the sqlite index. Prints one line per source."""
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[3]
    cmd = [sys.executable, "-m", "content_factory.reference.build"]
    if root:
        cmd += ["--root", root]
    if check:
        cmd += ["--check"]
    if only:
        cmd += ["--only", only]
    raise typer.Exit(code=subprocess.run(cmd, cwd=repo, check=False).returncode)  # noqa: S603


@app.command("search")
def search_library(
    question: str = typer.Argument(..., help="What you are looking for, in ordinary words"),
    limit: int = typer.Option(8, help="How many matches"),
    people: int = typer.Option(0, help="Only clips with this many people (0 = any)"),
    affection: str = typer.Option(
        "", help="affection | neutral | aggression | staging; empty means any"
    ),
    usage: str = typer.Option(
        "", help="pose_derivable | pixels_usable | reference_only; empty means any"
    ),
    require_pose: bool = typer.Option(False, help="Only clips something can be driven from"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Ask the library a question. Prints the ranked clips, and what it could not answer.

    ``absent`` is the important line: those are words the library understood and has nothing for.
    It is a gap in the material, not a failure of the search, and it doubles as a shooting list.
    """
    from content_factory.config import get_settings
    from content_factory.reference.query import search
    from content_factory.schemas.reference import ReferenceQuery

    cfg = get_settings().reference
    index = Path(cfg.index_path)
    if not index.exists():
        typer.echo(
            json.dumps(
                {
                    "error": f"no index at {index}",
                    "fix": "uv run content-factory reference build",
                }
            ),
            err=True,
        )
        raise typer.Exit(code=2)

    query = ReferenceQuery(
        text=question,
        limit=limit,
        people_count=people or None,
        require_affection=affection or None,  # type: ignore[arg-type]
        require_usage=usage or None,  # type: ignore[arg-type]
        require_pose=require_pose,
    )
    result = search(index, query)

    if as_json:
        typer.echo(result.model_dump_json(indent=1))
        return
    for match in result.matches:
        typer.echo(f"{match.rank:2d}. {match.clip_id:26s} {match.score:9.3f}  {match.snippet[:70]}")
    typer.echo(f"understood: {', '.join(result.expanded_terms) or '(nothing)'}")
    if result.absent_terms:
        typer.echo(f"absent    : {', '.join(result.absent_terms)}  <- nothing on disk covers these")
    if result.unmatched_words:
        typer.echo(f"ignored   : {', '.join(result.unmatched_words)}")
    typer.echo(json.dumps({"matches": len(result.matches), "library": result.library_id}))
