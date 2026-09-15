"""Run a workflow's stages locally, in order, with the real executors and no Temporal."""

from __future__ import annotations

import json
import os
import time
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from content_factory.runners import attribution, pins
from content_factory.runners.registry import RunHandle, RunStopped, register_run
from content_factory.schemas.dag import Stage
from content_factory.schemas.fixtures import sample_campaign
from content_factory.services import durations
from content_factory.workflows.blocked import blocked_details
from content_factory.workflows.catalog import (
    WorkflowDefinitionError,
    load_definition,
    workflow_ids,
)
from content_factory.workflows.stages import STAGE_EXECUTORS, StageContext

REPO_ROOT = Path(__file__).resolve().parents[3]

Step = tuple[str, Stage, dict[str, str]]
"""``(node_key, stage, params)`` - one thing the runner does."""


def workflow_stages(workflow: str) -> tuple[Stage, ...]:
    """The stages one workflow runs, in order. Kept as a function so nothing caches a copy."""
    return load_definition(workflow).stages()


def workflow_steps(workflow: str) -> list[Step]:
    return [
        (key, stage, {k: str(v) for k, v in values.items()})
        for key, stage, values in load_definition(workflow).stage_order()
    ]


class LocalRunError(RuntimeError):
    """A stage stopped the run. ``blocked`` is set when it stopped for a person, not a defect."""

    def __init__(self, stage: Stage, report: dict, cause: BaseException) -> None:
        blocked = blocked_details(cause)
        verb = "blocked" if blocked else "failed"
        super().__init__(f"stage {stage.value} {verb}: {cause}")
        self.stage = stage
        self.report = report
        self.blocked: dict | None = blocked


def make_context(
    *,
    project_dir: Path,
    artifacts_dir: Path | None = None,
    deliverable_id: str | None = None,
    quality: str = "demo",
    brief: Mapping[str, object] | None = None,
) -> StageContext:
    """A context addressing a deliverable the campaign actually contains."""
    campaign = sample_campaign()
    if brief is not None:
        from content_factory.workspace.compile import campaign_with_brief

        campaign = campaign_with_brief(campaign, brief)
    if deliverable_id is None:
        video = next(
            (d for d in campaign.deliverables if d.type in ("short_video", "long_video")), None
        )
        deliverable_id = (video or campaign.deliverables[0]).deliverable_id
    return StageContext(
        workspace_id=campaign.workspace_id,
        project_dir=project_dir,
        artifacts_dir=artifacts_dir or project_dir / "artifacts",
        campaign=campaign,
        deliverable_id=deliverable_id,
        quality=quality,
        dep_outputs={},
    )


def run_stages(
    stages: Sequence[Stage],
    ctx: StageContext,
    *,
    report_path: Path | None = None,
    params: Mapping[Stage, Mapping[str, str]] | None = None,
    log=print,
) -> dict:
    """Execute ``stages`` in order, with parameters keyed by stage."""
    params = params or {}
    steps: list[Step] = [(s.value, s, dict(params.get(s, {}))) for s in stages]
    return run_plan(steps, ctx, report_path=report_path, log=log)


def stage_warnings(facts: dict[str, Any]) -> list[str]:
    """Facts that need an operator to decide something, as their own lines."""
    out: list[str] = []
    under = facts.get("underframed") or []
    if isinstance(under, list) and under:
        try:
            worst = min(float(u["body_fraction"]) for u in under)
            ids = ", ".join(str(u["shot_id"]) for u in under[:4])
        except (KeyError, TypeError, ValueError):
            return out
        out.append(
            f"{len(under)} shot(s) framed too small for the pose skeleton to be read, "
            f"worst {worst:.2f} of frame height: {ids}. "
            "Widen the frame or pick a clip whose cast stands closer."
        )
    return out


def _refuse_while_gpu_claimed() -> None:
    """Refuse to start while a higher-priority tenant holds the card."""
    if os.environ.get("CF_IGNORE_GPU_CLAIM") == "1":
        return
    from content_factory.services import gpu_priority

    claim = gpu_priority.blocking_claim(max_hold_s=gpu_priority.MAX_CLAIM_HOLD_S)
    if claim is None:
        return
    held = int(claim.held_for(time.time()))
    msg = (
        f"the GPU is claimed by {claim.reason!r} ({held // 60}m{held % 60:02d}s ago, "
        f"needs {claim.need_gib:.1f} GiB). It releases the claim when it finishes; "
        f"`content-factory gpu resume` puts back anything it parked, and "
        f"CF_IGNORE_GPU_CLAIM=1 overrides this."
    )
    raise LocalRunError(Stage.preflight, {"gpu_claim": claim.reason}, RuntimeError(msg))


def _merged_step_record(
    report_path: Path, steps: Sequence[tuple[str, Stage, Mapping[str, Any]]]
) -> list[dict[str, Any]]:
    """This run's node configuration, over whatever the last run in this directory recorded."""
    previous = recorded_values(report_path)
    now = {key: (stage.value, dict(values)) for key, stage, values in steps}
    order = list(previous) + [key for key in now if key not in previous]
    out: list[dict[str, Any]] = []
    for key in order:
        if key in now:
            stage_value, values = now[key]
            out.append({"node": key, "stage": stage_value, "values": values})
        else:
            out.append({"node": key, "stage": "", "values": previous[key]})
    return out


def run_plan(
    steps: Sequence[Step],
    ctx: StageContext,
    *,
    report_path: Path | None = None,
    workflow: str = "run",
    use_pins: bool = True,
    log=print,
) -> dict:
    """Execute ``steps`` in order; stop at the first failure."""
    missing = sorted({stage.value for _key, stage, _p in steps if stage not in STAGE_EXECUTORS})
    if missing:
        msg = f"stages without executors: {missing}"
        raise ValueError(msg)
    report: dict = {
        # services/durations.py estimates per (workflow, stage): generate_anchor is ~32 s on
        # image-set and ~608 s on audio-picture-story, and one median over both is wrong for both.
        "workflow": workflow,
        "project_dir": str(ctx.project_dir),
        "deliverable_id": ctx.deliverable_id,
        # What each node was configured with, so a --from resume reruns the same configuration
        # (journal 2026-09-10); merged with disk, since a slice must not forget earlier nodes.
        "steps": _merged_step_record(report_path or ctx.ddir() / "run.json", steps),
        "stages": [],
        "passed": False,
    }
    report_path = report_path or ctx.ddir() / "run.json"
    # Refused before anything is spent, not six minutes in when a later stage cannot find what a
    # pinned node was supposed to have left it.
    pinned = {} if use_pins is False else pins.load(report_path.parent)
    problems = pins.validate(report_path.parent, pinned, [key for key, _s, _p in steps])
    if problems:
        msg = "pinned nodes cannot be honoured:\n  " + "\n  ".join(problems)
        raise pins.PinError(msg)
    if pinned:
        report["pinned"] = sorted(pinned)
        log(f"--- {len(pinned)} pinned node(s), not run: {', '.join(sorted(pinned))}")
    _refuse_while_gpu_claimed()
    try:
        with register_run(workflow, ctx.project_dir) as handle:
            _run_steps(
                steps,
                ctx,
                report=report,
                report_path=report_path,
                handle=handle,
                workflow=workflow,
                pinned=pinned,
                log=log,
            )
    finally:
        _release_gpu_if_idle(log)
    report["passed"] = True
    _write_report(report_path, report)
    return report


def _release_gpu_if_idle(log=print) -> dict | None:
    """Stop the model servers once no run is left to use them."""
    from content_factory.config.settings import get_settings

    if not get_settings().local_services.release_when_idle:
        return None
    from content_factory.runners.registry import active_runs

    still_going = active_runs()
    if still_going:
        return None
    from content_factory.services.local import free_the_gpu

    freed = free_the_gpu(unload_ollama=False)
    raw = freed.get("stopped")
    stopped = [str(t) for t in raw] if isinstance(raw, list) else []
    if stopped:
        log(f"released the GPU: stopped {', '.join(stopped)} with no run left to use it")
    return freed


def _recorded_outputs(report_path: Path) -> dict[str, dict[str, Any]]:
    """What each node was last seen to produce, by node key, from the report already on disk."""
    if not report_path.is_file():
        return {}
    try:
        stages = json.loads(report_path.read_text()).get("stages") or []
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in stages:
        if isinstance(entry, dict) and entry.get("node") and isinstance(entry.get("outputs"), dict):
            out[str(entry["node"])] = entry["outputs"]
    return out


def _run_steps(
    steps: Sequence[Step],
    ctx: StageContext,
    *,
    report: dict,
    report_path: Path,
    handle: RunHandle,
    workflow: str = "run",
    pinned: Mapping[str, pins.Pin] | None = None,
    log=print,
) -> None:
    pinned = pinned or {}
    durations.refresh()  # once per run, so a long process estimates from history it helped write
    _log_eta_header(steps, workflow, log)
    # Outputs are observed, not reported: no stage executor knows its own file list. The
    # runners/attribution.py walk costs ~21 ms against stages of 48 s to 608 s.
    carried = _recorded_outputs(report_path)
    before = attribution.snapshot(ctx.project_dir)

    # The run's own paperwork, or the walk hands it to whichever step ran last. Relative paths,
    # not filenames: `controls/<shot>/run.json` is a real output of the control compiler.
    own_files = {
        path.relative_to(ctx.project_dir).as_posix()
        for path in (report_path, report_path.parent / pins.PINS_FILENAME)
        if path.is_relative_to(ctx.project_dir)
    }

    def observe() -> dict[str, Any]:
        """What the step that just ran left behind, and roll the baseline forward."""
        nonlocal before
        current = attribution.snapshot(ctx.project_dir)
        touched = [p for p in attribution.changed(before, current) if p not in own_files]
        record = attribution.node_files(touched).as_record()
        before = current
        return record

    for index, (node_key, stage, stage_params) in enumerate(steps):
        pin = pinned.get(node_key)
        if pin is not None:
            # `ok` because later stages will read the output; `pinned` so provenance says frozen,
            # not produced, and services/durations.py keeps the 0.0 s sample out of the medians.
            report["stages"].append(
                {
                    "stage": stage.value,
                    "node": node_key,
                    "ok": True,
                    "pinned": True,
                    **({"outputs": carried[node_key]} if node_key in carried else {}),
                    "seconds": 0.0,
                    "outputs_hash": pin.outputs_hash,
                    "facts": pin.facts,
                }
            )
            _write_report(report_path, report)
            # Same two-line shape as every other stage: a pinned node that printed nothing would
            # vanish from the run's output, and a pin must always be visible as a step not run.
            log(f"==> {stage.value} [{node_key}]")
            log(f"    pinned {pin.outputs_hash[:12]}, not run")
            continue
        reason = handle.stop_reason()
        if reason:
            report["stopped"] = {"reason": reason, "before": node_key}
            _write_report(report_path, report)
            log(f"    STOPPED before {node_key}: {reason}")
            raise RunStopped(reason, at=node_key, report=report)
        left = durations.forecast([s.value for _k, s, _p in steps[index:]], workflow)
        handle.note(step=node_key, eta_seconds=left.remaining_seconds)
        started = time.monotonic()
        label = stage.value if node_key == stage.value else f"{stage.value} [{node_key}]"
        mine = durations.estimate_stage(stage.value, workflow)
        # What this step should cost and what is left after it, on the line that announces it —
        # the two numbers somebody watching a terminal for ten minutes actually wants.
        shown = "  " + json.dumps(stage_params) if stage_params else ""
        log(f"==> {label}{shown}{_clock(mine, left)}")
        try:
            out = STAGE_EXECUTORS[stage](replace(ctx, params=dict(stage_params)))
        except RunStopped as stop:  # SIGTERM reached the run in the middle of this stage
            report["stopped"] = {"reason": stop.reason, "during": node_key}
            _write_report(report_path, report)
            log(f"    STOPPED during {node_key}: {stop.reason}")
            stop.report = report
            raise
        except Exception as exc:
            # A subprocess killed by the stop that killed the run raises an ordinary error; a
            # pending stop request re-labels it, since FAILED would invite a retry.
            stopping = handle.stop_reason()
            if stopping:
                report["stopped"] = {"reason": stopping, "during": node_key}
                _write_report(report_path, report)
                log(f"    STOPPED during {node_key}: {stopping}")
                raise RunStopped(stopping, at=node_key, report=report) from exc
            # A block is not a failure: the stage got an answer a person has to give, so it gets
            # its own field and the log says BLOCKED, because FAILED invites a retry.
            blocked = blocked_details(exc)
            record = {
                "stage": stage.value,
                "node": node_key,
                "ok": False,
                "seconds": round(time.monotonic() - started, 1),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-4000:],
                "outputs": observe(),
            }
            if blocked is not None:
                record["blocked"] = blocked
                report["blocked_at"] = stage.value
            report["stages"].append(record)
            _write_report(report_path, report)
            log(f"    {'BLOCKED' if blocked else 'FAILED'}: {type(exc).__name__}: {exc}")
            raise LocalRunError(stage, report, exc) from exc
        seconds = round(time.monotonic() - started, 1)
        report["stages"].append(
            {
                "stage": stage.value,
                "node": node_key,
                "ok": True,
                "seconds": seconds,
                "outputs_hash": out.outputs_hash,
                "facts": out.facts,
                "outputs": observe(),
            }
        )
        _write_report(report_path, report)
        # The countdown after this stage, so a terminal watched for ten minutes always shows how
        # much is left rather than how much was left when the run started.
        after = durations.forecast([s.value for _k, s, _p in steps[index + 1 :]], workflow)
        tail = _clock(None, after) if index + 1 < len(steps) else ""
        log(f"    ok {seconds}s {json.dumps(out.facts, default=str)[:160]}{tail}")
        for warning in stage_warnings(out.facts):
            log(f"    WARN {warning}")


def _clock(stage: durations.Estimate | None, remaining: durations.Forecast | None) -> str:
    """The timing bracket for one log line, or nothing at all."""
    parts: list[str] = []
    if stage is not None and stage.seconds >= 1:
        parts.append(f"~{stage.describe()}")
    if remaining is not None and (remaining.remaining_seconds >= 1 or remaining.unknown):
        parts.append(remaining.describe())
    return f"  [{', '.join(parts)}]" if parts else ""


def _log_eta_header(steps: Sequence[Step], workflow: str, log) -> None:
    """One line at the top saying when the whole thing should be done, and on what evidence."""
    fc = durations.forecast([s.value for _k, s, _p in steps], workflow)
    if fc.remaining_seconds <= 0 and not fc.unknown:
        return
    finish = fc.finish_at().astimezone().strftime("%H:%M")
    basis = f"median of {fc.samples} past run(s)" if fc.samples else "no history for these stages"
    log(f"--- {workflow}: {len(steps)} steps, {fc.describe()}, done about {finish} ({basis})")
    if fc.unknown:
        log(f"    not counted, never timed: {', '.join(sorted(set(fc.unknown)))}")


def _resolve_boundary(steps: Sequence[Step], name: str, which: str) -> int:
    """Index of the step ``name`` selects, by node key or by stage name."""
    by_node = [i for i, (key, _s, _p) in enumerate(steps) if key == name]
    if len(by_node) == 1:
        return by_node[0]
    by_stage = [i for i, (_k, stage, _p) in enumerate(steps) if stage.value == name]
    if len(by_stage) == 1:
        return by_stage[0]
    if len(by_stage) > 1:
        keys = [steps[i][0] for i in by_stage]
        msg = (
            f"--{which} {name!r} is ambiguous: this workflow runs that stage at nodes {keys}."
            " Name the node instead."
        )
        raise ValueError(msg)
    known = [f"{key} ({stage.value})" for key, stage, _p in steps]
    msg = f"--{which} {name!r} is not in this workflow. Steps: {known}"
    raise ValueError(msg)


# What a lane's file-input nodes stand for: the node type, and the sniffed kind that fits it.
SOURCE_NODE_KINDS: dict[str, str] = {
    "input.audio": "audio",
    "input.image": "image",
    "input.video": "video",
}


def workflow_inputs(workflow: str) -> tuple[str, ...]:
    """The kinds of file this lane needs supplied, from its declared file-input nodes."""
    template = load_definition(workflow)
    kinds = [SOURCE_NODE_KINDS[n.type] for n in template.nodes if n.type in SOURCE_NODE_KINDS]
    return tuple(dict.fromkeys(kinds))


def workflow_needs_takes(workflow: str) -> bool:
    """Whether this lane reads **one recording per beat**, named by beat id."""
    template = load_definition(workflow)
    return any(
        n.type == Stage.voice_over.value and str(n.values.get("source", "takes")) == "takes"
        for n in template.nodes
    )


def discovered_takes(project_dir: Path) -> list[str]:
    """The recordings a takes-mode lane would find in this run directory, by file name."""
    from content_factory.audio.takes import AUDIO_SUFFIXES
    from content_factory.config import get_settings

    configured = Path(get_settings().voice_over.takes_dir)
    roots = [
        project_dir / "uploads",
        configured if configured.is_absolute() else project_dir / configured,
    ]
    found: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        found += [
            p.name
            for p in sorted(root.iterdir())
            if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
        ]
    return found


def stage_inputs(
    project_dir: Path, paths: Sequence[Path], *, wanted: Sequence[str] = (), log=print
) -> list[dict[str, object]]:
    """Copy the operator's material into the run's uploads folder, sniffed and checked."""
    from content_factory.ingest.convert import CONVERTIBLE, blank_picture
    from content_factory.ingest.uploads import ALLOWED, MAX_UPLOAD_BYTES, sniff_mime

    files: list[Path] = []
    for path in paths:
        resolved = path.expanduser()
        if resolved.is_dir():
            inner = sorted(p for p in resolved.iterdir() if p.is_file())
            if not inner:
                msg = f"--input {path} is an empty directory"
                raise ValueError(msg)
            files.extend(inner)
            continue
        if not resolved.is_file():
            msg = f"--input {path} is neither a file nor a directory"
            raise ValueError(msg)
        files.append(resolved)

    uploads = project_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    staged: list[dict[str, object]] = []
    for source in files:
        mime = sniff_mime(source)
        kind = (ALLOWED.get(mime) or (CONVERTIBLE.get(mime), ""))[0]
        if not kind:
            msg = (
                f"{source.name} is {mime}, which this pipeline does not read. Convert it to"
                " mp4/mov, wav/mp3/flac or png/jpg first."
            )
            raise ValueError(msg)
        size = source.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            msg = f"{source.name} is {size / 1024**3:.1f} GiB; the limit is 2 GiB"
            raise ValueError(msg)
        blank = None
        if wanted and kind not in wanted:
            # Phones and voice-memo apps wrap sound in MP4: sound with a blank picture is a
            # recording; sound with a real picture is still the mistake this check is for.
            blank = blank_picture(source) if kind == "video" and "audio" in wanted else None
            if blank is None:
                msg = (
                    f"{source.name} is {kind}, and this lane takes {', '.join(wanted)}."
                    " Pick the lane that takes what you have (`workflows list`)."
                )
                raise ValueError(msg)
            kind = "audio"
        target = uploads / source.name
        # An identical file already staged is left alone: `--from` resumes a run whose uploads
        # folder is already correct, and copying gigabytes again to reach the same bytes is waste.
        if not (target.exists() and target.stat().st_size == size):
            target.write_bytes(source.read_bytes())
        record: dict[str, object] = {
            "file": target.name,
            "kind": kind,
            "mime": mime,
            "bytes": size,
        }
        if blank is not None:
            # The record says `audio` while the MIME says video/mp4, and the run log has to carry
            # the reason those two disagree rather than leave it to be rediscovered.
            record["picture"] = blank.reason
        staged.append(record)
    if staged:
        log(f"==> input  {json.dumps(staged)}")
    return staged


def _brief_for(workflow: str, subject: str | None) -> dict[str, object]:
    """The lane's ``input.brief`` widget values, with ``--subject`` overriding the topic."""
    template = load_definition(workflow)
    node = next((n for n in template.nodes if n.type == "input.brief"), None)
    values: dict[str, object] = dict(node.values) if node is not None else {}
    if subject:
        values["topic"] = subject
    values.setdefault("topic", template.description.strip() or template.name)
    return values


def recorded_values(report_path: Path) -> dict[str, dict[str, Any]]:
    """What each node was configured with on the run recorded at ``report_path``, by node key."""
    if not report_path.is_file():
        return {}
    try:
        recorded = json.loads(report_path.read_text()).get("steps") or []
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}
    return {
        str(entry["node"]): dict(entry.get("values") or {})
        for entry in recorded
        if isinstance(entry, dict) and entry.get("node")
    }


def resolved_steps(
    steps: Sequence[tuple[str, Stage, Mapping[str, Any]]],
    *,
    story: str | None = None,
    shots: str | None = None,
    style: str | None = None,
    subject: str | None = None,
    params: Mapping[Stage, Mapping[str, str]] | None = None,
    node_params: Mapping[str, Mapping[str, str]] | None = None,
    recorded: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[tuple[str, Stage, dict[str, Any]]]:
    """The lane's steps with every override applied, in precedence order."""
    by_stage: dict[Stage, dict[str, str]] = {}
    # Which film: the story fixture chooses the script, the shot fixture the staging.
    if story:
        by_stage.setdefault(Stage.plan_story, {})["story"] = story
    if shots:
        by_stage.setdefault(Stage.plan_shots, {}).update(
            {"planner": "fixture", "fixture_path": shots}
        )
    if subject:
        # Three models are told the one sentence: the story's visual_subject (which leads the
        # anchor prompt) and the single-clip video path, which has no ShotSpec to compile from.
        by_stage.setdefault(Stage.plan_story, {})["subject"] = subject
        by_stage.setdefault(Stage.generate_video, {})["subject"] = subject
        # The anchor's `prompt` widget is a framing instruction, not a subject: overwriting it drew
        # six wide vistas. Only a lane with no framing of its own gets the subject here.
        if not any(
            v.get("prompt", "").strip() for k, st, v in steps if st is Stage.generate_anchor
        ):
            by_stage.setdefault(Stage.generate_anchor, {})["prompt"] = subject
    if style:
        from content_factory.sequences.styles import resolve_style

        by_stage.setdefault(Stage.generate_anchor, {})["style"] = resolve_style(style)
    for stage, values in (params or {}).items():
        by_stage.setdefault(stage, {}).update({k: str(v) for k, v in values.items()})

    out: list[tuple[str, Stage, dict[str, Any]]] = []
    for key, stage, values in steps:
        # Weakest first: lane widget values, the resumed run's configuration, this invocation's
        # stage-wide overrides, then its per-node ones.
        merged: dict[str, Any] = dict(values)
        merged.update(dict((recorded or {}).get(key, {})))
        merged.update(by_stage.get(stage, {}))
        merged.update({k: str(v) for k, v in (node_params or {}).get(key, {}).items()})
        out.append((key, stage, merged))
    return out


def run_workflow(
    workflow: str,
    *,
    project_dir: Path | None = None,
    artifacts_dir: Path | None = None,
    from_stage: str | None = None,
    until_stage: str | None = None,
    use_pins: bool = True,
    quality: str = "demo",
    story: str | None = None,
    shots: str | None = None,
    style: str | None = None,
    subject: str | None = None,
    inputs: Sequence[Path] = (),
    params: Mapping[Stage, Mapping[str, str]] | None = None,
    node_params: Mapping[str, Mapping[str, str]] | None = None,
    log=print,
) -> dict:
    """Run one workflow definition end to end, or a slice of it."""
    try:
        steps = workflow_steps(workflow)
    except WorkflowDefinitionError as exc:
        msg = f"unknown workflow {workflow!r}; known: {sorted(workflow_ids())}"
        raise ValueError(msg) from exc

    # Slice from the tail first: taking --from first would leave --until indexing the original
    # list into the shortened one, which silently runs stages past the one you asked to stop at.
    if until_stage:
        steps = steps[: _resolve_boundary(steps, until_stage, "until") + 1]
    if from_stage:
        steps = steps[_resolve_boundary(steps, from_stage, "from") :]

    # Absolute paths only: the Remotion and skill subprocesses run from their own directories.
    project_dir = (project_dir or REPO_ROOT / "output" / "local-runs" / workflow).resolve()
    artifacts_dir = artifacts_dir.resolve() if artifacts_dir else None
    # The lane's own brief, not the demo fixture's, whose wind-in-Sweden question would otherwise
    # reach the image model over the top of whatever film was commissioned.
    ctx = make_context(
        project_dir=project_dir,
        artifacts_dir=artifacts_dir,
        quality=quality,
        brief=_brief_for(workflow, subject),
    )

    # Staged before the first stage runs, so `--input` works the same way for every lane instead
    # of each one documenting a different directory.
    if inputs:
        stage_inputs(project_dir, inputs, wanted=workflow_inputs(workflow), log=log)

    resolved = resolved_steps(
        steps,
        story=story,
        shots=shots,
        style=style,
        subject=subject,
        params=params,
        node_params=node_params,
        # A resume runs the configuration the run was started with. See `recorded_values`.
        recorded=recorded_values(ctx.ddir() / "run.json") if from_stage else None,
    )
    return run_plan(
        cast("list[Step]", resolved), ctx, workflow=workflow, use_pins=use_pins, log=log
    )


def _write_report(path: Path, report: dict) -> None:
    """Write atomically: a stop can land between any two bytecodes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    tmp.replace(path)
