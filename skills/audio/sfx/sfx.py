"""Stable Audio 3 Small-SFX: offline model loading, loop wrapping, loudness, QC.

Imported by `run.py` (one prompt) and `build_library.py` (the whole library). Never imported by
the control plane — this module lives in the skill's own uv environment (torch 2.7.1 + cu126).

Design rationale and every sourced number are in
`docs/research/2026-09-07-video-sfx-and-ambience-library.md`. The short version:

* small-sfx is a *post-trained* checkpoint, so `cfg_scale` and `negative_prompt` do nothing. All
  steering lives in the positive prompt.
* Loops are made by the "flip-flop" wrap: generate longer than needed, then fold the tail back over
  the head with an equal-power (cos/sin) crossfade. The wrap point is a continuous span of the
  original generation, so there is no splice and nothing to click.
* Every filter here is zero-phase and circular (rFFT), which is what keeps a wrapped loop loopable.
* Level is set with a single constant scalar. No compressor, no limiter, no ffmpeg `loudnorm`
  dynamic mode — time-varying gain would destroy the seam the wrap just built.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]  # skills/audio/sfx/sfx.py -> repo root
DEFAULT_CODE = REPO_ROOT / "external" / "stable-audio-3"
DEFAULT_MODEL = REPO_ROOT / "models" / "sound_effects" / "StableAudio3-Small-SFX"
MODEL_ID = "stabilityai/stable-audio-3-small-sfx"
SAMPLE_RATE = 44100


# --------------------------------------------------------------------------- model


def load_model(
    model_dir: Path | None = None,
    code_dir: Path | None = None,
    device: str = "cuda",
    half: bool = True,
):
    """Load small-sfx entirely from local disk.

    `StableAudioModel.from_pretrained("small-sfx")` resolves through `hf_hub_download`, which we do
    not want. `T5GemmaConditioner` accepts `model_path`, which takes precedence over `repo_id`, so
    rewriting the conditioner config to point at the checkout's `t5gemma-b-b-ul2/` subfolder and
    calling `load_diffusion_cond()` directly loads the DiT, the SAME-S autoencoder and the text
    encoder with no network access at all. HF_HUB_OFFLINE is set as a belt-and-braces guard.
    """
    code = Path(os.environ.get("CF_SA3_REPO", code_dir or DEFAULT_CODE)).expanduser()
    mdir = Path(os.environ.get("CF_SA3_SFX_MODEL_PATH", model_dir or DEFAULT_MODEL)).expanduser()
    if not (code / "stable_audio_3").is_dir():
        raise SystemExit(f"stable-audio-3 checkout not found at {code}")
    if not (mdir / "model_config.json").is_file():
        raise SystemExit(f"small-sfx weights not found at {mdir}")

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    if str(code) not in sys.path:
        sys.path.insert(0, str(code))

    import torch
    from stable_audio_3.loading_utils import load_diffusion_cond
    from stable_audio_3.model import StableAudioModel

    if device == "cuda" and not torch.cuda.is_available():
        device, half = "cpu", False

    cfg = json.loads((mdir / "model_config.json").read_text())
    for cond in cfg["model"]["conditioning"]["configs"]:
        if cond["type"] == "t5gemma":
            sub = cond["config"].pop("subfolder", None)
            cond["config"].pop("repo_id", None)
            cond["config"]["model_path"] = str(mdir / sub if sub else mdir)

    net = load_diffusion_cond(cfg, str(mdir / "model.safetensors"), device=device, model_half=half)
    net.use_lora = False
    net.lora_names = []
    return StableAudioModel(net, cfg, device, half)


def generate(model, prompt: str, duration_s: float, seed: int, steps: int = 8) -> np.ndarray:
    """One generation -> float32 array shaped (channels, samples)."""
    out = model.generate(prompt=prompt, duration=float(duration_s), steps=steps, seed=int(seed))
    return out[0].to("cpu").float().numpy()


# --------------------------------------------------------------------------- dsp


def _rfft_filter(x: np.ndarray, sr: int, mag) -> np.ndarray:
    """Zero-phase, circular magnitude filter. Circular is the point: a wrapped loop stays wrapped."""
    n = x.shape[-1]
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    spec = np.fft.rfft(x, axis=-1)
    return np.fft.irfft(spec * mag(freqs), n=n, axis=-1)


def highpass(x: np.ndarray, sr: int, fc: float) -> np.ndarray:
    """2nd-order-Butterworth magnitude high-pass. Kills DC and sub-20 Hz mic rumble."""
    if fc <= 0:
        return x

    def mag(f):
        r = np.divide(f, fc, out=np.zeros_like(f), where=f > 0)
        return (r**2) / np.sqrt(1.0 + r**4)

    return _rfft_filter(x, sr, mag)


def highpass_linear(x: np.ndarray, sr: int, fc: float) -> np.ndarray:
    """High-pass for non-looping material: zero-pad so circular wrap cannot fold a tail onto a head."""
    n = x.shape[-1]
    pad = min(n, int(sr * 0.5))
    padded = np.pad(x, ((0, 0), (pad, pad)))
    return highpass(padded, sr, fc)[:, pad : pad + n]


def loop_wrap(
    x: np.ndarray, sr: int, length_s: float, crossfade_s: float, law: str = "equal_power"
) -> np.ndarray:
    """Fold the tail back over the head with an equal-power crossfade.

        head = x[0:C]   body = x[C:L]   tail = x[L:L+C]
        out  = concat(tail*cos(pi/2*t) + head*sin(pi/2*t), body)      t: 0 -> 1 over C

    The result is exactly `length_s` long. Its last sample is x[L-1] and its first is tail[0] = x[L],
    so the join is a continuous span of the source: there is no splice to click on. cos/sin (rather
    than a linear fade) holds RMS flat across the overlap, which is correct for the noise-like,
    uncorrelated material every bed in this library is made of.
    """
    lo, cf = round(length_s * sr), round(crossfade_s * sr)
    if x.shape[-1] < lo + cf:
        raise ValueError(
            f"need {lo + cf} samples for a {length_s}s/{crossfade_s}s loop, got {x.shape[-1]}"
        )
    head, body, tail = x[:, :cf], x[:, cf:lo], x[:, lo : lo + cf]
    t = np.linspace(0.0, 1.0, cf, endpoint=False, dtype=np.float64)
    if law == "linear":
        # Equal-GAIN. Correct when head and tail are correlated (sustained tonal beds): cos/sin
        # would sum two phase-related copies to as much as +3 dB through the middle of the fade.
        joined = tail * (1.0 - t) + head * t
    else:
        joined = tail * np.cos(0.5 * np.pi * t) + head * np.sin(0.5 * np.pi * t)
    out = np.concatenate([joined, body], axis=-1)

    # The wrap is continuous by construction: out[-1] is x[lo-1] and out[0] is tail[0] = x[lo],
    # which are adjacent samples of the source. Assert it rather than measure it -- a statistical
    # "does the seam click" test cannot tell a splice artefact from a real transient that happens
    # to sit at the join, and will happily flag a perfectly good loop.
    assert np.allclose(out[:, 0], x[:, lo]) and np.allclose(out[:, -1], x[:, lo - 1]), (
        "loop_wrap: seam is not sample-adjacent in the source"
    )
    return out


def trim_oneshot(
    x: np.ndarray,
    sr: int,
    onset_db: float = -35.0,
    tail_db: float = -60.0,
    head_pad_ms: float = 30.0,
    tail_pad_ms: float = 60.0,
    fade_ms: float = 3.0,
) -> np.ndarray:
    """Trim to the event, then apply short fades so the file cannot click on trigger.

    The head threshold is deliberately much higher than the tail one. The model frequently lays
    down low-level room tone before the actual event and places the event late in the requested
    duration; a -60 dB gate sees that room tone as signal and leaves a one-shot with a half-second
    of dead air in front of it, which is useless for a UI cue that has to land on a frame. -35 dB
    relative to peak finds the onset while still keeping the quiet first note of a multi-note cue.
    """
    env = np.max(np.abs(x), axis=0)
    peak = float(env.max())
    if peak <= 0:
        return x
    onset = np.flatnonzero(env > peak * (10.0 ** (onset_db / 20.0)))
    above = np.flatnonzero(env > peak * (10.0 ** (tail_db / 20.0)))
    if onset.size:
        above = above[above >= 0]
        above = np.array([onset[0], max(above[-1], onset[-1])])
    if above.size == 0:
        return x
    lo = max(0, above[0] - int(sr * head_pad_ms / 1000))
    hi = min(x.shape[-1], above[-1] + int(sr * tail_pad_ms / 1000))
    y = x[:, lo:hi].copy()
    f = min(int(sr * fade_ms / 1000), y.shape[-1] // 2)
    if f > 1:
        ramp = np.linspace(0.0, 1.0, f)
        y[:, :f] *= ramp
        y[:, -f:] *= ramp[::-1]
    return y


# --------------------------------------------------------------------------- loudness


def write_wav(path: Path, x: np.ndarray, sr: int, subtype: str = "FLOAT") -> None:
    import soundfile as sf

    sf.write(str(path), x.T, sr, subtype=subtype)


_M_RE = re.compile(r"\bM:\s*(-?\d+\.?\d*)")
_TPK_RE = re.compile(r"\bTPK:\s*(-?\d+\.?\d*)\s+(-?\d+\.?\d*)")


def measure(x: np.ndarray, sr: int) -> dict:
    """EBU R128 via ffmpeg: integrated LUFS, loudness range, true peak, max momentary.

    Max momentary is what one-shots are levelled to. Integrated LUFS is meaningless for a 0.3 s
    click (BS.1770 gates on 400 ms blocks and discards quiet ones); momentary is not.

    Two ffmpeg details that are easy to get wrong:
      * `framelog=info` is required. `framelog=verbose` means "log frames at VERBOSE level", which
        is invisible at the default loglevel — the per-frame `M:` values simply never appear.
      * ebur128's momentary window is 400 ms, so a file shorter than that yields no `M:` reading at
        all. We zero-pad the measurement copy to 1 s. Padding changes nothing: the loudest 400 ms
        window still contains the same audio, silence falls below the -70 LUFS absolute gate so the
        integrated value is untouched, and true peak is unaffected.
    """
    if x.shape[-1] < sr:
        x = np.pad(x, ((0, 0), (0, sr - x.shape[-1])))
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "m.wav"
        write_wav(wav, x, sr)
        proc = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-nostats",
                "-i",
                str(wav),
                "-filter_complex",
                "ebur128=peak=true:framelog=info",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    err = proc.stderr
    moments = [float(m) for m in _M_RE.findall(err) if float(m) > -120.0]
    tpks = [max(float(a), float(b)) for a, b in _TPK_RE.findall(err)]

    def summary(label: str, default: float) -> float:
        m = re.search(rf"{label}:\s*(-?\d+\.?\d*)", err.split("Summary:")[-1])
        return float(m.group(1)) if m else default

    return {
        "integrated_lufs": summary("I", -70.0),
        "loudness_range_lu": summary("LRA", 0.0),
        "true_peak_dbtp": summary("Peak", -70.0)
        if "Peak:" in err
        else (max(tpks) if tpks else -70.0),
        "max_momentary_lufs": max(moments) if moments else -70.0,
        "sample_peak_dbfs": 20 * math.log10(max(float(np.abs(x).max()), 1e-12)),
    }


def normalize(
    x: np.ndarray, sr: int, metric: str, target_lufs: float, ceiling_dbtp: float
) -> tuple[np.ndarray, dict, dict]:
    """Apply ONE constant gain: whichever of (loudness target, true-peak ceiling) is quieter.

    Constant gain only. Any dynamic processing would move the loop seam built by loop_wrap().
    """
    before = measure(x, sr)
    want_db = target_lufs - before[metric]
    headroom_db = ceiling_dbtp - before["true_peak_dbtp"]
    gain_db = min(want_db, headroom_db)
    y = x * (10.0 ** (gain_db / 20.0))
    after = measure(y, sr)
    after["gain_db"] = round(gain_db, 3)
    after["gain_limited_by"] = "true_peak" if headroom_db < want_db else "loudness"
    return y, before, after


# --------------------------------------------------------------------------- qc


def spectrum(x: np.ndarray, sr: int, nfft: int = 8192) -> tuple[np.ndarray, np.ndarray]:
    """Welch-style averaged power spectrum of the mono sum."""
    mono = x.mean(axis=0)
    if mono.size < nfft:
        mono = np.pad(mono, (0, nfft - mono.size))
    hop, win = nfft // 2, np.hanning(nfft)
    frames = [
        np.abs(np.fft.rfft(mono[i : i + nfft] * win)) ** 2
        for i in range(0, mono.size - nfft + 1, hop)
    ]
    return np.fft.rfftfreq(nfft, 1.0 / sr), np.mean(frames, axis=0)


def tone_qc(x: np.ndarray, sr: int) -> dict:
    """Cheap stand-ins for the psychoacoustic metrics that predict unpleasantness.

    `harsh_band_ratio` is 2-5 kHz energy over 20 Hz-20 kHz energy — a proxy for Zwicker sharpness,
    which weights high frequencies and whose rise is what "sensory pleasantness decreases with an
    increase in Sharpness" refers to. It is a proxy, not an acum measurement; it exists so a harsh
    outlier gets flagged and regenerated instead of shipped.
    """
    f, p = spectrum(x, sr)
    band = (f >= 20) & (f <= 20000)
    total = float(p[band].sum()) or 1e-20
    harsh = (f >= 2000) & (f <= 5000)
    sub = (f >= 20) & (f < 120)
    air = (f > 8000) & (f <= 20000)

    # Spectral flatness (Wiener entropy): geometric mean over arithmetic mean of the power
    # spectrum. ~1 for white noise, ~0 for anything with structure. Measured on the existing
    # library it is the one metric that cleanly separates a designed one-shot (0.000) from a
    # failed generation that came back as broadband hiss (0.24-0.64).
    q = np.maximum(p[(f >= 50) & (f <= 16000)], 1e-20)
    flatness = float(np.exp(np.log(q).mean()) / q.mean())

    def band_db(lo: float, hi: float) -> float:
        sel = (f >= lo) & (f < hi)
        return 10 * math.log10(max(float(p[sel].sum()) / total, 1e-12))

    return {
        "spectral_flatness": round(flatness, 4),
        "hf_tilt_db": round(band_db(5657, 11314) - band_db(88, 177), 2),
        "spectral_centroid_hz": round(float((f[band] * p[band]).sum() / total), 1),
        "harsh_band_ratio": round(float(p[harsh].sum() / total), 4),
        "low_band_ratio": round(float(p[sub].sum() / total), 4),
        "air_band_ratio": round(float(p[air].sum() / total), 4),
        "dc_offset": round(float(np.abs(x.mean(axis=-1)).max()), 6),
    }


def band_focus_db(x: np.ndarray, sr: int, lo: float, hi: float) -> float:
    """How far the intended band is below holding all the energy. 0 = everything is in band.

    Used to steer candidate selection towards a sound that is what its name says: an airy whoosh
    should peak in the mids, a sub drop at the bottom. Both can be flawless generations; only one
    of them is the sound that was asked for.
    """
    f, p = spectrum(x, sr)
    total = float(p[(f >= 20) & (f <= 20000)].sum()) or 1e-20
    sel = (f >= lo) & (f < hi)
    return round(-10 * math.log10(max(float(p[sel].sum()) / total, 1e-12)), 2)


def loop_qc(x: np.ndarray, sr: int) -> dict:
    """Does the wrap actually loop, and is the bed even enough to survive repeating?

    Clicks are not measured here: loop_wrap asserts the seam is sample-adjacent in the source, so
    a click is impossible by construction. What can still go wrong is a *level* mismatch across the
    wrap, which pumps once per cycle. `seam_rms_delta_db` compares the 2 s either side -- 200 ms was
    too short a window to be stable on a bass-heavy bed.

    `event_prominence_db` is the third, different question: the research warning is not about clicks
    but about "distinct sounds within the ambience ... being noticeably repeated". Crest factor is
    the wrong test for that -- rain and fire are legitimately high-crest because of very short
    transients. What a listener actually remembers is an event of some duration, so this measures
    half-second block RMS and reports the loudest block over the median. `bed_evenness_db` (p95 over
    median) says how flat the bed is underneath any such event.
    """
    mono = x.mean(axis=0)
    w = min(int(2.0 * sr), mono.size // 4)
    rms_end = float(np.sqrt(np.mean(mono[-w:] ** 2))) or 1e-12
    rms_start = float(np.sqrt(np.mean(mono[:w] ** 2))) or 1e-12

    b = int(0.5 * sr)
    nb = mono.size // b
    blocks = np.sqrt((mono[: nb * b].reshape(nb, b) ** 2).mean(axis=1))
    bdb = 20 * np.log10(np.maximum(blocks, 1e-12))
    median_db = float(np.median(bdb))
    return {
        "seam_rms_delta_db": round(20 * math.log10(rms_start / rms_end), 2),
        "event_prominence_db": round(float(bdb.max()) - median_db, 2),
        "bed_evenness_db": round(float(np.percentile(bdb, 95)) - median_db, 2),
    }


def sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_flac(path: Path, x: np.ndarray, sr: int) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(x, -1.0, 1.0).T, sr, format="FLAC", subtype="PCM_24")
