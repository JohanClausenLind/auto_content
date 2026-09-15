"""Ask the vision model where the subject is, so the change map can free it and freeze the rest."""

from __future__ import annotations

import base64
import io

from PIL import Image
from pydantic import BaseModel, Field

from content_factory.budgets.ledger import Cap, Scope
from content_factory.models.catalog import build_gateway
from content_factory.models.gateway import GatewayOptions, ModelGateway
from content_factory.schemas.sequences import Box
from content_factory.schemas.skills import (
    PRESETS,
    CostEstimator,
    ExecutionLocation,
    ExecutorType,
    Lifecycle,
    SkillManifest,
    SkillPermissions,
)

GRID = 1000
"""Both axes normalised to 0-1000, independent of aspect: the grid the Ideogram caption uses too."""

LONG_EDGE = 768
"""Downscale before asking: the box returns on a relative grid, so resolution only costs tokens."""

VISION_ALIAS = "local_structured_quality"

_PROMPT = (
    "Find the single subject named below in this image and give its bounding box.\n"
    "The box is [x0, y0, x1, y1] on a 0-1000 grid: x is LEFT to RIGHT, y is TOP to BOTTOM,\n"
    "with x0 < x1 and y0 < y1. Cover the whole subject including limbs and hair, nothing else.\n"
    "If the subject is not in the image, set present to false.\n\nSubject: "
)

_SKILL = SkillManifest(
    skill_id="sequences.subject.locate",
    version="1.0.0",
    status=Lifecycle.active,
    purpose="locate the subject that moves, so everything else can be frozen",
    input_schema="SubjectLocateRequest",
    output_schema="SubjectLocation",
    executor=ExecutorType.model_role,
    implementation_ref="content_factory.sequences.subject_locate:locate_subject",
    permitted_locations=(ExecutionLocation.local_gpu,),
    required_models=(VISION_ALIAS,),
    # No egress: the frames being located are unpublished output and never leave the machine.
    permissions=SkillPermissions(network_egress=False),
    license_evidence="Qwen3 derivative (Apache-2.0 upstream); DavidAU fine-tune, licence unstated",
    cost=CostEstimator(kind="per_token", usd=0),
    timeout_seconds=300,
    max_retries=1,
)

_OPTIONS = GatewayOptions(
    structured_output=True,
    think=False,
    num_ctx=8192,
    max_tokens=400,
    # Hand the card back at once: this is a one-shot question and Ideogram wants the VRAM next.
    keep_alive=0,
    temperature=0.0,
)


class SubjectLocationError(RuntimeError):
    """The vision model did not return a usable box."""


class _Located(BaseModel):
    present: bool
    box: list[int] = Field(min_length=4, max_length=4)
    label: str = Field(default="", max_length=120)


def _image_part(image_png: bytes) -> dict:
    with Image.open(io.BytesIO(image_png)) as opened:
        image = opened.convert("RGB")
    scale = LONG_EDGE / max(image.size)
    if scale < 1:
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS
        )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}


def _gateway_for(gateway: ModelGateway | None) -> ModelGateway:
    if gateway is not None:
        return gateway
    built = build_gateway()
    built.ledger.set_cap(Cap(scope=Scope.monthly, key="subject-locate", limit_usd=1.0))
    return built


def to_box(grid_box: list[int]) -> Box:
    """A [x0, y0, x1, y1] box on the 0-1000 grid as the repo's relative ``Box``."""
    x0, y0, x1, y1 = (max(0, min(GRID, int(v))) for v in grid_box)
    if x1 <= x0 or y1 <= y0:
        msg = f"box {grid_box} is empty once clamped to the 0-{GRID} grid"
        raise SubjectLocationError(msg)
    return Box(x=x0 / GRID, y=y0 / GRID, w=(x1 - x0) / GRID, h=(y1 - y0) / GRID)


def pad_box(box: Box, pad: float) -> Box:
    """Grow a box outward, clamped to the frame."""
    x = max(0.0, box.x - pad)
    y = max(0.0, box.y - pad)
    return Box(x=x, y=y, w=min(1.0 - x, box.w + 2 * pad), h=min(1.0 - y, box.h + 2 * pad))


def locate_subject(
    image_png: bytes,
    subject: str,
    *,
    gateway: ModelGateway | None = None,
    pad: float = 0.02,
) -> Box:
    """Where ``subject`` is in ``image_png``, as a relative box grown outward by ``pad``."""
    # Pad outward: a box tight to the silhouette freezes the subject's own edge pixels, and the
    # repaint then has to meet the original outline exactly (journal 2026-09-15).
    if not subject.strip():
        msg = "subject must name what to find"
        raise SubjectLocationError(msg)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT + subject.strip()},
                _image_part(image_png),
            ],
        }
    ]
    result = _gateway_for(gateway).complete_structured(
        _SKILL,
        PRESETS["private_local"],
        _Located,
        messages,
        budget_scopes=[(Scope.monthly, "subject-locate")],
        estimated_tokens=2000,
        options=_OPTIONS,
    )
    located = result.value
    assert isinstance(located, _Located)
    if not located.present:
        msg = f"the vision model did not find {subject!r} in this frame"
        raise SubjectLocationError(msg)
    box = to_box(located.box)
    return pad_box(box, pad) if pad > 0 else box
