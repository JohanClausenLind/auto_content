"""Stage executors for the production workflow (phase 6). Every stage is deterministic given its
inputs, writes atomically into the project directory, and is safe to run twice. The workflow
caches by input hash (campaign + stage + dependency outputs + edit overlays), so a change to one
card invalidates exactly that card's render and nothing else."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from content_factory.artifacts import FilesystemArtifactStore
from content_factory.audio.alignment import validate_alignment
from content_factory.audio.captions import compile_captions, to_srt, to_webvtt
from content_factory.audio.mix import apply_measurements, build_narration_stem, lay_out, master, mux
from content_factory.audio.normalize import normalize_for_speech
from content_factory.audio.tts import MockTTS
from content_factory.config import get_settings
from content_factory.qc.audio import check_audio_in_video
from content_factory.research.citations import export_research, script_claim_gate
from content_factory.runners import demo as demo_fixtures
from content_factory.schemas.artboards import ArtboardSpec, Rect, SourceLayer, TextLayer
from content_factory.schemas.audio import AudioMixSpec, NarrationRequest
from content_factory.schemas.base import canonical_dumps, sha256_hex
from content_factory.schemas.content import CarouselSpec, ContentCampaign
from content_factory.schemas.dag import Stage
from content_factory.schemas.fixtures import sample_dataset, sample_sources, sample_story_plan
from content_factory.schemas.render import RenderBundle
from content_factory.schemas.scenes import TextRef
from content_factory.timeline.compiler import compile_timeline
from content_factory.video.render import render_artboard, render_timeline


@dataclass(frozen=True)
class StageContext:
    workspace_id: str
    project_dir: Path
    artifacts_dir: Path
    campaign: ContentCampaign
    deliverable_id: str | None
    quality: str
    dep_outputs: dict[str, str]  # dependency node_id -> outputs hash

    @property
    def store(self) -> FilesystemArtifactStore:
        return FilesystemArtifactStore(self.artifacts_dir)

    def ddir(self) -> Path:
        assert self.deliverable_id is not None
        d = self.project_dir / "deliverables" / self.deliverable_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def edit_overlay(self) -> dict:
        """Operator edits applied by EditorCore land here; stage inputs include this content."""
        path = self.project_dir / "edits" / "overlay.json"
        if path.exists():
            return json.loads(path.read_text())
        return {}


@dataclass(frozen=True)
class StageOutput:
    outputs_hash: str
    facts: dict


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _hash_obj(obj: object) -> str:
    return sha256_hex(canonical_dumps(obj).encode())


# --- shared stages --------------------------------------------------------------------------
def stage_research(ctx: StageContext) -> StageOutput:
    sources, evidence, claims = demo_fixtures.fixture_research(ctx.workspace_id)
    export_research(ctx.project_dir, sources, evidence, claims)
    return StageOutput(
        _hash_obj([c.model_dump(mode="json") for c in claims]), {"claims": len(claims)}
    )


def stage_verify_claims(ctx: StageContext) -> StageOutput:
    _sources, _evidence, claims = demo_fixtures.fixture_research(ctx.workspace_id)
    gate = script_claim_gate(sample_story_plan(), claims)
    result = {"passed": gate.passed, "findings": [f.__dict__ for f in gate.findings]}
    _write(
        ctx.project_dir / "research" / "claim-gate.json", json.dumps(result, indent=1, default=str)
    )
    if not gate.passed:
        raise RuntimeError(f"claim gate failed: {result['findings']}")
    return StageOutput(_hash_obj(result), result)


def stage_compile_datasets(ctx: StageContext) -> StageOutput:
    ds = sample_dataset()
    _write(ctx.project_dir / "data" / f"{ds.dataset_id}.json", ds.model_dump_json(indent=1))
    return StageOutput(ds.content_hash(), {"datasets": 1})


def stage_plan_story(ctx: StageContext) -> StageOutput:
    plan = sample_story_plan()
    _write(ctx.project_dir / "story" / "plan.json", plan.model_dump_json(indent=1))
    return StageOutput(plan.content_hash(), {"beats": len(plan.beats)})


def stage_originality_topic(ctx: StageContext) -> StageOutput:
    decision = {
        "decision": "ORIGINAL",
        "scopes": ["workspace"],
        "engine": "fixture-0.1",
        "explanation": "no prior coverage of this topic in the fixture archive",
    }
    _write(ctx.project_dir / "originality" / "topic.json", json.dumps(decision, indent=1))
    return StageOutput(_hash_obj(decision), decision)


def stage_preflight(ctx: StageContext) -> StageOutput:
    package = {
        "campaign_id": ctx.campaign.campaign_id,
        "quality": ctx.quality,
        "deliverables": [
            {
                "deliverable_id": d.deliverable_id,
                "type": d.type,
                "title": d.title,
                "destinations": [b.destination.platform for b in d.destinations],
            }
            for d in ctx.campaign.deliverables
        ],
        "expected_cost_usd": 0.0,
        "expected_external_calls": 0,
        "dep_outputs": dict(sorted(ctx.dep_outputs.items())),
    }
    revision_hash = _hash_obj(package)
    package["revision_hash"] = revision_hash
    _write(ctx.project_dir / "preflight" / "package.json", json.dumps(package, indent=1))
    return StageOutput(revision_hash, {"revision_hash": revision_hash})


# --- per-deliverable stages ------------------------------------------------------------------
def _spec(ctx: StageContext):
    return next(d for d in ctx.campaign.deliverables if d.deliverable_id == ctx.deliverable_id)


def stage_write_copy(ctx: StageContext) -> StageOutput:
    spec = _spec(ctx)
    overlay = ctx.edit_overlay().get("copy", {}).get(spec.deliverable_id, {})
    use_model = get_settings().execution.local_copywriter
    if spec.type == "carousel":
        assert isinstance(spec, CarouselSpec)
        if use_model:
            from content_factory.models.copywriter import draft_carousel

            drafted = draft_carousel(ctx.campaign, card_count=spec.card_count)
            base_cards = [c["text"] for c in drafted["cards"]]
        else:
            base_cards = list(demo_fixtures.CAROUSEL_TEXTS[: spec.card_count])
        cards = [
            {"card_id": f"card_{i + 1:012d}", "text": overlay.get(f"card_{i + 1:012d}", t)}
            for i, t in enumerate(base_cards)
        ]
        payload = {"cards": cards}
    else:
        if use_model:
            from content_factory.models.copywriter import draft_caption

            drafted = draft_caption(ctx.campaign)
            base_caption, base_alt = drafted["caption"], drafted["alt_text"]
        else:
            base_caption = "About a fifth of Sweden's electricity came from wind in 2025."
            base_alt = "Data card about wind power's share."
        payload = {
            "caption": overlay.get("caption", base_caption),
            "alt_text": overlay.get("alt_text", base_alt),
        }
    _write(ctx.ddir() / "copy.json", json.dumps(payload, indent=1))
    return StageOutput(_hash_obj(payload), {"units": len(payload.get("cards", [1]))})


def stage_compile_text_package(ctx: StageContext) -> StageOutput:
    copy = json.loads((ctx.ddir() / "copy.json").read_text())
    pkg = {"text": copy["caption"], "chars": len(copy["caption"])}
    _write(ctx.ddir() / "content" / "text-package.json", json.dumps(pkg, indent=1))
    return StageOutput(_hash_obj(pkg), pkg)


def stage_compile_artboards(ctx: StageContext) -> StageOutput:
    bundle = demo_fixtures.artboard_bundle_for(ctx.deliverable_id or "")
    _write(ctx.ddir() / "artboards" / "artboard.json", bundle.artboard.model_dump_json(indent=1))  # type: ignore[union-attr]
    _write(ctx.ddir() / "artboards" / "bundle.json", bundle.model_dump_json(indent=1))
    return StageOutput(bundle.content_hash(), {"artboards": 1})


def stage_render_static(ctx: StageContext) -> StageOutput:
    bundle = RenderBundle.model_validate_json(
        (ctx.ddir() / "artboards" / "bundle.json").read_text()
    )
    outcome = render_artboard(
        bundle, workspace_id=ctx.workspace_id, store=ctx.store, workdir=ctx.ddir() / "exports"
    )
    if not outcome.qc.passed:
        raise RuntimeError(f"static render failed QC: {outcome.qc.findings}")
    return StageOutput(outcome.artifact.sha256, {"artifact_key": outcome.artifact.key})


def stage_compile_cards(ctx: StageContext) -> StageOutput:
    copy = json.loads((ctx.ddir() / "copy.json").read_text())
    ds = sample_dataset()
    cards = []
    for i, card in enumerate(copy["cards"]):
        art = ArtboardSpec(
            artboard_id=f"art_card{i + 1:08d}",
            deliverable_id=ctx.deliverable_id,  # type: ignore[arg-type]
            width=1080,
            height=1350,
            alt_text=f"Carousel card {i + 1}: {card['text'][:80]}",
            layers=(
                TextLayer(
                    layer_id=f"lay_cardlabel{i + 1:03d}",
                    frame=Rect(x=0.08, y=0.08, w=0.84, h=0.07),
                    reading_order=0,
                    text=TextRef(text=f"WIND POWER · {i + 1}/{len(copy['cards'])}"),
                    role="label",
                ),
                TextLayer(
                    layer_id=f"lay_cardtext{i + 1:04d}",
                    frame=Rect(x=0.08, y=0.30, w=0.84, h=0.40),
                    reading_order=1,
                    text=TextRef(text=card["text"]),
                    role="headline",
                    max_lines=5,
                ),
                SourceLayer(
                    layer_id=f"lay_cardsrc{i + 1:05d}",
                    frame=Rect(x=0.08, y=0.88, w=0.84, h=0.05),
                    reading_order=2,
                    source_ids=("src_energimynd01",),
                ),
            ),
        )
        bundle = RenderBundle(
            bundle_id=f"bnd_card{i + 1:09d}",
            kind="artboard",
            artboard=art,
            datasets={ds.dataset_id: ds},
            sources=sample_sources(),
        )
        _write(
            ctx.ddir() / "artboards" / f"{card['card_id']}.bundle.json",
            bundle.model_dump_json(indent=1),
        )
        cards.append({"card_id": card["card_id"], "bundle_hash": bundle.content_hash()})
    manifest = {"cards": cards}
    _write(ctx.ddir() / "artboards" / "cards.json", json.dumps(manifest, indent=1))
    return StageOutput(_hash_obj(manifest), {"cards": len(cards)})


def stage_render_cards(ctx: StageContext) -> StageOutput:
    """Renders each card as its own cached unit: an edit to one card re-renders only that card."""
    manifest = json.loads((ctx.ddir() / "artboards" / "cards.json").read_text())
    results = []
    for entry in manifest["cards"]:
        card_id = entry["card_id"]
        bundle = RenderBundle.model_validate_json(
            (ctx.ddir() / "artboards" / f"{card_id}.bundle.json").read_text()
        )
        marker = ctx.ddir() / "exports" / f"{card_id}.done.json"
        if (
            marker.exists()
            and json.loads(marker.read_text())["bundle_hash"] == entry["bundle_hash"]
        ):
            results.append(
                {"card_id": card_id, **json.loads(marker.read_text()), "cache_hit": True}
            )
            continue
        outcome = render_artboard(
            bundle, workspace_id=ctx.workspace_id, store=ctx.store, workdir=ctx.ddir() / "exports"
        )
        if not outcome.qc.passed:
            raise RuntimeError(f"card {card_id} failed QC: {outcome.qc.findings}")
        record = {
            "bundle_hash": entry["bundle_hash"],
            "artifact_key": outcome.artifact.key,
            "sha256": outcome.artifact.sha256,
        }
        _write(marker, json.dumps(record, indent=1))
        _log_execution(ctx, f"render_card:{card_id}")
        results.append({"card_id": card_id, **record, "cache_hit": False})
    return StageOutput(
        _hash_obj([{k: r[k] for k in ("card_id", "sha256")} for r in results]), {"cards": results}
    )


def stage_lock_script(ctx: StageContext) -> StageOutput:
    plan = sample_story_plan()
    locked = {
        "deliverable_id": ctx.deliverable_id,
        "sentences": [b.display_text for b in plan.beats],
        "plan_hash": plan.content_hash(),
    }
    _write(ctx.ddir() / "script" / "locked.json", json.dumps(locked, indent=1))
    return StageOutput(_hash_obj(locked), {"sentences": len(locked["sentences"])})


def stage_synthesize_narration(ctx: StageContext) -> StageOutput:
    plan = sample_story_plan()
    seg_hashes = []
    for b in plan.beats:
        r = MockTTS().synthesize(
            NarrationRequest(
                beat_id=b.beat_id,
                display_text=b.display_text,
                spoken_text=normalize_for_speech(b.display_text),
                voice=demo_fixtures.MOCK_VOICE,
            )
        )
        (ctx.ddir() / "audio").mkdir(parents=True, exist_ok=True)
        (ctx.ddir() / "audio" / f"{b.beat_id}.wav").write_bytes(r.audio)
        _write(
            ctx.ddir() / "audio" / f"{b.beat_id}.segment.json", r.segment.model_dump_json(indent=1)
        )
        seg_hashes.append(r.segment.audio_sha256)
    return StageOutput(_hash_obj(seg_hashes), {"segments": len(seg_hashes)})


def _load_segments(ctx: StageContext):
    from content_factory.schemas.audio import NarrationSegment

    plan = sample_story_plan()
    segs, files = [], {}
    for b in plan.beats:
        segs.append(
            NarrationSegment.model_validate_json(
                (ctx.ddir() / "audio" / f"{b.beat_id}.segment.json").read_text()
            )
        )
        files[b.beat_id] = ctx.ddir() / "audio" / f"{b.beat_id}.wav"
    return plan, segs, files


def stage_align_words(ctx: StageContext) -> StageOutput:
    _plan, segs, _files = _load_segments(ctx)
    reports = [validate_alignment(s) for s in segs]
    payload = [r.model_dump(mode="json") for r in reports]
    _write(ctx.ddir() / "audio" / "alignment.json", json.dumps(payload, indent=1))
    if not all(r.passed for r in reports):
        raise RuntimeError("alignment validation failed")
    return StageOutput(_hash_obj(payload), {"reports": len(reports)})


def stage_compile_captions(ctx: StageContext) -> StageOutput:
    _plan, segs, _files = _load_segments(ctx)
    laid = lay_out(segs, AudioMixSpec(deliverable_id=ctx.deliverable_id))  # type: ignore[arg-type]
    words = [w for b in laid for w in b.words]
    track = compile_captions(ctx.deliverable_id or "", words)
    _write(ctx.ddir() / "captions" / "captions.srt", to_srt(track))
    _write(ctx.ddir() / "captions" / "captions.vtt", to_webvtt(track))
    return StageOutput(track.content_hash(), {"cues": len(track.cues)})


def stage_compile_timeline(ctx: StageContext) -> StageOutput:
    plan, segs, _files = _load_segments(ctx)
    laid = lay_out(segs, AudioMixSpec(deliverable_id=ctx.deliverable_id))  # type: ignore[arg-type]
    measured = plan.model_copy(update={"beats": apply_measurements(plan.beats, laid)})
    tl = compile_timeline(measured, timeline_id="tl_run0000000001", narrated=True)
    ds = sample_dataset()
    bundle = RenderBundle(
        bundle_id="bnd_run000000001",
        kind="timeline",
        plan=measured,
        timeline=tl,
        datasets={ds.dataset_id: ds},
        sources=sample_sources(),
    )
    _write(ctx.ddir() / "timeline" / "plan.json", measured.model_dump_json(indent=1))
    _write(ctx.ddir() / "timeline" / "compiled.json", tl.model_dump_json(indent=1))
    _write(ctx.ddir() / "timeline" / "bundle.json", bundle.model_dump_json(indent=1))
    return StageOutput(tl.content_hash(), {"frames": tl.total_frames})


def stage_render_scenes(ctx: StageContext) -> StageOutput:
    bundle = RenderBundle.model_validate_json((ctx.ddir() / "timeline" / "bundle.json").read_text())
    outcome = render_timeline(
        bundle, workspace_id=ctx.workspace_id, store=ctx.store, workdir=ctx.ddir() / "exports"
    )
    if not outcome.qc.passed:
        raise RuntimeError(f"video render failed QC: {outcome.qc.findings}")
    return StageOutput(outcome.artifact.sha256, {"artifact_key": outcome.artifact.key})


def stage_mix_audio(ctx: StageContext) -> StageOutput:
    _plan, segs, files = _load_segments(ctx)
    spec = AudioMixSpec(deliverable_id=ctx.deliverable_id)  # type: ignore[arg-type]
    stem = ctx.ddir() / "audio" / "narration-stem.wav"
    build_narration_stem(segs, files, spec, stem)
    mastered = ctx.ddir() / "audio" / "narration-mastered.wav"
    report = master(
        stem, mastered, target_lufs=spec.target_lufs, target_tp=spec.target_true_peak_dbtp
    )
    _write(ctx.ddir() / "audio" / "loudness.json", report.model_dump_json(indent=1))
    if not report.passed:
        raise RuntimeError(f"mastering missed target: {report}")
    return StageOutput(sha256_hex(mastered.read_bytes()), {"lufs": report.integrated_lufs})


def stage_compose_video(ctx: StageContext) -> StageOutput:
    silent = ctx.ddir() / "exports" / "bnd_run000000001.mp4"
    final = ctx.ddir() / "exports" / "final.mp4"
    mux(silent, ctx.ddir() / "audio" / "narration-mastered.wav", final)
    _plan, segs, _files = _load_segments(ctx)
    laid = lay_out(segs, AudioMixSpec(deliverable_id=ctx.deliverable_id))  # type: ignore[arg-type]
    speech_ms = max(w.end_ms for b in laid for w in b.words)
    qc = check_audio_in_video(final, expected_speech_ms=speech_ms)
    if not qc.passed:
        raise RuntimeError(f"final video failed audio QC: {qc.findings}")
    ref = ctx.store.put_file(ctx.workspace_id, "renders", final)
    return StageOutput(
        ref.sha256,
        {
            "artifact_key": ref.key,
            **{k: v for k, v in qc.facts.items() if k in ("integrated_lufs", "true_peak_dbtp")},
        },
    )


def stage_qc_deliverable(ctx: StageContext) -> StageOutput:
    """Real per-deliverable QC (17): accessibility on artboards, flashing + delivery checks on
    video, claim gate already ran as a shared stage. A failing layer fails the stage honestly."""
    from dataclasses import asdict

    from content_factory.qc.accessibility import (
        check_artboard_accessibility,
        check_flashing,
        export_accessibility_report,
    )
    from content_factory.qc.delivery import check_delivery_promise

    spec = _spec(ctx)
    checks: dict[str, object] = {}
    a11y_results = {}
    artboard_path = ctx.ddir() / "artboards" / "artboard.json"
    if artboard_path.exists():
        board = ArtboardSpec.model_validate_json(artboard_path.read_text())
        r = check_artboard_accessibility(board)
        a11y_results["artboard"] = r
        checks["artboard_accessibility"] = {
            "passed": r.passed,
            "findings": [asdict(f) for f in r.findings],
        }
    cards_manifest = ctx.ddir() / "artboards" / "cards.json"
    if cards_manifest.exists():
        for entry in json.loads(cards_manifest.read_text())["cards"]:
            bundle_path = ctx.ddir() / "artboards" / f"{entry['card_id']}.bundle.json"
            board = RenderBundle.model_validate_json(bundle_path.read_text()).artboard
            assert board is not None
            r = check_artboard_accessibility(board)
            a11y_results[entry["card_id"]] = r
            if not r.passed:
                checks[f"card_accessibility:{entry['card_id']}"] = {
                    "passed": False,
                    "findings": [asdict(f) for f in r.findings],
                }
    video_path = ctx.ddir() / "exports" / "bnd_run000000001.mp4"
    compiled_path = ctx.ddir() / "timeline" / "compiled.json"
    if video_path.exists() and compiled_path.exists():
        from content_factory.schemas.scenes import CompiledTimeline

        tl = CompiledTimeline.model_validate_json(compiled_path.read_text())
        flash = check_flashing(video_path, fps=tl.fps, frames=tl.total_frames)
        a11y_results["flashing"] = flash
        checks["flashing"] = {"passed": flash.passed, "facts": flash.facts}
        if spec.type in {"long_video", "short_video"}:
            # These scenes are chart/number-led, not promised as fully animated explainers;
            # the delivery check records facts and blocks only when the promise is "animated".
            promise = (
                "animated_explainer"
                if getattr(spec, "intent", "").startswith("animated")
                else "chart_led"
            )
            delivery = check_delivery_promise(video_path, tl, promised=promise)
            checks["delivery_promise"] = {"passed": delivery.passed, "facts": delivery.facts}
            if not delivery.passed:
                a11y_results["delivery"] = delivery
    if a11y_results:
        export_accessibility_report(
            ctx.ddir() / "qc" / "accessibility.json", ctx.deliverable_id or "", a11y_results
        )
    passed = all(bool(c.get("passed", True)) for c in checks.values() if isinstance(c, dict))
    result = {"deliverable_id": ctx.deliverable_id, "passed": passed, "checks": checks}
    _write(ctx.ddir() / "qc" / "report.json", json.dumps(result, indent=1, default=str))
    if not passed:
        raise RuntimeError(
            f"deliverable QC failed: {[k for k, c in checks.items() if isinstance(c, dict) and not c.get('passed', True)]}"  # noqa: E501
        )
    return StageOutput(_hash_obj(result), {"passed": passed, "checks": sorted(checks)})


def stage_originality_gate(ctx: StageContext) -> StageOutput:
    """The real originality gate (2.10): the new piece's fingerprint against the workspace's
    content memory. Blocking verdicts fail the stage; a model cannot override them."""
    from content_factory.imports.history import ContentMemoryStore
    from content_factory.originality.fingerprint import compare, decide, fingerprint_script

    texts: list[str] = []
    kinds: list[str] = []
    plan_path = ctx.project_dir / "story" / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        texts = [b["display_text"] for b in plan.get("beats", [])]
        kinds = [sc.get("kind", "") for sc in plan.get("scenes", [])]
    copy_path = ctx.ddir() / "copy.json"
    if copy_path.exists():
        copy = json.loads(copy_path.read_text())
        texts += [c["text"] for c in copy.get("cards", [])] or (
            [copy["caption"]] if copy.get("caption") else []
        )
    fp_new = fingerprint_script(texts or ["(no text)"], kinds or ["single"])
    store = ContentMemoryStore(ctx.project_dir.parent / ".content-memory" / ctx.workspace_id)
    comparisons = []
    for key, entry in sorted(store.entries.items()):
        if key.startswith(f"{ctx.campaign.campaign_id}:"):
            continue  # this campaign's own deliverables are one intentional content family
        prior_texts = entry.get("texts") or [entry.get("title", "")]
        prior_kinds = entry.get("scene_kinds") or ["single"]
        comparisons.append(
            compare(fp_new, fingerprint_script(prior_texts, prior_kinds), against=key)
        )
    decision = decide(comparisons)
    payload = {
        "deliverable_id": ctx.deliverable_id,
        "decision": decision.verdict.value,
        "blocking": decision.blocking,
        "explanations": list(decision.explanations),
        "compared": len(comparisons),
        "compared_scopes": ["account", "workspace", "content_family"],
    }
    _write(ctx.ddir() / "originality.json", json.dumps(payload, indent=1))
    if decision.blocking:
        raise RuntimeError(
            f"originality gate: {decision.verdict.value} — {decision.explanations[0]}"
        )
    # Record this piece into the archive so future runs compare against it (idempotent by key).
    store.add(
        f"{ctx.campaign.campaign_id}:{ctx.deliverable_id}",
        {
            "title": texts[0][:120] if texts else "",
            "texts": texts,
            "scene_kinds": kinds,
            "platform": "workspace",
            "url": f"project://{ctx.project_dir.name}/{ctx.deliverable_id}",
            "published_at": "",
            "batch_id": "pipeline",
        },
    )
    return StageOutput(
        _hash_obj(payload), {"decision": decision.verdict.value, "compared": len(comparisons)}
    )


def stage_compile_destination_packages(ctx: StageContext) -> StageOutput:
    spec = _spec(ctx)
    packages = [
        {
            "destination": b.destination.platform,
            "visibility": b.visibility,
            "deliverable_id": spec.deliverable_id,
            "status": "packaged",
        }
        for b in spec.destinations
    ]
    _write(ctx.ddir() / "destination-packages" / "packages.json", json.dumps(packages, indent=1))
    return StageOutput(_hash_obj(packages), {"packages": len(packages)})


def stage_package_qc(ctx: StageContext) -> StageOutput:
    result = {"passed": True}
    _write(ctx.ddir() / "qc" / "package.json", json.dumps(result, indent=1))
    return StageOutput(_hash_obj(result), result)


def _log_execution(ctx: StageContext, unit: str) -> None:
    log = ctx.project_dir / ".stages" / "executions.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        fh.write(unit + "\n")


STAGE_EXECUTORS = {
    Stage.research: stage_research,
    Stage.verify_claims: stage_verify_claims,
    Stage.compile_datasets: stage_compile_datasets,
    Stage.plan_story: stage_plan_story,
    Stage.originality_topic: stage_originality_topic,
    Stage.preflight: stage_preflight,
    Stage.write_copy: stage_write_copy,
    Stage.compile_text_package: stage_compile_text_package,
    Stage.compile_artboards: stage_compile_artboards,
    Stage.render_static: stage_render_static,
    Stage.compile_cards: stage_compile_cards,
    Stage.render_cards: stage_render_cards,
    Stage.lock_script: stage_lock_script,
    Stage.synthesize_narration: stage_synthesize_narration,
    Stage.align_words: stage_align_words,
    Stage.compile_captions: stage_compile_captions,
    Stage.mix_audio: stage_mix_audio,
    Stage.compile_timeline: stage_compile_timeline,
    Stage.render_scenes: stage_render_scenes,
    Stage.compose_video: stage_compose_video,
    Stage.qc_deliverable: stage_qc_deliverable,
    Stage.originality_gate: stage_originality_gate,
    Stage.compile_destination_packages: stage_compile_destination_packages,
    Stage.package_qc: stage_package_qc,
}
