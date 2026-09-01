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
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

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


@dataclass
class NodePlan:
    node_id: str
    stage: str
    deliverable_id: str | None
    depends_on: list[str]
    executor: str = "deterministic"


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
    from content_factory.schemas.dag import Stage as StageEnum

    campaign = ContentCampaign.model_validate_json(inp.campaign_json)
    human_raw = json.loads(inp.human_stages_json or "{}")
    human_stages = {d: {StageEnum(v) for v in stages} for d, stages in human_raw.items()}
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
    nodes = [
        NodePlan(n.node_id, n.stage.value, n.deliverable_id, list(n.depends_on), n.executor.value)
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
    if marker.exists():
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
    out = task.result()
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
    return NodeResult(
        inp.node_id,
        "",
        False,
        json.dumps({"rejected": f"no human validator for stage {inp.stage}"}),
    )


PRODUCTION_ACTIVITIES = [
    compile_plan,
    execute_node,
    validate_human_submission,
    record_run_state,
    record_node_state,
    upsert_action_item,
]


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


@workflow.defn
class ProductionWorkflow:
    def __init__(self) -> None:
        self._approval: ApprovalSignal | None = None
        self._state = "CREATED"
        self._preflight_hash: str | None = None
        self._rejections: list[str] = []
        self._human_submissions: dict[str, HumanTaskSubmission] = {}
        self._waiting_human_tasks: set[str] = set()

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

    @workflow.query
    def state(self) -> str:
        return self._state

    @workflow.query
    def preflight_revision(self) -> str | None:
        return self._preflight_hash

    @workflow.query
    def rejections(self) -> list[str]:
        return self._rejections

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
        heavy_opts = {
            "start_to_close_timeout": timedelta(seconds=240),
            "heartbeat_timeout": timedelta(seconds=6),
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
            digest = hashlib.sha256()
            digest.update(inp.campaign_json.encode())
            digest.update(inp.quality.encode())
            digest.update(node.node_id.encode())
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
                    execute_node, exec_input, **heavy_opts
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
            validation accepts or requests a redo; only then does anything downstream run."""
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
                    body="The pipeline is parked on this slot. Drop the finished asset onto the node (or submit via the PWA); it will be validated before anything downstream runs.",  # noqa: E501
                    dedupe_key=f"human:{inp.run_id}:{node.node_id}",
                    run_id=inp.run_id,
                    deep_link=f"/projects/{inp.run_id}",
                ),
                **opts,
            )
            self._waiting_human_tasks.add(node.node_id)
            while True:
                await workflow.wait_condition(lambda: node.node_id in self._human_submissions)
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
            await workflow.wait_condition(lambda: self._approval is not None)
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
        except Exception as exc:
            await self._set_state(
                inp,
                "FAILED",
                campaign_id=campaign_id,
                project_id=plan.project_id,
                error=_error_text(exc)[:800],
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
