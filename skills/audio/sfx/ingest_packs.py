"""Ingest a pack of individually licensed sound files into assets/sfx.

    uv run --project skills/audio/sfx python skills/audio/sfx/ingest_packs.py \
        --recipe skills/audio/sfx/mixkit.json [--only id,id] [--analyze] [--list]

The third builder, after `build_library.py` (generated) and `ingest_recorded.py` (excerpts cut
from the #GameAudioGDC bundle). All three write the same format to the same directory through the
same index writer -- 44.1 kHz stereo 24-bit FLAC, the same two loudness targets, the same loop
wrap, the same QC fields -- so a sound built here is indistinguishable in use from one built
there.

What differs is what has to be *chosen*. A generation picks between eight seeds of one prompt; an
excerpt picks between every 30 s window of a 680 s recording. A pack file has already been chosen:
a stock supplier cut it, trimmed it and named it, and second-guessing that with an event gate
throws away the edit you are licensing. So the default mode here is `whole` -- take the file as
delivered, trim the silence around it, high-pass, level -- and the only decision left is which of
the two loudness families it belongs to.

    whole                 one-shot target, measured on max-momentary
    whole + bed:true      integrated target, for a long continuous texture that does not loop
    whole + max_s: N      cap and fade at N seconds, for a delivered file that is a reel of takes
    loop {length_s, ...}  bed target, and the same window sweep the recorded half uses -- a long
                          ambience still has to be searched for the window that wraps cleanly

Provenance is per pack, not per file: every sound in a pack shares one licence, which the recipe
states and every entry repeats, because the licence is the first thing a reader of the manifest
needs and the last thing that should require a second lookup.

Rationale for every number: docs/research/2026-09-07-video-sfx-and-ambience-library.md
Pack licences: docs/research/2026-09-09-mixkit-sfx-pack-licence.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import index
import recorded
import sfx

HERE = Path(__file__).resolve().parent
REPO_ROOT = sfx.REPO_ROOT
OUT_ROOT = REPO_ROOT / "assets" / "sfx"

# Advisory thresholds. Same contract as the other two builders: a flag names the sounds worth
# listening to first, it does not fail the build. The set is the recorded one, for the same
# reasons -- a commercial recording cannot fail the way a generation fails, and `truncated` and
# `clipped` are how an already-cut file actually goes wrong.
HARSH_RATIO_MAX = 0.35
SEAM_RMS_MAX_DB = 3.0
SILENT_DBFS = -40.0
TRUNCATION_FLOOR_DB = -30.0  # a cap landing above this is an audible cut, not a spent tail
CLIP_DBFS = -0.1


def build_one(spec: dict, lib: dict, root: Path, analyze: bool) -> dict:
    sr = lib["sample_rate"]
    defaults = lib["defaults"]
    targets = lib["targets"]
    pack = lib["pack"]
    src_path = root / spec["src"]["pack"] / spec["src"]["file"]
    if not src_path.is_file():
        raise SystemExit(f"{spec['id']}: source not found: {src_path}\nRun stage_pack.py first.")
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
        metric = "integrated_lufs"
    else:
        whole = spec["whole"]
        x, cut = recorded.cut_whole(src_path, sr, hp, whole.get("max_s"), whole.get("trim", True))
        qc = sfx.tone_qc(x, sr)
        if "peak_band_hz" in spec:
            lo, hi = spec["peak_band_hz"]
            qc["band_focus_db"] = sfx.band_focus_db(x, sr, lo, hi)
        sel = 0.0
        bed = bool(whole.get("bed"))
        t = targets["bed"] if bed else targets["oneshot"]
        metric = "integrated_lufs" if bed else "max_momentary_lufs"

    if analyze:
        after = sfx.measure(x, sr)
        after["gain_db"] = 0.0
        after["gain_limited_by"] = "analyze"
    else:
        x, _before, after = sfx.normalize(x, sr, metric, t["value"], t["true_peak_dbtp"])
        qc = {**qc, **sfx.tone_qc(x, sr)}
        if loop:
            qc = {**qc, **sfx.loop_qc(x, sr)}

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
        if abs(qc.get("seam_rms_delta_db", 0.0)) > SEAM_RMS_MAX_DB:
            flags.append("seam_level_jump")
        if qc.get("event_prominence_db", 0.0) > index.EVENT_PROMINENCE_LOUD:
            flags.append("distinct_event")
    else:
        if cut["truncated_at_db"] is not None and cut["truncated_at_db"] > TRUNCATION_FLOOR_DB:
            flags.append("truncated")
        if cut["source_peak_dbfs"] > CLIP_DBFS:
            flags.append("clipped")

    entry = {
        "id": spec["id"],
        "category": spec["category"],
        "file": str(rel).replace("\\", "/"),
        "source": pack["id"],
        "tags": spec["tags"],
        "use": spec["use"],
        "loopable": bool(loop),
        "duration_s": round(x.shape[-1] / sr, 6),
        "sample_rate": sr,
        "channels": int(x.shape[0]),
        "bit_depth": 24,
        "sha256": sfx.sha256(path) if not analyze else "",
        "bytes": path.stat().st_size if not analyze else 0,
        "ingested_from": {
            "pack": pack["id"],
            "file": spec["src"]["file"],
            "library": pack["name"],
            "supplier": pack["supplier"],
            "url": pack.get("licence_url", ""),
            "licence": pack["licence"],
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


def _size(n_bytes: int) -> str:
    """A pack can be 0.7 GB or 14 MB; "0.0 GB" tells a reader nothing about the small one."""
    return f"{n_bytes / 1e9:.1f} GB" if n_bytes >= 1e8 else f"{n_bytes / 1e6:.0f} MB"


def pack_source_info(pack: dict, recipe: Path, root: Path) -> dict:
    total = sum(p.stat().st_size for p in root.rglob("*.wav") if "_duplicates" not in p.parts)
    return {
        "generator": "skills/audio/sfx/ingest_packs.py",
        "recipe": str(recipe).replace("\\", "/"),
        "stager": "skills/audio/sfx/stage_pack.py",
        "bundle": pack["name"],
        "bundle_size": _size(total),
        "bundle_path": str(root),
        "env_var": pack["root_env"],
        "supplier": pack["supplier"],
        "licence": pack["licence"],
        "licence_url": pack.get("licence_url", ""),
        "licence_retrieved": pack.get("licence_retrieved", ""),
        "headline": pack["headline"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", type=Path, required=True)
    ap.add_argument("--only", default="", help="comma-separated ids to (re)build")
    ap.add_argument("--analyze", action="store_true", help="select and measure, write nothing")
    ap.add_argument("--list", action="store_true", help="print the plan and exit")
    args = ap.parse_args()

    lib = json.loads(args.recipe.read_text())
    if "pack" not in lib:
        raise SystemExit(f"{args.recipe} has no `pack` block: not a pack recipe")
    pack = lib["pack"]
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    specs = [s for s in lib["sounds"] if not wanted or s["id"] in wanted]
    if wanted - {s["id"] for s in specs}:
        raise SystemExit(f"unknown ids: {sorted(wanted - {s['id'] for s in specs})}")

    if args.list:
        for s in specs:
            if "loop" in s:
                kind = f"loop {s['loop']['length_s']}s/xf{s['loop']['crossfade_s']}s"
            elif s["whole"].get("bed"):
                kind = "whole (bed level)"
            elif s["whole"].get("max_s"):
                kind = f"whole <={s['whole']['max_s']}s"
            else:
                kind = "whole"
            print(f"{s['id']:26s} {s['category']:11s} {kind:26s} {s['src']['file'][:60]}")
        print(f"\n{len(specs)} sounds")
        return 0

    root = recorded.pack_root(pack)
    print(f"pack: {pack['name']} at {root}\n", file=sys.stderr)

    built = []
    for i, spec in enumerate(specs, 1):
        entry = build_one(spec, lib, root, args.analyze)
        built.append(entry)
        src, ldn, qc = entry["ingested_from"], entry["loudness"], entry["qc"]
        if entry["loopable"]:
            where = (
                f"@{src['window_start_s']:7.2f}s/{src['source_duration_s']:6.1f}s"
                f" of {src['windows_auditioned']:4d}"
            )
            loopy = (
                f"ev={qc.get('event_prominence_db', 0.0):5.1f} "
                f"seam={qc.get('seam_rms_delta_db', 0.0):+5.2f}"
            )
        else:
            where = f"whole -{src['silence_trimmed_s']:5.2f}s silence"
            loopy = ""
        print(
            f"[{i:3d}/{len(specs)}] {entry['id']:26s} {entry['duration_s']:6.2f}s "
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
        pack["id"],
        pack_source_info(pack, args.recipe, root),
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
