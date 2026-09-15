"""What a dropped file is, and what the canvas should do with it."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from content_factory.artifacts import ArtifactStore
    from content_factory.schemas.content import StagedUpload

COPY_CHUNK_BYTES = 1024 * 1024

Kind = Literal["image", "video", "audio", "document", "data", "text"]

# Which node holds a dropped file of each kind. Data and documents have none on purpose: `ingest`
# already turns every upload into typed sources, and a second door would be two paths for one thing.
SOURCE_NODE: dict[str, str] = {
    "audio": "input.audio",
    "image": "input.image",
    "video": "input.video",
    "document": "ingest",
    "data": "ingest",
    "text": "ingest",
}

# The slot on the source node that carries the file downstream.
SOURCE_SLOT: dict[str, str] = {
    "input.audio": "audio",
    "input.image": "image",
    "input.video": "video",
    "ingest": "sources",
}


@dataclass(frozen=True)
class Suggestion:
    """One offered next step: a node to spawn, wired to the dropped file where a wire applies."""

    node_type: str
    title: str
    why: str
    to_slot: str
    values: dict[str, str | int | float | bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type,
            "title": self.title,
            "why": self.why,
            "to_slot": self.to_slot,
            "values": dict(self.values),
        }


def probe_media(path: Path) -> dict[str, Any]:
    """Duration, rate, channels and size, as ffprobe reports them."""
    from content_factory.qc.media import ffprobe

    try:
        data = ffprobe(path)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    streams = data.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    facts: dict[str, Any] = {}
    duration = (data.get("format") or {}).get("duration")
    if duration:
        facts["duration_ms"] = int(float(duration) * 1000)
    if audio:
        facts["audio_codec"] = audio.get("codec_name")
        facts["sample_rate_hz"] = int(audio.get("sample_rate") or 0) or None
        facts["channels"] = audio.get("channels")
    if video:
        facts["video_codec"] = video.get("codec_name")
        facts["width"] = video.get("width")
        facts["height"] = video.get("height")
        rate = video.get("avg_frame_rate") or "0/0"
        num, _, den = rate.partition("/")
        if den and float(den) > 0 and float(num) > 0:
            facts["fps"] = round(float(num) / float(den), 3)
    return {k: v for k, v in facts.items() if v is not None}


def _audio_suggestions(facts: dict[str, Any]) -> list[Suggestion]:
    """What to offer for a dropped recording."""
    rate = int(facts.get("sample_rate_hz") or 0)
    channels = facts.get("channels")
    narrow = 0 < rate < 44_100
    band = (
        f"{rate // 1000} kHz{' mono' if channels == 1 else ''}: the repair chain's band extension"
        " can put the top octaves back before delivery"
        if narrow
        else "the take is already full-band, so the repair chain needs no band extension"
    )
    return [
        Suggestion(
            node_type="transcribe_audio",
            title="Read what it says",
            why=(
                "word-by-word timings off the recording — the step every audio lane starts with,"
                f" because the repair, the captions and the mix all work per beat. {band}"
            ),
            to_slot="audio",
            values={"engine": "faster_whisper", "model": "base.en"},
        ),
        Suggestion(
            node_type="qc_deliverable",
            title="Check it against delivery",
            why="integrated loudness, true peak and dead air, measured on the file that ships",
            to_slot="deliverable",
        ),
    ]


def _image_suggestions(facts: dict[str, Any]) -> list[Suggestion]:
    size = (
        f"{facts['width']}x{facts['height']}"
        if facts.get("width") and facts.get("height")
        else "this still"
    )
    return [
        Suggestion(
            node_type="generate_video",
            title="Bring it into motion",
            why=f"LTX-2.5 grows a shot out of {size} as its first frame",
            to_slot="first_frame",
        ),
        Suggestion(
            node_type="qc_deliverable",
            title="Check it against delivery",
            why="accessibility and delivery-promise checks on the image itself",
            to_slot="deliverable",
        ),
    ]


# Above this rate there is nothing to smooth: interpolating already-smooth footage costs a GPU
# pass and buys a slow-motion option, which is a different thing to want.
SMOOTH_ENOUGH_FPS = 48.0


def _video_suggestions(facts: dict[str, Any]) -> list[Suggestion]:
    fps = float(facts.get("fps") or 0)
    smooth_already = fps >= SMOOTH_ENOUGH_FPS
    interpolate = Suggestion(
        node_type="interpolate",
        title="Smooth the motion",
        why=(
            f"already {fps:g} fps — worth it only to get slow motion out of it"
            if smooth_already
            else f"{fps:g} fps in: GIMM-VFI fills between the frames"
            if fps
            else "GIMM-VFI fills between the frames"
        ),
        to_slot="frames",
        values={"engine": "gimm_vfi"},
    )
    return [
        # A screen recording arrives at 60 fps, so the first thing offered should not be the one
        # thing it does not need.
        *([] if smooth_already else [interpolate]),
        Suggestion(
            node_type="upscale_video",
            title="Restore and upscale",
            why="SeedVR2 7B, for footage that is soft or noisy rather than badly framed",
            to_slot="frames",
        ),
        Suggestion(
            node_type="fix_video",
            title="Remove something from it",
            why="Cutie tracks what you point at and ProPainter paints it out; a no-op until then",
            to_slot="frames",
        ),
        Suggestion(
            node_type="sound_design",
            title="Write sound for it",
            why="MMAudio watches the cut and lands effects on the frame",
            to_slot="video",
        ),
        *([interpolate] if smooth_already else []),
    ]


def _sources_suggestions(kind: str) -> list[Suggestion]:
    research = Suggestion(
        node_type="research",
        title="Read it as evidence",
        why="pulls evidence and claims out of the file, alongside anything else researched",
        to_slot="sources",
    )
    if kind == "data":
        return [
            Suggestion(
                node_type="compile_datasets",
                title="Turn it into a dataset",
                why=(
                    "reproducible Polars transforms into typed tables the charts cite; it reads"
                    " the file out of the run's uploads folder, so there is no wire to draw"
                ),
                to_slot="",
            ),
            research,
        ]
    return [research]


def suggestions_for(kind: str, facts: dict[str, Any]) -> list[Suggestion]:
    """Type-correct next steps for a dropped file of this kind."""
    if kind == "audio":
        return _audio_suggestions(facts)
    if kind == "image":
        return _image_suggestions(facts)
    if kind == "video":
        return _video_suggestions(facts)
    return _sources_suggestions(kind)


def describe(kind: str, facts: dict[str, Any]) -> str:
    """One line for the canvas: what this file is, in the operator's terms."""
    duration = facts.get("duration_ms")
    seconds = f"{duration / 1000:.1f} s" if duration else ""
    if kind == "audio":
        rate = int(facts.get("sample_rate_hz") or 0)
        parts = [p for p in (seconds, f"{rate // 1000} kHz" if rate else "") if p]
        channels = facts.get("channels")
        if channels:
            parts.append("mono" if channels == 1 else f"{channels} channels")
        return "audio" + (f" · {' · '.join(parts)}" if parts else "")
    if kind == "video":
        size = (
            f"{facts['width']}x{facts['height']}"
            if facts.get("width") and facts.get("height")
            else ""
        )
        fps = f"{facts['fps']} fps" if facts.get("fps") else ""
        parts = [p for p in (size, fps, seconds) if p]
        return "video" + (f" · {' · '.join(parts)}" if parts else "")
    if kind == "image":
        size = (
            f"{facts['width']}x{facts['height']}"
            if facts.get("width") and facts.get("height")
            else ""
        )
        return "image" + (f" · {size}" if size else "")
    return kind


def safe_name(raw: str | None) -> str:
    """A display name made only of characters the staged-upload contract accepts."""
    name = Path(raw or "dropped").name
    # ASCII only: `str.isalnum()` is Unicode-aware and the staged-upload contract's pattern is
    # not, so "ünïcode.wav" would pass here and be refused by the contract two calls later.
    cleaned = "".join(
        c if ((c.isascii() and c.isalnum()) or c in "._ -") else "-" for c in name
    ).strip(" -")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = f"file-{cleaned}".strip("-")
    return cleaned[:200] or "dropped"


def materialize(
    uploads_dir: Path, store: ArtifactStore, workspace_id: str, staged: StagedUpload
) -> Path:
    """Write one staged upload into a run's uploads folder."""
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / safe_name(staged.filename)
    if dest.is_file() and dest.stat().st_size == staged.size_bytes:
        return dest
    with store.open(workspace_id, staged.asset_id) as src, dest.open("wb") as out:
        shutil.copyfileobj(src, out, COPY_CHUNK_BYTES)
    return dest
