"""Stage executors for the production workflow (phase 6). Every stage is deterministic given its
inputs, writes atomically into the project directory, and is safe to run twice. The workflow
caches by input hash (campaign + stage + dependency outputs + edit overlays), so a change to one
card invalidates exactly that card's render and nothing else."""

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
    # keys it knows through :meth:`param`; anything else stays inert. They are part of the node's
    # input hash, so turning a knob in the canvas re-runs exactly the stages it affects.
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
    """A list widget: a JSON array or a comma/space separated list; the setting when unset.

    The canvas has no list control, so a list arrives as text and the two spellings an operator
    would actually type both work. An empty widget means "the configured list", never "no items" —
    a blank text box is what a widget looks like before anyone touches it.
    """
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
        # person types and what YAML hands through as a plain string. Strip the brackets and treat
        # it as the separated list it plainly is, rather than reading "[image]" as one scene kind.
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
    """A claim set's identity, with the check DATE left out of it.

    ``checked_at`` is when the verification ran, not what it concluded. Folding it into the hash
    made every stage downstream of research re-run at midnight — a cache key that changes with the
    clock is not a cache key. What identifies a claim set is the statements, their verdicts, and
    the evidence and datasets they resolve to.
    """
    return _hash_obj(
        [{k: v for k, v in c.model_dump(mode="json").items() if k != "checked_at"} for c in claims]
    )


def stage_ingest(ctx: StageContext) -> StageOutput:
    """Every file in ``<project>/uploads`` into the artifact store as a typed source.

    The library this calls has existed and been tested since phase 4 (``ingest.uploads``: size
    caps, magic-number sniffing, an allowlist that never accepts SVG); what was missing was
    anything that called it, so ``data-story-video`` was the one lane the catalogue marks
    unfinished and every other lane's ``sources`` came from a committed fixture.

    Uploads are hostile input, so nothing here trusts a filename: the MIME is sniffed from the
    bytes, a declared extension from the dangerous set is refused whatever the sniff says, and a
    rejected file fails the stage by name rather than being skipped — a film built from three of
    an operator's four files, silently, is worse than one that stopped.
    """
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
    # A `<file>.transforms.json` sidecar declares how to shape the upload it sits beside. It is
    # metadata about an upload, not an upload: ingesting it would make it a source of its own and
    # compile it as a second dataset, which is exactly what happened the first time this ran.
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
    # never modified, and what goes into the artifact store is the converted copy the stages can
    # actually open. A file dropped through the browser was already converted at upload time;
    # this is the same step for one copied in by hand.
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
    """Sources, evidence and claims for the run.

    The real search/fetch/extract pipeline is opt-in (``execution.live_research``): it reaches the
    network, and the core suite must not. Without it this is the committed fixture — the same
    material every lane has used — plus whatever ``ingest`` put in the project, so an operator's
    own uploads are sources even on the offline path.
    """
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
    """Re-verify every claim against the evidence and datasets on disk, then gate the script.

    Two things were wrong here. It re-derived the *fixture* claims instead of reading what
    ``research`` wrote, so an operator's own sources could never reach it; and it gated
    ``sample_story_plan()`` rather than the plan this run is making, so the check passed on a film
    nobody was rendering. It also ran before ``plan_story``, which is the right place for the
    per-claim verification and the wrong place for the script gate — the script does not exist yet.
    So the verification stays here and the gate moved to ``lock_script``, where there is a script.
    """
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

    # The requirement compiler has set requires_independent_sources=2 on high-stakes claims since
    # it was written and nothing ever read it, so two outlets reprinting one press release counted
    # twice. This is where that number finally means something.
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
    """Uploaded CSV/JSON into typed ``DatasetTable``s through declared transforms.

    Returned ``sample_dataset()`` — the committed wind-power fixture — so a data-led film could
    only ever be about Swedish wind power whatever the operator uploaded. The transforms come from
    a ``<file>.transforms.json`` sidecar beside the upload, so the derivation lives with the data
    rather than inside a widget, and it is recorded next to the compiled table.
    """
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
    """A hand-authored story may bring its own evidence: ``<story>.datasets.json`` (a list of
    DatasetTable) and ``<story>.sources.json`` (a list of SourceCard) next to the fixture. They
    land where compile_timeline looks (``data/<id>.json``, ``research/source_cards.json``), so the
    charts and source cards of that story render its numbers, not the demo's."""
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
    # hand-authored film: every respelling the contracts can express was unreachable unless the
    # operator wrote the file into the run directory by hand.
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
    """Brand token overrides the story brought (``story/brand.json``): approved tokens only —
    paper, ink, accent — so a dark short is a three-line sidecar, not a theme fork."""
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
    """Files ``ingest`` put in the project, in the shape ``RenderBundle.assets`` takes.

    This is the plumbing that was missing, and it is why `image`, `screenshot`, `map` and
    `manim_asset` were unreachable from every lane: the scenes resolve `bundle.assets[asset_id]`,
    `ingest` wrote the mapping into ``research/uploads.json``, and nothing carried it across. A
    scene naming an asset that is not here still renders — as a card saying which id is missing —
    so a half-ingested project produces a film with a labelled hole rather than a crash.
    """
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
        # no English text-to-speech says and no English aligner spells. Without a respelling the
        # model produced "enner's aminahin sventzka craftnet" and the script gate refused the beat
        # at 0.22, so the lane that ships with this repo could not narrate its own demo film. The
        # plan is a code fixture, so its lexicon is one too.
        _write(
            ctx.project_dir / "story" / LEXICON_FILENAME,
            json.dumps(
                [e.model_dump(mode="json") for e in sample_story_lexicon()],
                indent=1,
                sort_keys=True,
            ),
        )
    # ``--subject`` is one sentence naming the film's world, which is exactly StoryPlan's
    # ``visual_subject``. Putting it on the plan is what carries it to both models: the shot
    # planner's state sentence and the video prompt compiler both read it from here, so the
    # operator says it once instead of once per stage. It wins over a fixture's own value: it is
    # the more specific instruction, given for this run.
    subject = _param(ctx, "subject").strip()
    if subject:
        plan = plan.model_copy(update={"visual_subject": subject[:400]})
    # ``beats`` is the lane's shape, and it was read in transcript mode only — so every other
    # lane's widget was decoration. `single-image` says `beats: 1` ("one beat is one image. More
    # beats would plan frames nothing draws") and planned the demo fixture's four;
    # `single-clip-post`
    # says 3 and planned four. It is a cap, not a demand: a five-beat script asked for seven beats
    # is a shorter film, not a failure, so the shortfall is recorded in the facts rather than
    # raised — but a plan longer than the lane can use is trimmed here instead of downstream.
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
    # editorial-arc outline and per-short vertical plans. Shorts are re-edited excerpts, never
    # crops: each derived plan re-runs narration/alignment/captions through its own branch.
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
    """The transcript ``transcribe_audio`` wrote for this deliverable, when there is one.

    ``plan_story`` is a *shared* stage: the campaign compiler runs one of it for the whole run, so
    it can arrive with no deliverable at all — and a transcript belongs to a deliverable's audio
    folder. No deliverable therefore means no transcript rather than an assertion, which is what
    the documentary tests found the moment this branch existed.
    """
    if ctx.deliverable_id is None:
        return None
    path = ctx.ddir() / "audio" / "transcript.json"
    return path if path.exists() else None


def _story_plan_from_recording(ctx: StageContext, path: Path) -> tuple[StoryPlan, dict]:
    """The plan a recording dictates: beats are spans of what was actually said.

    Precedence matters here and is deliberate. A named ``story`` fixture still wins, because an
    operator who names a plan means it; failing that, a transcript on disk beats both the script
    writer and the demo fixture, because a lane that transcribed a recording and then planned a
    film about Swedish wind power would be the single most confusing thing this pipeline could do.

    ``beats`` is the number of drawings. It is the one knob here that changes the film: each beat
    becomes one shot, one drawing and one held span of the recording, so asking for four beats out
    of a three-minute interview is asking for four pictures that hold for forty seconds each.
    """
    from content_factory.audio.transcribe import TRANSCRIBE_VERSION, story_plan_from_transcript
    from content_factory.schemas.audio import SpeechTranscript

    cfg = get_settings().transcription
    transcript = SpeechTranscript.model_validate_json(path.read_text())
    spec = _spec(ctx)
    width, height = aspect_dimensions(aspect_or_portrait(getattr(spec, "aspect", None)))
    fps = int(getattr(spec, "fps", 24) or 24)
    subject = _param(ctx, "subject").strip()
    # A plan made from a recording has no world of its own, and `visual_subject` is the only thing
    # that tells the image model what the film is *of*. Left empty, `plan_shots`' presets were the
    # only content in the prompt, so a recording explaining why the sky is blue came back as six
    # drawings of an unnamed man standing on open ground. The recording's opening line is a poor
    # substitute for an operator's sentence and a far better one than nothing: it is at least about
    # the thing that was said. `--subject` still wins, and the facts say which was used.
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
    """The local writer's plan, or a named failure. Never a silent fallback to the fixture.

    Falling back would be the worst of both: an operator who turned the writer on and got the demo
    film would have no way to tell, and the run would look like it worked.
    """
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
    # What the model call cost. The gateway has always returned tokens, wall clock and dollars and
    # every caller kept only the alias, so a run said which model wrote the copy and never what it
    # cost — the numbers that matter with a local 8B model sharing the card.
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
    """One artboard per card. A carousel's cards are its copy; a film's cards are its beats.

    ``write_copy`` writes a ``cards`` list only when the deliverable is a **carousel** — for
    anything else it writes a caption and an alt text. This stage read ``copy["cards"]``
    unconditionally, so `data-story-video`, whose deliverable is a video, died on a bare
    ``KeyError: 'cards'`` at stage 13 of 22, every run. The beats are the right source there:
    they are what `card_stills` renders and what the timeline cuts.
    """
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
    """Which story plan this deliverable's stages read, or None when nothing has been planned.

    A documentary campaign is one long video plus N shorts, and ``plan_story`` already writes a
    derived vertical plan per short to ``story/shorts/<deliverable_id>.plan.json``: a re-edit, never
    a crop, with the measured timings reset so narration, alignment and captions regenerate for
    that short and with a rewritten hook. Nothing consumed them. Every per-deliverable stage
    resolved ``story/plan.json``, so all N shorts narrated the LONG plan and came out as N copies
    of the same film at a different aspect ratio — the limitation recorded at STATUS 528 as "the
    lane's next smallest task".

    Preferring the short's own plan here is the whole fix: ``lock_script``,
    ``synthesize_narration``, ``align_words``, ``compile_captions``, ``compile_timeline``,
    ``plan_shots`` and ``find_reference`` all read the plan through this one function, so the
    playbook's episode-to-shorts recipe becomes ``make`` on a documentary campaign with no new lane
    and no new contract.
    """
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
    """Gate every character asset a shot uses on a reviewed, approved sculpture.

    Deterministic checks run over the turnaround the build already produced, and a contact sheet
    is written for a person to look at. The stage **blocks** while any asset is unapproved: an
    approval binds to the built mesh's digest, so rebuilding an asset withdraws it. Passing
    measurements is necessary and never sufficient — "this is a plausible human" is not something
    the checks decide.
    """
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
    # Whether this film sends an identity reference at all. If it does, a character with no styled
    # sheet for the film's style has nothing to fill that slot with — and the count of slots is
    # what selects the editing recipe upstream, so a silently empty one changes the whole run.
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
    """One ShotSpec per story beat (camera preset by scene kind), or a hand-authored fixture plan.
    Deterministic: the plan hash is the stage output, so downstream re-runs only on shot changes."""
    cfg = get_settings().shots
    planner = _param(ctx, "planner", cfg.planner)
    # The story's rate is the master rate. The setting is only the fallback for a lane that plans
    # shots without a story; taking it as the default meant a 30 fps story silently produced 24 fps
    # shots, because ShotSettings.fps is 24 and nothing compared the two.
    story = _story_plan_or_none(ctx)
    default_fps = story.fps if story is not None else cfg.fps
    if planner == "fixture":
        plan = load_fixture_plan(REPO_ROOT / _param(ctx, "fixture_path", cfg.fixture_path))
    elif planner == "reference":
        # Stage the beats from whatever find_reference chose. With no selection on disk this falls
        # through to the preset planner rather than failing: a lane must still run on a machine
        # with no reference library.
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
    # Which styled identity sheet each character's anchor will be conditioned on. Named on the
    # PLAN, by digest, rather than looked up at anchor time by style: a sheet rebuilt in the same
    # style is a different picture, and a plan has to say which one it was made against.
    plan = _attach_identity_sheets(ctx, plan)
    # Every shot in a plan shares one rate (ShotPlan enforces it), so one comparison covers the
    # film. Refused here rather than reported, because there is no honest way to run it: the
    # conform is lossy and invisible in the facts.
    if story is not None and plan.shots[0].fps != story.fps:
        msg = (
            f"the story is {story.fps} fps and this shot plan is {plan.shots[0].fps} fps."
            f" {FPS_MASTER_HINT}"
        )
        raise RuntimeError(msg)
    _write(ctx.ddir() / "shots" / "plan.json", plan.model_dump_json(indent=1))
    # Measured off the finished plan, not predicted, and reported rather than fixed: a shot under
    # the cliff renders its control passes and then has them ignored by the image model. The cause
    # is a frame too narrow to hold the cast at a legible size, so the answer is a wider frame or a
    # closer-standing clip. Neither is the planner's call to make silently.
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
    """The film's style prompt, resolved. One string for the whole film: the anchors and the
    identity sheets have to be in the same style or the reference fights the prompt."""
    cfg = get_settings().image_sequences
    return resolve_style(_param(ctx, "style", cfg.anchor_style_prompt))


def _attach_identity_sheets(ctx: StageContext, plan: ShotPlan) -> ShotPlan:
    """Put each character's styled sheet digest on its ``CharacterSpec``, when one is built.

    Absent sheets are not an error here. Whether a film *needs* them is
    ``anchor_references``' business, and ``review_assets`` is the gate that refuses to run a lane
    asking for an identity slot that nothing can fill — a plan is not the place to decide it.
    """
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
    """Hybrid workflow: decide per story beat whether Remotion renders it (D3 / Vega-Lite /
    MapLibre / Manim scenes) or the generative chain produces it (Blender controls -> HiDream ->
    LTX-2.5). A scene-kind table plus per-beat overrides from ``routing`` settings; the routing hash
    is the stage output, so downstream re-runs only when a decision changes."""
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
    """The first match that is a retargeted mocap clip on disk, or None.

    The one predicate that decides whether retrieval changes a film or only describes one. It lives
    here and in ``shots.planner.plan_shots_from_reference``; they must agree, and a test says so.
    """
    from content_factory.shots.planner import CLIPS_DIR

    for match in matches:
        clip = getattr(match, "clip_id", "") or ""
        if clip and (CLIPS_DIR / f"{clip}.json").is_file():
            return clip
    return None


def stage_find_reference(ctx: StageContext) -> StageOutput:
    """Ask the reference library, in the story's own words, for the real interaction to stage from.

    Writes ``reference/selection.json``: one ``ReferenceMatchSet`` per story beat, carrying the
    ranked clips, the terms the query was understood to mean, and the terms that are understood and
    known to be missing from every source on disk. ``plan_shots`` reads it to name a mocap clip on a
    character, and ``generate_anchor`` can read it for a reference frame.

    An absent library selects nothing rather than failing. The library is host-specific and 19 GB,
    so a machine without it must still be able to run every lane: the honest outcome is a selection
    file that says the library was not found, not a broken run. This mirrors ``select_music``, which
    behaves the same way when no music library is present.
    """
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
                # answers "will this stage?" on its own. Same predicate plan_shots_from_reference
                # applies, and a test pins that they agree.
                "stageable": _first_stageable(result.matches) is not None,
                "stageable_clip": _first_stageable(result.matches),
            }
        )

    selection["beats"] = beats
    selection["absent_terms"] = sorted(absent_terms)
    # How many beats found a clip that can actually drive a rig, which is a different number from
    # `selected` and the one that decides whether `plan_shots` stages anything.
    #
    # The distinction was invisible and it cost days. Three runs on this host recorded
    # `selected: 0` and nothing else, and that one number covers three unrelated situations: the
    # library is not installed; the library is fine but the story has no people in it (all three
    # of those runs were a Rayleigh-scattering explainer — "Sunlight is white. It carries every
    # colour at once."); or the search matched well and every match was an SBU sequence, which is
    # a skeleton to look at and not something a character can be posed from. Only 58 of the 15789
    # indexed clips are retargeted, so the third case is common and reads exactly like the first.
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
    """Per-frame control passes as ControlBundles. ``controls.compiler`` selects the builtin 2D
    MotionPlan renderer or the Blender scene controller; both write the same bundle layout."""
    compiler = _param(ctx, "compiler", get_settings().controls.compiler)
    controls_dir = ctx.ddir() / "controls"
    if compiler == "motion_plan":
        plan, _instructions = _sequence_motion_plan(ctx)
        shot_plan_path = ctx.ddir() / "shots" / "plan.json"
        if shot_plan_path.exists():
            # Shots planned (hybrid / blender workflows on mock backends): one bundle per routed
            # shot, keyed by the shot id, so anchors, clips and the compose step line up exactly as
            # they do with the Blender compiler. Anchor at frame 0 only: the 2D plan is a stand-in.
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
    # measured from. Splitting them into two widgets would let a lane ask for a depth reference
    # whose range nothing had measured.
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
    """Refuse a Blender plan whose figures nobody has described.

    The mesh carries a body and nothing else, and the depth and normals passes are renders of
    that bare body — so the image model, which draws what it is shown, draws a bare body. Measured
    on `picture-story` (2026-09-10): ten anchors of a **grey untextured mannequin in a T-pose**
    standing in a desert, 22 minutes of GPU, every frame unusable. It is the same failure ADR-0004
    records for identity references, arriving through the control passes instead.

    `CharacterSpec.appearance` is the field that fixes it and it is optional, so the refusal is
    here rather than in the contract: a shot plan with no figures at all is fine (that is most
    lanes), and one with figures nobody described cannot produce a picture worth the wait.
    """
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
    """One Blender skill run per shot, cached by shot hash + compiler settings. The skill writes
    its passes straight into ``controls/<shot_id>/``; the bundle builder verifies every digest and
    renders the pose/layout PNG tracks content-factory owns."""
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
        # The node's toggles decide which passes Blender renders. Sending a pass costs real time
        # per frame and, further down, real prompt influence — the image model draws what it is
        # shown — so a lane that wants skeleton and depth only should render skeleton and depth
        # only. The ShotSpec's own passes are the default when no toggle was set.
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
    """Whether two service URLs name the same server, ignoring trailing slashes and the
    scheme's default port."""
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
    """The local GPU tenant a backend talks to (None for mocks, and None for another machine's).

    Started lazily — only when a stage is about to generate something that is not cached — so a
    fully cached rerun never evicts whatever the other tenant is doing on the card.

    The endpoint check is not a detail. ``hidream_endpoints`` is a pool, and a pool entry pointing
    at the second box is not this machine's to start: matching on type alone meant every run
    dispatched to the remote host *also* booted the local server and left 17-19 GB resident on a
    card the run never touched. Measured 2026-09-10: a `single-image` run whose pool was
    `["http://100.82.150.94:8801"]` recorded `vram_before_mib=18994` locally, and the
    `silent-video` running beside it died at `sound_design` because MMAudio met 15.5 GiB of
    somebody else's weights. Only the endpoint `services.local` manages is ours to bring up.
    """
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
    # The one moment this run is about to put weights on the local card, and therefore the one
    # place worth asking whether the card is still there. A GPU that has fallen off the bus is
    # not a retryable condition — the driver's own recovery action is a reboot — and without
    # this the failure arrives later wearing someone else's clothes: ComfyUI answering
    # "All connection attempts failed", or SeedVR2 placing its VAE on the CPU and dying on
    # "device cpu:0 is invalid". A queue would spend every remaining job finding that out.
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
    "comfy-fixture": (
        "the fixture ComfyUI package renders an empty image and takes no references; it exists to"
        " prove the ComfyUI submit/collect path, not to draw anchors. Use mock for an offline run."
    ),
}
"""Options the widget offers that nothing can execute. Refused by name at the top of the stage,
before a GPU tenant is started, rather than silently falling back to the mock and delivering a
film of grey rectangles."""


def _anchor_backend_name(ctx: StageContext | None) -> str:
    """Which reference-edit backend draws the anchors.

    Three sources reach here and the order between them is the whole design:

    1. the node's ``backend`` key, which the local runner and the offline tests inject for one run;
    2. ``image_sequences.backend`` **when it was explicitly configured** — a machine that has said
       "no GPU here, use the mock" has to be obeyed, or that setting is unenforceable on any lane
       that pins a model;
    3. the ``model`` widget, which is what a lane definition freezes ("this lane is a HiDream
       lane"). It was inert before: three lanes declare ``model: hidream-o1`` and every one of them
       drew mock rectangles unless the operator also knew to set the setting;
    4. the setting's own default.

    ``model_fields_set`` is what separates 2 from 4, and it is the only honest way to: a configured
    ``mock`` and a defaulted ``mock`` are the same string and mean different things.
    """
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
    """``width`` x ``height`` scaled to a pixel budget, aspect held, both sides a multiple of 32.

    Zero or less means "leave it alone", which is the default: with a shot plan the size is the
    ShotSpec's and nothing here should overrule it. HiDream-O1 snaps to its own ~4 MP buckets by
    aspect ratio whatever it is asked for, so this only bites on the backends that honour a size.
    """
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

        # The pool's first host, not the singular endpoint. They are the same value whenever no
        # pool is configured (`hidream_pool()` falls back to it), so a single-machine run is
        # unchanged — but a pool that deliberately names only *other* machines, because this card
        # is doing something else, used to have its anchor drawn here anyway, starting a local
        # server the operator had just configured out of the run.
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
    """One backend per configured GPU host, for spreading a sequence's frames across machines.

    A pool of one is the default everywhere and makes ``build_sequence`` take its serial path, so
    a single-machine run is unchanged. The anchor is still made by ``_reference_backend``: it is
    one picture, every frame depends on it, and there is nothing to spread.
    """
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
        # Each host gets its own scratch dir. The reference files are content-addressed, so two
        # hosts would write identical bytes to one path -- identical, but not atomically, and a
        # half-written reference is a corrupt frame rather than a slow one.
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
    # The lock names what actually made the picture, because it is what a rerun is checked
    # against: a frame generated by one model must not be served from a cache entry another model
    # wrote, and the steps and guidance below mean different things per recipe.
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
    """The style leads, then the subject, then the shot's one-instant state.

    Order is not cosmetic here. With the style clause last — behind the subject and two sentences
    of staging — the image model ignored it: asking for a flat, gradient-free ukiyo-e woodblock
    returned the same heavy-outline photoreal idiom as every other style. The identical words moved
    to the front produced an actual woodblock. Same prompt, same reference, same seed; only the
    position changed. Anything appended after the style dilutes it, so keep the style first.

    ``motion_prompt`` is deliberately absent. An anchor is one still frame, and the progression
    ("the camera pushes slowly in") is an instruction the still cannot carry out: asking for it
    while generating a frame is how a static image acquires motion blur and a second pair of arms.
    The progression belongs to the clip, where ``shots.prompt_compile`` compiles it.

    The **world** is the StoryPlan's ``visual_subject`` — "one sentence naming the film's world,
    for the image and video models only", by the field's own definition. It reached the shot
    planner and the video prompt compiler and never this stage, while the node's ``prompt`` widget
    on these lanes is a *framing* instruction: "the subject alone, centred, plain background, the
    reference view of the set". Framing with no subject in it is a prompt that asks the model to
    invent one, and it does — an `image-set` run of a deep-sea documentary came back as a
    character line-up of three strangers in coats, because the style clause said "consistent
    character design" and nothing said what the picture was of. The two clauses are complementary,
    so both go in: what it is, then how it is framed. ``--subject`` writes to both places, so an
    exact repeat is emitted once.
    """
    framing = _param(ctx, "prompt").strip()
    world = ""
    story_path = _story_plan_path(ctx)
    if story_path is not None:
        world = (StoryPlan.model_validate_json(story_path.read_text()).visual_subject or "").strip()
    # The shot's state sentence is built by `shots.prompt_compile.state_sentence`, which already
    # embeds the story's `visual_subject` — so appending the world clause as well said the whole
    # thing twice. Measured on `audio-picture-story` (2026-09-10): 258 of a 480-character prompt
    # were one 129-character world sentence, printed twice, and the six drawings that came back
    # were six near-copies of the same vista — the camera clause is four words against two copies
    # of a long one and lost. The dedup below could not see it, because it compares whole clauses
    # and the repeat is a substring of a longer one.
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
    """The character's styled identity sheet, addressed by the digest the plan named.

    Never the turnaround. HiDream-O1's IP pipeline treats every reference as subject material, so
    the clay render makes it draw clay people and the untextured MPFB turnaround makes it draw a
    nude mannequin — both measured (STATUS 1339, 1379-1381). This used to send exactly that clay
    front view, which is why ``anchor_references`` could not include ``identity`` at all.

    Addressed by ``CharacterSpec.reference_image_sha256`` rather than by style, so a sheet rebuilt
    in the same style is a different picture and is not served for a plan made from the old one.
    """
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
    """The references for one frame, in the order upstream's IP pipeline expects: identity
    references (each with its layout box) first, then structural passes.

    ``wanted`` names the passes; ``identity`` opts the character sheets in. Sending a pass costs
    more than nothing — the model draws what it is shown — so the default is the OpenPose skeleton
    plus depth. See ``ImageSequenceSettings.anchor_references``.

    The second return value is what each slot actually carried: ``{slot, role, subject_id, sha256,
    box}``, in order. It is written into the anchor's marker because the reference *count* alone
    could not answer the question that mattered — upstream branches on ``len(ref_images) == 1``, so
    the number of slots selects the whole editing recipe, and a run whose identity sheet was
    missing silently became a one-reference run on a different scheduler.
    """
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
    # A skeleton of nobody is not conditioning, it is a scheduler switch. The docstring above
    # names the trap and this is the other way into it: a shot that stages no figures still gets a
    # `pose_skeleton` track compiled, and sending it made the anchor a **one-reference** request,
    #
    # The condition is the *shot's* character list, not the bundle's subject list, and getting
    # that wrong first is instructive. `ps1-pinecone`'s bundle does carry a subject — labelled
    # "one open pine cone on a plain grey slate" and carrying `subject_id: subj_hands0001` with
    # joints for two wrists and two index fingers. That is the motion_plan compiler's **builtin
    # hand-gesture fixture**, the one `image-set`'s own caveat warns about ("its per-image layout
    # is that plan's hand-gesture fixture, not the views the brief asks for"). So every object
    # subject gets a skeleton of two hands drawn over it and then sent as the anchor's reference.
    # `shot.characters` is empty for these lanes and is the honest signal.
    # which is exactly `is_editing` upstream. That routes the dev recipe onto `flow_match`, and
    # `skills/image/hidream/server.py` has the measurement for what that costs: speckle at 0.175
    # of pixels above a luma gradient of 60, against 0.0004 on `flash`.
    #
    # Measured on `audio-picture-story` 2026-09-10: "one open pine cone on a plain grey slate"
    # came back six times covered in white speckle, `references: 1`, the single slot a pose
    # skeleton of no one. The same prompt with no references at all renders clean on the same
    # server, at either aspect — which is what finally isolated it after three wrong diagnoses of
    # my own (paper texture from the style, the environment clause, the machine). The skeleton
    # carried no information about a pine cone and its only effect was to pick the noisier
    # scheduler.
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
    """Evict the managed GPU tenants (and the Ollama models) before a skill loads its own weights.

    ``_ensure_backend_ready`` covers the two stages that talk to a *managed* server. It does not
    cover the ones that load a model inside their own uv environment through a subprocess — the
    post chain (Cutie, ProPainter, SeedVR2, RIFE), ``sound_design`` (MMAudio, Stable Audio) and
    ``restore_speech`` (Resemble Enhance, ClearerVoice). Those went straight to torch, so nothing
    had stopped HiDream or ComfyUI and the second load met a card with 19 GB already resident.

    Only ever called on the uncached path, and only once per stage: a fully cached rerun must not
    evict a tenant somebody else is using to produce nothing.
    """
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
    """`""` when the GPU is there (or there never was one), else what nvidia-smi said.

    Deliberately not "is there a GPU": a machine with no NVIDIA driver at all is a normal offline
    machine and every mock backend works on it. This is the narrower question of whether the
    driver is present and has *lost* the device, which is not a condition any amount of retrying
    fixes — the driver's own recovery action for it is a reboot.
    """
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
    """What is on the card right now, in MiB, or None where there is no nvidia-smi.

    One number, taken before and after each uncached generation. It is the measurement that was
    missing every time this repo hit an out-of-memory failure: the record said which stage died
    and never what was resident when it started, so "Ollama was still holding the text model" was
    a hypothesis for weeks (STATUS 1213, 1361, 1380, 1657) rather than a number in a marker.
    """
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
    """What one uncached generation cost, in the marker's own words.

    ``vram_before_mib``/``vram_after_mib`` are the card, ``seconds`` is the stage's wall clock, and
    ``server_elapsed_s`` is what the model server itself reported. Keeping both timings is the
    point: they disagreed by a factor of three on the thirty-anchor run and the gap was recorded as
    "unexplained" because nothing held the two numbers side by side.
    """
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
    """The deterministic frame checks, reduced to the ones that mean "do not ship this".

    Only blockers. The advisory findings (tonal collapse, black clipping, edge intrusion,
    background churn) are for a person reading the contact sheet: they mean "look at this", and
    regenerating on them would spend GPU hours chasing a threshold that was deliberately set loose.
    """
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
    """Generate an anchor until the blocker checks pass.

    Returns ``(png, attempts, seed, telemetry)``.

    Before this, ``generate_anchor`` wrote whatever came back and the deterministic checks ran only
    later, inside ``review_frames``, where a person was already looking. So a thirty-anchor run
    spent three GPU hours and then showed a reviewer thirty frames, four of which had a style
    instruction half-applied — findings the code could have seen the moment each frame arrived.

    Regeneration follows the pattern ``sequences.engine.build_sequence`` already uses for
    keyframes: a new seed per attempt, derived rather than random, so the second attempt is the
    same second attempt on every machine and on every rerun. ``lock.seed`` is attempt 1, which
    keeps a frame that passes first time byte-identical to what it was before.

    ``seed_offset`` moves that whole ladder, and is how a frame a *person* rejected comes back
    different rather than identical — :func:`_anchor_rejection_offsets` sets it from how many times
    the frame has been turned down. It is 0 for a frame nobody has rejected, so the ordinary path
    is unchanged byte for byte.

    When the attempts run out this raises :class:`BlockedError` rather than writing the last one:
    a fourth attempt is more GPU time for the same answer, and the answer is that a person has to
    look. Every rejected candidate is kept on disk for them to look AT.
    """
    import time

    cfg = get_settings().image_sequences
    expect_monochrome = any(w in (lock.style_prompt or "").lower() for w in MONOCHROME_WORDS)
    candidates: list[dict] = []

    # Every host that can draw this, the given one first. A frame already falls back to another
    # card when one goes away (`WorkerPool`), and the anchor did not: measured on this host, the
    # local HiDream was killed by another tenant asking Ollama for a model and `silent-video`
    # failed at stage 4 of 12 with "server unreachable", with a healthy second host in the pool.
    # The anchor is one picture and every frame depends on it, so losing it loses the run.
    # Deduplicated by endpoint, not by identity: `_reference_backend` and `_reference_backends`
    # build separate client objects for the same URL, so an identity check left the first host in
    # the list twice and every anchor paid two connection failures to a dead server instead of one.
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
    """The operator's own still as the anchor, drawn by nobody.

    ``image-to-video``'s caveat used to read "no node type ingests a supplied still, so starting
    from an image you already have is not expressible in the graph today". This is that node
    behaviour: with ``source: upload`` the stage adopts the picture in the run's uploads folder
    instead of generating one, writes it as ``anchors/anchor.png`` and records the same manifest a
    generated anchor writes — so ``generate_video`` moves the operator's photograph with no
    knowledge that it was not drawn here, and no prompt, seed or model is consulted at all.
    """
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
    """``frame_id`` -> (png, marker, a filename-safe key), or None if it is not an anchor's.

    ``review_frames`` builds anchor ids as ``<shot_id>:<frame index>`` from the manifest, and the
    single-anchor branch records ``shot_id: None`` — so that lane's frames really are called
    ``None:0000`` on disk today. Both spellings are accepted here rather than only the tidy one,
    because a verdict already written against the old id has to keep working.
    """
    shot, _, tail = frame_id.rpartition(":")
    if not tail.isdigit():
        return None
    idx = int(tail)
    if shot in ("", "anchor", "None"):
        return anchors_dir / "anchor.png", anchors_dir / "anchor.done.json", f"anchor_{idx:04d}"
    png = anchors_dir / shot / f"{idx:04d}.png"
    return png, png.with_suffix(".done.json"), f"{shot}_{idx:04d}"


def _clear_rejected_anchors(ctx: StageContext) -> list[str]:
    """Drop the cache markers of anchors a person rejected, so the next run redraws them.

    The keyframe lanes have had this since 2026-09-10 (:func:`_clear_rejected_frames`). The anchor
    lanes did not, and the consequence was the same one, measured on `amber-refix` 2026-09-12:
    three of six frames rejected with reasons, the resume reported ``cache_hits: 6`` on
    generate_anchor, and the gate blocked again on the identical pictures. An operator who rejected
    a frame was stuck for ever unless the prompt changed and moved the input hash — which means the
    honest verdict the gate exists to collect was the one thing it could not act on.

    Only the marker is removed. The picture moves to ``anchors/rejected/``, so what was turned down
    can still be looked at, and the frame is named in the stage's facts, because a redraw nobody
    can see in the record is a redraw nobody can audit.
    """
    from content_factory.services.frame_reviews import current_batch

    # The MERGED state, not `batch.json` alone. `batch.json` is what the gate wrote when it last
    # ran; `verdict.json` beside it is what the operator answered afterwards, and the answer is the
    # whole point. Reading only the first works the first time and then silently stops: measured
    # here 2026-09-12, a second rejection reported `cache_hits: 6` because at the moment this runs
    # the gate's file still described the *previous* round, in which those frames were unreviewed.
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
    """``frame_id -> what the reviewer said it should show instead``, for frames they rejected.

    This is the half a seed offset cannot do. Moving the seed makes a redraw *different*; it does
    not make it different in the direction that was asked for, and measured on `amber-refix`
    2026-09-12 that is exactly what happened — of three frames rejected for having an open rim,
    two came back solid and the third came back a corked bottle, which is further from amber than
    what it replaced.

    The reviewer's ``reason`` is a complaint and cannot be sent to an image model as one: these
    weights run at guidance 0, where `sequences.styles` has already measured that a negation is a
    hint the model may or may not take. So the contract carries a separate ``redirect``, stated
    positively, and that is what goes into the prompt. A rejection with no redirect still redraws —
    it just redraws on a new seed alone, which is the behaviour this replaces.
    """
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
    """``prompt`` with the reviewer's corrections appended, as descriptions of the picture.

    Appended, not prepended: `_anchor_prompt` documents that the style leads and that anything
    after it is weaker, and a correction is about the subject rather than the medium. Each one is
    already phrased positively — that is what the contract asks the reviewer for — so they join the
    sentence list as they are rather than being wrapped in "avoid" or "not", which at guidance 0
    is a hint rather than an instruction.
    """
    extra = [r.strip().rstrip(".") for r in redirects if r.strip()]
    if not extra:
        return prompt
    return prompt.rstrip().rstrip(".") + ". " + ". ".join(extra) + "."


def _applied_redirects(anchors_dir: Path, key: str, marker: Path, pending: str) -> tuple[str, ...]:
    """The corrections this draw is made with.

    A frame whose marker survived was not rejected, so it keeps exactly the set it was drawn with
    and its input hash is unchanged — otherwise every untouched frame would regenerate the moment
    any frame in the set was corrected.
    """
    if marker.exists():
        try:
            return tuple(json.loads(marker.read_text()).get("redirects", ()))
        except (OSError, ValueError):
            return ()
    return _redirects_for_frame(anchors_dir, key, pending)


def _redirects_for_frame(anchors_dir: Path, key: str, pending: str) -> tuple[str, ...]:
    """Every redirect that applies to the next draw of one frame, oldest first.

    Sticky, and derived from files rather than recomputed from the verdict each time — which is
    what keeps the prompt stable across a resume. A redirect read straight from the verdict would
    vanish the moment the frame was redrawn (the new digest makes it `unreviewed` again), the
    prompt would revert, the input hash would revert with it, and the frame would regenerate
    *without* the correction it had just been given. Same failure shape as reading `batch.json`
    instead of the merged state, one layer down.

    So each draw records the set it was made with, that marker moves aside when the frame is
    rejected, and the next draw reads the most recent one and appends whatever is newly pending.
    Corrections accumulate: "a solid lump with no opening" and then "deeper orange" both hold.
    """
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
    """How far to move each anchor's seed, from how many times it has been turned down.

    Same arithmetic as :func:`_rejection_seed_offsets`, and for the same measured reason: a redraw
    has to come back *different*, and the backend derives its seed from the attempt number
    (``lock.seed + attempt - 1``), so redrawing without moving the base reproduces the picture that
    was just rejected. The multiplier is 8 because the three blocker retries inside one run already
    walk the seed by 0, 1, 2 — an offset of one would land the redraw on the second attempt of the
    run that was rejected.

    Counted off the files in ``anchors/rejected/``, so it survives a crash and needs no extra state.
    """
    reject_dir = anchors_dir / "rejected"
    if not reject_dir.is_dir():
        return {}
    offsets: dict[str, int] = {}
    for png in reject_dir.glob("*.reviewed*.png"):
        key = png.name.split(".", 1)[0]
        offsets[key] = offsets.get(key, 0) + 1
    return {key: n * 8 for key, n in offsets.items()}


def stage_generate_anchor(ctx: StageContext) -> StageOutput:
    """Anchor images. With control bundles present: one anchor per shot per ``anchor_frames``
    index, conditioned on the Blender passes (identity refs + layout boxes + rough RGB + skeleton).
    Without controls: a single anchor from the brief. Each anchor is cached by its input hash."""
    if _param(ctx, "source", "generate") == "upload":
        return _anchor_from_upload(ctx)
    backend = _reference_backend(ctx)
    warmed: set[str] = set()
    # A verdict is only worth collecting if the run can act on it. Clearing first means a rejected
    # anchor misses its cache below; the offsets then make sure what comes back is a different
    # picture rather than the one that was turned down.
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
                        # The exact words the model was given. It is already inside input_hash, so
                        # this adds nothing to caching — it is here so a frame can be read back
                        # against what was asked for without re-deriving the compiler's tables.
                        "prompt": frame_prompt,
                        # The reviewer corrections folded into that prompt, in the order they were
                        # given. Recorded rather than recomputed so the next draw can carry them:
                        # see `_redirects_for_frame`.
                        "redirects": list(redirects),
                        "png_sha256": sha256_hex(png),
                        "backend": backend.name,
                        "references": len(cond.reference_pngs),
                        "reference_kinds": list(references),
                        # What each slot carried, in order. The count decides the editing recipe
                        # upstream, so "which slots were filled" is not a detail.
                        "reference_slots": slots,
                        "layout_boxes": len(cond.layout_boxes),
                        # What the model actually returned. HiDream-O1 snaps to its own ~4 MP
                        # resolution buckets by aspect ratio, so the lock's width/height is a
                        # request, not a promise — record the delivered size rather than implying
                        # the requested one came back.
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
        # The story's frame, not the settings' default. A plan carries width and height because
        # the film has a shape, and this branch read `default_working_resolution` (16:9) whatever
        # it said — so a 1080x1920 story got a landscape picture. HiDream snaps to its own ~4 MP
        # bucket by *aspect*, so what is passed here decides portrait or landscape and nothing
        # else. Without a plan on disk the setting still decides, which is the brief-only path.
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
    """The style clause the first anchor was actually drawn with, from its own marker.

    `generate_anchor` records the exact prompt it sent, and that prompt begins with the style. The
    style is everything up to the first sentence break, because `_anchor_prompt` joins its clauses
    with ". " and puts the style first — deliberately, and measured (see its docstring).
    """
    for marker in (
        ctx.ddir() / "anchors" / "anchor.done.json",
        *sorted((ctx.ddir() / "anchors").glob("*/0000.done.json")),
    ):
        if marker.is_file():
            prompt = str(json.loads(marker.read_text()).get("prompt", ""))
            return prompt.split(". ", 1)[0].strip() if prompt else ""
    return ""


ANCHOR_BACKEND_MODELS: dict[str, str] = {
    # A backend's `.name`, which is what `generate_anchor` writes into its marker, back to the
    # `model` widget's spelling. Two of the three are already the widget's word; the mock's is
    # not, and reading its marker has to give "mock" rather than nothing.
    "hidream-o1": "hidream-o1",
    "flux2-dev": "flux2-dev",
    "mock-reference-edit": "mock",
    "mock": "mock",
}


def _anchor_recorded_backend(ctx: StageContext) -> str:
    """Which backend actually drew the first anchor, from its own marker — or `""`.

    The same argument as :func:`_anchor_recorded_style`, for the other half of the lock. This
    stage re-derived the backend from *its* node's `model` widget, and `lock_generation` has no
    such widget, so it fell through to the settings default: a run whose anchors were drawn by
    HiDream wrote a lock that said `mock-reference-edit` (measured on `image-set`, 2026-09-10).
    The lock's model rides in every spoke's `input_hash`, so the wrong name is not only a wrong
    record — it keys the whole frame set on a model that never touched it.
    """
    for marker in (
        ctx.ddir() / "anchors" / "anchor.done.json",
        *sorted((ctx.ddir() / "anchors").glob("*/0000.done.json")),
    ):
        if marker.is_file():
            return str(json.loads(marker.read_text()).get("backend", "")).strip()
    return ""


def stage_lock_generation(ctx: StageContext) -> StageOutput:
    """Freeze the generation lock around the first anchor: same model, seed, sampler and prompts
    for every frame that follows.

    The style comes from **the anchor's own node**, not from this one. `_anchor_lock` resolves the
    style from `ctx.params`, and a `--style` or a canvas widget sets it on `generate_anchor` — so
    this stage re-derived it from the settings default and wrote a lock whose art direction was
    not the art direction the anchor was drawn in. Every spoke was then edited under a style the
    hub had never seen, which is the one thing a lock exists to prevent.
    """
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
    """The style preset the sequence was locked to, or `""` when there is no lock on disk.

    Read rather than required: `generate_video` runs in lanes that never build an image sequence,
    and a missing lock means "no per-style override", not an error.
    """
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
    """The frame plan a keyframe lane draws, and what each frame is a picture *of*.

    ``sample_motion_plan()`` is a fixture: eight frames of two hands sliding together on a
    1024x576 canvas, every frame instructed "Move hands to the plotted position for frame N". It
    was what `image-set` and `photo-sequence-video` drew whatever story they were given — so the
    story widget those definitions describe as "the list of views, as beats" reached the anchor's
    prompt and nothing else, and a deep-sea documentary and a children's book came back as the
    same eight pictures of the fixture's subject moving its hands.

    With a story on disk the plan is derived from it: one frame per beat, on the story's canvas,
    and each frame's edit instruction is that beat's scene ``alt_text`` — the field whose whole
    job is to say what the picture shows. The layout box is held still across the set, because
    these lanes are views of one subject rather than a subject in motion, and the box is exactly
    what drift masking treats as allowed to change.

    Without a story on disk the fixture comes back unchanged, which is what the brief-only path
    and every existing test get.
    """
    path = _story_plan_path(ctx)
    if path is None:
        return sample_motion_plan(), {}
    story = StoryPlan.model_validate_json(path.read_text())
    if not story.beats:
        return sample_motion_plan(), {}
    frames = len(story.beats)
    # The fixture's subject and its trajectory are kept exactly as they are and only re-timed:
    # what the story knows is how many pictures there are, how big they are and what each one
    # shows, and it says nothing about where a subject sits in the frame. Re-deriving the layout
    # here would change what drift masking treats as allowed to move, which is a separate decision
    # from "draw the views the story lists".
    base = sample_motion_plan()
    # The label reaches the model: `compile_edit_instruction` writes "(subject: <label>)" into
    # every frame's edit instruction. Left at the fixture's, an owl sequence asked HiDream for
    # "The same tawny owl on the same mossy branch ... (subject: hands)", which is a contradiction
    # in the one sentence that is supposed to say what to change.
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
    """Drop the cache markers of frames a person rejected, so the next run redraws them.

    `image-set`'s own note promises that "rejecting one drawing costs one drawing, not the film",
    and nothing implemented it: `review_frames` recorded the verdict and `generate_keyframes`
    never read it, so a rejected frame was a cache hit for ever and the gate blocked on the same
    picture on every rerun. Measured 2026-09-10 on a malachite set — six consistent views of the
    wrong object, rejected, and the resume printed the identical rejection.

    Only the marker is removed. The picture itself moves to `sequence/rejected/`, beside the ones
    the drift check refuses, so what was turned down can still be looked at; and the frame is
    listed in the stage's facts, because a redraw nobody can see in the record is a redraw nobody
    can audit.
    """
    from content_factory.services.frame_reviews import current_batch

    # Merged, for the reason `_clear_rejected_anchors` records: `batch.json` is the gate's last
    # word and `verdict.json` is the operator's, and only the second one carries a rejection made
    # since the gate last ran. This path had the same latent flaw and had never been exercised
    # twice in a row.
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
    """How far to move each frame's seed, from how many times it has been turned down.

    A redraw has to come back *different*. Redrawing with the same instruction and the same first
    attempt reproduces the picture exactly — measured 2026-09-10, when a rejected kilim frame was
    redrawn and came back as the drawing that had just been rejected. The backend derives its
    seed from the attempt number (`lock.seed + attempt - 1`), so one offset per rejection walks
    it somewhere new and keeps doing so if the next one is rejected too. Counted off the files
    in `sequence/rejected/`, which means it survives a crash and needs no extra state.
    """
    reject_dir = workdir / "rejected"
    if not reject_dir.is_dir():
        return {}
    offsets: dict[int, int] = {}
    for png in reject_dir.glob("[0-9]*.reviewed*.png"):
        head = png.name.split(".", 1)[0]
        if head.isdigit():
            offsets[int(head)] = offsets.get(int(head), 0) + 1
    # One rejection is worth more than one attempt: within a run the three drift retries already
    # walk the seed by 0, 1, 2, so a redraw that only moved by one would land on the second
    # attempt of the run that was rejected.
    return {idx: n * 8 for idx, n in offsets.items()}


def stage_generate_keyframes(ctx: StageContext) -> StageOutput:
    """Hub-and-spoke keyframes from the MotionPlan (builtin controls). Blender-compiled shots are
    keyframed by their anchors instead and go straight to generate_video."""
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
    # more between frames than a photograph does. The lock freezes the prompt, so the preset name
    # is recovered from it; a hand-written prompt has no name and takes the defaults.
    from content_factory.sequences.drift import UNCALIBRATED
    from content_factory.sequences.styles import style_name_for

    locked_min, delta_max = cfg.drift_thresholds.resolve(style=style_name_for(lock.style_prompt))
    # The configured default (0.92 / 0.15) is a *mock* number: the stand-in backend repaints only
    # inside the motion box, so everything outside it is byte-identical and 0.92 is met by
    # construction. A diffusion model re-renders every pixel of a 4 MP frame, and measured here no
    # real frame came near it -- six drawings, eighteen attempts, nothing written. `drift.py`
    # already names the profile for that case and nothing selected it, so the node does:
    # `--set spokes.drift_profile=uncalibrated` observes rather than gates, which is what the run
    # that produces the numbers a real threshold is set from needs. `locked_min` and
    # `style_delta_max` set a measured pair directly.
    if _param(ctx, "drift_profile", "configured").strip().lower() == "uncalibrated":
        locked_min, delta_max = UNCALIBRATED
    # 0 is "leave the profile's own value alone", not "a floor of zero". The real thresholds are
    # per-style and per-camera and resolved above; a widget that had to carry a number would be a
    # third calibration to keep in step with the other two, and its default would silently
    # override them on every node that never touched it.
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
        # Same distinction as the anchors: the engine already regenerated each of these with a
        # fresh seed up to max_regen_attempts_per_frame and they still drifted. Another attempt is
        # more GPU time for the same answer, so this blocks for a person rather than failing.
        #
        # The candidates name `sequence/rejected/`, which is where the drawing that did not pass
        # actually is, with the measurement beside it. They used to name `sequence/frames/`, where
        # nothing was ever written for a failed frame, and to carry the *name* of the check with
        # no number in it -- so the block asked a person to look at a missing file and told them
        # neither what it scored nor what it needed.
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
    """Gate the generated frames on someone having looked at them.

    Writes a contact sheet of every drawing in order, with deterministic findings beside each —
    tonal collapse, a half-applied monochrome instruction, subject matter jammed into the frame
    edge, and how far each frame drifts from the one before it. Then it **blocks**: a verdict binds
    to the digests of the exact images reviewed, so a regenerated frame is unreviewed again.

    The measurements exist because those specific faults were invisible to the code and obvious in
    the pictures. What they cannot judge — whether these are the same two people as the last frame,
    whether a pose is bodily possible, whether two figures appear where two were staged — is why
    the sheet and the verdict exist at all.
    """
    import datetime as dt

    from content_factory.qc.frame_review import (
        consistency_matrix,
        contact_sheet,
        review_findings,
    )
    from content_factory.qc.reviewer import intended_reviewer, request_markdown
    from content_factory.qc.verdict import merge_verdict
    from content_factory.schemas.review import FrameRecord, FrameReviewBatch

    # Review what this lane's graph actually put in front of the reviewer. The keyframe lanes
    # (photo-sequence-video, picture-story) wire `drift_qc -> review_frames`, so the drawings
    # under `sequence/frames` are the ones a verdict is being asked about and the ones
    # compose_video cuts. The Blender/scene lanes generate no keyframes and their per-shot
    # anchors *are* the frames. Reading the anchor manifest unconditionally gated a single
    # picture on a lane whose wire said eight.
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
                # `or`, not a dict default: the single-anchor branch writes `shot_id: None`
                # rather than omitting the key, so `.get(..., "anchor")` returned None and that
                # lane's frames were called "None:0000" in every review request and verdict.
                # `_anchor_frame_paths` still accepts the old spelling, so a verdict already on
                # disk keeps working.
                shot_id = entry.get("shot_id") or "anchor"
                frame_id = f"{shot_id}:{frame['frame_index']:04d}"
                pngs.append((frame_id, (ctx.ddir() / frame["path"]).read_bytes()))
    if not pngs:
        msg = f"review_frames: {source} lists no frames"
        raise RuntimeError(msg)

    raw_style = _param(ctx, "style", get_settings().image_sequences.anchor_style_prompt)
    # Resolved once: a preset name has to become its prompt before either predicate can read it,
    # and `resolve_style` refuses a short unknown token rather than sending it to a model, so the
    # empty case is guarded rather than passed through.
    style = resolve_style(raw_style) if raw_style.strip() else ""
    monochrome = any(word in style.lower() for word in ("monochrome", "no colour", "no color"))
    findings = review_findings(
        pngs,
        expect_monochrome=monochrome,
        expect_photographic=_expects_photographic(style),
    )
    sheet_rel = Path("reviews") / "frames" / "contact-sheet.png"
    sheet_png = contact_sheet(pngs, ctx.ddir() / sheet_rel, findings=findings)
    # Every frame against every other, not just its neighbour: a set can drift a little at each
    # step and end somewhere else entirely with every consecutive pair looking fine. Written out
    # whole so a reviewer can cite the number rather than the impression.
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
    # `qc.verdict.merge_verdict`, shared with the review panel in the app: the panel has to show
    # exactly what this gate will decide on, and two copies of a digest comparison is how it would
    # come to show something else.
    verdict_path = ctx.ddir() / "reviews" / "frames" / "verdict.json"
    if verdict_path.exists():
        prior = FrameReviewBatch.model_validate_json(verdict_path.read_text())
        batch = merge_verdict(batch, prior)
    _write(ctx.ddir() / "reviews" / "frames" / "batch.json", batch.model_dump_json(indent=1))

    flagged = sum(1 for r in batch.frames for f in r.findings if not f.passed)
    # Who is being asked. A run started from a Claude Code session has an agent present, and 27
    # runs on this machine were parked here with their drawings finished and nobody coming.
    # Empty means "not set on the node", which is what lets the environment decide.
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
    """The one recording this run is about and why it counted, or a failure saying where to put one.

    Three places, in the order an operator would expect them to win:

    1. the node's ``source`` widget — a path, absolute or relative to the project directory;
    2. ``<project>/uploads/`` — where ``make --audio`` copies a file and where the canvas's
       Audio File node stages what was dropped on it;
    3. ``<deliverable>/audio/source.wav`` — the normalised copy a previous run of this stage
       wrote, so a resumed run needs neither the original nor the flag again.

    More than one candidate in uploads is an error rather than a coin flip: "which of these two
    interviews is the film" is not a question a stage may answer by sort order.

    The second element is empty for a file anyone would call a recording, and carries the
    measurement when the recording arrived in a video container — the one case where the stage
    read a file its own suffix list says is not audio, and therefore has to say so.
    """
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
    """Read the words off a recording, with timings, and keep the normalised audio beside them.

    This is the only stage in the factory whose input is speech and whose output is text, and it
    exists so a film can be made out of what somebody said rather than out of something somebody
    wrote. It writes three things into ``<deliverable>/audio``:

    * ``source.wav`` — the recording as mono PCM at the transcription rate. Every later stage
      measures, cuts and mixes PCM, so an m4a off a phone is normalised once, here.
    * ``transcript.json`` — the :class:`SpeechTranscript` contract: text, per-word timings, what
      read it, and whether those timings were measured or estimated.
    * ``transcript.txt`` — the same words as plain text, because an operator reading back what
      their interview said should not have to open a JSON file to do it.

    Cached by the recording's bytes and the transcriber's identity: re-running never re-transcribes
    the same file, which matters at a minute of CPU per few minutes of audio.
    """
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
    """The operator's own transcript, for the ``fixture`` engine: the widget, or a file it names.

    A widget holding a whole interview is unusable, and a path is unusable when the transcript is
    one sentence, so both spellings work and the file wins when it resolves.
    """
    raw = _param(ctx, "transcript").strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    for path in (candidate, ctx.project_dir / candidate, REPO_ROOT / candidate):
        if len(raw) < 400 and path.is_file():
            return path.read_text()
    return raw


def stage_lock_script(ctx: StageContext) -> StageOutput:
    """The sentences this deliverable speaks, frozen.

    A short reads its own derived plan (see :func:`_story_plan_path`), so the locked script is the
    short's re-edited beats rather than the whole episode's.
    """
    plan = _load_story_plan(ctx)
    # The read's locale, so a lexicon entry for another language is not applied to this script.
    lexicon = story_lexicon(ctx, get_settings().narration.locale)
    locked = {
        "deliverable_id": ctx.deliverable_id,
        "sentences": [b.display_text for b in plan.beats],
        # What is SAID, which is not always what is shown: a beat displayed as "21%" may be spoken
        # as "twenty-one per cent". The alignment and the caption timings are measured against
        # these, so locking only the display text locked the wrong string.
        "spoken": [spoken_line(b, lexicon) for b in plan.beats],
        "plan_hash": plan.content_hash(),
    }
    _write(ctx.ddir() / "script" / "locked.json", json.dumps(locked, indent=1))
    path = _story_plan_path(ctx)
    # The claim gate, here rather than in verify_claims, because THIS is where a script exists.
    # verify_claims runs before plan_story: it gated `sample_story_plan()` — a film nobody was
    # rendering — and passed. Locking the script is the last moment before words are spoken and
    # pictures are drawn from them, so it is the right place to refuse a statement whose claim is
    # unsupported.
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
    """The script's own claim gate, against the claims this run verified.

    Silent when there are no claims on disk: a hand-written StoryPlan fixture with no claim links
    is a legitimate film (every picture-story lane is one), and inventing a gate failure for it
    would block the lanes that never had claims to check.
    """
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
    """The film's own pronunciation list, from ``<project>/story/lexicon.json``.

    `NarrationRequest.lexicon` and `normalize_for_speech`'s `lexicon` argument have both existed
    since phase 15 and **nothing ever passed one**, so every respelling this repo's contracts can
    express was dead weight: a documentary about Energimyndigheten got whatever the TTS guessed at,
    once per beat, and the operator's only recourse was to rewrite the display text.

    A file, per story, because a pronunciation is a fact about *this* film's subject matter — a
    place name, an agency, a person, a unit — and a repo-wide default list would be a guess about
    films nobody has made yet. Entries whose locale does not match the read are skipped: a Swedish
    respelling of a Swedish name is wrong guidance for an English narrator.
    """
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
    """What is actually said for one beat.

    ``VisualBeat.spoken_text`` has existed since the scene grammar was written and every caller
    normalised ``display_text`` instead, so a plan that carefully spelled "twenty-one per cent" for
    a line displayed as "21%" was thrown away and the TTS was handed "21%" to guess at. When a beat
    says how it should be read, that is what is read; the normaliser still runs over it, because a
    hand-written spoken line can still contain a numeral the model would mispronounce — and the
    story's own lexicon is applied there, which is the only place a respelling can take effect.
    """
    written = (getattr(beat, "spoken_text", None) or "").strip()
    return normalize_for_speech(written or beat.display_text, lexicon)


def _tts_executor(ctx: StageContext | None = None) -> tuple[TTSExecutor, VoiceIdentity]:
    """``narration.tts`` selects the voice: the deterministic mock, Qwen3-TTS, or Kokoro.

    A canvas node overrides the configured voice, timbre, delivery note and aligner through
    ``ctx.params``; without a context the settings alone decide, which is what the tests and any
    non-graph caller want.
    """
    cfg = get_settings().narration

    def param(key: str, default: str) -> str:
        return _param(ctx, key, default) if ctx is not None else default

    backend = param("voice", cfg.tts)
    # The canvas labels Kokoro by its model name; the setting spells it by skill.
    if backend == "kokoro-82m":
        backend = "kokoro"
    # Before any weights load, and before the GPU is claimed: a locale the chosen voice cannot
    # speak is a refusal by name. Kokoro used to map every non-English locale to *British English*
    # and narrate the whole film in the wrong language without failing anywhere.
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
        voice = VoiceIdentity(
            provider="qwen3tts",
            # A cloned voice is identified by the clip it was cloned from, not by a timbre name.
            voice_id=(ref_audio.name[:80] if ref_audio else param("speaker", cfg.qwen_speaker)),
            model_revision=(
                "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
                if ref_audio
                else "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
            ),
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
            ref_audio=ref_audio,
            ref_text=cfg.qwen_ref_text,
            aligner=param("aligner", cfg.aligner),
            # The aligner checkpoint for *this* language: `base.en` cannot transcribe German and
            # does not say so, and a word-perfect take then fails the script gate. Configured
            # values are obeyed; see `audio.languages.aligner_model_for`.
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


def stage_synthesize_narration(ctx: StageContext) -> StageOutput:
    """One wav + NarrationSegment per beat. Cached per beat by (voice, spoken text): a real TTS
    is slow and a rerun with the same script must not re-speak (or re-time) anything."""
    plan = _load_story_plan(ctx)
    tts, voice = _tts_executor(ctx)
    lexicon = story_lexicon(ctx, voice.locale)
    audio_dir = ctx.ddir() / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    seg_hashes = []
    timing_sources: set[str] = set()
    script_checks: list[str] = []
    spoken = 0
    # The same eviction `restore_speech` and `sound_design` already do, and this stage was left out
    # of it. Qwen3-TTS loads its weights in its own uv environment through a subprocess, so nothing
    # stops the managed servers first: measured on `hybrid-video`, it met a card with HiDream's
    # 18.57 GB already resident and died on a 20 MiB allocation. Only on the uncached path — a
    # rerun that speaks nothing must not evict a tenant somebody else is using — and only for a
    # provider that actually loads a model on this card.
    freed = tts.provider == "mock"
    for b in plan.beats:
        request = NarrationRequest(
            beat_id=b.beat_id,
            display_text=b.display_text,
            spoken_text=spoken_line(b, lexicon),
            voice=voice,
            # The contract field that existed and was never filled. In the request because it is
            # in the cache key below: adding a respelling has to re-speak the beats it changes.
            lexicon=lexicon,
        )
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
    """The voice chain, per beat: detection, optional cleanup and band extension, restoration,
    then de-esser, EQ and light compression into the delivery sample rate.

    Word timings are not touched and cannot be: the chain returns each beat at exactly the length
    it received, ``restore_beat`` refuses to write anything else, and this stage re-checks. What
    the segment record *does* gain is the new audio hash and sample rate, so the contract keeps
    describing the bytes that actually exist.

    Cached per beat by (take bytes, spec): a rerun with the same script and the same chain never
    re-restores anything, which matters because Resemble Enhance is ~19x realtime on CPU.
    """
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
    # Resemble Enhance and ClearerVoice load their own weights inside their own uv environments,
    # so nothing here goes through _ensure_backend_ready and nothing had stopped the image or
    # video tenant. Freed once, lazily, on the first beat that actually has to be restored.
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
    """Human takes instead of a synthesized voice: the same NarrationSegment per beat, so the
    captions, timeline, mix and mux downstream cannot tell the difference.

    Recordings are found by beat id under ``takes_dir``, normalised to mono PCM, checked against
    the locked script and force-aligned. Nothing here generates speech — a beat without a take
    fails the stage and names the beat, which is what an operator waiting on a co-star needs to
    hear. Cached per beat by (take bytes, script, aligner): re-running never re-aligns a take
    that has not changed."""
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
    # The same lexicon the script was locked with: the aligner compares the take's transcript
    # against this string, so a respelling applied on one side and not the other reads as a
    # performer who said the wrong words.
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
    """One continuous recording as the narration, cut into the plan's beats.

    The other mode asks an operator to record their material beat by beat, which is the right
    shape for a film written before it was performed and the wrong one for material that already
    exists: nobody re-records an interview into six numbered files. So the beats are cut out of
    the recording instead, at the word boundaries the transcript measured, and what lands on disk
    is byte-for-byte the same per-beat layout the take path writes — ``<beat_id>.wav``, its
    ``<beat_id>.segment.json`` and a marker — so ``restore_speech``, ``align_words``,
    ``compile_captions``, the timeline compiler and the mix cannot tell the two apart.

    No aligner runs. The recording was measured once, whole, by ``transcribe_audio``, and a beat
    is a window on that measurement; re-transcribing each beat would cost minutes to rediscover
    timings already on disk, and would let two passes disagree about what was said.
    """
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
            # How much of the recording no beat owns: the pauses between beats, plus anything the
            # story dropped. A large number here is the operator's signal that the beat count is
            # wrong or that the plan is not this recording's.
            "unused_seconds": round(
                max(0, transcript.duration_ms - sum(e - s for s, e in spans.values())) / 1000, 1
            ),
        },
    )


SEQUENCE_PREVIEW_FPS = 8
"""The rate a keyframe flipbook is previewed and cut at. One constant, because package_sequence's
preview, stage_interpolate's ``sequence`` case and _silent_picture all have to agree."""


def _final_audio(ctx: StageContext) -> tuple[Path | None, str]:
    """The audio track for the finished cut, and what it is: speech, a bed, or nothing.

    ``audio/narration-mastered.wav`` is what ``mix_audio`` writes, whatever went into it — a
    narration stem, a music bed, effects, or a combination — so it is the first choice. Failing
    that, an unmastered music or effects file written by ``select_music``/``sound_design`` without a
    mix is still better than silence, and genuine silence is a real answer rather than a failure.
    """
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
    """Where the last word ends in the laid-out narration: the length a film has to reach.

    One definition for both compose paths, and for the mux itself. It is the *laid-out* position,
    not the sum of the segments: the mix puts ``lead_in_ms`` before the first beat and a pause
    between beats, so the last word is later than the audio alone would suggest.
    """
    _plan, segs, _files = _load_segments(ctx)
    laid = lay_out(segs, AudioMixSpec(deliverable_id=ctx.deliverable_id))  # type: ignore[arg-type]
    return max((w.end_ms for b in laid for w in b.words), default=0)


def _planned_beat_seconds(ctx: StageContext, count: int) -> list[float]:
    """How long each of ``count`` pictures is on screen, from the story's beats. Empty if unknown.

    A beat carries ``planned_duration_ms`` for exactly this: a silent lane has no narration to
    measure, so the plan is the only thing that knows the pace.
    """
    path = _story_plan_path(ctx)
    if path is None:
        return []
    beats = StoryPlan.model_validate_json(path.read_text()).beats
    if len(beats) != count or not all(b.planned_duration_ms for b in beats):
        return []
    return [round((b.planned_duration_ms or 0) / 1000, 3) for b in beats]


def _hold_frames(pngs: list[Path], target: Path, seconds: list[float], fps: int = 30) -> None:
    """Cut stills together, each held for its own length, with no blending across the joins.

    The same ffmpeg concat-demuxer shape `_hold_stills` uses for the shot-based lanes; this one
    takes a flat list of frames because a sequence lane has no ShotSpecs to read lengths from.
    """
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
    """The silent cut: whatever the video branch has produced, most-finished first.

    Both the sound designer and the final mux need this, and which file it is depends on the lane
    — a Remotion render bundle, the post chain's ``final.mp4``, or the concatenated generated
    clips. Naming them here keeps one answer for both.
    """
    exports = ctx.ddir() / "exports"
    for name in ("picture.mp4", POST_CHAIN_EXPORT, "generated.mp4"):
        candidate = exports / name
        if candidate.exists():
            return candidate
    renders = sorted(exports.glob("bnd_*.mp4"))
    if renders:
        return renders[-1]
    # An image-sequence lane (photo-sequence-video, image-set) has approved PNG frames and no
    # video at all: there is no generate_video in it and no interpolate either, on purpose —
    # inventing frames between two drawings is what a lane of held pictures does not want. So the
    # frames ARE the picture, and cutting them is this function's job rather than a new node's.
    # Before this, compose_video reached here and raised, which is why that lane had no picture.
    frames = ctx.ddir() / "sequence" / "frames"
    pngs = sorted(frames.glob("[0-9]*.png"))
    if pngs:
        target = exports / "generated.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Held for the beats' own lengths when the story says how long they are. Cut at the
        # flipbook rate instead — which is what this did unconditionally — a five-beat story
        # planned at eighteen seconds came out as a **0.6-second** film, because eight frames per
        # second is a preview of a frame set and not a cut of it. The lane's own description says
        # the pictures carry the story and the cut carries the pace; the pace is in the plan.
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
    """Video-synced SFX for the silent cut, written as a bed the mix folds under the narration.

    The backend watches the picture, so the sound lands on the frame: this is the stage that makes
    a generated walk sound like footsteps. Cached by (picture bytes, prompt, seed, steps)."""
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
    # MMAudio and Stable Audio load their weights in their own skill environment, so the image and
    # video tenants have to come off the card first. Past the cache check, so a rerun that changes
    # nothing does not evict anything — and never for the deterministic stand-in, which loads no
    # model at all and must not reach into another tenant to produce a tone.
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
        # The restore_speech stage writes its result alongside the take and never over it, so a
        # rerun can always start from the untouched synthesis output. Everything downstream reads
        # the restored file when it is there.
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
    """The words a caption shows, carrying the timings of the words that were said.

    They are usually the same string. They are not when the beat carries a pronunciation
    respelling: `spoken_line` substitutes it so the model says the name correctly, the aligner
    measures what it heard, and the captions are built from those measured words — so a film about
    the Swedish energy agency burned **"en-er-yee-MIN-dih-het-en"** across the bottom of the frame.
    `display_text` is the contract's answer to exactly this ("Display text; if it carries a factual
    statement it must link to claims") and it was not being used.

    Substituted only when the two tokenise to the same count, which a respelling does by
    construction — one token in, one token out. Anything else and the measured words stand, because
    a guess at which measured word belongs to which displayed one would put the caption out of sync
    with the voice, which is worse than showing the respelling.

    The written form is preferred **with its punctuation**: the measured words come from an aligner
    that emits bare tokens, so every caption in this repo read "Euclid asked whether they eventually
    stop Suppose they did" — two sentences run together with nothing between them. Splitting the
    display text on whitespace keeps the commas and full stops exactly where the author put them,
    and falls back to the aligner's tokenisation, and then to the measured words, when the counts
    do not line up.
    """
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
    """The headline burned over the opening seconds, unless the opening card is already one.

    The hook exists for "the words a muted viewer reads in the first 1-3 s", which is a real job
    when the film opens on a picture. It has nothing to do when the film opens on typography.

    Two tests, and the kind is the stronger one. A film that opens on a title card carrying the
    same line — the natural thing for an author to write — showed it twice, in two type sizes, at
    once; and a film whose title card *paraphrased* the hook showed two different headlines
    stacked, which reads worse than either. So: no hook over a headline card, and no hook that
    repeats the opening scene's words whatever kind of card carries them.
    """
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
    """Cues never straddle a beat: each beat's words are compiled on their own and the cues are
    concatenated, so a caption always belongs to the scene on screen (the compiler alone breaks
    on pauses, and a short beat gap can be under its 700 ms threshold)."""
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
    # The delivery rate. The timeline compiler reads it off the plan, so changing it means changing
    # the plan and recompiling, not post-processing the result: every scene boundary is an integer
    # frame index at this rate, and the narration boundaries are placed at absolute frames.
    fps = _param_int(ctx, "fps", plan.fps)
    if fps not in (24, 25, 30, 60):
        msg = f"compile_timeline fps must be 24, 25, 30 or 60, got {fps}"
        raise RuntimeError(msg)
    # The other half of the master-rate check. plan_shots refuses a plan that disagrees with the
    # story; this refuses a timeline that disagrees with the shots, which is the direction the wind
    # shorts actually failed in — the lane pinned `fps: 24` on this node to match the shot
    # planner's default while the script was 30, and compose_video conformed every generated clip.
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
    """Deterministic pick from the local music library; an empty library selects nothing (music
    is optional) rather than failing the run."""
    library = _music_library_dir()
    manifest = library / "tracks.json"
    mood = _param(ctx, "mood").strip().lower()
    gain_db = _param_float(ctx, "gain_db", -18.0)
    selection: dict = {"track_id": None, "reason": "music library has no manifest"}
    if manifest.exists():
        tracks = [MusicTrack.model_validate(t) for t in json.loads(manifest.read_text())]
        # The mood narrows the pool before the deterministic pick, so it decides which track is
        # chosen rather than only labelling the one that would have been. An unset mood takes the
        # whole library; a mood no track carries selects nothing and says which mood, because
        # bedding an energetic cue under a memorial piece is worse than no music.
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
            # The library-relative filename, never an absolute path: this dict is folded into
            # the stage's outputs_hash, and an absolute path would make the hash (and every
            # cached downstream stage) depend on where the repo happens to live.
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
    """How long the finished picture is, in milliseconds.

    A narrated mix takes its length from the measured speech. A silent lane has no speech, so the
    bed has to be as long as the film, and the film is the only thing that knows.
    """
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
    """Cut a cue sheet from the film's scene spans and fold the rendered track into the mix.

    Rules over the spans, so no model and no GPU: the same film always gets the same sounds in the
    same places. The sheet is written to ``audio/cue-sheet.json`` whether or not it is mixed,
    because it is the part of the mix a person reads rather than hears — every cue carries the
    reason it exists.

    ``beats`` are the measured narration boundaries this stage just laid out, which is the whole
    reason the cutter takes spans rather than a `CompiledTimeline`: **mix_audio runs before
    compile_timeline** in every lane that has both — it has to, because the timeline's scene
    durations are compiled from this stage's measurements — so a cutter that wanted a compiled
    timeline would have found none and placed nothing on every run. A silent lane has no beats,
    and falls back to the compiled timeline from a previous pass if one is on disk.

    The cue track ducks against the **narration stem**, not against `pre_master`: by the time it
    is added, `pre_master` may already carry a music bed and a generated effects bed, and "duck
    under speech" has to mean under speech.
    """
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
    """One bed, looped to ``duration_ms``, faded out over the last second, at its own level.

    The counterpart of ``add_music_bed`` for a film with no voice in it: there is nothing to duck
    under and nothing to mix against, so the sidechain and the amix would both be no-ops on one
    input. The gain is left alone here on purpose — the master sets the delivery level, and
    attenuating a bed that is the whole soundtrack only makes the master pull it back up.
    """
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
    """Narration stem, then the music bed, then the effects bed, then the master.

    The narration is optional. silent-video is a lane with no voice stage in it at all — its
    caveat used to say so — and this stage read per-beat narration segments unconditionally, so
    that lane died here on a missing file with the music already selected and the foley already
    generated. With no speech the first bed becomes the base and the master runs on that, which is
    exactly what a music-only or effects-only cut needs.
    """
    # The delivery loudness. -14 LUFS is the streaming default and the right answer for most
    # platforms; a lane delivering to a broadcast spec needs its own number, and `master` refuses
    # the mix rather than shipping one that missed the target it was given.
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
                # No speech to duck under: the bed IS the mix, so it is taken at its own level and
                # only the length has to be decided. Silent lanes have no measured speech to take
                # it from, so it comes from the picture.
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
        # Same bed mixer as music: looped to length, ducked under speech, faded at the tail. The
        # sidechain key is the narration stem and not `pre_master`, which by now contains the
        # music bed: keying off that made the effects duck under the *music*, and a -18 dB bed is
        # loud enough to hold them ducked for the whole film.
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
    """Render the .srt onto the picture with libass: white bold text in a translucent box, sized
    and placed by frame height so the same settings work for 9:16 and 16:9. libass scales SRT
    styles from a 384x288 script, so frame fractions are converted to that space."""
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
    """Render a styled ASS track (word highlight + hook headline) with libass; styles live in the
    file, so no force_style — only the pinned fonts directory."""
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
    """The silent picture with captions burned in when the captions stage ran and burning is on;
    otherwise the picture unchanged. Returns (video, srt used or None)."""
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
    """The video stream's frame rate, or None when it cannot be read.

    Used to decide whether a segment needs an ``fps=`` conform at all. Unreadable means "conform
    anyway", which is the safe direction: an unconformed segment at the wrong rate desynchronises
    everything after it in the concatenation.
    """
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
    """ffmpeg -vf for one beat segment at the timeline's size and fps, exactly duration_frames long.
    Remotion segments are cut from the full render by frame index; generated clips are fitted
    (letterboxed, never cropped), retimed, and held on their last frame if the shot is shorter.

    The ``fps=`` conform is emitted only when the source is not already at the timeline's rate.
    ffmpeg's ``fps`` filter reaches a higher target by DUPLICATING frames, which is what every wind
    short v1-v5 shipped: 24 fps footage stepped up to a 30 fps timeline, one frame in five shown
    twice. It is still emitted when it is needed — dropping it would desynchronise the
    concatenation — but it now shows up in ``compose.json`` as ``retimed_from_fps`` instead of
    being invisible.
    """
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
    """Editing rhythm facts: short-form retention holds when something on screen changes every
    3-5 s, so scenes longer than that are listed for the editor (not a blocker: a chart that
    animates for 6 s is fine, a static card is not)."""
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
    """Hybrid workflow: one segment per compiled scene, in beat order, each either cut from the
    Remotion render or conformed from the shot's generated clip; segments are encoded identically
    (cached by source digest + filter), concatenated losslessly, then narration is muxed on when
    the audio branch ran. ``exports/compose.json`` records what went where."""
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
        # No min_video_ms here: this path's picture IS the compiled timeline, frame for frame —
        # check_video asserts exactly that a few lines above — so holding its last frame would
        # break the one guarantee a timeline film makes about its own length.
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
    """Final assembly. Plain path: mux narration onto the Remotion render. Hybrid path (a shot
    routing with generate-routed beats): interleave Remotion segments and generated clips in beat
    order, then mux narration when present."""
    routing = _load_routing(ctx)
    if routing is not None and routing.generated_shot_ids():
        return _compose_mixed(ctx, routing)
    # Whatever produced the picture: the Remotion bundle for a deterministic film, the post-chain
    # or the generative concatenation for one that was drawn. Before this, a narrated generative
    # film failed here looking for a Remotion render it never had.
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
    # Narration is optional on this path, the same way it already was on the mixed one. Two lanes
    # exist for films with no voice in them — silent-video (music and effects) and
    # photo-sequence-video (nothing at all) — and both failed here: the stage muxed
    # audio/narration-mastered.wav unconditionally and then read per-beat narration segments to
    # size the speech QC, so a lane with no voice stage died on a missing file.
    audio, source = _final_audio(ctx)
    facts: dict = {"narrated": audio is not None and source == "narration"}
    # How long the film has to last to carry everything that is said, measured before the mux
    # rather than after it: a picture shorter than that loses words, and the mux holds its last
    # frame to the number instead. The same number is what the QC below checks the result against.
    speech_ms = _speech_end_ms(ctx) if facts["narrated"] else None
    # ...but only a picture whose length is its OWN is held. A Remotion bundle render is the
    # compiled timeline frame for frame, and that count is a promise three tests and the delivery
    # QC rely on; the timeline is itself compiled from the measured narration, so what -shortest
    # takes off it is the stem's trailing silence. A drawn or post-processed picture has no such
    # promise — its length is the spans of speech it illustrates — and that is the one that loses
    # sentences.
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
        # Only speech gets the speech check: it asserts the mux carries at least 90 % of the
        # measured words, which is meaningless for a music bed and needs per-beat segment files
        # that only voice_over or synthesize_narration write.
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
    """Which generator animates, and with which package — the same precedence as the anchor's.

    1. ``video.backend`` **when it was explicitly configured**, because a machine that has said
       "no ComfyUI here" has to be obeyed on every lane;
    2. the node's ``model`` widget, which is what a lane definition freezes;
    3. the setting's default, which is ``mock``.

    It was 1 and 3 only, and the widget did not exist. So a lane could name its LTX weight files —
    this node has three widgets for exactly that — and still run the deterministic ffmpeg
    stand-in, with nothing in the run saying the model had not been asked. Measured 2026-09-10:
    ``image-to-video``, ``silent-video``, ``scene-controlled-video``, ``hybrid-video`` and
    ``single-clip-post`` had every one of their generated clips recorded as ``"backend": "mock"``.
    """
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
    """The video executor, with the LTX weight file names the node asks for.

    The three ``*_name`` widgets exist because a machine may hold a different quantisation of the
    same weights; naming one should not mean editing ``ltx_packages``. They were inert, so a lane
    that named a file got whichever pin the module happened to carry, and the mismatch showed up as
    a ComfyUI node error minutes into a render.
    """
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
    """Write bytes through a temp file in the same directory, then rename. Returns the sha256.

    A 40-second generation written straight to its final name leaves a **truncated but present**
    file if the process dies mid-write, and the next run's `clip.exists()` treats it as a cache
    hit. `os.replace` within one directory is atomic, so the file either is not there or is whole.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return sha256_hex(data)


def _cached_output(marker: Path, artifact: Path, input_hash: str, sha_key: str) -> dict | None:
    """A cache hit only if the marker matches AND the file on disk is still the recorded bytes.

    Reusing a file because a marker says so is a guess about the filesystem. A clip truncated by a
    full disk, half-copied by a rerun, or edited by hand all satisfy "the marker matches and the
    file exists", and the failure surfaces four stages later as a QC finding about a container.
    Hashing it costs milliseconds against the forty seconds not regenerating it saves.
    """
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
    r"""The clip's frames at the given indices, as PNG bytes. One ffmpeg call per frame.

    ``select=eq(n\,N)`` with ``-vsync 0``, so N is the decoded frame index and not a timestamp
    rounded to the nearest frame — a guide is pinned to an index, and comparing the wrong frame
    would measure the model's motion rather than its adherence.
    """
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
            # ffmpeg refusing a frame index past the end of the clip is a *finding* — the guide was
            # pinned somewhere the clip does not reach — and `guide_adherence` reports it by name
            # from the absence. Logging it here would report the same thing twice, in worse words.
            continue
        if png.exists():
            out[index] = png.read_bytes()
    return out


def _guide_adherence(
    clip: Path, guide_pngs: dict[int, bytes], *, style: str = "", camera: str = ""
) -> dict[str, object]:
    """Did the clip actually pass through the guides it was given?

    The measurement the guided package never had: `LTXVAddGuide` pins an anchor at a frame index,
    and nothing afterwards checked that the clip went anywhere near it. A guide whose strength was
    too low, or whose index the 8k+1 length rule snapped past the end of the clip, produced exactly
    the same "ok" as one the model honoured.

    Reported, not gating. The bar it starts from has not been calibrated against a live run yet
    (`sequences.drift.GUIDE_SIMILARITY_MIN` says so), and a threshold nobody has measured must not
    fail a film — but it can and does put the number in the run record.
    """
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
    """Cut the drawings together, each held for its shot's length. No model, no interpolation.

    ffmpeg's concat demuxer with an explicit duration per image gives exactly this and nothing
    else: no blending across the joins, no invented in-between frames, every frame on screen one of
    the drawings that was approved. The jump between them is the look.
    """
    from content_factory.audio.mix import ffmpeg

    exports = ctx.ddir() / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    entries = manifest["shots"]
    # Whatever the finishing chain last produced for these drawings, if it produced all of them:
    # a lane that runs upscale_video before the hold gets the restored pictures on screen rather
    # than an orphaned frames directory. One picture per shot, in shot order, which is exactly the
    # order they were gathered in.
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
    # Cut at the drawings' own resolution, not the ShotSpec's. The ShotSpec size is what LTX
    # generates at; a held cut has no such constraint, and HiDream returns ~4 MP whatever was
    # asked for. Downscaling 2560x1440 ink hatching to 1024x576 throws away 84 % of the pixels
    # and *raises* measured edge energy by 54 % — fine line work aliasing into crunch rather than
    # resolving. A ``size`` parameter still forces a smaller cut for a quick preview.
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
            # The finishing chain's output is not in the anchor digests, so without this a cut
            # made from raw drawings would be reused after an upscale and the enlargement would
            # never reach the screen.
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
            # Colour has to be *declared*, not merely converted. Written untagged the file says
            # nothing about range or matrix, so every player guesses — and one that assumes
            # limited range on full-range data crushes the blacks and shifts the whole palette.
            # Convert to limited-range BT.709 and tag it, which is what h264 players expect.
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
    """The deliverable's story plan when plan_story has run for it, else None.

    ``generate_video`` needs only one field of it (``visual_subject``), and the single-clip path
    runs in lanes that never plan a story at all, so an absent plan is not an error here.
    """
    path = _story_plan_path(ctx)
    return StoryPlan.model_validate_json(path.read_text()) if path is not None else None


def _codec_size(width: int, height: int) -> tuple[int, int]:
    """The nearest frame size at or above ``width x height`` that a video codec will take.

    `VideoGenerationRequest` requires a multiple of 16 on both axes, because that is what h264
    macroblocks and every diffusion video model's latent grid are built on. **1080 is not one** —
    1080 / 16 is 67.5 — so every 1080x1920 story, which is the vertical format, failed the contract
    at `generate_video` with "Input should be a multiple of 16". (It is the same arithmetic that
    makes 1080p video encode as 1088 lines and crop.)

    Rounded up rather than down: the clip is composited into the timeline's frame afterwards, and
    scaling a slightly larger picture down keeps detail that scaling a smaller one up invents.
    """
    grid = 16

    def snap(value: int, limit: int) -> int:
        return max(64, min(limit, -(-value // grid) * grid))

    return snap(width, 3840), snap(height, 2160)


def _backend_size_cap(backend: object) -> tuple[int, int] | None:
    """The largest frame the backend's workflow package will accept, or None if it says.

    Read off the package's own parameter bindings rather than configured anywhere: the graph is
    what has the limit, and the LTX-2.5 package declares 1344 on both axes.
    """
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
    """``_codec_size``, but inside what the video model can actually generate.

    Measured 2026-09-10: `image-to-video` on a 2560x1440 photograph, and `silent-video` on a
    1920x1080 story, both died at `generate_video` with "parameter 'width' above maximum 1344.0"
    — raised by the ComfyUI package validator, after the anchors had already been generated. The
    limit belongs to the graph and nothing upstream had asked it.

    The two paths want different answers and that is deliberate. A *supplied still* has an
    incidental size — a phone took it — so the clip is fitted into the cap with its aspect kept
    (``fit=True``). A *shot plan* has a deliberate one, chosen with the deliverable, so silently
    shrinking a whole film is the wrong repair: it is refused, early, naming the cap and the knob.
    """
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
    """The compiled cinematography paragraph for one shot, under the lane's own preamble.

    The ``prompt`` widget is the lane's standing instruction to the video model — "hand-drawn
    animation on paper, two frames per second" — and it leads, for the same measured reason the
    style leads an anchor prompt. The compiled shot text follows it, and the shot's own words never
    come from a story beat.

    ``ctx.campaign.brief.topic`` is deliberately not consulted. A brief topic is a research
    question ("How much of Sweden's electricity came from wind in 2025?"), and it was reaching the
    LTX request two ways: as the whole prompt on the single-clip path, and as the substitute
    whenever a shot happened to carry no motion text. Both produced a clip prompted with a question
    about something that was not in the picture (STATUS 1370, 1678). The single-clip path has no
    ShotSpec to compile from, so it needs a subject stated for it: ``--subject``, or the story's
    ``visual_subject`` when a story was planned. With neither, this raises — before any GPU tenant
    is started, because nothing here touches a backend.
    """
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
    """Image-to-video through the video.generate skill.

    With anchors per shot (the scene-control layer): one clip per shot, first frame = the shot's
    anchor at frame 0, the remaining anchors pinned as LTXVAddGuide keyframes at their Blender frame
    indices, size/fps/length from the ShotSpec; clips are cached per shot and concatenated into
    ``exports/generated.mp4``. Without shots: one clip from the brief (single anchor as first frame
    when present). ``video.backend`` selects the mock or the LTX-2.5 ComfyUI backend."""
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
            # The routing sent every beat to the renderer, so there is nothing generative to do.
            # A no-op, not a failure: `compose_video` takes its plain path when the routing has no
            # generated shots, and the film is the Remotion render. Before this, the empty clip
            # list reached ffmpeg's concat demuxer and the lane died on "No files to concat" —
            # nine words that name neither the stage's problem nor the routing that caused it.
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
                # motion without editing the plan. Zero means "the shot decides", which is what a
                # seed widget at its default has to mean.
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
                    # The package id, its version and a digest of the un-parameterised graph, not
                    # just the backend's name: editing a node in ltx_packages.py used to produce
                    # the identical key, so the old clip was reused with the new graph. That is the
                    # one cache mistake nobody notices, because the file is a plausible clip of
                    # the right length.
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
            # Measured on the produced clip, cached or not, and written beside it: the number is
            # about this clip and this guide set, so a cache hit has the same answer as the run
            # that made it and re-reading it costs one ffmpeg call per guide.
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
    # With no ShotSpec the clip's length is this node's to choose. With one it is not: the shot's
    # frame count is what the anchor guide indices were placed against, so the widget stays out of
    # the per-shot path above rather than silently truncating a shot's guides.
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
    # The same `.done.json` discipline the per-shot path has had. Without it this branch
    # regenerated its clip on every rerun of the lane — forty seconds of GPU to produce a file
    # already on disk — because the only thing that decided was whether the stage ran.
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
    """(name, working dir) per clip to post-process: the per-shot clips of the scene-control path,
    else the single generated clip, else the MotionPlan keyframes, else what the operator supplied.

    The last case is what makes ``video-finish`` and ``image-upscale`` real lanes rather than
    lanes that describe themselves. Both exist to work on footage this repo did not generate, and
    before this the material had to be copied by hand into a deliverable directory whose name
    contains a generated id — so the documented way to run them was to run something else first.
    Now the entry point is the same one every lane uses: the run's uploads folder.
    """
    video_dir = ctx.ddir() / "video"
    shots = sorted(p.parent for p in video_dir.glob("*/clip.mp4")) if video_dir.exists() else []
    if shots:
        return [(d.name, d) for d in shots]
    if (ctx.ddir() / "exports" / "generated.mp4").exists():
        return [("main", video_dir / "main")]
    if (ctx.ddir() / "sequence" / "frames").exists():
        return [("sequence", ctx.ddir() / "sequence")]
    # The drawings, before anything has been held or animated. This is the entry the held-cut
    # lanes need: a film of N drawings held for their own spans has N pictures worth finishing
    # and thousands of identical frames, so the finishing chain has to run on the drawings and
    # not on the cut. Ordered after the clip cases so a lane that animates first is unaffected.
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
    """The operator's clip or stills, staged where the post chain reads them. Idempotent.

    A clip becomes ``video/upload/clip.mp4``, which ``_latest_frames`` explodes on demand; a
    folder of stills becomes ``sequence/frames/%04d.png``, the layout every frame tool in the post
    chain already speaks. Conversion is FFmpeg's job and happens once: a JPEG folder is rewritten
    to PNG because ProPainter, SeedVR2 and the interpolators all read PNG only.

    Two clips is a refusal, not a choice by sort order — "which of these is the film" is the
    operator's question. Stills and a clip together is the same refusal for the same reason.
    """
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
    """The anchor drawings gathered into ``anchors/frames/%04d.png``, in shot order.

    The post chain speaks one language — a directory of numbered PNGs — and the anchors are
    written per shot under their own shot ids. Gathering them is a copy of N pictures and is
    idempotent, so the chain can restore and enlarge the *drawings* of a held film. Without this
    the only thing an upscaler could see was the cut, which for held drawings is the same few
    pictures repeated a few thousand times: hours of GPU to enlarge six images.
    """
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
    """The post chain's finished drawings, when it has finished exactly ``expected`` of them.

    A partial set is not usable: holding four restored drawings and two raw ones would put a
    visible change of texture in the middle of a film, so the count has to match or the raw
    drawings are used. The chain records where its latest output is; nothing here re-derives it.
    """
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
    """Run one tool as a cached chain step. The step's input is pinned in ``chain.json`` the first
    time it runs, so a rerun never reads its own output; ``advance=False`` marks a side output
    (masks) that does not move the chain forward."""
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
    """Remove the configured segmentation ids: Cutie tracks them from the Blender seed mask,
    ProPainter inpaints them. Without ids (or without a seed mask) the frames pass through."""
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
    # The node's own widget wins over the setting, the way stage_interpolate already worked. It
    # did not here, so a workflow that asked for 2160 quietly got whatever the host was configured
    # for - a widget that looks bound and is not is worse than no widget.
    # _param speaks strings, the setting and the tool speak numbers, so the coercion is explicit
    # here rather than leaking a string into the facts and the tool arguments.
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
    # A held cut is the answer, not an intermediate. generate_video's ``motion: hold`` exists to
    # guarantee that every frame on screen is a drawing a person approved, and the jump between
    # drawings is the look (limited animation, stop motion). Interpolating it invents frames that
    # were never drawn and were never reviewed, which destroys exactly the property the mode was
    # chosen for — and it does it silently, because rife runs happily on a concat of stills.
    # So the marker wins over the engine widget: a lane can set both and still get its held cut.
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
            # An optical-flow interpolator's memory is quadratic in the frame area: GIMM-VFI's
            # RAFT correlation asked for **12.36 GiB** on a single 1088x1920 pair and died on a
            # 24 GB card. That is a size limit, not a broken install, and the answer is the film
            # without the invented frames rather than no film — recorded, because a lane that
            # quietly stopped interpolating would look like one that never tried.
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
    """Interpolation declined, and the picture still assembled from whatever the chain finished.

    This stage is the post chain's last step and therefore its muxer, so declining to interpolate
    must not mean declining to deliver. Before this it returned the most-finished *existing* video
    and nothing else, which silently threw away every earlier step: a lane that removed an object
    and upscaled and then held its drawings wrote its finished frames to disk and shipped the
    unfinished cut — the frames were on disk, nothing had muxed them, and the run said ok.

    A held cut re-muxes nothing: ``generate_video``'s hold already assembled it from the finished
    drawings, each on screen for its own span, and re-muxing exploded frames at a constant rate
    would throw that structure away. So the held case is the one that legitimately reports the
    existing picture.
    """
    from content_factory.postchain import mux_frames

    # Resolved where it is used, not up front. This function *produces* the picture on a lane that
    # has none yet — `video-finish` and `image-upscale` end at the post chain — so asking for one
    # first turned the out-of-memory fallback into a second failure: GIMM-VFI OOMed on a 1088x1920
    # pair (1.99 GiB), the fallback ran, and the run died on "no picture yet" instead of
    # delivering the film without the invented frames (measured 2026-09-10, the first run to get
    # past the CuPy break and reach this path at all).
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
    """Deterministic explainer animation (builtin Pillow renderer by default; the Manim skill
    renders the same spec when configured). Frames land like an image sequence; a preview mp4 is
    packaged alongside for review."""
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
    # equation and diagram_build reveal one line or one labelled box per step, and AnimationSpec
    # refuses either with no steps. The story's beats ARE the film's steps, so a planned story
    # supplies them; without one the stage says what is missing instead of inventing content.
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
    """Real per-deliverable QC (17): accessibility on artboards, flashing + delivery checks on
    video, claim gate already ran as a shared stage. A failing layer fails the stage honestly."""
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
    # The only QC line here whose failure cannot be undone. A caption or an on-screen line
    # carrying an API key is published the moment the file leaves the machine, and rotating the
    # key afterwards is damage control rather than a fix. Text only — it says so in its facts.
    # A scene kind with no renderer draws a labelled grey card, and a grey card passed every check
    # here — so a plan naming `ranking` shipped a placeholder where a chart was meant to be, and
    # nothing said so. scenes/kinds.py is the list both languages check.
    # A plan that will not parse is a finding of its own, not an exception: QC's job is to report
    # everything it found, and letting one unreadable input abort the stage would suppress the
    # credentials scan below — the one line here whose failure cannot be undone.
    try:
        story = _story_plan_or_none(ctx)
    except ValidationError as exc:
        story = None
        checks["scene_kinds_implemented"] = {
            "passed": False,
            "facts": {"story_plan": "unreadable", "errors": len(exc.errors())},
        }
    if story is not None:
        # Do the figures on screen come out of the datasets the plan cites? The renderer is total
        # by design — a missing column becomes zero and a missing row an em dash — so a chart whose
        # `y` column is misspelled draws a flat line along zero and passed every check there was.
        # A plausible chart of nothing, with a real dataset's name under it.
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
        # The same fault one step along. A scene whose asset is not in the bundle draws the same
        # labelled grey card an unimplemented kind does, and it passed everything here too: a
        # thirty-second film in which every single scene read "missing asset · ast_sky02000" came
        # back QC-passed and packaged. Only asked of a lane that renders a bundle — a generative
        # lane cuts its own frames and has no assets dict to check against.
        # ...but only on a lane that has no pictures of its own. A generative lane names assets in
        # its plan that the *shots* fill — hybrid-video routes some beats to a generated clip and
        # renders the rest as cards — so an empty bundle assets dict there means "the picture came
        # from somewhere else", not "the picture is missing".
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
        # The same fault a third time, on the other kind of reference a scene can dangle. A
        # scene names a source as `source_id` (quote, screenshot) or `source_ids` (source card,
        # chart), and when the bundle has no such source the renderer prints **the raw id** where
        # the citation goes. Measured on `f02-penny` (2026-09-10): a quote card shipped reading
        # "Source: src_authored000001", and all seven checks here passed. `scenes/kinds.py`
        # already says why it matters — "a quote attributed to a source that does not exist is a
        # fabricated citation" — and the script writer is held to it, so this only bites a story
        # that went round the writer. That is exactly how the placeholder films got out too.
        #
        # Not gated on `draws_its_own`, unlike the assets check above: a shot can supply a
        # picture the bundle does not carry, but nothing downstream ever invents a source.
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

            # The **pristine** picture, and the manifest that says how it was made. Three defects
            # in one line, all now fixed:
            #
            #  * it measured `exports/bnd_run000000001.mp4` — the Remotion bundle. On the hybrid
            #    path that is only the typeset half; the picture that ships is `composed.mp4`,
            #    which interleaves the generated clips. So the check was reading a file that had
            #    none of the generated motion in it and then reporting on the motion.
            #  * the promise came from `spec.intent.startswith("animated")` over a 1000-character
            #    free-text field. False for every real brief, so the promise was always
            #    `chart_led` — the one value that makes the check unable to fail.
            #  * and it had no route counts, so it could not tell a card that does not move (fine)
            #    from a generated clip that does not move (a wasted generation).
            #
            # `final.mp4` is deliberately NOT the file: it carries burned-in captions, and caption
            # animation would register as scene animation in exactly the scenes that have none.
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
            # Whether the clips came from a real generator. On the mock a generated clip is a
            # static test pattern, so the "generated segments do not move" finding would fail
            # every offline run of the hybrid lane — measuring the backend, not the film.
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
    # An audio master is a deliverable too, and this stage used to have nothing to say about one.
    # The two audio lanes (audio-restore, voice-over-track) deliver a WAV and nothing else; their
    # QC node sat with an unwired input because the slot did not accept AUDIO, and the caveat in
    # each definition admitted the stage "says nothing about loudness or clipping here". It can:
    # the delivered file is on disk and ffmpeg measures integrated loudness, true peak and dead
    # air. Severity does the rest — a missing audio stream or a truncated master is a failure, a
    # 2.5 s pause in a recording of somebody talking is a fact worth recording and nothing more.
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


# Where a deliverable's shippable files live, and what each one is *for* at a destination. Scanned
# in this order so the manifest reads the way a person would list it: the film first.
DELIVERY_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("video", "exports/final.mp4"),
    # The post chain's own output. It is the deliverable on a lane that ends there — `image-upscale`
    # and `video-finish` have no cut to make — and an intermediate on a lane that goes on to
    # compose, where `final.mp4` above wins because it is listed first.
    ("video", f"exports/{POST_CHAIN_EXPORT}"),
    ("video", "exports/generated.mp4"),
    ("image", "exports/artboard.png"),
    # A still lane's deliverable is the anchor itself. `single-image` draws one picture and stops,
    # so there is no export, no cards and no audio to scan — and without this line the scan found
    # only `qc/report.json`, so `DeliveryPackage` refused a package carrying only metadata and the
    # four-stage lane failed on its last stage having already drawn the picture it was asked for.
    ("image", "anchors/anchor.png"),
    # `package_sequence` writes these three beside `sequence/frames/`, and for a set-of-stills lane
    # they *are* the deliverable, which is what the `sequence` role means: the frame set is the
    # thing being published, not a description of it. A lane that also cuts a film ships that film
    # as the video and these alongside it — see the guard in `_delivery_files`.
    ("sequence", "sequence/preview.mp4"),
    ("sequence", "sequence/contact-sheet.png"),
    ("audio", "audio/narration-mastered.wav"),
    ("caption", "captions/captions.srt"),
    ("caption", "captions/captions.vtt"),
    # What a transcribed recording said, in plain text. A deliverable in its own right for the
    # lanes that start from sound — the most useful by-product a lecture recording has — and
    # `text`, not `metadata`, because it is content rather than a description of content.
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
    """Every shippable file this deliverable actually produced, with a digest for each.

    Scanned rather than declared: what a lane produces depends on the lane, and a hand-written
    list of expected outputs per deliverable type would go stale the first time a lane changed.
    What is not negotiable is that a file in the manifest exists and its digest is of the bytes
    on disk right now — a manifest is a claim about bytes, and an unverified claim about bytes is
    the thing this stage used to make (`"status": "packaged"`).
    """
    import mimetypes

    from content_factory.schemas.delivery import DeliveryFile

    files = []
    seen: set[str] = set()
    # A lane that cut a film already ships that film. `sequence/preview.mp4` is an 8 fps proof
    # reel of the same frames, so on those lanes it is the same footage a second time; on a
    # set-of-stills lane, which cuts nothing, it is the deliverable. Decided here rather than in
    # the table because it depends on what else the run produced.
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
    # Carousels and image sets export one file per card, so they are collected by glob rather
    # than named: the count is a property of the copy, not of the lane.
    #
    # Two naming schemes reach this stage and both have to be scanned. A lane run through the
    # local runner writes the fixed names above plus `exports/card_*.png`. A campaign run through
    # ProductionWorkflow writes one file per *render bundle* instead — `bnd_card000000001.png`,
    # `bnd_art000000001.png`, `bnd_tl0000000001.narrated.mp4` — because `render_artboard` names
    # its output after the bundle it rendered. Scanning only the lane names found nothing but
    # `qc/report.json` for a three-card carousel, so the package carried only metadata and
    # `DeliveryPackage` refused it. It was right to: the defect was here, not in the contract.
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
    # The post chain's own output. `image-upscale` and `video-finish` end in a chain step, not in
    # an export: SeedVR2 enlarged a still from 2560x1440 to 3840x2160, wrote it under
    # `sequence/upscaled/frames/`, and the package came back carrying `qc/report.json` and nothing
    # else — a finishing lane failing on its last stage having already done the work. `chain.json`
    # names the final directory in `latest`, which is the one thing that knows which step was last.
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
    """One typed `DeliveryPackage` per destination, with a digest per file.

    This stage used to write `{"status": "packaged"}` and not one word about *what* was packaged,
    so the last step before a destination recorded neither the files nor their bytes — and a
    package for a film that had failed to render looked exactly like one for a film that had not.
    """
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
    """Hard-link the run's film into one flat directory a person can browse.

    A deliverable lives at ``deliverables/<id>/exports/final.mp4``, which is correct and unusable:
    213 run directories held 34 films between them and finding one meant knowing the path. The
    last stage of a run now also puts the film in ``<gallery>/<run>.mp4`` — one directory, named
    by the run, so "where are the videos" has an answer that does not involve a glob.

    A hard link rather than a copy: the bytes exist once, the gallery costs nothing, and deleting
    it cannot lose a deliverable. Falls back to a copy across filesystems.

    Only for a run whose project directory is inside the repo. A test with a `tmp_path` project
    would otherwise litter the checkout with one file per test that happens to package something,
    and the gallery is for the operator's own runs.

    The primary video only, and only the first: `DELIVERY_CANDIDATES` is ordered, so `final.mp4`
    wins over the post chain's own export on a lane that has both, which is the same precedence
    the packages use.
    """
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
    """The one file that is the film, or None for a lane that cut none.

    ``DELIVERY_CANDIDATES`` is ordered and `_delivery_files` preserves that order, so the first
    ``video`` is ``exports/final.mp4`` on a lane that has both it and the post chain's own export.
    Shared with the harvest, which has to pick the same file out of the same manifest read on
    another machine — two implementations of "which one is the film" would eventually disagree.
    """
    return next((f for f in files if getattr(f, "role", "") == "video"), None)


def stage_package_qc(ctx: StageContext) -> StageOutput:
    result = {"passed": True}
    _write(ctx.ddir() / "qc" / "package.json", json.dumps(result, indent=1))
    return StageOutput(_hash_obj(result), result)


def _log_execution(ctx: StageContext, unit: str, telemetry: dict | None = None) -> None:
    """One line per unit of real work. ``telemetry`` appends what it cost, tab-separated.

    The log stays greppable — the unit name is still the whole first field, and every existing
    reader counts lines by prefix — but a line for an uncached generation now carries the wall
    clock, the card before and after, and the server's own timing. That is the record that was
    missing every time a run OOMed or a stage took three times what the server reported.
    """
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
