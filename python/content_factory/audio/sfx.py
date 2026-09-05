"""Video-synced sound effects (``sound_design`` stage).

MMAudio large 44k v2 is the operator's chosen foley model: it watches the finished picture and
writes sound that lands on the frame where the thing happens — footsteps under a walk cycle, cloth
when two hands meet — which is exactly what a generated clip lacks and what a music bed cannot
fake. It runs in the upstream checkout's own environment (``external/MMAudio/.venv``, torch 2.7.1
cu118) through a subprocess, like every other model in this repo, so the control plane keeps one
torch build and no GPU import.

MMAudio generates a fixed-length window at a time; a longer clip is generated window by window
against the matching slice of the picture and the pieces are concatenated, so a 60 s film gets
foley across its whole length instead of its first eight seconds.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from content_factory.audio.mix import ffmpeg

# Seam: unit tests replace this instead of running MMAudio.
SUBPROCESS_RUN: Callable[..., subprocess.CompletedProcess] = subprocess.run

REPO_ROOT = Path(__file__).resolve().parents[3]


class SfxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SfxRequest:
    video: Path
    duration_s: float
    prompt: str
    negative_prompt: str = ""
    seed: int = 7
    steps: int = 25
    window_s: float = 8.0
    timeout_s: int = 1800


class SfxBackend:
    name = "sfx"

    def generate(self, request: SfxRequest, out_wav: Path) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class MockSfxBackend(SfxBackend):
    """Deterministic, offline, silent-but-valid audio: the same shape MMAudio returns, so the mix
    and the QC downstream are exercised without a GPU."""

    name = "mock"

    def generate(self, request: SfxRequest, out_wav: Path) -> dict:
        import io
        import math
        import struct
        import wave

        rate = 44100
        n = max(1, int(request.duration_s * rate))
        seed = request.seed or 1
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            # A quiet, deterministic texture (two detuned tones) — audible in a mix check, never
            # mistaken for real foley.
            samples = [
                int(
                    900 * math.sin(2 * math.pi * (55 + seed % 7) * t / rate)
                    + 500 * math.sin(2 * math.pi * (83 + seed % 5) * t / rate)
                )
                for t in range(n)
            ]
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        out_wav.write_bytes(buf.getvalue())
        return {"backend": self.name, "windows": 1, "duration_s": round(request.duration_s, 3)}


class MMAudioBackend(SfxBackend):
    """MMAudio large 44k v2 in ``external/MMAudio/.venv``."""

    name = "mmaudio-large-44k-v2"

    def __init__(self, *, repo_dir: Path | None = None, variant: str = "large_44k_v2") -> None:
        self.repo_dir = repo_dir or Path(
            os.environ.get("CF_MMAUDIO_DIR", REPO_ROOT / "external" / "MMAudio")
        )
        self.variant = variant

    def interpreter(self) -> Path:
        env = os.environ.get("CF_MMAUDIO_PYTHON")
        if env:
            return Path(env)
        venv = self.repo_dir / ".venv" / "bin" / "python"
        if not venv.exists():
            raise SfxError(
                f"MMAudio has no environment at {venv}; create it in the checkout "
                f"({self.repo_dir}) or set CF_MMAUDIO_PYTHON"
            )
        return venv

    def _window(self, src: Path, start_s: float, length_s: float, dest: Path) -> None:
        ffmpeg(
            [
                "-ss",
                f"{start_s:.3f}",
                "-t",
                f"{length_s:.3f}",
                "-i",
                str(src),
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(dest),
            ],
            timeout=600,
        )

    def _run_once(
        self, request: SfxRequest, video: Path, length_s: float, seed: int, work: Path
    ) -> Path:
        out_dir = work / f"mmaudio_{seed}"
        proc = SUBPROCESS_RUN(
            [
                str(self.interpreter()),
                "demo.py",
                "--variant",
                self.variant,
                "--video",
                str(video.resolve()),
                "--prompt",
                request.prompt,
                "--negative_prompt",
                request.negative_prompt,
                "--duration",
                f"{length_s:.3f}",
                "--num_steps",
                str(request.steps),
                "--seed",
                str(seed),
                "--skip_video_composite",
                "--output",
                str(out_dir.resolve()),
            ],
            cwd=str(self.repo_dir),
            capture_output=True,
            text=True,
            timeout=request.timeout_s,
            check=False,
        )
        if proc.returncode != 0:
            raise SfxError(f"MMAudio failed (exit {proc.returncode}): {proc.stderr.strip()[-500:]}")
        flac = out_dir / f"{video.stem}.flac"
        if not flac.exists():
            found = sorted(out_dir.glob("*.flac")) if out_dir.is_dir() else []
            if not found:
                raise SfxError(f"MMAudio wrote no audio into {out_dir}")
            flac = found[0]
        return flac

    def generate(self, request: SfxRequest, out_wav: Path) -> dict:
        work = out_wav.parent / f".{out_wav.stem}.work"
        work.mkdir(parents=True, exist_ok=True)
        windows: list[Path] = []
        starts = []
        t = 0.0
        while t < request.duration_s - 0.05:
            starts.append((t, min(request.window_s, request.duration_s - t)))
            t += request.window_s
        for i, (start, length) in enumerate(starts):
            clip = work / f"win{i:03d}.mp4"
            self._window(request.video, start, length, clip)
            flac = self._run_once(request, clip, length, request.seed + i, work)
            wav = work / f"win{i:03d}.wav"
            ffmpeg(
                ["-i", str(flac), "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(wav)],
                timeout=600,
            )
            windows.append(wav)
        if not windows:
            raise SfxError("nothing to score: the video is shorter than 50 ms")
        concat(windows, out_wav)
        return {
            "backend": self.name,
            "windows": len(windows),
            "duration_s": round(request.duration_s, 3),
        }


def concat(wavs: list[Path], dest: Path) -> None:
    """Join same-format wavs end to end (ffmpeg concat demuxer)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(wavs) == 1:
        dest.write_bytes(wavs[0].read_bytes())
        return
    listing = dest.with_suffix(".concat.txt")
    listing.write_text("".join(f"file '{w.resolve()}'\n" for w in wavs))
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dest)], timeout=900)


def backend_for(name: str) -> SfxBackend:
    return MMAudioBackend() if name == "mmaudio" else MockSfxBackend()
