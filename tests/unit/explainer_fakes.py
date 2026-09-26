"""Fakes for the explainer pipeline tests: tiny mp4s, tones, even alignment, canned QC."""

from __future__ import annotations

import math
import subprocess
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from content_factory.explainer.narration import STEM_RATE_HZ, Asr, synth_cache_key
from content_factory.explainer.pipeline import EpisodeConfig, Seams
from content_factory.explainer.qc import QcFinding
from content_factory.explainer.timing import ESTIMATED_TOKEN_MS
from content_factory.schemas.explainer import (
    DiagramLayout,
    DiagramTemplate,
    ExplainerRenderBundle,
    LayoutEdge,
    LayoutNode,
    LayoutPoint,
    PixelBox,
    ScriptPlan,
    ScriptSegment,
    VisualSpec,
    VoiceSpec,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "explainer"
SIXTY = FIXTURES / "sixty"
VOICE = VoiceSpec(kind="preset", voice_id="af_heart", model_id="kokoro", model_revision="82M")


def fake_layouts(spec: VisualSpec, regions: dict[str, PixelBox]) -> tuple[DiagramLayout, ...]:
    """Diagram nodes in a row across the plot, edges straight between them; no Node, no ELK."""
    layouts: list[DiagramLayout] = []
    for scene in spec.scenes:
        template = scene.template
        if not isinstance(template, DiagramTemplate):
            continue
        plot = regions[scene.scene_id]
        step = plot.width // len(template.nodes)
        boxes = {
            n.entity_id: PixelBox(x=plot.x + i * step, y=plot.y, width=step - 24, height=64)
            for i, n in enumerate(template.nodes)
        }
        edges = tuple(
            LayoutEdge(
                entity_id=e.entity_id,
                points=(
                    LayoutPoint(x=boxes[e.source_entity_id].x, y=plot.y + 32),
                    LayoutPoint(x=boxes[e.target_entity_id].x, y=plot.y + 32),
                ),
            )
            for e in template.edges
        )
        layouts.append(
            DiagramLayout(
                scene_id=scene.scene_id,
                width=plot.width,
                height=plot.height,
                nodes=tuple(LayoutNode(entity_id=k, box=b) for k, b in boxes.items()),
                edges=edges,
            )
        )
    return tuple(layouts)


def write_tone(path: Path, duration_ms: int, *, rate: int = STEM_RATE_HZ) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(duration_ms * rate / 1000)) / rate
    samples = 0.3 * np.sin(2 * math.pi * 220 * t)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((samples * 32767).astype("<i2").tobytes())
    return path


def tone_synth(segment: ScriptSegment, _voice: VoiceSpec, path: Path) -> None:
    """A tone as long as the estimated speech, so the compiled timing matches the estimate."""
    write_tone(path, len(segment.tokens) * ESTIMATED_TOKEN_MS)


def even_aligner(audio: Path, words: Sequence[str]) -> dict[str, Any]:
    """Words spread evenly over the audio, all confident."""
    with wave.open(str(audio), "rb") as wf:
        duration = round(wf.getnframes() * 1000 / wf.getframerate())
    step = (duration - 100) / max(1, len(words))
    return {
        "words": [
            {
                "index": i,
                "start_ms": int(50 + i * step),
                "end_ms": int(50 + (i + 1) * step),
                "score": 0.95,
            }
            for i in range(len(words))
        ],
        "aligner": "even",
        "aligner_version": "0",
        "model_id": "test",
    }


def heard_script(script: ScriptPlan, voice: VoiceSpec = VOICE, seed: int = 0) -> Asr:
    """An ASR that hears a take's segment word for word: by synth cache key or take folder."""
    by_key = {synth_cache_key(s, voice, seed=seed): s for s in script.segments}
    by_id = {s.segment_id: s for s in script.segments}

    def hear(audio: Path) -> list[str]:
        segment = by_key.get(audio.stem) or by_id.get(audio.parent.name) or by_id.get(audio.stem)
        return list(segment.tokens) if segment else []

    return hear


class CountingRender:
    """A 64x36 colour mp4 with silent audio, as long as the timeline, tagged with the bundle."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, bundle: ExplainerRenderBundle, out: Path, crf: int | None) -> Path:
        self.calls.append(out)
        seconds = bundle.timeline.total_frames / bundle.timeline.fps
        fps = bundle.timeline.fps
        out.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=0x1d2b3a:s=64x36:r={fps}",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-metadata", f"comment={bundle.content_hash()}", str(out),
        ]  # fmt: skip
        subprocess.run(args, check=True, capture_output=True)
        return out


class CountingStills:
    def __init__(self) -> None:
        self.calls: list[tuple[int, ...]] = []

    def __call__(
        self, bundle: ExplainerRenderBundle, out_dir: Path, frames: Sequence[int]
    ) -> list[Path]:
        self.calls.append(tuple(frames))
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = [out_dir / f"f{frame:06d}.png" for frame in frames]
        for path in paths:
            Image.new("RGB", (64, 36), (29, 43, 58)).save(path)
        return paths


class CannedQc:
    """One passing check, plus a failing overlap with no typed repair when told to fail."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def __call__(self, bundle: ExplainerRenderBundle, mp4: Path, **_: object) -> list[QcFinding]:
        self.calls += 1
        compiled = bundle.timeline.scenes[1]
        scene = compiled.scene_id
        findings = [QcFinding("contract", scene, None, 0, None, 0.0, True, "canned pass")]
        if self.fail:
            last = compiled.start_frame + compiled.duration_frames - 1
            at_ms = last * 1000 // bundle.timeline.fps
            evidence = "two labels overlap by 14 px"
            findings.append(QcFinding("overlap", scene, None, at_ms, 14.0, 0.0, False, evidence))
        return findings


def fake_seams(**overrides: Any) -> Seams:
    fields: dict[str, Any] = {
        "render_video": CountingRender(),
        "render_stills": CountingStills(),
        "layout_diagrams": fake_layouts,
        "qc": CannedQc(),
        "synth": tone_synth,
        "aligner": even_aligner,
    }
    return Seams(**(fields | overrides))


def sixty_config(root: Path, **overrides: Any) -> EpisodeConfig:
    fields: dict[str, Any] = {
        "episode_id": "epi_sixty0001",
        "pack": SIXTY / "pack.json",
        "script": SIXTY / "script.json",
        "spec": SIXTY / "spec.json",
        "voice": VOICE,
        "synth": "kokoro",
        "output_root": root / "episodes",
    }
    return EpisodeConfig(**(fields | overrides))
