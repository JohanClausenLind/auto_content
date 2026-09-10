"""Cut the recorded half of assets/sfx out of the local #GameAudioGDC bundle.

    uv run --project skills/audio/sfx python skills/audio/sfx/ingest_recorded.py [--only id,id] [--analyze]

Companion to build_library.py, which generates the other half with Stable Audio 3 Small-SFX. The
two write the same format to the same directory through the same index writer, and a sound built
here is indistinguishable in use from one generated there: 44.1 kHz stereo 24-bit FLAC, the same
two loudness targets, the same loop wrap, the same QC fields in the manifest.

What differs is only where the audio comes from and therefore what has to be chosen. A generation
picks between eight seeds of the same prompt; an excerpt picks between every 30 s window of a
680 s recording, or between the sixty ticks in a minute of clock. Both are decided by measurement
and both record what they picked, so `just` can rebuild either half byte-for-byte.

`--analyze` runs the whole selection and prints what it would write without touching the library:
use it to check a pick before committing 40 MB of FLAC to it.

Rationale for every number: docs/research/2026-09-07-video-sfx-and-ambience-library.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import index
import numpy as np
import recorded
import sfx

HERE = Path(__file__).resolve().parent
REPO_ROOT = sfx.REPO_ROOT
OUT_ROOT = REPO_ROOT / "assets" / "sfx"

# Advisory thresholds, same contract as the generated half: a flag names the sounds worth
# listening to first, it does not fail the build. The set is not the same set, because the two
# halves cannot fail the same way.
#
# Dropped: `noise_like`. On the generated side high spectral flatness with a rising HF tilt is the
# signature of a take that came back as broadband hiss instead of a designed sound. A commercial
# recording cannot fail that way, and the measurement says so plainly -- a bright UI pop measures
# +35 dB of tilt and a corrupted-data glitch 0.78 flatness, and both are exactly the sound their
# supplier named. Flatness and tilt are still recorded in the manifest as description; they are
# just not evidence of anything here. Keeping the flag would have meant ten per-entry overrides
# to say "this one is allowed to be what it is", which is a check that has stopped checking.
#
# Added: `truncated` and `clipped`, which are how an *excerpt* actually goes wrong. `truncated`
# catches a max_s that ends a sound while it is still audible -- the mistake that would otherwise
# have shipped a 16 s horn braam cut off before its peak. `clipped` catches a source mastered into
# the ceiling, where the excerpt has no headroom to level from.
HARSH_RATIO_MAX = 0.35
SEAM_RMS_MAX_DB = 3.0
SILENT_DBFS = -40.0
EVENT_PROMINENCE_MAX_DB = 10.0
TRUNCATION_FLOOR_DB = -30.0  # a cap landing above this is an audible cut, not a spent tail
CLIP_DBFS = -0.1


def build_one(spec: dict, lib: dict, root: Path, credits: dict, analyze: bool) -> dict:
    sr = lib["sample_rate"]
    defaults = lib["defaults"]
    targets = lib["targets"]
    src_path = root / spec["src"]["pack"] / spec["src"]["file"]
    if not src_path.is_file():
        raise SystemExit(f"{spec['id']}: source not found: {src_path}")
    t0 = time.time()

    info = recorded.probe(src_path)
    hp = spec.get("highpass_hz", defaults["highpass_hz"])
    loop = spec.get("loop")
    cut: dict = {}

    if loop:
        search = {**defaults["search"], **spec.get("search", {})}
        x, qc, start_s, sel, n_windows = recorded.pick_window(
            src_path,
            sr,
            float(loop["length_s"]),
            float(loop["crossfade_s"]),
            spec.get("crossfade_law", defaults["crossfade_law"]),
            hp,
            float(search["hop_s"]),
            int(search["finalists"]),
        )
        cut = {
            "mode": "loop",
            "window_start_s": round(start_s, 3),
            "window_length_s": float(loop["length_s"]),
            "crossfade_s": float(loop["crossfade_s"]),
            "crossfade_law": spec.get("crossfade_law", defaults["crossfade_law"]),
            "windows_auditioned": n_windows,
        }
        t = targets["bed"]
        if not analyze:
            x, _b, after = sfx.normalize(x, sr, "integrated_lufs", t["value"], t["true_peak_dbtp"])
            qc = {**sfx.tone_qc(x, sr), **sfx.loop_qc(x, sr)}
    elif "whole" in spec:
        # The supplier already cut this file. `recorded.cut_whole` takes it as delivered and the
        # only decision left is which loudness family it belongs to -- see ingest_packs.py, which
        # uses the same helper for the same reason.
        whole = spec["whole"]
        x, cut = recorded.cut_whole(src_path, sr, hp, whole.get("max_s"), whole.get("trim", True))
        qc = sfx.tone_qc(x, sr)
        if "peak_band_hz" in spec:
            lo, hi = spec["peak_band_hz"]
            qc["band_focus_db"] = sfx.band_focus_db(x, sr, lo, hi)
        sel = 0.0
        bed = bool(whole.get("bed"))
        t = targets["bed"] if bed else targets["oneshot"]
        if not analyze:
            x, _b, after = sfx.normalize(
                x,
                sr,
                "integrated_lufs" if bed else "max_momentary_lufs",
                t["value"],
                t["true_peak_dbtp"],
            )
            qc = {**qc, **sfx.tone_qc(x, sr)}
    else:
        one = {**defaults["oneshot"], **spec["oneshot"]}
        raw = recorded.decode(src_path, sr)
        source_peak_dbfs = recorded.db(float(np.abs(raw).max()))
        picked, ev_idx, n_events = recorded.pick_event(
            raw, sr, int(one["event"]), float(one["min_gap_s"]), float(one["gate_db"])
        )
        x = sfx.trim_oneshot(picked, sr)
        x, trunc_db = recorded.cap_and_fade(x, sr, float(one["max_s"]))
        x = sfx.highpass_linear(x, sr, hp)  # zero-padded -> no circular fold
        qc = sfx.tone_qc(x, sr)
        if "peak_band_hz" in spec:
            lo, hi = spec["peak_band_hz"]
            qc["band_focus_db"] = sfx.band_focus_db(x, sr, lo, hi)
        cut = {
            "mode": "event",
            "event_index": ev_idx,
            "events_found": n_events,
            "gate_db": float(one["gate_db"]),
            "min_gap_s": float(one["min_gap_s"]),
            "max_s": float(one["max_s"]),
            "truncated_at_db": trunc_db,
            "source_peak_dbfs": source_peak_dbfs,
        }
        sel = 0.0
        t = targets["oneshot"]
        if not analyze:
            x, _b, after = sfx.normalize(
                x, sr, "max_momentary_lufs", t["value"], t["true_peak_dbtp"]
            )
            qc = {**qc, **sfx.tone_qc(x, sr)}

    if analyze:
        after = sfx.measure(x, sr)
        after["gain_db"] = 0.0
        after["gain_limited_by"] = "analyze"

    rel = Path(spec["category"]) / f"{spec['id']}.flac"
    path = OUT_ROOT / rel
    if not analyze:
        sfx.write_flac(path, x, sr)

    qc["crest_factor_db"] = round(after["true_peak_dbtp"] - after["integrated_lufs"], 2)

    flags = []
    if qc["harsh_band_ratio"] > spec.get("max_harsh_ratio", HARSH_RATIO_MAX):
        flags.append("harsh_band")
    if after["sample_peak_dbfs"] < SILENT_DBFS:
        flags.append("near_silent")
    if loop:
        if abs(qc["seam_rms_delta_db"]) > SEAM_RMS_MAX_DB:
            flags.append("seam_level_jump")
        if qc["event_prominence_db"] > EVENT_PROMINENCE_MAX_DB:
            flags.append("distinct_event")
    else:
        if cut["truncated_at_db"] is not None and cut["truncated_at_db"] > TRUNCATION_FLOOR_DB:
            flags.append("truncated")
        if cut["source_peak_dbfs"] > CLIP_DBFS:
            flags.append("clipped")

    cred = credits.get(spec["src"]["file"], {})
    entry = {
        "id": spec["id"],
        "category": spec["category"],
        "file": str(rel).replace("\\", "/"),
        "source": index.SOURCE_RECORDED,
        "tags": spec["tags"],
        "use": spec["use"],
        "loopable": bool(loop),
        "duration_s": round(x.shape[-1] / sr, 6),
        "sample_rate": sr,
        "channels": int(x.shape[0]),
        "bit_depth": 24,
        "sha256": sfx.sha256(path) if not analyze else "",
        "bytes": path.stat().st_size if not analyze else 0,
        "recorded_from": {
            "pack": spec["src"]["pack"],
            "file": spec["src"]["file"],
            "library": cred.get("library", ""),
            "supplier": cred.get("supplier", ""),
            "url": cred.get("url", ""),
            "source_sample_rate": info["sample_rate"],
            "source_channels": info["channels"],
            "source_duration_s": round(info["duration_s"], 3),
            "source_sha256": recorded.source_sha256(src_path),
            **cut,
        },
        "replaces": spec.get("replaces", ""),
        "selection_score": round(sel, 3),
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
        "ingest_s": round(time.time() - t0, 2),
    }
    if not entry["replaces"]:
        del entry["replaces"]
    return entry


def recorded_source_info(root: Path) -> dict:
    total = sum(p.stat().st_size for p in root.rglob("*.wav"))
    return {
        "generator": "skills/audio/sfx/ingest_recorded.py",
        "recipe": "skills/audio/sfx/recorded.json",
        "bundle": "Sonniss #GameAudioGDC Bundle 2026 (Part 9)",
        "bundle_size": f"{total / 1e9:.1f} GB",
        "bundle_path": str(root),
        "env_var": "CF_SONNISS_GDC_DIR",
        "licence": recorded.BUNDLE_LICENCE,
        "supplier": "",  # eleven of them; the supplier table in the README names each
        "headline": "cut from the #GameAudioGDC bundle",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", type=Path, default=HERE / "recorded.json")
    ap.add_argument("--only", default="", help="comma-separated ids to (re)cut")
    ap.add_argument("--analyze", action="store_true", help="select and measure, write nothing")
    ap.add_argument("--list", action="store_true", help="print the plan and exit")
    args = ap.parse_args()

    lib = json.loads(args.recipe.read_text())
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    specs = [s for s in lib["sounds"] if not wanted or s["id"] in wanted]
    if wanted - {s["id"] for s in specs}:
        raise SystemExit(f"unknown ids: {sorted(wanted - {s['id'] for s in specs})}")

    if args.list:
        for s in specs:
            if "loop" in s:
                kind = f"loop {s['loop']['length_s']}s/xf{s['loop']['crossfade_s']}s"
            elif "whole" in s:
                cap = s["whole"].get("max_s")
                level = "bed" if s["whole"].get("bed") else "one-shot"
                kind = f"whole {level}" + (f" <={cap}s" if cap else "")
            else:
                kind = f"event <={s['oneshot']['max_s']}s ev{s['oneshot'].get('event', -1)}"
            print(
                f"{s['id']:22s} {s['category']:11s} {kind:26s} "
                f"{'replaces generated' if s.get('replaces') else 'new':18s} {s['src']['file'][:64]}"
            )
        print(f"\n{len(specs)} sounds")
        return 0

    root = recorded.bundle_root()
    credits = recorded.credits(root)
    print(f"bundle: {root}  ({len(credits)} files in its tracklist)\n", file=sys.stderr)

    built = []
    for i, spec in enumerate(specs, 1):
        entry = build_one(spec, lib, root, credits, args.analyze)
        built.append(entry)
        src, ldn, qc = entry["recorded_from"], entry["loudness"], entry["qc"]
        if entry["loopable"]:
            where = (
                f"@{src['window_start_s']:7.2f}s/{src['source_duration_s']:6.1f}s"
                f" of {src['windows_auditioned']:4d}"
            )
            loopy = f"ev={qc['event_prominence_db']:5.1f} seam={qc['seam_rms_delta_db']:+5.2f}"
        elif src.get("mode") == "whole":
            where = f"whole -{src['silence_trimmed_s']:5.2f}s silence"
            loopy = ""
        else:
            where = f"event {src['event_index'] + 1} of {src['events_found']}"
            loopy = ""
        print(
            f"[{i:2d}/{len(specs)}] {entry['id']:22s} {entry['duration_s']:6.2f}s "
            f"{where:28s} I={ldn['integrated_lufs']:7.2f} M={ldn['max_momentary_lufs']:7.2f} "
            f"TP={ldn['true_peak_dbtp']:6.2f} flat={qc['spectral_flatness']:.3f} "
            f"tilt={qc['hf_tilt_db']:+6.1f} {loopy:24s} "
            f"{entry['ingest_s']:5.1f}s  {' '.join(entry['flags']) or '-'}",
            file=sys.stderr,
            flush=True,
        )

    if args.analyze:
        print("\n--analyze: nothing written", file=sys.stderr)
        return 0

    entries, prev_sources = index.load(OUT_ROOT / "manifest.json")
    for e in built:
        entries[e["id"]] = e

    manifest, missing, duplicated = index.write(
        OUT_ROOT,
        entries,
        lib["targets"],
        index.SOURCE_RECORDED,
        recorded_source_info(root),
        prev_sources,
    )

    flagged = [e["id"] for e in manifest["sounds"] if e["flags"]]
    total_mb = sum(e["bytes"] for e in manifest["sounds"]) / 1e6
    print(
        f"\n{manifest['count']} sounds ({manifest['count_by_source']}), {total_mb:.1f} MB -> {OUT_ROOT}",
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
