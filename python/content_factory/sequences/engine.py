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


class SequenceError(Exception):
    pass


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
            drifted = (
                Image.open(io.BytesIO(anchor_png)).convert("RGB").point(lambda v: min(255, v + 90))
            )
            buf = io.BytesIO()
            drifted.save(buf, format="PNG", compress_level=6)
            return buf.getvalue()
        base = Image.open(io.BytesIO(anchor_png)).convert("RGB")
        out = base.copy()
        draw = ImageDraw.Draw(out)
        w, h = out.size
        # Clear the anchor's subject area to background, then paint the subject at the control box.
        px = base.getpixel((2, 2))
        for x in range(w):
            for y in range(0, h, max(1, h // 64)):
                pass
        del px
        # Simplified deterministic repaint: cover centre band with background colour, then draw
        # the subject rectangle where the control says it should be.
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


def _frame_marker_hash(lock: GenerationLock, control_sha: str, instruction: str) -> str:
    return sha256_hex(
        canonical_dumps(
            {
                "lock": lock.model_dump(mode="json"),
                "control": control_sha,
                "instruction": instruction,
            }
        ).encode()
    )


def build_sequence(
    plan: MotionPlan,
    lock: GenerationLock,
    backend: ReferenceEditBackend,
    workdir: Path,
    *,
    frame_instructions: dict[int, str] | None = None,
    max_regen_attempts: int = 3,
    locked_region_similarity_min: float = 0.92,
    style_delta_max: float = 0.15,
) -> SequenceResult:
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

    for compiled in controls:
        idx = compiled.asset.frame_index
        subject = plan.subjects[0]
        # The frame's motion region: the interpolated layout box for drift masking.
        from content_factory.sequences.control_compile import _bracket  # deterministic helper

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
        marker_hash = _frame_marker_hash(lock, compiled.asset.png_sha256, instruction)
        if marker_path.exists():
            cached = json.loads(marker_path.read_text())
            if cached["input_hash"] == marker_hash:
                frames.append({**cached, "cache_hit": True})
                continue
        report: DriftReport | None = None
        png: bytes | None = None
        attempts = 0
        for attempt in range(1, max_regen_attempts + 1):
            attempts = attempt
            png = backend.edit(anchor_png, compiled.png, instruction, lock, attempt=attempt)
            report = drift_report(
                idx,
                anchor_png,
                png,
                # The motion region: where the subject IS this frame plus where it started
                # (it legitimately leaves its anchor position). Everything else is locked.
                [b for b in (box, first_box) if b],
                locked_region_similarity_min=locked_region_similarity_min,
                style_delta_max=style_delta_max,
            )
            if report.passed:
                break
            regenerated.append(idx)
        assert png is not None and report is not None
        if not report.passed:
            failed.append(idx)
            continue
        frame_path = frames_dir / f"{idx:04d}.png"
        frame_path.write_bytes(png)
        record = {
            "frame_index": idx,
            "input_hash": marker_hash,
            "png_sha256": sha256_hex(png),
            "control_sha256": compiled.asset.png_sha256,
            "lock_sha256": sha256_hex(canonical_dumps(lock.model_dump(mode="json")).encode()),
            "reference": "anchor",
            "anchor_sha256": anchor_sha,
            "attempts": attempts,
            "drift": {"locked": report.locked_region_similarity, "style": report.style_delta},
            "cache_hit": False,
        }
        marker_path.write_text(json.dumps(record, indent=1, sort_keys=True))
        frames.append(record)

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
