"""The `assets/sfx` index: one sha256-pinned manifest.json and one browsable README.md.

Every builder writes through here -- `build_library.py` for the sounds generated with
Stable Audio 3 Small-SFX, `ingest_recorded.py` for the ones cut from a licensed recording bundle,
`ingest_packs.py` for a pack of individually licensed files -- so the library has a single
description of itself no matter which part was rebuilt last. Running any builder re-renders the
index over the union of what is already in the manifest and what it just produced, which is why
an entry carries its own `source`: it is the only way a partial rebuild can still describe the
whole.

`source` is one key per *origin*, not one per builder: `generated`, `recorded`, and one per pack
(`mixkit`, `local-renders`). A pack is its own source because the thing a reader needs from this
file -- which licence governs this sound, and where would I get the audio again -- is a property
of the pack it came from, and merging two packs under one label would lose it.

Every number in the prose is measured from the entries rather than written into it. The README
makes claims about loop seams and about which one-shots sit under the target; those were true of
the 38 generated sounds and are not true of the same set plus three hundred more, so they are
computed here and cannot go stale.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

SOURCE_GENERATED = "generated"
SOURCE_RECORDED = "recorded"
RESEARCH = "docs/research/2026-09-07-video-sfx-and-ambience-library.md"

EVENT_PROMINENCE_LOUD = 10.0
"""Above this a bed's loudest half-second is audible as a recurring event when it wraps.
The builders flag it per sound; the README lists them, because a reader choosing a bed needs to
know which ones announce their own repeat."""


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


def origin(entry: dict) -> dict | None:
    """Where the audio came from, whichever builder wrote it. None for a generated sound.

    `recorded_from` is the #GameAudioGDC excerpt block and `ingested_from` the pack-file block;
    they carry the same provenance fields and differ only in what had to be chosen (a window in a
    680 s recording, versus nothing -- the supplier already cut the file).
    """
    return entry.get("recorded_from") or entry.get("ingested_from")


def _suppliers(entries: list[dict]) -> dict[str, dict]:
    """supplier -> {count, libraries}. A source with no named supplier contributes nothing.

    The operator's own renders have no supplier and no library to name; an empty row in this
    table would read as a supplier called "", which is worse than an absent one. Their provenance
    is the pack block in `sources` instead.
    """
    out: dict[str, dict] = {}
    for e in entries:
        src = origin(e)
        if not src or not src.get("supplier"):
            continue
        s = out.setdefault(src["supplier"], {"count": 0, "libraries": []})
        s["count"] += 1
        library = src.get("library") or ""
        if library and library not in s["libraries"]:
            s["libraries"].append(library)
    for s in out.values():
        s["libraries"].sort()
    return dict(sorted(out.items()))


def _present(entries: list[dict], sources: dict) -> dict[str, dict]:
    """The sources that actually have sounds in this library, in manifest order of appearance.

    A `sources` block carried forward from an older manifest can describe a source whose entries
    have all been replaced; describing it in the README would promise audio that is not there.
    """
    have = {e.get("source", SOURCE_GENERATED) for e in entries}
    return {k: v for k, v in sources.items() if k in have}


def _phrase(key: str, info: dict, n: int) -> str:
    if key == SOURCE_GENERATED:
        return f"**{n} generated locally** with {info.get('model', 'a local model')}"
    return f"**{n} {info.get('headline', 'from ' + info.get('bundle', key))}**"


def render_readme(entries: list[dict], targets: dict, sources: dict) -> str:
    present = _present(entries, sources)
    counts: dict[str, int] = {}
    for e in entries:
        key = e.get("source", SOURCE_GENERATED)
        counts[key] = counts.get(key, 0) + 1
    beds = [e for e in entries if e["loopable"]]
    fmt = _fmt(entries)
    sr_txt = (
        f"{fmt['sample_rate'] / 1000:g} kHz"
        if isinstance(fmt["sample_rate"], int)
        else "mixed rate"
    )

    seam_max = max((abs(e["qc"].get("seam_rms_delta_db", 0.0)) for e in beds), default=0.0)
    event_max = max((e["qc"].get("event_prominence_db", 0.0) for e in beds), default=0.0)
    loud_beds = sorted(
        e["id"] for e in beds if e["qc"].get("event_prominence_db", 0.0) > EVENT_PROMINENCE_LOUD
    )
    peak_limited = sorted(
        e["id"]
        for e in entries
        if not e["loopable"] and e["loudness"].get("gain_limited_by") == "true_peak"
    )
    suppliers = _suppliers(entries)
    bundles = {k: v for k, v in present.items() if k != SOURCE_GENERATED and v.get("bundle")}

    parts = [_phrase(k, present[k], counts[k]) for k in present]
    headline = f"{len(entries)} sounds: " + (
        ", ".join(parts[:-1]) + f" and {parts[-1]}." if len(parts) > 1 else f"{parts[0]}."
    )

    lines = [
        "# `assets/sfx` — video sound-effect and ambience library",
        "",
        headline,
        f"{sr_txt} stereo, 24-bit FLAC throughout, every file levelled to the same two targets.",
        "Nothing here was downloaded during a build; nothing here was uploaded.",
        "",
        "**Do not hand-edit this directory.** It is written by the builders below and they own it:",
        "",
        "| source | builder | recipe |",
        "|---|---|---|",
    ]
    for key, info in present.items():
        lines.append(f"| {key} | `{info['generator']}` | `{info['recipe']}` |")
    lines += [
        "",
        "Any builder re-renders this file and `manifest.json` over the whole library, so a",
        "partial rebuild stays consistent. `manifest.json` carries per-file sha256, loudness and QC",
        "numbers, plus the source file and offset every excerpt was cut at. Design decisions:",
        f"`{RESEARCH}`.",
        "",
        "## Provenance and licensing",
        "",
    ]
    for key, info in bundles.items():
        size = f" ({info['bundle_size']})" if info.get("bundle_size") else ""
        lines += [
            f"### {counts[key]} sounds — {info['bundle']}{size}",
            "",
            info["licence"],
            "",
        ]
        if info.get("licence_url"):
            lines += [
                f"Licence read from <{info['licence_url']}>"
                + (f" on {info['licence_retrieved']}." if info.get("licence_retrieved") else "."),
                "",
            ]
        if info.get("bundle_path"):
            lines += [
                "The source audio is **not** in this repo and is never committed: it lives at",
                f"`{info['bundle_path']}` and only the levelled excerpts are kept here. Point",
                f"`{info['env_var']}` at another copy to rebuild.",
                "",
            ]
    if suppliers:
        lines += [
            "Suppliers are recorded even where the licence does not ask for it — provenance is not",
            "the same thing as an obligation, and a library you cannot trace is a library you cannot",
            "re-cut. `manifest.json` names the exact source file, its sha256 and the offset for every",
            "excerpt.",
            "",
            "| supplier | sounds | libraries drawn from |",
            "|---|---|---|",
        ]
        for name, sup in suppliers.items():
            lines.append(f"| {name} | {sup['count']} | {', '.join(sup['libraries']) or '—'} |")
        lines.append("")
    if SOURCE_GENERATED in present:
        gen = present[SOURCE_GENERATED]
        lines += [
            f"### {counts[SOURCE_GENERATED]} sounds — {gen['model']} ({gen['licence']})",
            "",
            "Generated locally, each sound on a fixed seed, so it is reproducible from its recipe",
            "alone.",
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
        "because time-varying gain would break the loop seams. Every source goes through the same",
        "levelling code and lands on the same numbers, so sounds from different origins are",
        "interchangeable in a mix.",
        "",
    ]
    if peak_limited:
        lines += [
            "These one-shots stop short of the loudness target because their peaks reached the",
            "ceiling first — a short transient is nearly all crest, and reaching "
            f"{targets['oneshot']['value']} LUFS momentary",
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
        "thing that actually gives a loop away. Above about "
        f"{EVENT_PROMINENCE_LOUD:g} dB a viewer hears something recur;",
        f"the highest here is {event_max:.1f} dB.",
        "",
        "For a bed cut from a longer recording that is also what chose the excerpt. A long ambience",
        "is not uniformly loopable: the builder sweeps the whole recording on a 0.25 s envelope,",
        "shortlists the evenest windows, wraps each one for real and keeps the best-scoring —",
        "`window_start_s` and `windows_auditioned` in the manifest say where it landed and how many",
        "it looked at.",
        "",
    ]
    if loud_beds:
        lines += [
            "These beds carry an event loud enough to be heard recurring, and are listed rather than",
            "quietly shipped: " + ", ".join(f"`{i}`" for i in loud_beds) + ".",
            "Each one is a bed whose *subject* is the event — thunder in a thunderstorm, a bleat in a",
            "herd — so the alternative was not having it. Use them where the repeat is masked by",
            "narration or picture, and prefer a neutral bed plus a placed one-shot where it is not:",
            "`storm_bed_loop` has no thunder in it for exactly this reason, and `thunder_distant`",
            "and `thunder_strike` exist to be dropped on top where you want the rolls.",
            "",
        ]

    for cat in sorted({e["category"] for e in entries}):
        rows = sorted((e for e in entries if e["category"] == cat), key=lambda r: r["id"])
        lines += [f"## {cat}", "", "| id | file | length | source | use |", "|---|---|---|---|---|"]
        for e in rows:
            src = origin(e)
            who = (src.get("supplier") or src.get("pack")) if src else "generated"
            lines.append(
                f"| `{e['id']}` | `{e['file']}` | {e['duration_s']:.2f}s | {who} | {e['use']} |"
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

    Entries are ordered by (category, id) rather than by recipe position: with several recipes
    there is no single authoring order to preserve, and a stable sort means the manifest diff
    after a rebuild shows what actually changed instead of a reshuffle.

    `duplicated` is the third return: sets of ids whose FLACs are byte-identical. Two ids holding
    one sound is a curation error rather than a build failure -- it happened once for real, when
    two Mixkit items turned out to be the same recording trimmed to two lengths and both window
    sweeps landed on the same span -- so it is reported loudly and not raised. Nothing downstream
    breaks; the library just promises a choice it does not have.
    """
    sources = dict(prev_sources or {})
    sources[source_key] = source_info
    ordered = sorted(entries.values(), key=lambda e: (e["category"], e["id"]))
    missing = [e["id"] for e in ordered if not (out_root / e["file"]).is_file()]
    by_bytes: dict[str, list[str]] = {}
    for e in ordered:
        if e["sha256"]:
            by_bytes.setdefault(e["sha256"], []).append(e["id"])
    duplicated = [ids for ids in by_bytes.values() if len(ids) > 1]
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
    return manifest, missing, duplicated
