"""/v1/campaigns: the Create flow — validate-and-quote (preview) before any execution (20.4).

Only deliverable types whose production branches are fully implemented in this build are
accepted; everything else is refused with a plain-language reason (no logo-grid promises).
Research in this build runs against offline fixtures; the preview says so honestly.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, get_db, require_role
from content_factory.db.base import new_id
from content_factory.db.models import Role
from content_factory.deliverables.dag_compiler import compile_dag
from content_factory.config.settings import DELIVERABLE_TYPES
from content_factory.schemas import content
from content_factory.services import audit
from content_factory.services.runs import start_run

router = APIRouter(prefix="/v1/campaigns", tags=["campaigns"])
EDITOR = require_role(Role.editor)

SUPPORTED: dict[str, str] = {
    "single_image_post": "static branch (artboard → PNG) is implemented",
    "infographic": "static branch is implemented",
    "cover": "static branch is implemented",
    "carousel": "per-card render cache is implemented",
    "text_post": "text package branch is implemented",
    "short_video": "audio + video branches are implemented (mock narration in this build)",
    "long_video": "audio + video branches are implemented (mock narration in this build)",
}
UNSUPPORTED_REASON: dict[str, str] = {
    "thread": "thread splitting arrives with destination packaging (phase 9)",
    "article": "the article branch (draft, link check, SEO export) is not wired into the pipeline yet",  # noqa: E501
    "newsletter": "the email branch (MJML compile, client previews) is not wired yet",
    "email_campaign": "the email branch is not wired yet",
    "audio_clip": "standalone audio packaging is not wired yet",
    "audiogram": "audiogram compositions are not wired yet",
    "image_sequence": "the sequence engine ships; pipeline wiring arrives with the anchor workflow",
}


class DeliverableChoice(BaseModel):
    type: str
    title: str = Field(min_length=1, max_length=200)
    card_count: int | None = Field(default=None, ge=2, le=20)


class CampaignBody(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=1000)
    deliverables: list[DeliverableChoice] = Field(min_length=1, max_length=10)
    quality: Literal["smoke", "demo"] = "demo"


def _build_campaign(body: CampaignBody, workspace_id: str) -> content.ContentCampaign:
    export = content.DestinationBinding(
        destination=content.Destination(
            destination_id="dst_export000001", platform="export", capability_revision="2026-09-01"
        ),
        visibility="export_only",
    )
    deliverables: list[content.ContentDeliverable] = []
    for choice in body.deliverables:
        if choice.type not in SUPPORTED:
            reason = UNSUPPORTED_REASON.get(choice.type, "unknown deliverable type")
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{choice.type}: {reason}")
        common = {"deliverable_id": new_id("dlv"), "title": choice.title, "destinations": (export,)}
        if choice.type == "single_image_post":
            deliverables.append(content.SingleImagePostSpec(layout="number_led", **common))
        elif choice.type == "infographic":
            deliverables.append(content.InfographicSpec(**common))
        elif choice.type == "cover":
            deliverables.append(content.CoverSpec(**common))
        elif choice.type == "carousel":
            deliverables.append(content.CarouselSpec(card_count=choice.card_count or 5, **common))
        elif choice.type == "text_post":
            deliverables.append(content.TextPostSpec(**common))
        elif choice.type == "short_video":
            deliverables.append(content.ShortVideoSpec(target_duration_s=(15, 30), **common))
        elif choice.type == "long_video":
            deliverables.append(content.LongVideoSpec(**common))
    brief = content.ProjectBrief(
        brief_id=new_id("brf"),
        workspace_id=workspace_id,
        input_mode=content.InputMode.brief_first,
        topic=body.topic,
        objective=body.objective,
    )
    return content.ContentCampaign(
        campaign_id=new_id("cmp"),
        workspace_id=workspace_id,
        brief=brief,
        deliverables=tuple(deliverables),
    )


@router.get("/matrix")
async def deliverable_matrix(p: Principal = Depends(EDITOR)) -> dict[str, Any]:
    """Rows = content types; cells state plainly what works and what does not (and why)."""
    rows = []
    for t in DELIVERABLE_TYPES:
        rows.append(
            {
                "type": t,
                "supported": t in SUPPORTED,
                "reason": SUPPORTED.get(t) or UNSUPPORTED_REASON.get(t, ""),
                "destinations": ["export"],
            }
        )
    return {
        "rows": rows,
        "notes": [
            "Research uses offline fixtures in this build.",
            "All output is package-only export; publishing arrives with phase 9 destinations.",
        ],
    }


@router.post("/preview")
async def preview(body: CampaignBody, p: Principal = Depends(EDITOR)) -> dict[str, Any]:
    campaign = _build_campaign(body, p.workspace_id)
    dag = compile_dag(campaign)
    return {
        "campaign": campaign.model_dump(mode="json"),
        "dag": {
            "nodes": [
                {
                    "node_id": n.node_id,
                    "stage": n.stage.value,
                    "deliverable_id": n.deliverable_id,
                    "depends_on": list(n.depends_on),
                }
                for n in dag.topological()
            ],
            "pruned": [
                {"stage": nr.stage.value, "deliverable_id": nr.deliverable_id, "reason": nr.reason}
                for nr in dag.pruned
            ],
        },
        "estimates": {"external_cost_usd": 0.0, "external_calls": 0, "local_render": True},
        "notes": [
            "Research uses offline fixtures in this build; narration uses the deterministic mock voice."  # noqa: E501
        ],
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create(
    body: CampaignBody, p: Principal = Depends(EDITOR), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    campaign = _build_campaign(body, p.workspace_id)
    try:
        run_id = await start_run(campaign, quality=body.quality)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"workflow engine unavailable: {exc}"
        ) from exc
    await audit.record(
        db,
        "campaign.create",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="campaign",
        target_id=campaign.campaign_id,
        detail={"run_id": run_id, "deliverables": [d.type for d in campaign.deliverables]},
    )
    return {"run_id": run_id, "campaign_id": campaign.campaign_id}
