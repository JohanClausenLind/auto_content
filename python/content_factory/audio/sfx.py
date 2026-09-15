"""Video-synced sound effects (``sound_design`` stage)."""

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
    crossfade_s: float = 1.25
    """How much each window overlaps the one before it, and is blended across.

    Zero reproduces the butt-joint this replaces. MMAudio scores one window at a time and each
    window is an independent take: the level it settles at, the timbre it picks, and the taper it
    applies to its own tail are all unrelated to its neighbour's. Joined end to end that is an
    audible seam every ``window_s`` -- measured 2026-09-13 on a 10 s clifftop bed, a hard spectral
    step at 8.02 s and an envelope range of 43.1 dB (LRA 20.4) where an ambience bed should sit
    near 3-8. Overlapping and cross-fading puts each window's fading tail under the next window's
    strong head, which is where the pulsing goes."""
    flatten_windows: bool = True
    """Divide each window by its own slow envelope before blending.

    A crossfade fixes the *joint*; this fixes the window. MMAudio's output is not level across its
    own eight seconds -- it opens bright and decays -- so a long clip stitched from nine of them is
    a sawtooth with an eight-second period whatever the joins do. Measured 2026-09-13 over the full
    minute: an envelope range of 55.5 dB, and a spectrogram with nine visible ramps in it. Only the
    slow shape is removed (half-second blocks, and the correction is capped), so gusts, waves and
    footsteps survive -- what goes is the ramp the model puts on every window regardless of what it
    is looking at."""
    flatten_max_db: float = 9.0
    """Ceiling on that correction, so a genuinely quiet passage is not dragged up to meet a loud
    one. Without it the flattening turns a held silence into amplified room noise."""
    match_levels: bool = True
    """Bring every window to the median window's RMS before blending.

    A crossfade hides a seam between two windows of the same loudness; between windows 6 dB apart
    it just makes the step take a second. The median rather than the mean, so one loud window does
    not drag the whole bed."""
    timeout_s: int = 1800


class SfxBackend:
    name = "sfx"

    def generate(self, request: SfxRequest, out_wav: Path) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class MockSfxBackend(SfxBackend):
    """Deterministic silent-but-valid audio in MMAudio's shape, so mix and QC run without a GPU."""

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
        # Windows OVERLAP by `crossfade_s`, so the hop is shorter than the window. The last one is
        # pulled back to end exactly on the clip rather than running past it.
        hop = max(0.25, request.window_s - max(0.0, request.crossfade_s))
        t = 0.0
        while t < request.duration_s - 0.05:
            length = min(request.window_s, request.duration_s - t)
            starts.append((t, length))
            if t + length >= request.duration_s - 0.05:
                break
            t += hop
        for i, (start, length) in enumerate(starts):
            clip = work / f"win{i:03d}.mp4"
            self._window(request.video, start, length, clip)
            # One seed for every window, not `seed + i`: the seed fixes the character of the take,
            # and changing it per window made one continuous shot sound like spliced recordings.
            flac = self._run_once(request, clip, length, request.seed, work)
            wav = work / f"win{i:03d}.wav"
            ffmpeg(
                ["-i", str(flac), "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(wav)],
                timeout=600,
            )
            windows.append(wav)
        if not windows:
            raise SfxError("nothing to score: the video is shorter than 50 ms")
        stitch(
            windows,
            out_wav,
            crossfade_s=request.crossfade_s,
            match_levels=request.match_levels,
            flatten=request.flatten_windows,
            flatten_max_db=request.flatten_max_db,
        )
        return {
            "backend": self.name,
            "windows": len(windows),
            "crossfade_s": request.crossfade_s,
            "duration_s": round(request.duration_s, 3),
        }


def _read_wav(path: Path):
    import wave

    import numpy as np

    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0, rate


def _write_wav(samples, rate: int, dest: Path) -> None:
    import wave

    import numpy as np

    clipped = np.clip(samples, -1.0, 1.0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((clipped * 32767.0).astype("<i2").tobytes())


def flatten_envelope(samples, rate: int, *, block_s: float = 0.5, max_db: float = 9.0):
    """Remove a window's own slow level ramp, keeping everything faster than ``block_s``."""
    import numpy as np

    block = max(1, int(block_s * rate))
    if len(samples) < block * 3:
        return samples
    blocks = len(samples) // block
    trimmed = samples[: blocks * block].reshape(blocks, block)
    rms = np.sqrt((trimmed**2).mean(axis=1))
    reference = float(np.median(rms))
    if reference <= 1e-6:
        return samples
    # smooth the per-block gains so the correction itself has no steps in it
    gains = reference / np.maximum(rms, 1e-6)
    ceiling = 10 ** (max_db / 20.0)
    gains = np.clip(gains, 1.0 / ceiling, ceiling)
    kernel = np.ones(3) / 3.0
    gains = np.convolve(gains, kernel, mode="same")
    curve = np.interp(
        np.arange(len(samples)),
        np.linspace(0, len(samples) - 1, len(gains)),
        gains,
    )
    return samples * curve


def stitch(
    wavs: list[Path],
    dest: Path,
    *,
    crossfade_s: float = 1.25,
    match_levels: bool = True,
    flatten: bool = True,
    flatten_max_db: float = 9.0,
) -> None:
    """Overlap-add windows into one continuous bed."""
    import numpy as np

    if not wavs:
        raise SfxError("nothing to stitch")
    if len(wavs) == 1:
        dest.write_bytes(wavs[0].read_bytes())
        return
    tracks = [_read_wav(w) for w in wavs]
    rate = tracks[0][1]
    if any(r != rate for _, r in tracks):
        raise SfxError("windows disagree about sample rate")
    clips = [samples for samples, _ in tracks]
    if flatten:
        clips = [flatten_envelope(c, rate, max_db=flatten_max_db) for c in clips]
    if match_levels:
        levels = [float(np.sqrt((c**2).mean())) or 1e-9 for c in clips]
        target = float(np.median(levels))
        clips = [c * (target / lvl) for c, lvl in zip(clips, levels, strict=True)]
    overlap = int(max(0.0, crossfade_s) * rate)
    if overlap <= 0:
        _write_wav(np.concatenate(clips), rate, dest)
        return
    out = clips[0].copy()
    for clip in clips[1:]:
        n = min(overlap, len(out), len(clip))
        if n <= 0:
            out = np.concatenate([out, clip])
            continue
        ramp = np.linspace(0.0, 1.0, n, endpoint=False)
        fade_out, fade_in = np.sqrt(1.0 - ramp), np.sqrt(ramp)
        blended = out[-n:] * fade_out + clip[:n] * fade_in
        out = np.concatenate([out[:-n], blended, clip[n:]])
    _write_wav(out, rate, dest)


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
