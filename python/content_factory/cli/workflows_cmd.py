"""``content-factory workflows …`` and ``content-factory make …``.

Two audiences, one shape. A person wants to browse the catalogue; an agent wants to run a whole
production in one call and read as little as possible afterwards. So every command here defaults to
terse, line-per-fact output with a single JSON object at the end, and nothing prints a wall of text
unless asked.

``make`` is the one command. It resolves a workflow definition, preflights what the lane needs,
runs every stage, and prints one line per stage. That is deliberately the whole interface: the
alternative is an agent orchestrating fifteen stages by hand and paying for the transcript.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from content_factory.schemas.dag import Stage

app = typer.Typer(help="The workflow catalogue: browse, check, scaffold.")


def _catalog():
    from content_factory.workflows.catalog import load_definitions

    return load_definitions()


def _run_report(run_dir: Path) -> Path:
    """The run report inside a project directory, whatever the deliverable is called.

    Globbed rather than assuming `dlv_short0000001`: the id comes from the campaign, and a lane
    that produces something other than a short would have been silently given no memory at all.
    """
    found = sorted(run_dir.glob("deliverables/*/run.json"))
    return found[0] if found else run_dir / "deliverables" / "none" / "run.json"


def _load_report(run_dir: Path) -> dict:
    """The last run's report, or an empty one. Pinning reads it to learn what a node produced."""
    path = _run_report(run_dir)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _refuse_unknown_widget(node: str, key: str, steps) -> None:
    """A `--set` naming a widget the node does not declare is a typo, and has to say so.

    The owner was already checked; the key never was. A lane definition setting a widget that does
    not exist is rejected by `workflows/catalog.py` for exactly the reason its docstring gives —
    "a typo'd key is silently swallowed by the stage's `_param` default and the workflow quietly
    does something else" — and `--set` is the same door with no lock on it. Measured while adding
    the drift knobs: `--set spokes.drift_profil=uncalibrated` was accepted, ignored, and the run
    gated on the threshold the override was meant to lift.

    Stage-scoped overrides are left alone: they apply to every node running that stage, and a
    stage is not a node, so there is no single declaration to check against.
    """
    from content_factory.workflows.catalog import node_catalog

    stage = next((s for k, s, _v in steps if k == node), None)
    if stage is None:
        return
    declared = (node_catalog().get(stage.value) or {}).get("widgets")
    if not isinstance(declared, list) or key in declared:
        return
    known = ", ".join(sorted(declared)) if declared else "none"
    typer.echo(
        f"--set {node}.{key} names a widget {stage.value} does not declare. Declared: {known}",
        err=True,
    )
    raise typer.Exit(code=2)


def _parse_overrides(
    set_: list[str], steps
) -> tuple[dict[str, dict[str, str]], dict[Stage, dict[str, str]]]:
    """`--set node.key=value` / `--set stage.key=value`, split by which one the owner names.

    Parsed before `--plan` rather than after it, so the flag that prints what will run can print
    what will run. A malformed override now fails before the takes check instead of after it,
    which is also the better order: it is a typo in the command line, not a missing recording.
    """
    from content_factory.schemas.dag import Stage

    node_params: dict[str, dict[str, str]] = {}
    stage_params: dict[str, dict[str, str]] = {}
    known_nodes = {k for k, _s, _v in steps}
    for item in set_:
        if "=" not in item or "." not in item.split("=", 1)[0]:
            typer.echo(f"--set expects node.key=value, got {item!r}", err=True)
            raise typer.Exit(code=2)
        target, value = item.split("=", 1)
        owner, key = target.rsplit(".", 1)
        if owner in known_nodes:
            _refuse_unknown_widget(owner, key, steps)
            node_params.setdefault(owner, {})[key] = value
        else:
            stage_params.setdefault(owner, {})[key] = value

    by_stage: dict[Stage, dict[str, str]] = {}
    for name, values in stage_params.items():
        try:
            by_stage[Stage(name)] = values
        except ValueError:
            typer.echo(f"--set names neither a node nor a stage: {name!r}", err=True)
            raise typer.Exit(code=2) from None
    return node_params, by_stage


DRAWS_A_PICTURE = frozenset({"generate_anchor", "generate_keyframes", "generate_video"})
"""Stages that actually create the picture an `ImageScene` needs.

Not `render_scenes`, which renders the scene *including its placeholder*, and not `ingest`,
which only moves what the operator supplied — that case is a separate check below, against the
uploads folder, because a lane having an ingest node says nothing about whether anything was
handed to it."""


def _story_wants_pictures_the_lane_cannot_make(template, story: str, run_dir: Path) -> str:
    """`""` unless the story is made of pictures this lane will never produce.

    Measured 2026-09-10: five `narrated-video` runs delivered films that are five
    "PLACEHOLDER · IMAGE / missing asset" cards end to end, with narration over them and an mp4
    in the delivery package. The stories were `ImageScene`s and the lane draws none; the one run
    that supplied its own still is clean. `qc_deliverable` catches it now, but only after the
    render — this is the same fact, knowable in the first second.
    """
    if not story:
        return ""
    path = Path(story)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / story
    try:
        plan = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return ""  # a story this command cannot read is `run_workflow`'s problem to report
    wanted = [s for s in plan.get("scenes", []) if s.get("kind") == "image"]
    if not wanted:
        return ""
    if any(node.type in DRAWS_A_PICTURE for node in template.nodes):
        return ""
    supplied = [p for p in (run_dir / "uploads").glob("*") if p.is_file()]
    if supplied:
        return ""
    return (
        f"{len(wanted)} of the story's {len(plan.get('scenes', []))} scenes are pictures and"
        f" {template.id} has no stage that draws one. Supply them with --input (or in"
        f" {run_dir / 'uploads'}), pick a lane that draws — image-set, photo-sequence-video,"
        " picture-story — or rewrite those beats as cards. Without one of those the film renders"
        " labelled placeholders, which is what `qc_deliverable` will refuse after the render."
    )


def _missing_models(template) -> list[str]:
    """Model requirements that are not on this machine. Cheap path checks, no imports of torch."""
    from content_factory.config import get_settings

    settings = get_settings()
    roots = [Path(r) for r in (settings.comfyui.extra_model_roots or [])]
    store = Path("/mnt/fast/models")
    if store.is_dir() and store not in roots:
        roots.append(store)
    repo = Path(__file__).resolve().parents[3]
    missing: list[str] = []
    for req in template.models:
        if req.optional:
            continue
        if req.kind == "skill":
            if not (repo / req.skill).is_dir():
                missing.append(f"{req.label} (skill {req.skill})")
            continue
        needle = req.filename or req.path_includes
        found = False
        for root in roots:
            if not root.is_dir():
                continue
            if req.path_includes and any(
                req.path_includes in p.name for p in root.iterdir() if p.is_dir()
            ):
                found = True
                break
            if req.filename and next(root.rglob(req.filename), None) is not None:
                found = True
                break
        if not found:
            missing.append(f"{req.label} ({needle})")
    return missing


@app.command("list")
def list_workflows(
    as_json: bool = typer.Option(False, "--json"),
    category: str = typer.Option("", help="Only this category."),
) -> None:
    """Every workflow, one line each: id, category, how many stages, and whether it can run."""
    from content_factory.runners.local import workflow_inputs, workflow_needs_takes
    from content_factory.workflows.catalog import runnable_missing_executor

    rows = []
    for template in _catalog().values():
        if category and template.category != category:
            continue
        blocked = runnable_missing_executor(template)
        missing = _missing_models(template)
        rows.append(
            {
                "id": template.id,
                "name": template.name,
                "category": template.category,
                "stages": len(template.stages()),
                "needs_input": list(workflow_inputs(template.id)),
                "needs_takes": workflow_needs_takes(template.id),
                "ready": not blocked and not missing,
                "blocked_stages": list(blocked),
                "missing_models": missing,
                "prerequisite": template.prerequisite,
            }
        )
    if as_json:
        typer.echo(json.dumps({"workflows": rows}, indent=1))
        return
    for r in rows:
        flag = "ok  " if r["ready"] else "todo"
        extra = ""
        if r["blocked_stages"]:
            extra = f"  no executor: {','.join(r['blocked_stages'])}"
        elif r["missing_models"]:
            extra = f"  missing: {len(r['missing_models'])} model(s)"
        typer.echo(
            f"{flag} {r['id']:24s} {r['category']:8s} {r['stages']:2d} stages  {r['name']}{extra}"
        )
    typer.echo(f"{len(rows)} workflows")


@app.command("show")
def show_workflow(workflow_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    """One workflow: its stages in order, the values frozen on each, and what it needs first."""
    from content_factory.runners.local import workflow_inputs, workflow_needs_takes
    from content_factory.workflows.catalog import load_definition, runnable_missing_executor

    template = load_definition(workflow_id)
    steps = [
        {"node": key, "stage": stage.value, "values": values}
        for key, stage, values in template.stage_order()
    ]
    doc = {
        "id": template.id,
        "name": template.name,
        "description": " ".join(template.description.split()),
        "category": template.category,
        "prerequisite": template.prerequisite,
        "caveat": template.caveat,
        "blocked_stages": list(runnable_missing_executor(template)),
        "missing_models": _missing_models(template),
        "needs_input": list(workflow_inputs(template.id)),
        "needs_takes": workflow_needs_takes(template.id),
        "steps": steps,
    }
    if as_json:
        typer.echo(json.dumps(doc, indent=1))
        return
    typer.echo(f"{template.id}  {template.name}  [{template.category}]")
    typer.echo(" ".join(template.description.split()))
    if doc["needs_input"]:
        typer.echo(f"needs --input: {', '.join(doc['needs_input'])}")
    if doc["needs_takes"]:
        typer.echo(
            "needs takes: one recording per beat, <beat_id>.wav, in the run's uploads folder"
        )
    if template.prerequisite:
        typer.echo(f"needs first: {' '.join(template.prerequisite.split())}")
    if template.caveat:
        typer.echo(f"caveat: {' '.join(template.caveat.split())}")
    for i, step in enumerate(steps, 1):
        values = " ".join(f"{k}={v}" for k, v in sorted(step["values"].items()))
        typer.echo(f"{i:2d}. {step['stage']:30s} {step['node']:16s} {values[:90]}")
    if doc["missing_models"]:
        typer.echo("missing: " + "; ".join(doc["missing_models"]))


@app.command("validate")
def validate_workflows() -> None:
    """Load every definition and report the ones that do not hold together."""
    from content_factory.workflows.catalog import (
        DEFINITIONS_DIR,
        WorkflowDefinitionError,
        load_definition_file,
        runnable_missing_executor,
    )

    problems: list[str] = []
    checked = 0
    for path in sorted(DEFINITIONS_DIR.glob("*.yaml")):
        checked += 1
        try:
            template = load_definition_file(path)
        except WorkflowDefinitionError as exc:
            problems.append(str(exc))
            continue
        blocked = runnable_missing_executor(template)
        if blocked and not template.caveat:
            problems.append(f"{path.name}: uses {list(blocked)} with no executor and no caveat")
    for problem in problems:
        typer.echo(problem, err=True)
    typer.echo(json.dumps({"checked": checked, "problems": len(problems)}))
    if problems:
        raise typer.Exit(code=1)


@app.command("new")
def new_workflow(
    workflow_id: str = typer.Argument(..., help="Kebab-case id; becomes workflows/<id>.yaml"),
    copy_from: str = typer.Option("narrated-video", "--from", help="Definition to start from"),
) -> None:
    """Scaffold a definition that already validates, by copying the simplest lane that does."""
    from content_factory.workflows.catalog import DEFINITIONS_DIR, load_definition

    target = DEFINITIONS_DIR / f"{workflow_id}.yaml"
    if target.exists():
        typer.echo(f"{target} already exists", err=True)
        raise typer.Exit(code=1)
    source = DEFINITIONS_DIR / f"{load_definition(copy_from).id}.yaml"
    text = source.read_text()
    text = text.replace(f"id: {copy_from}", f"id: {workflow_id}", 1)
    target.write_text(text)
    typer.echo(
        json.dumps(
            {
                "created": str(target),
                "next": [
                    "edit name, description, nodes, wires and order",
                    "uv run python scripts/export_workflows.py",
                    "uv run content-factory workflows validate",
                ],
            }
        )
    )


def make(
    workflow: str = typer.Argument(..., help="Workflow id (see `workflows list`)"),
    story: str = typer.Option("", "--story", help="StoryPlan fixture, repo-relative"),
    shots: str = typer.Option("", "--shots", help="ShotPlan fixture; implies planner=fixture"),
    subject: str = typer.Option(
        "",
        "--subject",
        # Name no person or trade in it, even attributively: measured A/B, see StoryPlan
        # .visual_subject. "a blacksmith's anvil" draws the blacksmith; "an iron anvil" does not.
        help="One sentence naming the film's world (name no person or trade in it)",
    ),
    inputs: list[str] = typer.Option(
        [],
        "--input",
        "-i",
        help="Your own material: a recording, a still, a clip, or a folder of stills. Repeatable.",
        metavar="PATH",
    ),
    style: str = typer.Option("", "--style", help="A style preset name or a full prompt"),
    project_dir: str = typer.Option("", help="Where the run writes"),
    from_stage: str = typer.Option("", "--from", help="Resume at this node or stage"),
    until_stage: str = typer.Option("", "--until", help="Stop after this node or stage"),
    quality: str = typer.Option("demo"),
    set_: list[str] = typer.Option(
        [], "--set", help="Override a value: node.key=value or stage.key=value", metavar="K=V"
    ),
    plan_only: bool = typer.Option(False, "--plan", help="Print the steps and exit"),
    force: bool = typer.Option(False, "--force", help="Run even if the preflight found gaps"),
    pin: list[str] = typer.Option(
        [], "--pin", help="Freeze this node before running: skip it and reuse its last output"
    ),
    no_pins: bool = typer.Option(
        False, "--no-pins", help="Ignore every pin and run the whole lane"
    ),
) -> None:
    """Run one workflow end to end. This is the whole interface: one call, one line per stage.

    The preflight refuses a lane whose stages have no executor or whose weights are absent, because
    finding that out fifteen minutes into a render costs more than finding it out now. ``--force``
    runs anyway.
    """
    from content_factory.runners.local import (
        LocalRunError,
        discovered_takes,
        run_workflow,
        workflow_inputs,
        workflow_needs_takes,
    )
    from content_factory.runners.registry import RunStopped
    from content_factory.workflows.blocked import BLOCKED_EXIT_CODE
    from content_factory.workflows.catalog import (
        WorkflowDefinitionError,
        load_definition,
        runnable_missing_executor,
    )

    try:
        template = load_definition(workflow)
    except WorkflowDefinitionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    steps = template.stage_order()
    blocked = runnable_missing_executor(template)
    missing = _missing_models(template)
    node_params, by_stage = _parse_overrides(set_, steps)
    run_dir = (
        Path(project_dir).expanduser()
        if project_dir
        else Path(__file__).resolve().parents[3] / "output" / "local-runs" / template.id
    )

    if plan_only:
        # The steps as they will actually run. `--plan` printed the lane's own values before
        # this, so `--set`, `--story`, `--subject` and `--style` were all invisible to the one
        # flag whose job is to say what will happen (measured 2026-09-10, while checking whether
        # a `--set` had landed — it had, and `--plan` showed the untouched defaults).
        from content_factory.runners.local import recorded_values, resolved_steps

        planned = resolved_steps(
            steps,
            story=story or None,
            shots=shots or None,
            style=style or None,
            subject=subject or None,
            params=by_stage or None,
            node_params=node_params or None,
            # A resume remembers what the run was configured with, and `--plan` has to show the
            # same thing or it is lying about the very flag it was fixed to be honest about.
            recorded=recorded_values(_run_report(run_dir)) if from_stage else None,
        )
        typer.echo(
            json.dumps(
                {
                    "workflow": template.id,
                    "steps": [{"node": k, "stage": s.value, "values": v} for k, s, v in planned],
                    "blocked_stages": list(blocked),
                    "missing_models": missing,
                    "needs_input": list(workflow_inputs(template.id)),
                    "needs_takes": workflow_needs_takes(template.id),
                    "prerequisite": template.prerequisite,
                }
            )
        )
        return

    # A takes-mode lane needs one recording per beat, which no `--input` count can prove and no
    # file-input node can declare. Checked here, against the directory the run will actually read,
    # because the alternative is what it used to do: accept the lane and fail inside `voice_over`
    # — stage 3 of 8, or stage 10 of 20 with the drawings already paid for.
    if workflow_needs_takes(template.id) and not force:
        supplied = [Path(i).expanduser().name for i in inputs] + discovered_takes(run_dir)
        if not supplied:
            typer.echo(
                json.dumps(
                    {
                        "refused": template.id,
                        "needs_takes": True,
                        "hint": (
                            "this lane reads one recording per beat, named by beat id"
                            f" (<beat_id>.wav). Put them in {run_dir / 'uploads'} — with --input,"
                            " by dropping them on the canvas, or by hand — or point the voice node"
                            " at your own folder with --set voice.takes_dir=<dir>. The beat ids"
                            " are in story/plan.json once plan_story has run, so"
                            f" `--until story` first, or supply your own with --story. --force runs"
                            " anyway."
                        ),
                    }
                ),
                err=True,
            )
            raise typer.Exit(code=3)

    needs = workflow_inputs(template.id)
    # Material already in the run's uploads folder *is* material. The refusal used to ignore it
    # and send the operator to `--force`, which is the wrong instrument: a `--from` resume of a
    # lane that consumed its recording nine stages ago was told "this lane works on material you
    # supply" about a file the earlier stages had put there themselves (measured on
    # `audio-picture-story --from finish`, 2026-09-10). `--force` still exists for the case this
    # cannot see — a takes directory named by the voice node — and is no longer needed for the
    # ordinary one.
    staged = sorted(p for p in (run_dir / "uploads").glob("*") if p.is_file())
    if needs and not inputs and not staged and not force:
        typer.echo(
            json.dumps(
                {
                    "refused": template.id,
                    "needs_input": list(needs),
                    "hint": (
                        "this lane works on material you supply:"
                        f" --input <{'/'.join(needs)} file>, or put it in"
                        f" {run_dir / 'uploads'} yourself. --force runs without any."
                    ),
                }
            ),
            err=True,
        )
        raise typer.Exit(code=3)

    cannot_draw = _story_wants_pictures_the_lane_cannot_make(template, story, run_dir)
    if cannot_draw and not force:
        typer.echo(
            json.dumps({"refused": template.id, "story_needs_pictures": cannot_draw}), err=True
        )
        raise typer.Exit(code=3)

    if (blocked or missing) and not force:
        typer.echo(
            json.dumps(
                {
                    "refused": template.id,
                    "blocked_stages": list(blocked),
                    "missing_models": missing,
                    "hint": "fix these, pick another workflow, or pass --force",
                }
            ),
            err=True,
        )
        raise typer.Exit(code=3)

    from content_factory.deliverables.dag_compiler import human_gate_stages

    human_stages = human_gate_stages()
    lines: list[str] = []

    def terse(message: str) -> None:
        """One line per stage, hard-truncated. An agent should not pay for a render's chatter.

        Three outcomes, three labels, because they want three different responses. A human review
        gate is a GATE: show someone the contact sheet and ask. A BLOCK is a stage that generated,
        checked and gave up — a person has to look at what it produced. Only FAIL means fix
        something. They are the same exception underneath, and labelling all three FAIL invites
        reaching for --force, which is the one response a gate and a block must not get.
        """
        lines.append(message)
        if message.startswith("--- "):
            # When the run should be done, on the evidence of past runs. One line, at the top,
            # because "is it stuck or is it slow?" is the question a long lane provokes and the
            # only other way to answer it was watching nvidia-smi.
            typer.echo(message[4:])
        elif message.startswith("    not counted, never timed:"):
            typer.echo(message.strip())
        elif message.startswith("==> "):
            terse.current = message[4:].split()[0]  # type: ignore[attr-defined]
        elif message.startswith("    ok "):
            # The trailing "[Nm left]" is kept whole: it is appended after the facts are
            # truncated, so a chatty stage cannot push the countdown off the line.
            body = message[7:]
            eta = ""
            if body.endswith("]") and "  [" in body:
                body, _, bracket = body.rpartition("  [")
                eta = f"  [{bracket}"
            typer.echo(f"ok   {getattr(terse, 'current', '?'):28s} {body[:96]}{eta}")
        elif message.startswith("    pinned "):
            typer.echo(f"pin  {getattr(terse, 'current', '?'):28s} {message.strip()[7:]}")
        elif message.startswith("    WARN "):
            typer.echo(f"WARN {getattr(terse, 'current', '?'):28s} {message[9:][:200]}")
        elif message.startswith("    BLOCKED"):
            typer.echo(f"BLOCK {getattr(terse, 'current', '?'):27s} {message.strip()[:200]}")
        elif message.startswith("    FAILED"):
            current = getattr(terse, "current", "?")
            label = "GATE" if current in human_stages else "FAIL"
            typer.echo(f"{label} {current:28s} {message.strip()[:200]}")

    # `--pin` is applied before the run so one command can freeze a node and go, rather than
    # needing a separate `pins add` between two runs.
    if pin:
        from content_factory.runners import pins as pins_mod

        try:
            frozen = pins_mod.pin_nodes(
                _run_report(run_dir).parent, _load_report(run_dir), list(pin)
            )
        except pins_mod.PinError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
        listed = ", ".join(f.describe() for f in sorted(frozen.values(), key=lambda x: x.node))
        typer.echo(f"pinned: {listed}")

    try:
        report = run_workflow(
            workflow,
            project_dir=Path(project_dir).expanduser() if project_dir else None,
            use_pins=not no_pins,
            from_stage=from_stage or None,
            until_stage=until_stage or None,
            quality=quality,
            story=story or None,
            shots=shots or None,
            style=style or None,
            subject=subject or None,
            inputs=[Path(i).expanduser() for i in inputs],
            params=by_stage or None,
            node_params=node_params or None,
            log=terse,
        )
    except RunStopped as exc:
        # Exit 130, the shell's "ended by a signal from outside": every stage that finished is
        # still on disk and `--from` resumes there, so this is not a failure to report as one.
        typer.echo(
            json.dumps(
                {
                    "workflow": workflow,
                    "passed": False,
                    "stopped": exc.reason,
                    "stopped_at": exc.at,
                    "completed": len(exc.report.get("stages", [])),
                    "resume_with": f"--from {exc.at}" if exc.at else "",
                }
            ),
            err=True,
        )
        raise typer.Exit(code=130) from exc
    except LocalRunError as exc:
        stopped_at = exc.stage.value
        # A gate is waiting for a person, not broken, and --force is the wrong answer to it. The
        # last line says which, so a caller reading only the summary cannot confuse them.
        outcome: dict[str, object] = {"workflow": workflow, "passed": False}
        if exc.blocked is not None:
            outcome["blocked_at"] = stopped_at
            outcome["blocked"] = exc.blocked
            code = BLOCKED_EXIT_CODE
        elif stopped_at in human_stages:
            outcome["waiting_for_review"] = stopped_at
            code = 4
        else:
            outcome["failed_at"] = stopped_at
            code = 1
        outcome |= {
            "completed": len(exc.report["stages"]) - 1,
            "error": exc.report["stages"][-1]["error"],
            "report": str(Path(exc.report["project_dir"])),
        }
        typer.echo(json.dumps(outcome), err=True)
        raise typer.Exit(code=code) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    project = Path(report["project_dir"])
    typer.echo(
        json.dumps(
            {
                "workflow": workflow,
                "passed": report["passed"],
                "stages": len(report["stages"]),
                "seconds": round(sum(s.get("seconds", 0.0) for s in report["stages"]), 1),
                "project_dir": str(project),
                "report": str(project / "run.json"),
            }
        )
    )


pins_app = typer.Typer(
    help=(
        "Freeze a node so a rerun skips it and reuses what it already produced.\n\n"
        "The costly nodes in a lane are near the front and the node being worked on is near the "
        "back, so `--from` means naming the tail every time and a full run means paying for "
        "pictures nobody is looking at. Pin the front once and run the lane flat out instead."
    )
)


def _pins_run_dir(workflow: str, project_dir: str) -> Path:
    if project_dir:
        base = Path(project_dir).expanduser()
    else:
        base = Path(__file__).resolve().parents[3] / "output" / "local-runs" / workflow
    return _run_report(base).parent


@pins_app.command("list")
def pins_list(
    workflow: str = typer.Argument(..., help="Workflow id"),
    project_dir: str = typer.Option("", help="Where the run wrote"),
) -> None:
    """What is frozen for this lane's run directory."""
    from content_factory.runners import pins as pins_mod

    run_dir = _pins_run_dir(workflow, project_dir)
    frozen = pins_mod.load(run_dir)
    if not frozen:
        typer.echo(f"no pins for {workflow} ({run_dir})")
        return
    for pin in sorted(frozen.values(), key=lambda p: p.node):
        typer.echo(pin.describe())


@pins_app.command("add")
def pins_add(
    workflow: str = typer.Argument(..., help="Workflow id"),
    nodes: list[str] = typer.Argument(..., help="Node keys to freeze"),
    project_dir: str = typer.Option("", help="Where the run wrote"),
) -> None:
    """Freeze nodes using what the last run recorded for them."""
    from content_factory.runners import pins as pins_mod

    run_dir = _pins_run_dir(workflow, project_dir)
    try:
        frozen = pins_mod.pin_nodes(run_dir, _load_report(run_dir.parent.parent), list(nodes))
    except pins_mod.PinError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    for node in sorted(nodes):
        typer.echo(frozen[node].describe())


@pins_app.command("clear")
def pins_clear(
    workflow: str = typer.Argument(..., help="Workflow id"),
    nodes: list[str] = typer.Argument(None, help="Node keys to release; omit for all"),
    project_dir: str = typer.Option("", help="Where the run wrote"),
) -> None:
    """Release some pins, or all of them."""
    from content_factory.runners import pins as pins_mod

    run_dir = _pins_run_dir(workflow, project_dir)
    before = pins_mod.load(run_dir)
    if not before:
        typer.echo(f"no pins for {workflow}")
        return
    targets = list(nodes) if nodes else sorted(before)
    after = pins_mod.unpin_nodes(run_dir, targets)
    typer.echo(f"released {len(before) - len(after)}; {len(after)} still pinned")
