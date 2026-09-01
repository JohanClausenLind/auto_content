"""Offline demo runner: fixture campaign → pruned DAG → deliverables → QC → run report.

quality "smoke": silent single-scene video render (fast).
quality "demo":  full narrated pipeline — mock TTS per beat, measured word timings, narrated
timeline, full render, mastered stem (-14 LUFS / -1 dBTP), mux, captions (SRT + WebVTT),
alignment + audio + video QC. No network, no models, no credentials.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from content_factory.artifacts import FilesystemArtifactStore
from content_factory.audio.alignment import validate_alignment
from content_factory.audio.captions import compile_captions, to_srt, to_webvtt
from content_factory.audio.mix import apply_measurements, build_narration_stem, lay_out, master, mux
from content_factory.audio.normalize import normalize_for_speech
from content_factory.audio.tts import MockTTS
from content_factory.deliverables.dag_compiler import compile_dag
from content_factory.qc.audio import check_audio_in_video
from content_factory.research.citations import export_research, script_claim_gate
from content_factory.research.claims import build_claim
from content_factory.schemas.audio import AudioMixSpec, NarrationRequest, VoiceIdentity
from content_factory.schemas.fixtures import (
    WS,
    sample_artboard_bundle,
    sample_campaign,
    sample_dataset,
    sample_sources,
    sample_story_plan,
)
from content_factory.schemas.render import RenderBundle
from content_factory.schemas.research import (
    EvidenceLocator,
    EvidenceRecord,
    SourceClass,
    SourceRecord,
)
from content_factory.timeline.compiler import compile_timeline
from content_factory.video.render import render_artboard, render_timeline

REPO_ROOT = Path(__file__).resolve().parents[3]
MOCK_VOICE = VoiceIdentity(provider="mock", voice_id="narrator-a", model_revision="mock-1")


def run_demo(
    *, quality: str = "smoke", projects_dir: Path | None = None, artifacts_dir: Path | None = None
) -> dict:
    t0 = time.monotonic()
    projects_dir = projects_dir or REPO_ROOT / "projects"
    store = FilesystemArtifactStore(artifacts_dir or REPO_ROOT / "data" / "artifacts")
    campaign = sample_campaign()
    project_id = "prj_demo00000001"
    root = projects_dir / project_id
    (root / "deliverables").mkdir(parents=True, exist_ok=True)
    dag = compile_dag(campaign)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "workspace_id": WS,
                "campaign_id": campaign.campaign_id,
                "quality": quality,
            },
            indent=1,
        )
    )
    (root / "brief.json").write_text(campaign.brief.model_dump_json(indent=1))
    (root / "dag.json").write_text(dag.model_dump_json(indent=1))

    report: dict = {
        "project_id": project_id,
        "quality": quality,
        "deliverables": {},
        "dag_nodes": len(dag.nodes),
        "dag_pruned": len(dag.pruned),
    }

    # --- research/claims (fixture evidence; the script gate blocks uncited critical claims) --
    sources = [
        SourceRecord(
            source_id="src_energimynd01",
            workspace_id=WS,
            canonical_url="https://example.se/wind-2025",
            requested_url="https://example.se/wind-2025",
            final_url="https://example.se/wind-2025",
            title="Wind power supplied 21 percent of Sweden's electricity in 2025 (fixture)",
            publisher="Energimyndigheten",
            accessed_at="2026-09-01",
            capture_sha256="0" * 64,
            content_type="text/html",
            size_bytes=2048,
            published_at="2026-03-01",
            classification=SourceClass.official,
        ),
        SourceRecord(
            source_id="src_svk00000001",
            workspace_id=WS,
            canonical_url="https://example.se/grid-2025",
            requested_url="https://example.se/grid-2025",
            final_url="https://example.se/grid-2025",
            title="Grid statistics 2025 (fixture)",
            publisher="Svenska kraftnät",
            accessed_at="2026-09-01",
            capture_sha256="1" * 64,
            content_type="text/html",
            size_bytes=1024,
            published_at="2026-02-10",
            classification=SourceClass.official,
        ),
    ]
    evidence = [
        EvidenceRecord(
            evidence_id="evd_wind0000001",
            source_id="src_energimynd01",
            excerpt="wind power generated 34.9 TWh in 2025, about 21 percent of Sweden's total electricity generation",  # noqa: E501
            locator=EvidenceLocator(kind="char_range", start=120, end=240),
            captured_at="2026-09-01",
        ),
        EvidenceRecord(
            evidence_id="evd_wind0000002",
            source_id="src_energimynd01",
            excerpt="The share has roughly doubled since 2018, when wind supplied 11 percent.",
            locator=EvidenceLocator(kind="char_range", start=241, end=320),
            captured_at="2026-09-01",
        ),
    ]
    claims = [
        build_claim(
            "clm_wind0000001",
            WS,
            "In 2025, wind supplied about 21% of Sweden's electricity.",
            [evidence[0]],
            dataset=sample_dataset(),
            dataset_id="ds_wind00000001",
            sources_by_id={s.source_id: s for s in sources},
            checked_at="2026-09-01",
        ),
        build_claim(
            "clm_wind0000002",
            WS,
            "That share has roughly doubled since 2018, from 11% to 21%.",
            [evidence[1]],
            sources_by_id={s.source_id: s for s in sources},
            checked_at="2026-09-01",
        ),
    ]
    export_research(root, sources, evidence, claims)
    gate = script_claim_gate(sample_story_plan(), claims)
    report["claim_gate"] = {
        "passed": gate.passed,
        "findings": [f.__dict__ for f in gate.findings],
        "facts": gate.facts,
    }
    report["claims"] = {
        c.claim_id: {"status": c.status.value, "caveats": list(c.caveats)} for c in claims
    }
    if not gate.passed:
        report["passed"] = False
        (root / "final").mkdir(exist_ok=True)
        (root / "final" / "run-report.json").write_text(json.dumps(report, indent=1, default=str))
        return report

    # --- static deliverable -----------------------------------------------------------------
    art = sample_artboard_bundle()
    assert art.artboard is not None
    art_spec = art.artboard
    ddir = root / "deliverables" / art_spec.deliverable_id
    (ddir / "artboards").mkdir(parents=True, exist_ok=True)
    (ddir / "spec.json").write_text(
        next(
            d for d in campaign.deliverables if d.deliverable_id == art_spec.deliverable_id
        ).model_dump_json(indent=1)
    )
    (ddir / "artboards" / "artboard.json").write_text(art_spec.model_dump_json(indent=1))
    outcome = render_artboard(art, workspace_id=WS, store=store, workdir=ddir / "exports")
    report["deliverables"][art_spec.deliverable_id] = {
        "type": "single_image_post",
        "artifact": asdict(outcome.artifact),
        "qc_passed": outcome.qc.passed,
        "qc": [asdict(f) for f in outcome.qc.findings],
        "facts": outcome.qc.facts,
        "bundle_sha256": outcome.bundle_sha256,
    }

    # --- video deliverable ------------------------------------------------------------------
    plan = sample_story_plan()
    vdir = root / "deliverables" / plan.deliverable_id
    (vdir / "timeline").mkdir(parents=True, exist_ok=True)
    (vdir / "audio").mkdir(parents=True, exist_ok=True)
    (vdir / "captions").mkdir(parents=True, exist_ok=True)
    (vdir / "spec.json").write_text(
        next(
            d for d in campaign.deliverables if d.deliverable_id == plan.deliverable_id
        ).model_dump_json(indent=1)
    )
    narrated = quality == "demo"
    video_entry: dict = {"type": "short_video", "narrated": narrated}
    mix_spec: AudioMixSpec | None = None
    segments: list = []
    files: dict[str, Path] = {}
    laid: list = []

    if narrated:
        mix_spec = AudioMixSpec(deliverable_id=plan.deliverable_id)
        alignment_reports = []
        for b in plan.beats:
            r = MockTTS().synthesize(
                NarrationRequest(
                    beat_id=b.beat_id,
                    display_text=b.display_text,
                    spoken_text=normalize_for_speech(b.display_text),
                    voice=MOCK_VOICE,
                )
            )
            segments.append(r.segment)
            f = vdir / "audio" / f"{b.beat_id}.wav"
            f.write_bytes(r.audio)
            files[b.beat_id] = f
            alignment_reports.append(validate_alignment(r.segment))
            (vdir / "audio" / f"{b.beat_id}.segment.json").write_text(
                r.segment.model_dump_json(indent=1)
            )
        video_entry["alignment_passed"] = all(a.passed for a in alignment_reports)
        video_entry["alignment"] = [a.model_dump(mode="json") for a in alignment_reports]
        laid = lay_out(segments, mix_spec)
        plan = plan.model_copy(update={"beats": apply_measurements(plan.beats, laid)})
        timed_words = [w for b in laid for w in b.words]
        track = compile_captions(plan.deliverable_id, timed_words)
        (vdir / "captions" / "captions.srt").write_text(to_srt(track))
        (vdir / "captions" / "captions.vtt").write_text(to_webvtt(track))
        video_entry["caption_cues"] = len(track.cues)

    tl = compile_timeline(plan, timeline_id="tl_demo000000001", narrated=narrated)
    bundle = RenderBundle(
        bundle_id="bnd_tl0000000001",
        kind="timeline",
        plan=plan,
        timeline=tl,
        datasets={"ds_wind00000001": sample_dataset()},
        sources=sample_sources(),
    )
    (vdir / "timeline" / "plan.json").write_text(plan.model_dump_json(indent=1))
    (vdir / "timeline" / "compiled.json").write_text(tl.model_dump_json(indent=1))
    scene_id = tl.scenes[1].scene_id if quality == "smoke" else None
    outcome_v = render_timeline(
        bundle, workspace_id=WS, store=store, workdir=vdir / "exports", scene_id=scene_id
    )
    video_entry.update(
        {
            "rendered_scope": scene_id or "full",
            "artifact": asdict(outcome_v.artifact),
            "qc_passed": outcome_v.qc.passed,
            "qc": [asdict(f) for f in outcome_v.qc.findings],
            "facts": outcome_v.qc.facts,
            "bundle_sha256": outcome_v.bundle_sha256,
        }
    )

    if narrated:
        assert mix_spec is not None
        stem = vdir / "audio" / "narration-stem.wav"
        total_ms = build_narration_stem(segments, files, mix_spec, stem)
        mastered = vdir / "audio" / "narration-mastered.wav"
        loudness = master(
            stem,
            mastered,
            target_lufs=mix_spec.target_lufs,
            target_tp=mix_spec.target_true_peak_dbtp,
        )
        video_entry["stem_ms"] = total_ms
        video_entry["loudness"] = loudness.model_dump(mode="json")
        silent_mp4 = vdir / "exports" / f"{bundle.bundle_id}.mp4"
        final_mp4 = vdir / "exports" / f"{bundle.bundle_id}.narrated.mp4"
        mux(silent_mp4, mastered, final_mp4)
        speech_ms = max(w.end_ms for b in laid for w in b.words)
        audio_qc = check_audio_in_video(
            final_mp4,
            expected_speech_ms=speech_ms,
            target_lufs=mix_spec.target_lufs,
            target_tp=mix_spec.target_true_peak_dbtp,
        )
        ref = store.put_file(WS, "renders", final_mp4)
        video_entry["final_artifact"] = asdict(ref)
        video_entry["audio_qc_passed"] = audio_qc.passed
        video_entry["audio_qc"] = [asdict(f) for f in audio_qc.findings]
        video_entry["audio_facts"] = audio_qc.facts
        video_entry["qc_passed"] = (
            video_entry["qc_passed"] and audio_qc.passed and video_entry["alignment_passed"]
        )

    report["deliverables"][plan.deliverable_id] = video_entry
    report["elapsed_s"] = round(time.monotonic() - t0, 1)
    report["passed"] = report["claim_gate"]["passed"] and all(
        d["qc_passed"] for d in report["deliverables"].values()
    )
    (root / "final").mkdir(exist_ok=True)
    (root / "final" / "run-report.json").write_text(json.dumps(report, indent=1, default=str))
    return report
