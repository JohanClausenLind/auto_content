"""The explainer lane as an explicit step graph with a durable, content-addressed ledger (F4)."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from content_factory.artifacts.store import FilesystemArtifactStore
from content_factory.audio.mix import ffmpeg
from content_factory.explainer import ocr, render, reviewer_server, sources
from content_factory.explainer.capture import load_capture
from content_factory.explainer.compile import (
    COMPILER_VERSION,
    LayoutDiagrams,
    compile_episode,
    write_bundle,
)
from content_factory.explainer.errors import (
    ContractIssue,
    EpisodeInvalidError,
    explain_validation_error,
)
from content_factory.explainer.evidence import (
    check_evidence,
    check_narration,
    check_spec_text_numbers,
)
from content_factory.explainer.fonts import FONTS_VERSION
from content_factory.explainer.mix import MixResult, mix_episode
from content_factory.explainer.narration import Aligner, Asr, Synth, assemble_narration
from content_factory.explainer.passages import EXTRACTOR_VERSION, QuoteRequest, Tile
from content_factory.explainer.qc import QcFinding
from content_factory.explainer.qc_checks import QC_VERSION, run_deterministic_qc, sampled_ms
from content_factory.explainer.repair import repair_loop
from content_factory.explainer.review import PROMPT_SHA256 as QC_PROMPT_SHA256
from content_factory.explainer.review import RUBRIC_VERSION as QC_RUBRIC_VERSION
from content_factory.explainer.review import propose_repair, report_from_findings
from content_factory.explainer.reviewer_prompts import RUBRIC_VERSION, prompt_sha256
from content_factory.explainer.tokens_gen import DESIGN_SYSTEM_VERSION
from content_factory.explainer.tts_bench import (
    CANDIDATE_BY_KEY,
    Candidate,
    LicenseGateError,
    SynthRequest,
    selectable,
)
from content_factory.explainer.validate import check_bindings, check_state
from content_factory.runners.registry import RunStopped, register_run
from content_factory.schemas.audio import LoudnessReport
from content_factory.schemas.base import OpaqueId, canonical_dumps, file_sha256
from content_factory.schemas.explainer import (
    AssetRef,
    CaptureAsset,
    CategoryCoverage,
    CoverageSpan,
    EvidencePack,
    ExplainerRenderBundle,
    NarrationManifest,
    ReviewedArtifact,
    ReviewerIdentity,
    ReviewFinding,
    ReviewReport,
    ScriptPlan,
    ScriptSegment,
    SourceCaptureManifest,
    SourceDocumentTemplate,
    TimeInterval,
    VisualSpec,
    VoiceSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "output" / "explainer" / "episodes"
MUSIC_MANIFEST = REPO_ROOT / "assets" / "music" / "manifest.json"
SFX_MANIFEST = REPO_ROOT / "assets" / "sfx" / "manifest.json"
PIPELINE_VERSION = "1"
LEDGER_NAME = "ledger.json"
QUEUE_NAME = "review-queue.json"
CONFIG_NAME = "config.json"
WORKSPACE = "ws_explainer"
# Full size at --crf 23, the final at Remotion's default 18. Sixty, 2026-09-26: render time is
# frame-bound (~40 s at any crf) and crf 28/35 fail text_apca (Lc 73.9/70.4 < 75); 23 gives 75.8.
ANIMATIC_CRF = 23
MAX_REPAIR_ROUNDS = 2
SYNTH_ON_CPU = frozenset({"kokoro"})
MUX_ARGS = (
    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    "-ar", "48000", "-af", "apad", "-shortest", "-movflags", "+faststart",
)  # fmt: skip
# bundle.mjs hashes these plus public/; public/ holds only tiles staged by content hash (already in
# the bundle) and fonts copied from the lockfile's packages, so hashing it would chase side effects.
RENDERER_INPUTS = (
    "apps/renderer/src",
    "packages/content-ui/src",
    "packages/video-ui/src",
    "packages/explainer-ui/src",
    "fixtures/demo",
    "fixtures/explainer/render",
)
RENDERER_LOCKFILE = "pnpm-lock.yaml"
TILE_NAME = re.compile(r"^tile-(\d+)\.png$")
STEP_NAMES: tuple[str, ...] = (
    "freeze_evidence",
    "lock_script",
    "plan_visuals",
    "capture_sources",
    "narrate",
    "compile",
    "render_animatic",
    "qc_animatic",
    "review_animatic",
    "repair",
    "render_final",
    "mix",
    "mux",
    "qc_final",
    "review_final",
    "export",
)

Resource = Literal["cpu", "gpu"]
StepStatus = Literal["done", "failed", "stopped", "blocked"]
RunStatus = Literal["done", "blocked", "stopped", "failed", "incomplete"]
RenderVideo = Callable[[ExplainerRenderBundle, Path, int | None], Path]
RenderStills = Callable[[ExplainerRenderBundle, Path, Sequence[int]], list[Path]]
Reviewer = Callable[[ExplainerRenderBundle, Path], ReviewReport]
Capture = Callable[..., tuple[SourceCaptureManifest, tuple[Tile, ...]]]
MixEpisode = Callable[[Path, Path | None, Sequence[tuple[int, Path]], Path], MixResult]
Probe = Callable[[str], bool]


class Qc(Protocol):
    """run_deterministic_qc's shape, so a test can hand the pipeline canned findings."""

    def __call__(
        self,
        bundle: ExplainerRenderBundle,
        mp4: Path,
        *,
        pack: EvidencePack,
        script: ScriptPlan,
        spec: VisualSpec,
        narration: NarrationManifest | None = None,
        mix_loudness: LoudnessReport | None = None,
    ) -> Sequence[QcFinding]: ...


# --- configuration ---


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SfxCue(_Strict):
    sfx_id: str = Field(min_length=1, max_length=120)
    at_ms: int = Field(ge=0)


class CaptureInput(_Strict):
    """A capture made before the episode: its manifest, the WACZ it came from, and the tiles."""

    manifest: Path
    wacz: Path
    url: str = Field(min_length=1, max_length=2000)
    tiles_dir: Path


class ReviewerSettings(_Strict):
    """The advisory VLM reviewer; disabled or unreachable yields an uncertain report saying so."""

    enabled: bool = False
    base_url: str = reviewer_server.DEFAULT_CONFIG.base_url
    model: str = reviewer_server.DEFAULT_CONFIG.served_name
    model_revision: str = ""
    seed: int = 0


class EpisodeConfig(_Strict):
    """One episode's inputs; EpisodeConfig.load resolves relative paths against the file."""

    episode_id: OpaqueId
    pack: Path
    script: Path
    spec: Path
    takes_dir: Path | None = None
    captures: tuple[CaptureInput, ...] = ()
    voice: VoiceSpec
    synth: str = "kokoro"
    music_id: str | None = None
    sfx: tuple[SfxCue, ...] = ()
    reviewer: ReviewerSettings = ReviewerSettings()
    seed: int = 0
    fps: int = Field(default=30, ge=24, le=60)
    output_root: Path = DEFAULT_OUTPUT_ROOT

    @field_validator("synth")
    @classmethod
    def _licensed(cls, key: str) -> str:
        synth_candidate(key)
        return key

    @property
    def episode_dir(self) -> Path:
        return self.output_root / self.episode_id

    @classmethod
    def load(cls, path: Path) -> EpisodeConfig:
        data = json.loads(path.read_text(encoding="utf-8"))
        base = path.resolve().parent
        for key in ("pack", "script", "spec", "takes_dir", "output_root"):
            if data.get(key):
                data[key] = str(base / data[key])
        for item in data.get("captures") or ():
            for key in ("manifest", "wacz", "tiles_dir"):
                if item.get(key):
                    item[key] = str(base / item[key])
        return cls.model_validate(data)


def synth_candidate(key: str) -> Candidate:
    """The TTS candidate for a synth key, refused unless its licence allows commercial output."""
    candidate = CANDIDATE_BY_KEY.get(key)
    if candidate is None:
        known = ", ".join(sorted(CANDIDATE_BY_KEY))
        msg = f"unknown synth {key!r}; known: {known}"
        raise ValueError(msg)
    if not selectable(candidate):
        msg = f"{key} is excluded: licence {candidate.license!r}: {candidate.license_note}"
        raise LicenseGateError(msg)
    if candidate.needs_reference:
        msg = f"{key} clones a creator reference recording and the episode config names none"
        raise ValueError(msg)
    return candidate


def candidate_synth(key: str, seed: int) -> Synth:
    """narration's Synth over a licensed tts_bench runner."""
    runner = synth_candidate(key).runner
    if runner is None:
        msg = f"{key} has no runner"
        raise ValueError(msg)

    def synth(segment: ScriptSegment, _voice: VoiceSpec, path: Path) -> None:
        result = runner(SynthRequest(text=segment.spoken_text, out=path, seed=seed))
        if result.wav.resolve() != path.resolve():
            shutil.move(result.wav, path)

    return synth


# --- seams ---


def render_video(bundle: ExplainerRenderBundle, out: Path, crf: int | None) -> Path:
    """Stage the capture tiles, write the bundle beside the mp4 and render it with Remotion."""
    bundle_path = write_bundle(render.stage_captures(bundle), out.with_suffix(".bundle.json"))
    return render.render_bundle(bundle_path, out, crf=crf).out


def render_stills(
    bundle: ExplainerRenderBundle, out_dir: Path, frames: Sequence[int]
) -> list[Path]:
    bundle_path = write_bundle(render.stage_captures(bundle), out_dir / "bundle.json")
    return render.render_frames(bundle_path, out_dir, frames, mode="stills")


@dataclass(frozen=True)
class Seams:
    """Every call that reaches Node, a model, a server or the web; tests replace them."""

    render_video: RenderVideo = render_video
    render_stills: RenderStills = render_stills
    layout_diagrams: LayoutDiagrams | None = None
    qc: Qc = run_deterministic_qc
    reviewer: Reviewer | None = None
    probe_reviewer: Probe = reviewer_server.is_running
    synth: Synth | None = None
    aligner: Aligner | None = None
    asr: Asr | None = None
    capture: Capture = sources.capture_source
    mix: MixEpisode = mix_episode


def seam_id(seam: object) -> str:
    """Which implementation ran, so a fake's output is never reused by a real run."""
    if seam is None:
        return "default"
    name = getattr(seam, "__qualname__", type(seam).__qualname__)
    return f"{getattr(seam, '__module__', type(seam).__module__)}.{name}"


# --- ledger ---


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_atomic(path: Path, text: str) -> Path:
    """Write via tmp + rename: a stop can land between any two bytecodes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def write_model(path: Path, model: BaseModel) -> Path:
    return write_atomic(path, canonical_dumps(model.model_dump(mode="json")) + "\n")


class Ledger:
    """ledger.json: one entry per step, in pipeline order, rewritten atomically after each."""

    def __init__(self, episode_dir: Path, episode_id: str) -> None:
        self.episode_dir = episode_dir
        self.path = episode_dir / LEDGER_NAME
        if self.path.is_file():
            self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {
                "episode_id": episode_id,
                "ledger_version": 1,
                "status": "new",
                "ready": False,
                "stopped": None,
                "updated_at": None,
                "steps": [],
            }

    @classmethod
    def load(cls, episode_dir: Path) -> Ledger:
        path = episode_dir / LEDGER_NAME
        if not path.is_file():
            msg = f"no ledger at {path}; run the episode first"
            raise FileNotFoundError(msg)
        return cls(episode_dir, episode_dir.name)

    def entry(self, step: str) -> dict[str, Any] | None:
        return next((e for e in self.data["steps"] if e["step"] == step), None)

    def put(self, entry: dict[str, Any]) -> None:
        steps = [e for e in self.data["steps"] if e["step"] != entry["step"]] + [entry]
        order = {name: i for i, name in enumerate(STEP_NAMES)}
        self.data["steps"] = sorted(steps, key=lambda e: order.get(e["step"], len(order)))
        self.write()

    def write(self) -> None:
        self.data["updated_at"] = now_iso()
        write_atomic(self.path, json.dumps(self.data, indent=1, sort_keys=True) + "\n")

    def resolve(self, recorded: str) -> Path:
        path = Path(recorded)
        return path if path.is_absolute() else self.episode_dir / path

    def artifact(self, step: str, name: str) -> Path:
        entry = self.entry(step)
        if entry is None or name not in entry["artifacts"]:
            msg = f"ledger has no artifact {step}.{name}; run {step} first"
            raise KeyError(msg)
        return self.resolve(entry["artifacts"][name]["path"])

    def artifact_sha(self, step: str, name: str) -> str | None:
        entry = self.entry(step)
        record = (entry or {}).get("artifacts", {}).get(name)
        return None if record is None else str(record["sha256"])

    def output_digest(self, step: str) -> str:
        entry = self.entry(step)
        if entry is None:
            return ""
        shas = {name: record["sha256"] for name, record in entry["artifacts"].items()}
        return hashlib.sha256(canonical_dumps(shas).encode()).hexdigest()

    def reusable(self, step: str, fingerprint: str) -> bool:
        """Done with this fingerprint, and every artifact still on disk with its recorded hash."""
        entry = self.entry(step)
        if entry is None or entry["status"] != "done" or entry["fingerprint"] != fingerprint:
            return False
        for record in entry["artifacts"].values():
            path = self.resolve(record["path"])
            if not path.is_file() or file_sha256(path) != record["sha256"]:
                return False
        return True


# --- the run ---


@dataclass(frozen=True)
class StepOutput:
    artifacts: Mapping[str, Path]
    status: Literal["done", "blocked"] = "done"
    facts: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Ctx:
    config: EpisodeConfig
    seams: Seams
    ledger: Ledger
    store: FilesystemArtifactStore
    _renderer_hash: str | None = None

    @property
    def dir(self) -> Path:
        return self.config.episode_dir

    def out(self, *parts: str) -> Path:
        path = self.dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def artifact(self, step: str, name: str) -> Path:
        return self.ledger.artifact(step, name)

    def renderer_hash(self) -> str:
        if self._renderer_hash is None:
            self._renderer_hash = renderer_source_hash()
        return self._renderer_hash

    def pack(self) -> EvidencePack:
        return read_model(EvidencePack, self.artifact("freeze_evidence", "pack"))

    def script(self) -> ScriptPlan:
        return read_model(ScriptPlan, self.artifact("lock_script", "script"))

    def spec(self) -> VisualSpec:
        return read_model(VisualSpec, self.artifact("plan_visuals", "spec"))

    def narration(self) -> NarrationManifest:
        return read_model(NarrationManifest, self.artifact("narrate", "manifest"))

    def bundle(self, step: str) -> ExplainerRenderBundle:
        return read_model(ExplainerRenderBundle, self.artifact(step, "bundle"))

    def captures(self) -> tuple[CaptureAsset, ...]:
        index = json.loads(self.artifact("capture_sources", "index").read_text(encoding="utf-8"))
        return tuple(read_model(CaptureAsset, self.ledger.resolve(p)) for p in index["captures"])


@dataclass(frozen=True)
class Step:
    """One node: named input digests, a run, and the upstream steps whose outputs it reads."""

    name: str
    inputs: Callable[[Ctx], Mapping[str, str]]
    run: Callable[[Ctx], StepOutput]
    resource: Resource = "cpu"
    after: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    episode_dir: Path
    ran: tuple[str, ...]
    skipped: tuple[str, ...]
    ready: bool
    stopped_at: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "episode_dir": str(self.episode_dir)}


def fingerprint(step: Step, inputs: Mapping[str, str], ledger: Ledger) -> str:
    """sha256 over the step's named inputs plus its upstream steps' output digests."""
    payload = {
        "pipeline": PIPELINE_VERSION,
        "step": step.name,
        "inputs": dict(inputs),
        "upstream": {dep: ledger.output_digest(dep) for dep in step.after},
    }
    return hashlib.sha256(canonical_dumps(payload).encode()).hexdigest()


def run(
    config: EpisodeConfig,
    *,
    from_step: str | None = None,
    until: str | None = None,
    stop: Callable[[], str | None] | None = None,
    seams: Seams | None = None,
    log: Callable[[str], None] = print,
) -> RunResult:
    """Run the lane: reuse every step whose fingerprint and artifacts hold, stop at boundaries."""
    steps = pipeline_steps(config)
    first_forced = _step_index(from_step, "--from") if from_step else None
    last = _step_index(until, "--until") if until else len(steps) - 1
    episode_dir = config.episode_dir
    episode_dir.mkdir(parents=True, exist_ok=True)
    write_model(episode_dir / CONFIG_NAME, config)
    ledger = Ledger(episode_dir, config.episode_id)
    store = FilesystemArtifactStore(config.output_root.parent / "store")
    ctx = Ctx(config, seams or Seams(), ledger, store)
    ran: list[str] = []
    skipped: list[str] = []

    def finish(status: RunStatus, *, at: str | None = None, error: str | None = None) -> RunResult:
        ready = status == "done" and all(
            (ledger.entry(name) or {}).get("status") == "done" for name in STEP_NAMES
        )
        ledger.data.update(status=status, ready=ready)
        ledger.write()
        return RunResult(status, episode_dir, tuple(ran), tuple(skipped), ready, at, error)

    ledger.data.update(status="running", ready=False, stopped=None)
    ledger.write()
    with register_run(f"explainer-{config.episode_id}", episode_dir) as handle:
        for index, step in enumerate(steps[: last + 1]):
            reason = (stop() if stop else None) or handle.stop_reason()
            if reason:
                ledger.data["stopped"] = {"before": step.name, "reason": reason}
                log(f"    STOPPED before {step.name}: {reason}")
                return finish("stopped", at=step.name)
            handle.note(step=step.name)
            entry: dict[str, Any] = {
                "step": step.name,
                "resource": step.resource,
                "fingerprint": "",
                "inputs": {},
                "artifacts": {},
                "facts": {},
                "started_at": now_iso(),
                "finished_at": None,
                "status": "failed",
                "error": None,
            }
            started = time.monotonic()
            try:
                inputs = dict(step.inputs(ctx))
                entry.update(inputs=inputs, fingerprint=fingerprint(step, inputs, ledger))
                forced = first_forced is not None and index >= first_forced
                if not forced and ledger.reusable(step.name, entry["fingerprint"]):
                    skipped.append(step.name)
                    log(f"==> {step.name}: reused {entry['fingerprint'][:12]}")
                    continue
                log(f"==> {step.name} [{step.resource}]")
                output = step.run(ctx)
                entry["artifacts"] = {
                    name: _record(ctx, step.name, path) for name, path in output.artifacts.items()
                }
            except RunStopped as stopped:
                entry.update(status="stopped", error=stopped.reason, finished_at=now_iso())
                ledger.data["stopped"] = {"during": step.name, "reason": stopped.reason}
                ledger.put(entry)
                log(f"    STOPPED during {step.name}: {stopped.reason}")
                return finish("stopped", at=step.name)
            except Exception as error:
                reason = handle.stop_reason()
                status: StepStatus = "stopped" if reason else "failed"
                message = reason or f"{type(error).__name__}: {error}"
                entry.update(status=status, error=message, finished_at=now_iso())
                if reason:
                    ledger.data["stopped"] = {"during": step.name, "reason": reason}
                ledger.put(entry)
                log(f"    {status.upper()}: {message}")
                return finish(status, at=step.name, error=None if reason else message)
            entry.update(
                status=output.status,
                facts={**output.facts, "seconds": round(time.monotonic() - started, 1)},
                finished_at=now_iso(),
            )
            ledger.put(entry)
            ran.append(step.name)
            if output.status == "blocked":
                log(f"    BLOCKED: {step.name}; see {episode_dir / QUEUE_NAME}")
                return finish("blocked", at=step.name)
    return finish("done" if last == len(steps) - 1 else "incomplete")


def _step_index(name: str | None, flag: str) -> int:
    if name not in STEP_NAMES:
        msg = f"{flag} {name!r} is not a step; steps: {', '.join(STEP_NAMES)}"
        raise ValueError(msg)
    return STEP_NAMES.index(name)


def _record(ctx: Ctx, step: str, path: Path) -> dict[str, str]:
    ref = ctx.store.put_file(WORKSPACE, step, path)
    resolved = path.resolve()
    inside = resolved.is_relative_to(ctx.dir.resolve())
    shown = resolved.relative_to(ctx.dir.resolve()).as_posix() if inside else str(resolved)
    return {"sha256": ref.sha256, "path": shown, "store_key": ref.key}


def pipeline_steps(config: EpisodeConfig) -> tuple[Step, ...]:
    synth: Resource = "cpu" if config.synth in SYNTH_ON_CPU else "gpu"
    reviewer: Resource = "gpu" if config.reviewer.enabled else "cpu"
    evidence = ("freeze_evidence", "lock_script")
    return (
        Step("freeze_evidence", _freeze_inputs, _freeze_evidence),
        Step("lock_script", _lock_inputs, _lock_script, after=("freeze_evidence",)),
        Step("plan_visuals", _plan_inputs, _plan_visuals, after=evidence),
        Step("capture_sources", _capture_inputs, _capture_sources, after=("freeze_evidence",)),
        Step("narrate", _narrate_inputs, _narrate, synth, after=("lock_script",)),
        Step(
            "compile",
            _compile_inputs,
            _compile,
            after=(*evidence, "plan_visuals", "capture_sources", "narrate"),
        ),
        Step("render_animatic", _animatic_inputs, _render_animatic, after=("compile",)),
        Step(
            "qc_animatic",
            _qc_inputs,
            _qc_animatic,
            after=(*evidence, "narrate", "compile", "render_animatic"),
        ),
        Step(
            "review_animatic",
            _reviewer_inputs,
            _review_animatic,
            reviewer,
            after=(*evidence, "compile", "render_animatic"),
        ),
        Step("repair", _repair_inputs, _repair, after=STEP_NAMES[:8]),
        Step("render_final", _final_inputs, _render_final, after=("repair",)),
        Step("mix", _mix_inputs, _mix, after=("narrate",)),
        Step("mux", _mux_inputs, _mux, after=("render_final", "mix")),
        Step(
            "qc_final",
            _qc_inputs,
            _qc_final,
            after=(*evidence, "narrate", "repair", "mix", "mux"),
        ),
        Step(
            "review_final",
            _reviewer_inputs,
            _review_final,
            reviewer,
            after=(*evidence, "repair", "mux"),
        ),
        Step("export", _export_inputs, _export, after=STEP_NAMES[:-1]),
    )


# --- steps: evidence, script, visuals, captures, narration ---


def read_model[T: BaseModel](model: type[T], path: Path) -> T:
    """A contract from disk; a validation error comes back as actionable issues."""
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise EpisodeInvalidError(explain_validation_error(error, model)) from None


def _raise_issues(issues: Sequence[ContractIssue]) -> None:
    if issues:
        raise EpisodeInvalidError(list(issues))


def _freeze_inputs(ctx: Ctx) -> dict[str, str]:
    return {"pack_hash": read_model(EvidencePack, ctx.config.pack).pack_hash()}


def _freeze_evidence(ctx: Ctx) -> StepOutput:
    pack = read_model(EvidencePack, ctx.config.pack)
    _raise_issues(check_evidence(pack))
    if pack.frozen_at is None:
        pack = pack.frozen(now_iso())
    return StepOutput({"pack": write_model(ctx.out("evidence", "pack.json"), pack)})


def _lock_inputs(ctx: Ctx) -> dict[str, str]:
    return {"script_hash": read_model(ScriptPlan, ctx.config.script).script_hash()}


def _lock_script(ctx: Ctx) -> StepOutput:
    pack = ctx.pack()
    script = read_model(ScriptPlan, ctx.config.script)
    issues: list[ContractIssue] = []
    if script.pack_hash != pack.pack_hash():
        issues.append(
            ContractIssue(
                kind="stale_binding",
                where="ScriptPlan.pack_hash",
                message=(
                    f"script {script.script_id} is bound to {script.pack_hash[:12]} but the frozen "
                    f"pack hashes to {pack.pack_hash()[:12]}."
                ),
                fix="re-plan the script against the frozen pack.",
                ids=(script.script_id,),
            )
        )
    _raise_issues(issues + check_narration(pack, script))
    if script.locked_at is None:
        script = script.model_copy(update={"locked_at": now_iso()})
    return StepOutput({"script": write_model(ctx.out("script", "script.json"), script)})


def _plan_inputs(ctx: Ctx) -> dict[str, str]:
    spec = read_model(VisualSpec, ctx.config.spec)
    return {"spec_hash": spec.spec_hash(), "design_system_version": str(DESIGN_SYSTEM_VERSION)}


def _plan_visuals(ctx: Ctx) -> StepOutput:
    """Validate and bind the VisualSpec given as a file; an LLM planner is a Phase 6 piece."""
    pack, script = ctx.pack(), ctx.script()
    spec = read_model(VisualSpec, ctx.config.spec)
    issues = check_bindings(pack, script, spec) + check_state(spec)
    _raise_issues(issues + check_spec_text_numbers(pack, spec))
    return StepOutput({"spec": write_model(ctx.out("visuals", "spec.json"), spec)})


def _shown_captures(spec: VisualSpec) -> list[AssetRef]:
    assets = {a.asset_id: a for a in spec.assets}
    wanted = {
        s.template.capture_asset_id
        for s in spec.scenes
        if isinstance(s.template, SourceDocumentTemplate)
    }
    return sorted((assets[a] for a in wanted), key=lambda a: a.asset_id)


def stored_tiles(item: CaptureInput, manifest: SourceCaptureManifest) -> tuple[Tile, ...]:
    """The resolver's recorded offsets from <capture_id>.tiles.json, else rebuilt from the names."""
    index = item.manifest.parent / f"{manifest.capture_id}.tiles.json"
    if index.is_file():
        entries = json.loads(index.read_text(encoding="utf-8"))
        tiles = (Tile(REPO_ROOT / e["path"], int(e["y_px"])) for e in entries)
        return tuple(sorted(tiles, key=lambda t: t.y_px))
    # resolve-passages.mjs names tiles tile-NNNN.png and scrolls to min(N·vh, page - vh); the
    # browser may clamp or the page may grow, and the OCR re-verification catches either.
    height = manifest.viewport.height
    bottom = max(0, int(manifest.page_height_px) - height)
    found = [
        (int(m.group(1)), path)
        for path in (sorted(item.tiles_dir.iterdir()) if item.tiles_dir.is_dir() else ())
        if (m := TILE_NAME.match(path.name))
    ]
    return tuple(Tile(path, min(n * height, bottom)) for n, path in sorted(found))


def _capture_inputs(ctx: Ctx) -> dict[str, str]:
    stored = []
    for item in ctx.config.captures:
        manifest = read_model(SourceCaptureManifest, item.manifest)
        tiles = stored_tiles(item, manifest)
        stored.append(
            {
                "manifest": file_sha256(item.manifest),
                "wacz": file_sha256(item.wacz),
                "url": item.url,
                "tiles": [[t.y_px, file_sha256(t.path)] for t in tiles if t.path.is_file()],
            }
        )
    shown = [[a.asset_id, a.capture_id, a.sha256] for a in _shown_captures(ctx.spec())]
    return {
        "stored": canonical_dumps(stored),
        "shown": canonical_dumps(shown),
        "extractor_version": EXTRACTOR_VERSION,
        "ocr_threshold": str(ocr.OCR_THRESHOLD),
        "capture": seam_id(ctx.seams.capture),
    }


def _capture_sources(ctx: Ctx) -> StepOutput:
    """Stored captures are re-verified; a shown capture nobody stored is captured live."""
    pack, spec = ctx.pack(), ctx.spec()
    artifacts: dict[str, Path] = {}
    assets: dict[str, CaptureAsset] = {}
    try:
        for item in ctx.config.captures:
            asset = _stored_capture(ctx, item)
            assets[asset.capture_id] = asset
            artifacts[f"wacz:{asset.capture_id}"] = item.wacz
        for ref in _shown_captures(spec):
            if ref.capture_id not in assets:
                asset = _live_capture(ctx, pack, spec, ref)
                assets[asset.capture_id] = asset
    except EpisodeInvalidError as error:
        items = [_issue_item(issue) for issue in error.issues]
        return StepOutput(
            {"queue": _write_queue(ctx, "capture_sources", items)},
            status="blocked",
            facts={"blocked": len(items)},
        )
    paths = []
    for capture_id, asset in sorted(assets.items()):
        path = write_model(ctx.out("captures", f"{capture_id}.json"), asset)
        paths.append(path.relative_to(ctx.dir).as_posix())
        artifacts[f"capture:{capture_id}"] = path
        for i, tile in enumerate(asset.tiles):
            artifacts[f"tile:{capture_id}:{i}"] = Path(tile.path)
    index = write_atomic(ctx.out("captures", "index.json"), canonical_dumps({"captures": paths}))
    _clear_queue(ctx)
    return StepOutput({**artifacts, "index": index}, facts={"captures": len(paths)})


def _stored_capture(ctx: Ctx, item: CaptureInput) -> CaptureAsset:
    manifest = read_model(SourceCaptureManifest, item.manifest)
    actual = file_sha256(item.wacz)
    if actual != manifest.artifact_sha256:
        raise EpisodeInvalidError(
            [
                ContractIssue(
                    kind="stale_binding",
                    where=f"capture {manifest.capture_id}",
                    message=(
                        f"{item.wacz.name} hashes to {actual[:12]} but the manifest was made from "
                        f"{manifest.artifact_sha256[:12]}."
                    ),
                    fix="point the config at the WACZ the manifest was made from, or re-capture.",
                    ids=(manifest.capture_id,),
                )
            ]
        )
    tiles = stored_tiles(item, manifest)
    if tiles:
        return sources.capture_asset(ocr.verify_manifest(manifest, tiles), tiles)
    # No stored tiles: replay the stored WACZ (never the live page) to shoot and verify them again.
    replayed, tiles = sources.manifest_from_capture(
        load_capture(item.wacz, item.url, manifest.viewport),
        [QuoteRequest(q.text, q.occurrence_index, q.claim_ids) for q in manifest.quotes],
        ctx.dir / "captures" / manifest.capture_id,
        source=_source_info(manifest),
    )
    return sources.capture_asset(replayed, tiles)


def _source_info(manifest: SourceCaptureManifest) -> sources.SourceInfo:
    return sources.SourceInfo(
        source_id=manifest.source_id,
        publisher=manifest.publisher,
        title=manifest.title,
        author=manifest.author,
        published_at=manifest.published_at,
    )


def _live_capture(ctx: Ctx, pack: EvidencePack, spec: VisualSpec, ref: AssetRef) -> CaptureAsset:
    scene_sources = {
        sid
        for s in spec.scenes
        if isinstance(s.template, SourceDocumentTemplate)
        and s.template.capture_asset_id == ref.asset_id
        for sid in s.source_ids
    }
    source = next(
        (s for s in pack.sources if s.capture_id == ref.capture_id or s.source_id in scene_sources),
        None,
    )
    if source is None:
        raise EpisodeInvalidError(
            [
                ContractIssue(
                    kind="invalid_reference",
                    where=f"VisualSpec asset {ref.asset_id}",
                    message=f"capture {ref.capture_id} is not stored and no pack source names it.",
                    fix="add it to EpisodeConfig.captures, or set EvidenceSource.capture_id.",
                    ids=(ref.asset_id, ref.capture_id or ""),
                )
            ]
        )
    claims_of = {
        item.item_id: tuple(c.claim_id for c in pack.claims if item.item_id in c.evidence_ids)
        for item in pack.items
    }
    quotes = [
        QuoteRequest(item.passage, claim_ids=claims_of[item.item_id])
        for item in pack.items
        if item.source_id == source.source_id
    ]
    info = sources.SourceInfo(
        source.source_id, source.publisher, source.title, source.author, source.published_at
    )
    out_dir = ctx.dir / "captures" / f"live-{source.source_id}"
    manifest, tiles = ctx.seams.capture(source.url, quotes, out_dir, source=info)
    if manifest.capture_id != ref.capture_id:
        raise EpisodeInvalidError(
            [
                ContractIssue(
                    kind="stale_binding",
                    where=f"VisualSpec asset {ref.asset_id}",
                    message=(
                        f"a live capture of {source.url} is {manifest.capture_id} (artifact "
                        f"{manifest.artifact_sha256[:12]}); the spec binds {ref.capture_id}."
                    ),
                    fix=(
                        f"rebind asset {ref.asset_id} to capture {manifest.capture_id}, or store "
                        f"the capture the spec was planned against in EpisodeConfig.captures."
                    ),
                    ids=(ref.asset_id, manifest.capture_id),
                )
            ]
        )
    return sources.capture_asset(manifest, tiles)


def _recorded_takes(ctx: Ctx, script: ScriptPlan) -> dict[str, Path]:
    folder = ctx.config.takes_dir
    if folder is None:
        return {}
    takes = {s.segment_id: folder / f"{s.segment_id}.wav" for s in script.segments}
    return {sid: path for sid, path in takes.items() if path.is_file()}


def _narrate_inputs(ctx: Ctx) -> dict[str, str]:
    script = ctx.script()
    takes = {sid: file_sha256(p) for sid, p in _recorded_takes(ctx, script).items()}
    seams = ctx.seams
    return {
        "script_hash": script.script_hash(),
        "takes": canonical_dumps(takes),
        "voice": ctx.config.voice.canonical_json(),
        "synth": ctx.config.synth if seams.synth is None else seam_id(seams.synth),
        "seed": str(ctx.config.seed),
        "aligner": seam_id(seams.aligner),
        "asr": seam_id(seams.asr),
    }


def _narrate(ctx: Ctx) -> StepOutput:
    script = ctx.script()
    config, seams = ctx.config, ctx.seams
    manifest, stem = assemble_narration(
        script,
        _recorded_takes(ctx, script),
        synth=seams.synth or candidate_synth(config.synth, config.seed),
        voice=config.voice,
        out_dir=ctx.dir / "narration",
        seed=config.seed,
        asr=seams.asr,
        aligner=seams.aligner,
    )
    synthesized = sum(t.kind == "synthesized" for t in manifest.takes)
    return StepOutput(
        {"manifest": ctx.dir / "narration" / "narration-manifest.json", "stem": stem},
        facts={"duration_ms": manifest.total_duration_ms, "synthesized_takes": synthesized},
    )


# --- steps: compile, render, QC, review, repair ---


def _compile_inputs(ctx: Ctx) -> dict[str, str]:
    return {
        "compiler_version": COMPILER_VERSION,
        "design_system_version": str(DESIGN_SYSTEM_VERSION),
        "fonts_version": FONTS_VERSION,
        "fps": str(ctx.config.fps),
        "layout": seam_id(ctx.seams.layout_diagrams),
    }


def _compile_spec(ctx: Ctx, spec: VisualSpec) -> ExplainerRenderBundle:
    return compile_episode(
        ctx.pack(),
        ctx.script(),
        spec,
        narration=ctx.narration(),
        captures=ctx.captures(),
        layout_diagrams=ctx.seams.layout_diagrams,
        fps=ctx.config.fps,
    )


def _compile(ctx: Ctx) -> StepOutput:
    bundle = _compile_spec(ctx, ctx.spec())
    frames = bundle.timeline.total_frames
    return StepOutput(
        {"bundle": write_model(ctx.out("compile", "bundle.json"), bundle)},
        facts={"total_frames": frames},
    )


def _animatic_inputs(ctx: Ctx) -> dict[str, str]:
    return {
        "renderer_source_hash": ctx.renderer_hash(),
        "crf": str(ANIMATIC_CRF),
        "renderer": seam_id(ctx.seams.render_video),
    }


def _render_animatic(ctx: Ctx) -> StepOutput:
    out = ctx.out("animatic", "animatic.mp4")
    mp4 = ctx.seams.render_video(ctx.bundle("compile"), out, ANIMATIC_CRF)
    return StepOutput({"mp4": mp4})


def _qc_inputs(ctx: Ctx) -> dict[str, str]:
    return {
        "qc_version": QC_VERSION,
        "qc_prompt_sha256": QC_PROMPT_SHA256,
        "qc_rubric_version": QC_RUBRIC_VERSION,
        "qc": seam_id(ctx.seams.qc),
    }


def _run_qc(
    ctx: Ctx, bundle: ExplainerRenderBundle, mp4: Path, loudness: LoudnessReport | None
) -> list[QcFinding]:
    return list(
        ctx.seams.qc(
            bundle,
            mp4,
            pack=ctx.pack(),
            script=ctx.script(),
            spec=bundle.spec,
            narration=ctx.narration(),
            mix_loudness=loudness,
        )
    )


def _qc_record(
    ctx: Ctx,
    step: str,
    bundle: ExplainerRenderBundle,
    mp4: Path,
    kind: Literal["animatic", "mp4"],
    findings: Sequence[QcFinding],
) -> dict[str, Path]:
    artifact = ReviewedArtifact(kind=kind, sha256=file_sha256(mp4))
    report = report_from_findings(
        findings, bundle=bundle, artifact=artifact, sampled=sampled_ms(bundle)
    )
    dumped = json.dumps([asdict(f) for f in findings], indent=1, ensure_ascii=False)
    return {
        "report": write_model(ctx.out(step, "report.json"), report),
        "findings": write_atomic(ctx.out(step, "findings.json"), dumped + "\n"),
    }


def _load_findings(path: Path) -> list[QcFinding]:
    return [QcFinding(**item) for item in json.loads(path.read_text(encoding="utf-8"))]


def _qc_animatic(ctx: Ctx) -> StepOutput:
    bundle = ctx.bundle("compile")
    mp4 = ctx.artifact("render_animatic", "mp4")
    findings = _run_qc(ctx, bundle, mp4, None)
    failed = sum(f.passed is False for f in findings)
    unknown = sum(f.passed is None for f in findings)
    return StepOutput(
        _qc_record(ctx, "qc_animatic", bundle, mp4, "animatic", findings),
        facts={"failed": failed, "unknown": unknown},
    )


def _reviewer_inputs(ctx: Ctx) -> dict[str, str]:
    seams, settings = ctx.seams, ctx.config.reviewer
    if seams.reviewer is not None:
        return {"reviewer": seam_id(seams.reviewer)}
    if not settings.enabled:
        return {"reviewer": "none"}
    return {
        "reviewer": settings.model,
        "model_revision": settings.model_revision,
        "rubric_version": RUBRIC_VERSION,
        "prompt_sha256": prompt_sha256(),
        "seed": str(settings.seed),
        "reachable": str(seams.probe_reviewer(settings.base_url)),
    }


def unreviewed_report(
    bundle: ExplainerRenderBundle,
    artifact: ReviewedArtifact,
    why: str,
    *,
    model_id: str = "none",
    created_at: str | None = None,
) -> ReviewReport:
    """An advisory uncertain report that says why no model looked; the QC stays the gate."""
    # The contract needs one coverage span and one category; both name the scene nobody reviewed.
    fps = bundle.timeline.fps
    scene = bundle.timeline.scenes[0]
    end_ms = bundle.timeline.total_frames * 1000 // fps
    span = TimeInterval(
        start_ms=scene.start_frame * 1000 // fps,
        end_ms=(scene.start_frame + scene.duration_frames) * 1000 // fps,
    )
    identity = ReviewerIdentity(
        model_id=model_id[:160],
        prompt_sha256=prompt_sha256(),
        rubric_version=RUBRIC_VERSION,
        modalities=("image", "text"),
    )
    digest = hashlib.sha256(canonical_dumps([artifact.sha256, model_id, why]).encode()).hexdigest()
    finding = ReviewFinding(
        finding_id=f"fnd_{digest[:16]}",
        scene_id=scene.scene_id,
        interval=TimeInterval(start_ms=0, end_ms=end_ms),
        category="communication",
        severity="note",
        observed="the visual reviewer did not look at this artifact",
        evidence=why[:600],
        disposition="uncertain",
    )
    return ReviewReport(
        report_id=f"rev_{digest[:16]}",
        artifact=artifact,
        timeline_id=bundle.timeline.timeline_id,
        reviewer=identity,
        coverage=(
            CoverageSpan(scene_id=scene.scene_id, interval=span, sampled_ms=(span.start_ms,)),
        ),
        findings=(finding,),
        categories=(
            CategoryCoverage(category="communication", covered_by=f"nothing: {why}"[:160]),
        ),
        disposition="uncertain",
        authority="advisory",
        created_at=created_at or now_iso(),
    )


def _configured_reviewer(
    ctx: Ctx, mode: Literal["animatic", "final"]
) -> tuple[Reviewer | None, str]:
    settings = ctx.config.reviewer
    if not settings.enabled:
        return None, "no reviewer is configured (EpisodeConfig.reviewer.enabled is false)"
    if not ctx.seams.probe_reviewer(settings.base_url):
        return None, f"the reviewer server at {settings.base_url} is unreachable"
    from content_factory.explainer.review import ReviewCache
    from content_factory.explainer.vlm_reviewer import MmLimits, OpenAICompatAdapter, review_episode

    adapter = OpenAICompatAdapter(
        settings.base_url,
        settings.model,
        MmLimits.from_config(reviewer_server.DEFAULT_CONFIG),
        model_revision=settings.model_revision,
        seed=settings.seed,
    )
    out_dir = ctx.dir / f"review_{mode}" / "work"

    def review(bundle: ExplainerRenderBundle, mp4: Path) -> ReviewReport:
        # The animatic is judged from full-quality stills, the final from the mp4 viewers get.
        staged = render.stage_captures(bundle)
        return review_episode(
            staged,
            mp4 if mode == "final" else None,
            pack=ctx.pack(),
            script=ctx.script(),
            adapter=adapter,
            cache=ReviewCache(ctx.dir / "review-cache"),
            out_dir=out_dir,
            mode=mode,
        )

    return review, ""


def _review(
    ctx: Ctx,
    step: str,
    bundle: ExplainerRenderBundle,
    mp4: Path,
    mode: Literal["animatic", "final"],
) -> StepOutput:
    from content_factory.explainer.vlm_reviewer import ReviewerUnavailableError

    kind: Literal["animatic", "mp4"] = "animatic" if mode == "animatic" else "mp4"
    artifact = ReviewedArtifact(kind=kind, sha256=file_sha256(mp4))
    reviewer, why = ctx.seams.reviewer, ""
    if reviewer is None:
        reviewer, why = _configured_reviewer(ctx, mode)
    model = ctx.config.reviewer.model if ctx.config.reviewer.enabled else "none"
    if reviewer is None:
        report = unreviewed_report(bundle, artifact, why, model_id=model)
    else:
        try:
            report = reviewer(bundle, mp4)
        except ReviewerUnavailableError as error:
            report = unreviewed_report(bundle, artifact, str(error), model_id=model)
    return StepOutput(
        {"report": write_model(ctx.out(step, "report.json"), report)},
        facts={"disposition": report.disposition, "authority": report.authority},
    )


def _review_animatic(ctx: Ctx) -> StepOutput:
    mp4 = ctx.artifact("render_animatic", "mp4")
    return _review(ctx, "review_animatic", ctx.bundle("compile"), mp4, "animatic")


def _repair_inputs(ctx: Ctx) -> dict[str, str]:
    return {
        "max_rounds": str(MAX_REPAIR_ROUNDS),
        "compiler_version": COMPILER_VERSION,
        "renderer_source_hash": ctx.renderer_hash(),
        "crf": str(ANIMATIC_CRF),
        "qc_version": QC_VERSION,
        "qc": seam_id(ctx.seams.qc),
        "renderer": seam_id(ctx.seams.render_video),
    }


def _repair(ctx: Ctx) -> StepOutput:
    """repair.repair_loop over the animatic; its first round reuses the animatic and its QC."""
    pack, script, spec = ctx.pack(), ctx.script(), ctx.spec()
    first = ctx.bundle("compile")
    first_mp4 = ctx.artifact("render_animatic", "mp4")
    first_findings = _load_findings(ctx.artifact("qc_animatic", "findings"))
    rounds = itertools.count(1)

    def compile_spec(
        _pack: EvidencePack, _script: ScriptPlan, candidate: VisualSpec
    ) -> ExplainerRenderBundle:
        return first if candidate == spec else _compile_spec(ctx, candidate)

    def render_bundle(bundle: ExplainerRenderBundle, _scenes: Sequence[str] | None) -> Path:
        if bundle == first:
            return first_mp4
        out = ctx.out("repair", f"round-{next(rounds)}.mp4")
        return ctx.seams.render_video(bundle, out, ANIMATIC_CRF)

    def qc(bundle: ExplainerRenderBundle, mp4: Path) -> Sequence[QcFinding]:
        return first_findings if mp4 == first_mp4 else _run_qc(ctx, bundle, mp4, None)

    outcome = repair_loop(
        pack,
        script,
        spec,
        compile=compile_spec,
        render=render_bundle,
        qc=qc,
        max_rounds=MAX_REPAIR_ROUNDS,
    )
    bundle = compile_spec(pack, script, outcome.spec)
    artifacts: dict[str, Path] = {
        "spec": write_model(ctx.out("repair", "spec.json"), outcome.spec),
        "bundle": write_model(ctx.out("repair", "bundle.json"), bundle),
    }
    for i, report in enumerate(outcome.reports):
        artifacts[f"round-{i}"] = write_model(
            ctx.out("repair", "reports", f"round-{i}.json"), report
        )
    summary = {
        "rounds": outcome.rounds,
        "resolved": list(outcome.resolved),
        "blocked": [asdict(f) for f in outcome.blocked],
    }
    artifacts["outcome"] = write_atomic(ctx.out("repair", "outcome.json"), canonical_dumps(summary))
    facts = {"rounds": outcome.rounds, "resolved": len(outcome.resolved)}
    if outcome.blocked:
        items = [queue_item(f, bundle) for f in outcome.blocked]
        artifacts["queue"] = _write_queue(ctx, "repair", items)
        return StepOutput(artifacts, status="blocked", facts={**facts, "blocked": len(items)})
    _clear_queue(ctx)
    return StepOutput(artifacts, facts=facts)


def _final_inputs(ctx: Ctx) -> dict[str, str]:
    return {
        "renderer_source_hash": ctx.renderer_hash(),
        "crf": "remotion-default",
        "renderer": seam_id(ctx.seams.render_video),
    }


def _render_final(ctx: Ctx) -> StepOutput:
    mp4 = ctx.seams.render_video(ctx.bundle("repair"), ctx.out("final", "video.mp4"), None)
    return StepOutput({"mp4": mp4})


# --- steps: audio, mux, final QC, export ---


def library_entry(manifest_path: Path, sound_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(sound entry, whole manifest) for an id in an assets/<kind>/manifest.json."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next((s for s in manifest["sounds"] if s["id"] == sound_id), None)
    if entry is None:
        msg = f"{sound_id!r} is not in {manifest_path.relative_to(REPO_ROOT)}"
        raise ValueError(msg)
    return entry, manifest


def _library_wav(ctx: Ctx, manifest_path: Path, sound_id: str) -> Path:
    """The library FLAC checked against its manifest hash, as the 48 kHz mono WAV mix reads."""
    entry, _ = library_entry(manifest_path, sound_id)
    source = manifest_path.parent / entry["file"]
    if not source.is_file():
        msg = f"{source} is missing; library audio is git-ignored and staged per host"
        raise FileNotFoundError(msg)
    if file_sha256(source) != entry["sha256"]:
        msg = f"{source.name} does not match its manifest sha256 {entry['sha256'][:12]}"
        raise ValueError(msg)
    out = ctx.out("mix", "src", f"{sound_id}.wav")
    tmp = out.with_name(f".{out.stem}.{os.getpid()}.wav")
    ffmpeg(["-i", str(source), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(tmp)])
    tmp.replace(out)
    return out


def _mix_inputs(ctx: Ctx) -> dict[str, str]:
    config = ctx.config
    music = "none"
    if config.music_id:
        entry, _ = library_entry(MUSIC_MANIFEST, config.music_id)
        music = f"{config.music_id}:{entry['sha256']}"
    sfx = [
        [cue.sfx_id, cue.at_ms, library_entry(SFX_MANIFEST, cue.sfx_id)[0]["sha256"]]
        for cue in config.sfx
    ]
    return {"music": music, "sfx": canonical_dumps(sfx), "mix": seam_id(ctx.seams.mix)}


def _mix(ctx: Ctx) -> StepOutput:
    config = ctx.config
    music = _library_wav(ctx, MUSIC_MANIFEST, config.music_id) if config.music_id else None
    sfx = [(cue.at_ms, _library_wav(ctx, SFX_MANIFEST, cue.sfx_id)) for cue in config.sfx]
    result = ctx.seams.mix(ctx.artifact("narrate", "stem"), music, sfx, ctx.dir / "mix")
    loudness = write_model(ctx.out("mix", "loudness.json"), result.loudness)
    return StepOutput(
        {
            "narration": result.narration_stem,
            "music": result.music_stem,
            "sfx": result.sfx_stem,
            "master": result.master,
            "loudness": loudness,
        },
        facts={
            "integrated_lufs": result.loudness.integrated_lufs,
            "true_peak_dbtp": result.loudness.true_peak_dbtp,
        },
    )


def _mux_inputs(_ctx: Ctx) -> dict[str, str]:
    return {"args": canonical_dumps(list(MUX_ARGS))}


def _mux(ctx: Ctx) -> StepOutput:
    """Final video and the mastered audio into final.mp4, the audio padded to the picture."""
    out = ctx.dir / "final.mp4"
    tmp = out.with_name(f".final.{os.getpid()}.mp4")
    video, master = ctx.artifact("render_final", "mp4"), ctx.artifact("mix", "master")
    ffmpeg(["-i", str(video), "-i", str(master), *MUX_ARGS, str(tmp)])
    tmp.replace(out)
    return StepOutput({"mp4": out})


def _qc_final(ctx: Ctx) -> StepOutput:
    bundle = ctx.bundle("repair")
    mp4 = ctx.artifact("mux", "mp4")
    loudness = read_model(LoudnessReport, ctx.artifact("mix", "loudness"))
    findings = _run_qc(ctx, bundle, mp4, loudness)
    artifacts = _qc_record(ctx, "qc_final", bundle, mp4, "mp4", findings)
    failed = [f for f in findings if f.passed is False]
    facts = {"failed": len(failed), "unknown": sum(f.passed is None for f in findings)}
    if failed:
        items = [queue_item(f, bundle) for f in failed]
        artifacts["queue"] = _write_queue(ctx, "qc_final", items)
        return StepOutput(artifacts, status="blocked", facts=facts)
    _clear_queue(ctx)
    return StepOutput(artifacts, facts=facts)


def _review_final(ctx: Ctx) -> StepOutput:
    mp4 = ctx.artifact("mux", "mp4")
    return _review(ctx, "review_final", ctx.bundle("repair"), mp4, "final")


def _export_inputs(ctx: Ctx) -> dict[str, str]:
    from content_factory.explainer.export import EXPORT_VERSION

    return {"export_version": EXPORT_VERSION, "stills": seam_id(ctx.seams.render_stills)}


def _export(ctx: Ctx) -> StepOutput:
    from content_factory.explainer.export import EXPORT_MANIFEST, EXPORTS_DIR, export_bundle

    manifest = export_bundle(ctx.dir, config=ctx.config, render_stills=ctx.seams.render_stills)
    folder = ctx.dir / EXPORTS_DIR
    artifacts = {f"{EXPORTS_DIR}/{f.path}": folder / f.path for f in manifest.files}
    artifacts[f"{EXPORTS_DIR}/{EXPORT_MANIFEST}"] = folder / EXPORT_MANIFEST
    return StepOutput(artifacts, facts={"files": len(manifest.files)})


# --- review queue and renderer hash ---


def queue_item(finding: QcFinding, bundle: ExplainerRenderBundle) -> dict[str, Any]:
    """One blocked finding: where, which check, the evidence, and the typed repair if any."""
    repair = propose_repair(finding, bundle)
    return {
        "scene_id": finding.scene_id,
        "beat_id": _beat_at(bundle, finding.scene_id, finding.at_ms),
        "entity_id": finding.entity_id,
        "check": finding.check,
        "at_ms": finding.at_ms,
        "evidence": finding.evidence,
        "repair": repair.model_dump(mode="json") if repair is not None else None,
    }


def _issue_item(issue: ContractIssue) -> dict[str, Any]:
    return {
        "scene_id": None,
        "beat_id": None,
        "entity_id": None,
        "check": issue.kind,
        "at_ms": None,
        "evidence": f"{issue.where}: {issue.message} Fix: {issue.fix}",
        "ids": list(issue.ids),
        "repair": None,
    }


def _beat_at(bundle: ExplainerRenderBundle, scene_id: str, at_ms: int) -> str | None:
    """The beat whose action last started at or before at_ms in that scene."""
    compiled = next((s for s in bundle.timeline.scenes if s.scene_id == scene_id), None)
    if compiled is None:
        return None
    frame = at_ms * bundle.timeline.fps // 1000
    started = [a for a in compiled.actions if a.start_frame <= frame]
    return max(started, key=lambda a: a.start_frame).beat_id if started else None


def _write_queue(ctx: Ctx, step: str, items: Sequence[Mapping[str, Any]]) -> Path:
    payload = {
        "episode_id": ctx.config.episode_id,
        "step": step,
        "created_at": now_iso(),
        "items": list(items),
    }
    return write_atomic(ctx.dir / QUEUE_NAME, json.dumps(payload, indent=1, sort_keys=True) + "\n")


def _clear_queue(ctx: Ctx) -> None:
    (ctx.dir / QUEUE_NAME).unlink(missing_ok=True)


def renderer_source_hash(root: Path = REPO_ROOT) -> str:
    """sha256 over the renderer sources bundle.mjs bundles: path, NUL, bytes, NUL, per file."""
    files: list[Path] = []
    for folder in RENDERER_INPUTS:
        base = root / folder
        if base.is_dir():
            files += [p for p in base.rglob("*") if p.is_file() and "node_modules" not in p.parts]
    files.append(root / RENDERER_LOCKFILE)
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()
