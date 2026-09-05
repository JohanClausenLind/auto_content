"""/v1/graphs: workspace node-graph documents — persistence, compile preview, and Run.

The document is the WorkspaceGraph contract the browser editor serialises; every write is
validated against it (unknown fields are errors). `compile` is a dry run returning the typed
per-node dispositions; `runs` compiles and starts the same durable ProductionWorkflow campaigns
use, with the precompiled DAG as input. Approval gates apply to graph runs exactly as they do to
campaign runs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, get_db, require_role
from content_factory.db.models import Role, WorkspaceGraphDoc
from content_factory.schemas.fixtures import sample_campaign
from content_factory.schemas.workspace_graph import WorkspaceGraph
from content_factory.services import audit
from content_factory.services import runs as run_svc
from content_factory.workspace import GraphCompilation, compile_graph

router = APIRouter(prefix="/v1/graphs", tags=["graphs"])
VIEWER = require_role(Role.viewer)
EDITOR = require_role(Role.editor)


def _template(workspace_id: str):
    campaign = sample_campaign().model_copy(update={"workspace_id": workspace_id})
    return campaign.model_copy(
        update={"brief": campaign.brief.model_copy(update={"workspace_id": workspace_id})}
    )


def _parse(doc: dict[str, Any]) -> WorkspaceGraph:
    try:
        return WorkspaceGraph.model_validate(doc)
    except ValidationError as err:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"invalid graph: {err.errors()[0]['msg']}"
        ) from err


def _compilation_body(compilation: GraphCompilation) -> dict[str, Any]:
    return {
        "ok": compilation.ok,
        "problems": compilation.problems,
        "deliverable_type": compilation.deliverable_type,
        "dispositions": [
            {
                "node_id": d.node_id,
                "type": d.type,
                "kind": d.kind,
                "reason": d.reason,
                "dag_node_id": d.dag_node_id,
            }
            for d in compilation.dispositions
        ],
        "dag_nodes": len(compilation.dag.nodes) if compilation.dag else 0,
    }


@router.get("")
async def list_graphs(
    p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(WorkspaceGraphDoc)
            .where(WorkspaceGraphDoc.workspace_id == p.workspace_id)
            .order_by(WorkspaceGraphDoc.updated_at.desc())
        )
    ).scalars()
    return [
        {
            "graph_id": row.id,
            "name": row.name,
            "nodes": len(row.doc.get("nodes", [])),
            "links": len(row.doc.get("links", [])),
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/{graph_id}")
async def get_graph(
    graph_id: str, p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    row = await db.get(WorkspaceGraphDoc, graph_id)
    if row is None or row.workspace_id != p.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return row.doc


@router.put("/{graph_id}")
async def put_graph(
    graph_id: str,
    doc: dict[str, Any],
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    graph = _parse(doc)
    if graph.graph_id != graph_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "graph_id mismatch")
    row = await db.get(WorkspaceGraphDoc, graph_id)
    if row is not None and row.workspace_id != p.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if row is None:
        row = WorkspaceGraphDoc(id=graph_id, workspace_id=p.workspace_id)
        db.add(row)
    row.name = graph.name
    row.doc = graph.model_dump(mode="json")
    return {"graph_id": graph_id, "nodes": len(graph.nodes), "links": len(graph.links)}


@router.delete("/{graph_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_graph(
    graph_id: str, p: Principal = Depends(EDITOR), db: AsyncSession = Depends(get_db)
) -> None:
    await db.execute(
        delete(WorkspaceGraphDoc).where(
            WorkspaceGraphDoc.id == graph_id, WorkspaceGraphDoc.workspace_id == p.workspace_id
        )
    )


@router.post("/{graph_id}/compile")
async def compile_preview(
    graph_id: str, p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    row = await db.get(WorkspaceGraphDoc, graph_id)
    if row is None or row.workspace_id != p.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    compilation = compile_graph(_parse(row.doc), _template(p.workspace_id))
    return _compilation_body(compilation)


class StartGraphRunBody(BaseModel):
    quality: str = Field(default="demo", pattern=r"^(smoke|demo)$")


@router.post("/{graph_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def start_graph_run(
    graph_id: str,
    body: StartGraphRunBody,
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await db.get(WorkspaceGraphDoc, graph_id)
    if row is None or row.workspace_id != p.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    compilation = compile_graph(_parse(row.doc), _template(p.workspace_id))
    if not compilation.ok or compilation.campaign is None or compilation.dag is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": "graph does not compile", **_compilation_body(compilation)},
        )
    try:
        run_id = await run_svc.start_run(
            compilation.campaign,
            quality=body.quality,
            dag_json=compilation.dag.model_dump_json(),
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"workflow engine unavailable: {exc}"
        ) from exc
    await audit.record(
        db,
        "graph.run.start",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="run",
        target_id=run_id,
        detail={"graph_id": graph_id, "deliverable_type": compilation.deliverable_type},
    )
    return {"run_id": run_id, **_compilation_body(compilation)}
