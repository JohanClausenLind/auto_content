"""MiniMax-Music3 through the HOT-Step engine: server lifecycle, HTTP client, music QC.

The weights in `models/music/MiniMax-Music3-GGUF` are not usable with llama.cpp or ComfyUI-GGUF --
music generation needs the full five-module pipeline (LM -> RVQ depth decoder -> condition encoder
-> flow-matching DiT -> vocoder), which only the HOT-Step engine implements. So this module does no
inference itself: it starts `ace-server` against the weights and drives `POST /mm3/synth`.

The DSP and loudness machinery is *imported from the sfx skill* rather than copied. Both libraries
land in the same asset tree at the same level with the same measurements, and that code has already
been audited; a second implementation would only be a second thing to keep right. Importing it
costs nothing extra here — `sfx.py` imports numpy at module level and torch only inside
`load_model()`, which this module never calls.

Rationale and sources: docs/research/2026-09-07-background-music-library.md
"""

from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]  # skills/audio/music/mm3.py -> repo root
sys.path.insert(0, str(REPO_ROOT / "skills" / "audio" / "sfx"))
import sfx  # noqa: E402  (path-dependent: see the module docstring)

DEFAULT_ENGINE = REPO_ROOT / "external" / "HOT-Step-CPP" / "engine" / "build" / "ace-server"
DEFAULT_MODELS = REPO_ROOT / "models" / "music" / "MiniMax-Music3-GGUF"
MODEL_ID = "MiniMaxAI/MiniMax-Music3 (GGUF, q8_0 LM/DiT/depth + f16 cond/voc)"
SAMPLE_RATE = 44100
DEFAULT_PORT = 8137
FRAME_RATE = 25  # mm3 audio frames per second
MAX_FRAMES = 9000  # engine cap -> 360 s


# --------------------------------------------------------------------------- server


def _get(url: str, timeout: float = 10.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (localhost only)
        return r.read()


def _post_json(url: str, payload: dict, timeout: float = 60.0) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(  # noqa: S310 (fixed http://127.0.0.1 URLs, built here)
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read())


def server_up(port: int) -> bool:
    try:
        return json.loads(_get(f"http://127.0.0.1:{port}/health", 2.0)).get("status") == "ok"
    except Exception:
        return False


def start_server(
    port: int = DEFAULT_PORT,
    models: Path | None = None,
    engine: Path | None = None,
    log: Path | None = None,
    wait_s: float = 300.0,
) -> subprocess.Popen | None:
    """Start ace-server if it is not already answering. Returns the process we started, or None.

    Returning None for "already running" is what lets the caller leave a server it did not start
    alone: a 12 GB model load is not something to tear down on someone else's behalf.
    """
    if server_up(port):
        return None
    exe = Path(os.environ.get("CF_MM3_ENGINE", engine or DEFAULT_ENGINE))
    mdir = Path(os.environ.get("CF_MM3_MODELS", models or DEFAULT_MODELS))
    if not exe.is_file():
        raise SystemExit(
            f"HOT-Step engine not built at {exe}\n"
            f"  cd {REPO_ROOT}/external/HOT-Step-CPP/engine && ./buildcuda.sh   (or buildcpu.sh)"
        )
    if not any(mdir.glob("mm3-*.gguf")) and not (mdir / "mm3").is_dir():
        raise SystemExit(f"no mm3-*.gguf under {mdir}")

    log = log or Path(os.environ.get("TMPDIR", "/tmp")) / f"mm3-server-{port}.log"  # noqa: S108
    fh = log.open("ab")

    # libggml-cuda.so records RUNPATH $ORIGIN only, so the CUDA runtime libs have to be found
    # through the environment. CUDA_ROOT is the repo-local toolkit the engine was built against
    # (see this skill's README); a system toolkit on the default loader path needs nothing here.
    env = dict(os.environ)
    cuda_lib = REPO_ROOT / ".venvs" / "cuda-12.6" / "root" / "lib64"
    if cuda_lib.is_dir():
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            [str(cuda_lib)] + ([env["LD_LIBRARY_PATH"]] if env.get("LD_LIBRARY_PATH") else [])
        )
    proc = subprocess.Popen(
        [str(exe), "--models", str(mdir), "--port", str(port), "--keep-loaded"],
        stdout=fh,
        stderr=fh,
        env=env,
    )
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"ace-server exited with {proc.returncode}; see {log}")
        if server_up(port):
            return proc
        time.sleep(1.0)
    proc.terminate()
    raise SystemExit(f"ace-server did not become ready within {wait_s:.0f}s; see {log}")


def props(port: int = DEFAULT_PORT) -> dict:
    return json.loads(_get(f"http://127.0.0.1:{port}/mm3/props", 20.0))


# --------------------------------------------------------------------------- generate


def generate(
    caption: str,
    duration_s: float,
    seed: int,
    port: int = DEFAULT_PORT,
    steps: int = 30,
    cfg_flow: float = 1.7,
    wav_bits: int = 24,
    instrumental: bool = True,
    poll_s: float = 5.0,
    timeout_s: float = 3600.0,
    on_progress=None,
) -> tuple[np.ndarray, dict]:
    """Render one track. Returns (float64 audio shaped (channels, samples), job info).

    `instrumental=True` is not a hint to the caption: the engine substitutes the literal
    `[instrumental]` structure tag for the lyrics in the prompt it assembles, and skips LRC
    alignment capture entirely (mm3-align.h, mm3-job.h).
    """
    if duration_s * FRAME_RATE > MAX_FRAMES:
        raise ValueError(f"{duration_s}s exceeds the engine cap of {MAX_FRAMES / FRAME_RATE:.0f}s")
    base = f"http://127.0.0.1:{port}"
    job = _post_json(
        f"{base}/mm3/synth",
        {
            "caption": caption,
            "lyrics": "",
            "instrumental": instrumental,
            "duration": float(duration_s),
            "seed": int(seed),
            "steps": int(steps),
            "cfg_flow": float(cfg_flow),
            "get_wav_bits": int(wav_bits),
        },
    )
    jid = job["job_id"]

    deadline = time.time() + timeout_s
    info: dict = {}
    while time.time() < deadline:
        info = json.loads(_get(f"{base}/mm3/job?id={jid}", 20.0))
        status = info.get("status")
        if status == "done":
            break
        if status in ("failed", "error", "cancelled"):
            raise RuntimeError(f"mm3 job {jid} {status}: {info.get('error', '')}")
        if on_progress:
            on_progress(info)
        time.sleep(poll_s)
    else:
        raise TimeoutError(f"mm3 job {jid} still {info.get('status')} after {timeout_s:.0f}s")

    import soundfile as sf

    wav = _get(f"{base}/mm3/take?id={jid}&take=0", 300.0)
    data, sr = sf.read(io.BytesIO(wav), always_2d=True, dtype="float64")
    if sr != SAMPLE_RATE:
        raise RuntimeError(f"expected {SAMPLE_RATE} Hz from the engine, got {sr}")
    return data.T, {**job, **info}


# --------------------------------------------------------------------------- music dsp / qc


def trim_head(
    x: np.ndarray, sr: int, floor_db: float = -55.0, keep_ms: float = 120.0
) -> np.ndarray:
    """Drop dead air before the first note. The model often starts a beat or two late."""
    env = np.max(np.abs(x), axis=0)
    peak = float(env.max())
    if peak <= 0:
        return x
    above = np.flatnonzero(env > peak * (10.0 ** (floor_db / 20.0)))
    if above.size == 0:
        return x
    return x[:, max(0, above[0] - int(sr * keep_ms / 1000)) :]


def fade(x: np.ndarray, sr: int, in_s: float, out_s: float) -> np.ndarray:
    """Equal-gain fades. A library bed has to be safe to drop on a timeline anywhere."""
    y = x.copy()
    n = y.shape[-1]
    fi, fo = min(int(in_s * sr), n // 2), min(int(out_s * sr), n // 2)
    if fi > 1:
        y[:, :fi] *= np.linspace(0.0, 1.0, fi)
    if fo > 1:
        y[:, -fo:] *= np.linspace(1.0, 0.0, fo)
    return y


def presence_ratio(x: np.ndarray, sr: int) -> float:
    """Share of total energy in 1-4 kHz -- the band a narrator competes for.

    This is the one measurement that says whether a bed will fight the voice. It is a *ratio*, so
    it describes something turning the track down cannot fix: ducking moves the whole spectrum, it
    does not change how much of the track lives where the words live.
    """
    f, p = sfx.spectrum(x, sr)
    total = float(p[(f >= 20) & (f <= 20000)].sum()) or 1e-20
    return round(float(p[(f >= 1000) & (f <= 4000)].sum() / total), 4)


def slow_envelope_range_db(x: np.ndarray, sr: int, win_s: float = 10.0) -> float:
    """p95 - p5 of a 10-second moving RMS: does the track actually travel?

    EBU LRA is the wrong tool for this question on this material. A felt piano with long gaps of
    near-silence between chords measured LRA 25.7 LU while being perfectly even in level -- LRA sees
    the gaps, which is what it is for. Smoothing over ten seconds throws the gaps away and leaves
    the shape: a bed that holds one level reads near zero however sparse it is, and one that builds
    and recedes reads high.
    """
    mono = x.mean(axis=0)
    b = int(1.0 * sr)
    nb = max(mono.size // b, 1)
    sec = (mono[: nb * b].reshape(nb, b) ** 2).mean(axis=1)
    w = max(1, min(int(win_s), nb))
    smooth = np.convolve(sec, np.ones(w) / w, mode="valid")
    db = 10 * np.log10(np.maximum(smooth, 1e-12))
    return round(float(np.percentile(db, 95) - np.percentile(db, 5)), 2)


def best_loop_window(
    body: np.ndarray,
    sr: int,
    length_s: float,
    crossfade_s: float,
    law: str = "equal_power",
    step_s: float = 1.0,
):
    """Wrap the most level-stable window of the take, not simply the first one.

    A pad that swells over its length gives a wrap whose two sides sit at different levels, which
    pumps once per cycle. Sliding the window costs nothing (it is all numpy on audio we already
    have) and picks the span where the two ends agree.
    """
    need = round((length_s + crossfade_s) * sr)
    if body.shape[-1] < need:
        return None, None
    best, best_x, best_off = None, None, 0.0
    for off in np.arange(0.0, (body.shape[-1] - need) / sr + 1e-9, step_s):
        i = round(off * sr)
        cand = sfx.loop_wrap(body[:, i : i + need], sr, length_s, crossfade_s, law)
        q = sfx.loop_qc(cand, sr)
        cost = abs(q["seam_rms_delta_db"]) + 0.5 * q["event_prominence_db"]
        if best is None or cost < best:
            best, best_x, best_off = cost, cand, float(off)
    return best_x, best_off


def music_qc(x: np.ndarray, sr: int) -> dict:
    """Tone QC plus the two music-specific numbers: room for the voice, and does it move."""
    q = sfx.tone_qc(x, sr)
    q["presence_band_ratio"] = presence_ratio(x, sr)
    mono = x.mean(axis=0)
    b = int(1.0 * sr)
    nb = max(mono.size // b, 1)
    blocks = np.sqrt((mono[: nb * b].reshape(nb, b) ** 2).mean(axis=1))
    bdb = 20 * np.log10(np.maximum(blocks, 1e-12))
    q["dynamic_span_db"] = round(float(np.percentile(bdb, 95) - np.percentile(bdb, 5)), 2)
    q["slow_envelope_range_db"] = slow_envelope_range_db(x, sr)
    return q


def hhmmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:d}:{s:02d}"


__all__ = [
    "DEFAULT_MODELS",
    "DEFAULT_PORT",
    "FRAME_RATE",
    "MAX_FRAMES",
    "MODEL_ID",
    "SAMPLE_RATE",
    "fade",
    "generate",
    "hhmmss",
    "math",
    "music_qc",
    "presence_ratio",
    "props",
    "server_up",
    "sfx",
    "start_server",
    "trim_head",
]
