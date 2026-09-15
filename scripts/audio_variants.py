"""Render one clip's audio several different ways, so the choice is made by listening."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MASTER_LUFS = -16.0

# Library layers with a slow level automation each: steady loops summed together average their
# dynamics away (LRA 2.4 against outdoor 10-14). Periods are detuned so the swells never pulse.
SURF = ("ocean_waves_loop", "0.80+0.20*sin(2*PI*t/13)")
WIND = ("wind_open_loop", "0.62+0.38*sin(2*PI*t/7.3)")
AIR = ("breeze_trees_loop", "0.60+0.40*sin(2*PI*t/5.1+1.7)")
BIRDS = ("birds_morning_loop", None)
STEPS = ("footsteps_grass_loop", None)

VARIANTS: dict[str, dict] = {
    "01-mm-only": {"layers": [], "mm_db": 0.0, "why": "MMAudio alone: the baseline"},
    "02-library-only": {
        "layers": [(SURF, -9.0), (WIND, -14.0), (AIR, -5.0), (BIRDS, -22.0)],
        "mm_db": None,
        "why": "library layers alone: spectrum without the picture",
    },
    "03-balanced": {
        "layers": [(SURF, -9.0), (WIND, -14.0), (AIR, -5.0), (BIRDS, -22.0)],
        "mm_db": -6.0,
        "why": "library for the spectrum, MMAudio folded under for the picture",
    },
    "04-mm-forward": {
        "layers": [(SURF, -12.0), (WIND, -16.0), (AIR, -8.0), (BIRDS, -24.0)],
        "mm_db": -1.0,
        "why": "MMAudio leads, library only fills the air it leaves empty",
    },
    "05-mm-back": {
        "layers": [(SURF, -8.0), (WIND, -13.0), (AIR, -4.0), (BIRDS, -21.0)],
        "mm_db": -16.0,
        "why": "MMAudio barely there: almost the library bed",
    },
    "06-bright": {
        "layers": [(SURF, -13.0), (WIND, -16.0), (AIR, -2.0), (BIRDS, -17.0)],
        "mm_db": -8.0,
        "why": "air forward: exposed clifftop, thin and windy",
    },
    "07-deep": {
        "layers": [(SURF, -4.0), (WIND, -10.0), (AIR, -12.0), (BIRDS, -28.0)],
        "mm_db": -6.0,
        "why": "surf forward: heavy sea, close to the edge",
    },
    "08-footsteps": {
        # -6 lands about 3 dB under the surf once `library_trim_db` adds +16.1 dB; before that
        # compensation, -20 and -8 were both inaudible (byte-identical to `03-balanced`).
        "layers": [(SURF, -9.0), (WIND, -14.0), (AIR, -5.0), (BIRDS, -22.0), (STEPS, -6.0)],
        "mm_db": -6.0,
        "why": "balanced plus footsteps on the path",
    },
    "09-music": {
        "layers": [(SURF, -11.0), (WIND, -16.0), (AIR, -7.0), (BIRDS, -24.0)],
        "mm_db": -9.0,
        "music": ("nature_open_wide", -14.0),
        "why": "balanced, pulled back under a music bed",
    },
    "10-no-gusts": {
        "layers": [(SURF, -9.0), (WIND, -14.0), (AIR, -5.0), (BIRDS, -22.0)],
        "mm_db": -6.0,
        "gusts": False,
        "why": "control: the same mix with the level automation switched off",
    },
    "11-lowcut": {
        "layers": [(SURF, -9.0), (WIND, -14.0), (AIR, -5.0), (BIRDS, -22.0)],
        "mm_db": -6.0,
        "post": "highpass=f=70",
        "why": "balanced with the sub-70 Hz rumble taken out",
    },
    "12-wide": {
        "layers": [(SURF, -9.0), (WIND, -14.0), (AIR, -5.0), (BIRDS, -22.0)],
        "mm_db": -6.0,
        "stereo": True,
        "why": "balanced, opened into stereo (surf left, air right)",
    },
    "13-piano": {
        "layers": [(SURF, -12.0), (WIND, -17.0), (AIR, -8.0), (BIRDS, -25.0)],
        "mm_db": -10.0,
        "music": ("human_warm_piano", -11.0),
        "why": "warm piano over a quiet bed: the scored version",
    },
    "14-sparse": {
        "layers": [(WIND, -12.0), (BIRDS, -20.0)],
        "mm_db": -9.0,
        "why": "wind and birds only: no surf, no leaves",
    },
    "15-nearly-silent": {
        "layers": [(SURF, -18.0), (WIND, -20.0), (AIR, -16.0), (BIRDS, -30.0)],
        "mm_db": -14.0,
        "why": "a very quiet bed, for a cut that wants to feel still",
    },
}


NOMINAL_LUFS = -23.0
"""What the library normalises a sound to -- when it can.

Not every sound gets there, and the manifest says so. `footsteps_grass_loop` records
``integrated_lufs: -39.1`` with ``gain_limited_by: "true_peak"``: sparse footstep transients have a
high crest factor, so the normaliser hit the true-peak ceiling after 1.4 dB of gain and stopped,
leaving the file **16 LU below every bed in the library**. A caller that assumes one common target
places it 16 dB too quiet, which is exactly what happened here -- at a stated -20 dB the footsteps
variant measured byte-identically to the mix without them, and at -8 dB it moved the spectrum by
0.2 %. So gains below are stated against this nominal level and compensated per sound from what the
manifest actually measured."""


def library_trim_db(sound_id: str) -> float:
    """How far a sound sits from the nominal bed level, from the manifest's own measurement."""
    import json

    manifest = json.loads((REPO / "assets" / "sfx" / "manifest.json").read_text())
    rows = (
        manifest
        if isinstance(manifest, list)
        else manifest.get("sounds", manifest.get("entries", []))
    )
    for row in rows:
        if row.get("id") == sound_id:
            measured = (row.get("loudness") or {}).get("integrated_lufs")
            if measured is None:
                return 0.0
            return NOMINAL_LUFS - float(measured)
    return 0.0


def library_path(sound_id: str) -> Path:
    from content_factory.audio.cues import load_library

    return Path(load_library().sounds[sound_id].path)


def music_path(track_id: str) -> Path:
    index = json.loads((REPO / "assets" / "music" / "tracks.json").read_text())
    rows = index if isinstance(index, list) else index["tracks"]
    row = next(r for r in rows if (r.get("track_id") or r.get("id")) == track_id)
    hits = list((REPO / "assets" / "music").rglob(row["filename"]))
    if not hits:
        raise FileNotFoundError(row["filename"])
    return hits[0]


def render(name: str, spec: dict, video: Path, mm: Path, duration: float, out_dir: Path) -> Path:
    """One variant, mastered to `MASTER_LUFS` so the comparison is about content, not level."""
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    idx = 0
    use_gusts = spec.get("gusts", True)
    for (sound_id, gust), gain in spec["layers"]:
        inputs += ["-stream_loop", "-1", "-t", f"{duration}", "-i", str(library_path(sound_id))]
        effective = gain + library_trim_db(sound_id)
        chain = f"[{idx}:a]aformat=channel_layouts=mono,volume={effective:.2f}dB"
        if gust and use_gusts:
            chain += f",volume='{gust}':eval=frame"
        filters.append(chain + f"[a{idx}]")
        labels.append(f"[a{idx}]")
        idx += 1
    if spec.get("mm_db") is not None:
        inputs += ["-t", f"{duration}", "-i", str(mm)]
        filters.append(f"[{idx}:a]aformat=channel_layouts=mono,volume={spec['mm_db']}dB[a{idx}]")
        labels.append(f"[a{idx}]")
        idx += 1
    if spec.get("music"):
        track, gain = spec["music"]
        inputs += ["-stream_loop", "-1", "-t", f"{duration}", "-i", str(music_path(track))]
        filters.append(f"[{idx}:a]aformat=channel_layouts=mono,volume={gain}dB[a{idx}]")
        labels.append(f"[a{idx}]")
        idx += 1
    filters.append("".join(labels) + f"amix=inputs={idx}:normalize=0[mixed]")
    # A static gain plus a true-peak limiter, not single-pass `loudnorm`: that compresses, and
    # flattened an earlier bed to LRA 2.4 where the real recordings measure 10-14.
    tail = "[mixed]"
    if spec.get("post"):
        tail += spec["post"] + ","
    if spec.get("stereo"):
        # A mono bed opened with a short haas delay on one side: enough width for headphones,
        # and it stays mono-compatible because the delay is under 20 ms.
        tail += "aformat=channel_layouts=stereo,extrastereo=m=1.6,"
    filters.append(tail + "alimiter=limit=0.9:level=disabled[outa]")
    wav = out_dir / f"{name}.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outa]",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(wav),
        ],
        check=True,
    )
    _to_target(wav, stereo=bool(spec.get("stereo")))
    muxed = out_dir / f"{name}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-i",
            str(wav),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(muxed),
        ],
        check=True,
    )
    return wav


def _to_target(wav: Path, *, stereo: bool = False) -> None:
    """Measure, then apply one static gain. No dynamics touched."""
    measured = _loudness(wav)
    if measured is None:
        return
    gain = MASTER_LUFS - measured
    tmp = wav.with_suffix(".gain.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(wav),
            "-af",
            f"volume={gain:.2f}dB,alimiter=limit=0.89:level=disabled",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(tmp),
        ],
        check=True,
    )
    tmp.replace(wav)


def _loudness(path: Path) -> float | None:
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    for line in proc.stderr.splitlines():
        if line.strip().startswith("I:"):
            try:
                return float(line.split()[-2])
            except (ValueError, IndexError):
                return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("mmaudio")
    ap.add_argument("out_dir", nargs="?", default="output/audio/variants")
    ap.add_argument("--only", nargs="*", default=[])
    args = ap.parse_args()

    from content_factory.qc.media import ffprobe

    video, mm = Path(args.video), Path(args.mmaudio)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = float(ffprobe(video)["format"]["duration"])
    chosen = {k: v for k, v in VARIANTS.items() if not args.only or k in args.only}
    for name, spec in chosen.items():
        render(name, spec, video, mm, duration, out_dir)
        print(f"{name:<18} {spec['why']}", flush=True)
    print(f"\n{len(chosen)} variants -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
