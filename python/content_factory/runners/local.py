"""Run a workflow's stages locally, in order, with the real executors and no Temporal.

The definitions in ``workflows/*.yaml`` are the single source of truth: this module reads them
through ``content_factory.workflows.catalog`` rather than keeping its own copy of what a workflow
is. It used to keep one, and it drifted from the canvas's copy in every way a duplicated definition
can. Stages that need GPU servers start them through services/local.py. Each run writes
``run.json`` (per-stage hashes, facts, timings) next to the deliverable.

Steps are keyed by NODE KEY, not by stage, so a workflow may use one stage twice with different
parameters. The stage-keyed table this replaced silently collapsed those.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from content_factory.runners.registry import RunHandle, RunStopped, register_run
from content_factory.schemas.dag import Stage
from content_factory.schemas.fixtures import sample_campaign
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
    """A context addressing a deliverable the campaign actually contains.

    The delivery stages look their own spec up in the campaign, so an invented id fails them with
    a bare ``StopIteration`` several minutes into a run. Default to the campaign's video
    deliverable, which is what every lane here produces.

    ``brief`` are an ``input.brief`` node's widget values (topic, audience). Given them, the
    campaign carries the lane's own brief instead of the demo fixture's — the same substitution
    the canvas path makes, through the same helper. Without them the fixture brief survives, which
    is what the non-lane callers (tests, the MCP tools) want.
    """
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
    """Execute ``stages`` in order, with parameters keyed by stage.

    Kept for callers that have a bare stage list. A workflow run goes through :func:`run_plan`,
    which keys parameters by node and so can carry a stage that appears twice.
    """
    params = params or {}
    steps: list[Step] = [(s.value, s, dict(params.get(s, {}))) for s in stages]
    return run_plan(steps, ctx, report_path=report_path, log=log)


def stage_warnings(facts: dict[str, Any]) -> list[str]:
    """Facts that need an operator to decide something, as their own lines.

    The stage line is truncated to keep a run's output cheap to read, which means a warning
    arriving as the tail of a fact blob is a warning nobody sees. This is emitted here rather than
    in a CLI because this is where the untruncated facts are, and every front end gets it.
    """
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
    """Refuse to start while a higher-priority tenant holds the card.

    Priority that only works one way is not priority: without this, a run started during a study
    session takes back the VRAM that ``gpu yield`` just freed, and the session OOMs instead. The
    claim expires on its own (see ``gpu_priority.MAX_CLAIM_HOLD_S``) because the other tenant is a
    separate program that can crash, and a stale file must not stop this machine rendering ever
    again. ``CF_IGNORE_GPU_CLAIM=1`` is the deliberate override.
    """
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


def run_plan(
    steps: Sequence[Step],
    ctx: StageContext,
    *,
    report_path: Path | None = None,
    workflow: str = "run",
    log=print,
) -> dict:
    """Execute ``steps`` in order; stop at the first failure. The report is written after every
    step, so a crash leaves a record of what completed.

    Parameters are the widget values a workspace graph would have frozen onto each node, so a local
    run and a Run started from the canvas execute the same way.

    The run publishes itself in the registry (``runners/registry``) for as long as it executes, so
    ``content-factory stop`` can find it from another terminal. A stop asked for between steps ends
    the run as :class:`RunStopped` with the report written and every finished stage left on disk,
    which is what makes ``--from`` a resume rather than a restart."""
    missing = sorted({stage.value for _key, stage, _p in steps if stage not in STAGE_EXECUTORS})
    if missing:
        msg = f"stages without executors: {missing}"
        raise ValueError(msg)
    report: dict = {
        "project_dir": str(ctx.project_dir),
        "deliverable_id": ctx.deliverable_id,
        "stages": [],
        "passed": False,
    }
    report_path = report_path or ctx.ddir() / "run.json"
    _refuse_while_gpu_claimed()
    with register_run(workflow, ctx.project_dir) as handle:
        _run_steps(steps, ctx, report=report, report_path=report_path, handle=handle, log=log)
    report["passed"] = True
    _write_report(report_path, report)
    return report


def _run_steps(
    steps: Sequence[Step],
    ctx: StageContext,
    *,
    report: dict,
    report_path: Path,
    handle: RunHandle,
    log,
) -> None:
    for node_key, stage, stage_params in steps:
        reason = handle.stop_reason()
        if reason:
            report["stopped"] = {"reason": reason, "before": node_key}
            _write_report(report_path, report)
            log(f"    STOPPED before {node_key}: {reason}")
            raise RunStopped(reason, at=node_key, report=report)
        handle.note(step=node_key)
        started = time.monotonic()
        label = stage.value if node_key == stage.value else f"{stage.value} [{node_key}]"
        log(f"==> {label}{'  ' + json.dumps(stage_params) if stage_params else ''}")
        try:
            out = STAGE_EXECUTORS[stage](replace(ctx, params=dict(stage_params)))
        except RunStopped as stop:  # SIGTERM reached the run in the middle of this stage
            report["stopped"] = {"reason": stop.reason, "during": node_key}
            _write_report(report_path, report)
            log(f"    STOPPED during {node_key}: {stop.reason}")
            stop.report = report
            raise
        except Exception as exc:
            # A stage whose subprocess was killed by the same stop that killed the run raises an
            # ordinary error. Reporting that as FAILED would invite a retry for something nobody
            # broke, so a pending stop request re-labels it as what it is.
            stopping = handle.stop_reason()
            if stopping:
                report["stopped"] = {"reason": stopping, "during": node_key}
                _write_report(report_path, report)
                log(f"    STOPPED during {node_key}: {stopping}")
                raise RunStopped(stopping, at=node_key, report=report) from exc
            # A block is not a failure. The stage did its job, ran the deterministic checks and
            # got an answer a person has to give — so the report says so in its own field, and the
            # log line says BLOCKED, because "FAILED" is what invites reaching for a retry.
            blocked = blocked_details(exc)
            record = {
                "stage": stage.value,
                "node": node_key,
                "ok": False,
                "seconds": round(time.monotonic() - started, 1),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-4000:],
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
            }
        )
        _write_report(report_path, report)
        log(f"    ok {seconds}s {json.dumps(out.facts, default=str)[:160]}")
        for warning in stage_warnings(out.facts):
            log(f"    WARN {warning}")


def _resolve_boundary(steps: Sequence[Step], name: str, which: str) -> int:
    """Index of the step ``name`` selects, by node key or by stage name.

    A stage name that appears twice in the workflow is ambiguous, and the error says so and names
    the node keys, rather than quietly picking the first one."""
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


def _brief_for(workflow: str, subject: str | None) -> dict[str, object]:
    """The lane's ``input.brief`` widget values, with ``--subject`` overriding the topic.

    A lane with no brief node still gets one: ``--subject`` alone is enough, and failing that the
    lane's name and description are a truthful placeholder rather than the demo fixture's question
    about Swedish wind power.
    """
    template = load_definition(workflow)
    node = next((n for n in template.nodes if n.type == "input.brief"), None)
    values: dict[str, object] = dict(node.values) if node is not None else {}
    if subject:
        values["topic"] = subject
    values.setdefault("topic", template.description.strip() or template.name)
    return values


def run_workflow(
    workflow: str,
    *,
    project_dir: Path | None = None,
    artifacts_dir: Path | None = None,
    from_stage: str | None = None,
    until_stage: str | None = None,
    quality: str = "demo",
    story: str | None = None,
    shots: str | None = None,
    style: str | None = None,
    subject: str | None = None,
    params: Mapping[Stage, Mapping[str, str]] | None = None,
    node_params: Mapping[str, Mapping[str, str]] | None = None,
    log=print,
) -> dict:
    """Run one workflow definition end to end, or a slice of it.

    ``--from`` and ``--until`` take a node key or a stage name. Overrides arrive two ways: by node
    key, which is exact, or by stage, which applies to every node running that stage."""
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
    # The lane's own brief, not the demo fixture's, and --subject overrides its topic. A run whose
    # brief says "How much of Sweden's electricity came from wind in 2025?" asks the image model
    # for that question over the top of whatever film was actually commissioned.
    ctx = make_context(
        project_dir=project_dir,
        artifacts_dir=artifacts_dir,
        quality=quality,
        brief=_brief_for(workflow, subject),
    )

    by_stage: dict[Stage, dict[str, str]] = {}
    # Which film: the story fixture chooses the script, the shot fixture the staging.
    if story:
        by_stage.setdefault(Stage.plan_story, {})["story"] = story
    if shots:
        by_stage.setdefault(Stage.plan_shots, {}).update(
            {"planner": "fixture", "fixture_path": shots}
        )
    if subject:
        # One sentence, three places, because three different models are told it. It leads the
        # anchor prompt; it becomes the story's ``visual_subject``, which is what the shot prompt
        # compiler names the film's world from; and it reaches the single-clip video path, which
        # has no ShotSpec to compile from.
        by_stage.setdefault(Stage.generate_anchor, {})["prompt"] = subject
        by_stage.setdefault(Stage.plan_story, {})["subject"] = subject
        by_stage.setdefault(Stage.generate_video, {})["subject"] = subject
    if style:
        from content_factory.sequences.styles import resolve_style

        by_stage.setdefault(Stage.generate_anchor, {})["style"] = resolve_style(style)
    for stage, values in (params or {}).items():
        by_stage.setdefault(stage, {}).update({k: str(v) for k, v in values.items()})

    resolved: list[Step] = []
    for key, stage, values in steps:
        merged = dict(values)
        merged.update(by_stage.get(stage, {}))
        merged.update({k: str(v) for k, v in (node_params or {}).get(key, {}).items()})
        resolved.append((key, stage, merged))
    return run_plan(resolved, ctx, workflow=workflow, log=log)


def _write_report(path: Path, report: dict) -> None:
    """Atomically, because a stop can interrupt the run between any two bytecodes and a truncated
    run.json is worse than a stale one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    tmp.replace(path)
