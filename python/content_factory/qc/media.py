"""Post-render deterministic QC (section 17): ffprobe/PIL assertions, only the relevant checks.

Static outputs never fail for having no FPS; video outputs are checked for codec, dimensions,
fps, duration/frame count, pixel format, fast start, black/frozen frames.
"""

from __future__ import annotations

import io
import json
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from PIL import Image


class Severity(StrEnum):
    blocker = "blocker"
    critical = "critical"
    major = "major"
    minor = "minor"
    advisory = "advisory"


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    message: str


@dataclass(frozen=True)
class QCResult:
    findings: tuple[Finding, ...]
    facts: dict[str, object]

    @property
    def passed(self) -> bool:
        return not any(f.severity in {Severity.blocker, Severity.critical} for f in self.findings)


def ffprobe(path: Path) -> dict:
    out = subprocess.run(  # noqa: S603
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def moov_before_mdat(path: Path) -> bool:
    data = path.read_bytes()
    moov, mdat = data.find(b"moov"), data.find(b"mdat")
    return moov >= 0 and mdat >= 0 and moov < mdat


def check_video(
    path: Path,
    *,
    width: int,
    height: int,
    fps: int,
    frames: int,
    codec: str = "h264",
    pix_fmt: str = "yuv420p",
) -> QCResult:
    findings: list[Finding] = []
    info = ffprobe(path)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        return QCResult((Finding("stream", Severity.blocker, "no video stream"),), {})
    facts: dict[str, object] = {
        "codec": video.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "r_frame_rate": video.get("r_frame_rate"),
        "nb_frames": int(video.get("nb_frames") or 0),
        "pix_fmt": video.get("pix_fmt"),
        "duration_s": float(info["format"].get("duration") or 0),
        "faststart": moov_before_mdat(path),
    }
    if facts["codec"] != codec:
        findings.append(
            Finding("codec", Severity.critical, f"expected {codec}, got {facts['codec']}")
        )
    if (facts["width"], facts["height"]) != (width, height):
        findings.append(
            Finding(
                "dimensions",
                Severity.critical,
                f"expected {width}x{height}, got {facts['width']}x{facts['height']}",
            )
        )
    if facts["r_frame_rate"] != f"{fps}/1":
        findings.append(
            Finding("fps", Severity.critical, f"expected {fps}/1, got {facts['r_frame_rate']}")
        )
    if facts["nb_frames"] != frames:
        findings.append(
            Finding(
                "frames", Severity.critical, f"expected {frames} frames, got {facts['nb_frames']}"
            )
        )
    if facts["pix_fmt"] != pix_fmt:
        findings.append(
            Finding("pix_fmt", Severity.major, f"expected {pix_fmt}, got {facts['pix_fmt']}")
        )
    if not facts["faststart"]:
        findings.append(Finding("faststart", Severity.major, "moov atom is not before mdat"))
    findings.extend(_black_or_frozen(path, fps, frames))
    return QCResult(tuple(findings), facts)


def _black_or_frozen(path: Path, fps: int, frames: int) -> list[Finding]:
    """Sample a few frames; flag fully black frames and long identical runs."""
    findings: list[Finding] = []
    sample_idx = sorted({0, frames // 4, frames // 2, (3 * frames) // 4, frames - 1})
    hashes: list[bytes] = []
    for idx in sample_idx:
        out = subprocess.run(  # noqa: S603
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-vf",
                f"select=eq(n\\,{idx})",
                "-vframes",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "-",
            ],
            capture_output=True,
            check=False,
        )
        if out.returncode != 0 or not out.stdout:
            findings.append(Finding("decode", Severity.critical, f"could not decode frame {idx}"))
            continue
        img = Image.open(io.BytesIO(out.stdout)).convert("L")
        small = img.resize((32, 32))
        extrema = cast(tuple[int, int], small.getextrema())
        if extrema[1] < 8:
            findings.append(Finding("black_frame", Severity.major, f"frame {idx} is black"))
        hashes.append(small.tobytes())
    if len(hashes) >= 3 and len(set(hashes)) == 1:
        findings.append(Finding("frozen", Severity.major, "all sampled frames are identical"))
    return findings


def check_still(path: Path, *, width: int, height: int) -> QCResult:
    findings: list[Finding] = []
    with Image.open(path) as img:
        img.verify()
    with Image.open(path) as img:
        facts: dict[str, object] = {
            "format": img.format,
            "mode": img.mode,
            "width": img.width,
            "height": img.height,
        }
        if img.format != "PNG":
            findings.append(Finding("format", Severity.critical, f"expected PNG, got {img.format}"))
        if (img.width, img.height) != (width, height):
            findings.append(
                Finding(
                    "dimensions",
                    Severity.critical,
                    f"expected {width}x{height}, got {img.width}x{img.height}",
                )
            )
        grey = img.convert("L").resize((32, 32))
        lo, hi = cast(tuple[int, int], grey.getextrema())
        if hi - lo < 16:
            findings.append(
                Finding("blank", Severity.critical, "image has almost no contrast (blank render?)")
            )
    return QCResult(tuple(findings), facts)
