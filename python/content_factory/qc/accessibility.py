"""Accessibility pack (17): flashing/PSE detection, reading order, alt text, exportable report."""

from __future__ import annotations

import io
import itertools
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from PIL import Image

from content_factory.qc.media import Finding, QCResult, Severity
from content_factory.schemas.artboards import ArtboardSpec


def check_flashing(
    video: Path, *, fps: int, frames: int, max_flashes_per_second: float = 3.0
) -> QCResult:
    """WCAG 2.3.1-style general flash detection: count large opposing luminance swings."""
    sample = min(frames, fps * 10)
    out = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            f"select=lt(n\\,{sample}),scale=32:18",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    blobs = out.stdout.split(b"\x89PNG")[1:]
    lumas: list[float] = []
    for blob in blobs:
        img = Image.open(io.BytesIO(b"\x89PNG" + blob)).convert("L")
        hist = img.histogram()
        lumas.append(sum(i * c for i, c in enumerate(hist)) / sum(hist))
    swings = 0
    direction = 0
    for a, b in itertools.pairwise(lumas):
        delta = b - a
        if abs(delta) > 20:  # a "general flash"-scale luminance change on the downscaled frame
            new_dir = 1 if delta > 0 else -1
            if new_dir != direction:
                swings += 1
                direction = new_dir
    seconds = max(1e-6, len(lumas) / fps)
    rate = swings / 2 / seconds  # a flash = a pair of opposing transitions
    findings: list[Finding] = []
    if rate > max_flashes_per_second:
        findings.append(
            Finding(
                "flashing",
                Severity.blocker,
                f"{rate:.1f} flashes/s exceeds the {max_flashes_per_second}/s photosensitivity limit",  # noqa: E501
            )
        )
    return QCResult(
        tuple(findings), {"flash_rate_per_s": round(rate, 2), "frames_sampled": len(lumas)}
    )


def check_artboard_accessibility(artboard: ArtboardSpec) -> QCResult:
    findings: list[Finding] = []
    if not artboard.alt_text.strip():
        findings.append(Finding("alt_text", Severity.critical, "artboard has no alt text"))
    orders = sorted(layer.reading_order for layer in artboard.layers)
    if orders != list(range(len(orders))):
        findings.append(
            Finding("reading_order", Severity.critical, "reading order has gaps or duplicates")
        )
    for layer in artboard.layers:
        if layer.kind == "image" and not layer.alt_text.strip():
            findings.append(
                Finding(
                    "alt_text", Severity.critical, f"image layer {layer.layer_id} has no alt text"
                )
            )
    return QCResult(tuple(findings), {"layers": len(artboard.layers)})


def export_accessibility_report(
    out_path: Path, deliverable_id: str, results: dict[str, QCResult]
) -> dict:
    report = {
        "deliverable_id": deliverable_id,
        "passed": all(r.passed for r in results.values()),
        "checks": {
            name: {
                "passed": r.passed,
                "findings": [asdict(f) for f in r.findings],
                "facts": r.facts,
            }
            for name, r in results.items()
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    return report
