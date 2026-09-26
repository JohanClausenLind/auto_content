"""``content-factory explainer …``: run, inspect, export and verify one explainer episode."""

from __future__ import annotations

import getpass
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import typer

if TYPE_CHECKING:
    from content_factory.explainer.pipeline import EpisodeConfig
    from content_factory.schemas.explainer import EvidencePack, ScriptPlan, TypedRepair, VisualSpec

app = typer.Typer(help="The explainer lane: a resumable step graph with one ledger per episode.")

ROOT_HELP = "Episodes folder (default: output/explainer/episodes)."
CONFIG_HELP = "The episode is read from this config JSON file."
EPISODE_HELP = "The episode is the one with this episode_id in its config."
FOLDER_HELP = "Episodes are looked up in this folder, output/explainer/episodes by default."
REASON_HELP = "The reason is kept in the patch history beside the change."
AUTHOR_HELP = "The author is recorded with the patch; it defaults to the login name."
# Webpage text reaches the terminal: control characters print as \xNN, never as escapes.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


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


def _plain(text: str) -> str:
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def _fail(message: str) -> NoReturn:
    typer.echo(_plain(message), err=True)
    raise typer.Exit(code=1)


def _raw(config: EpisodeConfig) -> tuple[EvidencePack, ScriptPlan, VisualSpec]:
    from content_factory.explainer.errors import EpisodeInvalidError
    from content_factory.explainer.pipeline import read_model
    from content_factory.schemas.explainer import EvidencePack, ScriptPlan, VisualSpec

    try:
        pack = read_model(EvidencePack, config.pack)
        return pack, read_model(ScriptPlan, config.script), read_model(VisualSpec, config.spec)
    except EpisodeInvalidError as error:
        _fail(str(error))


def _inputs(config: EpisodeConfig) -> tuple[EvidencePack, ScriptPlan, VisualSpec]:
    """The config's pack, script and spec with the recorded spec and script patches applied."""
    from content_factory.explainer.errors import EpisodeInvalidError
    from content_factory.explainer.patches import (
        SCRIPT_KINDS,
        SPEC_KINDS,
        apply_patches,
        load_patches,
        of_kinds,
    )

    pack, script, spec = _raw(config)
    try:
        records = of_kinds(load_patches(config.patches), SPEC_KINDS | SCRIPT_KINDS)
        spec, script, _ = apply_patches(spec, script, {}, records)
    except EpisodeInvalidError as error:
        _fail(str(error))
    return pack, script, spec


def _patches_path(config: EpisodeConfig) -> Path:
    if config.patches is None:
        _fail('the config sets "patches": null; name a patches.jsonl to record reviewer patches')
    return config.patches


def _record(
    config: EpisodeConfig, repair: TypedRepair, reason: str, author: str, take: Path | None
) -> None:
    """Check, append and report one patch with the steps the next run redoes."""
    from content_factory.explainer.errors import EpisodeInvalidError
    from content_factory.explainer.patches import record_patch
    from content_factory.explainer.pipeline import invalidated_steps

    path = _patches_path(config)
    pack, script, spec = _raw(config)
    try:
        record, appended = record_patch(
            path,
            repair,
            reason=reason,
            author=author or getpass.getuser(),
            pack=pack,
            script=script,
            spec=spec,
            take=take,
        )
    except EpisodeInvalidError as error:
        _fail(str(error))
    reruns = invalidated_steps(config)
    verb = "recorded" if appended else "already recorded"
    typer.echo(f"patch {record.patch_id} ({repair.repair}) {verb} in {path}")
    typer.echo(f"the next run redoes: {', '.join(reruns) or 'nothing'}")
    summary = {"patch_id": record.patch_id, "appended": appended, "reruns": list(reruns)}
    typer.echo(json.dumps(summary))


@app.command("evidence")
def evidence(
    config: Path = typer.Argument(..., exists=True, dir_okay=False, help=CONFIG_HELP),
) -> None:
    """Print every source with its URL and captures, and each claim with its evidence and scenes."""
    from content_factory.explainer.pipeline import EpisodeConfig, read_model
    from content_factory.explainer.review_page import claim_citations, segment_citations
    from content_factory.schemas.explainer import SourceCaptureManifest

    episode = EpisodeConfig.load(config)
    pack, script, spec = _inputs(episode)
    captures: dict[str, list[str]] = {
        s.source_id: [s.capture_id] if s.capture_id else [] for s in pack.sources
    }
    for item in episode.captures:
        manifest = read_model(SourceCaptureManifest, item.manifest)
        known = captures.setdefault(manifest.source_id, [])
        if manifest.capture_id not in known:
            known.append(manifest.capture_id)
    typer.echo(f"Sources ({len(pack.sources)})")
    for source in pack.sources:
        typer.echo(_plain(f"- {source.source_id}: {source.title or '(untitled)'}"))
        typer.echo(_plain(f"  {source.publisher or 'publisher not stated'} <{source.url}>"))
        typer.echo(f"  captures: {', '.join(captures[source.source_id]) or 'none'}")
    scenes, segments = claim_citations(spec), segment_citations(script)
    items = {i.item_id: i for i in pack.items}
    typer.echo(f"Claims ({len(pack.claims)})")
    for claim in pack.claims:
        q = claim.value
        value = f" = {q.magnitude:g} {q.unit} ({q.uncertainty})" if q else ""
        typer.echo(
            _plain(f"- {claim.claim_id} [{claim.epistemic_class}]{value}: {claim.statement}")
        )
        for evidence_id in claim.evidence_ids:
            item = items[evidence_id]
            typer.echo(_plain(f'  evidence {evidence_id} ({item.source_id}): "{item.passage}"'))
        if claim.calculation_id:
            typer.echo(f"  derived by calculation {claim.calculation_id}")
        cited = ", ".join(scenes.get(claim.claim_id, ())) or "none"
        spoken = ", ".join(segments.get(claim.claim_id, ())) or "none"
        typer.echo(f"  scenes: {cited}; segments: {spoken}")


@app.command("findings")
def findings(
    episode_id: str = typer.Argument(..., help=EPISODE_HELP),
    root: str = typer.Option("", help=FOLDER_HELP),
) -> None:
    """Print every QC and review finding with scene, beat, time, frame, verdict and any repair."""
    from content_factory.explainer.review_page import clock_text, finding_rows, qc_tallies

    folder = _episode_dir(episode_id, root)
    try:
        tallies, rows = qc_tallies(folder), finding_rows(folder)
    except FileNotFoundError as error:
        _fail(str(error))
    for tally in tallies:
        typer.echo(
            f"{tally.step}: {tally.passed} checks passed, {tally.failed} failed, "
            f"{tally.unknown} not measured"
        )
    for row in rows:
        f = row.finding
        at = f.interval.start_ms
        link = f"{row.video}#t={at / 1000:.3f}" if row.video else "reviewed video not on disk"
        where = f"scene={f.scene_id} beat={row.beat_id or '-'} t={clock_text(at)} frame={row.frame}"
        typer.echo(f"- {row.step} {f.finding_id} {where} {link}")
        typer.echo(_plain(f"  {f.category}/{f.severity}/{f.disposition}: {f.observed}"))
        typer.echo(_plain(f"  evidence: {f.evidence}"))
        if f.proposed_repair:
            typer.echo(f"  repair: {json.dumps(f.proposed_repair.model_dump(mode='json'))}")
    typer.echo(json.dumps({"findings": len(rows)}))


@app.command("patch")
def patch(
    config: Path = typer.Argument(..., exists=True, dir_okay=False, help=CONFIG_HELP),
    repair: str = typer.Option(
        ...,
        help=(
            'The repair is one TypedRepair as JSON, for example {"repair": "text_correction", '
            '"entity_id": "ent_x", "text": "Rear sprocket"} to correct the words on screen.'
        ),
    ),
    reason: str = typer.Option(..., help=REASON_HELP),
    author: str = typer.Option("", help=AUTHOR_HELP),
) -> None:
    """Check a typed repair against the current spec and script, record it, and list what reruns."""
    from pydantic import TypeAdapter, ValidationError

    from content_factory.explainer.patches import take_path_for
    from content_factory.explainer.pipeline import EpisodeConfig
    from content_factory.schemas.explainer import TakeSelectionRepair, TypedRepair

    episode = EpisodeConfig.load(config)
    try:
        typed: TypedRepair = TypeAdapter(TypedRepair).validate_json(repair)
    except ValidationError as error:
        _fail("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()))
    take = None
    if isinstance(typed, TakeSelectionRepair):
        takes_dir = episode.takes_dir or _patches_path(episode).parent / "takes"
        take = take_path_for(typed.take_id, typed.segment_id, takes_dir)
        if not take.is_file():
            _fail(f"no recording {take}; add one with `content-factory explainer take`")
    _record(episode, typed, reason, author, take)


@app.command("take")
def take(
    config: Path = typer.Argument(..., exists=True, dir_okay=False, help=CONFIG_HELP),
    segment: str = typer.Option(..., help="The recording replaces this script segment's take."),
    wav: Path = typer.Option(
        ..., exists=True, dir_okay=False, help="The new recording is this 16-bit PCM WAV file."
    ),
    reason: str = typer.Option(..., help=REASON_HELP),
    author: str = typer.Option("", help=AUTHOR_HELP),
) -> None:
    """Store a new recording for one script segment and record the patch that selects it."""
    from content_factory.explainer.patches import store_take
    from content_factory.explainer.pipeline import EpisodeConfig
    from content_factory.schemas.explainer import TakeSelectionRepair

    episode = EpisodeConfig.load(config)
    _, script, _ = _inputs(episode)
    if all(s.segment_id != segment for s in script.segments):
        known = ", ".join(s.segment_id for s in script.segments)
        _fail(f"the script has no segment {segment}; segments: {known}")
    takes_dir = episode.takes_dir or _patches_path(episode).parent / "takes"
    try:
        take_id, stored = store_take(wav, segment, takes_dir)
    except ValueError as error:
        _fail(str(error))
    repair = TakeSelectionRepair(repair="take_selection", segment_id=segment, take_id=take_id)
    _record(episode, repair, reason, author, stored)


@app.command("rerender")
def rerender(
    config: Path = typer.Argument(..., exists=True, dir_okay=False, help=CONFIG_HELP),
) -> None:
    """Run the episode again after review, redoing only the steps a patch or take invalidated."""
    from content_factory.explainer.pipeline import EpisodeConfig, run

    result = run(EpisodeConfig.load(config))
    typer.echo(json.dumps(result.as_dict()))
    raise typer.Exit(code=0 if result.status in {"done", "incomplete"} else 1)


@app.command("page")
def page(
    episode_id: str = typer.Argument(..., help=EPISODE_HELP),
    root: str = typer.Option("", help=FOLDER_HELP),
) -> None:
    """Write review.html in the episode folder with the video, transcript, findings and sources."""
    from content_factory.explainer.review_page import write_review_page

    try:
        path = write_review_page(_episode_dir(episode_id, root))
    except FileNotFoundError as error:
        _fail(str(error))
    typer.echo(json.dumps({"page": str(path)}))
