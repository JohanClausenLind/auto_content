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

import typer

app = typer.Typer(help="The workflow catalogue: browse, check, scaffold.")


def _catalog():
    from content_factory.workflows.catalog import load_definitions

    return load_definitions()


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
        "steps": steps,
    }
    if as_json:
        typer.echo(json.dumps(doc, indent=1))
        return
    typer.echo(f"{template.id}  {template.name}  [{template.category}]")
    typer.echo(" ".join(template.description.split()))
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
    subject: str = typer.Option("", "--subject", help="One sentence naming the film's world"),
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
) -> None:
    """Run one workflow end to end. This is the whole interface: one call, one line per stage.

    The preflight refuses a lane whose stages have no executor or whose weights are absent, because
    finding that out fifteen minutes into a render costs more than finding it out now. ``--force``
    runs anyway.
    """
    from content_factory.runners.local import LocalRunError, run_workflow
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

    if plan_only:
        typer.echo(
            json.dumps(
                {
                    "workflow": template.id,
                    "steps": [{"node": k, "stage": s.value, "values": v} for k, s, v in steps],
                    "blocked_stages": list(blocked),
                    "missing_models": missing,
                    "prerequisite": template.prerequisite,
                }
            )
        )
        return

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

    node_params: dict[str, dict[str, str]] = {}
    stage_params: dict[str, dict[str, str]] = {}
    for item in set_:
        if "=" not in item or "." not in item.split("=", 1)[0]:
            typer.echo(f"--set expects node.key=value, got {item!r}", err=True)
            raise typer.Exit(code=2)
        target, value = item.split("=", 1)
        owner, key = target.rsplit(".", 1)
        known_nodes = {k for k, _s, _v in steps}
        if owner in known_nodes:
            node_params.setdefault(owner, {})[key] = value
        else:
            stage_params.setdefault(owner, {})[key] = value

    from content_factory.schemas.dag import Stage

    by_stage = {}
    for name, values in stage_params.items():
        try:
            by_stage[Stage(name)] = values
        except ValueError:
            typer.echo(f"--set names neither a node nor a stage: {name!r}", err=True)
            raise typer.Exit(code=2) from None

    from content_factory.deliverables.dag_compiler import stage_defaults
    from content_factory.schemas.dag import Executor

    human_stages = {
        stage.value for stage, (_res, ex) in stage_defaults().items() if ex is Executor.human
    }
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
        if message.startswith("==> "):
            terse.current = message[4:].split()[0]  # type: ignore[attr-defined]
        elif message.startswith("    ok "):
            typer.echo(f"ok   {getattr(terse, 'current', '?'):28s} {message[7:][:96]}")
        elif message.startswith("    WARN "):
            typer.echo(f"WARN {getattr(terse, 'current', '?'):28s} {message[9:][:200]}")
        elif message.startswith("    BLOCKED"):
            typer.echo(f"BLOCK {getattr(terse, 'current', '?'):27s} {message.strip()[:200]}")
        elif message.startswith("    FAILED"):
            current = getattr(terse, "current", "?")
            label = "GATE" if current in human_stages else "FAIL"
            typer.echo(f"{label} {current:28s} {message.strip()[:200]}")

    try:
        report = run_workflow(
            workflow,
            project_dir=Path(project_dir).expanduser() if project_dir else None,
            from_stage=from_stage or None,
            until_stage=until_stage or None,
            quality=quality,
            story=story or None,
            shots=shots or None,
            style=style or None,
            subject=subject or None,
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
