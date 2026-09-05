"""Build the background-music library from library.json into assets/music/.

    uv run --project skills/audio/music python skills/audio/music/build_library.py [--only id,id]

Starts the HOT-Step engine against models/music/MiniMax-Music3-GGUF if it is not already running,
renders each track through POST /mm3/synth, levels it, measures it and writes 24-bit FLAC plus a
sha256-pinned manifest.json and a browsable README.md -- the same shape as assets/sfx/.

Deterministic: every track carries a fixed seed, so the library is reproducible from library.json.

Rationale for every number here: docs/research/2026-09-07-background-music-library.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import mm3

HERE = Path(__file__).resolve().parent
OUT_ROOT = mm3.REPO_ROOT / "assets" / "music"
sfx = mm3.sfx

# QC thresholds. Advisory: they name the tracks to listen to first, they do not fail the build.
PRESENCE_MAX = 0.30  # 1-4 kHz share. Above this the bed competes with narration whatever you do.
HARSH_MAX = 0.30  # 2-5 kHz share; sharpness proxy, shared with the sfx library
SILENT_DBFS = -40.0


def render(spec: dict, lib: dict, port: int, seed: int, log=print):
    """One take: generate, then all the DSP except levelling.

    The requested duration is only a CAP on the AR loop -- MM3 is not conditioned on length at all
    and stops when it stops (measured: 19-62% of the cap for these captions, and no sampler setting
    moves it). So we ask for `cap_s`, take what arrives, and fit the loop to it.
    """
    sr = mm3.SAMPLE_RATE
    d = lib["defaults"]
    loop = spec.get("loop")

    t0 = time.time()
    raw, info = mm3.generate(
        spec["caption"],
        lib["cap_s"],
        seed,
        port=port,
        steps=lib["steps"],
        cfg_flow=lib["cfg_flow"],
        wav_bits=lib["wav_bits"],
        on_progress=lambda i: log(
            f"        {i.get('stage', '?'):>5s} win {i.get('window', 0) + 1}/{i.get('n_windows', 1)}"
            f" step {i.get('step', 0)}/{i.get('n_steps', 0)}"
        ),
    )
    gen_time = time.time() - t0
    raw_s = raw.shape[-1] / sr

    if loop:
        # Trim the model's fade-in and fade-out away from the wrap, then take the longest loop the
        # take can support, capped at what the entry asks for.
        body = raw[:, int(d["lead_in_s"] * sr) : raw.shape[-1] - int(d["tail_pad_s"] * sr)]
        cf = float(loop["crossfade_s"])
        # Leave slack so best_loop_window has somewhere to slide. Taking the longest loop the take
        # can support leaves exactly one candidate window, which is how the first attempt ended up
        # with a 7 dB level jump across the wrap: for a pad, a shorter loop with a clean seam beats
        # a longer one that pumps every cycle.
        slack = float(loop.get("search_slack_s", 8.0))
        avail = body.shape[-1] / sr - cf
        length = min(float(loop["max_length_s"]), max(avail - slack, float(loop["min_length_s"])))
        if length > avail or length < float(loop["min_length_s"]):
            return None, info, gen_time, raw_s, f"take too short to loop ({raw_s:.1f}s)"
        x = sfx.loop_wrap(body, sr, length, cf, spec.get("crossfade_law", "equal_power"))
        x = sfx.highpass(x, sr, d["highpass_hz"])  # circular -> stays loopable
    else:
        x = mm3.trim_head(raw, sr)
        x = sfx.highpass_linear(x, sr, d["highpass_hz"])
        x = mm3.fade(x, sr, d["fade_in_s"], d["fade_out_s"])
    return x, info, gen_time, raw_s, None


def score(spec: dict, x, qc: dict, sr: int) -> float:
    """Lower is better. Loops and one-shot cues are judged on different things.

    A **cue** is judged on length first, because length is the one property no request field
    controls (see the research doc G1-G2): a 55 s bed covers a segment, a 20 s one is barely a
    sting. Presence-band energy and harshness are tie-breakers.

    A **loop** is not judged on length at all -- it wraps, so it is as long as the editor wants.
    What matters is that the wrap is inaudible and the material stays put: seam level match, then
    how little it travels, then how much it stays out of the narrator's band.
    """
    if spec.get("loop"):
        # Length is not the goal for a loop, but it is not irrelevant either: a short take leaves
        # best_loop_window no offsets to choose between, which is how two textures ended up as 14 s
        # loops with a level jump across the wrap. A mild length term buys the search room without
        # letting length outrank the seam.
        return (
            2.0 * abs(qc["seam_rms_delta_db"])
            + 1.0 * qc["event_prominence_db"]
            + 1.0 * qc["slow_envelope_range_db"]
            + 40.0 * max(0.0, qc["presence_band_ratio"] - 0.20)
            - 0.15 * (x.shape[-1] / sr)
        )
    seconds = x.shape[-1] / sr
    return (
        -seconds
        + 40.0 * max(0.0, qc["presence_band_ratio"] - 0.20)
        + 20.0 * max(0.0, qc["harsh_band_ratio"] - HARSH_MAX)
    )


def build_one(spec: dict, lib: dict, port: int, log=print) -> dict:
    sr = mm3.SAMPLE_RATE
    loop = spec.get("loop")
    n = int(spec.get("candidates", lib["candidates"]))

    takes, gen_total, raw_lens, why = [], 0.0, [], []
    for k in range(n):
        seed = int(spec["seed"]) + k
        x, info, gt, raw_s, skip = render(spec, lib, port, seed, log)
        gen_total += gt
        raw_lens.append(round(raw_s, 1))
        if skip:
            why.append(f"seed {seed}: {skip}")
            continue
        # Loop candidates are scored on their seam, so the seam metrics have to exist before
        # scoring -- music_qc alone does not carry them.
        cand_qc = mm3.music_qc(x, sr)
        if loop:
            cand_qc.update(sfx.loop_qc(x, sr))
        takes.append((score(spec, x, cand_qc, sr), seed, x, info))
    if not takes:
        raise RuntimeError(f"{spec['id']}: no usable take from {n} seeds -- " + "; ".join(why))
    takes.sort(key=lambda t: t[0])
    _sc, seed, x, info = takes[0]

    t = lib["targets"]["bed"]
    x, _before, after = sfx.normalize(x, sr, "integrated_lufs", t["value"], t["true_peak_dbtp"])

    qc = mm3.music_qc(x, sr)
    if loop:
        qc.update(sfx.loop_qc(x, sr))

    rel = Path(spec["category"]) / f"{spec['id']}.flac"
    path = OUT_ROOT / rel
    sfx.write_flac(path, x, sr)

    seconds = x.shape[-1] / sr
    lo, hi = spec["arc_band_db"]
    arc = qc["slow_envelope_range_db"]
    flags = []
    if qc["presence_band_ratio"] > spec.get("max_presence_ratio", PRESENCE_MAX):
        flags.append("crowds_the_voice")
    if qc["harsh_band_ratio"] > HARSH_MAX:
        flags.append("harsh_band")
    if after["sample_peak_dbfs"] < SILENT_DBFS:
        flags.append("near_silent")
    if seconds < float(spec["min_length_s"]):
        flags.append("short")
    if arc < lo:
        flags.append("too_flat")  # asked for an arc, got a drone
    if arc > hi:
        flags.append("too_dynamic")  # a bed that travels this far fights the edit
    if loop and abs(qc["seam_rms_delta_db"]) > 3.0:
        flags.append("seam_level_jump")

    return {
        "id": spec["id"],
        "category": spec["category"],
        "file": str(rel).replace("\\", "/"),
        "tags": spec["tags"],
        "use": spec["use"],
        "loopable": bool(loop),
        "instrumental": True,
        "duration_s": round(seconds, 6),
        "sample_rate": sr,
        "channels": int(x.shape[0]),
        "bit_depth": 24,
        "sha256": sfx.sha256(path),
        "bytes": path.stat().st_size,
        "caption": spec["caption"],
        "seed": seed,
        "seed_base": int(spec["seed"]),
        "candidates_auditioned": n,
        "candidate_raw_lengths_s": raw_lens,
        "steps": lib["steps"],
        "cfg_flow": lib["cfg_flow"],
        "arc_band_db": spec["arc_band_db"],
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
        "generate_s": round(gen_total, 1),
        "prompt_tokens": info.get("prompt_tokens"),
    }


def render_readme(lib: dict, entries: list[dict]) -> str:
    t = lib["targets"]["bed"]
    total = sum(e["duration_s"] for e in entries)
    lines = [
        "# `assets/music` — non-verbal background music for video",
        "",
        f"{len(entries)} instrumental tracks ({mm3.hhmmss(total)} of audio) generated locally with",
        f"**{lib['model']}** through the HOT-Step engine. 44.1 kHz stereo, 24-bit FLAC.",
        "Nothing here was downloaded; nothing here was uploaded.",
        "",
        "**Do not hand-edit this directory.** It is generated by",
        "`skills/audio/music/build_library.py` from `skills/audio/music/library.json`; every track",
        "has a fixed seed, so the set is reproducible. `manifest.json` carries per-file sha256,",
        "loudness and QC numbers. The design decisions are in",
        "`docs/research/2026-09-07-background-music-library.md`.",
        "",
        "## All of it is underscore",
        "",
        "Every track is **fully instrumental** — no vocals, no vocal samples — and written to sit",
        "*under* narration rather than beside it: slow simple harmony, short motifs instead of",
        "melodies, texture instead of arrangement, and no drum kit anywhere. That is the brief, not",
        "a limitation: a hummable tune competes with a speaking voice for the same attention.",
        "",
        "## Levels and leaving room for the voice",
        "",
        f"Every track is levelled to integrated **{t['value']} LUFS**, ceiling {t['true_peak_dbtp']} dBTP,",
        "with a single constant gain — no compression, no limiting. That sits well under a -14 LUFS",
        "programme, and under the -20..-30 dB that music wants to be at while somebody is speaking.",
        "",
        "`qc.presence_band_ratio` is the number to read before choosing a bed: the share of the",
        "track's energy between 1 kHz and 4 kHz, which is the band a narrator competes for. Lower is",
        "safer under dialogue. **The library is deliberately not EQ-carved** — carving bakes in an",
        "assumption about a particular voice, and the pipeline already ducks music under speech",
        "(sidechain in `add_music_bed`). The measurement is here so you can pick a bed that needs",
        "less of it.",
        "",
        "## Does it move?",
        "",
        "`loudness.loudness_range_lu` (EBU R128 LRA) says whether a track holds one level or travels.",
        "Each entry declares the band it was written for, and QC flags a track that missed it —",
        "`too_flat` for an arc that never arrived, `too_dynamic` for a bed that jumps around under an",
        "edit. Textures sit near 0-3 LU; the arc tracks are the high end.",
        "",
        "## Looping",
        "",
        "Only the `texture` tracks are marked `loopable`, and only they are safe to butt-join: they",
        "are pad and drone material with no phrase structure, so the same tail-over-head wrap the",
        "sfx beds use applies cleanly. **The other tracks are not loops** — music has phrases, and",
        "wrapping one mid-phrase is audible however well the crossfade is done. Use those at their",
        "written length, or cut them on a musical boundary.",
        "",
    ]
    for cat in sorted({e["category"] for e in entries}):
        rows = sorted([e for e in entries if e["category"] == cat], key=lambda r: r["id"])
        lines += [
            f"## {cat}",
            "",
            "| id | file | length | LRA | 1-4 kHz | use |",
            "|---|---|---|---|---|---|",
        ]
        for e in rows:
            lines.append(
                f"| `{e['id']}` | `{e['file']}` | {mm3.hhmmss(e['duration_s'])} | "
                f"{e['qc']['slow_envelope_range_db']:.1f} dB | {e['qc']['presence_band_ratio']:.2f} | {e['use']} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", type=Path, default=HERE / "library.json")
    ap.add_argument("--only", default="", help="comma-separated ids to (re)build")
    ap.add_argument("--port", type=int, default=mm3.DEFAULT_PORT)
    ap.add_argument("--list", action="store_true")
    ap.add_argument(
        "--reflag",
        action="store_true",
        help="recompute QC and flags from the audio already on disk; no generation",
    )
    ap.add_argument(
        "--stop-server",
        action="store_true",
        help="shut the engine down afterwards (only if we started it)",
    )
    args = ap.parse_args()

    lib = json.loads(args.library.read_text())
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    specs = [s for s in lib["sounds"] if not wanted or s["id"] in wanted]
    if wanted - {s["id"] for s in specs}:
        raise SystemExit(f"unknown ids: {sorted(wanted - {s['id'] for s in specs})}")

    if args.reflag:
        import soundfile as sf

        mpath = OUT_ROOT / "manifest.json"
        man = json.loads(mpath.read_text())
        by_id = {s_["id"]: s_ for s_ in lib["sounds"]}
        for e in man["sounds"]:
            spec = by_id[e["id"]]
            audio, sr = sf.read(str(OUT_ROOT / e["file"]), always_2d=True, dtype="float64")
            audio = audio.T
            qc = mm3.music_qc(audio, sr)
            if e["loopable"]:
                qc.update(sfx.loop_qc(audio, sr))
            lo, hi = spec["arc_band_db"]
            arc = qc["slow_envelope_range_db"]
            flags = []
            if qc["presence_band_ratio"] > spec.get("max_presence_ratio", PRESENCE_MAX):
                flags.append("crowds_the_voice")
            if qc["harsh_band_ratio"] > HARSH_MAX:
                flags.append("harsh_band")
            if e["duration_s"] < float(spec["min_length_s"]):
                flags.append("short")
            if arc < lo:
                flags.append("too_flat")
            if arc > hi:
                flags.append("too_dynamic")
            if e["loopable"] and abs(qc["seam_rms_delta_db"]) > 3.0:
                flags.append("seam_level_jump")
            e["qc"], e["flags"], e["arc_band_db"] = qc, flags, spec["arc_band_db"]
        mpath.write_text(json.dumps(man, indent=2) + "\n")
        (OUT_ROOT / "README.md").write_text(render_readme(lib, man["sounds"]) + "\n")
        bad = [e["id"] for e in man["sounds"] if e["flags"]]
        print(f"re-flagged {len(man['sounds'])} tracks; flagged: {', '.join(bad) or 'none'}")
        return 0

    if args.list:
        for s_ in specs:
            kind = (
                f"loop<={s_['loop']['max_length_s']:.0f}s"
                if "loop" in s_
                else f">={s_['min_length_s']:.0f}s"
            )
            print(f"{s_['id']:28s} {s_['category']:12s} {kind:>11s}  arc {s_['arc_band_db']} dB")
        print(
            f"\n{len(specs)} tracks x {lib['candidates']} candidates, cap {lib['cap_s']:.0f}s each."
        )
        print(
            "Track length is emergent: MM3 is not conditioned on duration, so the cap is only a "
            "cap and the longest passing take is kept."
        )
        return 0

    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    proc = mm3.start_server(port=args.port)
    log(f"engine: {'started by us' if proc else 'already running'} on :{args.port}")
    p = mm3.props(args.port)
    if not p.get("available") or not p.get("synth_ready"):
        raise SystemExit(f"engine reports MM3 not ready: {json.dumps(p)[:400]}")
    log(f"model: {p['files']['lm']['general_name']} ({p['files']['lm']['name']})\n")

    manifest_path = OUT_ROOT / "manifest.json"
    existing = {}
    if manifest_path.is_file():
        existing = {e["id"]: e for e in json.loads(manifest_path.read_text())["sounds"]}

    try:
        for i, spec in enumerate(specs, 1):
            log(f"[{i:2d}/{len(specs)}] {spec['id']}")
            e = build_one(spec, lib, args.port, log)
            existing[e["id"]] = e
            log(
                f"      {mm3.hhmmss(e['duration_s'])}  I={e['loudness']['integrated_lufs']:.1f} "
                f"arc={e['qc']['slow_envelope_range_db']:.1f}dB "
                f"voice={e['qc']['presence_band_ratio']:.3f} "
                f"harsh={e['qc']['harsh_band_ratio']:.3f} "
                f"{e['generate_s']:.0f}s  {' '.join(e['flags']) or 'ok'}\n"
            )
    finally:
        order = {s["id"]: i for i, s in enumerate(lib["sounds"])}
        entries = sorted(existing.values(), key=lambda e: order.get(e["id"], 1 << 30))
        if entries:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "generator": "skills/audio/music/build_library.py",
                        "recipe": "skills/audio/music/library.json",
                        "research": "docs/research/2026-09-07-background-music-library.md",
                        "model": lib["model"],
                        "model_id": mm3.MODEL_ID,
                        "runtime": lib["runtime"],
                        "licence": (
                            "MiniMax-Music3 Community License (weights) — requires clear disclosure "
                            "of AI generation for publicly distributed outputs; HOT-Step engine MIT"
                        ),
                        "format": {
                            "container": "flac",
                            "sample_rate": lib["sample_rate"],
                            "channels": lib["channels"],
                            "bit_depth": 24,
                        },
                        "targets": lib["targets"],
                        "count": len(entries),
                        "sounds": entries,
                    },
                    indent=2,
                )
                + "\n"
            )
            (OUT_ROOT / "README.md").write_text(render_readme(lib, entries) + "\n")
        if proc and args.stop_server:
            proc.terminate()
            log("engine stopped")

    flagged = [e["id"] for e in entries if e["flags"]]
    log(f"\n{len(entries)} tracks, {sum(e['bytes'] for e in entries) / 1e6:.1f} MB -> {OUT_ROOT}")
    log(f"flagged: {', '.join(flagged) if flagged else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
