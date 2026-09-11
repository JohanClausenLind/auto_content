"""Asking a vision model to look at the pictures — with the story, and at all of them at once.

The frame-review gate has had two reviewers: the deterministic checks in
:mod:`content_factory.qc.frame_review`, which measure tone, colour, edges and palette distance,
and a person. The gap between them is written down in both modules, in almost the same words,
because it is the failure this machine actually produces:

    *What it cannot judge, and the reason a reviewer still looks: whether the subject is the same
    subject. Two drawings of different objects in the same palette and light score as consistent,
    which is precisely the failure this machine produced when "honey-coloured" drew jars of honey.*

A vision model can answer that, and the contract has expected one since it was written —
``ReviewerKind`` is ``operator | agent | vlm``. This module is the ``vlm``. It gathers the story
the frames are for, the per-frame intent, and every frame *in one call* — because the question
that matters is about the set, and a model shown one picture at a time cannot answer it — and asks
for a structured opinion.

Three things it deliberately does not do:

* **It does not decide.** The result is a :class:`SetReview` stored beside the batch. Nothing here
  can accept or reject a frame; :func:`content_factory.qc.verdict.decide` is the only path to a
  verdict and it takes a reviewer who is an operator or an agent that named every frame. A model
  that mistakes what it is looking at does so fluently, which is exactly why its output is an
  opinion shown next to the picture rather than a gate that opens.
* **It does not take the GPU from a run.** The vision tier is 17.8 GB on a 24 GB card, so it
  cannot share with HiDream or LTX. A review asked for while a run is executing is refused with
  the run named, rather than evicting the weights that run is using. ``keep_alive=0`` hands the
  card back the moment the answer arrives.
* **It does not claim to be current.** The review binds to the digests of the pictures it saw, so
  a regenerated frame is not covered by an opinion about the frame it replaced — the same rule
  :func:`content_factory.qc.verdict.merge_verdict` enforces for a human verdict.
"""

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
    """The review cannot be asked for, and the message says what to do about it.

    Its own type because every caller turns it into the same thing — a sentence for the operator
    and no state change — and none of them should have to tell it apart from a real failure of the
    model call by reading the string.
    """


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
    # No egress, so the gateway drops every cloud candidate whatever the policy says. The frames
    # are unpublished work and several of them are the ones that came out wrong; none of that
    # leaves the machine to be judged.
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
    """Exactly the judgement, and nothing that would be a claim about provenance.

    A private model rather than :class:`SetReview` itself: the stored contract carries provenance
    the model must not be able to write — which weights answered, what they were shown, how long
    it took, and the digests the opinion binds to. A model allowed to fill those in could hand
    back a review that says it is about pictures it never saw.
    """

    frames: list[FrameOpinion] = Field(min_length=1, max_length=MAX_FRAMES)
    set: SetOpinion


def review_path(deliverable_dir: Path) -> Path:
    return deliverable_dir / frame_reviews.REVIEW_REL / REVIEW_FILENAME


def load_review(deliverable_dir: Path) -> SetReview | None:
    """The stored opinion, or None when there is none or it no longer parses.

    An unreadable file is None rather than an error: it is an opinion, the run does not depend on
    it, and a panel must not fail to draw the pictures because a cached judgement went stale in a
    way the contract cannot read.
    """
    try:
        return SetReview.model_validate_json(review_path(deliverable_dir).read_text())
    except (OSError, ValueError):
        return None


def digests_of(batch: FrameReviewBatch) -> dict[str, str]:
    """What the batch says the pictures are, as the review binds to them."""
    return {frame.frame_id: frame.png_sha256 for frame in batch.frames}


# --- the context the model is given -------------------------------------------------------------


def _story_text(run_dir: Path, deliverable_dir: Path) -> str:
    """The story these frames are for, in the words the run itself recorded.

    Read from the plan on disk rather than rebuilt from the campaign, because the plan is what the
    picture stages were actually given: a run resumed with a different brief would otherwise be
    reviewed against a story it never drew.
    """
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
    """What each frame was asked to show, by frame id, from the shot plan.

    Best-effort on purpose: several lanes have no per-frame prompt at all — an image set's frames
    are edits of one anchor under a lock, and what each shows is the control plan's business, not
    a written instruction. A frame with no recorded intent is described in the prompt as having
    none, which is honest and still lets `shows` do its work.
    """
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
    """The intent recorded for a frame, matched on the shot half of its id.

    ``shot_ab54…:0000`` is one frame of a shot and ``frame:0007`` is one keyframe of a sequence,
    so the lookup is on the part before the colon and falls back to the whole id.
    """
    head, _, _tail = frame_id.rpartition(":")
    return intents.get(head) or intents.get(frame_id) or "(no per-frame instruction recorded)"


def _image_part(path: Path) -> dict[str, Any]:
    """One frame, downscaled, as litellm's image content part.

    A data URI rather than a file path or a URL: the provider is loopback Ollama, ``ollama_chat``
    extracts base64 from exactly this shape, and there is no HTTP server in the loop that would
    have to be allowed to read the run directory.

    The downscale is not a nicety, it is what makes the call possible. Measured on this machine
    against the running Ollama (2026-09-11), one upscaled frame of ``w-iceberg`` at its native
    2560x1440 costs **3,618 prompt tokens**, so a six-frame set came to 22,494 and was refused
    outright. The same frame at :data:`REVIEW_LONG_EDGE`:

        native  2560x1440   3,618 tokens
        768      768x432      354
        512      512x288      162
        384      384x216      102

    768 buys a twelve-frame review for about 4,200 tokens. It is also enough to judge what is
    being asked: ``qc.frame_review`` measures tone, colour and palette distance at 640x360, and
    "is this the same subject in the same world" does not need more pixels than that.
    """
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
    # JPEG at 90 rather than PNG: a lossless 768px frame is four times the bytes for a judgement
    # about subject and staging, and the artefacts at this quality are well below the faults the
    # reviewer is being asked about.
    image.save(buffer, format="JPEG", quality=90)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}


def _refuse_while_a_run_holds_the_card() -> None:
    """Refuse rather than evict. The vision tier does not share a 24 GB card with HiDream.

    ``services.local._release_gpu_if_idle`` makes the same check the other way round, for the same
    reason: on this machine an idle HiDream held 18,936 MiB and left 3.2 GiB free, so a review
    started mid-run would either OOM or take the weights the run is using.
    """
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
    """The weight the alias resolved to, as the provider spells it.

    Recorded on the review rather than the alias alone: "the local vision model" is three
    different models over a year, and an opinion is worth what the model that gave it is worth.
    """
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
    """The one call: the story, each frame's intent, and the pictures themselves.

    Returns the messages, the intent text exactly as the model will see it (stored on the review,
    so a reader can tell whether a "does not match" is about the picture or about a brief that
    never arrived), and the frame ids that were actually attached.
    """
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
    """Ask the vision model about this deliverable's frames, and write the answer beside them.

    Raises :class:`VlmReviewUnavailableError` when the review cannot be asked for at all — no
    batch, no frame files on disk, a run holding the card — and lets a genuine model failure (no
    server, no weights, OOM, an answer that will not validate) propagate as itself, because those
    want different responses and a single error type would hide which one happened.
    """
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
    # Every frame id the model returns is checked against the ones actually attached, and
    # anything else is dropped. A model that volunteered an opinion about a picture it was not
    # shown has said something about nothing, and storing it would put that sentence next to a
    # real image in the panel — or, for a drifting id, put a mark on a frame that does not exist.
    seen = set(attached)
    opinions = tuple(f for f in answer.frames if f.frame_id in seen)
    if not opinions:
        # It answered about none of the pictures it was given, which is a model that did not do
        # the task rather than a review with nothing in it. The caller turns this into "the
        # reviewer did not answer", which is the right thing to fix.
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
    """The stored review and whether it is about the pictures now on disk.

    Two values rather than discarding a stale one, because "the model looked at the frames you
    replaced" is worth showing — labelled — while "there is no review" is a different sentence
    with a different button under it.
    """
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
