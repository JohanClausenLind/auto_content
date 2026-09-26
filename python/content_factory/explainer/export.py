"""The export bundle and its verification (Gate F6): everything an upload needs, hashed."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import wave
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import Field

from content_factory.audio.captions import compile_captions, to_srt, to_webvtt
from content_factory.explainer.errors import ContractIssue, IssueKind
from content_factory.explainer.pipeline import (
    MUSIC_MANIFEST,
    SFX_MANIFEST,
    EpisodeConfig,
    Ledger,
    RenderStills,
    library_entry,
    now_iso,
    read_model,
    render_stills,
    synth_candidate,
    write_atomic,
    write_model,
)
from content_factory.explainer.timing import TokenClock
from content_factory.qc.media import ffprobe
from content_factory.schemas.audio import CaptionTrack
from content_factory.schemas.base import SchemaModel, Sha256Hex, file_sha256
from content_factory.schemas.documentary import ChapterMarker
from content_factory.schemas.explainer import (
    CaptureAsset,
    EvidencePack,
    ExplainerRenderBundle,
    NarrationManifest,
    ReviewReport,
    ScriptPlan,
)
from content_factory.schemas.scenes import WordTiming

EXPORT_VERSION = "1"
EXPORTS_DIR = "exports"
EXPORT_MANIFEST = "export-manifest.json"
# Label texts and pages from docs/research/2026-09-15-explainer-stack.md.
AI_LABEL = "Altered or synthetic content"
AI_REFERENCE = "https://support.google.com/youtube/answer/14328491"
PAID_LABEL = "Includes paid promotion"
PAID_REFERENCE = "https://support.google.com/youtube/answer/154235"
SCREENSHOT_NOTE = "A screenshot shows what a source said, not that it is true."
STEM_TOLERANCE_MS = 50
CAPTION_SLACK_MS = 50
STEMS = ("narration", "music", "sfx", "master")
REEXPORT = "re-run the pipeline, then `content-factory explainer export <episode_id>`."
SECTION_TITLES: dict[str, str] = {
    "cold_open": "Opening",
    "question_stakes": "The question",
    "build_model": "Building the model",
    "run_system": "Running the system",
    "change_variable": "Changing one variable",
    "show_limits": "Where it breaks",
    "synthesis": "What it means",
    "sponsor": "Sponsor",
}
# Exported copies that must equal the ledger's final revision of the artifact they came from.
LEDGER_BOUND: dict[str, tuple[str, str]] = {
    "final.mp4": ("mux", "mp4"),
    "manifests/pack.json": ("freeze_evidence", "pack"),
    "manifests/script.json": ("lock_script", "script"),
    "manifests/spec.json": ("repair", "spec"),
    "manifests/bundle.json": ("repair", "bundle"),
    "manifests/narration-manifest.json": ("mix", "manifest"),
    "review/qc_animatic.json": ("qc_animatic", "report"),
    "review/review_animatic.json": ("review_animatic", "report"),
    "review/qc_final.json": ("qc_final", "report"),
    "review/review_final.json": ("review_final", "report"),
    **{f"stems/{name}.wav": ("mix", name) for name in STEMS},
}
_CUE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3}) --> (\d+):(\d{2}):(\d{2})[,.](\d{3})")


class ExportFile(SchemaModel):
    path: str = Field(min_length=1, max_length=300)
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)


class ExportManifest(SchemaModel):
    """What was exported, from which ledger fingerprints, with the hash of every file."""

    episode_id: str = Field(min_length=1, max_length=64)
    export_version: str
    created_at: str = Field(min_length=4)
    duration_ms: int = Field(ge=1)
    fingerprints: dict[str, str]
    ai_disclosure: bool
    paid_promotion: bool
    files: tuple[ExportFile, ...] = Field(min_length=1)


def export_bundle(
    episode_dir: Path,
    *,
    config: EpisodeConfig,
    render_stills: RenderStills = render_stills,
    created_at: str | None = None,
) -> ExportManifest:
    """Build exports/ beside the ledger, then swap it in whole: a stale file never survives."""
    ledger = Ledger.load(episode_dir)
    pack = read_model(EvidencePack, ledger.artifact("freeze_evidence", "pack"))
    script = read_model(ScriptPlan, ledger.artifact("lock_script", "script"))
    narration = read_model(NarrationManifest, ledger.artifact("mix", "manifest"))
    bundle = read_model(ExplainerRenderBundle, ledger.artifact("repair", "bundle"))
    captures = _captures(ledger)
    tmp = episode_dir / f".{EXPORTS_DIR}-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    for rel, (step, name) in ledger_bound(ledger).items():
        _copy(ledger.artifact(step, name), tmp / rel)
    _copy(ledger.path, tmp / "manifests" / "ledger.json")
    track = caption_track(script, narration, config.episode_id, language=pack.language)
    write_atomic(tmp / "captions.srt", to_srt(track))
    write_atomic(tmp / "captions.vtt", to_webvtt(track))
    markers = chapter_markers(script, narration)
    write_atomic(tmp / "chapters.txt", chapters_text(markers))
    chapters = [m.model_dump(mode="json") for m in markers]
    write_atomic(tmp / "chapters.json", json.dumps(chapters, indent=1) + "\n")
    write_atomic(tmp / "sources.md", sources_markdown(pack, captures))
    with tempfile.TemporaryDirectory(prefix=".thumb-", dir=episode_dir) as frames:
        [still] = render_stills(bundle, Path(frames), [thumbnail_frame(bundle)])
        _copy(still, tmp / "thumbnail.png")
    titles = [p.model_dump(mode="json") for p in script.promises]
    write_atomic(tmp / "titles.json", json.dumps(titles, indent=1, ensure_ascii=False) + "\n")
    rights = rights_record(config, pack, script, narration)
    write_atomic(tmp / "rights.json", json.dumps(rights, indent=1, ensure_ascii=False) + "\n")
    files = tuple(
        ExportFile(
            path=p.relative_to(tmp).as_posix(), sha256=file_sha256(p), size_bytes=p.stat().st_size
        )
        for p in sorted(tmp.rglob("*"))
        if p.is_file()
    )
    manifest = ExportManifest(
        episode_id=config.episode_id,
        export_version=EXPORT_VERSION,
        created_at=created_at or now_iso(),
        duration_ms=media_duration_ms(tmp / "final.mp4"),
        fingerprints=_fingerprints(ledger),
        ai_disclosure=rights["ai_disclosure"]["required"],
        paid_promotion=rights["paid_promotion"]["required"],
        files=files,
    )
    write_model(tmp / EXPORT_MANIFEST, manifest)
    _swap(tmp, episode_dir / EXPORTS_DIR)
    return manifest


def ledger_bound(ledger: Ledger) -> dict[str, tuple[str, str]]:
    """LEDGER_BOUND plus the repair rounds and the captures this episode actually has."""
    bound = dict(LEDGER_BOUND)
    for step, prefix, folder in (
        ("repair", "round-", "review/repair-"),
        ("capture_sources", "capture:", "manifests/captures/"),
    ):
        entry = ledger.entry(step) or {}
        for name in entry.get("artifacts", {}):
            if name.startswith(prefix):
                bound[f"{folder}{name.removeprefix(prefix)}.json"] = (step, name)
    return bound


def _captures(ledger: Ledger) -> tuple[CaptureAsset, ...]:
    index = json.loads(ledger.artifact("capture_sources", "index").read_text(encoding="utf-8"))
    return tuple(read_model(CaptureAsset, ledger.resolve(p)) for p in index["captures"])


def _fingerprints(ledger: Ledger) -> dict[str, str]:
    return {e["step"]: e["fingerprint"] for e in ledger.data["steps"] if e["step"] != "export"}


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _swap(new: Path, target: Path) -> None:
    old = target.with_name(f".{target.name}-old-{os.getpid()}")
    if target.exists():
        target.rename(old)
    new.rename(target)
    shutil.rmtree(old, ignore_errors=True)


# --- captions, chapters, sources, thumbnail, rights ---


def narration_words(script: ScriptPlan, narration: NarrationManifest) -> list[WordTiming]:
    """Every script token at its aligned time on the stem; an unaligned token keeps its place."""
    tokens = {s.segment_id: s.tokens for s in script.segments}
    words: list[WordTiming] = []
    cursor = 0
    for take in sorted(narration.takes, key=lambda t: t.start_ms):
        timed = (
            {(w.segment_id, w.token_index): w for w in take.alignment.words}
            if take.alignment
            else {}
        )
        cursor = max(cursor, take.start_ms)
        for sid in take.segment_ids:
            for k, token in enumerate(tokens.get(sid, ())):
                word = timed.get((sid, k))
                start = max(cursor, take.start_ms + word.start_ms) if word else cursor
                end = max(start, take.start_ms + word.end_ms) if word else start
                words.append(WordTiming(word=token, start_ms=start, end_ms=end))
                cursor = start
    return words


def caption_track(
    script: ScriptPlan, narration: NarrationManifest, episode_id: str, *, language: str = "en"
) -> CaptionTrack:
    deliverable = "dlv_" + hashlib.sha256(episode_id.encode()).hexdigest()[:16]
    return compile_captions(deliverable, narration_words(script, narration), language=language)


def chapter_markers(script: ScriptPlan, narration: NarrationManifest) -> list[ChapterMarker]:
    """One chapter per script section at its first spoken token; the first sits at 00:00."""
    clock = TokenClock(script, narration)
    markers: list[ChapterMarker] = []
    seen: set[str] = set()
    for segment in script.segments:
        if segment.section in seen:
            continue
        seen.add(segment.section)
        at_s = clock.start_ms(segment.segment_id, 0) // 1000 if markers else 0
        if markers and at_s <= markers[-1].at_s:
            continue
        markers.append(ChapterMarker(at_s=at_s, title=SECTION_TITLES[segment.section]))
    return markers


def chapter_stamp(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def chapters_text(markers: Sequence[ChapterMarker]) -> str:
    """YouTube's description form: one `mm:ss Title` per line."""
    return "".join(f"{chapter_stamp(m.at_s)} {m.title}\n" for m in markers)


def sources_markdown(pack: EvidencePack, captures: Sequence[CaptureAsset]) -> str:
    claims_of = {
        item.item_id: [c.claim_id for c in pack.claims if item.item_id in c.evidence_ids]
        for item in pack.items
    }
    lines = ["# Sources", "", SCREENSHOT_NOTE, ""]
    for source in pack.sources:
        lines += [
            f"## {source.title or source.url}",
            "",
            f"- Publisher: {source.publisher or 'not stated'}",
            f"- URL: {source.url}",
            f"- Accessed: {source.accessed_at}",
            f"- Published: {source.published_at or 'not stated'}",
        ]
        shown = [c.manifest for c in captures if c.manifest.source_id == source.source_id]
        for m in shown:
            signed = f", signed for {m.signature_domain}" if m.signed else ""
            lines.append(
                f"- Capture {m.capture_id}: captured {m.captured_at}, {m.capture_kind} "
                f"sha256 {m.artifact_sha256}{signed}"
            )
        items = [i for i in pack.items if i.source_id == source.source_id]
        if items or any(m.quotes for m in shown):
            lines += ["", "Quoted passages:"]
        for item in items:
            claims = ", ".join(claims_of[item.item_id]) or "none"
            lines += ["", f"> {item.passage}", "", f"Claims: {claims} (evidence {item.item_id})"]
        for m in shown:
            for quote in m.quotes:
                verified = ", OCR verified" if quote.ocr_verified else ""
                claims = ", ".join(quote.claim_ids) or "none"
                where = f"capture {m.capture_id} quote {quote.quote_id}{verified}"
                lines += ["", f"> {quote.text}", "", f"Claims: {claims} (on screen: {where})"]
        lines.append("")
    return "\n".join(lines)


def thumbnail_frame(bundle: ExplainerRenderBundle) -> int:
    """The cold open's settled frame: after its last action ends, inside the scene."""
    spec_scenes = bundle.spec.scenes
    scene_id = next(
        (s.scene_id for s in spec_scenes if s.section == "cold_open"), spec_scenes[0].scene_id
    )
    compiled = next(s for s in bundle.timeline.scenes if s.scene_id == scene_id)
    last = compiled.start_frame + compiled.duration_frames - 1
    middle = (compiled.start_frame + last) // 2
    return min(max((a.end_frame for a in compiled.actions), default=middle), last)


def rights_record(
    config: EpisodeConfig, pack: EvidencePack, script: ScriptPlan, narration: NarrationManifest
) -> dict[str, Any]:
    """Licences of every sound, the AI-disclosure and paid-promotion flags, and attribution."""
    candidate = synth_candidate(config.synth)
    synthesized = sum(t.kind == "synthesized" for t in narration.takes)
    reasons: list[str] = []
    if synthesized:
        reasons.append(
            f"{synthesized} of {len(narration.takes)} narration takes are synthesized "
            f"({candidate.label})"
        )
    music: list[dict[str, Any]] = []
    attribution = [
        f"{s.title or s.url} - {s.publisher or 'publisher not stated'} - {s.url} "
        f"(accessed {s.accessed_at})"
        for s in pack.sources
    ]
    if config.music_id:
        entry, manifest = library_entry(MUSIC_MANIFEST, config.music_id)
        music.append(
            {
                "id": entry["id"],
                "file": f"assets/music/{entry['file']}",
                "sha256": entry["sha256"],
                "licence": manifest["licence"],
                "model": manifest.get("model", ""),
                "generated": True,
            }
        )
        reasons.append(f"music {entry['id']} is generated by {manifest.get('model', 'a model')}")
        attribution.append(f"Music: {entry['id']}, generated with {manifest.get('model', '')}")
    sfx = [_sfx_rights(cue.sfx_id, cue.at_ms) for cue in config.sfx]
    reasons += [f"sound effect {s['id']} is generated" for s in sfx if s["generated"]]
    sponsor = script.sponsor
    return {
        "episode_id": config.episode_id,
        "narration": {
            "synth": config.synth,
            "model": candidate.label,
            "licence": candidate.license,
            "licence_note": candidate.license_note,
            "commercial_output_allowed": candidate.commercial_output_allowed,
            "recorded_takes": len(narration.takes) - synthesized,
            "synthesized_takes": synthesized,
        },
        "music": music,
        "sfx": sfx,
        "ai_disclosure": {
            "required": bool(reasons),
            "label": AI_LABEL,
            "reasons": reasons,
            "reference": AI_REFERENCE,
        },
        "paid_promotion": {
            "required": sponsor is not None,
            "label": PAID_LABEL,
            "sponsor": sponsor.sponsor_name if sponsor else None,
            "disclosure_text": sponsor.disclosure_text if sponsor else None,
            "reference": PAID_REFERENCE,
        },
        "attribution": attribution,
    }


def _sfx_rights(sfx_id: str, at_ms: int) -> dict[str, Any]:
    entry, manifest = library_entry(SFX_MANIFEST, sfx_id)
    source = manifest.get("sources", {}).get(entry.get("source", ""), {})
    origin = entry.get("ingested_from") or entry.get("recorded_from") or {}
    return {
        "id": entry["id"],
        "at_ms": at_ms,
        "file": f"assets/sfx/{entry['file']}",
        "sha256": entry["sha256"],
        "source": entry.get("source", ""),
        "supplier": origin.get("supplier") or source.get("supplier", ""),
        "library": origin.get("library", ""),
        "licence": origin.get("licence") or source.get("licence", ""),
        "generated": entry.get("source") == "generated",
    }


# --- verification ---


def media_duration_ms(path: Path) -> int:
    return round(float(ffprobe(path)["format"]["duration"]) * 1000)


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as wf:
        return round(wf.getnframes() * 1000 / wf.getframerate())


def verify_export(episode_dir: Path) -> list[ContractIssue]:
    """F6: hashes match the ledger's final revision, and captions, chapters, stems fit the mp4."""
    folder = episode_dir / EXPORTS_DIR
    if not (folder / EXPORT_MANIFEST).is_file():
        fix = "run `content-factory explainer export <episode_id>`."
        return [_issue("invalid_reference", EXPORT_MANIFEST, "is missing.", fix)]
    manifest = ExportManifest.model_validate_json((folder / EXPORT_MANIFEST).read_text())
    listed = {f.path: f for f in manifest.files}
    ledger = Ledger.load(episode_dir)
    issues = _file_issues(folder, listed) + _ledger_issues(ledger, listed, manifest)
    present = {rel for rel, f in listed.items() if (folder / rel).is_file()}
    if "final.mp4" not in present:
        return issues
    duration_ms = media_duration_ms(folder / "final.mp4")
    if {
        "captions.srt",
        "captions.vtt",
        "manifests/script.json",
        "manifests/narration-manifest.json",
    } <= present:
        issues += _caption_issues(folder, duration_ms)
    if {"chapters.json", "chapters.txt"} <= present:
        issues += _chapter_issues(folder, duration_ms)
    return issues + _stem_issues(folder)


def _issue(kind: IssueKind, rel: str, message: str, fix: str) -> ContractIssue:
    return ContractIssue(
        kind=kind, where=f"exports/{rel}", message=f"exports/{rel} {message}", fix=fix, ids=(rel,)
    )


def _file_issues(folder: Path, listed: Mapping[str, Any]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for rel, record in listed.items():
        path = folder / rel
        if not path.is_file():
            issues.append(_issue("invalid_reference", rel, "is listed but missing.", REEXPORT))
        elif file_sha256(path) != record.sha256:
            message = f"hashes to {file_sha256(path)[:12]}, the manifest says {record.sha256[:12]}."
            issues.append(_issue("stale_binding", rel, message, REEXPORT))
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if path.is_file() and rel != EXPORT_MANIFEST and rel not in listed:
            issues.append(_issue("stale_binding", rel, "is not in the export manifest.", REEXPORT))
    return issues


def _ledger_issues(
    ledger: Ledger, listed: Mapping[str, ExportFile], manifest: ExportManifest
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for rel, (step, name) in ledger_bound(ledger).items():
        final = ledger.artifact_sha(step, name)
        record = listed.get(rel)
        if record is None:
            issues.append(_issue("invalid_reference", rel, "is not in the export.", REEXPORT))
        elif record.sha256 != final:
            shown = final[:12] if final else "nothing"
            message = (
                f"hashes to {record.sha256[:12]} but the ledger's final {step}.{name} is {shown}."
            )
            issues.append(_issue("stale_binding", rel, message, REEXPORT))
    final_mp4 = listed.get("final.mp4")
    for rel in ("review/qc_final.json", "review/review_final.json"):
        path = ledger.episode_dir / EXPORTS_DIR / rel
        if final_mp4 is None or not path.is_file():
            continue
        reviewed = ReviewReport.model_validate_json(path.read_text()).artifact.sha256
        if reviewed != final_mp4.sha256:
            message = f"reviewed {reviewed[:12]}, not final.mp4 {final_mp4.sha256[:12]}."
            issues.append(_issue("stale_binding", rel, message, REEXPORT))
    now = _fingerprints(ledger)
    for step in sorted(set(now) | set(manifest.fingerprints)):
        entry = ledger.entry(step) or {}
        then = manifest.fingerprints.get(step, "")
        if now.get(step) != then or entry.get("status") != "done":
            message = (
                f"was made from {step} {then[:12] or 'nothing'}; the ledger now records "
                f"{now.get(step, '')[:12] or 'nothing'} ({entry.get('status', 'absent')})."
            )
            issues.append(_issue("stale_binding", EXPORT_MANIFEST, message, REEXPORT))
    return issues


def _cue_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _CUE.finditer(text):
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in match.groups())
        spans.append(
            (((h1 * 60 + m1) * 60 + s1) * 1000 + ms1, ((h2 * 60 + m2) * 60 + s2) * 1000 + ms2)
        )
    return spans


def _caption_issues(folder: Path, duration_ms: int) -> list[ContractIssue]:
    script = read_model(ScriptPlan, folder / "manifests" / "script.json")
    narration = read_model(NarrationManifest, folder / "manifests" / "narration-manifest.json")
    words = narration_words(script, narration)
    srt = _cue_spans((folder / "captions.srt").read_text(encoding="utf-8"))
    vtt = _cue_spans((folder / "captions.vtt").read_text(encoding="utf-8"))
    fix = "re-export; captions come from the narration manifest's aligned words."
    if not srt or not words:
        return [_issue("invalid_value", "captions.srt", "has no cues.", fix)]
    issues: list[ContractIssue] = []
    if vtt != srt:
        issues.append(
            _issue("conflicting_state", "captions.vtt", "disagrees with captions.srt.", fix)
        )
    if srt[0][0] > words[0].start_ms + CAPTION_SLACK_MS:
        message = f"starts at {srt[0][0]} ms, after the narration starts at {words[0].start_ms} ms."
        issues.append(_issue("timing", "captions.srt", message, fix))
    if srt[-1][1] < words[-1].end_ms - CAPTION_SLACK_MS:
        message = f"ends at {srt[-1][1]} ms, before the narration ends at {words[-1].end_ms} ms."
        issues.append(_issue("timing", "captions.srt", message, fix))
    if srt[-1][1] > duration_ms + CAPTION_SLACK_MS:
        message = f"runs to {srt[-1][1]} ms, past the {duration_ms} ms video."
        issues.append(_issue("timing", "captions.srt", message, fix))
    return issues


def _chapter_issues(folder: Path, duration_ms: int) -> list[ContractIssue]:
    raw = json.loads((folder / "chapters.json").read_text(encoding="utf-8"))
    markers = [ChapterMarker.model_validate(item) for item in raw]
    fix = "re-export; chapters come from the script sections' first spoken tokens."
    issues: list[ContractIssue] = []
    if not markers or markers[0].at_s != 0:
        issues.append(_issue("invalid_value", "chapters.json", "does not start at 00:00.", fix))
    stamps = [m.at_s for m in markers]
    if stamps != sorted(set(stamps)):
        issues.append(_issue("invalid_value", "chapters.json", "is not strictly ascending.", fix))
    for marker in markers:
        if marker.at_s * 1000 >= duration_ms:
            message = (
                f"puts {marker.title!r} at {marker.at_s} s, outside the {duration_ms} ms video."
            )
            issues.append(_issue("timing", "chapters.json", message, fix))
    if (folder / "chapters.txt").read_text(encoding="utf-8") != chapters_text(markers):
        issues.append(
            _issue("conflicting_state", "chapters.txt", "disagrees with chapters.json.", fix)
        )
    return issues


def _stem_issues(folder: Path) -> list[ContractIssue]:
    paths = {name: folder / "stems" / f"{name}.wav" for name in STEMS}
    missing = [n for n, p in paths.items() if not p.is_file()]
    fix = "re-run mix, then re-export."
    issues = [_issue("invalid_reference", f"stems/{n}.wav", "is missing.", fix) for n in missing]
    if "master" in missing:
        return issues
    master_ms = wav_duration_ms(paths["master"])
    for name in STEMS[:-1]:
        if name in missing:
            continue
        stem_ms = wav_duration_ms(paths[name])
        if abs(stem_ms - master_ms) > STEM_TOLERANCE_MS:
            message = (
                f"lasts {stem_ms} ms, the master {master_ms} ms (tolerance {STEM_TOLERANCE_MS})."
            )
            issues.append(_issue("timing", f"stems/{name}.wav", message, fix))
    return issues
