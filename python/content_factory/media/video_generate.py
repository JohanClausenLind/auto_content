"""`video.generate` media skill: image-to-video with interchangeable backends.

The same typed invocation runs a local ComfyUI workflow package (LTX-2.5 i2v on this hardware) or
a deterministic mock unchanged — mirroring `media/generate.py` for images. Generated clips are
editorial assets, never evidence; every result carries full provenance, and the container is
verified with ffprobe before anything downstream may consume it.
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from content_factory.comfyui.client import ComfyUIClient, ExecutionState
from content_factory.schemas.base import (
    OpaqueId,
    SchemaModel,
    Sha256Hex,
    canonical_dumps,
    sha256_hex,
)
from content_factory.schemas.comfyui import ComfyWorkflowPackage


class GuideFrame(SchemaModel):
    """A keyframe the clip must pass through: an anchor image pinned at ``frame_index``
    (LTXVAddGuide). The bytes travel separately; the digest ties them to the request."""

    frame_index: int = Field(ge=0)
    png_sha256: Sha256Hex
    strength: float = Field(default=1.0, ge=0.0, le=10.0)


class VideoGenerationRequest(SchemaModel):
    request_id: OpaqueId
    purpose: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=5000)
    width: int = Field(ge=64, le=3840, multiple_of=16)
    height: int = Field(ge=64, le=2160, multiple_of=16)
    duration_s: float = Field(ge=1.0, le=30.0)
    fps: int = Field(default=24, ge=8, le=60)
    seed: int = Field(ge=0)
    with_audio: bool = True
    guides: tuple[GuideFrame, ...] = ()


class VideoGenerationResult(SchemaModel):
    request_id: OpaqueId
    video_sha256: Sha256Hex
    width: int
    height: int
    duration_s: float
    has_audio: bool
    backend: str
    model_revision: str
    seed: int
    provenance: dict[str, Any]


class VideoGenerationError(Exception):
    pass


@dataclass(frozen=True)
class VideoOutput:
    result: VideoGenerationResult
    mp4: bytes


class VideoBackend(ABC):
    name: str

    @abstractmethod
    def generate(
        self,
        request: VideoGenerationRequest,
        first_frame_png: bytes | None,
        *,
        workdir: Path,
        guide_pngs: Mapping[int, bytes] | None = None,
        extra_files: Mapping[str, Path] | None = None,
    ) -> VideoOutput: ...

    def graph_fingerprint(self, guides: int = 0) -> dict[str, str]:
        """What this backend would run, apart from the parameters. Part of a clip's cache key.

        The key used to carry the backend *name* and a settings string, so editing a node in
        `ltx_packages.py` — a sampler, a step count, a node the graph wires differently — produced
        the identical key and the old clip was reused. That is the one cache mistake nobody
        notices, because the file is a plausible clip of the right length. The digest is over the
        **un-parameterised** graph, so changing a prompt or a seed is still handled by the rest of
        the key rather than by invalidating every clip in the film.
        """
        return {"backend": self.name}


def probe(mp4: Path) -> dict[str, Any]:
    """qc.media.ffprobe is the one ffprobe call site; this wraps its failure into the
    skill's typed error so generation and QC can never disagree on how a container is read."""
    from content_factory.qc.media import ffprobe

    try:
        return ffprobe(mp4)
    except subprocess.CalledProcessError as err:
        raise VideoGenerationError(f"ffprobe failed: {(err.stderr or '')[-500:]}") from err


def run_video_skill(
    request: VideoGenerationRequest,
    backend: VideoBackend,
    *,
    workdir: Path,
    first_frame_png: bytes | None = None,
    guide_pngs: Mapping[int, bytes] | None = None,
    extra_files: Mapping[str, Path] | None = None,
) -> VideoOutput:
    """The single invocation path: verify the guide bytes → execute → verify the container →
    verify provenance. ``extra_files`` are package parameters bound to uploaded files (e.g. the
    Wan pose video)."""
    guide_pngs = dict(guide_pngs or {})
    for guide in request.guides:
        png = guide_pngs.get(guide.frame_index)
        if png is None:
            raise VideoGenerationError(f"guide frame {guide.frame_index} has no image bytes")
        if sha256_hex(png) != guide.png_sha256:
            raise VideoGenerationError(
                f"guide frame {guide.frame_index} bytes do not match its digest"
            )
    for name, path in (extra_files or {}).items():
        if not Path(path).exists():
            raise VideoGenerationError(f"extra file for {name!r} is missing: {path}")
    out = backend.generate(
        request, first_frame_png, workdir=workdir, guide_pngs=guide_pngs, extra_files=extra_files
    )
    if out.result.video_sha256 != sha256_hex(out.mp4):
        raise VideoGenerationError("provenance hash does not match the returned bytes")
    workdir.mkdir(parents=True, exist_ok=True)
    check = workdir / f"{request.request_id}.verify.mp4"
    check.write_bytes(out.mp4)
    info = probe(check)
    streams = info.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not video:
        raise VideoGenerationError(f"{backend.name} returned a container with no video stream")
    got = (int(video[0].get("width", 0)), int(video[0].get("height", 0)))
    if got != (request.width, request.height):
        raise VideoGenerationError(
            f"{backend.name} returned {got[0]}x{got[1]}, requested {request.width}x{request.height}"
        )
    duration = float(info.get("format", {}).get("duration", 0.0))
    if abs(duration - request.duration_s) > max(0.5, request.duration_s * 0.15):
        raise VideoGenerationError(
            f"{backend.name} returned {duration:.2f}s, requested {request.duration_s:.2f}s"
        )
    if out.result.has_audio != bool(audio):
        raise VideoGenerationError("provenance audio flag does not match the container")
    return out


class ComfyUIVideoBackend(VideoBackend):
    """Image-to-video through allowlisted workflow packages on the local ComfyUI. ``package`` is
    the plain i2v graph; ``guided_packages[n]`` is the variant with ``n`` LTXVAddGuide keyframes,
    picked by the number of guides on the request."""

    name = "comfyui"

    def __init__(
        self,
        endpoint: str,
        package: ComfyWorkflowPackage,
        *,
        guided_packages: Mapping[int, ComfyWorkflowPackage] | None = None,
        param_map: dict[str, str] | None = None,
        collect: Literal["history", "websocket"] = "history",
        timeout_s: float = 1800.0,
        snap_length_to_8k1: bool = True,
        length_rule: Literal["8k+1", "4k+1", "none"] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.package = package
        self.guided_packages = dict(guided_packages or {})
        self.collect: Literal["history", "websocket"] = collect
        self.timeout_s = timeout_s
        self.snap_length_to_8k1 = snap_length_to_8k1
        # LTX generates 8k+1 frames, Wan 4k+1; "none" passes the requested length through.
        self.length_rule: str = length_rule or ("8k+1" if snap_length_to_8k1 else "none")
        # request field -> package parameter name; frame length is derived, not a request field.
        self.param_map = param_map or {
            "prompt": "prompt",
            "width": "width",
            "height": "height",
            "seed": "seed",
        }

    def graph_fingerprint(self, guides: int = 0) -> dict[str, str]:
        package = self._package_for(guides)
        return {
            "backend": self.name,
            "package_id": package.package_id,
            "package_version": str(package.version),
            # The graph as the package declares it: node types, wiring and every default. The
            # parameters are injected later and are already in the key.
            "graph_sha256": hashlib.sha256(
                canonical_dumps(package.api_workflow).encode()
            ).hexdigest()[:32],
        }

    def _package_for(self, guides: int) -> ComfyWorkflowPackage:
        if guides == 0:
            return self.package
        pkg = self.guided_packages.get(guides)
        if pkg is None:
            have = sorted(self.guided_packages)
            raise VideoGenerationError(
                f"no workflow package for {guides} guide frame(s); have {have}"
            )
        return pkg

    def generate(
        self,
        request: VideoGenerationRequest,
        first_frame_png: bytes | None,
        *,
        workdir: Path,
        guide_pngs: Mapping[int, bytes] | None = None,
        extra_files: Mapping[str, Path] | None = None,
    ) -> VideoOutput:
        if first_frame_png is None:
            raise VideoGenerationError("image-to-video needs a first frame")
        guide_pngs = dict(guide_pngs or {})
        extra_files = dict(extra_files or {})
        package = self._package_for(len(request.guides))
        params: dict[str, int | float | str] = {}
        for field, param in self.param_map.items():
            params[param] = getattr(request, field)
        length = round(request.duration_s * request.fps)
        if self.length_rule == "8k+1":
            from content_factory.media.ltx_packages import snap_length

            length = snap_length(length)
        elif self.length_rule == "4k+1":
            from content_factory.media.wan_packages import snap_length_4k1

            length = snap_length_4k1(length)
        params["length"] = length

        async def _run():
            client = ComfyUIClient(self.endpoint, client_id=f"video-{request.request_id}")
            try:
                frame_path = workdir / f"{request.request_id}_first_frame.png"
                frame_path.parent.mkdir(parents=True, exist_ok=True)
                frame_path.write_bytes(first_frame_png)
                params["first_frame"] = await client.upload_image(frame_path)
                for i, guide in enumerate(request.guides, start=1):
                    from content_factory.media.ltx_packages import guide_param_names

                    img, idx, strength = guide_param_names(i)
                    guide_path = workdir / f"{request.request_id}_guide_{i}.png"
                    guide_path.write_bytes(guide_pngs[guide.frame_index])
                    params[img] = await client.upload_image(guide_path)
                    params[idx] = min(guide.frame_index, length - 1)
                    params[strength] = guide.strength
                for name, path in extra_files.items():
                    params[name] = await client.upload_image(Path(path))
                return await client.run_package(
                    package,
                    params,
                    output_dir=workdir / "comfy",
                    collect=self.collect,
                    timeout_s=self.timeout_s,
                    # A generation is forty seconds of exclusive GPU on the LTX GGUF stack, so a
                    # process that died after submitting must not queue a second one. The journal
                    # sits beside the clip and is what a rerun reconciles against.
                    journal=workdir / "submitted.json",
                )
            finally:
                await client.aclose()

        result = asyncio.run(_run())
        if result.state != ExecutionState.completed or not result.outputs:
            raise VideoGenerationError(f"comfyui run {result.state}: {result.error}")
        mp4 = result.outputs[0].local_path.read_bytes()
        assert result.provenance is not None
        return VideoOutput(
            result=VideoGenerationResult(
                request_id=request.request_id,
                video_sha256=sha256_hex(mp4),
                width=request.width,
                height=request.height,
                duration_s=round(length / request.fps, 3),
                has_audio=request.with_audio,
                backend=self.name,
                model_revision=f"{package.package_id}@{package.version}",
                seed=request.seed,
                provenance={
                    "prompt_id": result.provenance.prompt_id,
                    "workflow_sha256": result.provenance.injected_workflow_sha256,
                    "purpose": request.purpose,
                    "prompt": request.prompt,
                    "length": length,
                    "guides": [g.model_dump(mode="json") for g in request.guides],
                },
            ),
            mp4=mp4,
        )


class MockVideoBackend(VideoBackend):
    """Deterministic offline stand-in: a seed-coloured clip (with a quiet tone when audio is
    requested) synthesised by ffmpeg. Same shape, same verification path, zero models."""

    name = "mock"

    def generate(
        self,
        request: VideoGenerationRequest,
        first_frame_png: bytes | None,
        *,
        workdir: Path,
        guide_pngs: Mapping[int, bytes] | None = None,
        extra_files: Mapping[str, Path] | None = None,
    ) -> VideoOutput:
        workdir.mkdir(parents=True, exist_ok=True)
        out = workdir / f"{request.request_id}.mp4"
        color = f"0x{(request.seed * 2654435761) % 0xFFFFFF:06X}"
        args = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={request.width}x{request.height}:r={request.fps}:d={request.duration_s}",
        ]
        if request.with_audio:
            args += ["-f", "lavfi", "-i", f"sine=frequency=220:duration={request.duration_s}"]
        args += ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"]
        if request.with_audio:
            args += ["-c:a", "aac", "-shortest"]
        args += [str(out)]
        proc = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603
        if proc.returncode != 0:
            raise VideoGenerationError(f"ffmpeg failed: {proc.stderr[-500:]}")
        mp4 = out.read_bytes()
        return VideoOutput(
            result=VideoGenerationResult(
                request_id=request.request_id,
                video_sha256=sha256_hex(mp4),
                width=request.width,
                height=request.height,
                duration_s=request.duration_s,
                has_audio=request.with_audio,
                backend=self.name,
                model_revision="mock-0.1",
                seed=request.seed,
                provenance={
                    "purpose": request.purpose,
                    "prompt": request.prompt,
                    "first_frame": first_frame_png is not None,
                    "guides": [g.model_dump(mode="json") for g in request.guides],
                    "extra_files": sorted(extra_files or {}),
                },
            ),
            mp4=mp4,
        )
