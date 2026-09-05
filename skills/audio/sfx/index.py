"""The `assets/sfx` index: one sha256-pinned manifest.json and one browsable README.md.

Both builders write through here -- `build_library.py` for the sounds generated with
Stable Audio 3 Small-SFX, `ingest_recorded.py` for the ones cut from a licensed recording bundle --
so the library has a single description of itself no matter which half was rebuilt last. Running
either builder re-renders the index over the union of what is already in the manifest and what it
just produced, which is why an entry carries its own `source`: it is the only way a partial
rebuild can still describe the whole.

Every number in the prose is measured from the entries rather than written into it. The README
makes claims about loop seams and about which one-shots sit under the target; those were true of
the 38 generated sounds and are not true of the same set plus twenty recorded ones, so they are
computed here and cannot go stale.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

SOURCE_GENERATED = "generated"
SOURCE_RECORDED = "recorded"
RESEARCH = "docs/research/2026-09-07-video-sfx-and-ambience-library.md"


def load(manifest_path: Path) -> tuple[dict[str, dict], dict]:
    """(entries by id, previous sources block). An entry written before `source` existed is generated."""
    if not manifest_path.is_file():
        return {}, {}
    doc = json.loads(manifest_path.read_text())
    out = {}
    for e in doc["sounds"]:
        e.setdefault("source", SOURCE_GENERATED)
        out[e["id"]] = e
    return out, doc.get("sources", {})


def _fmt(entries: list[dict]) -> dict:
    srs = {e["sample_rate"] for e in entries}
    chs = {e["channels"] for e in entries}
    return {
        "container": "flac",
        "sample_rate": srs.pop() if len(srs) == 1 else sorted(srs),
        "channels": chs.pop() if len(chs) == 1 else sorted(chs),
        "bit_depth": 24,
    }


def _suppliers(entries: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in entries:
        src = e.get("recorded_from")
        if not src:
            continue
        s = out.setdefault(src["supplier"], {"count": 0, "libraries": []})
        s["count"] += 1
        if src["library"] not in s["libraries"]:
            s["libraries"].append(src["library"])
    for s in out.values():
        s["libraries"].sort()
    return dict(sorted(out.items()))


def render_readme(entries: list[dict], targets: dict, sources: dict) -> str:
    rec = [e for e in entries if e.get("source") == SOURCE_RECORDED]
    gen = [e for e in entries if e.get("source") != SOURCE_RECORDED]
    has_rec = bool(rec) and SOURCE_RECORDED in sources
    has_gen = bool(gen) and SOURCE_GENERATED in sources
    beds = [e for e in entries if e["loopable"]]
    fmt = _fmt(entries)
    sr_txt = (
        f"{fmt['sample_rate'] / 1000:g} kHz"
        if isinstance(fmt["sample_rate"], int)
        else "mixed rate"
    )

    seam_max = max((abs(e["qc"].get("seam_rms_delta_db", 0.0)) for e in beds), default=0.0)
    event_max = max((e["qc"].get("event_prominence_db", 0.0) for e in beds), default=0.0)
    peak_limited = sorted(
        e["id"]
        for e in entries
        if not e["loopable"] and e["loudness"].get("gain_limited_by") == "true_peak"
    )
    suppliers = _suppliers(rec)

    if has_rec and has_gen:
        headline = (
            f"{len(entries)} sounds: **{len(rec)} cut from licensed recordings** and "
            f"**{len(gen)} generated locally** with {sources[SOURCE_GENERATED]['model']}."
        )
    elif has_rec:
        headline = f"{len(entries)} sounds cut from licensed recordings."
    else:
        headline = (
            f"{len(entries)} sounds generated locally with {sources[SOURCE_GENERATED]['model']}."
        )

    lines = [
        "# `assets/sfx` — video sound-effect and ambience library",
        "",
        headline,
        f"{sr_txt} stereo, 24-bit FLAC throughout, every file levelled to the same two targets.",
        "Nothing here was downloaded during a build; nothing here was uploaded.",
        "",
        "**Do not hand-edit this directory.** It is written by two builders and they own it:",
        "",
        "| half | builder | recipe |",
        "|---|---|---|",
    ]
    if has_rec:
        lines.append(
            f"| recorded | `{sources[SOURCE_RECORDED]['generator']}` | `{sources[SOURCE_RECORDED]['recipe']}` |"
        )
    if has_gen:
        lines.append(
            f"| generated | `{sources[SOURCE_GENERATED]['generator']}` | `{sources[SOURCE_GENERATED]['recipe']}` |"
        )
    lines += [
        "",
        "Either builder re-renders this file and `manifest.json` over the whole library, so a",
        "partial rebuild stays consistent. `manifest.json` carries per-file sha256, loudness and QC",
        f"numbers, plus the source offset every recorded excerpt was cut at. Design decisions: `{RESEARCH}`.",
        "",
    ]
    if has_rec:
        lines += [
            "## Provenance and licensing",
            "",
            f"The recorded half is cut from the **{sources[SOURCE_RECORDED]['bundle']}**, which the",
            "operator holds locally. Its licence is royalty-free: unlimited personal and commercial",
            "projects, modification permitted, **no attribution required**. Suppliers are recorded",
            "anyway — provenance is not the same thing as an obligation, and a library you cannot trace",
            "is a library you cannot re-cut. `manifest.json` names the exact source file, its sha256 and",
            "the offset for every excerpt.",
            "",
            "| supplier | sounds | libraries drawn from |",
            "|---|---|---|",
        ]
        for name, sup in suppliers.items():
            lines.append(f"| {name} | {sup['count']} | {', '.join(sup['libraries'])} |")
        lines += [
            "",
            f"The bundle is **not** in this repo and is never committed: {sources[SOURCE_RECORDED]['bundle_size']}",
            "of source audio lives outside it, and only the excerpts below are kept. Point",
            f"`{sources[SOURCE_RECORDED]['env_var']}` at an extracted copy to rebuild.",
            "",
        ]
    if has_gen:
        lines += [
            f"The generated half comes from **{sources[SOURCE_GENERATED]['model']}** "
            f"({sources[SOURCE_GENERATED]['licence']}),",
            "each sound on a fixed seed, so it is reproducible from its recipe alone.",
            "",
        ]
    lines += [
        "## Levels",
        "",
        "| Family | Target | Ceiling |",
        "|---|---|---|",
        f"| One-shots | max-momentary **{targets['oneshot']['value']} LUFS** | {targets['oneshot']['true_peak_dbtp']} dBTP |",
        f"| Beds (`loopable: true`) | integrated **{targets['bed']['value']} LUFS** | {targets['bed']['true_peak_dbtp']} dBTP |",
        "",
        "Both sit deliberately below the -14 LUFS programme target, so you add gain rather than",
        "fight clipping. Level was set with a single constant scalar — no compression, no limiting —",
        "because time-varying gain would break the loop seams. Recorded and generated sounds go",
        "through the same levelling code and land on the same numbers, so the two halves are",
        "interchangeable in a mix.",
        "",
    ]
    if peak_limited:
        lines += [
            "These one-shots stop short of the loudness target because their peaks reached the",
            "ceiling first — a short transient is nearly all crest, and reaching -16 LUFS momentary",
            "would clip it: " + ", ".join(f"`{i}`" for i in peak_limited) + ".",
            "`loudness.gain_limited_by` in the manifest is the field that says so. It is also how",
            "they should sit — a UI click belongs under a scene transition, not level with it.",
            "",
        ]
    lines += [
        "## Loops",
        "",
        "Every `loopable: true` file is butt-joinable: set the clip to repeat with **no crossfade",
        "and no gap**. The tail was folded back over the head before the file was written, so the",
        "wrap point is a continuous span of the source rather than a splice — there is nothing there",
        "to click on. Two manifest numbers say whether a bed survives repeating:",
        f"`qc.seam_rms_delta_db` is the level match across the wrap (all beds here are within {seam_max:.1f} dB)",
        "and `qc.event_prominence_db` is the loudest half-second over the median half-second — the",
        "thing that actually gives a loop away. Above about 10 dB a viewer hears something recur;",
        f"everything here is at {event_max:.1f} dB or below.",
        "",
        "For a recorded bed that is also what chose the excerpt. A long ambience is not uniformly",
        "loopable: the builder sweeps the whole recording on a 0.25 s envelope, shortlists the",
        "evenest windows, wraps each one for real and keeps the best-scoring — `window_start_s` and",
        "`windows_auditioned` in the manifest say where it landed and how many it looked at.",
        "",
        "Thunder is deliberately **not** in `storm_bed_loop` for that reason. Use the",
        "`thunder_distant` one-shot over the bed and place the rolls where you want them.",
        "",
    ]

    for cat in sorted({e["category"] for e in entries}):
        rows = sorted((e for e in entries if e["category"] == cat), key=lambda r: r["id"])
        lines += [f"## {cat}", "", "| id | file | length | source | use |", "|---|---|---|---|---|"]
        for e in rows:
            src = e.get("recorded_from")
            origin = f"{src['supplier']}" if src else "generated"
            lines.append(
                f"| `{e['id']}` | `{e['file']}` | {e['duration_s']:.2f}s | {origin} | {e['use']} |"
            )
        lines.append("")
    return "\n".join(lines)


def write(
    out_root: Path,
    entries: dict[str, dict],
    targets: dict,
    source_key: str,
    source_info: dict,
    prev_sources: dict | None = None,
):
    """Write manifest.json + README.md over the union of entries. Returns (manifest, missing).

    The caller describes only its own half in `source_info`; the other half's description is
    carried forward from the manifest already on disk. That is what lets either builder run
    alone -- `build_library.py` on a machine with no recording bundle, `ingest_recorded.py`
    without loading a 4 GB text encoder -- and still leave the index describing the whole
    library rather than only the part it happened to touch.

    Entries are ordered by (category, id) rather than by recipe position: with two recipes there
    is no single authoring order to preserve, and a stable sort means the manifest diff after a
    rebuild shows what actually changed instead of a reshuffle.
    """
    sources = dict(prev_sources or {})
    sources[source_key] = source_info
    ordered = sorted(entries.values(), key=lambda e: (e["category"], e["id"]))
    missing = [e["id"] for e in ordered if not (out_root / e["file"]).is_file()]
    by_source: dict[str, int] = {}
    for e in ordered:
        by_source[e.get("source", SOURCE_GENERATED)] = (
            by_source.get(e.get("source", SOURCE_GENERATED), 0) + 1
        )

    manifest = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "research": RESEARCH,
        "format": _fmt(ordered),
        "targets": targets,
        "sources": sources,
        "suppliers": _suppliers(ordered),
        "count": len(ordered),
        "count_by_source": dict(sorted(by_source.items())),
        "sounds": ordered,
    }
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out_root / "README.md").write_text(render_readme(ordered, targets, sources) + "\n")
    return manifest, missing
