"""The compiler: validated episode in, ExplainerRenderBundle out, every failure an issue list."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from pathlib import Path

from content_factory.explainer import render
from content_factory.explainer.colors import allocate_colors
from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.fonts import FONTS_VERSION
from content_factory.explainer.layout import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    LayoutChoice,
    SceneBoxes,
    boxes_for,
    region,
    regions_for,
)
from content_factory.explainer.timing import (
    TokenClock,
    resolve_timing,
    scene_frames,
    source_hash,
    to_frames,
)
from content_factory.explainer.tokens_gen import DESIGN_SYSTEM_VERSION
from content_factory.explainer.validate import validate_episode
from content_factory.schemas.explainer import (
    CompiledExplainerScene,
    DiagramLayout,
    DiagramTemplate,
    EvidencePack,
    ExplainerRenderBundle,
    ExplainerTimeline,
    InputHash,
    NarrationManifest,
    PixelBox,
    ResolvedAction,
    ScriptPlan,
    SourceCaptureManifest,
    VisualSpec,
)

COMPILER_VERSION = "0.1.0"
LayoutDiagrams = Callable[[VisualSpec, dict[str, PixelBox]], tuple[DiagramLayout, ...]]


def compile_episode(
    pack: EvidencePack,
    script: ScriptPlan,
    spec: VisualSpec,
    *,
    narration: NarrationManifest | None = None,
    manifests: Iterable[SourceCaptureManifest] = (),
    layout_diagrams: LayoutDiagrams | None = None,
    fps: int = 30,
) -> ExplainerRenderBundle:
    validate_episode(pack, script, spec, manifests, narration)
    issues: list[ContractIssue] = []
    colors = _collect(lambda: allocate_colors(spec), issues) or {}
    boxes: dict[str, SceneBoxes] = {}
    for scene in spec.scenes:
        placed = _collect(lambda s=scene: boxes_for(s, spec), issues)
        if placed is not None:
            boxes[scene.scene_id] = placed
    if issues:
        raise EpisodeInvalidError(issues)
    clock = TokenClock(script, narration)
    timed = resolve_timing(spec, clock, {sid: b.texts for sid, b in boxes.items()})
    frames = scene_frames(timed, fps)
    scenes = tuple(
        CompiledExplainerScene(
            scene_id=scene.scene_id,
            start_frame=start,
            duration_frames=duration,
            regions=regions_for(scene),
            colors=colors[scene.scene_id],
            boxes=boxes[scene.scene_id].boxes,
            actions=tuple(
                ResolvedAction(
                    beat_id=a.beat_id,
                    index=a.index,
                    action=a.action,
                    start_frame=to_frames(a.start_ms, fps),
                    end_frame=to_frames(a.end_ms, fps),
                    targets=a.targets,
                )
                for a in t.actions
            ),
        )
        for scene, t, (start, duration) in zip(spec.scenes, timed, frames, strict=True)
    )
    spec_hash = spec.spec_hash()
    timeline = ExplainerTimeline(
        timeline_id=f"tl_{spec_hash[:12]}",
        spec_id=spec.spec_id,
        spec_hash=spec_hash,
        script_hash=script.script_hash(),
        pack_hash=pack.pack_hash(),
        narration_manifest_id=narration.manifest_id if narration else None,
        fps=fps,
        width=CANVAS_WIDTH,
        height=CANVAS_HEIGHT,
        total_frames=sum(d for _, d in frames),
        scenes=scenes,
        inputs=(
            InputHash(name="pack", sha256=pack.pack_hash()),
            InputHash(name="script", sha256=script.script_hash()),
            InputHash(name="spec", sha256=spec_hash),
            InputHash(name="design_system_version", sha256=_text_hash(str(DESIGN_SYSTEM_VERSION))),
            InputHash(name="fonts_version", sha256=_text_hash(FONTS_VERSION)),
            InputHash(name="compiler_version", sha256=_text_hash(COMPILER_VERSION)),
            InputHash(name="timing_source", sha256=source_hash(clock.timing_source)),
        ),
        compiler_version=COMPILER_VERSION,
    )
    plots = {
        s.scene_id: region(s, "plot")
        for s in spec.scenes
        if isinstance(s.template, DiagramTemplate)
    }
    layouts = (layout_diagrams or render.layout_diagrams)(spec, plots) if plots else ()
    referenced = {a.dataset_id for a in spec.assets if a.dataset_id is not None}
    return ExplainerRenderBundle(
        bundle_id=f"bndl_{timeline.content_hash()[:16]}",
        spec=spec,
        timeline=timeline,
        datasets=tuple(d for d in pack.datasets if d.dataset_id in referenced),
        layouts=tuple(layouts),
        design_system_version=DESIGN_SYSTEM_VERSION,
        fonts_version=FONTS_VERSION,
    )


def layout_choices(spec: VisualSpec) -> dict[str, tuple[LayoutChoice, ...]]:
    """Which entities the renderer must draw by short_label, per scene."""
    return {scene.scene_id: boxes_for(scene, spec).choices for scene in spec.scenes}


def write_bundle(bundle: ExplainerRenderBundle, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bundle.canonical_json() + "\n", encoding="utf-8")
    return path


def _collect[T](step: Callable[[], T], issues: list[ContractIssue]) -> T | None:
    try:
        return step()
    except EpisodeInvalidError as error:
        issues.extend(error.issues)
        return None


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
