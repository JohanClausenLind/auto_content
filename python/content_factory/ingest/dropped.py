"""What a dropped file is, and what the canvas should do with it.

The browser half of this is a drop target; everything that requires knowing something — what the
file actually is, which node can hold it, and what a sensible next step would be — is decided
here, because the answers come from the pipeline's own stages and from measurements of the file
rather than from its name.

Three steps, in order:

* **Identity** comes from :mod:`content_factory.ingest.uploads`: the MIME is sniffed from the
  bytes, extensions from the dangerous set are refused whatever the sniff says, and anything not
  on the allowlist is rejected. Nothing below is reached by a file that failed that.
* **Facts** come from ffprobe — duration, sample rate, channels, resolution, frame rate. They are
  what makes a suggestion specific instead of generic: a 16 kHz mono take needs band extension
  and a 48 kHz one does not, and only measuring the file can tell which is on the table.
* **Suggestions** are type-correct by construction. Each one names a node type, the slot on that
  node the source connects to, and the reason, so the canvas can spawn it already wired — and a
  test asserts every slot named here exists in the node catalogue with a compatible type, because
  a suggestion that produces a refused link is worse than no suggestion.
"""

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

# Which node holds a dropped file of each kind. Data and documents have no node of their own on
# purpose: `ingest` already turns every file in the uploads folder into typed sources, which is
# exactly what a CSV or a PDF is for here, and inventing a second door for them would mean two
# code paths for one thing.
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
    """One offered next step: a node to spawn, wired to the dropped file where a wire applies.

    ``to_slot`` empty means the node takes nothing from the source node because it reads the
    run's uploads folder itself — which is true of the stages built around uploaded data. The
    canvas spawns those unwired rather than drawing a link the graph would refuse.
    """

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
    """Duration, rate, channels and size, as ffprobe reports them. ``{}`` when it cannot say.

    Deliberately total: a file ffprobe refuses is still a file the operator dropped, and the
    caller has already established what it is from its bytes. No facts means unspecific
    suggestions, not an error.
    """
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
    rate = int(facts.get("sample_rate_hz") or 0)
    channels = facts.get("channels")
    narrow = 0 < rate < 44_100
    why_clean = (
        f"{rate // 1000} kHz{' mono' if channels == 1 else ''}: band extension puts the top"
        " octaves back before delivery, and the restorer takes the room off"
        if narrow
        else "cleanup and restoration; the take is already full-band, so no band extension"
    )
    return [
        Suggestion(
            node_type="restore_speech",
            title="Clean the voice",
            why=why_clean,
            to_slot="audio",
            values={
                "cleanup": "clearervoice",
                "band_extension": "clearervoice_sr" if narrow else "off",
                "enhancer": "resemble_enhance",
                # The operator dropped a clip and asked for it to be cleaned: the chain should
                # not second-guess that by measuring the take and deciding it sounds fine.
                "gate": "always",
                "device": "cuda",
            },
        ),
        Suggestion(
            node_type="mix_audio",
            title="Master the loudness",
            why="two-pass loudnorm to -16 LUFS, the spoken-word target rather than the video one",
            to_slot="audio",
            values={"target_lufs": -16.0},
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
    """A display name made only of characters the staged-upload contract accepts.

    The name is never how a file is identified — that is the sniffed kind and the content hash —
    so anything unusable here is replaced rather than argued with.
    """
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
    """Write one staged upload into a run's uploads folder. Idempotent by name and size.

    Called by the production workflow's setup activity rather than by a request: the project
    directory does not exist until the run starts, which is why the campaign carries the staged
    reference instead of the browser writing a file somewhere itself.
    """
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / safe_name(staged.filename)
    if dest.is_file() and dest.stat().st_size == staged.size_bytes:
        return dest
    with store.open(workspace_id, staged.asset_id) as src, dest.open("wb") as out:
        shutil.copyfileobj(src, out, COPY_CHUNK_BYTES)
    return dest
