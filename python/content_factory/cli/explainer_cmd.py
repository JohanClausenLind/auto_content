"""``content-factory explainer …``: run, inspect, export and verify one explainer episode."""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(help="The explainer lane: a resumable step graph with one ledger per episode.")

ROOT_HELP = "Episodes folder (default: output/explainer/episodes)."


def _episode_dir(episode_id: str, root: str) -> Path:
    from content_factory.explainer.pipeline import DEFAULT_OUTPUT_ROOT

    return (Path(root) if root else DEFAULT_OUTPUT_ROOT) / episode_id


@app.command("run")
def run_episode(
    config: Path = typer.Argument(..., exists=True, dir_okay=False, help="Episode config JSON."),
    from_step: str = typer.Option("", "--from", help="Re-run this step and every later one."),
    until: str = typer.Option("", "--until", help="Stop after this step."),
) -> None:
    """Run one explainer episode, reusing every step whose inputs and artifacts are unchanged."""
    from content_factory.explainer.pipeline import EpisodeConfig, run

    result = run(EpisodeConfig.load(config), from_step=from_step or None, until=until or None)
    typer.echo(json.dumps(result.as_dict()))
    raise typer.Exit(code=0 if result.status in {"done", "incomplete"} else 1)


@app.command("status")
def status(
    episode_id: str = typer.Argument(..., help="The episode_id from its config."),
    root: str = typer.Option("", help=ROOT_HELP),
) -> None:
    """Print each step's status, fingerprint and finish time from the episode's ledger."""
    from content_factory.explainer.pipeline import Ledger

    data = Ledger.load(_episode_dir(episode_id, root)).data
    typer.echo(f"{episode_id}: {data['status']}, ready={str(data['ready']).lower()}")
    for entry in data["steps"]:
        error = (entry["error"] or "")[:100]
        finished = entry["finished_at"] or "-"
        line = f"  {entry['step']:<16} {entry['status']:<8} {entry['fingerprint'][:12]}  {finished}"
        typer.echo(f"{line}  {error}".rstrip())
    if data.get("stopped"):
        typer.echo(f"  stopped: {json.dumps(data['stopped'])}")


@app.command("export")
def export(
    episode_id: str = typer.Argument(..., help="The episode_id from its config."),
    root: str = typer.Option("", help=ROOT_HELP),
) -> None:
    """Rebuild the episode's exports folder from the final revision its ledger records."""
    from content_factory.explainer.export import export_bundle
    from content_factory.explainer.pipeline import CONFIG_NAME, QUEUE_NAME, EpisodeConfig, Ledger

    folder = _episode_dir(episode_id, root)
    final_qc = Ledger.load(folder).entry("qc_final") or {}
    if (folder / QUEUE_NAME).is_file() or final_qc.get("status") != "done":
        typer.echo(f"{episode_id} has not passed qc_final; see `content-factory explainer queue`")
        raise typer.Exit(code=1)
    config = EpisodeConfig.model_validate_json((folder / CONFIG_NAME).read_text())
    manifest = export_bundle(folder, config=config)
    summary = {
        "exports": str(folder / "exports"),
        "files": len(manifest.files),
        "ai_disclosure": manifest.ai_disclosure,
        "paid_promotion": manifest.paid_promotion,
    }
    typer.echo(json.dumps(summary))


@app.command("verify")
def verify(
    episode_id: str = typer.Argument(..., help="The episode_id from its config."),
    root: str = typer.Option("", help=ROOT_HELP),
) -> None:
    """Check the export against the ledger, the mp4, the captions, the chapters and the stems."""
    from content_factory.explainer.export import verify_export

    issues = verify_export(_episode_dir(episode_id, root))
    for issue in issues:
        typer.echo(str(issue))
    typer.echo(json.dumps({"verified": not issues, "issues": len(issues)}))
    raise typer.Exit(code=1 if issues else 0)


@app.command("queue")
def queue(
    episode_id: str = typer.Argument(..., help="The episode_id from its config."),
    root: str = typer.Option("", help=ROOT_HELP),
) -> None:
    """List the blocked findings a person has to resolve, with any typed repair on offer."""
    from content_factory.explainer.pipeline import QUEUE_NAME

    path = _episode_dir(episode_id, root) / QUEUE_NAME
    if not path.is_file():
        typer.echo(f"{episode_id}: no blocked findings")
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    typer.echo(f"{episode_id}: blocked at {payload['step']}, {len(payload['items'])} item(s)")
    for item in payload["items"]:
        where = f"scene={item['scene_id']} beat={item['beat_id']} at={item['at_ms']} ms"
        typer.echo(f"- {item['check']} {where}: {item['evidence']}")
        if item["repair"]:
            typer.echo(f"  repair: {json.dumps(item['repair'])}")
