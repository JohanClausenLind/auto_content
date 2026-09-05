"""The durable production workflow (section 7, phase 6).

States: CREATED → PREFLIGHTING → WAITING_FOR_APPROVAL → APPROVED → PRODUCING → COMPLETE
(FAILED/CANCELLED as side states). The approval signal binds to the exact preflight revision hash;
a mismatched approval is recorded and ignored. WAITING_FOR_APPROVAL consumes no worker. Every
stage activity is idempotent: results are cached by an input hash that folds in the campaign,
quality, edit overlays, and all dependency output hashes, so a targeted edit invalidates exactly
its dependency closure on the next run of the same project.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from content_factory.workflows.blocked import (
    BLOCKED_FAILURE_TYPE,
    BlockedError,
    blocked_details,
)

PRODUCTION_QUEUE = "control"


@dataclass
class ProductionInput:
    run_id: str
    workspace_id: str
    campaign_json: str
    quality: str
    projects_dir: str
    artifacts_dir: str
    project_id: str | None = None  # reuse an existing project for targeted rebuilds
    human_stages_json: str = "{}"  # deliverable_id -> [stage names] executed by a human (14.3)
    # A precompiled DeliverableDAG (workspace-graph runs). Empty = compile from the campaign.
    dag_json: str = ""


DEFAULT_ACTIVITY_TIMEOUT_S = 240
"""What every activity used to get, and what a node of an unknown class still gets."""

HEARTBEAT_TIMEOUT_S = 6
"""Short for every class on purpose: it distinguishes a *stuck* activity from a slow one. A GPU
stage heartbeats through its generation, so one that has stopped has died rather than got slow."""

ACTIVITY_TIMEOUTS: dict[str, int] = {
    # Writes a file, hashes a plan, records a state. Seconds, not minutes — and a control node
    # that hangs for four minutes was hiding a bug behind a generous deadline.
    "control": 120,
    # Reaches the network when `execution.live_research` is on: fetches, extracts, dedupes.
    "research": 900,
    # ffmpeg and Remotion. A ten-minute episode's Remotion render is the long pole here.
    "render-cpu": 3600,
    # Blender, and the compositing chain over a whole film.
    "render-gpu": 5400,
    # Ollama through the gateway. The scriptwriter's one call measured 30 s for seven beats; a
    # long episode's plan and a cold model load are the tail.
    "inference-llm": 900,
    # HiDream anchors. Measured on this host: 100-330 s for a pair, and a node holds several.
    "inference-image": 3600,
    # LTX-2.5. 40 s per clip at 704x384 measured, and a shot list is tens of clips; SeedVR2 and
    # the post chain run per clip on top of that.
    "inference-video": 7200,
    # Qwen3-TTS per beat plus forced alignment. 8 s a beat measured, and an episode has dozens.
    "inference-audio": 1800,
    # Deliberately not generous: nothing here publishes during development, and a publish node
    # that sat for an hour would be a hang rather than a slow upload.
    "publish": 300,
}
"""Start-to-close per resource class, in seconds. Every number has a measurement or a stated
reason behind it — the point is not to be generous, it is to make a timeout *mean* something."""


@dataclass
class NodePlan:
    node_id: str
    stage: str
    deliverable_id: str | None
    depends_on: list[str]
    executor: str = "deterministic"
    params: dict[str, str] = dc_field(default_factory=dict)
    resource_class: str = "control"
    """What this node needs to run, from `StageNode.resource_class`. The DAG has carried it since
    it was written and the workflow threw it away, so every activity got the same 240-second
    timeout: too generous for a JSON write and **far too short for a GPU stage**. Measured on this
    host, one LTX-2.5 anchor pair takes 100-330 s and a guided clip 40 s at 704x384 — a
    render-gpu node with several shots in it therefore times out mid-generation and Temporal
    retries the whole thing. See :data:`ACTIVITY_TIMEOUTS`."""


@dataclass
class CompiledPlan:
    project_id: str
    project_dir: str
    nodes: list[NodePlan]
    pruned: int
    overlay_hashes: dict[str, str]  # deliverable_id (or "shared") -> overlay-section hash


@dataclass
class ExecuteNodeInput:
    run_id: str
    workspace_id: str
    node_id: str
    stage: str
    deliverable_id: str | None
    campaign_json: str
    quality: str
    project_dir: str
    artifacts_dir: str
    input_hash: str
    dep_outputs_json: str = "{}"
    params_json: str = "{}"
    prepare: bool = False
    """Run a human-gate stage for its *side effects* and do not treat its gate as a failure.

    `review_frames` and `review_assets` write the contact sheet a person looks at and then block
    until someone has. In the durable path the workflow parked on the signal **without ever running
    them**, so the ActionItem said "drop the asset onto the node" and pointed at a sheet that did
    not exist. In prepare mode the stage runs, the sheet is written, its "blocked the run" error is
    the *expected* outcome rather than a failure, and no `.done.json` is written — so the real,
    gating execution still happens after the approval arrives."""


@dataclass
class HumanTaskSubmission:
    node_id: str
    payload_json: str  # stage-specific: e.g. {"cards": [...]} for write_copy


@dataclass
class HumanValidationInput:
    run_id: str
    workspace_id: str
    node_id: str
    stage: str
    deliverable_id: str | None
    project_dir: str
    payload_json: str
    input_hash: str


@dataclass
class NodeResult:
    node_id: str
    outputs_hash: str
    cache_hit: bool
    facts_json: str


@dataclass
class RunStateUpdate:
    run_id: str
    workspace_id: str
    state: str
    campaign_id: str = ""
    project_id: str = ""
    quality: str = ""
    preflight_revision_hash: str | None = None
    approved_by: str | None = None
    error: str | None = None
    report_json: str | None = None


@dataclass
class NodeStateUpdate:
    run_id: str
    workspace_id: str
    node_id: str
    stage: str
    deliverable_id: str | None
    state: str
    cache_hit: bool = False
    duration_ms: int | None = None
    error: str | None = None


@dataclass
class ActionItemUpsert:
    workspace_id: str
    kind: str
    severity: str
    title: str
    body: str
    dedupe_key: str
    run_id: str | None = None
    deep_link: str | None = None
    resolve: bool = False


# --- activities -------------------------------------------------------------------------------
@activity.defn
async def compile_plan(inp: ProductionInput) -> CompiledPlan:
    from content_factory.deliverables.dag_compiler import compile_dag
    from content_factory.schemas.content import ContentCampaign
    from content_factory.schemas.dag import DeliverableDAG
    from content_factory.schemas.dag import Stage as StageEnum

    campaign = ContentCampaign.model_validate_json(inp.campaign_json)
    human_raw = json.loads(inp.human_stages_json or "{}")
    human_stages = {d: {StageEnum(v) for v in stages} for d, stages in human_raw.items()}
    if inp.dag_json:
        # Workspace-graph runs arrive with the DAG already compiled (and validated) at start
        # time; re-validating here keeps a tampered payload out of the workers.
        dag = DeliverableDAG.model_validate_json(inp.dag_json)
    else:
        dag = compile_dag(campaign, human_stages=human_stages)
    project_id = inp.project_id or f"prj_{inp.run_id.replace('-', '')[:22]}"
    project_dir = Path(inp.projects_dir) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "manifest.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "workspace_id": inp.workspace_id,
                "campaign_id": campaign.campaign_id,
                "run_id": inp.run_id,
                "quality": inp.quality,
            },
            indent=1,
        )
    )
    (project_dir / "dag.json").write_text(dag.model_dump_json(indent=1))
    (project_dir / "campaign.json").write_text(campaign.model_dump_json(indent=1))
    # Files the operator dropped on the canvas. They are copied out of the artifact store into
    # the uploads folder here, in the first activity, because this is the moment the project
    # directory exists — and because the `ingest` stage reads that folder, so a dropped file
    # becomes a typed source through the same path (and the same sniffing) as one put there by
    # hand. Idempotent by name and size: a replayed activity re-copies nothing.
    if campaign.staged_uploads:
        from content_factory.artifacts import open_store
        from content_factory.ingest.dropped import materialize
        from content_factory.workflows.stages import UPLOADS_DIRNAME

        store = open_store()
        uploads_dir = project_dir / UPLOADS_DIRNAME
        for upload in campaign.staged_uploads:
            materialize(uploads_dir, store, campaign.workspace_id, upload)
    nodes = [
        NodePlan(
            n.node_id,
            n.stage.value,
            n.deliverable_id,
            list(n.depends_on),
            n.executor.value,
            dict(n.params),
            n.resource_class,
        )
        for n in dag.topological()
    ]
    overlay_path = project_dir / "edits" / "overlay.json"
    overlay = json.loads(overlay_path.read_text()) if overlay_path.exists() else {}
    hashes: dict[str, str] = {
        "shared": hashlib.sha256(
            json.dumps(overlay.get("shared", {}), sort_keys=True).encode()
        ).hexdigest()
    }
    for d in campaign.deliverables:
        section = overlay.get("copy", {}).get(d.deliverable_id, {})
        hashes[d.deliverable_id] = hashlib.sha256(
            json.dumps(section, sort_keys=True).encode()
        ).hexdigest()
    return CompiledPlan(
        project_id=project_id,
        project_dir=str(project_dir),
        nodes=nodes,
        pruned=len(dag.pruned),
        overlay_hashes=hashes,
    )


@activity.defn
async def execute_node(inp: ExecuteNodeInput) -> NodeResult:
    from content_factory.schemas.content import ContentCampaign
    from content_factory.schemas.dag import Stage
    from content_factory.workflows.stages import STAGE_EXECUTORS, StageContext

    project_dir = Path(inp.project_dir)
    marker = project_dir / ".stages" / f"{inp.node_id.replace(':', '_')}.done.json"
    if marker.exists() and not inp.prepare:
        cached = json.loads(marker.read_text())
        if cached.get("input_hash") == inp.input_hash:
            return NodeResult(
                inp.node_id, cached["outputs_hash"], True, json.dumps(cached.get("facts", {}))
            )
    campaign = ContentCampaign.model_validate_json(inp.campaign_json)
    ctx = StageContext(
        workspace_id=inp.workspace_id,
        project_dir=project_dir,
        artifacts_dir=Path(inp.artifacts_dir),
        campaign=campaign,
        deliverable_id=inp.deliverable_id,
        quality=inp.quality,
        dep_outputs=json.loads(inp.dep_outputs_json),
        params=json.loads(inp.params_json),
    )
    executor = STAGE_EXECUTORS[Stage(inp.stage)]
    log = project_dir / ".stages" / "executions.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        fh.write(f"{inp.node_id}\t{inp.input_hash[:12]}\n")
    delay = float(os.environ.get("CF_TEST_STAGE_DELAY_S", "0") or 0)
    if delay:
        await asyncio.sleep(delay)
    task = asyncio.create_task(asyncio.to_thread(executor, ctx))
    while not task.done():
        activity.heartbeat(inp.node_id)
        await asyncio.wait([task], timeout=0.5)
    try:
        out = task.result()
    # BlockedError FIRST: it subclasses RuntimeError, so the prepare-mode clause below would
    # otherwise shadow it and a blocked run would raise a plain error instead of the
    # non-retryable ApplicationError the BLOCKED state machine reads. pyright caught this.
    except BlockedError as blocked:
        # Non-retryable on purpose. The stage has already regenerated up to
        # max_regen_attempts_per_frame and run the deterministic checks on every attempt; a
        # Temporal retry is three more GPU-minutes per frame for the same answer. The payload
        # rides in the failure details so the workflow can set RunState.BLOCKED and the operator
        # can see which candidate images to look at.
        raise ApplicationError(
            str(blocked),
            blocked.as_dict(),
            type=BLOCKED_FAILURE_TYPE,
            non_retryable=True,
        ) from blocked
    except RuntimeError as exc:
        if not inp.prepare:
            raise
        # Prepare mode: the gate being shut is what we are here to arrange, not a failure. The
        # sheet, the manifest and the findings are on disk now, which is what the operator needs
        # before they can approve anything.
        return NodeResult(
            inp.node_id, "", False, json.dumps({"prepared": True, "gate": str(exc)[:600]})
        )
    if inp.prepare:
        # No marker: the gating run still has to happen once the verdict is in.
        return NodeResult(
            inp.node_id, "", False, json.dumps({"prepared": True, **out.facts}, default=str)
        )
    record = {"input_hash": inp.input_hash, "outputs_hash": out.outputs_hash, "facts": out.facts}
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1, default=str))
    tmp.replace(marker)
    return NodeResult(inp.node_id, out.outputs_hash, False, json.dumps(out.facts, default=str))


@activity.defn
async def record_run_state(update: RunStateUpdate) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from content_factory.db.models import ProductionRun, RunState
    from content_factory.db.session import session_scope

    async with session_scope() as db:
        values = {
            "workspace_id": update.workspace_id,
            "campaign_id": update.campaign_id or "cmp_unknown0000",
            "project_id": update.project_id or "prj_unknown0000",
            "state": RunState(update.state),
            "quality": update.quality or "demo",
        }
        stmt = insert(ProductionRun).values(id=update.run_id, **values)
        upd: dict[str, object] = {"state": RunState(update.state)}
        if update.project_id:
            upd["project_id"] = update.project_id
        if update.preflight_revision_hash:
            upd["preflight_revision_hash"] = update.preflight_revision_hash
        if update.approved_by:
            upd["approved_by"] = update.approved_by
        if update.error:
            upd["error"] = update.error
        if update.report_json:
            upd["report"] = json.loads(update.report_json)
        await db.execute(stmt.on_conflict_do_update(index_elements=[ProductionRun.id], set_=upd))


@activity.defn
async def record_node_state(update: NodeStateUpdate) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from content_factory.db.base import new_id
    from content_factory.db.models import NodeState, RunNode
    from content_factory.db.session import session_scope

    async with session_scope() as db:
        stmt = insert(RunNode).values(
            id=new_id("rn"),
            workspace_id=update.workspace_id,
            run_id=update.run_id,
            node_id=update.node_id,
            stage=update.stage,
            deliverable_id=update.deliverable_id,
            state=NodeState(update.state),
            cache_hit=update.cache_hit,
            duration_ms=update.duration_ms,
            error=update.error,
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[RunNode.run_id, RunNode.node_id],
                set_={
                    "state": NodeState(update.state),
                    "cache_hit": update.cache_hit,
                    "duration_ms": update.duration_ms,
                    "error": update.error,
                    "attempts": RunNode.attempts + (1 if update.state == "running" else 0),
                },
            )
        )


@activity.defn
async def upsert_action_item(item: ActionItemUpsert) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from content_factory.db.base import new_id, utcnow
    from content_factory.db.models import ActionItem, ActionItemStatus
    from content_factory.db.session import session_scope

    async with session_scope() as db:
        if item.resolve:
            from sqlalchemy import update as sa_update

            await db.execute(
                sa_update(ActionItem)
                .where(
                    ActionItem.dedupe_key == item.dedupe_key,
                    ActionItem.status == ActionItemStatus.open,
                )
                .values(status=ActionItemStatus.resolved, resolved_at=utcnow())
            )
            return
        stmt = insert(ActionItem).values(
            id=new_id("ai"),
            workspace_id=item.workspace_id,
            kind=item.kind,
            severity=item.severity,
            title=item.title,
            body=item.body,
            dedupe_key=item.dedupe_key,
            run_id=item.run_id,
            deep_link=item.deep_link,
        )
        await db.execute(stmt.on_conflict_do_nothing(index_elements=[ActionItem.dedupe_key]))


@activity.defn
async def validate_human_submission(inp: HumanValidationInput) -> NodeResult:
    """Validate one submission; an empty outputs_hash means rejected (with reasons in facts)."""
    import hashlib as _hashlib

    project_dir = Path(inp.project_dir)
    try:
        payload = json.loads(inp.payload_json)
    except ValueError:
        return NodeResult(
            inp.node_id, "", False, json.dumps({"rejected": "payload is not valid JSON"})
        )
    if inp.stage == "write_copy":
        cards = payload.get("cards")
        caption = payload.get("caption")
        if cards is not None:
            problems = []
            if not isinstance(cards, list) or not cards:
                problems.append("cards must be a non-empty list")
            else:
                for i, card in enumerate(cards):
                    if (
                        not isinstance(card.get("card_id"), str)
                        or not isinstance(card.get("text"), str)
                        or not card["text"].strip()
                    ):
                        problems.append(f"card {i + 1} needs card_id and non-empty text")
            if problems:
                return NodeResult(inp.node_id, "", False, json.dumps({"rejected": problems}))
        elif not isinstance(caption, str) or not caption.strip():
            return NodeResult(
                inp.node_id, "", False, json.dumps({"rejected": "caption text is required"})
            )
        assert inp.deliverable_id is not None
        ddir = project_dir / "deliverables" / inp.deliverable_id
        ddir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=1, sort_keys=True)
        tmp = ddir / "copy.json.tmp"
        tmp.write_text(text)
        tmp.replace(ddir / "copy.json")
        marker = project_dir / ".stages" / f"{inp.node_id.replace(':', '_')}.done.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        outputs_hash = _hashlib.sha256(text.encode()).hexdigest()
        marker.write_text(
            json.dumps(
                {
                    "input_hash": inp.input_hash,
                    "outputs_hash": outputs_hash,
                    "facts": {"human": True},
                },
                indent=1,
            )
        )
        return NodeResult(
            inp.node_id,
            outputs_hash,
            False,
            json.dumps({"accepted": True, "units": len(payload.get("cards", [1]))}),
        )
    if inp.stage == "review_frames":
        return _validate_frame_review(inp, project_dir, payload)
    if inp.stage == "review_assets":
        return _validate_asset_review(inp, project_dir, payload)
    return NodeResult(
        inp.node_id,
        "",
        False,
        json.dumps({"rejected": f"no human validator for stage {inp.stage}"}),
    )


def _validate_frame_review(
    inp: HumanValidationInput, project_dir: Path, payload: dict
) -> NodeResult:
    """A submitted frame verdict, checked against the frames actually on disk.

    `review_frames` had no validator at all, so a durable run parked on it for ever: there was no
    way to submit the verdict the stage reads. What a verdict has to satisfy is not "is this valid
    JSON" — it is **which images was this a verdict about**. A verdict that reviewed a frame that
    has since been regenerated is not a verdict on what would ship, which is the whole reason
    `FrameRecord` carries `png_sha256` and the stage re-checks it.
    """
    from content_factory.schemas.review import FrameReviewBatch

    assert inp.deliverable_id is not None
    ddir = project_dir / "deliverables" / inp.deliverable_id
    prepared_path = ddir / "reviews" / "frames" / "batch.json"
    if not prepared_path.is_file():
        return NodeResult(
            inp.node_id,
            "",
            False,
            json.dumps({"rejected": "no prepared review on disk; review_frames has not run"}),
        )
    try:
        submitted = FrameReviewBatch.model_validate(payload)
    except Exception as exc:
        return NodeResult(inp.node_id, "", False, json.dumps({"rejected": str(exc)[:600]}))
    prepared = FrameReviewBatch.model_validate_json(prepared_path.read_text())
    on_disk = {f.frame_id: f.png_sha256 for f in prepared.frames}
    problems = []
    if submitted.reviewer is None:
        problems.append("a verdict needs a reviewer and a reviewed_at")
    stale = [
        f.frame_id
        for f in submitted.frames
        if f.frame_id in on_disk and f.png_sha256 != on_disk[f.frame_id]
    ]
    unknown = [f.frame_id for f in submitted.frames if f.frame_id not in on_disk]
    missing = sorted(set(on_disk) - {f.frame_id for f in submitted.frames})
    if stale:
        problems.append(f"verdict is about images that have been regenerated since: {stale[:5]}")
    if unknown:
        problems.append(f"frames not in this deliverable: {unknown[:5]}")
    if missing:
        problems.append(f"no verdict for {len(missing)} frame(s), e.g. {missing[:5]}")
    if problems:
        return NodeResult(inp.node_id, "", False, json.dumps({"rejected": problems}))
    _atomic_json(ddir / "reviews" / "frames" / "verdict.json", submitted.model_dump_json(indent=1))
    return NodeResult(
        inp.node_id,
        "",
        False,
        json.dumps(
            {
                "accepted": True,
                "frames": len(submitted.frames),
                "rejected_frames": [f.frame_id for f in submitted.rejected],
            }
        ),
    )


def _validate_asset_review(
    inp: HumanValidationInput, project_dir: Path, payload: dict
) -> NodeResult:
    """A submitted asset approval, checked against the built mesh it claims to be about.

    Same discipline as a frame verdict and for the same reason: `CharacterAssetReview` carries
    `blend_sha256` because a rebuild withdraws the approval. An approval for a mesh that is no
    longer the one on disk would let an unreviewed sculpture through the gate that exists to stop
    exactly that.

    Approvals are written to `controls.asset_approvals_dir` rather than into the run: an approved
    character outlives the run that first built it, which is what `review_assets` already assumes.
    """
    from content_factory.config import get_settings
    from content_factory.schemas.assets import CharacterAssetReview

    assert inp.deliverable_id is not None
    ddir = project_dir / "deliverables" / inp.deliverable_id
    try:
        submitted = CharacterAssetReview.model_validate(payload)
    except Exception as exc:
        return NodeResult(inp.node_id, "", False, json.dumps({"rejected": str(exc)[:600]}))
    prepared_path = ddir / "reviews" / "assets" / f"{submitted.asset}.review.json"
    if not prepared_path.is_file():
        return NodeResult(
            inp.node_id,
            "",
            False,
            json.dumps({"rejected": f"no prepared review for asset {submitted.asset}"}),
        )
    prepared = CharacterAssetReview.model_validate_json(prepared_path.read_text())
    problems = []
    if not submitted.approved_by:
        problems.append("an approval needs approved_by and approved_at")
    if submitted.blend_sha256 != prepared.blend_sha256:
        problems.append(
            "approval is about a different build than the one on disk"
            f" ({submitted.blend_sha256[:12]} vs {prepared.blend_sha256[:12]})"
        )
    if problems:
        return NodeResult(inp.node_id, "", False, json.dumps({"rejected": problems}))
    approvals = Path(get_settings().controls.asset_approvals_dir)
    _atomic_json(approvals / f"{submitted.asset}.json", submitted.model_dump_json(indent=1))
    return NodeResult(
        inp.node_id,
        "",
        False,
        json.dumps(
            {"accepted": True, "asset": submitted.asset, "blend": submitted.blend_sha256[:12]}
        ),
    )


def _atomic_json(path: Path, text: str) -> None:
    """tmp + replace, the same discipline `write_copy`'s validator already used: a submission
    half-written by a crash must not read as a verdict."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


PRODUCTION_ACTIVITIES = [
    compile_plan,
    execute_node,
    validate_human_submission,
    record_run_state,
    record_node_state,
    upsert_action_item,
]


PREPARABLE_HUMAN_STAGES = frozenset({"review_frames", "review_assets"})
"""Human-gate stages that have a deterministic half worth running before anyone is asked to look.

Both write a contact sheet and a set of findings and then block on a verdict. `write_copy`'s human
executor is not here: it has no deterministic half — the person *is* the executor — so preparing
it would run the model that the human slot exists to replace.
"""


def _human_task_body(stage: str, prepared: dict) -> str:
    """The ActionItem's body, naming what is on disk to look at.

    "Drop the finished asset onto the node" is the right instruction for `write_copy` and the wrong
    one for a review gate, where the operator's job is to look at a sheet that already exists and
    say yes or no.
    """
    base = (
        "The pipeline is parked on this slot. Drop the finished asset onto the node"
        " (or submit via the PWA); it will be validated before anything downstream runs."
    )
    if stage not in PREPARABLE_HUMAN_STAGES:
        return base
    sheet = prepared.get("contact_sheet") or prepared.get("sheet") or "reviews/"
    counts = ", ".join(
        f"{k}: {v}" for k, v in sorted(prepared.items()) if isinstance(v, int | float)
    )
    gate = str(prepared.get("gate", "")).splitlines()[0][:200]
    lines = [
        f"The pipeline is parked on {stage}. The deterministic checks have run and the contact"
        f" sheet is on disk at {sheet} — look at it, then submit a verdict for each item.",
    ]
    if counts:
        lines.append(f"Measured: {counts}.")
    if gate:
        lines.append(f"What is blocking: {gate}")
    return " ".join(lines)


# --- workflow ----------------------------------------------------------------------------------
def _error_text(exc: BaseException) -> str:
    """The deepest cause message: Temporal wraps stage errors in generic ActivityError text."""
    seen: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(seen) < 8:
        message = getattr(current, "message", None) or str(current)
        if message and message not in seen:
            seen.append(message)
        current = getattr(current, "cause", None) or current.__cause__
    return seen[-1] if seen else str(exc)


@dataclass
class ApprovalSignal:
    actor: str
    revision_hash: str
    decision: str = "approve"  # approve | reject
    reason: str = ""


@dataclass
class StopSignal:
    """Stop this run: sent by ``content-factory stop`` and POST /v1/runs/{id}/stop.

    A signal rather than a Temporal cancellation because a cancelled workflow cannot run one more
    activity, and the run row would stay at PRODUCING for ever with nothing left to correct it.
    Signalled, the workflow closes itself the way a rejected preflight does: the open ActionItem is
    resolved, the run is recorded CANCELLED with who stopped it, and Temporal sees an ordinary
    completion. The caller still cancels afterwards if a long node is holding the boundary.
    """

    actor: str
    reason: str = ""


class _Stopped(Exception):  # noqa: N818 - control flow inside the workflow, not an error
    """Raised where a stop request is noticed, caught once at the top of ``run``."""

    def __init__(self, where: str) -> None:
        super().__init__(where)
        self.where = where


@workflow.defn
class ProductionWorkflow:
    def __init__(self) -> None:
        self._approval: ApprovalSignal | None = None
        self._state = "CREATED"
        self._preflight_hash: str | None = None
        self._rejections: list[str] = []
        self._human_submissions: dict[str, HumanTaskSubmission] = {}
        self._waiting_human_tasks: set[str] = set()
        self._stop: StopSignal | None = None
        self._current_node = ""

    @workflow.signal
    def submit_human_task(self, submission: HumanTaskSubmission) -> None:
        self._human_submissions[submission.node_id] = submission

    @workflow.query
    def waiting_human_tasks(self) -> list[str]:
        return sorted(self._waiting_human_tasks)

    @workflow.signal
    def submit_approval(self, signal: ApprovalSignal) -> None:
        if (
            signal.decision == "approve"
            and self._preflight_hash
            and signal.revision_hash != self._preflight_hash
        ):
            self._rejections.append(
                f"approval for stale revision {signal.revision_hash[:12]} ignored"
            )
            return
        self._approval = signal

    @workflow.signal
    def request_stop(self, signal: StopSignal) -> None:
        """Stop at the next node boundary. The first request wins: a second actor asking again
        must not overwrite who stopped the run, and stopping is not undoable."""
        if self._stop is None:
            self._stop = signal

    @workflow.query
    def stop_requested(self) -> str | None:
        return None if self._stop is None else (self._stop.reason or "stop requested")

    @workflow.query
    def state(self) -> str:
        return self._state

    @workflow.query
    def preflight_revision(self) -> str | None:
        return self._preflight_hash

    @workflow.query
    def rejections(self) -> list[str]:
        return self._rejections

    async def _close_as_stopped(
        self, inp: ProductionInput, *, campaign_id: str, project_id: str, where: str, detail: str
    ) -> dict:
        """End the run the way a rejected preflight ends it: CANCELLED, with nothing left open.

        Reached two ways. Cleanly, when the stop is noticed at a node boundary. And from the
        generic failure handler, when the stop arrived while a node was executing: cancelling the
        run cancels that activity, which surfaces here as an ordinary activity failure. Recording
        that as FAILED would open a "run failed" ActionItem for something an operator did on
        purpose, and FAILED is the state that invites a retry.
        """
        assert self._stop is not None
        opts = {
            "start_to_close_timeout": timedelta(seconds=30),
            "retry_policy": RetryPolicy(maximum_attempts=3),
        }
        # Everything a person is still being asked for goes with the run: an ActionItem for a run
        # that no longer exists is a chore nobody can discharge.
        for dedupe in [
            f"approval:{inp.run_id}",
            *(f"human:{inp.run_id}:{n}" for n in sorted(self._waiting_human_tasks)),
        ]:
            await workflow.execute_activity(
                upsert_action_item,
                ActionItemUpsert(
                    workspace_id=inp.workspace_id,
                    kind="stopped",
                    severity="normal",
                    title="",
                    body="",
                    dedupe_key=dedupe,
                    resolve=True,
                ),
                **opts,  # type: ignore[arg-type]
            )
        reason = self._stop.reason or "no reason given"
        error = f"stopped by {self._stop.actor} at {where}: {reason}"
        await self._set_state(
            inp,
            "CANCELLED",
            campaign_id=campaign_id,
            project_id=project_id,
            error=f"{error} ({detail})" if detail else error,
        )  # type: ignore[arg-type]
        return {
            "state": "CANCELLED",
            "stopped_at": where,
            "stopped_by": self._stop.actor,
            "reason": reason,
        }

    async def _set_state(self, inp: ProductionInput, state: str, **extra) -> None:
        self._state = state
        await workflow.execute_activity(
            record_run_state,
            RunStateUpdate(
                run_id=inp.run_id,
                workspace_id=inp.workspace_id,
                state=state,
                quality=inp.quality,
                **extra,
            ),  # type: ignore[arg-type]
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )

    @workflow.run
    async def run(self, inp: ProductionInput) -> dict:
        opts = {
            "start_to_close_timeout": timedelta(seconds=30),
            "retry_policy": RetryPolicy(maximum_attempts=3),
        }

        def node_opts(resource_class: str) -> dict:
            """Activity options for one node, sized by what it actually needs.

            Everything used to get 240 seconds. That is minutes of slack for a node that writes a
            JSON file, and not enough for a single GPU stage: an LTX-2.5 anchor pair measured
            100-330 s on this host and a render-gpu node holds several shots. When the deadline
            passes mid-generation Temporal retries the *whole activity*, so the failure mode was
            "the GPU work restarts for ever" rather than "this timed out".
            """
            seconds = ACTIVITY_TIMEOUTS.get(resource_class, DEFAULT_ACTIVITY_TIMEOUT_S)
            return {
                "start_to_close_timeout": timedelta(seconds=seconds),
                # The heartbeat stays short whatever the class: it is how a *stuck* activity is
                # told apart from a slow one, and a GPU stage that stopped heartbeating has died.
                "heartbeat_timeout": timedelta(seconds=HEARTBEAT_TIMEOUT_S),
                "retry_policy": RetryPolicy(maximum_attempts=3),
            }

        campaign_id = json.loads(inp.campaign_json)["campaign_id"]
        await self._set_state(inp, "CREATED", campaign_id=campaign_id)  # type: ignore[arg-type]
        plan: CompiledPlan = await workflow.execute_activity(compile_plan, inp, **opts)
        await self._set_state(
            inp, "PREFLIGHTING", campaign_id=campaign_id, project_id=plan.project_id
        )  # type: ignore[arg-type]
        for node in plan.nodes:
            await workflow.execute_activity(
                record_node_state,
                NodeStateUpdate(
                    inp.run_id,
                    inp.workspace_id,
                    node.node_id,
                    node.stage,
                    node.deliverable_id,
                    "queued",
                ),
                **opts,
            )

        outputs: dict[str, str] = {}
        results: dict[str, NodeResult] = {}

        async def run_node(node: NodePlan) -> NodeResult:
            if self._stop is not None:
                raise _Stopped(node.node_id)
            self._current_node = node.node_id
            digest = hashlib.sha256()
            digest.update(inp.campaign_json.encode())
            digest.update(inp.quality.encode())
            digest.update(node.node_id.encode())
            digest.update(json.dumps(node.params, sort_keys=True).encode())
            if node.stage == "write_copy":  # overlay edits enter here; downstream follows via deps
                digest.update(
                    plan.overlay_hashes.get(node.deliverable_id or "shared", "none").encode()
                )
            for dep in sorted(node.depends_on):
                digest.update(f"{dep}={outputs[dep]}".encode())
            if node.executor == "human":
                return await run_human_node(node, digest.hexdigest())

            exec_input = ExecuteNodeInput(
                run_id=inp.run_id,
                workspace_id=inp.workspace_id,
                node_id=node.node_id,
                stage=node.stage,
                deliverable_id=node.deliverable_id,
                campaign_json=inp.campaign_json,
                quality=inp.quality,
                project_dir=plan.project_dir,
                artifacts_dir=inp.artifacts_dir,
                input_hash=digest.hexdigest(),
                dep_outputs_json=json.dumps({d: outputs[d] for d in node.depends_on}),
                params_json=json.dumps(node.params, sort_keys=True),
            )
            await workflow.execute_activity(
                record_node_state,
                NodeStateUpdate(
                    inp.run_id,
                    inp.workspace_id,
                    node.node_id,
                    node.stage,
                    node.deliverable_id,
                    "running",
                ),
                **opts,
            )
            started = workflow.now()
            try:
                result: NodeResult = await workflow.execute_activity(
                    execute_node, exec_input, **node_opts(node.resource_class)
                )
            except Exception as exc:
                await workflow.execute_activity(
                    record_node_state,
                    NodeStateUpdate(
                        inp.run_id,
                        inp.workspace_id,
                        node.node_id,
                        node.stage,
                        node.deliverable_id,
                        "failed",
                        error=_error_text(exc)[:800],
                    ),
                    **opts,
                )
                raise
            duration_ms = int((workflow.now() - started).total_seconds() * 1000)
            await workflow.execute_activity(
                record_node_state,
                NodeStateUpdate(
                    inp.run_id,
                    inp.workspace_id,
                    node.node_id,
                    node.stage,
                    node.deliverable_id,
                    "complete",
                    cache_hit=result.cache_hit,
                    duration_ms=duration_ms,
                ),
                **opts,
            )
            outputs[node.node_id] = result.outputs_hash
            results[node.node_id] = result
            return result

        async def run_human_node(node: NodePlan, input_hash: str) -> NodeResult:
            """The workflow PARKS here on a signal wait — no worker slot is consumed. The spec is
            shown on the node (ActionItem + Pipeline Canvas); the operator drops the asset in;
            validation accepts or requests a redo; only then does anything downstream run.

            Before parking, the stage runs in **prepare mode** when it has an executor. That is
            what puts the contact sheet, the review manifest and the deterministic findings on
            disk. Without it the workflow parked on an empty slot: the ActionItem said "drop the
            finished asset onto the node" and there was nothing for the operator to look at, so a
            durable run and a local run of the same lane asked a person for different things.
            """
            prepared: dict = {}
            if node.stage in PREPARABLE_HUMAN_STAGES:
                prepare_result: NodeResult = await workflow.execute_activity(
                    execute_node,
                    ExecuteNodeInput(
                        run_id=inp.run_id,
                        workspace_id=inp.workspace_id,
                        node_id=node.node_id,
                        stage=node.stage,
                        deliverable_id=node.deliverable_id,
                        campaign_json=inp.campaign_json,
                        quality=inp.quality,
                        project_dir=plan.project_dir,
                        artifacts_dir=inp.artifacts_dir,
                        input_hash=input_hash,
                        dep_outputs_json=json.dumps({d: outputs[d] for d in node.depends_on}),
                        params_json=json.dumps(node.params, sort_keys=True),
                        prepare=True,
                    ),
                    **node_opts(node.resource_class),
                )
                prepared = json.loads(prepare_result.facts_json or "{}")
            await workflow.execute_activity(
                record_node_state,
                NodeStateUpdate(
                    inp.run_id,
                    inp.workspace_id,
                    node.node_id,
                    node.stage,
                    node.deliverable_id,
                    "blocked",
                    error="waiting for a human task submission",
                ),
                **opts,
            )
            await workflow.execute_activity(
                upsert_action_item,
                ActionItemUpsert(
                    workspace_id=inp.workspace_id,
                    kind="human_task_waiting",
                    severity="normal",
                    title=f"Your turn: {node.stage} for {node.deliverable_id}",
                    body=_human_task_body(node.stage, prepared),
                    dedupe_key=f"human:{inp.run_id}:{node.node_id}",
                    run_id=inp.run_id,
                    deep_link=f"/projects/{inp.run_id}",
                ),
                **opts,
            )
            self._waiting_human_tasks.add(node.node_id)
            while True:
                await workflow.wait_condition(
                    lambda: node.node_id in self._human_submissions or self._stop is not None
                )
                if self._stop is not None:
                    raise _Stopped(node.node_id)
                submission = self._human_submissions.pop(node.node_id)
                result: NodeResult = await workflow.execute_activity(
                    validate_human_submission,
                    HumanValidationInput(
                        run_id=inp.run_id,
                        workspace_id=inp.workspace_id,
                        node_id=node.node_id,
                        stage=node.stage,
                        deliverable_id=node.deliverable_id,
                        project_dir=plan.project_dir,
                        payload_json=submission.payload_json,
                        input_hash=input_hash,
                    ),
                    **opts,
                )
                if result.outputs_hash:
                    break
                await workflow.execute_activity(
                    record_node_state,
                    NodeStateUpdate(
                        inp.run_id,
                        inp.workspace_id,
                        node.node_id,
                        node.stage,
                        node.deliverable_id,
                        "blocked",
                        error=f"submission rejected: {result.facts_json[:300]}",
                    ),
                    **opts,
                )
            self._waiting_human_tasks.discard(node.node_id)
            await workflow.execute_activity(
                upsert_action_item,
                ActionItemUpsert(
                    workspace_id=inp.workspace_id,
                    kind="human_task_waiting",
                    severity="normal",
                    title="",
                    body="",
                    dedupe_key=f"human:{inp.run_id}:{node.node_id}",
                    resolve=True,
                ),
                **opts,
            )
            await workflow.execute_activity(
                record_node_state,
                NodeStateUpdate(
                    inp.run_id,
                    inp.workspace_id,
                    node.node_id,
                    node.stage,
                    node.deliverable_id,
                    "complete",
                ),
                **opts,
            )
            outputs[node.node_id] = result.outputs_hash
            results[node.node_id] = result
            return result

        try:
            preflight_result: NodeResult | None = None
            shared = [n for n in plan.nodes if n.deliverable_id is None]
            branches = [n for n in plan.nodes if n.deliverable_id is not None]
            for node in shared:
                res = await run_node(node)
                if node.stage == "preflight":
                    preflight_result = res
            assert preflight_result is not None
            self._preflight_hash = preflight_result.outputs_hash
            await workflow.execute_activity(
                upsert_action_item,
                ActionItemUpsert(
                    workspace_id=inp.workspace_id,
                    kind="approval_waiting",
                    severity="normal",
                    title=f"Preflight approval needed for {campaign_id}",
                    body=f"Revision {self._preflight_hash[:12]} is ready for review.",
                    dedupe_key=f"approval:{inp.run_id}",
                    run_id=inp.run_id,
                    deep_link=f"/projects/{plan.project_id}",
                ),
                **opts,
            )
            await self._set_state(
                inp,
                "WAITING_FOR_APPROVAL",
                campaign_id=campaign_id,
                project_id=plan.project_id,
                preflight_revision_hash=self._preflight_hash,
            )  # type: ignore[arg-type]
            await workflow.wait_condition(
                lambda: self._approval is not None or self._stop is not None
            )
            if self._stop is not None:
                raise _Stopped("waiting_for_approval")
            assert self._approval is not None
            await workflow.execute_activity(
                upsert_action_item,
                ActionItemUpsert(
                    workspace_id=inp.workspace_id,
                    kind="approval_waiting",
                    severity="normal",
                    title="",
                    body="",
                    dedupe_key=f"approval:{inp.run_id}",
                    resolve=True,
                ),
                **opts,
            )
            if self._approval.decision == "reject":
                await self._set_state(
                    inp,
                    "CANCELLED",
                    campaign_id=campaign_id,
                    project_id=plan.project_id,
                    error=f"rejected by {self._approval.actor}: {self._approval.reason}",
                )  # type: ignore[arg-type]
                return {"state": "CANCELLED", "rejections": self._rejections}
            await self._set_state(
                inp,
                "APPROVED",
                campaign_id=campaign_id,
                project_id=plan.project_id,
                approved_by=self._approval.actor,
            )  # type: ignore[arg-type]

            await self._set_state(
                inp, "PRODUCING", campaign_id=campaign_id, project_id=plan.project_id
            )  # type: ignore[arg-type]
            for node in branches:
                await run_node(node)
            report = {
                "run_id": inp.run_id,
                "project_id": plan.project_id,
                "nodes": len(plan.nodes),
                "pruned": plan.pruned,
                "cache_hits": sum(1 for r in results.values() if r.cache_hit),
                "approved_by": self._approval.actor,
                "preflight_revision": self._preflight_hash,
            }
            await self._set_state(
                inp,
                "COMPLETE",
                campaign_id=campaign_id,
                project_id=plan.project_id,
                report_json=json.dumps(report),
            )  # type: ignore[arg-type]
            return {"state": "COMPLETE", **report}
        except _Stopped as stop:
            return await self._close_as_stopped(
                inp,
                campaign_id=campaign_id,
                project_id=plan.project_id,
                where=stop.where,
                detail="",
            )
        except Exception as exc:
            if self._stop is not None:
                # The stop landed inside a node. Cancelling the run cancelled that activity, and
                # the cancellation arrives here as an ordinary failure — it is still a stop.
                return await self._close_as_stopped(
                    inp,
                    campaign_id=campaign_id,
                    project_id=plan.project_id,
                    where=self._current_node or "a node in flight",
                    detail=_error_text(exc)[:200],
                )
            # BLOCKED has been in the RunState enum since the state machine was written and
            # nothing set it. This is what sets it: a stage that generated, checked and gave up is
            # waiting for a person, not broken, and a run marked FAILED gets retried.
            blocked = blocked_details(exc)
            await self._set_state(
                inp,
                "BLOCKED" if blocked else "FAILED",
                campaign_id=campaign_id,
                project_id=plan.project_id,
                error=_error_text(exc)[:800],
                **({"report_json": json.dumps({"blocked": blocked})} if blocked else {}),
            )  # type: ignore[arg-type]
            await workflow.execute_activity(
                upsert_action_item,
                ActionItemUpsert(
                    workspace_id=inp.workspace_id,
                    kind="run_failed",
                    severity="high",
                    title=f"Run {inp.run_id} failed",
                    body=_error_text(exc)[:500],
                    dedupe_key=f"failed:{inp.run_id}",
                    run_id=inp.run_id,
                ),
                **opts,
            )
            raise
