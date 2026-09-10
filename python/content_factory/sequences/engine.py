"""Consistent image-sequence engine (16.6): anchor → lock → deterministic controls →
hub-and-spoke keyframes → drift QC with bounded regeneration → packaging.

Every keyframe is generated as a reference edit of the ANCHOR (never frame N from N-1). A frame's
cache marker folds in the lock, control, instruction, and attempt salt, so a single-frame revision
rebuilds exactly that frame."""

from __future__ import annotations

import io
import json
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from content_factory.schemas.base import canonical_dumps, sha256_hex
from content_factory.schemas.sequences import (
    Box,
    ControlKind,
    FrameDelta,
    GenerationLock,
    MotionPlan,
)
from content_factory.sequences.control_compile import compile_control_assets, interpolate_box
from content_factory.sequences.drift import DriftReport, drift_report
from content_factory.sequences.instructions import compile_edit_instruction
from content_factory.services.gpu_pool import WorkerPool


class SequenceError(Exception):
    pass


@dataclass(frozen=True)
class ControlConditioning:
    """Everything a frame is conditioned on besides the anchor and the text:

    * ``reference_pngs`` — ordered reference images. Identity references (one per layout box)
      come first, then structural references (the Blender rough render, the OpenPose skeleton).
    * ``layout_boxes`` — where each identity reference goes (relative x, y, w, h), at most as
      many as there are references.
    * ``control_png`` — the legacy 2D control raster; kept in the cache key, sent as one more
      reference only when the backend opts in.
    """

    control_png: bytes | None = None
    reference_pngs: tuple[bytes, ...] = ()
    layout_boxes: tuple[Box, ...] = ()

    def sha256(self) -> str:
        return sha256_hex(
            canonical_dumps(
                {
                    "control": sha256_hex(self.control_png) if self.control_png else None,
                    "refs": [sha256_hex(r) for r in self.reference_pngs],
                    "boxes": [b.model_dump(mode="json") for b in self.layout_boxes],
                }
            ).encode()
        )

    @property
    def empty(self) -> bool:
        return not (self.control_png or self.reference_pngs or self.layout_boxes)


class ReferenceEditBackend(ABC):
    """A reference_image_edit-capable executor (ComfyWorkflowPackage or provider API)."""

    name: str

    @abstractmethod
    def edit(
        self,
        anchor_png: bytes,
        control_png: bytes,
        instruction: str,
        lock: GenerationLock,
        *,
        attempt: int,
    ) -> bytes: ...

    def edit_conditioned(
        self,
        anchor_png: bytes,
        conditioning: ControlConditioning,
        instruction: str,
        lock: GenerationLock,
        *,
        attempt: int,
    ) -> bytes:
        """Edit with a full conditioning bundle. Backends that only understand the legacy control
        raster get it through ``edit``; richer backends override this."""
        return self.edit(
            anchor_png, conditioning.control_png or b"", instruction, lock, attempt=attempt
        )

    def generate(
        self, prompt: str, conditioning: ControlConditioning, lock: GenerationLock, *, seed: int
    ) -> bytes:
        """Generate an anchor (no existing image to edit) from text plus conditioning."""
        raise NotImplementedError(f"{self.name} cannot generate anchors")


class MockReferenceEditBackend(ReferenceEditBackend):
    """Deterministic stand-in: repaints the anchor with the subject moved to the control's boxes.
    Faithful by construction; `drift_at` can inject an off-style frame on a given attempt."""

    name = "mock-reference-edit"

    def __init__(self, drift_at: tuple[int, int] | None = None) -> None:
        self.drift_at = drift_at  # (frame_index_marker_in_instruction, attempt) — see edit()
        self.calls: list[dict] = []

    def edit(
        self,
        anchor_png: bytes,
        control_png: bytes,
        instruction: str,
        lock: GenerationLock,
        *,
        attempt: int,
    ) -> bytes:
        control = Image.open(io.BytesIO(control_png)).convert("RGB")
        self.calls.append(
            {"anchor_sha": sha256_hex(anchor_png), "instruction": instruction, "attempt": attempt}
        )
        frame_index = (
            int(instruction.split("frame ")[-1].split(" ")[0]) if "frame " in instruction else -1
        )
        if self.drift_at is not None and self.drift_at == (frame_index, attempt):

            def _brighten(v: int) -> int:
                return min(255, v + 90)

            drifted = Image.open(io.BytesIO(anchor_png)).convert("RGB").point(_brighten)
            buf = io.BytesIO()
            drifted.save(buf, format="PNG", compress_level=6)
            return buf.getvalue()
        base = Image.open(io.BytesIO(anchor_png)).convert("RGB")
        out = base.copy()
        draw = ImageDraw.Draw(out)
        w, h = out.size
        # Deterministic repaint: cover the centre band with the background colour (removing the
        # subject wherever it was), then draw the subject where the control says it should be.
        bg = base.getpixel((1, 1))
        draw.rectangle([0, int(h * 0.3), w, int(h * 0.8)], fill=bg)
        control_l = control.convert("L")
        mask = control_l.point(lambda v: 255 if int(v) > 8 else 0)  # type: ignore[arg-type]
        bbox = mask.getbbox()
        if bbox:
            draw.rectangle(bbox, fill=(180, 60, 40))
        buf = io.BytesIO()
        out.save(buf, format="PNG", compress_level=6)
        return buf.getvalue()

    def generate(
        self, prompt: str, conditioning: ControlConditioning, lock: GenerationLock, *, seed: int
    ) -> bytes:
        """Deterministic anchor: the mock canvas plus one filled rectangle per layout box, so a
        conditioned anchor is visibly different from an unconditioned one."""
        self.calls.append(
            {
                "prompt": prompt,
                "seed": seed,
                "conditioning": conditioning.sha256(),
                "generate": True,
            }
        )
        first = conditioning.layout_boxes[0] if conditioning.layout_boxes else None
        png = make_anchor(
            lock.model_copy(update={"seed": seed}),
            width=lock.width,
            height=lock.height,
            subject_box=first,
        )
        img = Image.open(io.BytesIO(png)).convert("RGB")
        draw = ImageDraw.Draw(img)
        w, h = img.size
        # A different prompt has to give a different picture, or every cache test that turns a
        # prompt knob is vacuous: the anchor comes back byte-identical, its sha256 is unchanged,
        # and the clip generated from it is served from cache even though a real backend would
        # have drawn something else. One deterministic row of colour keyed on the prompt is enough
        # to make that real while leaving the frame's tonal statistics where they were.
        tint = sha256_hex(prompt.encode())
        draw.rectangle(
            [0, 0, w, 0],
            fill=(int(tint[0:2], 16), int(tint[2:4], 16), int(tint[4:6], 16)),
        )
        if len(conditioning.layout_boxes) <= 1:
            buf = io.BytesIO()
            img.save(buf, format="PNG", compress_level=6)
            return buf.getvalue()
        for i, box in enumerate(conditioning.layout_boxes[1:], start=1):
            colour = (60 + 40 * i % 160, 90, 150)
            draw.rectangle(
                [
                    int(w * box.x),
                    int(h * box.y),
                    int(w * (box.x + box.w)),
                    int(h * (box.y + box.h)),
                ],
                fill=colour,
            )
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=6)
        return buf.getvalue()


@dataclass(frozen=True)
class SequenceResult:
    anchor_sha256: str
    lock: GenerationLock
    frames: list[dict]
    regenerated: list[int]
    failed: list[int]
    packaging: dict


def make_anchor(
    lock: GenerationLock, *, width: int, height: int, subject_box: Box | None = None
) -> bytes:
    """Mock anchor generation (deterministic from the lock's seed). Real anchors come from a
    text_to_image ComfyWorkflowPackage selected by the router."""
    seed = lock.seed
    box = subject_box or Box(x=0.42, y=0.45, w=0.16, h=0.25)
    img = Image.new("RGB", (width, height), (240 - seed % 16, 238, 232))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, int(height * 0.8), width, height], fill=(40 + seed % 32, 60, 70))
    draw.rectangle(
        [
            int(width * box.x),
            int(height * box.y),
            int(width * (box.x + box.w)),
            int(height * (box.y + box.h)),
        ],
        fill=(180, 60, 40),
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def _frame_marker_hash(
    lock: GenerationLock,
    control_sha: str,
    instruction: str,
    *,
    conditioning_sha: str | None = None,
) -> str:
    key: dict = {
        "lock": lock.model_dump(mode="json"),
        "control": control_sha,
        "instruction": instruction,
    }
    if conditioning_sha is not None:  # absent -> identical to the pre-conditioning hash
        key["conditioning"] = conditioning_sha
    return sha256_hex(canonical_dumps(key).encode())


@dataclass(frozen=True)
class _FrameJob:
    """One frame's inputs, resolved before any GPU is touched.

    Everything here is computed in a single serial pass so the pool only ever sees independent
    work: no job reads another job's output, and the cache decision is already made."""

    idx: int
    control_png: bytes
    control_sha256: str
    instruction: str
    conditioning: ControlConditioning | None
    marker_hash: str
    marker_path: Path
    boxes: list[Box]
    seed_offset: int = 0
    """Added to the attempt number, and so to the seed the backend derives from it.

    A frame a person rejected has to come back *different*. Redrawing it with the same
    instruction and the same first attempt reproduces the same picture exactly — measured
    2026-09-10, when a rejected kilim frame was redrawn and came back byte-for-byte the drawing
    that had just been turned down. It is the same lesson the TTS retake learned: a retry that
    reuses the seed reproduces what it was retrying."""


@dataclass(frozen=True)
class _FrameOutcome:
    png: bytes
    report: DriftReport
    attempts: int
    regenerated: int
    """How many attempts drifted. The caller expands this back into one entry per failure so the
    ``regenerated`` list keeps the shape the serial loop gave it."""
    served_by: str
    record: dict | None = None
    """The frame's manifest entry, already written to disk beside the PNG, or None if it never
    passed its drift check. Built in the worker rather than in the caller because that is where
    the frame stops being in flight."""


def build_sequence(
    plan: MotionPlan,
    lock: GenerationLock,
    backend: ReferenceEditBackend,
    workdir: Path,
    *,
    backends: Sequence[ReferenceEditBackend] | None = None,
    frame_instructions: dict[int, str] | None = None,
    conditioning_for: Callable[[int], ControlConditioning] | None = None,
    max_regen_attempts: int = 3,
    locked_region_similarity_min: float = 0.92,
    style_delta_max: float = 0.15,
    seed_offsets: Mapping[int, int] | None = None,
) -> SequenceResult:
    """Build every frame of a hub-and-spoke sequence from one anchor.

    ``backends``, when given, is a pool of interchangeable servers -- one per GPU host -- and the
    frames are spread over it. It REPLACES ``backend`` for the frame work (``backend`` still makes
    the anchor), so the local server has to be in the list to take a share. Frames never read each
    other and ``drift_report`` measures each against the *anchor*, so which host serves which frame
    changes the wall clock and nothing else. Omit it and the serial path runs untouched."""
    workdir.mkdir(parents=True, exist_ok=True)
    frames_dir = workdir / "frames"
    frames_dir.mkdir(exist_ok=True)
    anchor_path = workdir / "anchor.png"
    first_box = plan.subjects[0].keyframes[0].layout
    if anchor_path.exists():
        anchor_png = anchor_path.read_bytes()
    else:
        anchor_png = make_anchor(
            lock, width=plan.canvas_width, height=plan.canvas_height, subject_box=first_box
        )
        anchor_path.write_bytes(anchor_png)
    anchor_sha = sha256_hex(anchor_png)
    if lock.reference_asset_sha256 != anchor_sha:
        lock = lock.model_copy(update={"reference_asset_sha256": anchor_sha})

    controls = list(compile_control_assets(plan, ControlKind.layout_boxes))
    frame_instructions = frame_instructions or {}
    frames: list[dict] = []
    regenerated: list[int] = []
    failed: list[int] = []

    from content_factory.sequences.control_compile import _bracket  # deterministic helper

    order: list[int] = []
    done_already: dict[int, dict] = {}
    jobs: list[_FrameJob] = []
    for compiled in controls:
        idx = compiled.asset.frame_index
        subject = plan.subjects[0]
        # The frame's motion region: the interpolated layout box for drift masking.
        prev, nxt, t = _bracket(subject, idx)
        box = interpolate_box(prev.layout, nxt.layout, t) or Box(x=0.3, y=0.3, w=0.4, h=0.4)
        base_instruction = frame_instructions.get(
            idx,
            f"Move {subject.label} to the plotted position for frame {idx} .",
        )
        delta = FrameDelta(
            kind="move_subject", subject_label=subject.label, instruction=base_instruction
        )
        instruction = compile_edit_instruction(delta, lock)
        marker_path = frames_dir / f"{idx:04d}.done.json"
        conditioning = conditioning_for(idx) if conditioning_for is not None else None
        marker_hash = _frame_marker_hash(
            lock,
            compiled.asset.png_sha256,
            instruction,
            conditioning_sha=conditioning.sha256() if conditioning is not None else None,
        )
        order.append(idx)
        if marker_path.exists():
            cached = json.loads(marker_path.read_text())
            if cached["input_hash"] == marker_hash:
                done_already[idx] = {**cached, "cache_hit": True}
                continue
        jobs.append(
            _FrameJob(
                idx=idx,
                control_png=compiled.png,
                control_sha256=compiled.asset.png_sha256,
                instruction=instruction,
                conditioning=conditioning,
                seed_offset=(seed_offsets or {}).get(idx, 0),
                marker_hash=marker_hash,
                marker_path=marker_path,
                # The motion region: where the subject IS this frame plus where it started
                # (it legitimately leaves its anchor position). Everything else is locked.
                boxes=[b for b in (box, first_box) if b],
            )
        )

    def _render(worker: ReferenceEditBackend, job: _FrameJob) -> _FrameOutcome:
        report: DriftReport | None = None
        png: bytes | None = None
        attempts = 0
        regen = 0
        for attempt in range(1, max_regen_attempts + 1):
            attempts = attempt
            if job.conditioning is not None:
                full = ControlConditioning(
                    control_png=job.control_png,
                    reference_pngs=job.conditioning.reference_pngs,
                    layout_boxes=job.conditioning.layout_boxes,
                )
                png = worker.edit_conditioned(
                    anchor_png, full, job.instruction, lock, attempt=attempt + job.seed_offset
                )
            else:
                png = worker.edit(
                    anchor_png,
                    job.control_png,
                    job.instruction,
                    lock,
                    attempt=attempt + job.seed_offset,
                )
            report = drift_report(
                job.idx,
                anchor_png,
                png,
                job.boxes,
                locked_region_similarity_min=locked_region_similarity_min,
                style_delta_max=style_delta_max,
            )
            if report.passed:
                break
            regen += 1
        assert png is not None and report is not None
        served_by = getattr(worker, "endpoint", worker.name)
        # Written here, the moment the frame exists, rather than after the whole pool comes back.
        # Every frame already carried its own ``input_hash`` marker so that a rerun could skip the
        # ones that were finished -- and it could not, because nothing reached disk until the last
        # frame did. A sequence is tens of minutes to hours of GPU time; one interruption threw all
        # of it away and the next run redrew every picture. Paths are keyed by frame index, so two
        # workers never write the same file.
        record: dict | None = None
        if not report.passed:
            # The picture that did not pass, kept where it can be looked at. The block this
            # becomes says "look at sequence/frames/0000.png" -- and that file was never written,
            # because only a passing frame reached disk. An operator was being sent to a path that
            # did not exist, to judge a rejection they could not see, and the measurement that
            # rejected it existed only in memory. Out of `frames/` so the review sheet's glob and
            # the cut do not pick a rejected drawing up.
            reject_dir = workdir / "rejected"
            reject_dir.mkdir(parents=True, exist_ok=True)
            (reject_dir / f"{job.idx:04d}.png").write_bytes(png)
            (reject_dir / f"{job.idx:04d}.json").write_text(
                json.dumps(
                    {
                        "frame_index": job.idx,
                        "attempts": attempts,
                        "locked_region_similarity": report.locked_region_similarity,
                        "style_delta": report.style_delta,
                        "locked_region_similarity_min": locked_region_similarity_min,
                        "style_delta_max": style_delta_max,
                        "reasons": list(report.reasons),
                        "instruction": job.instruction,
                        "served_by": served_by,
                    },
                    indent=1,
                    sort_keys=True,
                )
            )
        if report.passed:
            (frames_dir / f"{job.idx:04d}.png").write_bytes(png)
            record = {
                "frame_index": job.idx,
                "input_hash": job.marker_hash,
                "png_sha256": sha256_hex(png),
                "control_sha256": job.control_sha256,
                "lock_sha256": sha256_hex(canonical_dumps(lock.model_dump(mode="json")).encode()),
                "reference": "anchor",
                "anchor_sha256": anchor_sha,
                "attempts": attempts,
                "drift": {
                    "locked": report.locked_region_similarity,
                    "style": report.style_delta,
                },
                "served_by": served_by,
                "cache_hit": False,
            }
            job.marker_path.write_text(json.dumps(record, indent=1, sort_keys=True))
        return _FrameOutcome(
            png=png,
            report=report,
            attempts=attempts,
            regenerated=regen,
            served_by=served_by,
            record=record,
        )

    outcomes = WorkerPool(list(backends) if backends else [backend]).map_ordered(jobs, _render)
    by_idx = {job.idx: (job, outcome) for job, outcome in zip(jobs, outcomes, strict=True)}

    for idx in order:
        cached_record = done_already.get(idx)
        if cached_record is not None:
            frames.append(cached_record)
            continue
        _job, outcome = by_idx[idx]
        # One entry per failed attempt, in frame order: the shape the serial loop produced.
        regenerated.extend([idx] * outcome.regenerated)
        if outcome.record is None:
            failed.append(idx)
            continue
        frames.append(outcome.record)

    packaging = package_sequence(plan, workdir) if not failed else {}
    return SequenceResult(anchor_sha, lock, frames, regenerated, failed, packaging)


def package_sequence(plan: MotionPlan, workdir: Path, *, fps: int = 8) -> dict:
    frames_dir = workdir / "frames"
    frame_files = sorted(frames_dir.glob("*.png"))
    if not frame_files:
        raise SequenceError("no frames to package")
    # Contact sheet
    cols = min(4, len(frame_files))
    rows = (len(frame_files) + cols - 1) // cols
    thumb_w, thumb_h = 320, round(320 * plan.canvas_height / plan.canvas_width)
    sheet = Image.new(
        "RGB", (cols * thumb_w + (cols + 1) * 16, rows * (thumb_h + 40) + 16), (250, 249, 246)
    )
    draw = ImageDraw.Draw(sheet)
    for i, f in enumerate(frame_files):
        img = Image.open(f).resize((thumb_w, thumb_h))
        x = 16 + (i % cols) * (thumb_w + 16)
        y = 16 + (i // cols) * (thumb_h + 40)
        sheet.paste(img, (x, y))
        draw.text((x, y + thumb_h + 6), f"frame {i + 1}", fill=(60, 60, 60))
    sheet_path = workdir / "contact-sheet.png"
    sheet.save(sheet_path, compress_level=6)
    # Animated preview (MP4)
    preview = workdir / "preview.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "%04d.png"),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(preview),
        ],
        check=True,
    )
    # Print-ready flipbook PDF: one frame per page, margins + binding edge + frame numbers.
    pdf_path = workdir / "flipbook.pdf"
    margin, binding = 60, 140
    pages: list[Image.Image] = []
    for i, f in enumerate(frame_files):
        img = Image.open(f).convert("RGB")
        page = Image.new(
            "RGB", (img.width + margin * 2 + binding, img.height + margin * 2), (255, 255, 255)
        )
        page.paste(img, (binding + margin, margin))
        d = ImageDraw.Draw(page)
        d.text(
            (binding + margin, margin + img.height + 8),
            f"{i + 1} / {len(frame_files)}",
            fill=(0, 0, 0),
        )
        d.line([(binding // 2, 0), (binding // 2, page.height)], fill=(200, 200, 200), width=2)
        pages.append(page)
    pages[0].save(pdf_path, save_all=True, append_images=pages[1:], format="PDF", resolution=300)
    return {
        "frames": len(frame_files),
        "contact_sheet": str(sheet_path),
        "preview": str(preview),
        "flipbook_pdf": str(pdf_path),
    }
