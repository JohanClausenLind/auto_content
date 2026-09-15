"""Model-backed copywriting for the write_copy stage (local models only, via the gateway)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field

from content_factory.budgets.ledger import Cap, Scope
from content_factory.models.catalog import build_gateway
from content_factory.models.gateway import GatewayOptions, ModelGateway
from content_factory.prompting import CAPTION, CARDS
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


class HookCopy(BaseModel):
    hook: str = Field(min_length=10, max_length=200)


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


CAPTION_MAX_CHARS = 280
"""Two sentences' worth. Named because it is in the prompt and in the contract's own `max_length`,
and the two disagreeing is how a reply gets truncated after the model was told it fit."""

CARD_MAX_CHARS = 90

COPY_OPTIONS = GatewayOptions(
    # The schema constrains decoding, so the retry loop is only a safety net. Thinking is off
    # because reasoning tokens share the answer's budget and a caption is not a reasoning problem.
    structured_output=True,
    think=False,
    # Unload at once: a lane that captions and then generates an anchor otherwise meets HiDream's
    # ~19.4 GB load with 12 GB of text model still resident (the OOM free_the_gpu cleans up).
    keep_alive=0,
)

TONES: dict[str, str] = {
    "neutral": "clear, warm, concrete; no hype, no emoji unless asked",
    "playful": "light and conversational, one wry turn at most; still concrete, no hype, no emoji",
    "direct": "short declarative sentences, the claim first, nothing softened; no hype, no emoji",
    "formal": "measured and impersonal, full sentences, no contractions; no hype, no emoji",
}
"""The ``tone`` widget's options, spelled as instructions. A tone is a sentence for the model, not
an enum it has to guess the meaning of; an unknown name falls back to neutral rather than being
passed through, so a typo cannot become the whole tone instruction."""


def gateway_facts(result) -> dict[str, object]:
    """What one model call cost, in the shape a stage puts in its facts."""
    return {
        "writer": result.model_alias,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "elapsed_s": result.elapsed_s,
        "usd": round(result.actual_usd, 6),
        "attempts": result.attempts,
        # Whether the JSON was a constraint or a request, and how much prompt the model could see.
        # Both change what a retry or a truncated answer means, and neither was recorded before.
        "schema_enforced": result.schema_enforced,
        "num_ctx": result.num_ctx,
    }


def _brief_context(campaign: ContentCampaign, *, tone: str = "neutral") -> str:
    brief = campaign.brief
    return (
        f"Topic: {brief.topic}\n"
        f"Objective: {brief.objective}\n"
        f"Audience: {brief.audience or 'general'}\n"
        f"Language: {brief.language.code}\n"
        f"Tone: {TONES.get(tone, TONES['neutral'])}."
    )


def draft_caption(
    campaign: ContentCampaign, *, platform_hint: str = "", tone: str = "neutral"
) -> dict:
    result = _gateway().complete_structured(
        _SKILL,
        PRESETS["private_local"],
        CaptionCopy,
        [
            {
                "role": "user",
                "content": _brief_context(campaign, tone=tone)
                + "\n\n"
                + CAPTION.render(
                    platform=platform_hint or "a social feed",
                    topic=campaign.brief.topic,
                    audience=campaign.brief.audience,
                    tone=TONES.get(tone, TONES["neutral"]),
                    max_chars=CAPTION_MAX_CHARS,
                ),
            }
        ],
        budget_scopes=[(Scope.monthly, "copywriter")],
        options=COPY_OPTIONS,
    )
    copy = result.value
    assert isinstance(copy, CaptionCopy)
    return {
        "caption": copy.caption,
        "alt_text": copy.alt_text,
        "model": gateway_facts(result),
        "writer": result.model_alias,
    }


def draft_carousel(campaign: ContentCampaign, *, card_count: int, tone: str = "neutral") -> dict:
    result = _gateway().complete_structured(
        _SKILL,
        PRESETS["private_local"],
        CarouselCopy,
        [
            {
                "role": "user",
                "content": _brief_context(campaign, tone=tone)
                + "\n\n"
                + CARDS.render(
                    count=card_count,
                    topic=campaign.brief.topic,
                    audience=campaign.brief.audience,
                    tone=TONES.get(tone, TONES["neutral"]),
                    max_chars=CARD_MAX_CHARS,
                ),
            }
        ],
        budget_scopes=[(Scope.monthly, "copywriter")],
        options=COPY_OPTIONS,
    )
    copy = result.value
    assert isinstance(copy, CarouselCopy)
    texts = [t.strip() for t in copy.cards if t.strip()][:card_count]
    while len(texts) < card_count:  # a short reply never breaks the deliverable contract
        texts.append(texts[-1] if texts else "…")
    return {
        "cards": [{"card_id": f"card_{i + 1:012d}", "text": t} for i, t in enumerate(texts)],
        "model": gateway_facts(result),
        "writer": result.model_alias,
    }


def draft_hook(campaign: ContentCampaign, *, excerpt_text: str) -> str:
    """Rewrite a short's opening line as a hook."""
    result = _gateway().complete_structured(
        _SKILL,
        PRESETS["private_local"],
        HookCopy,
        [
            {
                "role": "user",
                "content": (
                    f"{_brief_context(campaign)}\n\nRewrite this opening line as a vertical "
                    "short-video hook: one sentence, max 120 characters, curiosity-first. Do not "
                    "add numbers, names, or claims that are not already present.\n\n"
                    f"{excerpt_text}"
                ),
            }
        ],
        budget_scopes=[(Scope.monthly, "copywriter")],
        options=COPY_OPTIONS,
    )
    copy = result.value
    assert isinstance(copy, HookCopy)
    return copy.hook
