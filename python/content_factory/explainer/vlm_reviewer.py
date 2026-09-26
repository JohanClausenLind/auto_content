"""A vision model looks at sampled frames and crops; what it finds becomes an advisory report."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import httpx
from PIL import Image

from content_factory.explainer import render
from content_factory.explainer.qc import Decoder, decode_frames, visible_at
from content_factory.explainer.review import CATEGORY_OF, ReviewCache, review_cache_key
from content_factory.explainer.reviewer_prompts import (
    DISPOSITIONS,
    IMAGE_CATEGORIES,
    RUBRIC_VERSION,
    SEVERITIES,
    prompt_sha256,
    render_bulk_prompt,
    render_episode_prompt,
    retry_prompt,
)
from content_factory.explainer.reviewer_server import DEFAULT_STATE_DIR, ReviewerServerConfig
from content_factory.explainer.source_scene import PageGeometry, camera_at, screen_rect
from content_factory.schemas.base import canonical_dumps, file_sha256
from content_factory.schemas.explainer import (
    CategoryCoverage,
    Claim,
    CompiledExplainerScene,
    CoverageSpan,
    Disposition,
    EvidencePack,
    ExplainerRenderBundle,
    LabelWordingRepair,
    PixelBox,
    QuoteAction,
    ReviewCategory,
    ReviewedArtifact,
    ReviewerIdentity,
    ReviewFinding,
    ReviewReport,
    ReviewSeverity,
    Scene,
    ScriptPlan,
    SourceDocumentTemplate,
    SourcePassageRepair,
    TextTemplate,
    TimeInterval,
    TypedRepair,
)

ReviewMode = Literal["animatic", "final"]
RenderFrames = Callable[[Path, Path, Sequence[int], render.FrameMode], list[Path]]
REPO_ROOT = Path(__file__).resolve().parents[3]
TARGET_MINUTES_PER_EPISODE = 20
# Measured 2026-09-23: ~12 s per two-image call, decoding the findings dominates; twenty minutes
# buys ~100 calls, so a ten-minute episode of forty scenes gets two or three looks per scene.
SECONDS_PER_CALL = 12.0
IMAGE_TOKENS_MARGIN = 60
PROMPT_TOKENS_BUDGET = 1500
BULK_ANSWER_TOKENS = 600
EPISODE_ANSWER_TOKENS = 1500
EPISODE_TEMPLATE_TOKENS = 500
CHARS_PER_TOKEN = 3.5
CAMERA_STEP_FRAMES = 6
CROP_PAD_PX = 12
MIN_CROP_PX = 24
TEXT_LIMIT = 600
NOTE_LIMIT = 240
EPISODE_CATEGORIES: tuple[ReviewCategory, ...] = ("communication", "continuity")
LABEL_TEMPLATES = frozenset({"chart", "diagram"})
THIN_ORDER = (4, 3, 2, 1)


class ReviewerUnavailableError(RuntimeError):
    """The model server refused or failed the call; the message carries its answer."""


class VerdictParseError(ValueError):
    """The answer was not the strict JSON verdict the prompt asked for."""


@dataclass(frozen=True)
class MmLimits:
    """What one prompt may carry, from the serving recipe."""

    max_images: int = 2
    max_pixels: int = 409_600
    max_model_len: int = 4096

    @classmethod
    def from_config(cls, config: ReviewerServerConfig) -> MmLimits:
        return cls(config.max_images, config.max_pixels, config.max_model_len)


@dataclass(frozen=True)
class AdapterResult:
    text: str
    usage: dict[str, int]
    wall_s: float


class ReviewerAdapter(Protocol):
    model: str
    model_revision: str
    quantization: str
    limits: MmLimits

    def review_frames(
        self, images: Sequence[Path], prompt: str, *, thinking: bool, max_tokens: int
    ) -> AdapterResult: ...


def image_tokens(max_pixels: int) -> int:
    """Upper bound per image: one token per 32x32 patch after the resize, plus a margin."""
    return math.ceil(max_pixels / 1024) + IMAGE_TOKENS_MARGIN


def frames_per_call(limits: MmLimits) -> int:
    """The server's image cap, or fewer when the window cannot hold them plus prompt and answer."""
    room = limits.max_model_len - PROMPT_TOKENS_BUDGET - BULK_ANSWER_TOKENS
    return max(1, min(limits.max_images, room // image_tokens(limits.max_pixels)))


def call_budget(minutes: int = TARGET_MINUTES_PER_EPISODE) -> int:
    return int(minutes * 60 / SECONDS_PER_CALL)


class OpenAICompatAdapter:
    """The model seam: any OpenAI-style chat server taking image_url parts (vLLM here) plugs in."""

    def __init__(
        self,
        base_url: str,
        model: str,
        limits: MmLimits,
        *,
        model_revision: str = "",
        quantization: str = "",
        timeout_s: float = 600.0,
        seed: int = 0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.limits = limits
        self.model_revision = model_revision
        self.quantization = quantization
        self.seed = seed
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    def review_frames(
        self, images: Sequence[Path], prompt: str, *, thinking: bool, max_tokens: int
    ) -> AdapterResult:
        if len(images) > self.limits.max_images:
            msg = f"{len(images)} images in one call, the server takes {self.limits.max_images}"
            raise ValueError(msg)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in images:
            url = data_url(path, self.limits.max_pixels)
            content.append({"type": "image_url", "image_url": {"url": url}})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "seed": self.seed,
            "chat_template_kwargs": {"enable_thinking": thinking},
        }
        # Greedy decoding loops when the model thinks (Qwen model card); sampling is seeded instead.
        body.update({"temperature": 0.6, "top_p": 0.95} if thinking else {"temperature": 0.0})
        started = time.perf_counter()
        try:
            response = self._http.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.HTTPError as error:
            msg = f"reviewer server at {self.base_url} unreachable: {error}"
            raise ReviewerUnavailableError(msg) from error
        wall_s = time.perf_counter() - started
        if response.status_code != 200:
            msg = f"reviewer server answered {response.status_code}: {response.text[:300]}"
            raise ReviewerUnavailableError(msg)
        payload = response.json()
        message = payload["choices"][0]["message"]
        usage = {k: int(v) for k, v in (payload.get("usage") or {}).items() if isinstance(v, int)}
        return AdapterResult(strip_thinking(str(message.get("content") or "")), usage, wall_s)


@dataclass(frozen=True)
class FakeCall:
    images: tuple[Path, ...]
    prompt: str
    thinking: bool
    max_tokens: int


class FakeAdapter:
    """Scripted answers in order, the last one repeating; every call is recorded for the tests."""

    model = "fake-reviewer"
    model_revision = "fake"
    quantization = ""

    def __init__(self, answers: Sequence[str], limits: MmLimits | None = None) -> None:
        self.answers = list(answers)
        self.limits = limits or MmLimits()
        self.calls: list[FakeCall] = []

    def review_frames(
        self, images: Sequence[Path], prompt: str, *, thinking: bool, max_tokens: int
    ) -> AdapterResult:
        self.calls.append(FakeCall(tuple(images), prompt, thinking, max_tokens))
        text = self.answers[min(len(self.calls), len(self.answers)) - 1]
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        return AdapterResult(text, usage, 0.0)


def data_url(path: Path, max_pixels: int) -> str:
    """The frame as a PNG data URL, resized to the server's pixel cap once, here, not twice."""
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    pixels = image.width * image.height
    if pixels > max_pixels:
        scale = math.sqrt(max_pixels / pixels)
        size = (max(1, math.floor(image.width * scale)), max(1, math.floor(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def strip_thinking(text: str) -> str:
    """vLLM returns the reasoning inline before `</think>` when no reasoning parser is set."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.removeprefix("<think>").strip()


@dataclass(frozen=True)
class RawFinding:
    category: ReviewCategory
    severity: ReviewSeverity
    observed: str
    evidence: str
    disposition: Disposition
    confidence: float | None
    scene_id: str | None = None


@dataclass(frozen=True)
class Verdict:
    findings: tuple[RawFinding, ...]
    summary: str


def parse_verdict(
    text: str,
    *,
    summary_key: str = "scene_summary",
    categories: Sequence[str] = IMAGE_CATEGORIES,
    scene_ids: Sequence[str] | None = None,
) -> Verdict:
    """Strict: only the contract's vocabularies pass; anything else is a VerdictParseError."""
    payload = _json_object(text)
    findings = payload.get("findings")
    if not isinstance(findings, list):
        msg = "findings must be a list"
        raise VerdictParseError(msg)
    parsed: list[RawFinding] = []
    for i, item in enumerate(findings):
        if not isinstance(item, dict):
            msg = f"findings[{i}] is not an object"
            raise VerdictParseError(msg)
        parsed.append(_raw_finding(cast(dict[str, Any], item), i, categories, scene_ids))
    summary = payload.get(summary_key)
    return Verdict(tuple(parsed), _clip(summary) if isinstance(summary, str) else "")


def _raw_finding(
    item: dict[str, Any], i: int, categories: Sequence[str], scene_ids: Sequence[str] | None
) -> RawFinding:
    confidence = item.get("confidence")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            msg = f"findings[{i}].confidence is not a number"
            raise VerdictParseError(msg)
        if not 0 <= confidence <= 1:
            msg = f"findings[{i}].confidence {confidence} is outside 0..1"
            raise VerdictParseError(msg)
    scene_id = item.get("scene_id")
    if scene_ids is not None and scene_id not in scene_ids:
        msg = f"findings[{i}].scene_id {scene_id!r} is not one of the scenes"
        raise VerdictParseError(msg)
    return RawFinding(
        category=cast(ReviewCategory, _one_of(item, "category", categories, i)),
        severity=cast(ReviewSeverity, _one_of(item, "severity", SEVERITIES, i)),
        observed=_text(item, "observed", i),
        evidence=_text(item, "evidence", i),
        disposition=cast(Disposition, _one_of(item, "disposition", DISPOSITIONS, i)),
        confidence=None if confidence is None else float(confidence),
        scene_id=scene_id if isinstance(scene_id, str) else None,
    )


def _one_of(item: dict[str, Any], key: str, allowed: Sequence[str], i: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or value not in allowed:
        msg = f"findings[{i}].{key} {value!r} is not one of {list(allowed)}"
        raise VerdictParseError(msg)
    return value


def _text(item: dict[str, Any], key: str, i: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        msg = f"findings[{i}].{key} must be a non-empty string"
        raise VerdictParseError(msg)
    return _clip(value.strip())


def _json_object(text: str) -> dict[str, Any]:
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        msg = "no JSON object in the answer"
        raise VerdictParseError(msg)
    try:
        payload = json.loads(stripped[start : end + 1])
    except ValueError as error:
        msg = f"invalid JSON: {error}"
        raise VerdictParseError(msg) from error
    if not isinstance(payload, dict):
        msg = "the JSON is not an object"
        raise VerdictParseError(msg)
    return cast(dict[str, Any], payload)


# --- sampling ---


@dataclass(frozen=True)
class Sample:
    """One frame worth a look; priority 0 is never thinned, 4 is the first to go."""

    scene_id: str
    frame: int
    why: str
    priority: int


@dataclass(frozen=True)
class Crop:
    """A full-resolution cut of one text box or highlight at the frame where it has settled."""

    scene_id: str
    frame: int
    box: PixelBox
    target_id: str
    kind: Literal["text", "highlight"]


def sample_plan(bundle: ExplainerRenderBundle) -> dict[str, list[int]]:
    """Frames per scene: action start, mid and end, holds, boundaries ±1, dense during motion."""
    plan: dict[str, set[int]] = {s.scene_id: set() for s in bundle.timeline.scenes}
    for sample in planned_samples(bundle):
        plan[sample.scene_id].add(sample.frame)
    for crop in crop_plan(bundle):
        plan[crop.scene_id].add(crop.frame)
    return {scene_id: sorted(frames) for scene_id, frames in plan.items()}


def planned_samples(bundle: ExplainerRenderBundle) -> list[Sample]:
    last = bundle.timeline.total_frames - 1
    samples: list[Sample] = []
    for compiled in bundle.timeline.scenes:
        start = compiled.start_frame
        end = min(last, start + compiled.duration_frames - 1)
        for frame, why, priority in _scene_samples(compiled, start, end):
            samples.append(Sample(compiled.scene_id, min(max(start, frame), end), why, priority))
    return samples


def _scene_samples(
    compiled: CompiledExplainerScene, start: int, end: int
) -> list[tuple[int, str, int]]:
    wanted: list[tuple[int, str, int]] = [
        (start, "scene start", 0),
        (start + 1, "scene start +1", 0),
        (end - 1, "scene end -1", 0),
        (end, "scene end", 0),
    ]
    for action in compiled.actions:
        wanted.append((action.start_frame, f"{action.action} start", 2))
        wanted.append((action.end_frame, f"{action.action} end", 1))
        midpoint = (action.start_frame + action.end_frame) // 2
        wanted.append((midpoint, f"{action.action} midpoint", 2 if action.action == "hold" else 3))
    for a, b in pairwise(compiled.camera):
        if b.easing != "hold":
            wanted += [
                (f, "camera move", 4) for f in range(a.frame, b.frame + 1, CAMERA_STEP_FRAMES)
            ]
    for key in compiled.highlights:
        sweep = range(key.start_frame, key.end_frame + 1, CAMERA_STEP_FRAMES)
        wanted += [(f, f"highlight sweep {key.quote_id}", 4) for f in sweep]
        if key.clear_start_frame is not None and key.clear_end_frame is not None:
            clear = range(key.clear_start_frame, key.clear_end_frame + 1, CAMERA_STEP_FRAMES)
            wanted += [(f, f"highlight clear {key.quote_id}", 4) for f in clear]
    return wanted


def crop_plan(bundle: ExplainerRenderBundle) -> list[Crop]:
    """Every text box and highlight, cut from the 1080 frame once it has finished appearing."""
    scenes = {s.scene_id: s for s in bundle.spec.scenes}
    captures = {c.capture_id: c for c in bundle.captures}
    assets = {a.asset_id: a for a in bundle.spec.assets}
    canvas = PixelBox(x=0, y=0, width=bundle.timeline.width, height=bundle.timeline.height)
    last = bundle.timeline.total_frames - 1
    crops: list[Crop] = []
    for compiled in bundle.timeline.scenes:
        scene = scenes[compiled.scene_id]
        end = min(last, compiled.start_frame + compiled.duration_frames - 1)
        for box in compiled.boxes:
            if box.font_px is None:
                continue
            frame = settled_frame(scene, compiled, box.entity_id, end)
            padded = _pad(box.box, canvas)
            if frame is not None and padded is not None:
                crops.append(Crop(compiled.scene_id, frame, padded, box.entity_id, "text"))
        template = scene.template
        if not isinstance(template, SourceDocumentTemplate) or not compiled.highlights:
            continue
        capture = captures[assets[template.capture_asset_id].capture_id or ""]
        page = next((r.box for r in compiled.regions if r.name == "page"), None)
        if page is None:
            continue
        geometry = PageGeometry(
            page, capture.manifest.viewport.width, capture.manifest.page_height_px
        )
        for key in compiled.highlights:
            frame = min(key.end_frame, end)
            camera = camera_at(compiled.camera, frame)
            rects = [screen_rect(r, camera, geometry) for r in key.rects]
            x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
            x1, y1 = max(r[2] for r in rects), max(r[3] for r in rects)
            box = _clamped(math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1), canvas)
            if box is not None:
                crops.append(Crop(compiled.scene_id, frame, box, key.quote_id, "highlight"))
    return crops


def settled_frame(
    scene: Scene, compiled: CompiledExplainerScene, entity_id: str, end: int
) -> int | None:
    """The first action end at which the entity is on screen, else None when it never is."""
    candidates = sorted({a.end_frame for a in compiled.actions} | {end})
    for frame in candidates:
        if entity_id in visible_at(scene, compiled, min(frame, end)):
            return min(frame, end)
    return None


def thin_samples(samples: Sequence[Sample], max_frames: int) -> tuple[list[Sample], int]:
    """Drop the densest looks first until the distinct frames fit; boundaries and ends stay."""
    kept = list(samples)
    dropped = 0
    for priority in THIN_ORDER:
        if len({(s.scene_id, s.frame) for s in kept}) <= max_frames:
            break
        before = len(kept)
        kept = [s for s in kept if s.priority != priority]
        dropped += before - len(kept)
    return kept, dropped


def _pad(box: PixelBox, canvas: PixelBox) -> PixelBox | None:
    return _clamped(
        box.x - CROP_PAD_PX,
        box.y - CROP_PAD_PX,
        box.x + box.width + CROP_PAD_PX,
        box.y + box.height + CROP_PAD_PX,
        canvas,
    )


def _clamped(x0: int, y0: int, x1: int, y1: int, canvas: PixelBox) -> PixelBox | None:
    x0, y0 = max(canvas.x, x0), max(canvas.y, y0)
    x1, y1 = min(canvas.x + canvas.width, x1), min(canvas.y + canvas.height, y1)
    if x1 - x0 < MIN_CROP_PX or y1 - y0 < MIN_CROP_PX:
        return None
    return PixelBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0)


# --- frames on disk ---


@dataclass(frozen=True)
class FrameImage:
    path: Path
    frame: int
    sha256: str
    label: str
    crop: Crop | None = None


class FrameStore:
    """PNGs of every sampled frame and crop under out_dir/frames, each with its file sha256."""

    def __init__(self, out_dir: Path, fps: int, total_frames: int) -> None:
        self.dir = out_dir / "frames"
        self.fps = fps
        self.last = total_frames - 1
        self._sha: dict[Path, str] = {}

    def ms(self, frame: int) -> int:
        return math.floor(min(frame, self.last) * 1000 / self.fps)

    def path(self, frame: int) -> Path:
        return self.dir / f"f{frame:06d}.png"

    def load(
        self,
        frames: Iterable[int],
        *,
        mp4: Path | None,
        bundle_path: Path | None,
        decode: Decoder,
        render_frames: RenderFrames,
    ) -> None:
        wanted = sorted({min(f, self.last) for f in frames})
        self.dir.mkdir(parents=True, exist_ok=True)
        if mp4 is not None:
            for frame in wanted:
                if not self.path(frame).exists():
                    decode(mp4, [self.ms(frame)])[0].save(self.path(frame))
            return
        if bundle_path is None:
            msg = "an animatic review needs the bundle JSON path to render stills from"
            raise ValueError(msg)
        missing = [f for f in wanted if not self.path(f).exists()]
        if missing:
            render_frames(bundle_path, self.dir, missing, "stills")

    def frame(self, frame: int, label: str) -> FrameImage:
        path = self.path(min(frame, self.last))
        return FrameImage(path, frame, self.sha(path), label)

    def crop(self, crop: Crop) -> FrameImage:
        path = self.dir / f"f{crop.frame:06d}-{_slug(crop.target_id)}.png"
        b = crop.box
        if not path.exists():
            with Image.open(self.path(crop.frame)) as full:
                full.crop((b.x, b.y, b.x + b.width, b.y + b.height)).save(path)
        label = (
            f"full-resolution crop of {crop.kind} {crop.target_id} at ({b.x},{b.y}) "
            f"{b.width}x{b.height} from frame {crop.frame}"
        )
        return FrameImage(path, crop.frame, self.sha(path), label, crop)

    def sha(self, path: Path) -> str:
        if path not in self._sha:
            self._sha[path] = file_sha256(path)
        return self._sha[path]


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)


# --- the review ---


@dataclass
class ReviewStats:
    """What one review cost; the caller keeps it for the journal."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    wall_s: float = 0.0
    parse_retries: int = 0
    unparsed: int = 0
    frames: int = 0
    crops: int = 0
    thinned: int = 0
    paired_crops: bool = False
    cache_hit: bool = False

    def record(self, result: AdapterResult) -> None:
        self.calls += 1
        self.prompt_tokens += result.usage.get("prompt_tokens", 0)
        self.completion_tokens += result.usage.get("completion_tokens", 0)
        self.wall_s += result.wall_s


@dataclass(frozen=True)
class Look:
    """One call: the images of one scene sent together, and what each of them is."""

    scene_id: str
    images: tuple[FrameImage, ...]
    why: str


@dataclass
class _SceneNote:
    scene: Scene
    compiled: CompiledExplainerScene
    narration: str
    summary: str = ""
    findings: list[tuple[RawFinding, Look]] = field(default_factory=list)


def review_episode(
    bundle: ExplainerRenderBundle,
    mp4: Path | None,
    *,
    pack: EvidencePack,
    script: ScriptPlan,
    adapter: ReviewerAdapter,
    cache: ReviewCache,
    out_dir: Path,
    mode: ReviewMode,
    bundle_path: Path | None = None,
    decode: Decoder = decode_frames,
    render_frames: RenderFrames = render.render_frames,
    stats: ReviewStats | None = None,
    created_at: str | None = None,
) -> ReviewReport:
    """Bulk pass over frames and crops with thinking off, then an episode pass with it on."""
    stats = stats if stats is not None else ReviewStats()
    if mode == "final":
        if mp4 is None:
            msg = "a final review needs the mp4"
            raise ValueError(msg)
        artifact = ReviewedArtifact(kind="mp4", sha256=file_sha256(mp4))
    else:
        artifact = ReviewedArtifact(kind="animatic", sha256=bundle.content_hash())
        if bundle_path is None:
            out_dir.mkdir(parents=True, exist_ok=True)
            bundle_path = out_dir / "bundle.json"
            bundle_path.write_text(bundle.canonical_json() + "\n", encoding="utf-8")
    key = review_cache_key(
        media_sha256=artifact.sha256,
        evidence_hash=pack.pack_hash(),
        model_id=adapter.model,
        model_revision=adapter.model_revision,
        rubric_version=RUBRIC_VERSION,
        prompt_sha256=prompt_sha256(),
    )
    cached = cache.get(key)
    if cached is not None and (
        ReviewCache.covers(cached, mp4) if mp4 is not None else cached.artifact == artifact
    ):
        stats.cache_hit = True
        return cached
    looks, store, sampled = _plan_looks(
        bundle,
        out_dir,
        mp4=mp4,
        bundle_path=bundle_path,
        decode=decode,
        render_frames=render_frames,
        per_call=frames_per_call(adapter.limits),
        stats=stats,
    )
    notes = _notes(bundle, script)
    for look in looks:
        note = notes[look.scene_id]
        verdict = _ask(adapter, look, note, notes, bundle, pack, stats)
        note.summary = verdict.summary or note.summary
        note.findings.extend((raw, look) for raw in verdict.findings)
    episode_findings = _episode_pass(script, list(notes.values()), adapter, stats)
    findings = _review_findings(notes, episode_findings, bundle, store)
    identity = ReviewerIdentity(
        model_id=adapter.model,
        model_revision=adapter.model_revision[:80],
        quantization=adapter.quantization[:40],
        prompt_sha256=prompt_sha256(),
        rubric_version=RUBRIC_VERSION,
        modalities=("image", "text"),
    )
    body = canonical_dumps(
        [artifact.sha256, identity.model_dump(mode="json"), [f.model_dump() for f in findings]]
    )
    report = ReviewReport(
        report_id="rev_" + hashlib.sha256(body.encode()).hexdigest()[:16],
        artifact=artifact,
        timeline_id=bundle.timeline.timeline_id,
        reviewer=identity,
        coverage=_coverage(bundle, notes, sampled, store),
        findings=findings,
        categories=_categories(adapter.model),
        disposition=_disposition(findings),
        # Blocking authority is granted by a person editing this constant after the fixture set
        # shows zero missed defects and an acceptable false-alarm rate (journal 2026-09-23).
        authority="advisory",
        created_at=created_at or datetime.now(UTC).isoformat(timespec="seconds"),
    )
    cache.put(key, report)
    return report


def _plan_looks(
    bundle: ExplainerRenderBundle,
    out_dir: Path,
    *,
    mp4: Path | None,
    bundle_path: Path | None,
    decode: Decoder,
    render_frames: RenderFrames,
    per_call: int,
    stats: ReviewStats,
) -> tuple[list[Look], FrameStore, dict[str, set[int]]]:
    crops = crop_plan(bundle)
    # Crops, boundaries and each scene's unthinnable ends take calls first; past the budget,
    # one scene's crops share a call without their full frame, and samples get what is left.
    scenes = len(bundle.timeline.scenes)
    boundaries = scenes - 1 if per_call >= 2 else 0
    paired = per_call >= 2 and len(crops) + boundaries + scenes + 4 > call_budget()
    by_scene: dict[str, list[Crop]] = {}
    for crop in crops:
        by_scene.setdefault(crop.scene_id, []).append(crop)
    crop_calls = sum(-(-len(cs) // per_call) for cs in by_scene.values()) if paired else len(crops)
    samples, dropped = thin_samples(
        planned_samples(bundle), max(0, call_budget() - crop_calls - boundaries - 4) * per_call
    )
    stats.thinned, stats.paired_crops = dropped, paired
    frames: dict[str, set[int]] = {s.scene_id: set() for s in bundle.timeline.scenes}
    why: dict[tuple[str, int], str] = {}
    for sample in samples:
        frames[sample.scene_id].add(sample.frame)
        why.setdefault((sample.scene_id, sample.frame), sample.why)
    for crop in crops:
        frames[crop.scene_id].add(crop.frame)
        why.setdefault((crop.scene_id, crop.frame), f"{crop.kind} settled")
    store = FrameStore(out_dir, bundle.timeline.fps, bundle.timeline.total_frames)
    store.load(
        {f for fs in frames.values() for f in fs},
        mp4=mp4,
        bundle_path=bundle_path,
        decode=decode,
        render_frames=render_frames,
    )
    stats.frames = sum(len(fs) for fs in frames.values())
    stats.crops = len(crops)
    looks: list[Look] = []
    used: set[tuple[str, int]] = set()
    if paired:
        for scene_id, scene_crops in by_scene.items():
            for i in range(0, len(scene_crops), per_call):
                chunk = scene_crops[i : i + per_call]
                what = ", ".join(f"{c.kind} {c.target_id}" for c in chunk)
                looks.append(Look(scene_id, tuple(store.crop(c) for c in chunk), what))
                used.update((c.scene_id, c.frame) for c in chunk)
    for crop in () if paired else crops:
        if per_call < 2:
            looks.append(Look(crop.scene_id, (store.crop(crop),), f"{crop.kind} {crop.target_id}"))
            continue
        base = store.frame(crop.frame, f"full frame {crop.frame}, {why[crop.scene_id, crop.frame]}")
        looks.append(Look(crop.scene_id, (base, store.crop(crop)), f"{crop.kind} {crop.target_id}"))
        used.add((crop.scene_id, crop.frame))
    for a, b in pairwise(bundle.timeline.scenes):
        end, start = a.start_frame + a.duration_frames - 1, b.start_frame
        if per_call >= 2 and (a.scene_id, end) not in used and (b.scene_id, start) not in used:
            images = (
                store.frame(end, f"last frame {end} of the previous scene {a.scene_id}"),
                store.frame(start, f"first frame {start} of this scene"),
            )
            looks.append(Look(b.scene_id, images, f"transition from {a.scene_id}"))
            used.update({(a.scene_id, end), (b.scene_id, start)})
    for scene_id, scene_frames in frames.items():
        rest = [f for f in sorted(scene_frames) if (scene_id, f) not in used]
        for chunk in (rest[i : i + per_call] for i in range(0, len(rest), per_call)):
            images = tuple(store.frame(f, f"frame {f}, {why[scene_id, f]}") for f in chunk)
            looks.append(Look(scene_id, images, "consecutive samples"))
    return looks, store, frames


def _notes(bundle: ExplainerRenderBundle, script: ScriptPlan) -> dict[str, _SceneNote]:
    compiled = {s.scene_id: s for s in bundle.timeline.scenes}
    return {
        s.scene_id: _SceneNote(s, compiled[s.scene_id], _narration(s, script))
        for s in bundle.spec.scenes
    }


def _narration(scene: Scene, script: ScriptPlan) -> str:
    spans: dict[str, list[int]] = {}
    for beat in scene.beats:
        span = spans.setdefault(beat.cue.segment_id, [beat.cue.token_start, beat.cue.token_end])
        span[0], span[1] = min(span[0], beat.cue.token_start), max(span[1], beat.cue.token_end)
    lines = []
    for segment_id, (lo, hi) in spans.items():
        try:
            text = script.segment(segment_id).spoken_text
        except KeyError:
            text = "(segment not in this script)"
        lines.append(f"[{segment_id} tokens {lo}-{hi}] {text}")
    return "\n".join(lines)


def _ask(
    adapter: ReviewerAdapter,
    look: Look,
    note: _SceneNote,
    notes: dict[str, _SceneNote],
    bundle: ExplainerRenderBundle,
    pack: EvidencePack,
    stats: ReviewStats,
) -> Verdict:
    prompt = _bulk_prompt(look, note, notes, bundle, pack)
    images = [image.path for image in look.images]
    result = adapter.review_frames(images, prompt, thinking=False, max_tokens=BULK_ANSWER_TOKENS)
    stats.record(result)
    try:
        return parse_verdict(result.text)
    except VerdictParseError as first:
        stats.parse_retries += 1
        retry = adapter.review_frames(
            images, retry_prompt(prompt, str(first)), thinking=False, max_tokens=BULK_ANSWER_TOKENS
        )
        stats.record(retry)
        try:
            return parse_verdict(retry.text)
        except VerdictParseError as second:
            stats.unparsed += 1
            return Verdict((_unparsed(second, retry.text),), "")


def _unparsed(error: VerdictParseError, text: str) -> RawFinding:
    return RawFinding(
        category="technical",
        severity="note",
        observed="the reviewer's answer was not a valid verdict after one retry",
        evidence=_clip(f"{error}; answer: {text.strip()[:300]}"),
        disposition="uncertain",
        confidence=None,
    )


def _bulk_prompt(
    look: Look,
    note: _SceneNote,
    notes: dict[str, _SceneNote],
    bundle: ExplainerRenderBundle,
    pack: EvidencePack,
) -> str:
    scene = note.scene
    frames = "\n".join(
        f"Image {i + 1}: {image.label} ({_ms(image.frame, bundle.timeline.fps)} ms). "
        f"Expected on screen: {_expected_visible(scene, note.compiled, image.frame, bundle)}"
        for i, image in enumerate(look.images)
    )
    cue = ", ".join(f"{b.cue.token_start}-{b.cue.token_end}" for b in scene.beats)
    order = [n.scene.scene_id for n in notes.values()]
    index = order.index(scene.scene_id)
    neighbours = []
    if index > 0:
        previous = notes[order[index - 1]]
        neighbours.append(
            f"Previous scene {previous.scene.scene_id} ({previous.scene.section}): "
            f"{previous.summary or previous.scene.purpose}"
        )
    if index + 1 < len(order):
        following = notes[order[index + 1]]
        neighbours.append(
            f"Next scene {following.scene.scene_id} ({following.scene.section}): "
            f"{following.scene.purpose}"
        )
    return render_bulk_prompt(
        count=len(look.images),
        frames=frames + f"\nLook: {look.why}",
        scene_id=scene.scene_id,
        section=scene.section,
        template=scene.template.template,
        purpose=scene.purpose,
        neighbours="\n".join(neighbours),
        segment_id=", ".join(dict.fromkeys(b.cue.segment_id for b in scene.beats)),
        cue_span=cue,
        narration=note.narration,
        claims=_claims_text(scene, bundle, pack),
        quotes=_quotes_text(scene, bundle),
    )


def _expected_visible(
    scene: Scene, compiled: CompiledExplainerScene, frame: int, bundle: ExplainerRenderBundle
) -> str:
    """Settled entities and the ones mid-animation at the frame; before any reveal it is bare."""
    labels = {e.entity_id: e.short_label or e.label for e in bundle.spec.entities}
    settled = visible_at(scene, compiled, frame)
    moving = {
        t for a in compiled.actions if a.start_frame <= frame < a.end_frame for t in a.targets
    }
    parts = []
    shown = [labels[e] for e in sorted(settled) if e in labels]
    if shown:
        parts.append("settled: " + ", ".join(shown))
    animating = [labels[e] for e in sorted(moving - settled) if e in labels]
    if animating:
        parts.append("mid-animation (partial or fading is correct): " + ", ".join(animating))
    if not parts:
        return "the empty template only (nothing revealed yet, which is correct here)"
    return "; ".join(parts)


def _claims_text(scene: Scene, bundle: ExplainerRenderBundle, pack: EvidencePack) -> str:
    """The scene's own claims; an entity's claims only when the scene lists none of its own."""
    ids = list(scene.claim_ids)
    if isinstance(scene.template, TextTemplate):
        ids.extend(i.claim_id for i in scene.template.items if i.claim_id)
    if not ids:
        entities = {e.entity_id: e for e in bundle.spec.entities}
        for entity_id in _scene_entity_ids(scene):
            ids.extend(entities[entity_id].claim_ids if entity_id in entities else ())
    lines = []
    for claim_id in dict.fromkeys(ids):
        try:
            claim = pack.claim(claim_id)
        except KeyError:
            continue
        lines.append(f"- {claim.claim_id}: {claim.statement}{_value(claim)}")
    return "\n".join(lines)


def _value(claim: Claim) -> str:
    if claim.value is None:
        return ""
    return f" [= {claim.value.magnitude:g} {claim.value.unit}, {claim.value.uncertainty}]"


def _quotes_text(scene: Scene, bundle: ExplainerRenderBundle) -> str:
    template = scene.template
    if isinstance(template, TextTemplate):
        return "\n".join(f'- {item.entity_id}: "{item.text}"' for item in template.items)
    if not isinstance(template, SourceDocumentTemplate):
        return ""
    assets = {a.asset_id: a for a in bundle.spec.assets}
    capture_id = assets[template.capture_asset_id].capture_id
    capture = next((c for c in bundle.captures if c.capture_id == capture_id), None)
    if capture is None:
        return ""
    quoted = {a.quote_id for b in scene.beats for a in b.actions if isinstance(a, QuoteAction)}
    return "\n".join(
        f'- {q.quote_id}: "{q.text}"' for q in capture.manifest.quotes if q.quote_id in quoted
    )


def _scene_entity_ids(scene: Scene) -> list[str]:
    ids = list(scene.initial_visible)
    for beat in scene.beats:
        for action in beat.actions:
            ids.extend(getattr(action, "targets", ()))
    return ids


def _episode_pass(
    script: ScriptPlan, notes: Sequence[_SceneNote], adapter: ReviewerAdapter, stats: ReviewStats
) -> list[RawFinding]:
    """Thinking on, text only: the ordered scene notes, chunked to fit the window."""
    lines = [
        f"{i + 1}. {n.scene.scene_id} ({n.scene.section}) purpose: {n.scene.purpose} | narration: "
        f"{_clip(n.narration, NOTE_LIMIT)} | seen: {_clip(n.summary, NOTE_LIMIT) or '(no note)'}"
        for i, n in enumerate(notes)
    ]
    room = adapter.limits.max_model_len - EPISODE_ANSWER_TOKENS - EPISODE_TEMPLATE_TOKENS
    chunks = _chunks(lines, room)
    findings: list[RawFinding] = []
    scene_ids = [n.scene.scene_id for n in notes]
    for chunk in chunks:
        prompt = render_episode_prompt(
            question=script.question, contribution=script.contribution, scenes="\n".join(chunk)
        )
        findings.extend(_ask_episode(adapter, prompt, scene_ids, stats, chunk))
    return findings


def _chunks(lines: Sequence[str], room_tokens: int) -> list[list[str]]:
    """Consecutive notes that fit the window; later chunks repeat the opening for the payoff."""
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if current and _tokens([*current, line]) > room_tokens:
            chunks.append(current)
            current = [lines[0]]
        current.append(line)
    if current:
        chunks.append(current)
    return chunks or [[]]


def _tokens(lines: Sequence[str]) -> int:
    return math.ceil(sum(len(line) for line in lines) / CHARS_PER_TOKEN)


def _ask_episode(
    adapter: ReviewerAdapter,
    prompt: str,
    scene_ids: Sequence[str],
    stats: ReviewStats,
    chunk: Sequence[str],
) -> list[RawFinding]:
    result = adapter.review_frames((), prompt, thinking=True, max_tokens=EPISODE_ANSWER_TOKENS)
    stats.record(result)
    kwargs: dict[str, Any] = {
        "summary_key": "episode_summary",
        "categories": EPISODE_CATEGORIES,
        "scene_ids": scene_ids,
    }
    try:
        return list(parse_verdict(result.text, **kwargs).findings)
    except VerdictParseError as first:
        stats.parse_retries += 1
        retry = adapter.review_frames(
            (), retry_prompt(prompt, str(first)), thinking=True, max_tokens=EPISODE_ANSWER_TOKENS
        )
        stats.record(retry)
        try:
            return list(parse_verdict(retry.text, **kwargs).findings)
        except VerdictParseError as second:
            stats.unparsed += 1
            first_scene = chunk[0].split(" ")[1] if chunk else scene_ids[0]
            return [replace(_unparsed(second, retry.text), scene_id=first_scene)]


def _review_findings(
    notes: dict[str, _SceneNote],
    episode: Sequence[RawFinding],
    bundle: ExplainerRenderBundle,
    store: FrameStore,
) -> tuple[ReviewFinding, ...]:
    fps = bundle.timeline.fps
    findings: list[ReviewFinding] = []
    for note in notes.values():
        for raw, look in note.findings:
            frames = [image.frame for image in look.images]
            start_ms, end_ms = store.ms(min(frames)), store.ms(max(frames))
            findings.append(
                _finding(
                    len(findings),
                    raw,
                    note.scene.scene_id,
                    _beat_at(note.compiled, min(frames)),
                    TimeInterval(start_ms=start_ms, end_ms=max(end_ms, start_ms + _ms(1, fps))),
                    tuple(image.sha256 for image in look.images),
                    _repair(raw, look, note.scene, bundle),
                )
            )
    for raw in episode:
        note = notes[raw.scene_id or next(iter(notes))]
        compiled = note.compiled
        interval = TimeInterval(
            start_ms=store.ms(compiled.start_frame),
            end_ms=store.ms(compiled.start_frame + compiled.duration_frames),
        )
        findings.append(_finding(len(findings), raw, note.scene.scene_id, None, interval, (), None))
    return tuple(findings)


def _finding(
    index: int,
    raw: RawFinding,
    scene_id: str,
    beat_id: str | None,
    interval: TimeInterval,
    shas: tuple[str, ...],
    repair: TypedRepair | None,
) -> ReviewFinding:
    key = f"{index}:{scene_id}:{raw.category}:{interval.start_ms}:{raw.observed}"
    return ReviewFinding(
        finding_id="fnd_" + hashlib.sha256(key.encode()).hexdigest()[:16],
        scene_id=scene_id,
        beat_id=beat_id,
        interval=interval,
        frame_sha256s=shas,
        category=raw.category,
        severity=raw.severity,
        observed=_clip(raw.observed),
        evidence=_clip(raw.evidence),
        proposed_repair=repair,
        disposition=raw.disposition,
        confidence=raw.confidence,
    )


def _repair(
    raw: RawFinding, look: Look, scene: Scene, bundle: ExplainerRenderBundle
) -> TypedRepair | None:
    """A typed repair only where the look itself names the target the finding is about."""
    crop = next((image.crop for image in look.images if image.crop is not None), None)
    if crop is None or raw.disposition != "fail":
        return None
    if raw.category == "highlight_correctness" and crop.kind == "highlight":
        return SourcePassageRepair(
            repair="source_passage", scene_id=scene.scene_id, quote_id=crop.target_id
        )
    if raw.category != "readability" or crop.kind != "text":
        return None
    entity = next((e for e in bundle.spec.entities if e.entity_id == crop.target_id), None)
    compiled = next(s for s in bundle.timeline.scenes if s.scene_id == scene.scene_id)
    drawn = next((b.text for b in compiled.boxes if b.entity_id == crop.target_id), None)
    if (
        entity is not None
        and entity.short_label
        and drawn != entity.short_label
        and scene.template.template in LABEL_TEMPLATES
    ):
        return LabelWordingRepair(
            repair="label_wording", entity_id=entity.entity_id, short_label=entity.short_label
        )
    return None


def _beat_at(compiled: CompiledExplainerScene, frame: int) -> str | None:
    current: str | None = None
    for action in sorted(compiled.actions, key=lambda a: (a.start_frame, a.index)):
        if action.start_frame <= frame:
            current = action.beat_id
    return current


def _coverage(
    bundle: ExplainerRenderBundle,
    notes: dict[str, _SceneNote],
    sampled: dict[str, set[int]],
    store: FrameStore,
) -> tuple[CoverageSpan, ...]:
    spans = []
    for compiled in bundle.timeline.scenes:
        frames = sorted(sampled.get(compiled.scene_id) or {compiled.start_frame})
        shas = [store.sha(store.path(f)) for f in frames if store.path(f).exists()]
        spans.append(
            CoverageSpan(
                scene_id=compiled.scene_id,
                beat_ids=tuple(b.beat_id for b in notes[compiled.scene_id].scene.beats),
                interval=TimeInterval(
                    start_ms=store.ms(compiled.start_frame),
                    end_ms=store.ms(compiled.start_frame + compiled.duration_frames),
                ),
                sampled_ms=tuple(sorted({store.ms(f) for f in frames})),
                frame_sha256s=tuple(dict.fromkeys(shas)),
            )
        )
    return tuple(spans)


def _categories(model: str) -> tuple[CategoryCoverage, ...]:
    """Every image category, by this model; audio is absent because nothing here listened."""
    return tuple(
        CategoryCoverage(
            category=c,
            covered_by=(
                f"{model}: bulk pass on frames and episode pass on scene notes"
                if c in EPISODE_CATEGORIES
                else f"{model}: bulk pass on frames"
            )[:160],
        )
        for c in IMAGE_CATEGORIES
    )


def _disposition(findings: Sequence[ReviewFinding]) -> Disposition:
    if any(f.disposition == "fail" for f in findings):
        return "fail"
    if any(f.disposition == "uncertain" for f in findings):
        return "uncertain"
    return "pass"


# --- fixture evaluation ---


@dataclass(frozen=True)
class PairEval:
    pair: str
    expected_check: str
    expected_category: str
    rejected_findings: tuple[str, ...]
    accepted_findings: tuple[str, ...]
    missed: bool
    false_alarm: bool
    calls: int
    wall_s: float
    tokens: int


@dataclass(frozen=True)
class FixtureEval:
    missed: int
    false_alarms: int
    per_pair: list[PairEval]
    unscored: int = 0
    json_path: Path | None = None
    md_path: Path | None = None


def evaluate_on_fixtures(
    pairs_dir: Path,
    adapter: ReviewerAdapter,
    *,
    out_dir: Path = DEFAULT_STATE_DIR,
    cache: ReviewCache | None = None,
    render_frames: RenderFrames = render.render_frames,
    now: datetime | None = None,
    log: Callable[[str], object] = lambda _msg: None,
) -> FixtureEval:
    """Missed: rejected with no non-pass finding of the expected category; alarm: see PairEval."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    cache = cache if cache is not None else ReviewCache(out_dir / "cache")
    pack = EvidencePack.model_validate_json((pairs_dir / "pack.json").read_text(encoding="utf-8"))
    expected = expected_checks(pairs_dir / "README.md")
    per_pair: list[PairEval] = []
    for pair_dir in sorted(p for p in pairs_dir.iterdir() if p.is_dir()):
        script_path = pair_dir / "script.json"
        if not script_path.exists():
            continue
        script = ScriptPlan.model_validate_json(script_path.read_text(encoding="utf-8"))
        reports: dict[str, ReviewReport] = {}
        stats = ReviewStats()
        bundle: ExplainerRenderBundle | None = None
        for kind in ("accepted", "rejected"):
            path = pair_dir / f"{kind}.json"
            if not path.exists():
                continue
            bundle = load_bundle(path)
            work = out_dir / "fixture-eval" / pair_dir.name / kind
            work.mkdir(parents=True, exist_ok=True)
            staged = work / "bundle.json"
            staged.write_text(
                render.stage_captures(bundle).canonical_json() + "\n", encoding="utf-8"
            )
            log(f"{pair_dir.name}/{kind}: reviewing")
            reports[kind] = review_episode(
                bundle,
                None,
                pack=pack,
                script=script,
                adapter=adapter,
                cache=cache,
                out_dir=work,
                mode="animatic",
                bundle_path=staged,
                render_frames=render_frames,
                stats=stats,
            )
        if bundle is None or len(reports) < 2:
            continue
        check = expected.get(pair_dir.name, "")
        category = expected_category(check, bundle) if check else ""
        rejected = reports["rejected"].findings
        accepted = reports["accepted"].findings
        detected = any(f.category == category and f.disposition != "pass" for f in rejected)
        alarm = any(
            f.severity in {"blocker", "major"} and f.disposition == "fail" for f in accepted
        )
        per_pair.append(
            PairEval(
                pair=pair_dir.name,
                expected_check=check,
                expected_category=category,
                rejected_findings=tuple(_describe(f) for f in rejected),
                accepted_findings=tuple(_describe(f) for f in accepted),
                missed=bool(category) and not detected,
                false_alarm=alarm,
                calls=stats.calls,
                wall_s=round(stats.wall_s, 1),
                tokens=stats.prompt_tokens + stats.completion_tokens,
            )
        )
        log(f"{pair_dir.name}: missed={per_pair[-1].missed} false_alarm={alarm}")
    result = FixtureEval(
        missed=sum(p.missed for p in per_pair),
        false_alarms=sum(p.false_alarm for p in per_pair),
        per_pair=per_pair,
        unscored=sum(not p.expected_category for p in per_pair),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"fixture-eval-{stamp}.json"
    json_path.write_text(
        json.dumps(
            {
                "pairs_dir": str(pairs_dir),
                "model": adapter.model,
                "model_revision": adapter.model_revision,
                "prompt_sha256": prompt_sha256(),
                "rubric_version": RUBRIC_VERSION,
                "missed": result.missed,
                "false_alarms": result.false_alarms,
                "unscored": result.unscored,
                "per_pair": [p.__dict__ for p in per_pair],
            },
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    md_path = json_path.with_suffix(".md")
    md_path.write_text(fixture_eval_markdown(result), encoding="utf-8")
    return FixtureEval(
        result.missed, result.false_alarms, per_pair, result.unscored, json_path, md_path
    )


def fixture_eval_markdown(result: FixtureEval) -> str:
    rows = [
        "| pair | expected | rejected: findings | accepted: findings | missed | false alarm "
        "| calls | s |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in result.per_pair:
        rows.append(
            f"| {p.pair} | {p.expected_check} → {p.expected_category or '?'} | "
            f"{_summary(p.rejected_findings)} | {_summary(p.accepted_findings)} | "
            f"{'yes' if p.missed else 'no'} | {'yes' if p.false_alarm else 'no'} | "
            f"{p.calls} | {p.wall_s:.0f} |"
        )
    rows.append("")
    rows.append(
        f"missed {result.missed}, false alarms {result.false_alarms}, "
        f"pairs {len(result.per_pair)}, unscored {result.unscored}"
    )
    return "\n".join(rows) + "\n"


def expected_checks(readme: Path) -> dict[str, str]:
    """The README's table of pair -> expected failed check; empty when the folder has none yet."""
    if not readme.is_file():
        return {}
    table = re.findall(
        r"^\|\s*`(\w+)`\s*\|\s*`(\w+)`\s*\|", readme.read_text(encoding="utf-8"), re.M
    )
    return dict(table)


def expected_category(check: str, bundle: ExplainerRenderBundle) -> str:
    if check == "ocr_text" and any(
        isinstance(s.template, SourceDocumentTemplate) for s in bundle.spec.scenes
    ):
        return "highlight_correctness"
    return CATEGORY_OF.get(check, "")


def load_bundle(path: Path, root: Path = REPO_ROOT) -> ExplainerRenderBundle:
    """A bundle JSON with capture tile paths made absolute against the repo root."""
    bundle = ExplainerRenderBundle.model_validate_json(path.read_text(encoding="utf-8"))
    if not bundle.captures:
        return bundle
    captures = tuple(
        c.model_copy(
            update={
                "tiles": tuple(
                    t
                    if t.path.startswith(render.URL_PREFIXES) or Path(t.path).is_absolute()
                    else t.model_copy(update={"path": str(root / t.path)})
                    for t in c.tiles
                )
            }
        )
        for c in bundle.captures
    )
    return bundle.model_copy(update={"captures": captures})


def _describe(finding: ReviewFinding) -> str:
    return f"{finding.category}/{finding.severity}/{finding.disposition}: {finding.observed}"


def _summary(described: Sequence[str]) -> str:
    if not described:
        return "none"
    return "; ".join(d.split(": ", 1)[0] for d in described)


def _clip(text: str, limit: int = TEXT_LIMIT) -> str:
    text = text or "-"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ms(frame: int, fps: int) -> int:
    return frame * 1000 // fps
