"""Ingest side of the SFX library: real recordings out of a local sound-effects bundle."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from html import unescape
from pathlib import Path

import numpy as np
import sfx

DEFAULT_BUNDLE = Path.home() / "Music" / "sonniss_gdc_2026"
FILELIST_GLOB = "*Filelist.xlsx"
LICENCE_PDF = "License - GDC Game Audio.pdf"

# The bundle ships one royalty-free licence covering every pack in it: unlimited projects,
# commercial use, modification allowed, no attribution required.
BUNDLE_LICENCE = (
    "Sonniss #GameAudioGDC Bundle licensing agreement: worldwide, non-exclusive, royalty-free; "
    "unlimited personal and commercial projects; modification permitted; no attribution required"
)


def bundle_root() -> Path:
    root = Path(os.environ.get("CF_SONNISS_GDC_DIR", DEFAULT_BUNDLE)).expanduser()
    if not root.is_dir():
        raise SystemExit(
            f"sound-effects bundle not found at {root}\n"
            "Set CF_SONNISS_GDC_DIR to the extracted #GameAudioGDC bundle."
        )
    return root


def pack_root(pack: dict, must_exist: bool = True) -> Path:
    """The staging root of a pack recipe: its own env var, else the default it names."""
    root = Path(os.environ.get(pack["root_env"], pack["root_default"])).expanduser()
    if must_exist and not root.is_dir():
        raise SystemExit(
            f"pack root not found at {root}\n"
            f"Set {pack['root_env']}, or stage the pack with stage_pack.py."
        )
    return root


# --------------------------------------------------------------------------- provenance


_CELL_RE = re.compile(r'<c\b[^>]*?\br="([A-Z]+)\d+"([^>]*)>(.*?)</c>', re.S)
_ROW_RE = re.compile(r"<row\b[^>]*>(.*?)</row>", re.S)
_INLINE_RE = re.compile(r"<t[^>]*>(.*?)</t>", re.S)
_VALUE_RE = re.compile(r"<v>(.*?)</v>", re.S)


def _cell_text(attrs: str, body: str) -> str:
    """One cell's text, for the two encodings a tracklist of strings can arrive in."""
    if 't="s"' in attrs:
        raise SystemExit("tracklist uses a shared-string table this parser does not read")
    inline = _INLINE_RE.findall(body)
    if inline:
        return unescape("".join(inline)).strip()
    v = _VALUE_RE.search(body)
    return unescape(v.group(1)).strip() if v else ""


def credits(root: Path) -> dict[str, dict]:
    """filename -> {library, supplier, url}, read from the bundle's own tracklist."""
    sheets = sorted(root.glob(FILELIST_GLOB))
    if not sheets:
        raise SystemExit(f"no {FILELIST_GLOB} in {root}: cannot establish provenance")
    import zipfile

    with zipfile.ZipFile(sheets[0]) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8", "replace")

    out: dict[str, dict] = {}
    for rm in _ROW_RE.finditer(xml):
        cells = {col: _cell_text(attrs, body) for col, attrs, body in _CELL_RE.findall(rm.group(1))}
        name = cells.get("A", "")
        if not name or name.upper() == "FILENAME":
            continue
        out[name] = {
            "library": cells.get("B", ""),
            "supplier": cells.get("C", ""),
            "url": cells.get("D", ""),
        }
    if not out:
        raise SystemExit(f"{sheets[0].name} parsed to zero rows")
    return out


# --------------------------------------------------------------------------- source audio


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels,bits_per_raw_sample:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    d = json.loads(proc.stdout)
    st = d["streams"][0]
    return {
        "sample_rate": int(st["sample_rate"]),
        "channels": int(st["channels"]),
        "bit_depth": int(st.get("bits_per_raw_sample") or 0) or None,
        "duration_s": float(d["format"]["duration"]),
    }


def decode(path: Path, sr: int, start_s: float = 0.0, dur_s: float | None = None) -> np.ndarray:
    """Decode to (2, n) float64 at `sr`, resampled with soxr at its highest precision."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if start_s:
        cmd += ["-ss", f"{start_s:.6f}"]
    if dur_s is not None:
        cmd += ["-t", f"{dur_s:.6f}"]
    cmd += [
        "-i",
        str(path),
        "-af",
        "aresample=resampler=soxr:precision=28",
        "-ar",
        str(sr),
        "-ac",
        "2",
        "-f",
        "f32le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    x = np.frombuffer(proc.stdout, dtype="<f4").reshape(-1, 2).T
    return np.ascontiguousarray(x, dtype=np.float64)


def block_rms_db(path: Path, sr: int, block_s: float = 0.25) -> np.ndarray:
    """Streaming mono block-RMS in dB over the whole file. The cheap pass of the window search."""
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "mono.f32"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-af",
                "aresample=resampler=soxr:precision=28",
                "-ar",
                str(sr),
                "-ac",
                "1",
                "-f",
                "f32le",
                str(raw),
            ],
            check=True,
        )
        mono = np.fromfile(raw, dtype="<f4")
    b = int(block_s * sr)
    nb = mono.size // b
    blocks = np.sqrt((mono[: nb * b].astype(np.float64).reshape(nb, b) ** 2).mean(axis=1))
    return 20.0 * np.log10(np.maximum(blocks, 1e-12))


# --------------------------------------------------------------------------- window choice


def prescore_windows(
    bdb: np.ndarray, block_s: float, length_s: float, crossfade_s: float, hop_s: float
) -> list[tuple[float, float]]:
    """Rank every candidate start on the two things that give a loop away, from block RMS alone."""
    need = round((length_s + crossfade_s) / block_s)
    seam = max(1, round(2.0 / block_s))
    body = round(length_s / block_s)
    step = max(1, round(hop_s / block_s))
    out: list[tuple[float, float]] = []
    for i in range(0, max(1, bdb.size - need), step):
        w = bdb[i : i + need]
        prominence = float(w.max() - np.median(w))
        head = float(np.mean(w[:seam]))
        tail = float(np.mean(w[body - seam : body]))
        out.append((prominence + 2.0 * abs(head - tail), i * block_s))
    out.sort()
    return out


def pick_window(
    path: Path,
    sr: int,
    length_s: float,
    crossfade_s: float,
    law: str,
    highpass_hz: float,
    hop_s: float,
    finalists: int,
) -> tuple[np.ndarray, dict, float, float, int]:
    """Choose the best loop window in a recording and return it wrapped."""
    bdb = block_rms_db(path, sr)
    ranked = prescore_windows(bdb, 0.25, length_s, crossfade_s, hop_s)
    if not ranked:
        raise SystemExit(f"{path.name}: too short for a {length_s}s/{crossfade_s}s loop")

    best: tuple[float, np.ndarray, dict, float] | None = None
    for _pre, start_s in ranked[:finalists]:
        raw = decode(path, sr, start_s, length_s + crossfade_s + 0.5)
        if raw.shape[-1] < round((length_s + crossfade_s) * sr):
            continue
        x = sfx.loop_wrap(raw, sr, length_s, crossfade_s, law)
        x = sfx.highpass(x, sr, highpass_hz)  # circular -> stays loopable
        qc = {**sfx.tone_qc(x, sr), **sfx.loop_qc(x, sr)}
        s = (
            qc["event_prominence_db"]
            + 2.0 * abs(qc["seam_rms_delta_db"])
            + 20.0 * max(0.0, qc["harsh_band_ratio"] - 0.35)
        )
        if best is None or s < best[0]:
            best = (s, x, qc, start_s)
    if best is None:
        raise SystemExit(f"{path.name}: no usable {length_s}s window")
    s, x, qc, start_s = best
    return x, qc, start_s, s, len(ranked)


# --------------------------------------------------------------------------- event choice


def find_events(
    x: np.ndarray,
    sr: int,
    gate_db: float = -35.0,
    min_gap_s: float = 0.15,
    min_len_s: float = 0.01,
) -> list[tuple[int, int]]:
    """Split a recording into discrete events at `gate_db` below its peak."""
    env = np.max(np.abs(x), axis=0)
    peak = float(env.max())
    if peak <= 0:
        return []
    smooth = max(1, int(0.005 * sr))
    env = np.convolve(env, np.ones(smooth) / smooth, mode="same")
    above = env > peak * (10.0 ** (gate_db / 20.0))
    if not above.any():
        return []
    edges = np.flatnonzero(np.diff(above.astype(np.int8)))
    bounds = np.concatenate(([0] if above[0] else [], edges + 1, [above.size] if above[-1] else []))
    runs = [(int(bounds[i]), int(bounds[i + 1])) for i in range(0, len(bounds) - 1, 2)]

    gap = int(min_gap_s * sr)
    merged: list[list[int]] = []
    for lo, hi in runs:
        if merged and lo - merged[-1][1] <= gap:
            merged[-1][1] = hi
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged if hi - lo >= int(min_len_s * sr)]


def pick_event(
    x: np.ndarray,
    sr: int,
    index: int,
    min_gap_s: float,
    gate_db: float = -35.0,
    pad_s: float = 0.05,
) -> tuple[np.ndarray, int, int]:
    """Crop to the `index`-th event."""
    events = find_events(x, sr, gate_db=gate_db, min_gap_s=min_gap_s)
    if not events:
        return x, 0, 0
    if index < 0:
        env = np.max(np.abs(x), axis=0)
        order = sorted(range(len(events)), key=lambda i: -env[events[i][0] : events[i][1]].max())
        chosen = order[min(-index - 1, len(order) - 1)]
    else:
        chosen = min(index, len(events) - 1)
    lo, hi = events[chosen]
    pad = int(pad_s * sr)
    return x[:, max(0, lo - pad) : min(x.shape[-1], hi + pad)].copy(), chosen, len(events)


def cap_and_fade(
    x: np.ndarray, sr: int, max_s: float, fade_ms: float = 30.0
) -> tuple[np.ndarray, float | None]:
    """Cap a one-shot's length and fade the cut."""
    cap = int(max_s * sr)
    if x.shape[-1] <= cap:
        return x, None
    env = np.max(np.abs(x), axis=0)
    peak = float(env.max()) or 1e-12
    at = float(env[max(0, cap - int(0.01 * sr)) : cap].max())
    y = x[:, :cap].copy()
    f = min(int(sr * fade_ms / 1000), y.shape[-1] // 2)
    if f > 1:
        y[:, -f:] *= np.linspace(1.0, 0.0, f)
    return y, round(20.0 * math.log10(max(at / peak, 1e-12)), 1)


def cut_whole(
    path: Path, sr: int, highpass_hz: float, max_s: float | None = None, trim: bool = True
) -> tuple[np.ndarray, dict]:
    """The file as delivered: trim the silence around it, optionally cap, high-pass."""
    raw = decode(path, sr)
    source_peak_dbfs = db(float(np.abs(raw).max()))
    x = sfx.trim_oneshot(raw, sr) if trim else raw
    trimmed_s = round((raw.shape[-1] - x.shape[-1]) / sr, 3)
    trunc_db = None
    if max_s is not None:
        x, trunc_db = cap_and_fade(x, sr, float(max_s))
    x = sfx.highpass_linear(x, sr, highpass_hz)  # zero-padded -> no circular fold
    return x, {
        "mode": "whole",
        "silence_trimmed_s": trimmed_s,
        "max_s": float(max_s) if max_s is not None else None,
        "truncated_at_db": trunc_db,
        "source_peak_dbfs": source_peak_dbfs,
    }


def source_sha256(path: Path) -> str:
    return sfx.sha256(path)


def db(v: float) -> float:
    return round(20.0 * math.log10(max(v, 1e-12)), 2)
