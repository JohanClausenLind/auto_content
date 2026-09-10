"""Build the video SFX + ambience library from library.json into assets/sfx/.

    uv run --project skills/audio/sfx python skills/audio/sfx/build_library.py [--only id,id] [--device cpu]

Deterministic: every sound carries a fixed seed, so the whole library is reproducible from
library.json alone. Writes 24-bit FLAC at 44.1 kHz stereo through index.py, which re-renders the
sha256-pinned manifest.json and the browsable README.md over the whole library -- including the
recorded half that ingest_recorded.py cuts from a licensed bundle. Running this builder does not
disturb those entries and does not require the bundle to be present.

Rationale for every number here: docs/research/2026-09-07-video-sfx-and-ambience-library.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import index
import numpy as np
import sfx

HERE = Path(__file__).resolve().parent
REPO_ROOT = sfx.REPO_ROOT
OUT_ROOT = REPO_ROOT / "assets" / "sfx"

# QC thresholds. Flags are advisory: they name the sounds worth listening to first, they do not
# fail the build. Sourced reasoning in the research doc, section B and C.
HARSH_RATIO_MAX = 0.35  # 2-5 kHz share of total energy; sharpness proxy
SEAM_RMS_MAX_DB = 3.0  # level jump across the wrap
SILENT_DBFS = -40.0  # a generation that produced nothing
FLATNESS_MAX = 0.15  # above this a one-shot is broadband hiss, not a designed sound
TILT_MAX_DB = 0.0  # HF-rising spectrum: the other half of the same hiss signature
# Loudest half-second over the median half-second. A bed above this holds a recognisable event, and
# a recognisable event inside a loop is exactly what gives the loop away on the second pass.
EVENT_PROMINENCE_MAX_DB = 10.0

# Never ask small-sfx for less than this, however short the finished sound is. Measured over three
# prompts x eight seeds: 0/8 takes usable at 0.8 s, 7/8 at 1.5 s, 8/8 at 2.5 s, 8/8 at 4.0 s. Short
# requests are out of distribution and come back as broadband hiss. The requested duration is a
# generation parameter; the length of the finished file is set by trimming to the event.
MIN_GEN_S = 3.0


def make_candidate(model, spec: dict, defaults: dict, steps: int, seed: int):
    """Generate one take and run all the DSP, but not the levelling. Returns (audio, qc).

    Levelling is deliberately left out: it costs an ffmpeg round-trip per measurement, and every
    metric used to choose between takes is scale-invariant, so only the winner needs it.
    """
    sr = sfx.SAMPLE_RATE
    loop = spec.get("loop")
    hp = spec.get("highpass_hz", defaults["highpass_hz"])

    if loop:
        lead, pad = defaults["lead_in_s"], defaults["tail_pad_s"]
        length_s, cf_s = float(loop["length_s"]), float(loop["crossfade_s"])
        raw = sfx.generate(model, spec["prompt"], lead + length_s + cf_s + pad, seed, steps)
        # Drop the model's habitual fade-in at the head and fade-out at the tail before wrapping.
        raw = raw[:, int(lead * sr) : raw.shape[-1] - int(pad * sr)]
        x = sfx.loop_wrap(
            raw.astype(np.float64), sr, length_s, cf_s, spec.get("crossfade_law", "equal_power")
        )
        x = sfx.highpass(x, sr, hp)  # circular -> stays loopable
        qc = {**sfx.tone_qc(x, sr), **sfx.loop_qc(x, sr)}
    else:
        want_s = float(spec["duration_s"])
        raw = sfx.generate(model, spec["prompt"], max(MIN_GEN_S, want_s + 1.0), seed, steps)
        x = sfx.trim_oneshot(raw.astype(np.float64), sr)
        # Asking for 3 s to get a 0.3 s tick means the take can hold more than the tick. Cap what
        # is kept, and fade, so a UI cue stays a UI cue.
        cap = int(max(want_s * 2.0, want_s + 0.5) * sr)
        if x.shape[-1] > cap:
            x = x[:, :cap].copy()
            f = int(0.03 * sr)
            x[:, -f:] *= np.linspace(1.0, 0.0, f)
        x = sfx.highpass_linear(x, sr, hp)  # zero-padded -> no circular fold
        qc = sfx.tone_qc(x, sr)
        if "peak_band_hz" in spec:
            lo, hi = spec["peak_band_hz"]
            qc["band_focus_db"] = sfx.band_focus_db(x, sr, lo, hi)
    return x, qc


def score(spec: dict, qc: dict) -> float:
    """Lower is better. Two different questions for the two families.

    One-shot: is it a designed sound at all (flatness, HF tilt), and is it the sound that was asked
    for (energy in the intended band)? Both matter -- a flawless sub rumble scored as an "airy
    whoosh" is still the wrong file.

    Bed: will it survive being repeated? A distinct event dominates that judgement, a level
    mismatch across the wrap pumps once per cycle, and harshness above the threshold is penalised
    because a bed is on screen for minutes at a time.
    """
    if spec.get("loop"):
        return (
            qc["event_prominence_db"]
            + 2.0 * abs(qc["seam_rms_delta_db"])
            + 20.0 * max(0.0, qc["harsh_band_ratio"] - HARSH_RATIO_MAX)
        )
    return (
        10.0 * qc["spectral_flatness"]
        + 0.5 * max(0.0, qc["hf_tilt_db"])
        + qc.get("band_focus_db", 0.0)
    )


def build_one(
    model, spec: dict, defaults: dict, targets: dict, steps: int, n_candidates: int
) -> dict:
    sr = sfx.SAMPLE_RATE
    loop = spec.get("loop")
    t0 = time.time()

    takes = []
    for k in range(n_candidates):
        seed = int(spec["seed"]) + k
        x, qc = make_candidate(model, spec, defaults, steps, seed)
        takes.append((score(spec, qc), seed, x, qc))
    takes.sort(key=lambda t: t[0])
    best_score, seed, x, qc = takes[0]

    if loop:
        t = targets["bed"]
        x, _before, after = sfx.normalize(x, sr, "integrated_lufs", t["value"], t["true_peak_dbtp"])
        qc = {**sfx.tone_qc(x, sr), **sfx.loop_qc(x, sr)}
    else:
        t = targets["oneshot"]
        x, _before, after = sfx.normalize(
            x, sr, "max_momentary_lufs", t["value"], t["true_peak_dbtp"]
        )
        qc = {**qc, **sfx.tone_qc(x, sr)}

    rel = Path(spec["category"]) / f"{spec['id']}.flac"
    path = OUT_ROOT / rel
    sfx.write_flac(path, x, sr)

    crest_db = after["true_peak_dbtp"] - after["integrated_lufs"]
    qc["crest_factor_db"] = round(crest_db, 2)

    flags = []
    if qc["harsh_band_ratio"] > HARSH_RATIO_MAX:
        flags.append("harsh_band")
    if after["sample_peak_dbfs"] < SILENT_DBFS:
        flags.append("near_silent")
    if loop:
        if abs(qc["seam_rms_delta_db"]) > SEAM_RMS_MAX_DB:
            flags.append("seam_level_jump")
        if qc["event_prominence_db"] > EVENT_PROMINENCE_MAX_DB:
            flags.append("distinct_event")
    else:
        # A handful of one-shots have a genuinely broadband source -- paper rustle, a glass
        # shimmer -- and read as "hiss" under the blanket threshold. Those entries carry their own
        # limits in library.json rather than switching the check off.
        if qc["spectral_flatness"] > spec.get("max_flatness", FLATNESS_MAX) or qc[
            "hf_tilt_db"
        ] > spec.get("max_tilt_db", TILT_MAX_DB):
            flags.append("noise_like")

    return {
        "id": spec["id"],
        "category": spec["category"],
        "file": str(rel).replace("\\", "/"),
        "source": index.SOURCE_GENERATED,
        "tags": spec["tags"],
        "use": spec["use"],
        "loopable": bool(loop),
        "duration_s": round(x.shape[-1] / sr, 6),
        "sample_rate": sr,
        "channels": int(x.shape[0]),
        "bit_depth": 24,
        "sha256": sfx.sha256(path),
        "bytes": path.stat().st_size,
        "prompt": spec["prompt"],
        "seed": seed,
        "seed_base": int(spec["seed"]),
        "candidates_auditioned": n_candidates,
        "selection_score": round(best_score, 3),
        "steps": steps,
        "loudness": {
            "integrated_lufs": round(after["integrated_lufs"], 2),
            "max_momentary_lufs": round(after["max_momentary_lufs"], 2),
            "true_peak_dbtp": round(after["true_peak_dbtp"], 2),
            "loudness_range_lu": round(after["loudness_range_lu"], 2),
            "gain_applied_db": after["gain_db"],
            "gain_limited_by": after["gain_limited_by"],
        },
        "qc": qc,
        "flags": flags,
        "generate_s": round(time.time() - t0, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", type=Path, default=HERE / "library.json")
    ap.add_argument("--only", default="", help="comma-separated ids to (re)build")
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    ap.add_argument("--list", action="store_true", help="print the plan and exit")
    args = ap.parse_args()

    lib = json.loads(args.library.read_text())
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    specs = [s for s in lib["sounds"] if not wanted or s["id"] in wanted]
    if wanted - {s["id"] for s in specs}:
        raise SystemExit(f"unknown ids: {sorted(wanted - {s['id'] for s in specs})}")

    if args.list:
        for s in specs:
            kind = (
                f"loop {s['loop']['length_s']}s/xf{s['loop']['crossfade_s']}s"
                if "loop" in s
                else f"one-shot {s['duration_s']}s"
            )
            print(f"{s['id']:24s} {s['category']:12s} {kind}")
        print(f"\n{len(specs)} sounds")
        return 0

    model = sfx.load_model(device=args.device, half=(args.device == "cuda"))
    print(f"model loaded on {args.device}\n", file=sys.stderr)

    existing, prev_sources = index.load(OUT_ROOT / "manifest.json")

    built = []
    for i, spec in enumerate(specs, 1):
        n = spec.get("candidates", lib["candidates"]["bed" if "loop" in spec else "oneshot"])
        entry = build_one(model, spec, lib["defaults"], lib["targets"], lib["steps"], n)
        built.append(entry)
        existing[entry["id"]] = entry
        flag = " ".join(entry["flags"]) or "-"
        print(
            f"[{i:2d}/{len(specs)}] {entry['id']:24s} {entry['duration_s']:6.2f}s "
            f"I={entry['loudness']['integrated_lufs']:7.2f} M={entry['loudness']['max_momentary_lufs']:7.2f} "
            f"TP={entry['loudness']['true_peak_dbtp']:6.2f} flat={entry['qc']['spectral_flatness']:.3f} "
            f"tilt={entry['qc']['hf_tilt_db']:+6.1f} seed={entry['seed']} "
            f"{entry['generate_s']:5.1f}s  {flag}",
            file=sys.stderr,
            flush=True,
        )

    manifest, missing, duplicated = index.write(
        OUT_ROOT,
        existing,
        lib["targets"],
        index.SOURCE_GENERATED,
        {
            "generator": "skills/audio/sfx/build_library.py",
            "recipe": "skills/audio/sfx/library.json",
            "model": lib["model"],
            "model_id": sfx.MODEL_ID,
            "licence": "Stability AI Community License (weights); Gemma Terms of Use (T5Gemma text encoder)",
        },
        prev_sources,
    )

    flagged = [e["id"] for e in manifest["sounds"] if e["flags"]]
    total_mb = sum(e["bytes"] for e in manifest["sounds"]) / 1e6
    print(
        f"\n{manifest['count']} sounds ({manifest['count_by_source']}), "
        f"{total_mb:.1f} MB -> {OUT_ROOT}",
        file=sys.stderr,
    )
    print(f"flagged: {', '.join(flagged) if flagged else 'none'}", file=sys.stderr)
    if missing:
        print(f"MISSING FILES (in manifest, not on disk): {', '.join(missing)}", file=sys.stderr)
    if duplicated:
        for ids in duplicated:
            print(f"DUPLICATE BYTES (one sound, several ids): {', '.join(ids)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
