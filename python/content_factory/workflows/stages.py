"""Stage executors for the production workflow (phase 6)."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from content_factory.artifacts import ArtifactStore, open_store
from content_factory.audio.alignment import validate_alignment
from content_factory.audio.captions import to_srt, to_webvtt, wrap_words
from content_factory.audio.languages import aligner_model_for, check_narration_language
from content_factory.audio.mix import (
    add_music_bed,
    apply_measurements,
    build_narration_stem,
    ffmpeg,
    lay_out,
    master,
    mux,
)
from content_factory.audio.normalize import NORMALIZATION_VERSION, normalize_for_speech
from content_factory.audio.tts import MockTTS, TTSExecutor
from content_factory.config import get_settings
from content_factory.deliverables.documentary import (
    aspect_dimensions,
    aspect_or_portrait,
    episode_outline,
    find_documentary,
    plan_shorts,
    short_story_plan,
)
from content_factory.qc.audio import check_audio_in_video
from content_factory.research.citations import export_research, script_claim_gate
from content_factory.runners import demo as demo_fixtures
from content_factory.schemas.artboards import ArtboardSpec, Rect, SourceLayer, TextLayer
from content_factory.schemas.audio import (
    AudioMixSpec,
    MasterChainSpec,
    MusicTrack,
    NarrationRequest,
    PronunciationEntry,
    SpeechRestorationSpec,
    VoiceIdentity,
)
from content_factory.schemas.base import canonical_dumps, file_sha256, sha256_hex
from content_factory.schemas.content import CarouselSpec, ContentCampaign
from content_factory.schemas.dag import Stage
from content_factory.schemas.fixtures import (
    sample_dataset,
    sample_motion_plan,
    sample_sources,
    sample_story_lexicon,
    sample_story_plan,
)
from content_factory.schemas.render import RenderBundle
from content_factory.schemas.scenes import CompiledTimeline, StoryPlan, TextRef
from content_factory.schemas.sequences import (
    ControlKind,
    GenerationLock,
    MotionPlan,
)
from content_factory.schemas.shots import (
    ControlBundle,
    SegmentClipPose,
    ShotPlan,
    ShotRouting,
    ShotSpec,
)
from content_factory.sequences.control_compile import (
    COMPILER_VERSION as CONTROL_COMPILER_VERSION,
)
from content_factory.sequences.control_compile import compile_bundle_from_motion_plan
from content_factory.sequences.engine import (
    ControlConditioning,
    MockReferenceEditBackend,
    ReferenceEditBackend,
    build_sequence,
    package_sequence,
)
from content_factory.sequences.styles import resolve_style
from content_factory.shots import load_fixture_plan, plan_shots_from_story, route_shots
from content_factory.shots.prompt_compile import PROMPT_COMPILER_VERSION, compile_video_prompt
from content_factory.timeline.compiler import compile_timeline
from content_factory.video.render import REPO_ROOT, render_artboard, render_timeline
from content_factory.workflows.blocked import BlockedError

if TYPE_CHECKING:  # the runtime import stays inside `_delivery_files`
    from content_factory.schemas.delivery import DeliveryFile


@dataclass(frozen=True)
class StageContext:
    workspace_id: str
    project_dir: Path
    artifacts_dir: Path
    campaign: ContentCampaign
    deliverable_id: str | None
    quality: str
    dep_outputs: dict[str, str]  # dependency node_id -> outputs hash
    # Widget values frozen onto this DAG node by the workspace-graph compiler. A stage reads the
    # keys it knows through :meth:`param`; anything else stays inert.
    params: dict[str, str] = field(default_factory=dict)

    @property
    def store(self) -> ArtifactStore:
        return open_store(self.artifacts_dir)

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


def _param(ctx: StageContext, key: str, default: str = "") -> str:
    """A node parameter as the canvas set it; the configured default when the node did not."""
    value = ctx.params.get(key)
    return default if value is None or value == "" else str(value)


def _param_int(ctx: StageContext, key: str, default: int) -> int:
    try:
        return int(float(_param(ctx, key, str(default))))
    except ValueError:
        return default


def _param_float(ctx: StageContext, key: str, default: float) -> float:
    try:
        return float(_param(ctx, key, str(default)))
    except ValueError:
        return default


def _param_bool(ctx: StageContext, key: str, default: bool) -> bool:
    return _param(ctx, key, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _param_list(ctx: StageContext, key: str, default: Sequence[str]) -> tuple[str, ...]:
    """A list widget: a JSON array or a comma/space separated list; the setting when unset."""
    raw = _param(ctx, key).strip()
    if not raw:
        return tuple(default)
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return tuple(str(x).strip() for x in parsed if str(x).strip())
        # A bracketed list that is not valid JSON — `[image]`, `[title, quote]`, which is what a
        # person types and what YAML hands through as a plain string.
        raw = raw[1:-1] if raw.endswith("]") else raw[1:]
    return tuple(
        part.strip().strip("\"'") for part in raw.replace(",", " ").split() if part.strip("\"' ")
    )


def _param_int_list(ctx: StageContext, key: str, default: Sequence[int]) -> tuple[int, ...]:
    """The same, for integers. A non-numeric entry is refused by name rather than dropped."""
    raw = _param_list(ctx, key, [str(v) for v in default])
    out: list[int] = []
    for item in raw:
        if not item.lstrip("-").isdigit():
            msg = f"{key} takes whole numbers; {item!r} is not one"
            raise RuntimeError(msg)
        out.append(int(item))
    return tuple(out)


def _param_size(ctx: StageContext, key: str, default: tuple[int, int]) -> tuple[int, int]:
    """A "WxH" widget value; the configured size when it is absent or malformed."""
    raw = _param(ctx, key)
    if "x" not in raw.lower():
        return default
    w, _, h = raw.lower().partition("x")
    try:
        return int(w), int(h)
    except ValueError:
        return default


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _hash_obj(obj: object) -> str:
    return sha256_hex(canonical_dumps(obj).encode())


# --- shared stages --------------------------------------------------------------------------
UPLOADS_DIRNAME = "uploads"
"""Where an operator drops files for a run, relative to the project directory. A plain directory
rather than an API: the whole point of ``ingest`` is that a spreadsheet somebody already has can
become a film without going through a web form first."""


def _claim_digest(claims: Sequence) -> str:
    """A claim set's identity, with the check DATE left out of it."""
    return _hash_obj(
        [{k: v for k, v in c.model_dump(mode="json").items() if k != "checked_at"} for c in claims]
    )


def stage_ingest(ctx: StageContext) -> StageOutput:
    """Every file in ``<project>/uploads`` into the artifact store as a typed source."""
    import tempfile

    from content_factory.datasets import is_transform_sidecar
    from content_factory.ingest.convert import ConversionError, convert_media, needs_conversion
    from content_factory.ingest.uploads import (
        UploadRejectedError,
        ingest_upload,
        sniff_mime,
    )
    from content_factory.schemas.research import SourceClass, SourceRecord

    uploads = ctx.project_dir / UPLOADS_DIRNAME
    # A `<file>.transforms.json` sidecar declares how to shape the upload it sits beside.
    files = (
        sorted(p for p in uploads.glob("**/*") if p.is_file() and not is_transform_sidecar(p))
        if uploads.is_dir()
        else []
    )
    sidecars = (
        sorted(p.name for p in uploads.glob("**/*") if is_transform_sidecar(p))
        if uploads.is_dir()
        else []
    )
    if not files:
        # Not an error. Most lanes have nothing to ingest, and the stage has to be harmless in
        # them; what it must not do is pretend it found something.
        _write(
            ctx.project_dir / "research" / "uploads.json",
            json.dumps(
                {
                    "uploads_dir": str(uploads),
                    "files": [],
                    "transform_sidecars": sidecars,
                    "note": "nothing to ingest",
                }
            ),
        )
        return StageOutput(_hash_obj({"uploads": []}), {"uploads": 0, "sources": 0})

    store = ctx.store
    records: list[dict] = []
    sources: list[SourceRecord] = []
    rejected: list[str] = []
    # Conversions land here and are thrown away with it: the operator's own file in uploads/ is
    # never modified.
    with tempfile.TemporaryDirectory(prefix="cf-convert-") as convert_dir:
        for path in files:
            rel = path.relative_to(uploads).as_posix()
            usable, conversion = path, None
            try:
                sniffed = sniff_mime(path)
                if needs_conversion(sniffed):
                    conversion = convert_media(path, sniffed, Path(convert_dir))
                    usable = conversion.path
            except ConversionError as exc:
                rejected.append(f"{rel}: could not be converted: {exc}")
                continue
            try:
                ingested = ingest_upload(store, ctx.workspace_id, usable)
            except UploadRejectedError as exc:
                rejected.append(f"{rel}: {exc}")
                continue
            record = {
                "file": rel,
                "kind": ingested.kind,
                "mime": ingested.sniffed_mime,
                "artifact_key": ingested.artifact.key,
                "sha256": ingested.artifact.sha256,
                "bytes": usable.stat().st_size,
            }
            if conversion is not None:
                # The record has to carry both, or a later reader cannot tell why the manifest's
                # mime disagrees with the file still sitting in uploads/.
                record["converted_from"] = conversion.from_mime
                record["conversion"] = conversion.action
                record["conversion_detail"] = conversion.detail
                record["stored_as"] = usable.name
                # A scene naming this asset must be handed the converted copy in the artifact
                # store, not the .mkv in uploads/ that the renderer cannot open.
                record["asset_path"] = str(ctx.artifacts_dir / ingested.artifact.key)
            records.append(record)
            # An uploaded file IS a source: it is what a claim about it resolves to. The URL is
            # the file's own path, because inventing an http one would be inventing provenance.
            sources.append(
                SourceRecord(
                    source_id=f"src_{sha256_hex(rel.encode())[:12]}",
                    workspace_id=ctx.workspace_id,
                    canonical_url=f"file://{rel}",
                    requested_url=f"file://{rel}",
                    final_url=f"file://{rel}",
                    title=path.name,
                    publisher="",
                    accessed_at=_today(),
                    capture_sha256=ingested.artifact.sha256,
                    content_type=ingested.sniffed_mime,
                    size_bytes=record["bytes"],
                    classification=SourceClass.operator_upload,
                )
            )
    if rejected:
        raise RuntimeError(
            "ingest refused "
            + f"{len(rejected)} of {len(files)} upload(s):\n  "
            + "\n  ".join(rejected)
        )
    manifest = {
        "uploads_dir": str(uploads),
        "files": records,
        "transform_sidecars": sidecars,
        # asset_id -> path, in the shape RenderBundle.assets takes, so a Map/Screenshot/Manim
        # scene can name an uploaded file from any lane.
        "assets": {
            f"ast_{r['sha256'][:12]}": r.get("asset_path") or str(uploads / r["file"])
            for r in records
            if r["kind"] in ("image", "video", "audio", "document")
        },
    }
    _write(
        ctx.project_dir / "research" / "uploads.json",
        json.dumps(manifest, indent=1, sort_keys=True),
    )
    _write(
        ctx.project_dir / "research" / "upload-sources.json",
        json.dumps([s.model_dump(mode="json") for s in sources], indent=1, sort_keys=True),
    )
    return StageOutput(
        _hash_obj([r["sha256"] for r in records]),
        {
            "uploads": len(records),
            "sources": len(sources),
            "kinds": sorted({r["kind"] for r in records}),
            "assets": len(manifest["assets"]),
            "transform_sidecars": len(sidecars),
        },
    )


def _today() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).date().isoformat()


def stage_research(ctx: StageContext) -> StageOutput:
    """Sources, evidence and claims for the run."""
    sources, evidence, claims = demo_fixtures.fixture_research(ctx.workspace_id)
    if get_settings().execution.live_research:
        from content_factory.research.pipeline import research_topic

        live = research_topic(
            ctx.campaign.brief.topic,
            workspace_id=ctx.workspace_id,
            objective=ctx.campaign.brief.objective,
        )
        sources, evidence, claims = live.sources, live.evidence, live.claims
    uploaded = ctx.project_dir / "research" / "upload-sources.json"
    if uploaded.exists():
        from content_factory.schemas.research import SourceRecord

        known = {s.source_id for s in sources}
        sources = sources + [
            SourceRecord.model_validate(raw)
            for raw in json.loads(uploaded.read_text())
            if raw["source_id"] not in known
        ]
    export_research(ctx.project_dir, sources, evidence, claims)
    return StageOutput(
        _claim_digest(claims),
        {
            "claims": len(claims),
            "sources": len(sources),
            "evidence": len(evidence),
            "live": get_settings().execution.live_research,
        },
    )


def stage_verify_claims(ctx: StageContext) -> StageOutput:
    """Re-verify every claim against the evidence and datasets on disk, then gate the script."""
    from content_factory.research.claims import build_claim
    from content_factory.schemas.research import ClaimRecord, EvidenceRecord, SourceRecord

    research = ctx.project_dir / "research"
    if not (research / "claims.json").exists():
        msg = "verify_claims needs research/claims.json: run research first"
        raise RuntimeError(msg)
    prior = [
        ClaimRecord.model_validate(r) for r in json.loads((research / "claims.json").read_text())
    ]
    evidence = [
        EvidenceRecord.model_validate(r)
        for r in json.loads((research / "evidence.json").read_text())
    ]
    sources = {
        s.source_id: s
        for s in (
            SourceRecord.model_validate(r)
            for r in json.loads((research / "sources.json").read_text())
        )
    }
    by_id = {e.evidence_id: e for e in evidence}
    datasets = _project_datasets(ctx)
    verified: list[ClaimRecord] = []
    for claim in prior:
        # Re-run the verifier over what is on disk NOW, rather than trusting the verdict the
        # research stage recorded: the evidence or the dataset may have been re-ingested since.
        verified.append(
            build_claim(
                claim.claim_id,
                ctx.workspace_id,
                claim.statement,
                [by_id[e] for e in claim.evidence_ids if e in by_id],
                operator_assertions=ctx.campaign.brief.operator_assertions,
                dataset=datasets.get(claim.dataset_id) if claim.dataset_id else None,
                dataset_id=claim.dataset_id,
                sources_by_id=sources,
            )
        )
    _write(
        research / "claims.json",
        json.dumps([c.model_dump(mode="json") for c in verified], indent=1, sort_keys=True),
    )
    from content_factory.qc.media import Severity
    from content_factory.research.claims import independence_findings

    # The requirement compiler has set requires_independent_sources=2 on high-stakes claims since it
    # was written and nothing ever read it.
    independence = independence_findings(
        verified,
        evidence,
        sources,
        operator_assertions=ctx.campaign.brief.operator_assertions,
    )
    by_status: dict[str, int] = {}
    for claim in verified:
        by_status[claim.status.value] = by_status.get(claim.status.value, 0) + 1
    result = {
        "claims": len(verified),
        "by_status": by_status,
        "independence": [f.__dict__ for f in independence],
    }
    _write(research / "claim-gate.json", json.dumps(result, indent=1, default=str))
    blocking = [f for f in independence if f.severity in (Severity.blocker, Severity.critical)]
    if blocking:
        raise RuntimeError("verify_claims: " + "; ".join(f.message for f in blocking))
    return StageOutput(_claim_digest(verified), result)


def stage_compile_datasets(ctx: StageContext) -> StageOutput:
    """Uploaded CSV/JSON into typed ``DatasetTable``s through declared transforms."""
    from content_factory.datasets import (
        DatasetError,
        compile_dataset,
        is_transform_sidecar,
        parse_transforms,
        sidecar_for,
    )

    uploads_manifest = ctx.project_dir / "research" / "uploads.json"
    tabular: list[Path] = []
    if uploads_manifest.exists():
        manifest = json.loads(uploads_manifest.read_text())
        uploads = Path(manifest["uploads_dir"])
        tabular = [
            uploads / r["file"] for r in manifest.get("files", []) if r.get("kind") == "data"
        ]
        assert all(not is_transform_sidecar(p) for p in tabular)
    data_dir = ctx.project_dir / "data"
    if not tabular:
        # No uploads: the fixture, as before, so every lane that charts something still runs.
        ds = sample_dataset()
        _write(data_dir / f"{ds.dataset_id}.json", ds.model_dump_json(indent=1))
        return StageOutput(ds.content_hash(), {"datasets": 1, "source": "fixture"})
    compiled: list[dict] = []
    for path in tabular:
        sidecar = sidecar_for(path)
        try:
            transforms = parse_transforms(
                json.loads(sidecar.read_text()) if sidecar.exists() else None
            )
            result = compile_dataset(
                path,
                dataset_id=f"ds_{sha256_hex(path.name.encode())[:12]}",
                transforms=transforms,
                source_ids=(f"src_{sha256_hex(path.name.encode())[:12]}",),
            )
        except DatasetError as exc:
            raise RuntimeError(f"compile_datasets: {exc}") from exc
        _write(data_dir / f"{result.table.dataset_id}.json", result.table.model_dump_json(indent=1))
        _write(
            data_dir / f"{result.table.dataset_id}.transforms.json",
            json.dumps(result.record(), indent=1, sort_keys=True),
        )
        compiled.append(result.record())
    return StageOutput(
        _hash_obj(compiled),
        {
            "datasets": len(compiled),
            "source": "uploads",
            "classifications": sorted({c["classification"] for c in compiled}),
            "transforms": sum(len(c["transforms"]) for c in compiled),
        },
    )


def _import_story_sidecars(ctx: StageContext, fixture: Path, hashes: list[str]) -> dict:
    """A hand-authored story may bring its own evidence: ``<story>.datasets.json``."""
    from content_factory.schemas.render import DatasetTable, SourceCard

    facts: dict = {}
    stem = fixture.with_suffix("")
    datasets_path = stem.with_suffix(".datasets.json")
    if datasets_path.exists():
        tables = [DatasetTable.model_validate(d) for d in json.loads(datasets_path.read_text())]
        for table in tables:
            _write(
                ctx.project_dir / "data" / f"{table.dataset_id}.json",
                table.model_dump_json(indent=1),
            )
            hashes.append(table.content_hash())
        facts["datasets"] = len(tables)
    sources_path = stem.with_suffix(".sources.json")
    if sources_path.exists():
        cards = [SourceCard.model_validate(c) for c in json.loads(sources_path.read_text())]
        _write(
            ctx.project_dir / "research" / "source_cards.json",
            json.dumps([c.model_dump(mode="json") for c in cards], indent=1, sort_keys=True),
        )
        hashes.append(_hash_obj([c.model_dump(mode="json") for c in cards]))
        facts["sources"] = len(cards)
    # `story_lexicon` reads `<project>/story/lexicon.json` and nothing ever put one there for a
    # hand-authored film: every respelling the contracts can express was unreachable.
    lexicon_path = stem.with_suffix(".lexicon.json")
    if lexicon_path.exists():
        entries = [
            PronunciationEntry.model_validate(e) for e in json.loads(lexicon_path.read_text())
        ]
        _write(
            ctx.project_dir / "story" / LEXICON_FILENAME,
            json.dumps([e.model_dump(mode="json") for e in entries], indent=1, sort_keys=True),
        )
        hashes.append(_hash_obj([e.model_dump(mode="json") for e in entries]))
        facts["lexicon_terms"] = len(entries)
    brand_path = stem.with_suffix(".brand.json")
    if brand_path.exists():
        from content_factory.schemas.render import BrandTokens

        brand = BrandTokens.model_validate_json(brand_path.read_text())
        _write(ctx.project_dir / "story" / "brand.json", brand.model_dump_json(indent=1))
        hashes.append(brand.content_hash())
        facts["brand"] = True
    return facts


def _project_brand(ctx: StageContext):
    """Brand token overrides the story brought (``story/brand.json``): approved tokens only."""
    from content_factory.schemas.render import BrandTokens

    path = ctx.project_dir / "story" / "brand.json"
    return BrandTokens.model_validate_json(path.read_text()) if path.exists() else BrandTokens()


def _project_datasets(ctx: StageContext) -> dict:
    """Datasets the project carries (``data/*.json``), else the demo dataset."""
    from content_factory.schemas.render import DatasetTable

    data_dir = ctx.project_dir / "data"
    tables = (
        [DatasetTable.model_validate_json(p.read_text()) for p in sorted(data_dir.glob("*.json"))]
        if data_dir.exists()
        else []
    )
    if not tables:
        tables = [sample_dataset()]
    return {t.dataset_id: t for t in tables}


def _project_sources(ctx: StageContext) -> dict:
    """Source cards the project carries (``research/source_cards.json``), else the demo cards."""
    from content_factory.schemas.render import SourceCard

    path = ctx.project_dir / "research" / "source_cards.json"
    if path.exists():
        cards = [SourceCard.model_validate(c) for c in json.loads(path.read_text())]
        return {c.source_id: c for c in cards}
    return sample_sources()


def _project_assets(ctx: StageContext) -> dict[str, str]:
    """Files ``ingest`` put in the project, in the shape ``RenderBundle.assets`` takes."""
    path = ctx.project_dir / "research" / "uploads.json"
    if not path.exists():
        return {}
    assets = json.loads(path.read_text()).get("assets", {})
    return {str(k): str(v) for k, v in assets.items()}


def stage_plan_story(ctx: StageContext) -> StageOutput:
    # ``story`` names a hand-authored StoryPlan fixture (repo-relative), the twin of plan_shots'
    # fixture planner: a film whose script is written, not researched, still gets a typed plan.
    fixture = _param(ctx, "story")
    drafted: dict | None = None
    transcript_facts: dict | None = None
    if fixture:
        plan = StoryPlan.model_validate_json((REPO_ROOT / fixture).read_text())
    elif (transcribed := _transcript_path(ctx)) is not None:
        plan, transcript_facts = _story_plan_from_recording(ctx, transcribed)
    elif get_settings().execution.local_scriptwriter:
        plan, drafted = _draft_story_plan(ctx)
    else:
        plan = sample_story_plan()
        # The demo plan's last beat is "Sources: Energimyndigheten, Svenska kraftnät." — two words
        # no English text-to-speech says and no English aligner spells.
        _write(
            ctx.project_dir / "story" / LEXICON_FILENAME,
            json.dumps(
                [e.model_dump(mode="json") for e in sample_story_lexicon()],
                indent=1,
                sort_keys=True,
            ),
        )
    # ``--subject`` is one sentence naming the film's world, which is exactly StoryPlan's
    # ``visual_subject``.
    subject = _param(ctx, "subject").strip()
    if subject:
        plan = plan.model_copy(update={"visual_subject": subject[:400]})
    # ``beats`` is the lane's shape, and it was read in transcript mode only — so every other lane's
    # widget was decoration. `single-image` says `beats: 1`.
    want = _param_int(ctx, "beats", 0)
    trimmed = 0
    if want > 0 and want < len(plan.beats):
        trimmed = len(plan.beats) - want
        keep = plan.beats[:want]
        keep_ids = {b.beat_id for b in keep}
        plan = plan.model_copy(
            update={
                "beats": keep,
                "scenes": tuple(sc for sc in plan.scenes if sc.beat_id in keep_ids),
            }
        )
    _write(ctx.project_dir / "story" / "plan.json", plan.model_dump_json(indent=1))
    facts: dict = {"beats": len(plan.beats)}
    if trimmed:
        facts["beats_trimmed"] = trimmed
    elif want > len(plan.beats):
        facts["beats_short_of"] = want
    hashes: list[str] = [plan.content_hash()]
    if fixture:
        facts.update(_import_story_sidecars(ctx, REPO_ROOT / fixture, hashes))

    # Documentary-shaped campaigns (a long_video, optionally with excerpt_of shorts) also get an
    # editorial-arc outline and per-short vertical plans.
    doc = find_documentary(ctx.campaign)
    if doc is not None:
        long_spec, short_specs = doc
        lo, hi = long_spec.target_duration_s
        outline = episode_outline(
            deliverable_id=long_spec.deliverable_id, target_duration_s=(lo + hi) // 2
        )
        _write(ctx.project_dir / "story" / "outline.json", outline.model_dump_json(indent=1))
        hashes.append(outline.content_hash())
        facts["outline_sections"] = len(outline.sections)
        if short_specs:
            ids = [s.deliverable_id for s in short_specs]
            shorts_plan = plan_shorts(plan, ids)
            if get_settings().execution.local_copywriter:
                from content_factory.models.copywriter import draft_hook

                hooks = {
                    e.short_deliverable_id: draft_hook(ctx.campaign, excerpt_text=e.hook_text)
                    for e in shorts_plan.excerpts
                }
                shorts_plan = plan_shorts(plan, ids, hooks=hooks)
            _write(ctx.project_dir / "story" / "shorts.json", shorts_plan.model_dump_json(indent=1))
            hashes.append(shorts_plan.content_hash())
            specs_by_id = {s.deliverable_id: s for s in short_specs}
            for excerpt in shorts_plan.excerpts:
                spec = specs_by_id[excerpt.short_deliverable_id]
                w, h = aspect_dimensions(spec.aspect)
                sp = short_story_plan(plan, excerpt, width=w, height=h)
                _write(
                    ctx.project_dir / "story" / "shorts" / f"{sp.deliverable_id}.plan.json",
                    sp.model_dump_json(indent=1),
                )
                hashes.append(sp.content_hash())
            facts["shorts"] = len(shorts_plan.excerpts)
    if drafted is not None:
        facts.update(drafted)
    if transcript_facts is not None:
        facts.update(transcript_facts)
    outputs_hash = hashes[0] if len(hashes) == 1 else _hash_obj(hashes)
    return StageOutput(outputs_hash, facts)


def _transcript_path(ctx: StageContext) -> Path | None:
    """The transcript ``transcribe_audio`` wrote for this deliverable, when there is one."""
    if ctx.deliverable_id is None:
        return None
    path = ctx.ddir() / "audio" / "transcript.json"
    return path if path.exists() else None


def _story_plan_from_recording(ctx: StageContext, path: Path) -> tuple[StoryPlan, dict]:
    """The plan a recording dictates: beats are spans of what was actually said."""
    from content_factory.audio.transcribe import TRANSCRIBE_VERSION, story_plan_from_transcript
    from content_factory.schemas.audio import SpeechTranscript

    cfg = get_settings().transcription
    transcript = SpeechTranscript.model_validate_json(path.read_text())
    spec = _spec(ctx)
    width, height = aspect_dimensions(aspect_or_portrait(getattr(spec, "aspect", None)))
    fps = int(getattr(spec, "fps", 24) or 24)
    subject = _param(ctx, "subject").strip()
    # A plan made from a recording has no world of its own, and `visual_subject` is the only thing
    # that tells the image model what the film is *of*.
    opening = " ".join(transcript.text.split())[:200].strip()
    from_transcript = not subject and bool(opening)
    subject_source = "--subject" if subject else ("transcript" if from_transcript else "none")
    plan = story_plan_from_transcript(
        transcript,
        deliverable_id=spec.deliverable_id,
        beats=max(1, _param_int(ctx, "beats", cfg.beats)),
        width=width,
        height=height,
        fps=fps if fps in (24, 25, 30, 60) else 24,
        visual_subject=subject or opening or None,
    )
    return plan, {
        "planned_from": "transcript",
        "visual_subject_from": subject_source,
        "transcript_engine": transcript.engine,
        "transcript_timings": transcript.timing_source.value,
        "spoken_seconds": round(transcript.duration_ms / 1000, 1),
        "transcribe_version": TRANSCRIBE_VERSION,
    }


def _draft_story_plan(ctx: StageContext) -> tuple[StoryPlan, dict]:
    """The local writer's plan, or a named failure."""
    from content_factory.models.scriptwriter import draft_story_plan
    from content_factory.schemas.research import ClaimRecord

    spec = _spec(ctx)
    lo, hi = getattr(spec, "target_duration_s", (60, 120))
    outline = episode_outline(
        deliverable_id=spec.deliverable_id, target_duration_s=max(60, (lo + hi) // 2)
    )
    claims_path = ctx.project_dir / "research" / "claims.json"
    claims = (
        [ClaimRecord.model_validate(r) for r in json.loads(claims_path.read_text())]
        if claims_path.exists()
        else []
    )
    datasets = _project_datasets(ctx)
    sources = _project_sources(ctx)
    uploads_path = ctx.project_dir / "research" / "uploads.json"
    assets = (
        tuple(json.loads(uploads_path.read_text()).get("assets", {}))
        if uploads_path.exists()
        else ()
    )
    # Not every deliverable spec has an aspect — an article and a newsletter do not — so the
    # default stands for "no picture was asked for" rather than for a missing field.
    width, height = aspect_dimensions(aspect_or_portrait(getattr(spec, "aspect", None)))
    result = draft_story_plan(
        ctx.campaign,
        outline,
        deliverable_id=spec.deliverable_id,
        claims=claims,
        datasets=datasets,
        source_ids=tuple(sources),
        asset_ids=assets,
        width=width,
        height=height,
        visual_subject=_param(ctx, "subject").strip() or None,
    )
    # The gaps are the operator's next task, so they are written whether or not a plan came back.
    _write(
        ctx.project_dir / "story" / "gaps.json",
        json.dumps(
            {"version": result.facts.get("version"), "gaps": [g.__dict__ for g in result.gaps]},
            indent=1,
            sort_keys=True,
        ),
    )
    if result.plan is None:
        raise RuntimeError(
            "the script writer produced no usable beat; every one was refused. See"
            " story/gaps.json:\n  " + "\n  ".join(g.reason for g in result.gaps[:6])
        )
    return result.plan, {f"writer_{k}": v for k, v in result.facts.items()}


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
    # The tone reaches the copywriter as an instruction. Without the model it changes nothing and
    # says so in the facts, rather than looking like it did something to a fixture string.
    tone = _param(ctx, "tone", "neutral")
    if spec.type == "carousel":
        assert isinstance(spec, CarouselSpec)
        if use_model:
            from content_factory.models.copywriter import draft_carousel

            drafted = draft_carousel(ctx.campaign, card_count=spec.card_count, tone=tone)
            base_cards = [c["text"] for c in drafted["cards"]]
        else:
            drafted = {}
            base_cards = list(demo_fixtures.CAROUSEL_TEXTS[: spec.card_count])
        cards = [
            {"card_id": f"card_{i + 1:012d}", "text": overlay.get(f"card_{i + 1:012d}", t)}
            for i, t in enumerate(base_cards)
        ]
        payload = {"cards": cards, **({"model": drafted["model"]} if drafted else {})}
    else:
        if use_model:
            from content_factory.models.copywriter import draft_caption

            drafted = draft_caption(ctx.campaign, tone=tone)
            base_caption, base_alt = drafted["caption"], drafted["alt_text"]
        else:
            drafted = {}
            base_caption = "About a fifth of Sweden's electricity came from wind in 2025."
            base_alt = "Data card about wind power's share."
        payload = {
            "caption": overlay.get("caption", base_caption),
            "alt_text": overlay.get("alt_text", base_alt),
            **({"model": drafted["model"]} if drafted else {}),
        }
    _write(ctx.ddir() / "copy.json", json.dumps(payload, indent=1))
    facts: dict = {
        "units": len(payload.get("cards", [1])),
        "tone": tone if use_model else f"{tone} (ignored: execution.local_copywriter is off)",
    }
    # What the model call cost.
    if isinstance(payload.get("model"), dict):
        facts.update({f"model_{k}": v for k, v in payload["model"].items()})
    return StageOutput(_hash_obj(payload), facts)


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
    # "2x" renders at twice the artboard's pixel size for a retina export; the still QC is told
    # the scaled size, so a 2x render is checked against 2x rather than failing its own dimensions.
    scale = 2 if _param(ctx, "scale", "1x").strip().lower().startswith("2") else 1
    outcome = render_artboard(
        bundle,
        workspace_id=ctx.workspace_id,
        store=ctx.store,
        workdir=ctx.ddir() / "exports",
        scale=scale,
    )
    if not outcome.qc.passed:
        raise RuntimeError(f"static render failed QC: {outcome.qc.findings}")
    return StageOutput(
        outcome.artifact.sha256, {"artifact_key": outcome.artifact.key, "scale": scale}
    )


def stage_compile_cards(ctx: StageContext) -> StageOutput:
    """One artboard per card."""
    copy_path = ctx.ddir() / "copy.json"
    copy = json.loads(copy_path.read_text()) if copy_path.is_file() else {}
    source = "copy"
    cards_in = copy.get("cards")
    if not cards_in:
        source = "beats"
        cards_in = [
            {"card_id": f"card_{i + 1:012d}", "text": beat.display_text}
            for i, beat in enumerate(_load_story_plan(ctx).beats)
        ]
    # The label was the literal string "WIND POWER", which is the demo fixture's subject printed
    # across the top of every card of every other film this lane has ever made.
    label = (ctx.campaign.brief.topic or "").strip().upper()[:28] or "CARD"
    ds = sample_dataset()
    cards = []
    for i, card in enumerate(cards_in):
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
                    text=TextRef(text=f"{label} · {i + 1}/{len(cards_in)}"),
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
    return StageOutput(_hash_obj(manifest), {"cards": len(cards), "from": source})


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


# --- 3D scene-control layer (shots -> control passes) -----------------------------------------
def _story_plan_path(ctx: StageContext) -> Path | None:
    """Which story plan this deliverable's stages read, or None when nothing has been planned."""
    if ctx.deliverable_id:
        short = ctx.project_dir / "story" / "shorts" / f"{ctx.deliverable_id}.plan.json"
        if short.exists():
            return short
    plan = ctx.project_dir / "story" / "plan.json"
    return plan if plan.exists() else None


def _load_story_plan(ctx: StageContext) -> StoryPlan:
    path = _story_plan_path(ctx)
    if path is not None:
        return StoryPlan.model_validate_json(path.read_text())
    return sample_story_plan()


def _reviews_dir(ctx: StageContext) -> Path:
    return ctx.project_dir / "reviews"


def stage_review_assets(ctx: StageContext) -> StageOutput:
    """Gate every character asset a shot uses on a reviewed, approved sculpture."""
    from content_factory.controls.asset_review import review_asset
    from content_factory.schemas.assets import CharacterAssetReview

    plan_path = ctx.ddir() / "shots" / "plan.json"
    if not plan_path.exists():
        msg = "review_assets needs shots/plan.json: run plan_shots first"
        raise RuntimeError(msg)
    plan = ShotPlan.model_validate_json(plan_path.read_text())
    assets = sorted({c.asset for shot in plan.shots for c in shot.characters})
    if not assets:
        return StageOutput(_hash_obj({"assets": []}), {"assets": 0, "note": "no characters staged"})

    root = Path(get_settings().controls.assets_root) / "characters"
    reviews_dir = _reviews_dir(ctx) / "assets"
    # Approvals are not run artifacts: see ControlsSettings.asset_approvals_dir.
    approvals = Path(get_settings().controls.asset_approvals_dir)
    # Whether this film sends an identity reference at all.
    references = _param_list(ctx, "references", get_settings().image_sequences.anchor_references)
    from content_factory.controls.identity_sheets import style_slug

    wants_identity = "identity" in references
    slug = style_slug(_anchor_style(ctx))
    records: list[dict] = []
    blocked: list[str] = []
    for asset in assets:
        asset_dir = root / asset
        if not asset_dir.is_dir():
            blocked.append(f"{asset}: not built at {asset_dir}")
            continue
        review = review_asset(asset_dir, sheet_dest=reviews_dir / f"{asset}.sheet.png")
        # An approval recorded earlier applies only to the same built mesh.
        approval_path = approvals / f"{asset}.json"
        if approval_path.exists():
            prior = CharacterAssetReview.model_validate_json(approval_path.read_text())
            if prior.blend_sha256 == review.blend_sha256 and prior.approved_by:
                review = review.model_copy(
                    update={
                        "approved_by": prior.approved_by,
                        "approved_at": prior.approved_at,
                        "notes": prior.notes,
                    }
                )
        _write(reviews_dir / f"{asset}.review.json", review.model_dump_json(indent=1))
        sheet = review.sheet_for(slug)
        records.append(
            {
                "asset": asset,
                "blend": review.blend_sha256[:12],
                "checks_passed": review.checks_passed,
                "approved_by": review.approved_by,
                "flagged": [c.check for c in review.checks if not c.passed],
                "identity_sheet": sheet.png_sha256[:12] if sheet else None,
            }
        )
        if not review.checks_passed:
            blocked.append(f"{asset}: " + "; ".join(c.detail for c in review.blockers))
        elif not review.approved_by:
            blocked.append(
                f"{asset}: checks pass but nobody has approved it — look at "
                f"reviews/assets/{asset}.sheet.png, then "
                f"`content-factory assets approve {asset} --as <name>`"
            )
        elif wants_identity and sheet is None:
            blocked.append(
                f"{asset}: this run sends an identity reference and there is no styled sheet for"
                f" the film's style ({slug}). Build one:"
                " `uv run python skills/video/blender_scene/assets_build/build_identity_sheet.py"
                f" --asset {asset} --style <the film's style>`. The clay turnaround is not a"
                " substitute — the model draws what it is shown."
            )
        elif wants_identity and sheet is not None:
            prior_sheet = None
            if approval_path.exists():
                prior_sheet = CharacterAssetReview.model_validate_json(
                    approval_path.read_text()
                ).sheet_for(slug)
            if prior_sheet is None or prior_sheet.png_sha256 != sheet.png_sha256:
                blocked.append(
                    f"{asset}: the identity sheet for {slug} is not the one that was approved —"
                    " look at it and approve the asset again"
                    f" (`content-factory assets approve {asset} --as <name>`). An approval covers"
                    " the mesh and its sheets, so a redrawn sheet is unreviewed."
                )
    if blocked:
        raise RuntimeError("review_assets blocked the run:\n  " + "\n  ".join(blocked))
    return StageOutput(
        _hash_obj(records),
        {
            "assets": len(records),
            "approved": [r["asset"] for r in records],
            "identity_references": wants_identity,
            "identity_sheets": [r["identity_sheet"] for r in records],
        },
    )


FPS_MASTER_HINT = (
    "One rate has to be master. A mismatch is not caught anywhere downstream: compose_video"
    " conforms the clip to the timeline by DUPLICATING frames, which is how every wind short"
    " v1-v5 shipped 24 fps footage in a 30 fps film. Set the plan_shots `fps` widget to the"
    " story's rate, or plan a story at the shots' rate."
)


def stage_plan_shots(ctx: StageContext) -> StageOutput:
    """One ShotSpec per story beat (camera preset by scene kind)."""
    cfg = get_settings().shots
    planner = _param(ctx, "planner", cfg.planner)
    # The story's rate is the master rate.
    story = _story_plan_or_none(ctx)
    default_fps = story.fps if story is not None else cfg.fps
    if planner == "fixture":
        plan = load_fixture_plan(REPO_ROOT / _param(ctx, "fixture_path", cfg.fixture_path))
    elif planner == "reference":
        # Stage the beats from whatever find_reference chose.
        from content_factory.shots.planner import plan_shots_from_reference

        selection_path = ctx.ddir() / "reference" / "selection.json"
        selection = json.loads(selection_path.read_text()) if selection_path.exists() else {}
        width, height = _param_size(ctx, "size", cfg.shot_size)
        plan = plan_shots_from_reference(
            _load_story_plan(ctx),
            selection,
            width=width,
            height=height,
            fps=_param_int(ctx, "fps", default_fps),  # type: ignore[arg-type]
            snap_to_ltx_length=cfg.snap_to_ltx_length,
            with_character=_param(ctx, "characters", "one").strip().lower() != "none",
        )
    else:
        width, height = _param_size(ctx, "size", cfg.shot_size)
        plan = plan_shots_from_story(
            _load_story_plan(ctx),
            width=width,
            height=height,
            fps=_param_int(ctx, "fps", default_fps),  # type: ignore[arg-type]
            snap_to_ltx_length=cfg.snap_to_ltx_length,
            with_character=_param(ctx, "characters", "one").strip().lower() != "none",
            lighting_preset=_param(ctx, "lighting", "studio"),  # type: ignore[arg-type]
        )
    # Which styled identity sheet each character's anchor will be conditioned on.
    plan = _attach_identity_sheets(ctx, plan)
    # Every shot in a plan shares one rate (ShotPlan enforces it), so one comparison covers the
    # film.
    if story is not None and plan.shots[0].fps != story.fps:
        msg = (
            f"the story is {story.fps} fps and this shot plan is {plan.shots[0].fps} fps."
            f" {FPS_MASTER_HINT}"
        )
        raise RuntimeError(msg)
    _write(ctx.ddir() / "shots" / "plan.json", plan.model_dump_json(indent=1))
    # Measured off the finished plan, not predicted, and reported rather than fixed: a shot under
    # the cliff renders its control passes and then has them ignored by the image model.
    from content_factory.shots.planner import underframed_shots

    under = underframed_shots(plan)
    return StageOutput(
        plan.content_hash(),
        {
            "shots": len(plan.shots),
            "planner": plan.planner,
            "fps": plan.shots[0].fps,
            "frames": sum(sh.frame_count for sh in plan.shots),
            "staged_from": sorted(
                {
                    c.pose.name
                    for sh in plan.shots
                    for c in sh.characters
                    if isinstance(c.pose, SegmentClipPose)
                }
            ),
            "underframed": [{"shot_id": s, "body_fraction": f} for s, f in under],
            "identity_sheets": sorted(
                {
                    sha[:12]
                    for sh in plan.shots
                    for c in sh.characters
                    for sha in c.reference_image_sha256
                }
            ),
        },
    )


def _anchor_style(ctx: StageContext) -> str:
    """The film's style prompt, resolved."""
    cfg = get_settings().image_sequences
    return resolve_style(_param(ctx, "style", cfg.anchor_style_prompt))


def _attach_identity_sheets(ctx: StageContext, plan: ShotPlan) -> ShotPlan:
    """Put each character's styled sheet digest on its ``CharacterSpec``, when one is built."""
    from content_factory.controls.identity_sheets import sheet_sha_for_style

    root = Path(get_settings().controls.assets_root)
    style = _anchor_style(ctx)
    by_asset: dict[str, tuple[str, ...]] = {}
    for shot in plan.shots:
        for character in shot.characters:
            if character.asset in by_asset:
                continue
            sha = sheet_sha_for_style(root, character.asset, style)
            by_asset[character.asset] = (sha,) if sha else ()
    if not any(by_asset.values()):
        return plan
    return plan.model_copy(
        update={
            "shots": tuple(
                shot.model_copy(
                    update={
                        "characters": tuple(
                            c.model_copy(
                                update={"reference_image_sha256": by_asset.get(c.asset, ())}
                            )
                            for c in shot.characters
                        )
                    }
                )
                for shot in plan.shots
            )
        }
    )


def _load_routing(ctx: StageContext) -> ShotRouting | None:
    """The hybrid workflow's per-beat routing, when route_shots has run for this deliverable."""
    path = ctx.ddir() / "shots" / "routing.json"
    return ShotRouting.model_validate_json(path.read_text()) if path.exists() else None


def stage_route_shots(ctx: StageContext) -> StageOutput:
    """Hybrid workflow: decide per story beat whether Remotion renders it."""
    plan_path = ctx.ddir() / "shots" / "plan.json"
    if not plan_path.exists():
        msg = "route_shots needs shots/plan.json: run plan_shots first"
        raise RuntimeError(msg)
    cfg = get_settings().routing
    default_route = _param(ctx, "default_route", cfg.default_route)
    if default_route not in ("render", "generate"):
        msg = f"route_shots default_route must be 'render' or 'generate', got {default_route!r}"
        raise RuntimeError(msg)
    generate_kinds = _param_list(ctx, "generate_kinds", cfg.generate_kinds)
    routing = route_shots(
        _load_story_plan(ctx),
        ShotPlan.model_validate_json(plan_path.read_text()),
        default_route=default_route,  # type: ignore[arg-type]
        generate_kinds=generate_kinds,
        overrides=cfg.overrides,
    )
    _write(ctx.ddir() / "shots" / "routing.json", routing.model_dump_json(indent=1))
    generated = sum(1 for b in routing.beats if b.route == "generate")
    return StageOutput(
        routing.content_hash(),
        {
            "beats": len(routing.beats),
            "generate": generated,
            "render": len(routing.beats) - generated,
            "default_route": default_route,
            "generate_kinds": list(generate_kinds),
        },
    )


def _first_stageable(matches) -> str | None:
    """The first match that is a retargeted mocap clip on disk, or None."""
    from content_factory.shots.planner import CLIPS_DIR

    for match in matches:
        clip = getattr(match, "clip_id", "") or ""
        if clip and (CLIPS_DIR / f"{clip}.json").is_file():
            return clip
    return None


def stage_find_reference(ctx: StageContext) -> StageOutput:
    """Ask the reference library, in the story's own words."""
    cfg = get_settings().reference
    index_path = Path(cfg.index_path)
    story = _load_story_plan(ctx)
    limit = int(_param(ctx, "limit", str(cfg.default_limit)))
    affection = _param(ctx, "affection", cfg.require_affection or "any")
    usage = _param(ctx, "usage", cfg.require_usage or "any")
    require_pose = str(_param(ctx, "require_pose", "true")).lower() in ("1", "true", "yes")

    selection: dict[str, object] = {
        "schema": "cf.reference_selection.v1",
        "index_path": str(index_path),
        "beats": [],
    }

    if not index_path.exists():
        selection["library"] = None
        selection["note"] = (
            f"no reference library at {index_path}; nothing selected. Build one with"
            " `uv run python -m content_factory.reference.build`."
        )
        _write(ctx.ddir() / "reference" / "selection.json", json.dumps(selection, indent=1))
        return StageOutput(
            _hash_obj({"library": None, "beats": len(story.beats)}),
            {"library": "absent", "beats": len(story.beats), "selected": 0},
        )

    from content_factory.reference.query import search
    from content_factory.schemas.reference import ReferenceQuery

    beats: list[dict] = []
    selected = 0
    absent_terms: set[str] = set()
    for beat in sorted(story.beats, key=lambda b: b.order):
        text = (beat.spoken_text or beat.display_text or "").strip()
        if not text:
            beats.append({"beat_id": beat.beat_id, "order": beat.order, "skipped": "no text"})
            continue
        query = ReferenceQuery(
            text=text[:500],
            limit=limit,
            require_affection=None if affection == "any" else affection,  # type: ignore[arg-type]
            require_usage=None if usage == "any" else usage,  # type: ignore[arg-type]
            require_pose=require_pose,
        )
        result = search(index_path, query)
        absent_terms.update(result.absent_terms)
        if result.matches:
            selected += 1
        beats.append(
            {
                "beat_id": beat.beat_id,
                "order": beat.order,
                "match_set": json.loads(result.model_dump_json()),
                # Decided here rather than left for plan_shots to discover, so the selection file
                # answers "will this stage?" on its own.
                "stageable": _first_stageable(result.matches) is not None,
                "stageable_clip": _first_stageable(result.matches),
            }
        )

    selection["beats"] = beats
    selection["absent_terms"] = sorted(absent_terms)
    # How many beats found a clip that can actually drive a rig, which is a different number from
    # `selected` and the one that decides whether `plan_shots` stages anything.
    stageable = sum(1 for b in beats if b.get("stageable"))
    selection["stageable_beats"] = stageable
    selection["reason"] = _selection_reason(len(beats), selected, stageable)
    _write(
        ctx.ddir() / "reference" / "selection.json",
        json.dumps(selection, indent=1, sort_keys=True),
    )
    return StageOutput(
        _hash_obj([b.get("match_set", {}).get("matches", []) for b in beats]),
        {
            "beats": len(beats),
            "selected": selected,
            "stageable": stageable,
            "reason": selection["reason"],
            # Terms the library understood and has nothing for. This is the operator's shooting
            # list, and it belongs in the run facts rather than only in a file.
            "absent_terms": sorted(absent_terms)[:8],
        },
    )


def _selection_reason(beats: int, selected: int, stageable: int) -> str:
    """One sentence a person can act on, for why this run will or will not stage from reference."""
    if not beats:
        return "the story has no beats with text"
    if not selected:
        return (
            "nothing matched any beat — the library holds two-person interaction, so a story with"
            " no people in it correctly returns nothing"
        )
    if not stageable:
        return (
            f"{selected} of {beats} beats matched, but none of the matches is a retargeted clip;"
            " matches from SBU, TVHI and MotionHub are reference to look at, not rigs to stage"
            " from. plan_shots will use the preset plan"
        )
    return f"{stageable} of {beats} beats can be staged from a captured take"


def stage_compile_controls(ctx: StageContext) -> StageOutput:
    """Per-frame control passes as ControlBundles."""
    compiler = _param(ctx, "compiler", get_settings().controls.compiler)
    controls_dir = ctx.ddir() / "controls"
    if compiler == "motion_plan":
        plan, _instructions = _sequence_motion_plan(ctx)
        shot_plan_path = ctx.ddir() / "shots" / "plan.json"
        if shot_plan_path.exists():
            # Shots planned (hybrid / blender workflows on mock backends): one bundle per routed
            # shot, keyed by the shot id.
            shot_plan = ShotPlan.model_validate_json(shot_plan_path.read_text())
            routing = _load_routing(ctx)
            wanted = [
                sh
                for sh in shot_plan.shots
                if routing is None or sh.shot_id in routing.generated_shot_ids()
            ]
            bundles = [
                compile_bundle_from_motion_plan(
                    plan, controls_dir / sh.shot_id, shot_id=sh.shot_id, anchor_frames=(0,)
                )
                for sh in wanted
            ]
        else:
            bundles = [compile_bundle_from_motion_plan(plan, controls_dir / plan.sequence_id)]
    else:
        bundles = _compile_controls_blender(ctx)
    manifest = {
        "compiler": compiler,
        "compiler_version": bundles[0].compiler_version if bundles else CONTROL_COMPILER_VERSION,
        "bundles": [
            {
                "shot_id": b.shot_id,
                "path": str(Path("controls") / b.shot_id / "bundle.json"),
                "bundle_hash": b.content_hash(),
            }
            for b in bundles
        ],
    }
    _write(controls_dir / "manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
    kinds = sorted({t.kind.value for b in bundles for t in b.tracks})
    return StageOutput(
        _hash_obj([b.content_hash() for b in bundles]),
        {"compiler": compiler, "shots": len(bundles), "kinds": kinds},
    )


_CONTROL_PASS_WIDGETS: tuple[tuple[str, bool, tuple[ControlKind, ...]], ...] = (
    ("rough_rgb", True, (ControlKind.rough_rgb,)),
    # One toggle, two passes: the 8-bit PNG a model can be shown and the EXR the depth range is
    # measured from.
    ("depth", True, (ControlKind.depth, ControlKind.depth_exr)),
    ("normals", True, (ControlKind.normals,)),
    ("segmentation", True, (ControlKind.segmentation,)),
    ("skeleton", True, (ControlKind.pose_skeleton,)),
    ("canny", False, (ControlKind.canny,)),
)
"""``(widget, default, passes)``. The defaults are the canvas widget defaults in catalog.ts."""

_ALWAYS_COMPILED: tuple[ControlKind, ...] = (ControlKind.layout_boxes,)
"""Not a toggle: the layout boxes are where each identity reference goes, and the anchor stage
reads them off the bundle's subjects. They are content-factory's own render, not Blender's."""


def _control_passes(ctx: StageContext) -> tuple[ControlKind, ...]:
    """Which control passes the Blender compiler renders, from the node's toggles."""
    wanted: list[ControlKind] = []
    for key, default, kinds in _CONTROL_PASS_WIDGETS:
        if _param_bool(ctx, key, default):
            wanted.extend(kinds)
    wanted.extend(_ALWAYS_COMPILED)
    return tuple(dict.fromkeys(wanted))


def _refuse_unclothed_staging(plan: ShotPlan) -> None:
    """Refuse a Blender plan whose figures nobody has described."""
    staged = [c for sh in plan.shots for c in sh.characters]
    if not staged or any(c.appearance for c in staged):
        return
    who = ", ".join(sorted({c.asset for c in staged}))
    msg = (
        f"the shot plan stages {len(staged)} figure(s) ({who}) and none of them has an"
        " `appearance`. The Blender compiler renders depth and normals of the bare mesh and the"
        " image model draws exactly that — an untextured grey mannequin, measured over ten"
        " anchors. Write CharacterSpec.appearance (build, hair, clothing) on the shot plan, or"
        " use controls.compiler=motion_plan for a lane that stages nobody."
    )
    raise RuntimeError(msg)


def _compile_controls_blender(ctx: StageContext) -> list[ControlBundle]:
    """One Blender skill run per shot, cached by shot hash + compiler settings."""
    from content_factory.controls.blender import BUNDLE_BUILDER_VERSION, run_blender_scene
    from content_factory.controls.bundle import build_control_bundle

    cfg = get_settings().controls
    plan_path = ctx.ddir() / "shots" / "plan.json"
    if not plan_path.exists():
        msg = "controls.compiler=blender needs shots/plan.json: run plan_shots first"
        raise RuntimeError(msg)
    plan = ShotPlan.model_validate_json(plan_path.read_text())
    _refuse_unclothed_staging(plan)
    routing = _load_routing(ctx)
    # Hybrid workflow: beats routed to Remotion never reach Blender; the shots that stay are the
    # ones compose_video will splice in as generated clips.
    shots_to_compile = [
        sh for sh in plan.shots if routing is None or sh.shot_id in routing.generated_shot_ids()
    ]
    passes = _control_passes(ctx)
    bundles: list[ControlBundle] = []
    for shot in shots_to_compile:
        # The node's toggles decide which passes Blender renders.
        shot = shot.model_copy(update={"render": shot.render.model_copy(update={"passes": passes})})

        shot_dir = ctx.ddir() / "controls" / shot.shot_id
        marker = shot_dir / ".done.json"
        bundle_path = shot_dir / "bundle.json"
        input_hash = _hash_obj(
            {
                "shot": shot.content_hash(),
                "compiler": "blender",
                "engine": cfg.engine,
                "assets_root": cfg.assets_root,
                "builder": BUNDLE_BUILDER_VERSION,
            }
        )  # the passes are inside shot.content_hash(), having been applied above
        if (
            marker.exists()
            and bundle_path.exists()
            and json.loads(marker.read_text()).get("input_hash") == input_hash
        ):
            bundles.append(ControlBundle.model_validate_json(bundle_path.read_text()))
            continue
        spec_path = shot_dir / "shot_spec.json"
        _write(spec_path, shot.model_dump_json(indent=1))
        run = run_blender_scene(
            spec_path,
            shot_dir,
            blender_bin=cfg.blender_bin,
            engine=cfg.engine,
            assets_root=Path(cfg.assets_root),
            timeout_s=cfg.timeout_s,
        )
        bundle = build_control_bundle(shot, shot_dir)
        _write(
            marker,
            json.dumps(
                {
                    "input_hash": input_hash,
                    "bundle_hash": bundle.content_hash(),
                    "blender_version": bundle.blender_version,
                    "engines": run.summary.get("engines"),
                },
                indent=1,
                sort_keys=True,
            ),
        )
        _log_execution(ctx, f"blender_scene:{shot.shot_id}")
        bundles.append(bundle)
    return bundles


# --- anchors, lock, keyframes (image-sequence branch executors) --------------------------------
def _same_endpoint(a: str, b: str) -> bool:
    """Whether two service URLs name the same server, ignoring trailing slashes."""
    from urllib.parse import urlparse

    def parts(url: str) -> tuple[str, str, int]:
        u = urlparse(url if "://" in url else f"http://{url}")
        return (
            u.scheme or "http",
            (u.hostname or "").lower(),
            u.port or (443 if u.scheme == "https" else 80),
        )

    return parts(a) == parts(b)


def _service_for_backend(backend: object) -> str | None:
    """The local GPU tenant a backend talks to (None for mocks, and None for another machine's)."""
    from content_factory.media.video_generate import ComfyUIVideoBackend
    from content_factory.sequences.hidream_backend import HiDreamReferenceEditBackend

    app = get_settings()
    if isinstance(backend, HiDreamReferenceEditBackend):
        managed = app.image_sequences.hidream_endpoint
        return "hidream" if _same_endpoint(backend.endpoint, managed) else None
    if isinstance(backend, ComfyUIVideoBackend):
        return "comfyui" if _same_endpoint(backend.endpoint, app.comfyui.endpoint) else None
    return None


def _ensure_backend_ready(backend: object, warmed: set[str]) -> None:
    tenant = _service_for_backend(backend)
    if tenant is None or tenant in warmed:
        return
    # The one moment this run is about to put weights on the local card, and therefore the one place
    # worth asking whether the card is still there.
    gone = gpu_is_gone()
    if gone:
        from content_factory.workflows.blocked import BlockedError

        raise BlockedError(
            f'the GPU is not there: nvidia-smi says {gone!r}. Xid 79 ("GPU has fallen off the'
            " bus\") sets the driver's recovery action to a node reboot, and no amount of"
            " retrying or restarting a server changes that — check `journalctl -k | grep Xid`"
            " and reboot the host. Runs that need no GPU still work: set"
            " CF__IMAGE_SEQUENCES__BACKEND=mock and CF__VIDEO__BACKEND=mock, or point"
            " CF__IMAGE_SEQUENCES__HIDREAM_ENDPOINTS at another machine.",
            stage="ensure_backend",
        )
    from content_factory.services.local import ensure_service

    ensure_service(tenant)  # type: ignore[arg-type]
    warmed.add(tenant)


ANCHOR_MODEL_BACKENDS: dict[str, str] = {
    "hidream-o1": "hidream",
    "flux2-dev": "flux2",
    "mock": "mock",
}
"""The canvas ``model`` widget, spelled by model name, to the backend that runs it."""

ANCHOR_MODELS_WITHOUT_BACKEND: dict[str, str] = {
    "krea2-turbo": (
        "Krea2 is the video showcase lane's text-to-image model and has no reference-edit server"
        " here: it cannot take the Blender control passes as references, which is the whole job of"
        " this stage. Use hidream-o1 (one world across a film) or flux2-dev (several references"
        " composed at once)."
    ),
    "ideogram-4": (
        "Ideogram 4 has no image path into its conditioning, so it cannot take the references or"
        " control passes this stage exists to apply. It draws text-to-image only. For a region of"
        " an existing frame use scripts/ideogram_inpaint.py (Differential Diffusion); for an"
        " anchor a later frame must match, use hidream-o1 or flux2-dev."
    ),
    "ideogram-4-sdnq": (
        "the SDNQ repack of the same model, and it has the same limit: no image conditioning, so"
        " no reference edit. It adds inpainting, which scripts/ideogram_inpaint.py drives."
    ),
    "comfy-fixture": (
        "the fixture ComfyUI package renders an empty image and takes no references; it exists to"
        " prove the ComfyUI submit/collect path, not to draw anchors. Use mock for an offline run."
    ),
}
"""Options the widget offers that nothing can execute. Refused by name at the top of the stage,
before a GPU tenant is started, rather than silently falling back to the mock and delivering a
film of grey rectangles."""


def _anchor_backend_name(ctx: StageContext | None) -> str:
    """Which reference-edit backend draws the anchors."""
    cfg = get_settings().image_sequences
    if ctx is None:
        return cfg.backend
    configured = "backend" in cfg.model_fields_set
    default = cfg.backend
    model = _param(ctx, "model").strip()
    if model and not configured:
        if model in ANCHOR_MODELS_WITHOUT_BACKEND:
            msg = (
                f"generate_anchor model={model!r} cannot run:"
                f" {ANCHOR_MODELS_WITHOUT_BACKEND[model]}"
            )
            raise RuntimeError(msg)
        if model not in ANCHOR_MODEL_BACKENDS:
            known = sorted(ANCHOR_MODEL_BACKENDS) + sorted(ANCHOR_MODELS_WITHOUT_BACKEND)
            msg = f"unknown generate_anchor model {model!r}; the widget offers {known}"
            raise RuntimeError(msg)
        default = ANCHOR_MODEL_BACKENDS[model]
    return _param(ctx, "backend", default)


def _megapixel_size(width: int, height: int, megapixels: float) -> tuple[int, int]:
    """``width`` x ``height`` scaled to a pixel budget, aspect held, both sides a multiple of 32."""
    if megapixels <= 0:
        return width, height
    scale = (megapixels * 1_000_000 / (width * height)) ** 0.5

    def snap(value: int) -> int:
        return max(64, round(value * scale / 32) * 32)

    return snap(width), snap(height)


def _reference_backend(ctx: StageContext | None = None) -> ReferenceEditBackend:
    cfg = get_settings().image_sequences
    backend = _anchor_backend_name(ctx)
    if backend == "hidream":
        from content_factory.sequences.hidream_backend import HiDreamReferenceEditBackend

        # The pool's first host, not the singular endpoint.
        return HiDreamReferenceEditBackend(
            endpoint=cfg.hidream_pool()[0],
            send_control_as_reference=cfg.control_as_reference,
        )
    if backend == "flux2":
        from content_factory.sequences.flux2_backend import Flux2ReferenceBackend

        # Uploads and collected outputs live under the run, not /tmp, so a frame's references are
        # recoverable from the run that made it rather than from whatever the machine kept.
        workdir = (ctx.ddir() / "flux2") if ctx is not None else Path("output/flux2")
        return Flux2ReferenceBackend(
            workdir=workdir,
            endpoint=cfg.flux2_pool()[0],
            turbo=cfg.flux2_turbo,
            reference_roles=cfg.anchor_references,
            send_control_as_reference=cfg.control_as_reference,
        )
    return MockReferenceEditBackend()


def _reference_backends(ctx: StageContext | None = None) -> list[ReferenceEditBackend]:
    """One backend per configured GPU host, for spreading a sequence's frames across machines."""
    cfg = get_settings().image_sequences
    backend = _anchor_backend_name(ctx)
    if backend == "hidream":
        from content_factory.sequences.hidream_backend import HiDreamReferenceEditBackend

        return [
            HiDreamReferenceEditBackend(
                endpoint=e, send_control_as_reference=cfg.control_as_reference
            )
            for e in cfg.hidream_pool()
        ]
    if backend == "flux2":
        from content_factory.sequences.flux2_backend import Flux2ReferenceBackend

        endpoints = cfg.flux2_pool()
        base = (ctx.ddir() / "flux2") if ctx is not None else Path("output/flux2")
        # Each host gets its own scratch dir.
        return [
            Flux2ReferenceBackend(
                workdir=base if len(endpoints) == 1 else base / f"host{i}",
                endpoint=endpoint,
                turbo=cfg.flux2_turbo,
                reference_roles=cfg.anchor_references,
                send_control_as_reference=cfg.control_as_reference,
            )
            for i, endpoint in enumerate(endpoints)
        ]
    return [MockReferenceEditBackend()]


def _anchor_lock(
    *, width: int, height: int, backend: ReferenceEditBackend, ctx: StageContext | None = None
) -> GenerationLock:
    cfg = get_settings().image_sequences
    if ctx is not None:
        width, height = _megapixel_size(width, height, _param_float(ctx, "megapixels", 0.0))
    # A preset name resolves to its prompt; a prompt written out in full passes through.
    style = resolve_style(
        cfg.anchor_style_prompt if ctx is None else _param(ctx, "style", cfg.anchor_style_prompt)
    )
    seed = cfg.anchor_seed if ctx is None else _param_int(ctx, "seed", cfg.anchor_seed)
    # The lock names what actually made the picture, because it is what a rerun is checked against:
    # a frame generated by one model must not be served from a cache entry another model wrote.
    package_ids = {
        "hidream-o1": ("hidream-o1-image", cfg.anchor_model_revision),
        "flux2-dev": ("flux2-dev.reference", "FLUX.2-dev-Q4_K_M"),
    }
    package_id, revision = package_ids.get(backend.name, ("mock.reference-edit", "mock-1"))
    return GenerationLock(
        workflow_package_id=package_id,
        workflow_package_version="1.0.0",
        model_revision=revision,
        width=width,
        height=height,
        seed=seed,
        sampler=cfg.anchor_sampler,
        steps=cfg.anchor_steps,
        guidance=cfg.anchor_guidance,
        style_prompt=style,
        camera_prompt="as staged by the shot camera",
        lighting_prompt=cfg.anchor_lighting_prompt,
        background_prompt=cfg.anchor_background_prompt,
        reference_asset_sha256="0" * 64,
    )


def _anchor_prompt(ctx: StageContext, shot: ShotSpec | None, lock: GenerationLock) -> str:
    """The style leads, then the subject, then the shot's one-instant state."""
    framing = _param(ctx, "prompt").strip()
    world = ""
    story_path = _story_plan_path(ctx)
    if story_path is not None:
        world = (StoryPlan.model_validate_json(story_path.read_text()).visual_subject or "").strip()
    # The shot's state sentence is built by `shots.prompt_compile.state_sentence`, which already
    # embeds the story's `visual_subject`.
    staging = shot.description.strip() if shot is not None and shot.description else ""
    said = [lock.style_prompt.rstrip("."), staging.rstrip(".")]
    parts = [lock.style_prompt]
    for clause in (world, framing):
        bare = clause.rstrip(".")
        if clause and not any(bare and bare in already for already in said):
            parts.append(clause)
            said.append(bare)
    if len(parts) == 1 and not staging:
        parts.append(ctx.campaign.brief.topic.strip())
    if staging:
        parts.append(staging)
    return ". ".join(p.rstrip(".") for p in parts if p) + "."


def _identity_reference(shot: ShotSpec | None, character_id: str) -> tuple[bytes, str] | None:
    """The character's styled identity sheet, addressed by the digest the plan named."""
    from content_factory.controls.identity_sheets import load_sheet_by_sha

    if shot is None:
        return None
    character = next((c for c in shot.characters if c.id == character_id), None)
    if character is None or not character.reference_image_sha256:
        return None
    root = Path(get_settings().controls.assets_root)
    for sha in character.reference_image_sha256:
        png = load_sheet_by_sha(root, character.asset, sha)
        if png is not None:
            return png, sha
    return None


def _png_size(png: bytes) -> dict[str, int]:
    """(width, height) from the PNG's IHDR — no image library, no decode."""
    if len(png) < 24 or png[12:16] != b"IHDR":
        return {}
    return {
        "png_width": int.from_bytes(png[16:20], "big"),
        "png_height": int.from_bytes(png[20:24], "big"),
    }


def _conditioning_for_frame(
    bundle: ControlBundle,
    bundle_dir: Path,
    frame: int,
    shot: ShotSpec | None,
    wanted: Sequence[str] = ("pose_skeleton",),
) -> tuple[ControlConditioning, list[dict]]:
    """The references for one frame, in the order upstream's IP pipeline expects."""
    refs: list[bytes] = []
    boxes: list = []
    slots: list[dict] = []
    if "identity" in wanted:
        for subject in bundle.subjects:
            ident = _identity_reference(shot, subject.subject_id)
            box = subject.layouts[frame] if frame < len(subject.layouts) else None
            if ident is not None and box is not None:
                png, sha = ident
                refs.append(png)
                boxes.append(box)
                slots.append(
                    {
                        "slot": len(refs) - 1,
                        "role": "identity",
                        "subject_id": subject.subject_id,
                        "sha256": sha,
                        "box": box.model_dump(mode="json"),
                    }
                )
    available = {t.kind.value for t in bundle.tracks}
    # A skeleton of nobody is not conditioning, it is a scheduler switch.
    staged_figures = bool(shot.characters) if shot is not None else bool(bundle.subjects)
    if not staged_figures:
        wanted = tuple(k for k in wanted if k != "pose_skeleton")
    for kind in wanted:
        if kind == "identity":
            continue
        png_path = bundle_dir / kind / "frames" / f"{frame:04d}.png"
        if kind in available and png_path.exists():
            data = png_path.read_bytes()
            refs.append(data)
            slots.append(
                {
                    "slot": len(refs) - 1,
                    "role": kind,
                    "subject_id": None,
                    "sha256": sha256_hex(data),
                    "box": None,
                }
            )
    return ControlConditioning(reference_pngs=tuple(refs), layout_boxes=tuple(boxes)), slots


def _shots_by_id(ctx: StageContext) -> dict[str, ShotSpec]:
    plan_path = ctx.ddir() / "shots" / "plan.json"
    if not plan_path.exists():
        return {}
    plan = ShotPlan.model_validate_json(plan_path.read_text())
    return {s.shot_id: s for s in plan.shots}


def _free_the_gpu(ctx: StageContext, reason: str) -> dict:
    """Evict the managed GPU tenants (and the Ollama models) before a skill loads its own."""
    from content_factory.services.local import free_the_gpu

    freed = free_the_gpu()
    if freed["stopped"] or freed["ollama_unloaded"]:
        _log_execution(ctx, f"free_gpu:{reason}", {**freed, "vram_mib": gpu_memory_used_mib()})
    return freed


GPU_GONE_MARKERS = (
    "no devices were found",
    "unable to determine the device handle",
    "has fallen off the bus",
    "couldn\u2019t communicate with the nvidia driver",
    "couldn't communicate with the nvidia driver",
)
"""What `nvidia-smi` says when the card is not there any more, lowercased.

Measured 2026-09-10 03:12 on vegaserv: an uncorrectable PCIe AER error during an LTX-2.5 22B
generation, then `Xid 79, GPU has fallen off the bus` and `Xid 154, GPU recovery action changed
to 0x2 (Node Reboot Required)`. From inside the pipeline that looked like two unrelated bugs —
`ComfyTransientError: All connection attempts failed` on one lane and SeedVR2's
`SafetensorError: device cpu:0 is invalid` on the next, because with no GPU to see the upscaler
put its VAE on the CPU — and a queue would have spent every remaining job finding out.
"""


def gpu_is_gone() -> str:
    """`""` when the GPU is there (or there never was one), else what nvidia-smi said."""
    import subprocess

    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""  # no nvidia-smi: an offline machine, not a broken one
    said = f"{proc.stdout}\n{proc.stderr}".strip()
    low = said.lower()
    if any(marker in low for marker in GPU_GONE_MARKERS):
        return " ".join(said.split())[:300]
    return ""


def gpu_memory_used_mib() -> int | None:
    """What is on the card right now, in MiB, or None where there is no nvidia-smi."""
    import subprocess

    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = proc.stdout.strip().splitlines()[:1]
    if proc.returncode != 0 or not first or not first[0].strip().isdigit():
        return None
    return int(first[0].strip())


def _generation_telemetry(
    *, before: int | None, seconds: float, backend: object, extra: dict | None = None
) -> dict:
    """What one uncached generation cost, in the marker's own words."""
    facts: dict[str, object] = {
        "seconds": round(seconds, 2),
        "vram_before_mib": before,
        "vram_after_mib": gpu_memory_used_mib(),
    }
    server = getattr(backend, "last_facts", None)
    if isinstance(server, dict) and server.get("elapsed_s") is not None:
        facts["server_elapsed_s"] = server["elapsed_s"]
    facts.update(extra or {})
    return {k: v for k, v in facts.items() if v is not None}


MONOCHROME_WORDS = ("monochrome", "no colour", "no color", "greyscale", "grayscale")
"""Style phrasings that mean "no colour". A style that asks for it and gets 39 % saturated pixels
is a half-applied instruction, which is what reads as *weird* — the one blocker-severity finding
``qc.frame_review`` raises."""

PHOTOGRAPHIC_STYLES = ("photographic", "photographic_set", "cinematic", "low_key", "documentary")
"""The presets that asked to look photographed, by name.

Keyed on the preset NAME rather than on words in the prompt, which is the difference between this
and MONOCHROME_WORDS above. "photograph" appears inside several illustration presets as the thing
they are *not* ("no photographic lighting"), so a substring test would arm the dead-flat texture
check on exactly the styles it must never fire for — a watercolour has large flat areas because
that is what watercolour is. A style written out in full has no name, gets no match, and is
treated as not photographic: the quiet default, for a string nobody has classified."""


def _expects_photographic(style_prompt: str) -> bool:
    """Whether the dead-flat texture check applies to frames made under this style."""
    from content_factory.sequences.styles import style_name_for

    return style_name_for(style_prompt or "") in PHOTOGRAPHIC_STYLES


def _blocker_findings(png: bytes, *, expect_monochrome: bool):
    """The deterministic frame checks, reduced to the ones that mean "do not ship this"."""
    from content_factory.qc.frame_review import colour_findings, edge_findings, tonal_findings

    found = [
        *tonal_findings(png),
        *colour_findings(png, expect_monochrome=expect_monochrome),
        *edge_findings(png),
    ]
    return [f for f in found if f.severity == "blocker" and not f.passed]


def _generate_checked(
    ctx: StageContext,
    backend: ReferenceEditBackend,
    prompt: str,
    cond: ControlConditioning,
    lock: GenerationLock,
    *,
    dest: Path,
    label: str,
    warmed: set[str],
    seed_offset: int = 0,
) -> tuple[bytes, int, int, dict]:
    """Generate an anchor until the blocker checks pass."""
    import time

    cfg = get_settings().image_sequences
    expect_monochrome = any(w in (lock.style_prompt or "").lower() for w in MONOCHROME_WORDS)
    candidates: list[dict] = []

    # Every host that can draw this, the given one first.
    def _where(host: object) -> str:
        return str(getattr(host, "endpoint", getattr(host, "name", host)))

    hosts = [backend]
    seen_hosts = {_where(backend)}
    for host in _reference_backends(ctx):
        if _where(host) not in seen_hosts:
            hosts.append(host)
            seen_hosts.add(_where(host))
    for attempt in range(1, cfg.max_regen_attempts_per_frame + 1):
        png = None
        last_error: Exception | None = None
        seed = lock.seed + seed_offset + attempt - 1
        # Bound before the host loop so the telemetry below always has values; the real readings
        # are taken inside it, once, immediately before the generation that produced the frame.
        before: int | None = None
        started = time.monotonic()
        for host in hosts:
            try:
                _ensure_backend_ready(host, warmed)
                before = gpu_memory_used_mib()
                started = time.monotonic()
                png = host.generate(prompt, cond, lock, seed=seed)
                backend = host
                break
            except Exception as exc:
                last_error = exc
                _log_execution(ctx, f"anchor-host-failed:{getattr(host, 'endpoint', host.name)}")
        if png is None:
            raise last_error if last_error is not None else RuntimeError("no host generated")
        telemetry = _generation_telemetry(
            before=before,
            seconds=time.monotonic() - started,
            backend=backend,
            extra={"attempt": attempt},
        )
        blockers = _blocker_findings(png, expect_monochrome=expect_monochrome)
        if not blockers:
            return png, attempt, seed, telemetry
        kept = dest.with_suffix(f".attempt{attempt}.png")
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(png)
        candidates.append(
            {
                "attempt": attempt,
                "seed": seed,
                "path": str(kept.relative_to(ctx.ddir())),
                "blockers": [f.model_dump(mode="json") for f in blockers],
                "telemetry": telemetry,
            }
        )
    checks = sorted({f["check"] for c in candidates for f in c["blockers"]})
    raise BlockedError(
        f"anchor {label} failed {', '.join(checks)} on every attempt",
        stage="generate_anchor",
        attempts=len(candidates),
        candidates=candidates,
    )


def _anchor_from_upload(ctx: StageContext) -> StageOutput:
    """The operator's own still as the anchor, drawn by nobody."""
    uploads = ctx.project_dir / UPLOADS_DIRNAME
    stills = (
        sorted(
            p
            for p in uploads.glob("**/*")
            if p.is_file() and p.suffix.lower() in PICTURE_SUFFIXES_IN
        )
        if uploads.is_dir()
        else []
    )
    if not stills:
        msg = (
            "generate_anchor source=upload has no picture to start from: put one in"
            f" {uploads} (`content-factory make <lane> --input <image>`) or set the node back to"
            " source=generate to draw one."
        )
        raise RuntimeError(msg)
    if len(stills) > 1:
        names = ", ".join(p.name for p in stills)
        msg = f"generate_anchor source=upload found {len(stills)} pictures ({names}); leave one."
        raise RuntimeError(msg)
    source = stills[0]
    anchors_dir = ctx.ddir() / "anchors"
    png_path = anchors_dir / "anchor.png"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".png":
        png = source.read_bytes()
    else:
        from content_factory.audio.mix import ffmpeg

        ffmpeg(["-i", str(source), str(png_path)], timeout=120)
        png = png_path.read_bytes()
    _write_atomic(png_path, png)
    size = _png_size(png)
    width = int(size.get("png_width") or 0)
    height = int(size.get("png_height") or 0)
    if width < 16 or height < 16:
        msg = f"{source.name} is {width}x{height}; that is not a picture anything can work from"
        raise RuntimeError(msg)
    record = {
        "frame_index": 0,
        "shot_id": None,
        "png_sha256": sha256_hex(png),
        "backend": "upload",
        "source": source.name,
        **size,
    }
    _write(anchors_dir / "anchor.done.json", json.dumps(record, indent=1, sort_keys=True))
    _write(
        anchors_dir / "manifest.json",
        json.dumps(
            {
                "backend": "upload",
                "shots": [
                    {
                        "shot_id": None,
                        "width": width,
                        "height": height,
                        "frames": [
                            {
                                "frame_index": 0,
                                "path": "anchors/anchor.png",
                                "sha256": record["png_sha256"],
                            }
                        ],
                    }
                ],
            },
            indent=1,
            sort_keys=True,
        ),
    )
    return StageOutput(
        _hash_obj([record["png_sha256"]]),
        {
            "anchors": 1,
            "backend": "upload",
            "shots": 1,
            "source": source.name,
            "size": f"{width}x{height}",
        },
    )


def _anchor_frame_paths(anchors_dir: Path, frame_id: str) -> tuple[Path, Path, str] | None:
    """``frame_id`` -> (png, marker, a filename-safe key), or None if it is not an anchor's."""
    shot, _, tail = frame_id.rpartition(":")
    if not tail.isdigit():
        return None
    idx = int(tail)
    if shot in ("", "anchor", "None"):
        return anchors_dir / "anchor.png", anchors_dir / "anchor.done.json", f"anchor_{idx:04d}"
    png = anchors_dir / shot / f"{idx:04d}.png"
    return png, png.with_suffix(".done.json"), f"{shot}_{idx:04d}"


def _clear_rejected_anchors(ctx: StageContext) -> list[str]:
    """Drop the cache markers of anchors a person rejected, so the next run redraws them."""
    from content_factory.services.frame_reviews import current_batch

    # The MERGED state, not `batch.json` alone.
    batch = current_batch(ctx.ddir())
    if batch is None:
        return []
    anchors_dir = ctx.ddir() / "anchors"
    reject_dir = anchors_dir / "rejected"
    cleared: list[str] = []
    for record in batch.rejected:
        resolved = _anchor_frame_paths(anchors_dir, record.frame_id)
        if resolved is None:
            continue
        png, marker, key = resolved
        if not marker.exists() and not png.exists():
            continue
        reject_dir.mkdir(parents=True, exist_ok=True)
        # Numbered, so a second rejection does not overwrite the first and the offset below has
        # something to count.
        turn = 1 + len(list(reject_dir.glob(f"{key}.reviewed*.png")))
        if png.exists():
            png.replace(reject_dir / f"{key}.reviewed{turn}.png")
        if marker.exists():
            marker.replace(reject_dir / f"{key}.reviewed{turn}.done.json")
        cleared.append(record.frame_id)
    if cleared:
        _log_execution(ctx, "anchors-rejected", {"redrawing": sorted(cleared)})
    return sorted(cleared)


def _anchor_redirects(ctx: StageContext) -> dict[str, str]:
    """``frame_id -> what the reviewer said it should show instead``, for frames they rejected."""
    from content_factory.services.frame_reviews import current_batch

    batch = current_batch(ctx.ddir())
    if batch is None:
        return {}
    return {
        f.frame_id: f.redirect.strip()
        for f in batch.frames
        if f.verdict == "reject" and f.redirect.strip()
    }


def _with_redirects(prompt: str, redirects: Sequence[str]) -> str:
    """``prompt`` with the reviewer's corrections appended, as descriptions of the picture."""
    extra = [r.strip().rstrip(".") for r in redirects if r.strip()]
    if not extra:
        return prompt
    return prompt.rstrip().rstrip(".") + ". " + ". ".join(extra) + "."


def _applied_redirects(anchors_dir: Path, key: str, marker: Path, pending: str) -> tuple[str, ...]:
    """The corrections this draw is made with."""
    if marker.exists():
        try:
            return tuple(json.loads(marker.read_text()).get("redirects", ()))
        except (OSError, ValueError):
            return ()
    return _redirects_for_frame(anchors_dir, key, pending)


def _redirects_for_frame(anchors_dir: Path, key: str, pending: str) -> tuple[str, ...]:
    """Every redirect that applies to the next draw of one frame, oldest first."""
    applied: tuple[str, ...] = ()
    reject_dir = anchors_dir / "rejected"
    if reject_dir.is_dir():
        rounds = sorted(
            reject_dir.glob(f"{key}.reviewed*.done.json"),
            key=lambda q: len(q.name),  # reviewed2 sorts after reviewed10 lexically; length first
        )
        rounds.sort()
        if rounds:
            try:
                applied = tuple(json.loads(rounds[-1].read_text()).get("redirects", ()))
            except (OSError, ValueError):
                applied = ()
    if pending and pending not in applied:
        applied = (*applied, pending)
    return applied


def _anchor_rejection_offsets(anchors_dir: Path) -> dict[str, int]:
    """How far to move each anchor's seed, from how many times it has been turned down."""
    reject_dir = anchors_dir / "rejected"
    if not reject_dir.is_dir():
        return {}
    offsets: dict[str, int] = {}
    for png in reject_dir.glob("*.reviewed*.png"):
        key = png.name.split(".", 1)[0]
        offsets[key] = offsets.get(key, 0) + 1
    return {key: n * 8 for key, n in offsets.items()}


def stage_generate_anchor(ctx: StageContext) -> StageOutput:
    """Anchor images."""
    if _param(ctx, "source", "generate") == "upload":
        return _anchor_from_upload(ctx)
    backend = _reference_backend(ctx)
    warmed: set[str] = set()
    # A verdict is only worth collecting if the run can act on it.
    redrawing = _clear_rejected_anchors(ctx)
    seed_offsets = _anchor_rejection_offsets(ctx.ddir() / "anchors")
    # Read before the loop, because clearing above is what makes these frames eligible to redraw
    # and the verdict they came from is the only place the correction exists.
    pending_redirects = _anchor_redirects(ctx)
    references = tuple(
        r.strip()
        for r in _param(
            ctx, "references", ",".join(get_settings().image_sequences.anchor_references)
        ).split(",")
        if r.strip()
    )
    anchors_dir = ctx.ddir() / "anchors"
    manifest_path = ctx.ddir() / "controls" / "manifest.json"
    records: list[dict] = []
    shots_out: list[dict] = []
    if manifest_path.exists():
        shots = _shots_by_id(ctx)
        for entry in json.loads(manifest_path.read_text())["bundles"]:
            bundle_path = ctx.ddir() / entry["path"]
            bundle = ControlBundle.model_validate_json(bundle_path.read_text())
            shot = shots.get(bundle.shot_id)
            lock = _anchor_lock(width=bundle.width, height=bundle.height, backend=backend, ctx=ctx)
            prompt = _anchor_prompt(ctx, shot, lock)
            frames: list[dict] = []
            for idx in bundle.anchor_frames:
                cond, slots = _conditioning_for_frame(
                    bundle, bundle_path.parent, idx, shot, references
                )
                key = f"{bundle.shot_id}_{idx:04d}"
                seed_offset = seed_offsets.get(key, 0)
                seed = lock.seed + seed_offset
                png_path = anchors_dir / bundle.shot_id / f"{idx:04d}.png"
                marker = png_path.with_suffix(".done.json")
                redirects = _applied_redirects(
                    anchors_dir,
                    key,
                    marker,
                    pending_redirects.get(f"{bundle.shot_id}:{idx:04d}", ""),
                )
                frame_prompt = _with_redirects(prompt, redirects)
                input_hash = _hash_obj(
                    {
                        "lock": lock.model_dump(mode="json"),
                        "prompt": frame_prompt,
                        "conditioning": cond.sha256(),
                        "references": list(references),
                        "seed": seed,
                        "backend": backend.name,
                    }
                )
                anchor_cached = _cached_output(marker, png_path, input_hash, "png_sha256")
                if anchor_cached is not None:
                    record = {**anchor_cached, "cache_hit": True}
                else:
                    png, attempts, used_seed, telemetry = _generate_checked(
                        ctx,
                        backend,
                        frame_prompt,
                        cond,
                        lock,
                        dest=png_path,
                        label=f"{bundle.shot_id}:{idx}",
                        warmed=warmed,
                        seed_offset=seed_offset,
                    )
                    _write_atomic(png_path, png)
                    record = {
                        "frame_index": idx,
                        "shot_id": bundle.shot_id,
                        "input_hash": input_hash,
                        # How many times the deterministic checks sent it back, and which seed
                        # actually drew the frame on disk. 1 and lock.seed is the ordinary case.
                        "attempts": attempts,
                        "seed": used_seed,
                        "telemetry": telemetry,
                        # The exact words the model was given.
                        "prompt": frame_prompt,
                        # The reviewer corrections folded into that prompt, in the order they were
                        # given.
                        "redirects": list(redirects),
                        "png_sha256": sha256_hex(png),
                        "backend": backend.name,
                        "references": len(cond.reference_pngs),
                        "reference_kinds": list(references),
                        # What each slot carried, in order. The count decides the editing recipe
                        # upstream, so "which slots were filled" is not a detail.
                        "reference_slots": slots,
                        "layout_boxes": len(cond.layout_boxes),
                        # What the model actually returned.
                        **_png_size(png),
                        "cache_hit": False,
                    }
                    _write(marker, json.dumps(record, indent=1, sort_keys=True))
                    _log_execution(ctx, f"anchor:{bundle.shot_id}:{idx}", telemetry)
                frames.append(record)
                records.append(record)
            shots_out.append(
                {
                    "shot_id": bundle.shot_id,
                    "width": bundle.width,
                    "height": bundle.height,
                    "frames": [
                        {
                            "frame_index": r["frame_index"],
                            "path": f"anchors/{bundle.shot_id}/{r['frame_index']:04d}.png",
                            "sha256": r["png_sha256"],
                        }
                        for r in frames
                    ],
                }
            )
    else:
        # The story's frame, not the settings' default.
        width, height = get_settings().image_sequences.default_working_resolution
        story_path = _story_plan_path(ctx)
        if story_path is not None:
            story = StoryPlan.model_validate_json(story_path.read_text())
            width, height = story.width, story.height
        lock = _anchor_lock(width=width, height=height, backend=backend, ctx=ctx)
        prompt = _anchor_prompt(ctx, None, lock)
        cond = ControlConditioning()
        seed_offset = seed_offsets.get("anchor_0000", 0)
        png_path = anchors_dir / "anchor.png"
        marker = anchors_dir / "anchor.done.json"
        redirects = _applied_redirects(
            anchors_dir, "anchor_0000", marker, pending_redirects.get("anchor:0000", "")
        )
        frame_prompt = _with_redirects(prompt, redirects)
        input_hash = _hash_obj(
            {
                "lock": lock.model_dump(mode="json"),
                "prompt": frame_prompt,
                "conditioning": cond.sha256(),
                "seed": lock.seed + seed_offset,
                "backend": backend.name,
            }
        )
        anchor_cached = _cached_output(marker, png_path, input_hash, "png_sha256")
        if anchor_cached is not None:
            record = {**anchor_cached, "cache_hit": True}
        else:
            png, attempts, used_seed, telemetry = _generate_checked(
                ctx,
                backend,
                frame_prompt,
                cond,
                lock,
                dest=png_path,
                label="anchor",
                warmed=warmed,
                seed_offset=seed_offset,
            )
            _write_atomic(png_path, png)
            record = {
                "frame_index": 0,
                "shot_id": None,
                "input_hash": input_hash,
                "attempts": attempts,
                "seed": used_seed,
                "telemetry": telemetry,
                "prompt": frame_prompt,
                "redirects": list(redirects),
                "png_sha256": sha256_hex(png),
                "backend": backend.name,
                "references": 0,
                "layout_boxes": 0,
                "cache_hit": False,
            }
            _write(marker, json.dumps(record, indent=1, sort_keys=True))
            _log_execution(ctx, "anchor:single", telemetry)
        records.append(record)
        shots_out.append(
            {
                "shot_id": None,
                "width": width,
                "height": height,
                "frames": [
                    {"frame_index": 0, "path": "anchors/anchor.png", "sha256": record["png_sha256"]}
                ],
            }
        )
    _write(
        anchors_dir / "manifest.json",
        json.dumps({"backend": backend.name, "shots": shots_out}, indent=1, sort_keys=True),
    )
    return StageOutput(
        _hash_obj([r["png_sha256"] for r in records]),
        {
            "anchors": len(records),
            "backend": backend.name,
            "shots": len(shots_out),
            "cache_hits": sum(1 for r in records if r["cache_hit"]),
            # Above the number of anchors means the checks sent frames back. Worth seeing in the
            # run log: it is GPU time spent on frames that arrived unusable.
            "attempts": sum(int(r.get("attempts", 1)) for r in records),
            # Which frames a person turned down and this run redrew. A redraw nobody can see in
            # the record is a redraw nobody can audit.
            "redrawn_after_rejection": redrawing,
        },
    )


def _first_anchor(ctx: StageContext) -> tuple[str, int, int]:
    manifest = json.loads((ctx.ddir() / "anchors" / "manifest.json").read_text())
    first = manifest["shots"][0]
    return first["frames"][0]["sha256"], first["width"], first["height"]


def _anchor_recorded_style(ctx: StageContext) -> str:
    """The style clause the first anchor was actually drawn with, from its own marker."""
    for marker in (
        ctx.ddir() / "anchors" / "anchor.done.json",
        *sorted((ctx.ddir() / "anchors").glob("*/0000.done.json")),
    ):
        if marker.is_file():
            prompt = str(json.loads(marker.read_text()).get("prompt", ""))
            return prompt.split(". ", 1)[0].strip() if prompt else ""
    return ""


ANCHOR_BACKEND_MODELS: dict[str, str] = {
    # A backend's `.name` (what `generate_anchor` writes into its marker) back to the `model`
    # widget's spelling; the mock's marker has to read "mock" rather than nothing.
    "hidream-o1": "hidream-o1",
    "flux2-dev": "flux2-dev",
    "mock-reference-edit": "mock",
    "mock": "mock",
}


def _anchor_recorded_backend(ctx: StageContext) -> str:
    """Which backend actually drew the first anchor, from its own marker — or `""`."""
    for marker in (
        ctx.ddir() / "anchors" / "anchor.done.json",
        *sorted((ctx.ddir() / "anchors").glob("*/0000.done.json")),
    ):
        if marker.is_file():
            return str(json.loads(marker.read_text()).get("backend", "")).strip()
    return ""


def stage_lock_generation(ctx: StageContext) -> StageOutput:
    """Freeze the generation lock around the first anchor, in the style the anchor was drawn in."""
    sha, width, height = _first_anchor(ctx)
    anchor_style = _anchor_recorded_style(ctx)
    params = dict(ctx.params)
    if anchor_style and not _param(ctx, "style", "").strip():
        params["style"] = anchor_style
    # And the model, for the same reason and from the same marker.
    drew_it = ANCHOR_BACKEND_MODELS.get(_anchor_recorded_backend(ctx), "")
    if drew_it and not _param(ctx, "model", "").strip():
        params["model"] = drew_it
    backend = _reference_backend(replace(ctx, params=params))
    lock = _anchor_lock(
        width=width, height=height, backend=backend, ctx=replace(ctx, params=params)
    ).model_copy(update={"reference_asset_sha256": sha})
    _write(ctx.ddir() / "sequence" / "lock.json", lock.model_dump_json(indent=1))
    return StageOutput(lock.content_hash(), {"reference": sha[:12], "backend": backend.name})


def _sequence_style_name(ctx: StageContext) -> str:
    """The style preset the sequence was locked to, or `""` when there is no lock on disk."""
    path = ctx.ddir() / "sequence" / "lock.json"
    if not path.exists():
        return ""
    from content_factory.sequences.styles import style_name_for

    return style_name_for(GenerationLock.model_validate_json(path.read_text()).style_prompt)


def _sequence_lock(ctx: StageContext) -> GenerationLock:
    return GenerationLock.model_validate_json((ctx.ddir() / "sequence" / "lock.json").read_text())


def _controls_compiler(ctx: StageContext) -> str | None:
    manifest = ctx.ddir() / "controls" / "manifest.json"
    return json.loads(manifest.read_text())["compiler"] if manifest.exists() else None


def _sequence_motion_plan(ctx: StageContext) -> tuple[MotionPlan, dict[int, str]]:
    """The frame plan a keyframe lane draws, and what each frame is a picture *of*."""
    path = _story_plan_path(ctx)
    if path is None:
        return sample_motion_plan(), {}
    story = StoryPlan.model_validate_json(path.read_text())
    if not story.beats:
        return sample_motion_plan(), {}
    frames = len(story.beats)
    # The fixture's subject and trajectory are kept and only re-timed: the story says nothing about
    # where a subject sits, and re-deriving the layout would change what drift masking lets move.
    base = sample_motion_plan()
    # The label reaches the model via "(subject: <label>)" in every edit instruction; left at the
    # fixture's, an owl sequence asked HiDream for "the same tawny owl ... (subject: hands)".
    label = (story.visual_subject or "").split(",")[0].strip()[:80] or base.subjects[0].label
    subjects = tuple(
        subject.model_copy(
            update={
                "label": label,
                "keyframes": tuple(
                    k.model_copy(update={"frame_index": min(k.frame_index, frames - 1)})
                    for i, k in enumerate(subject.keyframes)
                    if i == 0 or min(k.frame_index, frames - 1) > 0
                ),
            }
        )
        for subject in base.subjects
    )
    plan = base.model_copy(
        update={
            "plan_id": "mp_story00000001",
            "sequence_id": "seq_story0000001",
            "frame_count": frames,
            "canvas_width": story.width,
            "canvas_height": story.height,
            "description": story.visual_subject or base.description,
            "subjects": subjects,
        }
    )
    scene_by_beat = {sc.beat_id: sc for sc in story.scenes}
    instructions: dict[int, str] = {}
    for i, beat in enumerate(story.beats):
        scene = scene_by_beat.get(beat.beat_id)
        what = getattr(scene, "alt_text", "") if scene is not None else ""
        if what:
            instructions[i] = what
    return plan, instructions


def _clear_rejected_frames(ctx: StageContext, workdir: Path) -> list[int]:
    """Drop the cache markers of frames a person rejected, so the next run redraws them."""
    from content_factory.services.frame_reviews import current_batch

    # Merged, for the reason `_clear_rejected_anchors` records: `batch.json` is the gate's last
    # word and `verdict.json` the operator's, and only the second carries a rejection since then.
    batch = current_batch(ctx.ddir())
    if batch is None:
        return []
    frames_dir = workdir / "frames"
    reject_dir = workdir / "rejected"
    cleared: list[int] = []
    for record in batch.rejected:
        # "frame:0007" -> 7. Anything else is not one of this stage's frames.
        _, _, tail = record.frame_id.partition(":")
        if not tail.isdigit():
            continue
        idx = int(tail)
        marker = frames_dir / f"{idx:04d}.done.json"
        png = frames_dir / f"{idx:04d}.png"
        if not marker.exists() and not png.exists():
            continue
        reject_dir.mkdir(parents=True, exist_ok=True)
        # Numbered, so a second rejection does not overwrite the first and the seed offset below
        # has something to count.
        turn = 1 + len(list(reject_dir.glob(f"{idx:04d}.reviewed*.png")))
        if png.exists():
            png.replace(reject_dir / f"{idx:04d}.reviewed{turn}.png")
        if marker.exists():
            marker.replace(reject_dir / f"{idx:04d}.reviewed{turn}.done.json")
        cleared.append(idx)
    if cleared:
        _log_execution(ctx, "frames-rejected", {"redrawing": sorted(cleared)})
    return sorted(cleared)


def _rejection_seed_offsets(workdir: Path) -> dict[int, int]:
    """How far to move each frame's seed, from how many times it has been turned down."""
    reject_dir = workdir / "rejected"
    if not reject_dir.is_dir():
        return {}
    offsets: dict[int, int] = {}
    for png in reject_dir.glob("[0-9]*.reviewed*.png"):
        head = png.name.split(".", 1)[0]
        if head.isdigit():
            offsets[int(head)] = offsets.get(int(head), 0) + 1
    # One rejection is worth more than one attempt: the three drift retries already walk the seed
    # by 0, 1, 2, so a redraw moved by one would land on the rejected run's second attempt.
    return {idx: n * 8 for idx, n in offsets.items()}


def stage_generate_keyframes(ctx: StageContext) -> StageOutput:
    """Hub-and-spoke keyframes from the MotionPlan (builtin controls)."""
    if _controls_compiler(ctx) == "blender":
        return StageOutput(
            _hash_obj({"skipped": "blender"}),
            {"skipped": "blender bundles keyframe through anchors; generate_video consumes them"},
        )
    plan, frame_instructions = _sequence_motion_plan(ctx)
    lock = _sequence_lock(ctx)
    workdir = ctx.ddir() / "sequence"
    redrawing = _clear_rejected_frames(ctx, workdir)
    anchor = ctx.ddir() / "anchors" / "anchor.png"
    if anchor.exists() and not (workdir / "anchor.png").exists():
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "anchor.png").write_bytes(anchor.read_bytes())
    cfg = get_settings().image_sequences
    # Per style, not one pair for twelve art directions: a watercolour wash legitimately varies
    # more between frames than a photograph. The preset name is recovered from the locked prompt.
    from content_factory.sequences.drift import UNCALIBRATED
    from content_factory.sequences.styles import style_name_for

    locked_min, delta_max = cfg.drift_thresholds.resolve(style=style_name_for(lock.style_prompt))
    # The configured default (0.92 / 0.15) is a *mock* number, met by construction; no real frame
    # came near it (six drawings, eighteen attempts, nothing written), so `uncalibrated` observes.
    if _param(ctx, "drift_profile", "configured").strip().lower() == "uncalibrated":
        locked_min, delta_max = UNCALIBRATED
    # 0 is "leave the profile's own value alone", not "a floor of zero": the real thresholds are
    # per-style and per-camera, and a widget default would silently override them everywhere.
    locked_min = _param_float(ctx, "locked_min", 0.0) or locked_min
    delta_max = _param_float(ctx, "style_delta_max", 0.0) or delta_max
    pool = _reference_backends(ctx)
    result = build_sequence(
        plan,
        lock,
        pool[0],
        workdir,
        seed_offsets=_rejection_seed_offsets(workdir),
        backends=pool,
        frame_instructions=frame_instructions,
        max_regen_attempts=cfg.max_regen_attempts_per_frame,
        locked_region_similarity_min=locked_min,
        style_delta_max=delta_max,
    )
    if result.failed:
        # Already retried max_regen_attempts_per_frame times and still drifting: block for a person.
        # Candidates name `sequence/rejected/`, where the drawing and its measurement actually are.
        rejects = workdir / "rejected"
        measured = {}
        for idx in result.failed:
            path = rejects / f"{idx:04d}.json"
            if path.is_file():
                measured[idx] = json.loads(path.read_text())
        raise BlockedError(
            f"keyframes {result.failed} still drift from the anchor after regeneration",
            stage="generate_keyframes",
            attempts=cfg.max_regen_attempts_per_frame,
            candidates=[
                {
                    "attempt": measured.get(idx, {}).get(
                        "attempts", cfg.max_regen_attempts_per_frame
                    ),
                    "path": f"sequence/rejected/{idx:04d}.png",
                    "blockers": [
                        {
                            "check": "locked_region_similarity",
                            "frame_index": idx,
                            "measured": measured.get(idx, {}).get("locked_region_similarity"),
                            "needed": locked_min,
                        },
                        {
                            "check": "style_delta",
                            "frame_index": idx,
                            "measured": measured.get(idx, {}).get("style_delta"),
                            "needed": delta_max,
                        },
                    ],
                }
                for idx in result.failed
            ],
        )
    return StageOutput(
        _hash_obj([f["png_sha256"] for f in result.frames]),
        {
            "frames": len(result.frames),
            "regenerated": sorted(set(result.regenerated)),
            # Frames a person turned down on the last pass, which this run redrew. Kept in the
            # facts because a redraw nobody can see in the record is a redraw nobody can audit.
            "redrawn_after_rejection": redrawing,
            "anchor": result.anchor_sha256[:12],
        },
    )


def stage_review_frames(ctx: StageContext) -> StageOutput:
    """Gate the generated frames on someone having looked at them."""
    import datetime as dt

    from content_factory.qc.frame_review import (
        consistency_matrix,
        contact_sheet,
        review_findings,
    )
    from content_factory.qc.reviewer import intended_reviewer, request_markdown
    from content_factory.qc.verdict import merge_verdict
    from content_factory.schemas.review import FrameRecord, FrameReviewBatch

    # Review what this lane's graph put in front of the reviewer: `sequence/frames` on the keyframe
    # lanes; on the Blender/scene lanes, which generate no keyframes, the anchors *are* the frames.
    frames_dir = ctx.ddir() / "sequence" / "frames"
    sequence_pngs = sorted(frames_dir.glob("[0-9]*.png"))
    pngs: list[tuple[str, bytes]] = []
    if sequence_pngs:
        source = "sequence/frames"
        pngs = [(f"frame:{path.stem}", path.read_bytes()) for path in sequence_pngs]
    else:
        source = "anchors/manifest.json"
        manifest_path = ctx.ddir() / "anchors" / "manifest.json"
        if not manifest_path.exists():
            msg = (
                "review_frames found neither sequence/frames nor anchors/manifest.json: "
                "run generate_keyframes or generate_anchor first"
            )
            raise RuntimeError(msg)
        entries = json.loads(manifest_path.read_text())["shots"]
        for entry in entries:
            for frame in entry["frames"]:
                # `or`, not a dict default: the single-anchor branch writes `shot_id: None`, so
                # `.get(..., "anchor")` returned None and frames were called "None:0000".
                shot_id = entry.get("shot_id") or "anchor"
                frame_id = f"{shot_id}:{frame['frame_index']:04d}"
                pngs.append((frame_id, (ctx.ddir() / frame["path"]).read_bytes()))
    if not pngs:
        msg = f"review_frames: {source} lists no frames"
        raise RuntimeError(msg)

    raw_style = _param(ctx, "style", get_settings().image_sequences.anchor_style_prompt)
    # Resolved once: a preset name has to become its prompt before either predicate reads it, and
    # `resolve_style` refuses a short unknown token, so the empty case is guarded.
    style = resolve_style(raw_style) if raw_style.strip() else ""
    monochrome = any(word in style.lower() for word in ("monochrome", "no colour", "no color"))
    findings = review_findings(
        pngs,
        expect_monochrome=monochrome,
        expect_photographic=_expects_photographic(style),
    )
    sheet_rel = Path("reviews") / "frames" / "contact-sheet.png"
    sheet_png = contact_sheet(pngs, ctx.ddir() / sheet_rel, findings=findings)
    # Every frame against every other, not just its neighbour: a set can drift a little each step
    # and end somewhere else with every consecutive pair looking fine.
    matrix = consistency_matrix(pngs)
    _write(ctx.ddir() / "reviews" / "frames" / "consistency.json", json.dumps(matrix, indent=1))

    records = tuple(
        FrameRecord(
            frame_id=frame_id,
            png_sha256=sha256_hex(png),
            findings=tuple(findings.get(frame_id, [])),
        )
        for frame_id, png in pngs
    )
    batch = FrameReviewBatch(
        deliverable_id=ctx.deliverable_id or "dlv_unknown00001",
        contact_sheet_sha256=sha256_hex(sheet_png),
        contact_sheet_path=str(sheet_rel),
        frames=records,
        created_at=dt.datetime.now(dt.UTC),
    )
    # A verdict recorded earlier applies only if it reviewed these same images. The overlay is
    # `qc.verdict.merge_verdict`, shared with the app's review panel so both decide the same way.
    verdict_path = ctx.ddir() / "reviews" / "frames" / "verdict.json"
    if verdict_path.exists():
        prior = FrameReviewBatch.model_validate_json(verdict_path.read_text())
        batch = merge_verdict(batch, prior)
    _write(ctx.ddir() / "reviews" / "frames" / "batch.json", batch.model_dump_json(indent=1))

    flagged = sum(1 for r in batch.frames for f in r.findings if not f.passed)
    # Who is being asked: a run started from a Claude Code session has an agent present, and 27
    # runs were parked here with nobody coming. Empty means "not set", so the environment decides.
    wants = intended_reviewer(_param(ctx, "reviewer") or None)
    request_rel = Path("reviews") / "frames" / "request.md"
    if wants == "agent" and not batch.passed:
        _write(
            ctx.ddir() / request_rel,
            request_markdown(
                run_dir=str(ctx.project_dir),
                deliverable=ctx.deliverable_id or "dlv_short0000001",
                contact_sheet=str(sheet_rel),
                frames=batch.frames,
                consistency=matrix,
            ),
        )
    facts = {
        "frames": len(batch.frames),
        "reviewed_from": source,
        "findings_flagged": flagged,
        "contact_sheet": str(sheet_rel),
        "reviewer": batch.reviewer,
        "review_wanted_from": wants,
        "consistency_median": matrix["median_distance"],
        "consistency_outliers": matrix["outliers"],
        "rejected": [r.frame_id for r in batch.rejected],
    }
    if not batch.passed:
        detail = []
        for record in batch.rejected:
            why = record.reason or "; ".join(
                f.detail for f in record.findings if f.severity == "blocker" and not f.passed
            )
            detail.append(f"{record.frame_id}: {why}")
        if batch.unreviewed:
            if wants == "agent":
                detail.append(
                    f"{len(batch.unreviewed)} frame(s) awaiting an agent review — read "
                    f"{request_rel}: it lists every image to open, the consistency measurements, "
                    f"and the command that answers. `--accept-all` is not available to an agent"
                )
            else:
                detail.append(
                    f"{len(batch.unreviewed)} frame(s) not yet reviewed — look at "
                    f"{sheet_rel}, then `content-factory frames review --accept-all` "
                    f"or reject the ones that are wrong"
                )
        raise RuntimeError("review_frames blocked the run:\n  " + "\n  ".join(detail))
    return StageOutput(_hash_obj([r.png_sha256 for r in batch.frames]), facts)


def stage_drift_qc(ctx: StageContext) -> StageOutput:
    """Re-read the per-frame drift records the engine wrote; fail if any frame is missing."""
    if _controls_compiler(ctx) == "blender":
        return StageOutput(
            _hash_obj({"skipped": "blender"}), {"skipped": "no MotionPlan keyframes to check"}
        )
    frames_dir = ctx.ddir() / "sequence" / "frames"
    markers = sorted(frames_dir.glob("*.done.json"))
    if not markers:
        raise RuntimeError("drift_qc: no keyframes found; run generate_keyframes first")
    records = [json.loads(m.read_text()) for m in markers]
    worst_locked = min(r["drift"]["locked"] for r in records)
    worst_style = max(r["drift"]["style"] for r in records)
    return StageOutput(
        _hash_obj([r["png_sha256"] for r in records]),
        {
            "frames": len(records),
            "worst_locked_similarity": round(worst_locked, 4),
            "worst_style_delta": round(worst_style, 4),
        },
    )


def stage_package_sequence(ctx: StageContext) -> StageOutput:
    if _controls_compiler(ctx) == "blender":
        return StageOutput(
            _hash_obj({"skipped": "blender"}), {"skipped": "shots are packaged by generate_video"}
        )
    pkg = package_sequence(_sequence_motion_plan(ctx)[0], ctx.ddir() / "sequence")
    digest = _hash_obj({k: file_sha256(Path(v)) for k, v in pkg.items() if k != "frames"})
    return StageOutput(
        digest, {"frames": pkg["frames"], "outputs": sorted(k for k in pkg if k != "frames")}
    )


AUDIO_SUFFIXES_IN: tuple[str, ...] = (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac")
"""What ``transcribe_audio`` will pick up out of the run's uploads folder. FFmpeg reads far more
than this; the list is short because a lane that grabs "the audio file" out of a folder should
grab something a person would call a recording, not the audio track of a video they also dropped.
"""

CONTAINER_SUFFIXES_IN: tuple[str, ...] = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")
"""Containers this stage looks *inside* before deciding they are not recordings.

A phone, a voice-memo app and a meeting recorder all write MP4, so an operator's interview
arrives as a video file whose picture is an hour of black — and refusing it for its container
was the pipeline declining to read a file it can read perfectly well.
``ingest.convert.blank_picture`` measures which of these have nothing to look at; one that
carries real picture is a film and is still passed over, because "the audio track of a video they
also dropped" is what the list above exists to avoid grabbing.
"""


def _blank_pictured(paths: Sequence[Path]) -> dict[Path, str]:
    """Of these containers, the ones that are recordings, each with the reason it counted as one."""
    from content_factory.ingest.convert import blank_picture

    out: dict[Path, str] = {}
    for path in paths:
        blank = blank_picture(path)
        if blank is not None:
            out[path] = blank.reason
    return out


def _source_recording(ctx: StageContext) -> tuple[Path, str]:
    """The one recording this run is about, or a failure saying where to put one."""
    named = _param(ctx, "source").strip()
    if named:
        path = Path(named).expanduser()
        path = path if path.is_absolute() else ctx.project_dir / path
        if not path.is_file():
            msg = f"transcribe_audio: no recording at {path} (the node's source widget names it)"
            raise RuntimeError(msg)
        return path, ""
    uploads = ctx.project_dir / UPLOADS_DIRNAME
    files = sorted(p for p in uploads.glob("**/*") if p.is_file()) if uploads.is_dir() else []
    found = [p for p in files if p.suffix.lower() in AUDIO_SUFFIXES_IN]
    containers = [p for p in files if p.suffix.lower() in CONTAINER_SUFFIXES_IN]
    # Only when there is no plain recording: a folder holding both an interview and a clip is not
    # ambiguous, and probing the clip to discover that would be work done to learn nothing.
    blanks = _blank_pictured(containers) if not found and containers else {}
    found = found or sorted(blanks)
    if len(found) > 1:
        names = ", ".join(p.name for p in found)
        msg = (
            f"transcribe_audio: {len(found)} recordings in {uploads} ({names}). This lane is about"
            " one recording — leave one in the folder, or name it on the node's source widget."
        )
        raise RuntimeError(msg)
    if found:
        return found[0], blanks.get(found[0], "")
    normalised = ctx.ddir() / "audio" / "source.wav"
    if normalised.is_file():
        return normalised, ""
    # A folder holding only films is the one empty-handed case with something to say: the
    # operator did supply material, and the reason it was passed over is a measurement.
    passed_over = (
        f" ({', '.join(p.name for p in containers)} is there, but carries picture: this lane"
        " wants a recording, and the picture would be thrown away without being asked.)"
        if containers
        else ""
    )
    msg = (
        "transcribe_audio has no recording. Put one in"
        f" {uploads} (`content-factory make <lane> --input <file>` does that for you), drop one on"
        f" the canvas's Audio File node, or name a path on the node's source widget.{passed_over}"
    )
    raise RuntimeError(msg)


def stage_transcribe_audio(ctx: StageContext) -> StageOutput:
    """Read the words off a recording, with timings, and keep the normalised audio beside them."""
    from content_factory.audio.takes import TakeError
    from content_factory.audio.transcribe import (
        TRANSCRIBE_VERSION,
        normalise_recording,
        sentences,
        transcribe,
    )
    from content_factory.schemas.audio import SpeechTranscript

    cfg = get_settings().transcription
    engine = _param(ctx, "engine", cfg.engine)
    model = _param(ctx, "model", cfg.faster_whisper_model)
    language = _param(ctx, "language", cfg.language)
    audio_dir = ctx.ddir() / "audio"
    source, picture = _source_recording(ctx)
    normalised = audio_dir / "source.wav"
    # Normalising in place would read and write the same file; a recording that is already the
    # normalised copy is left exactly as it is.
    if source.resolve() != normalised.resolve():
        try:
            normalise_recording(source, normalised, sample_rate=cfg.sample_rate_hz)
        except TakeError as exc:
            raise RuntimeError(f"transcribe_audio: {exc}") from exc
    fixture_text = _transcript_fixture(ctx)
    input_hash = _hash_obj(
        {
            "audio": file_sha256(normalised),
            "engine": engine,
            "model": model,
            "language": language,
            "fixture": sha256_hex(fixture_text.encode()) if fixture_text else "",
            "version": TRANSCRIBE_VERSION,
        }
    )
    marker = audio_dir / "transcript.done.json"
    out_path = audio_dir / "transcript.json"
    cached = (
        marker.exists()
        and out_path.exists()
        and json.loads(marker.read_text()).get("input_hash") == input_hash
    )
    if cached:
        transcript = SpeechTranscript.model_validate_json(out_path.read_text())
    else:
        try:
            transcript = transcribe(
                normalised,
                engine=engine,
                model=model,
                compute_type=cfg.faster_whisper_compute_type,
                timeout_s=cfg.timeout_s,
                language=language,
                fixture_text=fixture_text,
            )
        except TakeError as exc:
            raise RuntimeError(f"transcribe_audio: {exc}") from exc
        _write(out_path, transcript.model_dump_json(indent=1))
        _write(audio_dir / "transcript.txt", transcript.text + "\n")
        _write(marker, json.dumps({"input_hash": input_hash}, indent=1, sort_keys=True))
    return StageOutput(
        transcript.content_hash(),
        {
            "engine": transcript.engine,
            "words": len(transcript.words),
            "sentences": len(sentences(transcript)),
            "seconds": round(transcript.duration_ms / 1000, 1),
            "timings": transcript.timing_source.value,
            "source": source.name,
            # Empty unless the recording came out of a video container, where the run log has to
            # carry what was measured to decide that.
            **({"picture": picture} if picture else {}),
            "cache_hit": cached,
            # The first words, so a run log says which recording this was without opening a file.
            "opening": " ".join(transcript.text.split()[:12]),
        },
    )


def _transcript_fixture(ctx: StageContext) -> str:
    """The operator's own transcript, for the ``fixture`` engine: the widget, or a file it names."""
    raw = _param(ctx, "transcript").strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    for path in (candidate, ctx.project_dir / candidate, REPO_ROOT / candidate):
        if len(raw) < 400 and path.is_file():
            return path.read_text()
    return raw


def stage_lock_script(ctx: StageContext) -> StageOutput:
    """The sentences this deliverable speaks, frozen."""
    plan = _load_story_plan(ctx)
    # The read's locale, so a lexicon entry for another language is not applied to this script.
    lexicon = story_lexicon(ctx, get_settings().narration.locale)
    locked = {
        "deliverable_id": ctx.deliverable_id,
        "sentences": [b.display_text for b in plan.beats],
        # What is SAID, not always what is shown ("21%" vs "twenty-one per cent"): alignment and
        # caption timings are measured against these, so locking only the display text was wrong.
        "spoken": [spoken_line(b, lexicon) for b in plan.beats],
        "plan_hash": plan.content_hash(),
    }
    _write(ctx.ddir() / "script" / "locked.json", json.dumps(locked, indent=1))
    path = _story_plan_path(ctx)
    # The claim gate is here, not in verify_claims, because THIS is where a script exists:
    # locking it is the last moment before words are spoken and pictures drawn from them.
    gate = _script_claim_gate(ctx, plan)
    _write(
        ctx.ddir() / "script" / "claim-gate.json",
        json.dumps(gate, indent=1, sort_keys=True, default=str),
    )
    if not gate["passed"]:
        raise RuntimeError(
            "lock_script refused the script: "
            + "; ".join(f["message"] for f in gate["findings"])[:600]
        )
    return StageOutput(
        _hash_obj(locked),
        {
            "sentences": len(locked["sentences"]),
            # Which plan was locked, because a short silently narrating the long plan is exactly
            # the defect this resolves and it is invisible in a sentence count alone.
            "plan": str(path.relative_to(ctx.project_dir)) if path else "fixture",
            "lexicon_terms": len(lexicon),
            "claims_checked": gate["referenced"],
        },
    )


def _script_claim_gate(ctx: StageContext, plan: StoryPlan) -> dict:
    """The script's own claim gate, against the claims this run verified."""
    from content_factory.schemas.research import ClaimRecord

    claims_path = ctx.project_dir / "research" / "claims.json"
    referenced = sum(len(b.claim_ids) for b in plan.beats)
    if not claims_path.exists():
        return {
            "passed": True,
            "referenced": referenced,
            "findings": [],
            "note": "no research/claims.json: nothing to check the script against",
        }
    claims = [ClaimRecord.model_validate(r) for r in json.loads(claims_path.read_text())]
    gate = script_claim_gate(plan, claims)
    return {
        "passed": gate.passed,
        "referenced": referenced,
        "claims": len(claims),
        "findings": [
            {"check": f.check, "severity": f.severity.value, "message": f.message}
            for f in gate.findings
        ],
    }


LEXICON_FILENAME = "lexicon.json"


def story_lexicon(ctx: StageContext, locale: str = "") -> tuple[PronunciationEntry, ...]:
    """The film's own pronunciation list, from ``<project>/story/lexicon.json``."""
    path = ctx.project_dir / "story" / LEXICON_FILENAME
    if not path.exists():
        return ()
    raw = json.loads(path.read_text())
    entries = tuple(PronunciationEntry.model_validate(e) for e in raw)
    if not locale:
        return entries
    want = locale.split("-", 1)[0].lower()
    return tuple(e for e in entries if e.locale.split("-", 1)[0].lower() == want)


def spoken_line(beat, lexicon: tuple[PronunciationEntry, ...] = ()) -> str:
    """What is actually said for one beat."""
    written = (getattr(beat, "spoken_text", None) or "").strip()
    return normalize_for_speech(written or beat.display_text, lexicon)


def _qwen_revision(cloning: bool, designing: bool) -> str:
    """Which Qwen3-TTS weights a mode uses; only CustomVoice has built-in timbres."""
    if cloning:
        return "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    if designing:
        return "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
    return "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"


def _tts_executor(ctx: StageContext | None = None) -> tuple[TTSExecutor, VoiceIdentity]:
    """``narration.tts`` selects the voice: the deterministic mock, Qwen3-TTS, or Kokoro."""
    cfg = get_settings().narration

    def param(key: str, default: str) -> str:
        return _param(ctx, key, default) if ctx is not None else default

    backend = param("voice", cfg.tts)
    # The canvas labels Kokoro by its model name; the setting spells it by skill.
    if backend == "kokoro-82m":
        backend = "kokoro"
    # Before any weights load or the GPU is claimed: a locale the voice cannot speak is a refusal
    # by name. Kokoro used to map every non-English locale to British English without failing.
    language = check_narration_language(
        backend, cfg.locale, language=param("language", cfg.qwen_language)
    )
    if backend == "qwen3tts":
        from content_factory.audio.tts import Qwen3TTS

        ref_audio: Path | None = None
        if cfg.qwen_ref_audio:
            ref = Path(cfg.qwen_ref_audio)
            ref_audio = ref if ref.is_absolute() else REPO_ROOT / ref
            if not ref_audio.is_file():
                raise RuntimeError(f"narration.qwen_ref_audio does not exist: {ref_audio}")
            if not cfg.qwen_ref_text:
                raise RuntimeError(
                    "narration.qwen_ref_audio needs narration.qwen_ref_text: the Base weights"
                    " clone a voice from a clip AND its transcript"
                )
        describe = param("describe", cfg.qwen_describe)
        if describe and ref_audio:
            raise RuntimeError(
                "narration: qwen_describe designs a voice and qwen_ref_audio clones one;"
                " they are different weights, so set one or the other"
            )
        voice = VoiceIdentity(
            provider="qwen3tts",
            # A cloned voice is identified by the clip it was cloned from, not by a timbre name.
            # A designed voice has no timbre name, so the id records that it was described.
            voice_id=(
                ref_audio.name[:80]
                if ref_audio
                else ("designed" if describe else param("speaker", cfg.qwen_speaker))
            ),
            model_revision=_qwen_revision(bool(ref_audio), bool(describe)),
            speed=float(param("speed", str(cfg.speed))),
            locale=cfg.locale,
        )
        executor = Qwen3TTS(
            REPO_ROOT / "skills" / "audio" / "qwen3tts",
            device=cfg.qwen_device,
            # The validated word, not the raw setting: `auto` resolves to the locale's language
            # so the model is never left to guess at what the run already declared.
            language=language.qwen_language,
            instruct=param("instruct", cfg.qwen_instruct),
            describe=describe,
            ref_audio=ref_audio,
            ref_text=cfg.qwen_ref_text,
            aligner=param("aligner", cfg.aligner),
            # The aligner checkpoint for *this* language: `base.en` cannot transcribe German and
            # does not say so; see `audio.languages.aligner_model_for`.
            faster_whisper_model=aligner_model_for(
                cfg.locale,
                cfg.faster_whisper_model,
                configured="faster_whisper_model" in cfg.model_fields_set,
            ),
            compute_type=cfg.faster_whisper_compute_type,
            timeout_s=cfg.tts_timeout_s,
            aligner_timeout_s=cfg.aligner_timeout_s,
            script_similarity_min=cfg.script_similarity_min,
            seed=cfg.qwen_seed,
            takes=cfg.tts_takes,
        )
        return executor, voice
    if backend == "kokoro":
        from content_factory.audio.tts import KokoroTTS

        voice = VoiceIdentity(
            provider="kokoro",
            voice_id=param("speaker", cfg.kokoro_voice),
            model_revision="hexgrad/Kokoro-82M",
            speed=float(param("speed", str(cfg.speed))),
            locale=cfg.locale,
        )
        executor = KokoroTTS(
            REPO_ROOT / "skills" / "audio" / "kokoro",
            device=cfg.kokoro_device,
            lang_code=language.kokoro_lang_code,
        )
        return executor, voice
    # The mock reads ``request.voice.speed`` too, so a speed change re-times the offline demo the
    # same way it re-times a real take. Every executor here honours it; it is not a label.
    speed = float(param("speed", str(cfg.speed)))
    return MockTTS(), demo_fixtures.MOCK_VOICE.model_copy(update={"speed": speed})


def _narrate_continuously(
    ctx: StageContext,
    tts,
    voice,
    lexicon,
    requests: list[NarrationRequest],
    audio_dir: Path,
    group_hash: str,
    *,
    freed: bool,
) -> StageOutput:
    """Speak every beat as one take, then write the per-beat wavs and segments out of it."""
    from content_factory.audio.continuity import ContinuityError, cut_take, joined_text, segment_for

    marker = audio_dir / "narration.take.json"
    cached = json.loads(marker.read_text()) if marker.exists() else {}
    have_all = cached.get("input_hash") == group_hash and all(
        (audio_dir / f"{r.beat_id}.wav").exists()
        and (audio_dir / f"{r.beat_id}.segment.json").exists()
        for r in requests
    )
    if have_all:
        from content_factory.schemas.audio import NarrationSegment

        sources = {
            NarrationSegment.model_validate_json(
                (audio_dir / f"{r.beat_id}.segment.json").read_text()
            ).timing_source.value
            for r in requests
        }
        return StageOutput(
            _hash_obj(cached["audio_sha256s"]),
            {
                "segments": len(requests),
                "provider": tts.provider,
                "spoken": 0,
                "locale": voice.locale,
                "lexicon_terms": len(lexicon),
                "take": "cached",
                "timing_source": sorted(sources),
            },
        )
    if not freed and tts.provider != "mock":
        _free_the_gpu(ctx, "synthesize_narration")
    paragraph = joined_text(requests)
    take_request = NarrationRequest(
        beat_id=requests[0].beat_id,
        display_text=paragraph,
        spoken_text=paragraph,
        voice=voice,
        lexicon=lexicon,
    )
    result = tts.synthesize(take_request)
    try:
        cuts = cut_take(
            requests,
            result.audio,
            list(result.segment.words),
            result.segment.duration_ms,
            match=get_settings().narration.match_beat_levels,
        )
    except ContinuityError as exc:
        # Not a fallback to beat-at-a-time: that is the behaviour this stage exists to avoid, and
        # silently taking it would hide a script/transcript mismatch the operator needs to see.
        msg = f"synthesize_narration: {exc}"
        raise RuntimeError(msg) from exc
    hashes: list[str] = []
    for request, cut in zip(requests, cuts, strict=True):
        segment = segment_for(cut, request, result.segment)
        (audio_dir / f"{request.beat_id}.wav").write_bytes(cut.audio)
        _write(audio_dir / f"{request.beat_id}.segment.json", segment.model_dump_json(indent=1))
        hashes.append(segment.audio_sha256)
    (audio_dir / "narration.take.wav").write_bytes(result.audio)
    _write(
        marker,
        json.dumps(
            {
                "input_hash": group_hash,
                "audio_sha256s": hashes,
                "take_duration_ms": result.segment.duration_ms,
                "beats": [r.beat_id for r in requests],
                "spans_ms": [list(c.take_span_ms) for c in cuts],
            },
            indent=1,
            sort_keys=True,
        ),
    )
    check = getattr(tts, "last_script_check", "")
    return StageOutput(
        _hash_obj(hashes),
        {
            "segments": len(hashes),
            "provider": tts.provider,
            "spoken": len(hashes),
            "locale": voice.locale,
            "lexicon_terms": len(lexicon),
            "take": "continuous",
            "take_duration_ms": result.segment.duration_ms,
            "script_checks": [f"take: {check}"] if check else [],
            "timing_source": [result.segment.timing_source.value],
        },
    )


def stage_synthesize_narration(ctx: StageContext) -> StageOutput:
    """One wav + NarrationSegment per beat, spoken as ONE take and cut at the pauses."""
    plan = _load_story_plan(ctx)
    tts, voice = _tts_executor(ctx)
    lexicon = story_lexicon(ctx, voice.locale)
    audio_dir = ctx.ddir() / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    seg_hashes = []
    timing_sources: set[str] = set()
    script_checks: list[str] = []
    spoken = 0
    # Qwen3-TTS loads in its own uv environment, so the managed servers are evicted first (it met
    # HiDream's 18.57 GB resident and died on 20 MiB). Only uncached, and only for a real provider.
    freed = tts.provider == "mock"
    requests = [
        NarrationRequest(
            beat_id=b.beat_id,
            display_text=b.display_text,
            spoken_text=spoken_line(b, lexicon),
            voice=voice,
            # The contract field that existed and was never filled. In the request because it is
            # in the cache key below: adding a respelling has to re-speak the beats it changes.
            lexicon=lexicon,
        )
        for b in plan.beats
    ]
    continuous = _param_bool(ctx, "continuous_take", get_settings().narration.continuous_take)
    # One hash over the WHOLE script when the beats share a take: a cut depends on its neighbours'
    # word times, so a per-beat key would serve a stale slice of a take that no longer exists.
    group_hash = _hash_obj(
        {
            "provider": tts.provider,
            "requests": [r.model_dump(mode="json") for r in requests],
            "executor": tts.fingerprint(),
            "continuous": continuous,
        }
    )
    if continuous and len(requests) > 1:
        return _narrate_continuously(
            ctx, tts, voice, lexicon, requests, audio_dir, group_hash, freed=freed
        )
    for b, request in zip(plan.beats, requests, strict=True):
        input_hash = _hash_obj(
            {
                "provider": tts.provider,
                "request": request.model_dump(mode="json"),
                "executor": tts.fingerprint(),
            }
        )
        marker = audio_dir / f"{b.beat_id}.tts.json"
        wav = audio_dir / f"{b.beat_id}.wav"
        seg_path = audio_dir / f"{b.beat_id}.segment.json"
        if (
            marker.exists()
            and wav.exists()
            and seg_path.exists()
            and json.loads(marker.read_text()).get("input_hash") == input_hash
        ):
            seg_hashes.append(json.loads(marker.read_text())["audio_sha256"])
            continue
        if not freed:
            _free_the_gpu(ctx, "synthesize_narration")
            freed = True
        r = tts.synthesize(request)
        wav.write_bytes(r.audio)
        _write(seg_path, r.segment.model_dump_json(indent=1))
        _write(
            marker,
            json.dumps(
                {"input_hash": input_hash, "audio_sha256": r.segment.audio_sha256},
                indent=1,
                sort_keys=True,
            ),
        )
        seg_hashes.append(r.segment.audio_sha256)
        timing_sources.add(r.segment.timing_source.value)
        # How the script check went, per beat. A respelled beat cannot be checked by transcript
        # similarity (see Qwen3TTS._time_words), and a skipped check has to be visible.
        check = getattr(tts, "last_script_check", "")
        if check:
            script_checks.append(f"{b.beat_id}: {check}")
        spoken += 1
    if not timing_sources:  # everything came from cache; read it back rather than guess
        from content_factory.schemas.audio import NarrationSegment

        for b in plan.beats:
            seg_path = audio_dir / f"{b.beat_id}.segment.json"
            if seg_path.exists():
                timing_sources.add(
                    NarrationSegment.model_validate_json(seg_path.read_text()).timing_source.value
                )
    return StageOutput(
        _hash_obj(seg_hashes),
        {
            "segments": len(seg_hashes),
            "provider": tts.provider,
            "spoken": spoken,
            "locale": voice.locale,
            "lexicon_terms": len(lexicon),
            "script_checks": script_checks,
            "script_checks_skipped": sum(1 for c in script_checks if "skipped" in c),
            # Where the word timings came from: the model, forced alignment, or an estimate.
            # It decides how much the captions can be trusted, so it belongs in the run record.
            "timing_source": sorted(timing_sources),
        },
    )


def _restoration_spec(ctx: StageContext) -> SpeechRestorationSpec:
    cfg = get_settings().speech_restoration
    return SpeechRestorationSpec(
        cleanup=_param(ctx, "cleanup", cfg.cleanup),  # type: ignore[arg-type]
        band_extension=_param(ctx, "band_extension", cfg.band_extension),  # type: ignore[arg-type]
        enhancer=_param(ctx, "enhancer", cfg.enhancer),  # type: ignore[arg-type]
        enhancer_mode=_param(ctx, "enhancer_mode", cfg.enhancer_mode),  # type: ignore[arg-type]
        enhancer_nfe=_param_int(ctx, "enhancer_nfe", cfg.enhancer_nfe),
        enhancer_solver=cfg.enhancer_solver,
        enhancer_lambd=cfg.enhancer_lambd,
        enhancer_tau=cfg.enhancer_tau,
        gate=_param(ctx, "gate", cfg.gate),  # type: ignore[arg-type]
        device=_param(ctx, "device", cfg.device),  # type: ignore[arg-type]
        sample_rate_hz=cfg.sample_rate_hz,
    )


def stage_restore_speech(ctx: StageContext) -> StageOutput:
    """The voice chain per beat; each beat comes back at exactly the length it went in."""
    from content_factory.audio.restore import (
        CHAIN_VERSION,
        RestorationError,
        SkillDirs,
        restore_beat,
    )
    from content_factory.schemas.audio import NarrationSegment, SpeechRestorationReport

    cfg = get_settings().speech_restoration
    audio_dir = ctx.ddir() / "audio"
    if not cfg.enabled:
        # Switching the chain off does not undo an earlier run's work — the restored files are
        # still what the mix reads. Say so rather than letting it look like raw takes went out.
        stale = sorted(p.name for p in audio_dir.glob("*.restored.wav"))
        return StageOutput(
            _hash_obj({"enabled": False}),
            {
                "beats": 0,
                "skipped": "speech_restoration.enabled=0",
                "already_restored": stale,
            },
        )
    plan = _load_story_plan(ctx)
    spec = _restoration_spec(ctx)
    skills = SkillDirs(
        clearervoice=REPO_ROOT / "skills" / "audio" / "clearervoice",
        resemble_enhance=REPO_ROOT / "skills" / "audio" / "resemble_enhance",
    )
    reports: list[SpeechRestorationReport] = []
    restored_count = 0
    # Resemble Enhance and ClearerVoice load inside their own uv environments, so nothing here goes
    # through _ensure_backend_ready; the GPU is freed once, lazily, on the first beat restored.
    freed = False
    for beat in plan.beats:
        raw = audio_dir / f"{beat.beat_id}.wav"
        if not raw.is_file():
            raise RuntimeError(
                f"restore_speech: {beat.beat_id} has no take at {raw} —"
                " synthesize_narration or voice_over has to run first"
            )
        out_wav = audio_dir / f"{beat.beat_id}.restored.wav"
        marker = audio_dir / f"{beat.beat_id}.restoration.json"
        input_hash = _hash_obj(
            {
                "take": file_sha256(raw),
                "spec": spec.model_dump(mode="json"),
                "chain_version": CHAIN_VERSION,
            }
        )
        cached = None
        if marker.exists() and out_wav.exists():
            payload = json.loads(marker.read_text())
            if payload.get("input_hash") == input_hash and file_sha256(out_wav) == payload.get(
                "report", {}
            ).get("output_sha256"):
                cached = SpeechRestorationReport.model_validate(payload["report"])
        if cached is None:
            # Only when a step in the chain actually loads a model. With cleanup, band extension
            # and the enhancer all off the chain is FFmpeg filters, which need no card.
            loads_a_model = any(
                step != "off" for step in (spec.cleanup, spec.band_extension, spec.enhancer)
            )
            if not freed and loads_a_model:
                _free_the_gpu(ctx, "restore_speech")
                freed = True
            try:
                report = restore_beat(
                    raw,
                    out_wav,
                    beat_id=beat.beat_id,
                    spec=spec,
                    skills=skills,
                    workdir=audio_dir / "restore-work",
                    timeout_s=cfg.timeout_s,
                )
            except RestorationError as exc:
                raise RuntimeError(f"restore_speech: {exc}") from exc
            _write(
                marker,
                json.dumps(
                    {"input_hash": input_hash, "report": report.model_dump(mode="json")},
                    indent=1,
                    sort_keys=True,
                ),
            )
            restored_count += 1
        else:
            report = cached
        reports.append(report)
        # The segment record now describes the restored bytes. Duration and word timings are
        # unchanged by construction, so nothing downstream has to be recompiled.
        seg_path = audio_dir / f"{beat.beat_id}.segment.json"
        seg = NarrationSegment.model_validate_json(seg_path.read_text())
        if (
            seg.audio_sha256 != report.output_sha256
            or seg.sample_rate_hz != report.output_sample_rate_hz
        ):
            _write(
                seg_path,
                seg.model_copy(
                    update={
                        "audio_sha256": report.output_sha256,
                        "sample_rate_hz": report.output_sample_rate_hz,
                    }
                ).model_dump_json(indent=1),
            )
    _write(
        audio_dir / "restoration.json",
        json.dumps([r.model_dump(mode="json") for r in reports], indent=1),
    )
    # Every step that ran on any beat, in chain order. Beats can differ: the cleanup and band
    # extension gates look at each take's own measurements.
    order = [s for r in reports for s in r.steps]
    steps = sorted(set(order), key=order.index)
    return StageOutput(
        _hash_obj([r.output_sha256 for r in reports]),
        {
            "beats": len(reports),
            "restored": restored_count,
            "steps": steps,
            "band_limited_before": sum(1 for r in reports if r.before.needs_band_extension),
            "band_limited_after": sum(1 for r in reports if r.after.needs_band_extension),
        },
    )


def _takes_dir(ctx: StageContext) -> Path:
    configured = _param(ctx, "takes_dir", get_settings().voice_over.takes_dir)
    path = Path(configured)
    return path if path.is_absolute() else ctx.project_dir / path


def stage_voice_over(ctx: StageContext) -> StageOutput:
    """Human takes instead of a synthesized voice, as the same NarrationSegment per beat."""
    from content_factory.audio.takes import (
        TakeError,
        discover_takes,
        segment_for_take,
        to_wav_mono16k,
    )

    cfg = get_settings().voice_over
    aligner = _param(ctx, "aligner", cfg.aligner)
    plan = _load_story_plan(ctx)
    takes_dir = _takes_dir(ctx)
    audio_dir = ctx.ddir() / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    if _param(ctx, "source", "takes") == "recording":
        return _voice_over_from_recording(ctx, plan)
    try:
        takes = discover_takes(
            takes_dir,
            [b.beat_id for b in plan.beats],
            also=[ctx.project_dir / "uploads"],
        )
    except TakeError as exc:
        raise RuntimeError(f"voice_over: {exc}") from exc

    seg_hashes: list[str] = []
    verdicts: list[str] = []
    aligned = 0
    speakers: set[str] = set()
    # The same lexicon the script was locked with: a respelling applied on one side and not the
    # other reads as a performer who said the wrong words.
    lexicon = story_lexicon(ctx, get_settings().narration.locale)
    for beat in plan.beats:
        take = takes[beat.beat_id]
        spoken = spoken_line(beat, lexicon)
        input_hash = _hash_obj(
            {
                "take": file_sha256(take.path),
                "spoken": spoken,
                "aligner": aligner,
                "model": cfg.faster_whisper_model,
            }
        )
        marker = audio_dir / f"{beat.beat_id}.take.json"
        wav = audio_dir / f"{beat.beat_id}.wav"
        seg_path = audio_dir / f"{beat.beat_id}.segment.json"
        speakers.add(take.speaker)
        if (
            marker.exists()
            and wav.exists()
            and seg_path.exists()
            and json.loads(marker.read_text()).get("input_hash") == input_hash
        ):
            seg_hashes.append(json.loads(marker.read_text())["audio_sha256"])
            continue
        to_wav_mono16k(take.path, wav)
        try:
            segment, verdict = segment_for_take(
                take,
                wav,
                display_text=beat.display_text,
                spoken_text=spoken,
                aligner=aligner,
                faster_whisper_model=cfg.faster_whisper_model,
                compute_type=cfg.faster_whisper_compute_type,
                timeout_s=cfg.aligner_timeout_s,
                script_similarity_min=cfg.script_similarity_min,
                normalization_version=NORMALIZATION_VERSION,
            )
        except TakeError as exc:
            raise RuntimeError(f"voice_over: {exc}") from exc
        verdicts.append(verdict)
        _write(seg_path, segment.model_dump_json(indent=1))
        _write(
            marker,
            json.dumps(
                {
                    "input_hash": input_hash,
                    "audio_sha256": segment.audio_sha256,
                    "speaker": take.speaker,
                    "source": take.path.name,
                    "verdict": verdict,
                },
                indent=1,
                sort_keys=True,
            ),
        )
        seg_hashes.append(segment.audio_sha256)
        aligned += 1
    return StageOutput(
        _hash_obj(seg_hashes),
        {
            "segments": len(seg_hashes),
            "aligned": aligned,
            "aligner": aligner,
            # An operator needs to see "accepted as performed" without opening every take.
            "improvised": sum(1 for v in verdicts if v == "offer_accept_as_performed"),
            "speakers": sorted(speakers),
            "takes_dir": str(takes_dir.relative_to(ctx.project_dir))
            if takes_dir.is_relative_to(ctx.project_dir)
            else str(takes_dir),
        },
    )


def _voice_over_from_recording(ctx: StageContext, plan: StoryPlan) -> StageOutput:
    """One continuous recording as the narration, cut into the plan's beats."""
    from content_factory.audio.takes import TakeError
    from content_factory.audio.transcribe import beat_spans, cut_beat, segment_from_transcript
    from content_factory.schemas.audio import SpeechTranscript

    audio_dir = ctx.ddir() / "audio"
    transcript_path = audio_dir / "transcript.json"
    source = audio_dir / "source.wav"
    if not transcript_path.exists() or not source.exists():
        msg = (
            "voice_over source=recording needs the transcript and the normalised recording"
            f" ({transcript_path.name}, {source.name}): run transcribe_audio first"
        )
        raise RuntimeError(msg)
    transcript = SpeechTranscript.model_validate_json(transcript_path.read_text())
    try:
        spans = beat_spans(transcript, plan)
    except TakeError as exc:
        raise RuntimeError(f"voice_over: {exc}") from exc
    source_sha = file_sha256(source)
    seg_hashes: list[str] = []
    cut = 0
    for beat in sorted(plan.beats, key=lambda b: b.order):
        start_ms, end_ms = spans[beat.beat_id]
        wav = audio_dir / f"{beat.beat_id}.wav"
        seg_path = audio_dir / f"{beat.beat_id}.segment.json"
        marker = audio_dir / f"{beat.beat_id}.take.json"
        input_hash = _hash_obj(
            {
                "recording": source_sha,
                "transcript": transcript.transcript_id,
                "span": [start_ms, end_ms],
                "display": beat.display_text,
            }
        )
        if (
            marker.exists()
            and wav.exists()
            and seg_path.exists()
            and json.loads(marker.read_text()).get("input_hash") == input_hash
        ):
            seg_hashes.append(json.loads(marker.read_text())["audio_sha256"])
            continue
        try:
            cut_beat(source, wav, start_ms=start_ms, end_ms=end_ms)
            segment = segment_from_transcript(
                transcript,
                wav,
                beat_id=beat.beat_id,
                display_text=beat.display_text,
                span=(start_ms, end_ms),
            )
        except TakeError as exc:
            raise RuntimeError(f"voice_over: {beat.beat_id}: {exc}") from exc
        _write(seg_path, segment.model_dump_json(indent=1))
        _write(
            marker,
            json.dumps(
                {
                    "input_hash": input_hash,
                    "audio_sha256": segment.audio_sha256,
                    "speaker": "recording",
                    "source": source.name,
                    "span_ms": [start_ms, end_ms],
                    "verdict": "accepted_as_recorded",
                },
                indent=1,
                sort_keys=True,
            ),
        )
        seg_hashes.append(segment.audio_sha256)
        cut += 1
    return StageOutput(
        _hash_obj(seg_hashes),
        {
            "segments": len(seg_hashes),
            "cut": cut,
            "source": "recording",
            "timings": transcript.timing_source.value,
            "spoken_seconds": round(
                sum(end - start for start, end in spans.values()) / 1000,
                1,
            ),
            # How much of the recording no beat owns: pauses plus anything the story dropped. A big
            # number says the beat count is wrong or the plan is not this recording's.
            "unused_seconds": round(
                max(0, transcript.duration_ms - sum(e - s for s, e in spans.values())) / 1000, 1
            ),
        },
    )


SEQUENCE_PREVIEW_FPS = 8
"""The rate a keyframe flipbook is previewed and cut at. One constant, because package_sequence's
preview, stage_interpolate's ``sequence`` case and _silent_picture all have to agree."""


def _final_audio(ctx: StageContext) -> tuple[Path | None, str]:
    """The audio track for the finished cut, and what it is: speech, a bed, or nothing."""
    audio_dir = ctx.ddir() / "audio"
    mastered = audio_dir / "narration-mastered.wav"
    if mastered.exists():
        # Whether it is speech decides whether the speech QC applies below.
        spoken = any(audio_dir.glob("*.segment.json"))
        return mastered, "narration" if spoken else "bed"
    sfx = audio_dir / "sfx.wav"
    if sfx.exists():
        return sfx, "sfx"
    selection = audio_dir / "music-selection.json"
    if selection.exists():
        chosen = json.loads(selection.read_text())
        if chosen.get("filename"):
            track = _music_library_dir() / chosen["filename"]
            if track.exists():
                return track, "music"
    return None, "none"


def _speech_end_ms(ctx: StageContext) -> int:
    """Where the last word ends in the laid-out narration: the length a film has to reach."""
    _plan, segs, _files = _load_segments(ctx)
    laid = lay_out(segs, AudioMixSpec(deliverable_id=ctx.deliverable_id))  # type: ignore[arg-type]
    return max((w.end_ms for b in laid for w in b.words), default=0)


def _planned_beat_seconds(ctx: StageContext, count: int) -> list[float]:
    """How long each of ``count`` pictures is on screen, from the story's beats."""
    path = _story_plan_path(ctx)
    if path is None:
        return []
    beats = StoryPlan.model_validate_json(path.read_text()).beats
    if len(beats) != count or not all(b.planned_duration_ms for b in beats):
        return []
    return [round((b.planned_duration_ms or 0) / 1000, 3) for b in beats]


def _hold_frames(pngs: list[Path], target: Path, seconds: list[float], fps: int = 30) -> None:
    """Cut stills together, each held for its own length, with no blending across the joins."""
    from content_factory.audio.mix import ffmpeg

    lines = [f"file '{p.resolve()}'\nduration {s}\n" for p, s in zip(pngs, seconds, strict=True)]
    # The concat demuxer ignores the last entry's duration, so the final image is repeated.
    lines.append(f"file '{pngs[-1].resolve()}'\n")
    listing = target.with_suffix(".concat.txt")
    listing.write_text("".join(lines))
    size = _png_size(pngs[0].read_bytes())
    width = int(size.get("png_width") or 1920)
    height = int(size.get("png_height") or 1080)
    width, height = width - (width % 2), height - (height % 2)
    ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-vf",
            f"scale={width}:{height},fps={fps},format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            str(target),
        ],
        timeout=1800,
    )


POST_CHAIN_EXPORT = "postchain.mp4"
"""What `interpolate` concatenates its finished clips into.

It used to be `final.mp4` — the same path `compose_video` writes the delivered film to. On a lane
that composes, the post chain wrote it first and the cut overwrote it later, so a run that died in
between left a file at the deliverable's name that was not one: `silent-video` failed at
`sound_design`, three stages before the cut, and `exports/final.mp4` was sitting there — thirty
seconds of silent, uncaptioned, unmastered footage that anything reading the well-known path would
take for the film (measured 2026-09-10; this audit did exactly that). `final.mp4` now means the
finished cut and nothing else. A lane that ends at the post chain still delivers, because this
name is a delivery candidate in its own right.
"""


def _silent_picture(ctx: StageContext) -> Path:
    """The silent cut: whatever the video branch has produced, most-finished first."""
    exports = ctx.ddir() / "exports"
    for name in ("picture.mp4", POST_CHAIN_EXPORT, "generated.mp4"):
        candidate = exports / name
        if candidate.exists():
            return candidate
    renders = sorted(exports.glob("bnd_*.mp4"))
    if renders:
        return renders[-1]
    # An image-sequence lane has approved PNG frames and no video: no generate_video and no
    # interpolate, on purpose. So the frames ARE the picture, and cutting them is done here.
    frames = ctx.ddir() / "sequence" / "frames"
    pngs = sorted(frames.glob("[0-9]*.png"))
    if pngs:
        target = exports / "generated.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Held for the beats' own lengths when the story gives them; cut at the flipbook rate, a
        # five-beat story planned at eighteen seconds came out as a 0.6-second film.
        held = _planned_beat_seconds(ctx, len(pngs))
        if held:
            _hold_frames(pngs, target, held)
        else:
            from content_factory.postchain import mux_frames

            mux_frames(frames, target, fps=SEQUENCE_PREVIEW_FPS)
        return target
    raise RuntimeError(
        "no picture yet — run the video branch (render_scenes / generate_video / interpolate) "
        "or the image-sequence branch (generate_keyframes) before this stage"
    )


def stage_sound_design(ctx: StageContext) -> StageOutput:
    """Video-synced SFX for the silent cut, written as a bed the mix folds under the narration."""
    from content_factory.audio.sfx import SfxError, SfxRequest, backend_for
    from content_factory.qc.media import ffprobe

    cfg = get_settings().sound_design
    backend = backend_for(_param(ctx, "backend", cfg.backend))
    picture = _silent_picture(ctx)
    duration_s = float(ffprobe(picture)["format"].get("duration") or 0)
    if duration_s <= 0:
        raise RuntimeError(f"sound_design: {picture.name} reports no duration")
    request = SfxRequest(
        video=picture,
        duration_s=duration_s,
        prompt=_param(ctx, "prompt", cfg.prompt),
        negative_prompt=_param(ctx, "negative_prompt", cfg.negative_prompt),
        seed=_param_int(ctx, "seed", cfg.seed),
        steps=_param_int(ctx, "steps", cfg.steps),
        window_s=cfg.window_s,
        timeout_s=cfg.timeout_s,
    )
    gain_db = _param_float(ctx, "gain_db", cfg.gain_db)
    out_wav = ctx.ddir() / "audio" / "sfx.wav"
    marker = ctx.ddir() / "audio" / "sfx.json"
    input_hash = _hash_obj(
        {
            "picture": file_sha256(picture),
            "prompt": request.prompt,
            "negative": request.negative_prompt,
            "seed": request.seed,
            "steps": request.steps,
            "backend": backend.name,
            "condition": cfg.condition,
            "bed_lufs": cfg.bed_lufs if cfg.condition else None,
            "bed_true_peak_dbtp": cfg.bed_true_peak_dbtp if cfg.condition else None,
        }
    )
    if (
        marker.exists()
        and out_wav.exists()
        and json.loads(marker.read_text()).get("input_hash") == input_hash
    ):
        record = json.loads(marker.read_text())
        return StageOutput(record["sfx_sha256"], {**record["facts"], "cache_hit": True})
    # The generator's own output is kept and never overwritten, same discipline as restore_speech:
    # a rerun always starts from what the model actually produced.
    raw_wav = ctx.ddir() / "audio" / "sfx.raw.wav" if cfg.condition else out_wav
    # MMAudio and Stable Audio load in their own skill environment, so the image and video tenants
    # come off the card first — past the cache check, and never for the mock, which loads no model.
    if backend.name != "mock":
        _free_the_gpu(ctx, "sound_design")
    import time as _time

    before = gpu_memory_used_mib()
    started = _time.monotonic()
    try:
        facts = backend.generate(request, raw_wav)
    except SfxError as exc:
        raise RuntimeError(f"sound_design: {exc}") from exc
    sfx_telemetry = _generation_telemetry(
        before=before, seconds=_time.monotonic() - started, backend=backend
    )
    _log_execution(ctx, f"sound_design:{backend.name}", sfx_telemetry)
    condition_report = None
    if cfg.condition:
        from content_factory.audio.condition import ConditionError, condition_sound
        from content_factory.schemas.audio import AudioProfile, SoundConditionSpec

        try:
            condition_report = condition_sound(
                raw_wav,
                out_wav,
                asset_id="sfx",
                spec=SoundConditionSpec(
                    profile=AudioProfile.sound_effect,
                    # A bed runs under a whole scene, so integrated loudness is the metric that
                    # describes it; a one-shot would want max_momentary instead.
                    loudness_metric="integrated",
                    target_lufs=cfg.bed_lufs,
                    target_true_peak_dbtp=cfg.bed_true_peak_dbtp,
                ),
                workdir=ctx.ddir() / "audio" / "sfx-work",
            )
        except ConditionError as exc:
            raise RuntimeError(f"sound_design: {exc}") from exc
        # The level now comes from the normalisation target, not from a blind cut at mix time.
        gain_db = _param_float(ctx, "gain_db", cfg.bed_trim_db)
        facts["conditioned"] = list(condition_report.steps)
        facts["bed_lufs"] = condition_report.measured_lufs
        facts["bed_true_peak_dbtp"] = condition_report.measured_true_peak_dbtp
        facts["condition_gain_db"] = condition_report.gain_applied_db
        _write(
            ctx.ddir() / "audio" / "sfx-condition.json",
            condition_report.model_dump_json(indent=1),
        )
    facts["gain_db"] = gain_db
    record = {
        "input_hash": input_hash,
        "sfx_sha256": file_sha256(out_wav),
        "gain_db": gain_db,
        "scored": picture.name,
        "facts": facts,
    }
    _write(marker, json.dumps(record, indent=1, sort_keys=True))
    return StageOutput(record["sfx_sha256"], {**facts, "scored": picture.name, "cache_hit": False})


def _load_segments(ctx: StageContext):
    from content_factory.schemas.audio import NarrationSegment

    plan = _load_story_plan(ctx)
    segs, files = [], {}
    for b in plan.beats:
        segs.append(
            NarrationSegment.model_validate_json(
                (ctx.ddir() / "audio" / f"{b.beat_id}.segment.json").read_text()
            )
        )
        # Beside the take, never over it: a rerun starts from the raw synthesis, and everything
        # downstream reads the restored file when it is there.
        restored = ctx.ddir() / "audio" / f"{b.beat_id}.restored.wav"
        raw = ctx.ddir() / "audio" / f"{b.beat_id}.wav"
        files[b.beat_id] = restored if restored.exists() else raw
    return plan, segs, files


def stage_align_words(ctx: StageContext) -> StageOutput:
    _plan, segs, _files = _load_segments(ctx)
    reports = [validate_alignment(s) for s in segs]
    payload = [r.model_dump(mode="json") for r in reports]
    _write(ctx.ddir() / "audio" / "alignment.json", json.dumps(payload, indent=1))
    if not all(r.passed for r in reports):
        raise RuntimeError("alignment validation failed")
    return StageOutput(_hash_obj(payload), {"reports": len(reports)})


def _shown_words(measured: list, display_text: str) -> list:
    """The words a caption shows, carrying the timings of the words that were said."""
    from content_factory.audio.normalize import tokenize_words

    for shown in (display_text.split(), tokenize_words(display_text)):
        if shown and len(shown) == len(measured):
            return [w.model_copy(update={"word": t}) for w, t in zip(measured, shown, strict=True)]
    return measured


HEADLINE_OPENERS = frozenset({"title", "section_intro", "chapter_transition"})
"""Scene kinds that *are* a headline. A hook burned over one of these is a second headline over
the first, whatever it says — measured on `narrated-video` (2026-09-10): a film opening on
"Resistance is not learned" carried "Resistance is not something bacteria learn" across the top of
the same frame, and the word-comparison below could not see it because the two are not the same
sentence. They do not have to be: two headlines at once is the defect."""


def _hook_overlay(plan: StoryPlan) -> str | None:
    """The headline burned over the opening seconds, unless the opening card is already one."""
    if not plan.hook_text:
        return None
    opening = next((sc for sc in plan.scenes if sc.beat_id == plan.beats[0].beat_id), None)
    if opening is not None and getattr(opening, "kind", "") in HEADLINE_OPENERS:
        return None
    for slot in ("title", "heading", "label", "text"):
        ref = getattr(opening, slot, None)
        shown = getattr(ref, "text", None)
        if shown and _same_words(shown, plan.hook_text):
            return None
    return plan.hook_text


def _same_words(left: str, right: str) -> bool:
    from content_factory.audio.normalize import tokenize_words

    return tokenize_words(left) == tokenize_words(right)


def stage_compile_captions(ctx: StageContext) -> StageOutput:
    """Cues never straddle a beat: each beat compiles alone, so a caption belongs to its scene."""
    from content_factory.audio.captions import (
        balanced_groups,
        cues_from_groups,
        to_ass,
        wrap_chars_for,
    )
    from content_factory.schemas.audio import CaptionCue, CaptionTrack

    plan, segs, _files = _load_segments(ctx)
    laid = lay_out(segs, AudioMixSpec(deliverable_id=ctx.deliverable_id))  # type: ignore[arg-type]
    cfg = get_settings().compose
    # One wrap width for the sidecar cues and for the burn-in, resolved from the frame when the
    # setting is 0: a file and a picture that break a cue in different places are two captions.
    wrap_chars = wrap_chars_for(
        plan.width, plan.height, cfg.caption_size_frac, cfg.caption_max_chars_per_line
    )
    display_by_beat = {b.beat_id: b.display_text for b in plan.beats}
    groups: list[list] = []
    respelled_beats = 0
    for beat in laid:
        if beat.words:
            words = _shown_words(list(beat.words), display_by_beat.get(beat.beat_id, ""))
            if any(w.word != o.word for w, o in zip(words, beat.words, strict=True)):
                respelled_beats += 1
            groups.extend(balanced_groups(words, max_chars_per_line=wrap_chars))
    cues: list[CaptionCue] = []
    for group in groups:
        for cue in cues_from_groups(ctx.deliverable_id or "", [group], first_index=len(cues) + 1):
            if cues and cues[-1].end_ms > cue.start_ms:
                cues[-1] = cues[-1].model_copy(
                    update={"end_ms": max(cue.start_ms, cues[-1].start_ms + 1)}
                )
            cues.append(cue)
    track = CaptionTrack(deliverable_id=ctx.deliverable_id or "", cues=tuple(cues))
    _write(ctx.ddir() / "captions" / "captions.srt", to_srt(track))
    _write(ctx.ddir() / "captions" / "captions.vtt", to_webvtt(track))
    # Burn-in variant: word-by-word highlight in the brand accent, plus the story's hook headline
    # over the opening seconds (the words a muted viewer reads in the first 1-3 s).
    brand = _project_brand(ctx)
    ass = to_ass(
        groups,
        width=plan.width,
        height=plan.height,
        font=cfg.caption_font,
        size_frac=cfg.caption_size_frac,
        bottom_frac=cfg.caption_bottom_frac,
        max_chars_per_line=wrap_chars,
        highlight=(brand.accent or cfg.caption_highlight_color) if cfg.caption_highlight else None,
        box_alpha=cfg.caption_box_alpha,
        pop=cfg.caption_pop,
        fade_ms=cfg.caption_fade_ms,
        hook=_hook_overlay(plan) if cfg.hook_overlay else None,
        hook_seconds=cfg.hook_seconds,
        hook_font=cfg.hook_font,
        hook_size_frac=cfg.hook_size_frac,
        hook_top_frac=cfg.hook_top_frac,
    )
    _write(ctx.ddir() / "captions" / "captions.ass", ass)
    return StageOutput(
        _hash_obj([track.content_hash(), sha256_hex(ass.encode())]),
        {
            "cues": len(track.cues),
            "words": sum(len(g) for g in groups),
            "shown_as_written": respelled_beats,
            # Whether the headline was *drawn*, not whether the plan carries one: it is dropped
            # when the opening card already says the same words.
            "hook": bool(_hook_overlay(plan)) if cfg.hook_overlay else False,
        },
    )


def stage_compile_timeline(ctx: StageContext) -> StageOutput:
    plan, segs, _files = _load_segments(ctx)
    laid = lay_out(segs, AudioMixSpec(deliverable_id=ctx.deliverable_id))  # type: ignore[arg-type]
    # The delivery rate. Changing it means changing the plan and recompiling, not post-processing:
    # every scene boundary is an integer frame index at this rate.
    fps = _param_int(ctx, "fps", plan.fps)
    if fps not in (24, 25, 30, 60):
        msg = f"compile_timeline fps must be 24, 25, 30 or 60, got {fps}"
        raise RuntimeError(msg)
    # The other half of the master-rate check: plan_shots refuses a plan that disagrees with the
    # story; this refuses a timeline that disagrees with the shots (how the wind shorts failed).
    shots = _shots_by_id(ctx)
    shot_fps = {sh.fps for sh in shots.values()}
    if shot_fps and fps not in shot_fps:
        msg = (
            f"compile_timeline is at {fps} fps and the shot plan is at"
            f" {sorted(shot_fps)} fps. {FPS_MASTER_HINT}"
        )
        raise RuntimeError(msg)
    measured = plan.model_copy(update={"beats": apply_measurements(plan.beats, laid), "fps": fps})
    tl = compile_timeline(measured, timeline_id="tl_run0000000001", narrated=True)
    bundle = RenderBundle(
        bundle_id="bnd_run000000001",
        kind="timeline",
        plan=measured,
        timeline=tl,
        datasets=_project_datasets(ctx),
        sources=_project_sources(ctx),
        assets=_project_assets(ctx),
        brand=_project_brand(ctx),
    )
    _write(ctx.ddir() / "timeline" / "plan.json", measured.model_dump_json(indent=1))
    _write(ctx.ddir() / "timeline" / "compiled.json", tl.model_dump_json(indent=1))
    _write(ctx.ddir() / "timeline" / "bundle.json", bundle.model_dump_json(indent=1))
    return StageOutput(tl.content_hash(), {"frames": tl.total_frames, "fps": tl.fps})


def stage_render_scenes(ctx: StageContext) -> StageOutput:
    bundle = RenderBundle.model_validate_json((ctx.ddir() / "timeline" / "bundle.json").read_text())
    outcome = render_timeline(
        bundle, workspace_id=ctx.workspace_id, store=ctx.store, workdir=ctx.ddir() / "exports"
    )
    if not outcome.qc.passed:
        raise RuntimeError(f"video render failed QC: {outcome.qc.findings}")
    return StageOutput(outcome.artifact.sha256, {"artifact_key": outcome.artifact.key})


def _music_library_dir() -> Path:
    configured = Path(get_settings().media_library.music_dir).expanduser()
    if configured.is_absolute():
        return configured
    return REPO_ROOT / configured


def stage_select_music(ctx: StageContext) -> StageOutput:
    """Deterministic pick from the local music library; an empty library selects nothing."""
    library = _music_library_dir()
    manifest = library / "tracks.json"
    mood = _param(ctx, "mood").strip().lower()
    gain_db = _param_float(ctx, "gain_db", -18.0)
    selection: dict = {"track_id": None, "reason": "music library has no manifest"}
    if manifest.exists():
        tracks = [MusicTrack.model_validate(t) for t in json.loads(manifest.read_text())]
        # The mood narrows the pool before the deterministic pick. An unset mood takes the whole
        # library; a mood no track carries selects nothing: a mismatched bed is worse than no music.
        if mood:
            tracks = [t for t in tracks if mood in {m.lower() for m in t.moods}]
            if not tracks:
                selection = {"track_id": None, "reason": f"no track has mood {mood!r}"}
        if tracks:
            index = int(sha256_hex(ctx.campaign.brief.topic.encode())[:8], 16) % len(tracks)
            track = tracks[index]
            file = library / track.filename
            if not file.exists():
                raise RuntimeError(
                    f"music track {track.track_id} is declared in the manifest but missing"
                    f" at {file}"
                )
            if file_sha256(file) != track.sha256:
                raise RuntimeError(f"music track {track.track_id} does not match its manifest hash")
            # The library-relative filename, never an absolute path: this dict is folded into the
            # outputs_hash, and an absolute path would make it depend on where the repo lives.
            selection = {
                "track_id": track.track_id,
                "filename": track.filename,
                "sha256": track.sha256,
                "gain_db": gain_db,
                "mood": mood or None,
                "attribution": track.attribution,
            }
        elif not mood:
            selection = {"track_id": None, "reason": "music library is empty"}
    _write(ctx.ddir() / "audio" / "music-selection.json", json.dumps(selection, indent=1))
    return StageOutput(
        _hash_obj(selection),
        {"track": selection.get("track_id"), "mood": mood or None, "gain_db": gain_db},
    )


def _picture_ms(ctx: StageContext) -> int:
    """How long the finished picture is, in milliseconds."""
    from content_factory.qc.media import ffprobe

    picture = _silent_picture(ctx)
    seconds = float(ffprobe(picture)["format"].get("duration") or 0)
    if seconds <= 0:
        msg = f"mix_audio cannot size a bed: {picture.name} reports no duration"
        raise RuntimeError(msg)
    return round(seconds * 1000)


def _sfx_library_dir() -> Path:
    configured = Path(get_settings().media_library.sfx_dir).expanduser()
    return configured if configured.is_absolute() else REPO_ROOT / configured


def _place_library_cues(
    ctx: StageContext,
    spec: AudioMixSpec,
    pre_master: Path | None,
    *,
    total_ms: int,
    narration: Path,
    beats: list[tuple[str, int, int]],
) -> dict:
    """Cut a cue sheet from the film's scene spans and fold the rendered track into the mix."""
    cfg = get_settings().sound_design
    if not cfg.library_cues or total_ms <= 0:
        return {"mixed": False, "reason": "disabled" if not cfg.library_cues else "no length"}
    plan_path = _story_plan_path(ctx)
    if plan_path is None:
        return {"mixed": False, "reason": "no story plan"}
    from content_factory.audio.cues import (
        CueError,
        cut_cue_sheet,
        load_library,
        spans_from_beats,
        spans_from_timeline,
    )
    from content_factory.audio.mix import place_sfx

    try:
        library = load_library(_sfx_library_dir())
        plan = StoryPlan.model_validate_json(plan_path.read_text())
        compiled_path = ctx.ddir() / "timeline" / "compiled.json"
        if beats:
            spans = spans_from_beats(plan, beats)
            source = "measured beats"
        elif compiled_path.exists():
            timeline = CompiledTimeline.model_validate_json(compiled_path.read_text())
            spans = spans_from_timeline(plan, timeline)
            source = "compiled timeline"
        else:
            return {"mixed": False, "reason": "no scene spans"}
        sheet = cut_cue_sheet(
            plan,
            spans,
            library,
            deliverable_id=ctx.deliverable_id,  # type: ignore[arg-type]
            total_ms=total_ms,
        )
        _write(ctx.ddir() / "audio" / "cue-sheet.json", sheet.model_dump_json(indent=1))
        if not sheet.cues:
            return {"mixed": False, "reason": "no cues cut", "cues": 0, "spans": source}
        track = ctx.ddir() / "audio" / "cues.wav"
        facts = place_sfx(sheet, library, track, sample_rate_hz=spec.sample_rate_hz)
    except CueError as exc:
        raise RuntimeError(f"mix_audio: {exc}") from exc
    out = ctx.ddir() / "audio" / "narration-with-cues.wav"
    if pre_master is None:
        # Nothing to duck under: the cue track IS the mix.
        _loop_to_length(track, out, duration_ms=total_ms, spec=spec)
    else:
        add_music_bed(
            pre_master,
            track,
            out,
            duration_ms=total_ms,
            gain_db=cfg.cue_gain_db,
            sample_rate_hz=spec.sample_rate_hz,
            duck_against=narration if narration.exists() else None,
        )
    return {"mixed": True, "spans": source, **facts}


def _loop_to_length(source: Path, out_wav: Path, *, duration_ms: int, spec: AudioMixSpec) -> None:
    """One bed, looped to ``duration_ms``, faded out over the last second, at its own level."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-stream_loop",
            "-1",
            "-i",
            str(source),
            "-filter_complex",
            (
                f"[0:a]aresample={spec.sample_rate_hz},aformat=channel_layouts=mono,"
                f"afade=t=out:st={max(0.0, duration_ms / 1000 - 1.0):.3f}:d=1[out]"
            ),
            "-map",
            "[out]",
            "-t",
            f"{duration_ms / 1000:.3f}",
            "-ar",
            str(spec.sample_rate_hz),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )


def stage_mix_audio(ctx: StageContext) -> StageOutput:
    """Narration stem, then the music bed, then the effects bed, then the master."""
    # The delivery loudness: -14 LUFS is the streaming default; a broadcast lane needs its own
    # number, and `master` refuses a mix that missed the target it was given.
    spec = AudioMixSpec(
        deliverable_id=ctx.deliverable_id,  # type: ignore[arg-type]
        target_lufs=_param_float(ctx, "target_lufs", -14.0),
    )
    stem = ctx.ddir() / "audio" / "narration-stem.wav"
    spoken = any((ctx.ddir() / "audio").glob("*.segment.json"))
    beat_spans: list[tuple[str, int, int]] = []
    if spoken:
        _plan, segs, files = _load_segments(ctx)
        total_ms = build_narration_stem(segs, files, spec, stem)
        pre_master: Path | None = stem
        # Where each beat actually landed in the stem this stage just built. The cue cutter needs
        # scene boundaries and this is the only place they exist yet (see _place_library_cues).
        beat_spans = [(b.beat_id, b.start_ms, b.end_ms) for b in lay_out(segs, spec)]
    else:
        total_ms, pre_master = 0, None
    selection_path = ctx.ddir() / "audio" / "music-selection.json"
    if selection_path.exists():
        selection = json.loads(selection_path.read_text())
        if selection.get("track_id"):
            bedded = ctx.ddir() / "audio" / "narration-with-music.wav"
            track = _music_library_dir() / selection["filename"]
            if pre_master is None:
                # No speech to duck under: the bed IS the mix, taken at its own level; only the
                # length has to be decided, and with no measured speech it comes from the picture.
                total_ms = _picture_ms(ctx)
                _loop_to_length(track, bedded, duration_ms=total_ms, spec=spec)
            else:
                add_music_bed(
                    pre_master,
                    track,
                    bedded,
                    duration_ms=total_ms,
                    gain_db=float(selection.get("gain_db", -18.0)),
                    sample_rate_hz=spec.sample_rate_hz,
                )
            pre_master = bedded
    sfx_wav = ctx.ddir() / "audio" / "sfx.wav"
    sfx_marker = ctx.ddir() / "audio" / "sfx.json"
    if sfx_wav.exists() and sfx_marker.exists():
        # Same bed mixer as music, keyed off the narration stem and not `pre_master`: keying off
        # the music bed made the effects duck under the *music* for the whole film.
        with_sfx = ctx.ddir() / "audio" / "narration-with-sfx.wav"
        if pre_master is None:
            total_ms = _picture_ms(ctx)
            _loop_to_length(sfx_wav, with_sfx, duration_ms=total_ms, spec=spec)
        else:
            add_music_bed(
                pre_master,
                sfx_wav,
                with_sfx,
                duration_ms=total_ms,
                gain_db=float(json.loads(sfx_marker.read_text()).get("gain_db", -22.0)),
                sample_rate_hz=spec.sample_rate_hz,
                duck_against=stem if spoken else None,
            )
        pre_master = with_sfx
    cue_facts = _place_library_cues(
        ctx, spec, pre_master, total_ms=total_ms, narration=stem, beats=beat_spans
    )
    if cue_facts.get("mixed"):
        pre_master = ctx.ddir() / "audio" / "narration-with-cues.wav"
    if pre_master is None:
        msg = (
            "mix_audio has nothing to mix: no narration segments, no music selection and no"
            " sound-design bed. Run a voice stage, select_music or sound_design first — or drop"
            " mix_audio from the lane, which compose_video handles (it writes a video-only cut)."
        )
        raise RuntimeError(msg)
    mastered = ctx.ddir() / "audio" / "narration-mastered.wav"
    report = master(
        pre_master,
        mastered,
        MasterChainSpec(
            target_lufs=spec.target_lufs,
            target_true_peak_dbtp=spec.target_true_peak_dbtp,
            sample_rate_hz=spec.sample_rate_hz,
        ),
    )
    _write(ctx.ddir() / "audio" / "loudness.json", report.model_dump_json(indent=1))
    if not report.passed:
        # Which constraint bound: over the ceiling is a different problem from short of the target.
        why = (
            "true peak is over the ceiling"
            if report.true_peak_dbtp > report.target_true_peak_dbtp + 0.1
            else "loudness is off target and the peak ceiling was not what held it"
        )
        raise RuntimeError(f"mastering missed target ({why}): {report}")
    return StageOutput(
        file_sha256(mastered),
        {
            "lufs": report.integrated_lufs,
            "target_lufs": spec.target_lufs,
            "narrated": spoken,
            "seconds": round(total_ms / 1000, 2),
            # Which library sounds are in this mix, and how many. The cue sheet on disk says
            # where and why; this is what a run record needs to answer "was any of it used".
            "library_cues": cue_facts.get("cues", 0),
            "library_sounds": cue_facts.get("sounds", []),
        },
    )


COMPOSE_MIXED_VERSION = "0.3.0"
"""0.3.0: the ``fps=`` conform is emitted only when the source is not already at the timeline's
rate, and a segment that was conformed records ``retimed_from_fps``. Bumped because the filter
string is part of every segment's ``input_hash``."""


def _rewrap_srt(srt_text: str, max_chars: int) -> str:
    """Same cues, same timings, lines re-wrapped to ``max_chars`` for the burned-in rendering."""
    blocks = []
    for block in srt_text.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) < 3:
            blocks.append(block)
            continue
        words = " ".join(lines[2:]).split()
        wrapped = wrap_words(words, max_chars)
        blocks.append("\n".join([*lines[:2], *wrapped]))
    return "\n\n".join(blocks) + "\n"


def _burn_captions(video: Path, srt: Path, out: Path, *, width: int, height: int) -> None:
    """Burn the .srt with libass, sized by frame height; libass scales SRT styles from 384x288."""
    cfg = get_settings().compose
    wrapped = out.with_suffix(".burn.srt")
    wrapped.write_text(_rewrap_srt(srt.read_text(), cfg.caption_max_chars_per_line))
    font_size = max(6, round(cfg.caption_size_frac * 288))
    margin_v = max(0, round(cfg.caption_bottom_frac * 288))
    margin_h = max(0, round(0.07 * 384))
    box_alpha = round((1.0 - cfg.caption_box_alpha) * 255)  # ASS alpha: 00 opaque .. FF clear
    # The same look as the ASS track above, so the two caption paths are not two house styles:
    # a box only when `caption_box_alpha` asks for one, an outline and a soft shadow otherwise.
    if cfg.caption_box_alpha > 0:
        border = (
            f"OutlineColour=&H{box_alpha:02X}000000,BackColour=&H{box_alpha:02X}000000,"
            f"BorderStyle=3,Outline=1,Shadow=0"
        )
    else:
        border = (
            f"OutlineColour=&H20000000,BackColour=&H70000000,BorderStyle=1,"
            f"Outline={max(1, round(font_size * 0.085))},Shadow={max(1, round(font_size * 0.045))}"
        )
    style = (
        f"FontName={cfg.caption_font},FontSize={font_size},Bold=1,PrimaryColour=&H00FFFFFF,"
        f"{border},Alignment=2,MarginV={margin_v},"
        f"MarginL={margin_h},MarginR={margin_h},WrapStyle=2"
    )
    srt_arg = str(wrapped).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    fonts_arg = _fonts_dir_arg()
    ffmpeg(
        [
            "-i",
            str(video),
            "-an",
            "-vf",
            f"subtitles='{srt_arg}'{fonts_arg}:force_style='{style}'",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-video_track_timescale",
            "90000",
            "-movflags",
            "+faststart",
            str(out),
        ],
        timeout=1800,
    )


def _fonts_dir_arg() -> str:
    cfg = get_settings().compose
    fonts_dir = Path(cfg.caption_fonts_dir)
    if not fonts_dir.is_absolute():
        fonts_dir = REPO_ROOT / fonts_dir
    if not fonts_dir.is_dir():
        return ""
    escaped = str(fonts_dir).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f":fontsdir='{escaped}'"


def _burn_ass(video: Path, ass: Path, out: Path) -> None:
    """Burn a styled ASS track with libass; styles live in the file, so no force_style."""
    ass_arg = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    ffmpeg(
        [
            "-i",
            str(video),
            "-an",
            "-vf",
            f"subtitles='{ass_arg}'{_fonts_dir_arg()}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-video_track_timescale",
            "90000",
            "-movflags",
            "+faststart",
            str(out),
        ],
        timeout=1800,
    )


def _captioned(
    ctx: StageContext, silent: Path, *, width: int, height: int
) -> tuple[Path, Path | None]:
    """The silent picture with captions burned in when available and enabled, else unchanged."""
    cfg = get_settings().compose
    srt = ctx.ddir() / "captions" / "captions.srt"
    ass = ctx.ddir() / "captions" / "captions.ass"
    if not cfg.burn_captions:
        return silent, None
    out = silent.with_name(silent.stem + ".captioned.mp4")
    if cfg.caption_highlight and ass.exists():
        _burn_ass(silent, ass, out)
        return out, ass
    if srt.exists():
        _burn_captions(silent, srt, out, width=width, height=height)
        return out, srt
    return silent, None


def _source_fps(path: Path) -> float | None:
    """The video stream's frame rate, or None when it cannot be read."""
    from content_factory.qc.media import ffprobe

    try:
        info = ffprobe(path)
    except Exception:
        # A probe failure is not a reason to fail the compose: it means "conform anyway".
        return None
    for stream in info.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        rate = str(stream.get("r_frame_rate") or "")
        num, _, den = rate.partition("/")
        try:
            return float(num) / float(den or 1)
        except (TypeError, ValueError):
            return None
    return None


def _segment_filter(route: str, tl, scene, source_fps: float | None = None) -> str:
    """ffmpeg -vf for one beat segment at the timeline's size and fps, duration_frames long."""
    conform = f"fps={tl.fps}," if source_fps is None or abs(source_fps - tl.fps) > 0.01 else ""
    if route == "generate":
        hold_s = scene.duration_frames / tl.fps + 1.0
        return (
            f"scale={tl.width}:{tl.height}:force_original_aspect_ratio=decrease,"
            f"pad={tl.width}:{tl.height}:(ow-iw)/2:(oh-ih)/2:color=black,{conform}"
            f"tpad=stop_mode=clone:stop_duration={hold_s:.3f},"
            f"trim=end_frame={scene.duration_frames},setpts=PTS-STARTPTS"
        )
    end = scene.start_frame + scene.duration_frames
    return f"{conform}trim=start_frame={scene.start_frame}:end_frame={end},setpts=PTS-STARTPTS"


def _pacing(scene_frames: list[int], fps: int) -> dict:
    """Editing rhythm facts: scenes longer than 6 s are listed for the editor, not blocked."""
    secs = [round(f / fps, 2) for f in scene_frames]
    over = [round(x, 2) for x in secs if x > 6.0]
    return {
        "scenes": len(secs),
        "mean_scene_s": round(sum(secs) / max(1, len(secs)), 2),
        "longest_scene_s": max(secs) if secs else 0.0,
        "scenes_over_6s": over,
        "changes_per_10s": round(len(secs) / max(0.1, sum(secs)) * 10, 2),
    }


def _compose_mixed(ctx: StageContext, routing: ShotRouting) -> StageOutput:
    """Hybrid workflow: one segment per scene (Remotion cut or conformed clip), concatenated."""
    from content_factory.qc.media import check_video
    from content_factory.schemas.scenes import CompiledTimeline

    compiled_path = ctx.ddir() / "timeline" / "compiled.json"
    if not compiled_path.exists():
        msg = "compose_video with a shot routing needs timeline/compiled.json: run compile_timeline"
        raise RuntimeError(msg)
    tl = CompiledTimeline.model_validate_json(compiled_path.read_text())
    exports = ctx.ddir() / "exports"
    render = exports / "bnd_run000000001.mp4"
    seg_dir = exports / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    records: list[dict] = []
    for i, scene in enumerate(tl.scenes):
        route = routing.route_for(scene.beat_id)
        shot_id = routing.shot_for(scene.beat_id) if route == "generate" else None
        if route == "generate":
            source = ctx.ddir() / "video" / str(shot_id) / "clip.mp4"
            if not source.exists():
                msg = (
                    f"beat {scene.beat_id} is routed to the generative chain but "
                    f"video/{shot_id}/clip.mp4 is missing: run generate_video first"
                )
                raise RuntimeError(msg)
        else:
            source = render
            if not source.exists():
                msg = "mixed compose needs the Remotion render exports/bnd_run000000001.mp4"
                raise RuntimeError(msg)
        source_fps = _source_fps(source)
        vf = _segment_filter(route, tl, scene, source_fps)
        source_sha = file_sha256(source)
        input_hash = _hash_obj(
            {"source": source_sha, "vf": vf, "version": COMPOSE_MIXED_VERSION, "fps": tl.fps}
        )
        target = seg_dir / f"{i:03d}_{scene.beat_id}.mp4"
        marker = target.with_suffix(".done.json")
        cached = (
            target.exists()
            and marker.exists()
            and json.loads(marker.read_text()).get("input_hash") == input_hash
        )
        if not cached:
            ffmpeg(
                [
                    "-i",
                    str(source),
                    "-an",
                    "-vf",
                    vf,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-video_track_timescale",
                    "90000",
                    "-movflags",
                    "+faststart",
                    str(target),
                ],
                timeout=1800,
            )
            _write(
                marker,
                json.dumps(
                    {"input_hash": input_hash, "route": route, "frames": scene.duration_frames},
                    indent=1,
                    sort_keys=True,
                ),
            )
        segments.append(target)
        records.append(
            {
                "index": i,
                "beat_id": scene.beat_id,
                "scene_id": scene.scene_id,
                "route": route,
                "shot_id": shot_id,
                "start_frame": scene.start_frame,
                "frames": scene.duration_frames,
                "source": str(source.relative_to(ctx.ddir())),
                "source_sha256": source_sha,
                "segment_sha256": file_sha256(target),
                # Present only when the conform fired: the rate the source was actually at, so a
                # duplicated-frame segment is legible in the manifest rather than silent.
                "retimed_from_fps": (
                    round(source_fps, 3)
                    if source_fps is not None and abs(source_fps - tl.fps) > 0.01
                    else None
                ),
            }
        )
    silent = exports / "composed.mp4"
    if silent.exists():
        silent.unlink()
    _concat_clips(segments, silent)
    vqc = check_video(silent, width=tl.width, height=tl.height, fps=tl.fps, frames=tl.total_frames)
    if not vqc.passed:
        msg = f"mixed compose failed video QC: {vqc.findings}"
        raise RuntimeError(msg)
    picture, srt = _captioned(ctx, silent, width=tl.width, height=tl.height)
    final = exports / "final.mp4"
    narration = ctx.ddir() / "audio" / "narration-mastered.wav"
    facts: dict = {}
    if narration.exists():
        speech_ms = _speech_end_ms(ctx)
        # No min_video_ms here: this path's picture IS the compiled timeline, frame for frame, so
        # holding its last frame would break the one length guarantee a timeline film makes.
        mux(picture, narration, final)
        aqc = check_audio_in_video(final, expected_speech_ms=speech_ms)
        if not aqc.passed:
            msg = f"final video failed audio QC: {aqc.findings}"
            raise RuntimeError(msg)
        facts = {k: v for k, v in aqc.facts.items() if k in ("integrated_lufs", "true_peak_dbtp")}
    else:
        final.write_bytes(picture.read_bytes())
    generated = sum(1 for r in records if r["route"] == "generate")
    pacing = _pacing([r["frames"] for r in records], tl.fps)
    _write(
        exports / "compose.json",
        json.dumps(
            {
                "version": COMPOSE_MIXED_VERSION,
                "routing_id": routing.routing_id,
                "pacing": pacing,
                "fps": tl.fps,
                "width": tl.width,
                "height": tl.height,
                "total_frames": tl.total_frames,
                "narrated": narration.exists(),
                "captions": str(srt.relative_to(ctx.ddir())) if srt else None,
                # Which segments arrived at the wrong rate and were conformed by duplicating
                # frames. Empty is the good answer and means one master rate held end to end.
                "retimed_from_fps": sorted(
                    {r["retimed_from_fps"] for r in records if r["retimed_from_fps"]}
                ),
                "segments": records,
            },
            indent=1,
            sort_keys=True,
        ),
    )
    ref = ctx.store.put_file(ctx.workspace_id, "renders", final)
    return StageOutput(
        ref.sha256,
        {
            "artifact_key": ref.key,
            "segments": len(records),
            "generated": generated,
            "rendered": len(records) - generated,
            "frames": tl.total_frames,
            "fps": tl.fps,
            "retimed": sum(1 for r in records if r["retimed_from_fps"]),
            "longest_scene_s": pacing["longest_scene_s"],
            **facts,
        },
    )


def stage_compose_video(ctx: StageContext) -> StageOutput:
    """Final assembly."""
    routing = _load_routing(ctx)
    if routing is not None and routing.generated_shot_ids():
        return _compose_mixed(ctx, routing)
    # Whatever produced the picture: the Remotion bundle for a deterministic film, the post-chain
    # or the generative concatenation for one that was drawn.
    exports = ctx.ddir() / "exports"
    final = exports / "final.mp4"
    manifest_path = exports / "compose.json"
    # Idempotent: a rerun must start from the same pristine picture it used the first time, not
    # from its own captioned/muxed output (which would burn the captions twice).
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    pristine = ctx.ddir() / previous["picture"] if previous.get("picture") else None
    if pristine is not None and pristine.exists():
        silent = pristine
    else:
        silent = _silent_picture(ctx)
        if silent == final:
            # The post chain already owns final.mp4; mux beside it rather than into its own input.
            silent = silent.rename(final.with_name("picture.mp4"))
    story = _load_story_plan(ctx)
    picture, srt = _captioned(ctx, silent, width=story.width, height=story.height)
    # Narration is optional on this path too: silent-video and photo-sequence-video have no voice
    # stage, and muxing narration-mastered.wav unconditionally died on the missing file.
    audio, source = _final_audio(ctx)
    facts: dict = {"narrated": audio is not None and source == "narration"}
    # How long the film must last to carry everything said, measured before the mux: the mux holds
    # its last frame to this number, and the QC below checks the result against the same one.
    speech_ms = _speech_end_ms(ctx) if facts["narrated"] else None
    # ...but only a picture whose length is its OWN is held. A Remotion render is the compiled
    # timeline frame for frame (a promise tests and delivery QC rely on); a drawn picture is not.
    timeline_render = silent.name.startswith("bnd_")
    hold_to = None if timeline_render else speech_ms
    if audio is None:
        # Video only. -an rather than a silent audio track: a genuinely silent film should not
        # carry an empty stream for the delivery checks to measure.
        ffmpeg(["-i", str(picture), "-an", "-c:v", "copy", "-movflags", "+faststart", str(final)])
    else:
        mux(picture, audio, final, min_video_ms=hold_to)
    _write(
        manifest_path,
        json.dumps(
            {
                "version": COMPOSE_MIXED_VERSION,
                "picture": str(silent.relative_to(ctx.ddir())),
                "captions": str(srt.relative_to(ctx.ddir())) if srt else None,
                "narrated": facts["narrated"],
                "audio": source,
            },
            indent=1,
            sort_keys=True,
        ),
    )
    if facts["narrated"] and speech_ms is not None:
        # Only speech gets the speech check: it asserts 90 % of the measured words survived the
        # mux, which is meaningless for a music bed and needs per-beat segment files.
        qc = check_audio_in_video(final, expected_speech_ms=speech_ms)
        if not qc.passed:
            raise RuntimeError(f"final video failed audio QC: {qc.findings}")
        facts.update(
            {k: v for k, v in qc.facts.items() if k in ("integrated_lufs", "true_peak_dbtp")}
        )
    ref = ctx.store.put_file(ctx.workspace_id, "renders", final)
    return StageOutput(ref.sha256, {"artifact_key": ref.key, **facts})


VIDEO_MODEL_PACKAGES: dict[str, str] = {
    "ltx-2.5.i2v": "ltx-2.5.i2v",
    "wan-animate-2.pose": "wan-animate-2.pose",
}
"""The `generate_video` `model` widget, spelled by package name, for the ComfyUI backend. `mock`
is the third option and is absent here because it selects no package at all."""


def _video_model(ctx: StageContext | None) -> tuple[str, str]:
    """Which generator animates, and with which package — the same precedence as the anchor's."""
    cfg = get_settings().video
    if "backend" in cfg.model_fields_set or ctx is None:
        return cfg.backend, cfg.package
    model = _param(ctx, "model", "").strip()
    if not model or model == "mock":
        return ("mock" if model == "mock" else cfg.backend), cfg.package
    if model not in VIDEO_MODEL_PACKAGES:
        known = ["mock", *sorted(VIDEO_MODEL_PACKAGES)]
        msg = f"unknown generate_video model {model!r}; the widget offers {known}"
        raise RuntimeError(msg)
    return "comfyui", VIDEO_MODEL_PACKAGES[model]


def _video_backend(ctx: StageContext | None = None):
    """The video executor, with the LTX weight file names the node asks for."""
    from content_factory.media.video_generate import ComfyUIVideoBackend, MockVideoBackend

    cfg = get_settings().video
    backend_name, package = _video_model(ctx)
    if backend_name == "comfyui":
        if package == "wan-animate-2.pose":
            from content_factory.media.wan_packages import wan_animate2_pose_package

            return ComfyUIVideoBackend(
                get_settings().comfyui.endpoint,
                wan_animate2_pose_package(),
                collect=cfg.collect,
                timeout_s=float(cfg.timeout_s),
                length_rule="4k+1",
            )
        from content_factory.media import ltx_packages
        from content_factory.media.ltx_packages import ltx_i2v_guided_package, ltx_i2v_package

        unet = (
            ltx_packages.TRANSFORMER
            if ctx is None
            else _param(ctx, "unet_name", ltx_packages.TRANSFORMER)
        )
        clip = (
            ltx_packages.TEXT_ENCODER
            if ctx is None
            else _param(ctx, "clip_name", ltx_packages.TEXT_ENCODER)
        )
        vae = ltx_packages.VAE if ctx is None else _param(ctx, "vae_name", ltx_packages.VAE)
        return ComfyUIVideoBackend(
            get_settings().comfyui.endpoint,
            ltx_i2v_package(unet_name=unet, clip_name=clip, vae_name=vae),
            guided_packages={
                n: ltx_i2v_guided_package(n, unet_name=unet, clip_name=clip, vae_name=vae)
                for n in range(1, cfg.max_guides + 1)
            },
            collect=cfg.collect,
            timeout_s=float(cfg.timeout_s),
        )
    return MockVideoBackend()


def _write_atomic(path: Path, data: bytes) -> str:
    """Write bytes through a temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return sha256_hex(data)


def _cached_output(marker: Path, artifact: Path, input_hash: str, sha_key: str) -> dict | None:
    """A cache hit only if the marker matches AND the file on disk is still the recorded bytes."""
    if not marker.is_file() or not artifact.is_file():
        return None
    try:
        record = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if record.get("input_hash") != input_hash:
        return None
    recorded = record.get(sha_key)
    if recorded and file_sha256(artifact) != recorded:
        return None
    return record


def _extract_frames(clip: Path, indices: list[int]) -> dict[int, bytes]:
    """The clip's frames at the given indices, as PNG bytes."""
    from content_factory.audio.mix import AudioError, ffmpeg

    out: dict[int, bytes] = {}
    for index in indices:
        png = clip.with_name(f"{clip.stem}.f{index:05d}.png")
        try:
            ffmpeg(
                [
                    "-i",
                    str(clip),
                    "-vf",
                    rf"select=eq(n\,{index})",
                    "-vsync",
                    "0",
                    "-frames:v",
                    "1",
                    str(png),
                ],
                timeout=120,
            )
        except AudioError:
            # A frame index past the end of the clip is a finding `guide_adherence` reports by
            # name from the absence; logging it here would say the same thing twice.
            continue
        if png.exists():
            out[index] = png.read_bytes()
    return out


def _guide_adherence(
    clip: Path, guide_pngs: dict[int, bytes], *, style: str = "", camera: str = ""
) -> dict[str, object]:
    """Did the clip actually pass through the guides it was given?"""
    if not guide_pngs:
        return {"guides": 0, "passed": True, "similarity": [], "worst": 1.0, "reasons": []}
    from content_factory.sequences.drift import guide_adherence

    cfg = get_settings().image_sequences
    _locked, delta_max = cfg.drift_thresholds.resolve(style=style, camera=camera)
    frames = _extract_frames(clip, sorted(guide_pngs))
    return guide_adherence(frames, guide_pngs, style_delta_max=delta_max).as_facts()


def _concat_clips(clips: list[Path], target: Path) -> None:
    """Concatenate same-size clips losslessly with the ffmpeg concat demuxer."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if len(clips) == 1:
        target.write_bytes(clips[0].read_bytes())
        return
    listing = target.with_suffix(".concat.txt")
    listing.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(target)], timeout=600
    )


def _hold_stills(ctx: StageContext, manifest: dict, shots: dict[str, ShotSpec]) -> StageOutput:
    """Cut the drawings together, each held for its shot's length."""
    from content_factory.audio.mix import ffmpeg

    exports = ctx.ddir() / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    entries = manifest["shots"]
    # Whatever the finishing chain last produced for these drawings, if all of them: a lane that
    # upscales before the hold gets the restored pictures on screen. One per shot, in shot order.
    finished = _finished_anchor_frames(ctx, sum(len(e["frames"]) for e in entries))
    used: list[Path] = []
    listing_lines: list[str] = []
    held: list[dict] = []
    fps = 24
    for index, entry in enumerate(entries):
        shot = shots.get(entry["shot_id"])
        fps = shot.fps if shot else get_settings().video.fps
        seconds = round((shot.frame_count if shot else fps * 2) / fps, 3)
        png = (
            finished[index] if index < len(finished) else (ctx.ddir() / entry["frames"][0]["path"])
        ).resolve()
        used.append(png)
        listing_lines.append(f"file '{png}'\nduration {seconds}\n")
        held.append({"shot_id": entry["shot_id"], "seconds": seconds})
    # The concat demuxer ignores the final entry's duration, so the last image is repeated.
    listing_lines.append(f"file '{used[-1]}'\n")

    target = exports / "generated.mp4"
    listing = exports / "held.concat.txt"
    listing.write_text("".join(listing_lines))
    # Cut at the drawings' own resolution, not the ShotSpec's: downscaling 2560x1440 hatching to
    # 1024x576 *raised* edge energy 54 % (aliasing); ``size`` still forces a smaller preview cut.
    native = _png_size(used[0].read_bytes())
    default_size = (
        int(native.get("png_width") or entries[0]["width"]),
        int(native.get("png_height") or entries[0]["height"]),
    )
    width, height = _param_size(ctx, "size", default_size)
    width, height = width - (width % 2), height - (height % 2)  # yuv420p needs even dimensions
    input_hash = _hash_obj(
        {
            "held": held,
            "anchors": [e["frames"][0]["sha256"] for e in entries],
            # Not in the anchor digests: without this a raw cut is reused after an upscale and
            # the enlargement never reaches the screen.
            "used": [file_sha256(p) for p in used],
            "size": [width, height],
            "fps": fps,
        }
    )
    marker = exports / "held.done.json"
    if (
        marker.exists()
        and target.exists()
        and json.loads(marker.read_text()).get("input_hash") == input_hash
    ):
        record = json.loads(marker.read_text())
        return StageOutput(record["video_sha256"], {**record["facts"], "cache_hit": True})

    ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            # vfr keeps each still on screen for exactly its duration rather than resampling it
            # into a smooth stream.
            "-vsync",
            "vfr",
            # Colour has to be *declared*: untagged, every player guesses, and one that assumes
            # limited range on full-range data crushes the blacks. So convert to tv BT.709 and tag.
            "-vf",
            f"scale={width}:{height}:flags=lanczos"
            f":in_range=pc:out_range=tv:out_color_matrix=bt709,fps={fps}",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:v",
            "libx264",
            "-crf",
            "16",
            "-preset",
            "slow",
            "-movflags",
            "+faststart",
            str(target),
        ],
        timeout=3600,
    )
    total = round(sum(h["seconds"] for h in held), 2)
    facts = {
        "motion": "hold",
        "stills": len(held),
        "seconds": total,
        "fps": fps,
        "size": f"{width}x{height}",
    }
    ref = ctx.store.put_file(ctx.workspace_id, "renders", target)
    record = {
        "input_hash": input_hash,
        "video_sha256": file_sha256(target),
        "facts": {**facts, "artifact_key": ref.key},
    }
    _write(marker, json.dumps(record, indent=1, sort_keys=True))
    return StageOutput(record["video_sha256"], {**record["facts"], "cache_hit": False})


def _story_plan_or_none(ctx: StageContext) -> StoryPlan | None:
    """The deliverable's story plan when plan_story has run for it, else None."""
    path = _story_plan_path(ctx)
    return StoryPlan.model_validate_json(path.read_text()) if path is not None else None


def _codec_size(width: int, height: int) -> tuple[int, int]:
    """The nearest frame size at or above ``width x height`` that a video codec will take."""
    grid = 16

    def snap(value: int, limit: int) -> int:
        return max(64, min(limit, -(-value // grid) * grid))

    return snap(width, 3840), snap(height, 2160)


def _backend_size_cap(backend: object) -> tuple[int, int] | None:
    """The largest frame the backend's workflow package will accept, or None if it says."""
    params = getattr(getattr(backend, "package", None), "parameters", None)
    if not params:
        return None
    caps = {b.name: b.maximum for b in params if b.name in {"width", "height"}}
    if caps.get("width") is None or caps.get("height") is None:
        return None
    return int(caps["width"]), int(caps["height"])  # type: ignore[arg-type]


def _clip_size(
    backend: object, width: int, height: int, *, fit: bool, what: str
) -> tuple[int, int]:
    """``_codec_size``, but inside what the video model can actually generate."""
    cap = _backend_size_cap(backend)
    if cap is None or (width <= cap[0] and height <= cap[1]):
        return _codec_size(width, height)
    if not fit:
        msg = (
            f"{what} is {width}x{height} and this video package generates at most"
            f" {cap[0]}x{cap[1]}. Plan the story at or below that, or point"
            f" video.package at a graph that takes the larger frame — a clip generated"
            f" smaller and scaled up would invent the difference."
        )
        raise RuntimeError(msg)
    scale = min(cap[0] / width, cap[1] / height)
    return _codec_size(int(width * scale), int(height * scale))


def _video_prompt_for(ctx: StageContext, shot: ShotSpec | None, story: StoryPlan | None) -> str:
    """The compiled cinematography paragraph for one shot, under the lane's own preamble."""
    lane = _param(ctx, "prompt").strip()
    if shot is not None:
        joined = ". ".join(p.rstrip(".") for p in (lane, compile_video_prompt(shot, story)) if p)
        return (joined + ".")[:5000]
    subject = _param(ctx, "subject").strip() or ((story.visual_subject or "") if story else "")
    if not subject:
        msg = (
            "generate_video has no subject: this lane plans no shots, so the node's `prompt`"
            " widget says how to move and nothing says what is moving. Pass"
            ' --subject "one sentence naming the film\'s world", or run plan_story with a'
            " StoryPlan whose visual_subject is set."
        )
        raise RuntimeError(msg)
    joined = ". ".join(p.rstrip(".") for p in (lane, subject) if p)
    return (joined + ".")[:5000]


def stage_generate_video(ctx: StageContext) -> StageOutput:
    """Image-to-video through the video.generate skill."""
    from content_factory.media.video_generate import (
        GuideFrame,
        VideoGenerationRequest,
        run_video_skill,
    )

    cfg = get_settings().video
    anchors_manifest = ctx.ddir() / "anchors" / "manifest.json"
    shots = _shots_by_id(ctx)
    story = _story_plan_or_none(ctx)
    manifest = json.loads(anchors_manifest.read_text()) if anchors_manifest.exists() else None
    per_shot = manifest is not None and all(e.get("shot_id") for e in manifest["shots"])

    if _param(ctx, "motion", cfg.motion) == "hold":
        if manifest is None or not per_shot:
            msg = "motion=hold needs one anchor per shot: run plan_shots and generate_anchor first"
            raise RuntimeError(msg)
        return _hold_stills(ctx, manifest, shots)

    backend = _video_backend(ctx)
    warmed: set[str] = set()
    guide_strength = _param_float(ctx, "guide_strength", cfg.guide_strength)
    noise_seed = _param_int(ctx, "noise_seed", 0)

    if manifest is not None and per_shot:
        if not manifest["shots"]:
            # The routing sent every beat to the renderer: a no-op, not a failure. `compose_video`
            # takes its plain path, and an empty list would only die in ffmpeg's concat demuxer.
            return StageOutput(
                _hash_obj({"shots": []}),
                {
                    "backend": backend.name,
                    "shots": 0,
                    "note": "routing generated no shots; compose_video uses the rendered picture",
                },
            )
        clips: list[Path] = []
        results: list[dict] = []
        for entry in manifest["shots"]:
            shot = shots.get(entry["shot_id"])
            frames = entry["frames"]
            first_png = (ctx.ddir() / frames[0]["path"]).read_bytes()
            pose_driven = cfg.package == "wan-animate-2.pose"
            guide_source = [] if pose_driven else frames[1 : 1 + cfg.max_guides]
            guides = tuple(
                GuideFrame(
                    frame_index=f["frame_index"],
                    png_sha256=f["sha256"],
                    strength=guide_strength,
                )
                for f in guide_source
            )
            guide_pngs = {
                f["frame_index"]: (ctx.ddir() / f["path"]).read_bytes() for f in guide_source
            }
            extra_files: dict[str, Path] = {}
            if pose_driven:
                from content_factory.controls import pose_video_from_track

                extra_files["pose_video"] = pose_video_from_track(
                    ctx.ddir() / "controls" / entry["shot_id"], fps=shot.fps if shot else cfg.fps
                )
            fps = shot.fps if shot else cfg.fps
            frame_count = shot.frame_count if shot else round(cfg.default_duration_s * fps)
            clip_width, clip_height = _clip_size(
                backend,
                entry["width"],
                entry["height"],
                fit=False,
                what=f"shot {entry['shot_id']}",
            )
            request = VideoGenerationRequest(
                request_id=f"vid_{sha256_hex(entry['shot_id'].encode())[:12]}",
                purpose=f"shot {entry['shot_id']} for {ctx.deliverable_id or 'shared'}",
                prompt=_video_prompt_for(ctx, shot, story),
                width=clip_width,
                height=clip_height,
                duration_s=max(1.0, round(frame_count / fps, 3)),
                fps=fps,
                # The node's seed wins over the shot's, so an operator can re-roll a whole film's
                # motion without editing the plan. Zero means "the shot decides".
                seed=noise_seed or (shot.seed if shot else 0),
                with_audio=False,
                guides=guides,
            )
            shot_dir = ctx.ddir() / "video" / entry["shot_id"]
            clip = shot_dir / "clip.mp4"
            marker = shot_dir / ".done.json"
            input_hash = _hash_obj(
                {
                    "request": request.model_dump(mode="json"),
                    "first_frame": frames[0]["sha256"],
                    # The package id, version and un-parameterised graph digest, not just the
                    # backend's name: editing a node in ltx_packages.py used to reuse the old clip.
                    "graph": backend.graph_fingerprint(len(guides)),
                    "package": cfg.package,
                    # Reworded clause tables change the prompt without changing the ShotSpec, so
                    # the compiler's version is part of what a cached clip was made from.
                    "prompt_compiler": PROMPT_COMPILER_VERSION,
                    "extra_files": {k: file_sha256(v) for k, v in extra_files.items()},
                }
            )
            cached = _cached_output(marker, clip, input_hash, "video_sha256")
            if cached is not None:
                record = {**cached, "cache_hit": True}
            else:
                _ensure_backend_ready(backend, warmed)
                out = run_video_skill(
                    request,
                    backend,
                    workdir=shot_dir,
                    first_frame_png=first_png,
                    guide_pngs=guide_pngs,
                    extra_files=extra_files or None,
                )
                _write_atomic(clip, out.mp4)
                _write(shot_dir / "provenance.json", out.result.model_dump_json(indent=1))
                record = {
                    "shot_id": entry["shot_id"],
                    "input_hash": input_hash,
                    "video_sha256": out.result.video_sha256,
                    "duration_s": out.result.duration_s,
                    "guides": len(guides),
                    "backend": out.result.backend,
                    "cache_hit": False,
                }
                _write(marker, json.dumps(record, indent=1, sort_keys=True))
                _log_execution(ctx, f"generate_video:{entry['shot_id']}")
            # Measured on the produced clip, cached or not, and written beside it: a cache hit has
            # the same answer as the run that made it, at one ffmpeg call per guide.
            adherence = _guide_adherence(
                clip,
                guide_pngs,
                style=_sequence_style_name(ctx),
                camera=str(shot.camera.preset) if shot else "",
            )
            _write(
                shot_dir / "guide-adherence.json",
                json.dumps(adherence, indent=1, sort_keys=True),
            )
            record = {**record, "guide_adherence": adherence}
            clips.append(clip)
            results.append(record)
        target = ctx.ddir() / "exports" / "generated.mp4"
        _concat_clips(clips, target)
        ref = ctx.store.put_file(ctx.workspace_id, "renders", target)
        return StageOutput(
            _hash_obj([r["video_sha256"] for r in results]),
            {
                "backend": backend.name,
                "shots": len(results),
                "guides": sum(r["guides"] for r in results),
                "duration_s": round(sum(r["duration_s"] for r in results), 3),
                "cache_hits": sum(1 for r in results if r["cache_hit"]),
                # The worst guide across every shot, and which shots missed the (uncalibrated)
                # bar. Reported so a live run produces the number a real threshold is set from.
                "guide_similarity_worst": min(
                    (float(r["guide_adherence"]["worst"]) for r in results), default=1.0
                ),
                "guides_off_target": [
                    r["shot_id"] for r in results if not r["guide_adherence"]["passed"]
                ],
                "artifact_key": ref.key,
            },
        )

    first_png: bytes | None = None
    width, height = (640, 352) if backend.name == "mock" else cfg.default_size
    if manifest is not None:
        first = manifest["shots"][0]
        first_png = (ctx.ddir() / first["frames"][0]["path"]).read_bytes()
        width, height = first["width"], first["height"]
    prompt = _video_prompt_for(ctx, None, story)
    # With no ShotSpec the clip's length is this node's to choose; with one the shot's frame count
    # is what the guide indices were placed against, so the widget stays out of the per-shot path.
    duration_s = _param_float(
        ctx, "duration", cfg.default_duration_s if backend.name != "mock" else 4.0
    )
    # The supplied still's own size, fitted into what the model takes: see `_clip_size`.
    clip_width, clip_height = _clip_size(
        backend, width, height, fit=True, what="the supplied first frame"
    )
    request = VideoGenerationRequest(
        request_id=f"vid_{sha256_hex(ctx.campaign.campaign_id.encode())[:12]}",
        purpose=f"generated clip for {ctx.deliverable_id or 'shared'}",
        prompt=prompt,
        width=clip_width,
        height=clip_height,
        duration_s=duration_s,
        fps=cfg.fps,
        # Derived from what is being generated rather than from the brief: a clip regenerated after
        # the prompt changed must not reuse the noise of the one before it.
        seed=noise_seed or int(sha256_hex(prompt.encode())[:8], 16),
        with_audio=backend.name == "mock",
    )
    # The same `.done.json` discipline the per-shot path has: without it this branch regenerated
    # its clip on every rerun of the lane — forty seconds of GPU for a file already on disk.
    gen_dir = ctx.ddir() / "generated"
    target = ctx.ddir() / "exports" / "generated.mp4"
    marker = gen_dir / ".done.json"
    input_hash = _hash_obj(
        {
            "request": request.model_dump(mode="json"),
            "first_frame": sha256_hex(first_png) if first_png else "",
            "graph": backend.graph_fingerprint(0),
            "package": cfg.package,
            "prompt_compiler": PROMPT_COMPILER_VERSION,
        }
    )
    cached = _cached_output(marker, target, input_hash, "video_sha256")
    if cached is not None:
        ref = ctx.store.put_file(ctx.workspace_id, "renders", target)
        return StageOutput(
            str(cached["video_sha256"]),
            {
                "backend": cached.get("backend", backend.name),
                "duration_s": cached.get("duration_s"),
                "artifact_key": ref.key,
                "cache_hit": True,
            },
        )
    _ensure_backend_ready(backend, warmed)
    out = run_video_skill(request, backend, workdir=gen_dir, first_frame_png=first_png)
    _write_atomic(target, out.mp4)
    _write(gen_dir / "provenance.json", out.result.model_dump_json(indent=1))
    _write(
        marker,
        json.dumps(
            {
                "input_hash": input_hash,
                "video_sha256": out.result.video_sha256,
                "duration_s": out.result.duration_s,
                "backend": out.result.backend,
            },
            indent=1,
            sort_keys=True,
        ),
    )
    ref = ctx.store.put_file(ctx.workspace_id, "renders", target)
    return StageOutput(
        out.result.video_sha256,
        {
            "backend": out.result.backend,
            "duration_s": out.result.duration_s,
            "artifact_key": ref.key,
            "cache_hit": False,
        },
    )


# --- post-processing chain: fix (Cutie + ProPainter) -> upscale (SeedVR2) -> interpolate ----------
PICTURE_SUFFIXES_IN: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp")
VIDEO_SUFFIXES_IN: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
"""What the post chain will adopt out of the run's uploads folder. Both lists are what FFmpeg on
this host reads and what ``ingest.convert`` already accepts, so a lane cannot promise to finish a
container the converter would refuse."""


def _chain_inputs(ctx: StageContext) -> list[tuple[str, Path]]:
    """(name, working dir) per clip to post-process, falling back to the run's uploads folder."""
    video_dir = ctx.ddir() / "video"
    shots = sorted(p.parent for p in video_dir.glob("*/clip.mp4")) if video_dir.exists() else []
    if shots:
        return [(d.name, d) for d in shots]
    if (ctx.ddir() / "exports" / "generated.mp4").exists():
        return [("main", video_dir / "main")]
    if (ctx.ddir() / "sequence" / "frames").exists():
        return [("sequence", ctx.ddir() / "sequence")]
    # The drawings, before anything is held or animated: a held cut has N pictures worth finishing
    # and thousands of identical frames. After the clip cases so a lane that animates is unaffected.
    if _anchor_sequence_dir(ctx) is not None:
        return [("anchors", ctx.ddir() / "anchors")]
    supplied = _adopt_uploaded_picture(ctx)
    if supplied is not None:
        return [supplied]
    msg = (
        "post chain: nothing to process. This lane finishes material you supply, and there is"
        " none: no per-shot clips, no generated cut, no frame sequence, and nothing usable in"
        f" {ctx.project_dir / UPLOADS_DIRNAME}."
        " Run `content-factory make <lane> --input <clip or folder of stills>`."
    )
    raise RuntimeError(msg)


def _adopt_uploaded_picture(ctx: StageContext) -> tuple[str, Path] | None:
    """The operator's clip or stills, staged where the post chain reads them."""
    uploads = ctx.project_dir / UPLOADS_DIRNAME
    if not uploads.is_dir():
        return None
    files = sorted(p for p in uploads.glob("**/*") if p.is_file())
    clips = [p for p in files if p.suffix.lower() in VIDEO_SUFFIXES_IN]
    stills = [p for p in files if p.suffix.lower() in PICTURE_SUFFIXES_IN]
    if clips and stills:
        msg = (
            f"post chain: {uploads} holds both a clip ({clips[0].name}) and"
            f" {len(stills)} still(s). Finish one thing at a time."
        )
        raise RuntimeError(msg)
    if len(clips) > 1:
        names = ", ".join(p.name for p in clips)
        msg = f"post chain: {len(clips)} clips in {uploads} ({names}). Leave the one to finish."
        raise RuntimeError(msg)
    if clips:
        workdir = ctx.ddir() / "video" / "upload"
        target = workdir / "clip.mp4"
        source = clips[0]
        if source.suffix.lower() == ".mp4":
            if not (target.exists() and target.stat().st_size == source.stat().st_size):
                workdir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
        elif not target.exists():
            from content_factory.ingest.convert import convert_media
            from content_factory.ingest.uploads import sniff_mime

            workdir.mkdir(parents=True, exist_ok=True)
            converted = convert_media(source, sniff_mime(source), workdir)
            if converted.path.resolve() != target.resolve():
                target.write_bytes(converted.path.read_bytes())
        return ("upload", workdir)
    if stills:
        frames = ctx.ddir() / "sequence" / "frames"
        existing = sorted(frames.glob("[0-9]*.png"))
        if len(existing) == len(stills):
            return ("sequence", ctx.ddir() / "sequence")
        from content_factory.audio.mix import ffmpeg

        frames.mkdir(parents=True, exist_ok=True)
        for index, still in enumerate(stills):
            dest = frames / f"{index:04d}.png"
            if dest.exists():
                continue
            if still.suffix.lower() == ".png":
                dest.write_bytes(still.read_bytes())
            else:
                ffmpeg(["-i", str(still), str(dest)], timeout=120)
        return ("sequence", ctx.ddir() / "sequence")
    return None


def _anchor_sequence_dir(ctx: StageContext) -> Path | None:
    """The anchor drawings gathered into ``anchors/frames/%04d.png``, in shot order."""
    manifest_path = ctx.ddir() / "anchors" / "manifest.json"
    if not manifest_path.exists():
        return None
    entries = json.loads(manifest_path.read_text()).get("shots", [])
    paths = [ctx.ddir() / frame["path"] for entry in entries for frame in entry["frames"]]
    paths = [p for p in paths if p.is_file()]
    if not paths:
        return None
    frames = ctx.ddir() / "anchors" / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(paths):
        dest = frames / f"{index:04d}.png"
        if not dest.exists() or dest.stat().st_size != source.stat().st_size:
            dest.write_bytes(source.read_bytes())
    for stale in sorted(frames.glob("[0-9]*.png"))[len(paths) :]:
        stale.unlink()
    return frames


def _finished_anchor_frames(ctx: StageContext, expected: int) -> list[Path]:
    """The post chain's finished drawings, when it has finished exactly ``expected`` of them."""
    state = _chain_state(ctx.ddir() / "anchors")
    latest = state.get("latest")
    if not latest or not state.get("steps"):
        return []
    frames = sorted(Path(latest).glob("[0-9]*.png"))
    return frames if len(frames) == expected else []


def _chain_state(workdir: Path) -> dict:
    path = workdir / "chain.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _latest_frames(ctx: StageContext, name: str, workdir: Path) -> Path:
    """The frame dir the next step consumes: the last chain output, else the exploded clip."""
    from content_factory.postchain import explode_video

    state = _chain_state(workdir)
    if state.get("latest"):
        return Path(state["latest"])
    if name in ("sequence", "anchors"):
        return workdir / "frames"
    clip = workdir / "clip.mp4" if name != "main" else ctx.ddir() / "exports" / "generated.mp4"
    frames = workdir / "frames"
    if not any(frames.glob("*.png")):
        explode_video(clip, frames)
    return frames


def _chain_step(
    ctx: StageContext,
    name: str,
    workdir: Path,
    step: str,
    tool: str,
    params: dict,
    *,
    source: Path | None = None,
    advance: bool = True,
) -> dict:
    """Run one tool as a cached chain step."""
    from content_factory.postchain import POSTCHAIN_VERSION, frames_digest, run_tool

    cfg = get_settings().postchain
    state = _chain_state(workdir)
    pinned = state.get("inputs", {}).get(step)
    src = source or (Path(pinned) if pinned else _latest_frames(ctx, name, workdir))
    out_dir = workdir / step
    input_hash = _hash_obj(
        {
            "step": step,
            "tool": tool,
            "input": frames_digest(src),
            "params": params,
            "version": POSTCHAIN_VERSION,
        }
    )
    marker = out_dir / ".done.json"
    cached = (
        marker.exists()
        and json.loads(marker.read_text()).get("input_hash") == input_hash
        and any((out_dir / "frames").glob("*.png"))
    )
    if cached:
        record = {**json.loads(marker.read_text()), "cache_hit": True}
    else:
        import time

        _free_the_gpu(ctx, f"{step}:{tool}")
        before = gpu_memory_used_mib()
        started = time.monotonic()
        summary = run_tool(tool, src, out_dir, params, timeout_s=cfg.timeout_s)
        telemetry = _generation_telemetry(
            before=before, seconds=time.monotonic() - started, backend=None
        )
        record = {
            "step": step,
            "tool": tool,
            "input_hash": input_hash,
            "frames": int(summary["frames"]),
            "sha256": summary["sha256"],
            "telemetry": telemetry,
            "cache_hit": False,
        }
        _write(marker, json.dumps(record, indent=1, sort_keys=True))
        _log_execution(ctx, f"{step}:{name}", telemetry)
    state.setdefault("inputs", {})[step] = str(src)
    if advance:
        state["latest"] = str(out_dir / "frames")
        steps = list(state.get("steps", []))
        if step not in steps:
            steps.append(step)
        state["steps"] = steps
    _write(workdir / "chain.json", json.dumps(state, indent=1, sort_keys=True))
    return record


def stage_fix_video(ctx: StageContext) -> StageOutput:
    """Cutie + ProPainter remove the configured segmentation ids; with none, frames pass through."""
    cfg = get_settings().postchain
    remove_ids = _param_int_list(ctx, "remove_ids", cfg.remove_seg_ids)
    dilation = _param_int(ctx, "mask_dilation", cfg.mask_dilation)
    results: list[dict] = []
    for name, workdir in _chain_inputs(ctx):
        seed = ctx.ddir() / "controls" / name / "segmentation" / "frames" / "0000.png"
        if not remove_ids or not seed.exists():
            reason = "no segmentation ids to remove" if not remove_ids else "no seed mask"
            results.append({"clip": name, "skipped": reason})
            continue
        frames = _latest_frames(ctx, name, workdir)
        masks = _chain_step(
            ctx,
            name,
            workdir,
            "masks",
            "cutie",
            {"seed_mask": str(seed), "keep_ids": list(remove_ids)},
            source=frames,
            advance=False,
        )
        fixed = _chain_step(
            ctx,
            name,
            workdir,
            "fixed",
            "propainter",
            {"mask_dir": str(workdir / "masks" / "frames"), "mask_dilation": dilation},
        )
        results.append(
            {
                "clip": name,
                "masks": masks["sha256"],
                "fixed": fixed["sha256"],
                "cache_hit": fixed["cache_hit"],
            }
        )
    return StageOutput(
        _hash_obj(results),
        {"clips": results, "remove_ids": list(remove_ids), "mask_dilation": dilation},
    )


def stage_upscale_video(ctx: StageContext) -> StageOutput:
    cfg = get_settings().postchain
    # The node's widget wins over the setting, as in stage_interpolate. _param speaks strings and
    # the setting and tool speak numbers, so the coercion is explicit here.
    resolution = int(_param(ctx, "resolution", str(cfg.upscale_resolution)))
    results = [
        {
            "clip": name,
            **_chain_step(ctx, name, workdir, "upscaled", cfg.upscaler, {"resolution": resolution}),
        }
        for name, workdir in _chain_inputs(ctx)
    ]
    return StageOutput(
        _hash_obj([r["sha256"] for r in results]),
        {
            "clips": len(results),
            "resolution": resolution,
            "cache_hits": sum(1 for r in results if r["cache_hit"]),
        },
    )


def stage_interpolate(ctx: StageContext) -> StageOutput:
    """Frame interpolation as the last step; muxes ``exports/final.mp4`` (shots concatenated)."""
    from content_factory.postchain import PostChainError, mux_frames

    cfg = get_settings().postchain
    engine = _param(ctx, "engine", cfg.interpolator)
    raw_factor = _param(ctx, "factor", str(cfg.interpolate_factor)).strip().rstrip("xX")
    factor = int(raw_factor) if raw_factor.isdigit() else cfg.interpolate_factor
    # A held cut is the answer, not an intermediate: interpolating it invents frames nobody drew or
    # reviewed, and rife runs happily on a concat of stills. So the marker beats the engine widget.
    held = (ctx.ddir() / "exports" / "held.done.json").exists()
    if engine == "none" or held:
        return _mux_without_interpolation(
            ctx, skipped="held cut" if held else "no interpolation requested", requested=engine
        )
    results: list[dict] = []
    finals: list[Path] = []
    shots = _shots_by_id(ctx)
    for name, workdir in _chain_inputs(ctx):
        try:
            record = _chain_step(ctx, name, workdir, "interpolated", engine, {"factor": factor})
        except PostChainError as exc:
            # Optical-flow memory is quadratic in frame area: GIMM-VFI asked for 12.36 GiB on one
            # 1088x1920 pair (24 GB card). A size limit, not a broken install: ship uninterpolated.
            if "OutOfMemoryError" not in str(exc):
                raise
            return _mux_without_interpolation(
                ctx,
                skipped=f"{engine} ran out of memory on this frame size",
                requested=engine,
            )
        if name == "sequence":
            fps = 8 * factor  # keyframe flipbooks preview at 8 fps
        else:
            base = shots[name].fps if name in shots else get_settings().video.fps
            fps = base * factor
        final = workdir / "final.mp4"
        if not record["cache_hit"] or not final.exists():
            mux_frames(workdir / "interpolated" / "frames", final, fps=fps)
        finals.append(final)
        results.append({"clip": name, "fps": fps, **record})
    target = ctx.ddir() / "exports" / POST_CHAIN_EXPORT
    _concat_clips(finals, target)
    ref = ctx.store.put_file(ctx.workspace_id, "renders", target)
    return StageOutput(
        _hash_obj([r["sha256"] for r in results]),
        {
            "clips": len(results),
            "engine": engine,
            "factor": factor,
            "cache_hits": sum(1 for r in results if r["cache_hit"]),
            "artifact_key": ref.key,
        },
    )


def _mux_without_interpolation(ctx: StageContext, *, skipped: str, requested: str) -> StageOutput:
    """Interpolation declined, and the picture still assembled from whatever the chain finished."""
    from content_factory.postchain import mux_frames

    # Resolved where it is used: this function *produces* the picture on a lane that has none yet,
    # so asking up front turned the OOM fallback into a second failure (journal 2026-09-10).
    if skipped == "held cut":
        picture = _silent_picture(ctx)
        return StageOutput(
            file_sha256(picture),
            {
                "clips": 0,
                "engine": "none",
                "skipped": skipped,
                "requested_engine": requested,
                "picture": picture.name,
            },
        )
    try:
        inputs = _chain_inputs(ctx)
    except RuntimeError:
        inputs = []
    finals: list[Path] = []
    muxed: list[dict] = []
    shots = _shots_by_id(ctx)
    for name, workdir in inputs:
        state = _chain_state(workdir)
        latest = state.get("latest")
        if not latest or not state.get("steps"):
            continue
        frames = Path(latest)
        if not sorted(frames.glob("[0-9]*.png")):
            continue
        fps = (
            SEQUENCE_PREVIEW_FPS
            if name in ("sequence", "anchors")
            else (shots[name].fps if name in shots else get_settings().video.fps)
        )
        final = workdir / "final.mp4"
        mux_frames(frames, final, fps=fps)
        finals.append(final)
        muxed.append({"clip": name, "fps": fps, "steps": list(state["steps"])})
    if not finals:
        # Nothing came out of the chain, so whatever picture the run already had is the answer —
        # and if it has none, "no picture yet" is the honest failure.
        picture = _silent_picture(ctx)
        return StageOutput(
            file_sha256(picture),
            {
                "clips": 0,
                "engine": "none",
                "skipped": skipped,
                "requested_engine": requested,
                "picture": picture.name,
            },
        )
    target = ctx.ddir() / "exports" / POST_CHAIN_EXPORT
    _concat_clips(finals, target)
    ref = ctx.store.put_file(ctx.workspace_id, "renders", target)
    return StageOutput(
        file_sha256(target),
        {
            "clips": len(muxed),
            "engine": "none",
            "skipped": skipped,
            "muxed": muxed,
            "picture": target.name,
            "artifact_key": ref.key,
        },
    )


def stage_render_animation(ctx: StageContext) -> StageOutput:
    """Deterministic explainer animation: frames land like an image sequence plus a preview mp4."""
    from content_factory.animation import render_animation_frames
    from content_factory.schemas.animation import AnimationSpec

    ds = sample_dataset()
    kind = _param(ctx, "kind", "count_up")
    if kind not in ("count_up", "equation", "diagram_build"):
        msg = f"render_animation kind must be count_up, equation or diagram_build, got {kind!r}"
        raise RuntimeError(msg)
    fps = _param_int(ctx, "fps", 24)
    if fps not in (24, 30):
        msg = f"render_animation fps must be 24 or 30, got {fps}"
        raise RuntimeError(msg)
    duration_ms = max(500, min(60_000, round(_param_float(ctx, "duration_s", 2.0) * 1000)))
    # equation and diagram_build reveal one step at a time, and AnimationSpec refuses no steps. The
    # story's beats ARE the steps; without a story the stage says what is missing.
    steps: tuple[str, ...] = ()
    if kind != "count_up":
        story = _story_plan_or_none(ctx)
        if story is None:
            msg = (
                f"render_animation kind={kind} reveals one step at a time and this run has no"
                " story to take them from: run plan_story first, or use kind=count_up"
            )
            raise RuntimeError(msg)
        steps = tuple(b.display_text[:120] for b in sorted(story.beats, key=lambda b: b.order))[:12]
    width, height = _param_size(ctx, "size", (1280, 720))
    spec = AnimationSpec(
        animation_id=f"anim_{sha256_hex(ctx.campaign.campaign_id.encode())[:12]}",
        kind=kind,  # type: ignore[arg-type]
        title=ctx.campaign.brief.topic[:200],
        value=(float(len(ds.rows)) if getattr(ds, "rows", None) else 21.0)
        if kind == "count_up"
        else None,
        unit="",
        steps=steps,
        duration_ms=duration_ms,
        fps=fps,  # type: ignore[arg-type]
        width=width,
        height=height,
    )
    out_dir = ctx.ddir() / "animation"
    executor = get_settings().animation.executor
    spec_path = out_dir / "spec.json"
    _write(spec_path, spec.model_dump_json(indent=1))
    if executor == "manim":
        import subprocess

        proc = subprocess.run(  # noqa: S603
            [
                "uv",
                "run",
                "--project",
                "skills/video/manim",
                "python",
                "skills/video/manim/render.py",
                str(spec_path),
                str(out_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
            cwd=REPO_ROOT,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"manim skill failed: {proc.stderr[-800:]}")
        frames = sorted((out_dir / "frames").glob("*.png"))
    else:
        frames = render_animation_frames(spec, out_dir)
    preview = out_dir / "preview.mp4"
    # The shared wrapper supplies -hide_banner -nostdin -y and a timeout, so a hung encode
    # fails the stage instead of pinning the activity thread forever.
    ffmpeg(
        [
            "-framerate",
            str(spec.fps),
            "-i",
            str(out_dir / "frames" / "%04d.png"),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(preview),
        ],
        timeout=300,
    )
    frame_hashes = [sha256_hex(f.read_bytes()) for f in frames]
    return StageOutput(_hash_obj(frame_hashes), {"frames": len(frames), "renderer": executor})


def stage_qc_deliverable(ctx: StageContext) -> StageOutput:
    """Per-deliverable QC (17); every check is recorded and a failing layer fails the stage."""
    from dataclasses import asdict

    from content_factory.qc.accessibility import (
        check_artboard_accessibility,
        check_flashing,
        export_accessibility_report,
    )
    from content_factory.qc.delivery import check_delivery_promise
    from content_factory.qc.secrets import scan_deliverable

    spec = _spec(ctx)
    checks: dict[str, object] = {}
    a11y_results = {}
    # An unreadable plan is a finding, not an exception: aborting here would skip the credentials
    # scan below, the one QC line whose failure cannot be undone once the file leaves the machine.
    try:
        story = _story_plan_or_none(ctx)
    except ValidationError as exc:
        story = None
        checks["scene_kinds_implemented"] = {
            "passed": False,
            "facts": {"story_plan": "unreadable", "errors": len(exc.errors())},
        }
    if story is not None:
        # The renderer is total (missing column: zero, missing row: em dash), so a misspelled `y`
        # column draws a plausible flat chart of nothing. Check the figures against the datasets.
        from content_factory.qc.datarefs import plan_problems

        ref_problems = plan_problems(story, _project_datasets(ctx))
        checks["chart_values_match_datasets"] = {
            "passed": not ref_problems,
            "facts": {
                "scenes": len(story.scenes),
                "problems": [
                    {"scene_id": item.scene_id, "kind": item.kind, "detail": item.detail}
                    for item in ref_problems
                ],
            },
        }
        from content_factory.scenes.kinds import IMPLEMENTED_KINDS

        unimplemented = sorted({s.kind for s in story.scenes if s.kind not in IMPLEMENTED_KINDS})
        checks["scene_kinds_implemented"] = {
            "passed": not unimplemented,
            "facts": {
                "unimplemented": unimplemented,
                "scenes": len(story.scenes),
                "beats": [s.beat_id for s in story.scenes if s.kind not in IMPLEMENTED_KINDS],
            },
        }
        # A scene whose asset is missing from the bundle draws the same grey card. Only asked of a
        # lane that renders a bundle: a generative lane's shots supply pictures the bundle lacks.
        draws_its_own = (ctx.ddir() / "anchors" / "manifest.json").is_file() or (
            ctx.ddir() / "shots" / "plan.json"
        ).is_file()
        bundle_path = ctx.ddir() / "timeline" / "bundle.json"
        bundle = json.loads(bundle_path.read_text()) if bundle_path.is_file() else None
        if bundle is not None and not draws_its_own:
            have = set(bundle.get("assets") or {})
            wanted = [
                (sc.scene_id, str(getattr(sc, "asset_id", "")))
                for sc in story.scenes
                if getattr(sc, "asset_id", None)
            ]
            missing = [{"scene_id": sid, "asset_id": aid} for sid, aid in wanted if aid not in have]
            checks["scene_assets_present"] = {
                "passed": not missing,
                "facts": {
                    "scenes_naming_an_asset": len(wanted),
                    "in_the_bundle": len(have),
                    "missing": missing[:12],
                },
            }
        # A dangling `source_id`/`source_ids` prints the raw id as the citation, i.e. a fabricated
        # one (journal 2026-09-10). Unlike the assets check, no shot can ever supply a source.
        if bundle is not None:
            known = set(bundle.get("sources") or {})
            named: list[tuple[str, str]] = []
            for sc in story.scenes:
                one = getattr(sc, "source_id", None)
                if one:
                    named.append((sc.scene_id, str(one)))
                named.extend((sc.scene_id, str(s)) for s in getattr(sc, "source_ids", ()) or ())
            dangling = [{"scene_id": sid, "source_id": s} for sid, s in named if s not in known]
            checks["scene_sources_present"] = {
                "passed": not dangling,
                "facts": {
                    "scenes_naming_a_source": len(named),
                    "in_the_bundle": len(known),
                    "dangling": dangling[:12],
                },
            }
    leaks = scan_deliverable(ctx.project_dir, ctx.ddir(), ctx.deliverable_id)
    checks["no_credentials_in_output"] = {
        "passed": leaks.passed,
        "facts": leaks.facts,
        "findings": [asdict(f) for f in leaks.findings],
    }
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
        plan_path = ctx.ddir() / "timeline" / "plan.json"
        if plan_path.exists() and tl.audio:
            # Narration is the clock: cues off the measured voice by >2 frames are a hard failure.
            from content_factory.qc.drift import check_av_drift
            from content_factory.schemas.scenes import StoryPlan

            measured_plan = StoryPlan.model_validate_json(plan_path.read_text())
            drift = check_av_drift(tl, measured_plan.beats)
            checks["av_drift"] = {"passed": drift.passed, "facts": drift.facts}
        if spec.type in {"long_video", "short_video"}:
            from content_factory.qc.delivery import promised_delivery

            # Measure `composed.mp4` (the Remotion bundle lacks the generated motion), never
            # `final.mp4`: its burned-in captions would register as scene animation.
            composed = ctx.ddir() / "exports" / "composed.mp4"
            measured = composed if composed.exists() else video_path
            compose_manifest = ctx.ddir() / "exports" / "compose.json"
            routes_by_scene: dict[str, str] = {}
            route_counts: dict[str, int] = {}
            if compose_manifest.exists():
                manifest_json = json.loads(compose_manifest.read_text())
                for segment in manifest_json.get("segments", []):
                    routes_by_scene[segment["scene_id"]] = segment["route"]
                    route_counts[segment["route"]] = route_counts.get(segment["route"], 0) + 1
            promise = promised_delivery(getattr(spec, "intent", ""), route_counts)
            # On the mock a generated clip is a static test pattern, so "generated segments do not
            # move" would fail every offline hybrid run — measuring the backend, not the film.
            backends = {
                json.loads(marker.read_text()).get("backend")
                for marker in (ctx.ddir() / "video").glob("*/.done.json")
            }
            real = bool(backends) and "mock" not in backends
            delivery = check_delivery_promise(
                measured,
                tl,
                promised=promise,
                routes_by_scene=routes_by_scene,
                generated_for_real=real,
            )
            checks["delivery_promise"] = {
                "passed": delivery.passed,
                "facts": {
                    **delivery.facts,
                    "measured": str(measured.relative_to(ctx.ddir())),
                    "route_counts": route_counts,
                },
            }
            if not delivery.passed:
                a11y_results["delivery"] = delivery
    # An audio master is a deliverable too: ffmpeg measures loudness, true peak and dead air on it.
    # Severity decides — a missing stream or truncated master fails, a 2.5 s pause is only a fact.
    master_path = ctx.ddir() / "audio" / "narration-mastered.wav"
    if master_path.is_file() and master_path.stat().st_size > 0 and not video_path.exists():
        from content_factory.qc.audio import check_audio_in_video

        target_lufs = -14.0
        loudness_path = ctx.ddir() / "audio" / "loudness.json"
        if loudness_path.is_file():
            # What the mix was actually mastered to (audio-restore asks for -16), not the default.
            target_lufs = float(json.loads(loudness_path.read_text()).get("target_lufs", -14.0))
        spoken_ms = 0
        for segment in sorted((ctx.ddir() / "audio").glob("*.segment.json")):
            spoken_ms += int(json.loads(segment.read_text()).get("duration_ms", 0))
        audio_qc = check_audio_in_video(
            master_path, expected_speech_ms=spoken_ms, target_lufs=target_lufs
        )
        checks["audio_master"] = {
            "passed": audio_qc.passed,
            "facts": audio_qc.facts,
            "findings": [asdict(f) for f in audio_qc.findings],
        }

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
    """Originality gate (2.10): a blocking verdict against content memory fails the stage."""
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


# Where a deliverable's shippable files live, and what each one is *for* at a destination. Scanned
# in this order so the manifest reads the way a person would list it: the film first.
DELIVERY_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("video", "exports/final.mp4"),
    # The post chain's own output: the deliverable on a lane that ends there (`image-upscale`,
    # `video-finish`), an intermediate where the lane composes and `final.mp4` above wins.
    ("video", f"exports/{POST_CHAIN_EXPORT}"),
    ("video", "exports/generated.mp4"),
    ("image", "exports/artboard.png"),
    # A still lane's deliverable is the anchor itself: `single-image` draws a picture and stops;
    # without it the scan found only `qc/report.json` and `DeliveryPackage` rightly refused that.
    ("image", "anchors/anchor.png"),
    # `package_sequence` writes these beside `sequence/frames/`; on a set-of-stills lane they *are*
    # the deliverable (the `sequence` role). A lane that also cuts a film: see `_delivery_files`.
    ("sequence", "sequence/preview.mp4"),
    ("sequence", "sequence/contact-sheet.png"),
    ("audio", "audio/narration-mastered.wav"),
    ("caption", "captions/captions.srt"),
    ("caption", "captions/captions.vtt"),
    # What a transcribed recording said, in plain text: `text`, not `metadata`, because it is
    # content rather than a description of content.
    ("text", "audio/transcript.txt"),
    ("metadata", "audio/transcript.json"),
    ("metadata", "qc/report.json"),
    ("metadata", "exports/compose.json"),
)

_BUNDLE_EXPORT_ROLES: dict[str, str] = {".png": "image", ".mp4": "video", ".wav": "audio"}
"""What a render-bundle export is for, by extension. `.json` bundle sidecars have no role here and
are skipped rather than shipped."""

_DELIVERY_ROLE_ORDER: tuple[str, ...] = (
    "video",
    "image",
    "sequence",
    "audio",
    "text",
    "caption",
    "thumbnail",
    "metadata",
)
"""Reading order for the manifest: the thing being published first, what describes it last."""


def _delivery_files(ctx: StageContext) -> list:
    """Every shippable file this deliverable actually produced, with a digest for each."""
    import mimetypes

    from content_factory.schemas.delivery import DeliveryFile

    files = []
    seen: set[str] = set()
    # `sequence/preview.mp4` is an 8 fps proof reel of the same frames a cut film already carries,
    # so it ships only on a set-of-stills lane. Decided here because it depends on what else ran.
    cut_a_film = any(
        (ctx.ddir() / rel).is_file() for rel in ("exports/final.mp4", "exports/generated.mp4")
    )
    for role, relative in DELIVERY_CANDIDATES:
        path = ctx.ddir() / relative
        if not path.is_file() or relative in seen:
            continue
        if relative == "sequence/preview.mp4" and cut_a_film:
            continue
        size = path.stat().st_size
        if size == 0:
            # A zero-byte export is a failed render that left a file behind, and putting it in a
            # manifest would make it look like a deliverable.
            continue
        seen.add(relative)
        guessed, _ = mimetypes.guess_type(path.name)
        files.append(
            DeliveryFile(
                role=role,  # type: ignore[arg-type]
                path=relative,
                sha256=file_sha256(path),
                bytes=size,
                content_type=guessed or "application/octet-stream",
            )
        )
    # Per-card exports are collected by glob: the local runner writes `card_*.png`, and a campaign
    # run writes one file per render bundle (`bnd_*`). Scanning only the lane names found nothing.
    exports = ctx.ddir() / "exports"
    for path in sorted(exports.glob("card_*.png")) + sorted(exports.glob("bnd_*")):
        role = _BUNDLE_EXPORT_ROLES.get(path.suffix)
        relative = str(path.relative_to(ctx.ddir()))
        if role is None or relative in seen or path.stat().st_size == 0:
            continue
        # A muxed `<stem>.narrated.mp4` supersedes the silent `<stem>.mp4` it was made from;
        # shipping both would put two films in one package.
        name = path.name
        if (
            name.endswith(".mp4")
            and not name.endswith(".narrated.mp4")
            and (path.parent / f"{name[: -len('.mp4')]}.narrated.mp4").is_file()
        ):
            continue
        seen.add(relative)
        guessed, _ = mimetypes.guess_type(name)
        files.append(
            DeliveryFile(
                role=role,  # type: ignore[arg-type]
                path=relative,
                sha256=file_sha256(path),
                bytes=path.stat().st_size,
                content_type=guessed or "application/octet-stream",
            )
        )
    # `image-upscale` and `video-finish` end in a chain step, not an export. `chain.json` names the
    # final directory in `latest`, the one thing that knows which step was last.
    chain = ctx.ddir() / "sequence" / "chain.json"
    if chain.is_file():
        latest = Path(json.loads(chain.read_text()).get("latest", ""))
        if latest.is_dir():
            pngs = sorted(latest.glob("[0-9]*.png"))
            # One picture is an image; several are the frame set the `sequence` role is for.
            role = "image" if len(pngs) == 1 else "sequence"
            for path in pngs:
                try:
                    relative = str(path.relative_to(ctx.ddir()))
                except ValueError:
                    continue
                if relative in seen or path.stat().st_size == 0:
                    continue
                seen.add(relative)
                files.append(
                    DeliveryFile(
                        role=role,  # type: ignore[arg-type]
                        path=relative,
                        sha256=file_sha256(path),
                        bytes=path.stat().st_size,
                        content_type="image/png",
                    )
                )
    # Keep the documented reading order — the film first, metadata last — now that files arrive
    # from two scans rather than one ordered list.
    files.sort(key=lambda f: _DELIVERY_ROLE_ORDER.index(f.role))
    return files


def stage_compile_destination_packages(ctx: StageContext) -> StageOutput:
    """One typed `DeliveryPackage` per destination, with a digest per file."""
    import datetime as dt

    from content_factory.schemas.delivery import DeliveryPackage

    spec = _spec(ctx)
    files = _delivery_files(ctx)
    if not files:
        msg = (
            "compile_destination_packages found nothing to ship in"
            f" {ctx.ddir().name}: no export, no audio, no cards. Run the compose/render stage"
            " first — a package with no files is what this stage used to emit silently."
        )
        raise RuntimeError(msg)
    qc_path = ctx.ddir() / "qc" / "report.json"
    qc_passed: bool | None = None
    if qc_path.is_file():
        qc_passed = bool(json.loads(qc_path.read_text()).get("passed"))
    built_at = dt.datetime.now(dt.UTC).isoformat()
    packages = [
        DeliveryPackage(
            deliverable_id=spec.deliverable_id,
            destination=b.destination.platform,
            visibility=b.visibility,
            files=tuple(files),
            qc_passed=qc_passed,
            built_at=built_at,
        )
        for b in spec.destinations
    ]
    _write(
        ctx.ddir() / "destination-packages" / "packages.json",
        json.dumps([p.model_dump(mode="json") for p in packages], indent=1),
    )
    published = _publish_to_gallery(ctx, files)
    return StageOutput(
        # The digests, not the timestamp: two packages of the same bytes are the same package,
        # and hashing `built_at` would make this stage cache-miss on every run.
        _hash_obj([[p.destination, [f.sha256 for f in p.files]] for p in packages]),
        {
            "packages": len(packages),
            "files": len(files),
            "bytes": sum(f.bytes for f in files),
            "roles": sorted({f.role for f in files}),
            "qc_passed": qc_passed,
            **({"gallery": published} if published else {}),
        },
    )


def _publish_to_gallery(ctx: StageContext, files: Sequence) -> str | None:
    """Hard-link the run's film into one flat directory a person can browse."""
    from content_factory.deliverables.gallery import publish_film

    film = primary_film(files)
    if film is None:
        return None
    try:
        ctx.project_dir.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return None  # a run outside the checkout: not ours to publish
    return publish_film(
        name=ctx.project_dir.resolve().name,
        source=ctx.ddir() / film.path,
        gallery_dir=get_settings().gallery.dir,
        repo_root=REPO_ROOT,
    )


def primary_film(files: Sequence) -> DeliveryFile | None:
    """The one file that is the film, or None for a lane that cut none."""
    return next((f for f in files if getattr(f, "role", "") == "video"), None)


def stage_package_qc(ctx: StageContext) -> StageOutput:
    result = {"passed": True}
    _write(ctx.ddir() / "qc" / "package.json", json.dumps(result, indent=1))
    return StageOutput(_hash_obj(result), result)


def _log_execution(ctx: StageContext, unit: str, telemetry: dict | None = None) -> None:
    """One line per unit of real work."""
    log = ctx.project_dir / ".stages" / "executions.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    line = unit
    if telemetry:
        line += "\t" + " ".join(f"{k}={v}" for k, v in sorted(telemetry.items()))
    with log.open("a") as fh:
        fh.write(line + "\n")


STAGE_EXECUTORS = {
    Stage.ingest: stage_ingest,
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
    Stage.plan_shots: stage_plan_shots,
    Stage.route_shots: stage_route_shots,
    Stage.find_reference: stage_find_reference,
    Stage.compile_controls: stage_compile_controls,
    Stage.generate_anchor: stage_generate_anchor,
    Stage.lock_generation: stage_lock_generation,
    Stage.generate_keyframes: stage_generate_keyframes,
    Stage.drift_qc: stage_drift_qc,
    Stage.package_sequence: stage_package_sequence,
    Stage.transcribe_audio: stage_transcribe_audio,
    Stage.lock_script: stage_lock_script,
    Stage.synthesize_narration: stage_synthesize_narration,
    Stage.review_assets: stage_review_assets,
    Stage.review_frames: stage_review_frames,
    Stage.voice_over: stage_voice_over,
    Stage.sound_design: stage_sound_design,
    Stage.align_words: stage_align_words,
    Stage.compile_captions: stage_compile_captions,
    Stage.select_music: stage_select_music,
    Stage.restore_speech: stage_restore_speech,
    Stage.mix_audio: stage_mix_audio,
    Stage.compile_timeline: stage_compile_timeline,
    Stage.render_animation: stage_render_animation,
    Stage.render_scenes: stage_render_scenes,
    Stage.generate_video: stage_generate_video,
    Stage.fix_video: stage_fix_video,
    Stage.upscale_video: stage_upscale_video,
    Stage.interpolate: stage_interpolate,
    Stage.compose_video: stage_compose_video,
    Stage.qc_deliverable: stage_qc_deliverable,
    Stage.originality_gate: stage_originality_gate,
    Stage.compile_destination_packages: stage_compile_destination_packages,
    Stage.package_qc: stage_package_qc,
}
