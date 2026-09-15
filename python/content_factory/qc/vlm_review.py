"""Asking a vision model to look at the pictures — with the story, and at all of them at once."""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel, Field

from content_factory.budgets.ledger import Cap, Scope
from content_factory.models.catalog import build_gateway
from content_factory.models.gateway import GatewayOptions, Message, ModelGateway
from content_factory.prompting import SET_REVIEW
from content_factory.schemas.review import (
    FrameOpinion,
    FrameReviewBatch,
    SetOpinion,
    SetReview,
)
from content_factory.schemas.skills import (
    PRESETS,
    CostEstimator,
    ExecutionLocation,
    ExecutorType,
    Lifecycle,
    SkillManifest,
    SkillPermissions,
)
from content_factory.services import frame_reviews

REVIEW_FILENAME = "ai-review.json"
"""Beside ``batch.json`` and ``verdict.json``, in ``reviews/frames/``.

On disk rather than in a database for the same reason the batch is: it belongs to the run, it
travels with the run directory, and clearing the run clears it. The panel and the CLI read the
same file, so they cannot disagree about what the model said."""

VISION_ALIAS = "local_structured_quality"
"""The only catalogue entry that declares ``vision``.

Named here as a constant rather than left to routing to discover, because the failure mode of
getting it wrong is silent: a text-only model handed an image list answers confidently about
pictures it never received. ``required_models`` on the manifest below turns that into a refusal
from :func:`content_factory.models.routing.decide` instead."""

MAX_FRAMES = 12
"""How many pictures go into one call.

Not a policy choice — a context one. The frames are sent as images alongside a prompt that
describes each, against a 24 GB card whose KV cache is already the binding constraint on this
model (see ``catalog.py``: the declared 262k window is not a window that fits). A set larger than
this is reviewed as its first twelve with the count said plainly, because a truncated answer that
looks complete is the one failure mode a review must not have."""

REVIEW_LONG_EDGE = 768
"""Longest edge, in pixels, of a frame as the reviewer sees it. See :func:`_image_part` for the
token measurements this number comes from."""

MAX_IMAGE_BYTES = 48 * 1024 * 1024
"""A guard on the source file, not on what is sent — the frame is re-encoded at
:data:`REVIEW_LONG_EDGE` before it goes anywhere. It is here because a SeedVR2 upscale of a
picture story is a real 20 MB PNG and a corrupt one is not, and Pillow decoding an arbitrarily
large file is the one part of this that can hurt the machine rather than the review."""


class VlmReviewUnavailableError(RuntimeError):
    """The review cannot be asked for, and the message says what to do about it."""


_SKILL = SkillManifest(
    skill_id="review.frames.vlm",
    version="1.0.0",
    status=Lifecycle.active,
    purpose="describe each generated frame and judge whether the set is one set",
    input_schema="FrameReviewBatch",
    output_schema="SetReview",
    executor=ExecutorType.model_role,
    implementation_ref="content_factory.qc.vlm_review:review_batch",
    permitted_locations=(ExecutionLocation.local_gpu,),
    required_models=(VISION_ALIAS,),
    # No egress, so the gateway drops every cloud candidate whatever the policy says: unpublished
    # frames never leave the machine to be judged.
    permissions=SkillPermissions(network_egress=False),
    license_evidence="Qwen3 derivative (Apache-2.0 upstream); DavidAU fine-tune, licence unstated",
    cost=CostEstimator(kind="per_token", usd=0),
    timeout_seconds=900,
    max_retries=1,
)

_OPTIONS = GatewayOptions(
    structured_output=True,
    think=False,
    # The prompt is long and carries a dozen images; the answer is a paragraph per frame plus the
    # set. Both halves come out of the same window, and the default is not enough for either.
    num_ctx=16384,
    max_tokens=3000,
    # Hand the card back as soon as the answer arrives. A 17.8 GB resident model is the reason
    # `services.local.free_the_gpu` exists, and a review is a one-shot question, not a server.
    keep_alive=0,
    temperature=0.1,
)


# --- what the model is asked to return ----------------------------------------------------------


class _Answer(BaseModel):
    """Exactly the judgement, and nothing that would be a claim about provenance."""

    frames: list[FrameOpinion] = Field(min_length=1, max_length=MAX_FRAMES)
    set: SetOpinion


def review_path(deliverable_dir: Path) -> Path:
    return deliverable_dir / frame_reviews.REVIEW_REL / REVIEW_FILENAME


def load_review(deliverable_dir: Path) -> SetReview | None:
    """The stored opinion, or None when there is none or it no longer parses."""
    try:
        return SetReview.model_validate_json(review_path(deliverable_dir).read_text())
    except (OSError, ValueError):
        return None


def digests_of(batch: FrameReviewBatch) -> dict[str, str]:
    """What the batch says the pictures are, as the review binds to them."""
    return {frame.frame_id: frame.png_sha256 for frame in batch.frames}


# --- the context the model is given -------------------------------------------------------------


def _story_text(run_dir: Path, deliverable_dir: Path) -> str:
    """The story these frames are for, in the words the run itself recorded."""
    lines: list[str] = []
    try:
        plan = json.loads((run_dir / "story" / "plan.json").read_text())
    except (OSError, ValueError):
        plan = {}
    if isinstance(plan, dict):
        for label, key in (("Subject", "visual_subject"), ("Hook", "hook_text")):
            value = plan.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"{label}: {value.strip()}")
        beats = plan.get("beats")
        if isinstance(beats, list):
            for index, beat in enumerate(beats):
                if not isinstance(beat, dict):
                    continue
                text = beat.get("display_text") or beat.get("spoken_text")
                if isinstance(text, str) and text.strip():
                    lines.append(f"Beat {index + 1}: {text.strip()}")
    try:
        lock = json.loads((deliverable_dir / "sequence" / "lock.json").read_text())
    except (OSError, ValueError):
        lock = {}
    if isinstance(lock, dict):
        for label, key in (("Locked style", "style"), ("Locked subject prompt", "prompt")):
            value = lock.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"{label}: {value.strip()}")
    return "\n".join(lines) if lines else "(the run recorded no story plan)"


def _frame_intent(deliverable_dir: Path) -> dict[str, str]:
    """What each frame was asked to show, by frame id, from the shot plan."""
    out: dict[str, str] = {}
    try:
        plan = json.loads((deliverable_dir / "shots" / "plan.json").read_text())
    except (OSError, ValueError):
        return out
    beats: dict[str, str] = {}
    for shot in (plan or {}).get("shots") or []:
        if not isinstance(shot, dict):
            continue
        shot_id = str(shot.get("shot_id") or "")
        prompt = shot.get("prompt") or shot.get("description") or shot.get("beat_id")
        if shot_id and isinstance(prompt, str) and prompt.strip():
            beats[shot_id] = prompt.strip()
    for shot_id, text in beats.items():
        out[shot_id] = text
    return out


def _intent_for(frame_id: str, intents: dict[str, str]) -> str:
    """The intent recorded for a frame, matched on the shot half of its id."""
    head, _, _tail = frame_id.rpartition(":")
    return intents.get(head) or intents.get(frame_id) or "(no per-frame instruction recorded)"


def _image_part(path: Path) -> dict[str, Any]:
    """One frame, downscaled, as litellm's image content part."""
    if path.stat().st_size > MAX_IMAGE_BYTES:
        size = path.stat().st_size
        msg = f"{path.name} is {size / 1e6:.1f} MB; over the {MAX_IMAGE_BYTES / 1e6:.0f} MB cap"
        raise VlmReviewUnavailableError(msg)
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    scale = REVIEW_LONG_EDGE / max(width, height)
    if scale < 1:
        image = image.resize(
            (round(width * scale), round(height * scale)), Image.Resampling.LANCZOS
        )
    buffer = io.BytesIO()
    # JPEG at 90, not PNG: a lossless 768px frame is four times the bytes, and the artefacts at
    # this quality sit well below the faults the reviewer is asked about.
    image.save(buffer, format="JPEG", quality=90)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}


def _refuse_while_a_run_holds_the_card() -> None:
    """Refuse rather than evict."""
    from content_factory.runners.registry import active_runs

    running = active_runs()
    if not running:
        return
    names = ", ".join(sorted({str(getattr(r, "workflow", None) or "a run") for r in running}))
    msg = (
        f"the GPU is busy with {names}. The vision reviewer is 17.8 GB and cannot share the card"
        " with an image or video model, so it is not started while a run is executing —"
        " review when the run finishes, or stop it with `content-factory stop`."
    )
    raise VlmReviewUnavailableError(msg)


def _model_id_of(alias: str) -> str:
    """The weight the alias resolved to, as the provider spells it."""
    from content_factory.models.catalog import default_catalog

    return next((m.model_id for m in default_catalog() if m.alias == alias), alias)


def _gateway_for(gateway: ModelGateway | None) -> ModelGateway:
    if gateway is not None:
        return gateway
    built = build_gateway()
    built.ledger.set_cap(Cap(scope=Scope.monthly, key="frame-review", limit_usd=1.0))
    return built


# --- the review ---------------------------------------------------------------------------------


def build_messages(
    run_dir: Path, deliverable_dir: Path, batch: FrameReviewBatch
) -> tuple[list[Message], str, list[str]]:
    """The one call: the story, each frame's intent, and the pictures themselves."""
    intents = _frame_intent(deliverable_dir)
    attached: list[str] = []
    parts: list[dict[str, Any]] = []
    described: list[str] = []
    for record in batch.frames[:MAX_FRAMES]:
        image = frame_reviews.frame_image(deliverable_dir, record.frame_id)
        if image is None:
            continue  # the batch names a picture that is not where the run left it
        attached.append(record.frame_id)
        asked = _intent_for(record.frame_id, intents)
        described.append(f"- `{record.frame_id}` — asked for: {asked}")
        parts.append({"type": "text", "text": f"Frame `{record.frame_id}`:"})
        parts.append(_image_part(image))
    if not attached:
        msg = (
            f"none of this batch's {len(batch.frames)} frame files are where the run left them,"
            " so there is nothing to look at. Re-run the picture stages first."
        )
        raise VlmReviewUnavailableError(msg)
    story = _story_text(run_dir, deliverable_dir)
    prompt = SET_REVIEW.render(count=len(attached), story=story, frames="\n".join(described))
    if len(batch.frames) > len(attached):
        prompt += (
            f"\n\nNote: this set has {len(batch.frames)} frames and {len(attached)} are attached."
            " Judge only the ones you can see, and do not describe the others."
        )
    intent = f"{story}\n\n{chr(10).join(described)}"
    content = [{"type": "text", "text": prompt}, *parts]
    return [{"role": "user", "content": content}], intent, attached


def review_batch(
    run_dir: Path,
    deliverable_dir: Path,
    *,
    gateway: ModelGateway | None = None,
    now: dt.datetime | None = None,
    check_gpu: bool = True,
) -> SetReview:
    """Ask the vision model about this deliverable's frames, and write the answer beside them."""
    batch = frame_reviews.current_batch(deliverable_dir)
    if batch is None:
        msg = f"no frame-review batch under {deliverable_dir}"
        raise VlmReviewUnavailableError(msg)
    if check_gpu:
        _refuse_while_a_run_holds_the_card()
    messages, intent, attached = build_messages(run_dir, deliverable_dir, batch)

    result = _gateway_for(gateway).complete_structured(
        _SKILL,
        PRESETS["private_local"],
        _Answer,
        messages,
        budget_scopes=[(Scope.monthly, "frame-review")],
        estimated_tokens=6000,
        options=_OPTIONS,
    )
    answer = result.value
    assert isinstance(answer, _Answer)
    # Drop opinions about frames that were not attached: storing one would put a sentence about
    # nothing next to a real image, or a mark on a frame that does not exist.
    seen = set(attached)
    opinions = tuple(f for f in answer.frames if f.frame_id in seen)
    if not opinions:
        # Answering about none of the given pictures is a model that did not do the task, not a
        # review with nothing in it.
        named = ", ".join(f.frame_id for f in answer.frames[:4]) or "nothing"
        msg = (
            f"the reviewer described frames that were not in this set ({named}) and none of the"
            f" {len(attached)} that were. Nothing was stored."
        )
        raise ValueError(msg)
    review = SetReview(
        deliverable_id=batch.deliverable_id,
        reviewed_at=now or dt.datetime.now(dt.UTC),
        model_alias=result.model_alias,
        model_id=_model_id_of(result.model_alias),
        intent=intent[:4000],
        frames=opinions,
        set=answer.set.model_copy(
            update={"drifting_frames": tuple(f for f in answer.set.drifting_frames if f in seen)}
        ),
        digests={k: v for k, v in digests_of(batch).items() if k in seen},
        elapsed_s=result.elapsed_s,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    target = review_path(deliverable_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(review.model_dump_json(indent=1))
    return review


def current_review(run_dir: Path, deliverable_dir: Path) -> tuple[SetReview | None, bool]:
    """The stored review and whether it is about the pictures now on disk."""
    del run_dir  # the batch is per deliverable; the run directory is the caller's addressing
    review = load_review(deliverable_dir)
    if review is None:
        return None, False
    batch = frame_reviews.current_batch(deliverable_dir)
    if batch is None:
        return review, False
    on_disk = {
        frame_id: digest
        for frame_id, digest in digests_of(batch).items()
        if frame_id in review.digests
    }
    return review, review.covers(on_disk)
