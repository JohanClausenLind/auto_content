"""Model-backed copywriting for the write_copy stage (local models only, via the gateway).

Off by default (`execution.local_copywriter`); the fixture writer keeps offline runs
deterministic. When enabled, drafts come from the default local catalog (qwen38-ridge) with the
gateway's schema-validated retries; operator edit overlays always win over drafted text."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field

from content_factory.budgets.ledger import Cap, Scope
from content_factory.models.catalog import build_gateway
from content_factory.models.gateway import ModelGateway
from content_factory.schemas.content import ContentCampaign
from content_factory.schemas.skills import (
    PRESETS,
    CostEstimator,
    ExecutionLocation,
    ExecutorType,
    Lifecycle,
    SkillManifest,
    SkillPermissions,
)


class CaptionCopy(BaseModel):
    caption: str = Field(min_length=10, max_length=400)
    alt_text: str = Field(min_length=10, max_length=250)


class CarouselCopy(BaseModel):
    cards: list[str] = Field(min_length=1, max_length=12)


_SKILL = SkillManifest(
    skill_id="write.copy",
    version="1.0.0",
    status=Lifecycle.active,
    purpose="draft deliverable copy from the campaign brief",
    input_schema="ContentCampaign",
    output_schema="EditBatch",
    executor=ExecutorType.model_role,
    implementation_ref="content_factory.models.copywriter:draft_caption",
    permitted_locations=(ExecutionLocation.local_gpu, ExecutionLocation.local_cpu),
    required_models=("local_structured", "local_structured_small"),
    permissions=SkillPermissions(network_egress=False),
    license_evidence="Apache-2.0",
    cost=CostEstimator(kind="per_token", usd=0),
    timeout_seconds=300,
    max_retries=1,
)


@lru_cache(maxsize=1)
def _gateway() -> ModelGateway:
    gateway = build_gateway()
    gateway.ledger.set_cap(Cap(scope=Scope.monthly, key="copywriter", limit_usd=1.0))
    return gateway


def _brief_context(campaign: ContentCampaign) -> str:
    brief = campaign.brief
    return (
        f"Topic: {brief.topic}\n"
        f"Objective: {brief.objective}\n"
        f"Audience: {brief.audience or 'general'}\n"
        f"Language: {brief.language.code}\n"
        "Tone: clear, warm, concrete; no hype, no emoji unless asked."
    )


def draft_caption(campaign: ContentCampaign, *, platform_hint: str = "") -> dict:
    result = _gateway().complete_structured(
        _SKILL,
        PRESETS["private_local"],
        CaptionCopy,
        [
            {
                "role": "user",
                "content": (
                    f"{_brief_context(campaign)}\n\nWrite one social caption (max 2 sentences) "
                    f"and a literal alt text for the accompanying data card. {platform_hint}"
                ),
            }
        ],
        budget_scopes=[(Scope.monthly, "copywriter")],
    )
    copy = result.value
    assert isinstance(copy, CaptionCopy)
    return {"caption": copy.caption, "alt_text": copy.alt_text, "writer": result.model_alias}


def draft_carousel(campaign: ContentCampaign, *, card_count: int) -> dict:
    result = _gateway().complete_structured(
        _SKILL,
        PRESETS["private_local"],
        CarouselCopy,
        [
            {
                "role": "user",
                "content": (
                    f"{_brief_context(campaign)}\n\nWrite exactly {card_count} short carousel "
                    "card texts (one line each, max 90 characters, no numbering). Card 1 hooks; "
                    "the last card lands the takeaway."
                ),
            }
        ],
        budget_scopes=[(Scope.monthly, "copywriter")],
    )
    copy = result.value
    assert isinstance(copy, CarouselCopy)
    texts = [t.strip() for t in copy.cards if t.strip()][:card_count]
    while len(texts) < card_count:  # a short reply never breaks the deliverable contract
        texts.append(texts[-1] if texts else "…")
    return {
        "cards": [{"card_id": f"card_{i + 1:012d}", "text": t} for i, t in enumerate(texts)],
        "writer": result.model_alias,
    }
