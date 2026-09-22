"""Cues to milliseconds to frames: the token clock, action spans, scene bounds, reading floor."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.layout import synthetic_id
from content_factory.schemas.explainer import (
    AnnotateAction,
    Cue,
    NarrationManifest,
    Scene,
    ScriptPlan,
    VisualSpec,
)

TimingSource = Literal["estimated", "aligned"]
Durations = Mapping[tuple[str, int], int]
DURATION_MS: dict[str, int] = {"beat": 300, "short": 600, "medium": 1200, "long": 2400}
ESTIMATED_TOKEN_MS = 385
BEFORE_MS = 300
AFTER_MS = 150
SCENE_LEAD_MS = 400
INSPECTION_MS = 1000
TAIL_MS = 1000
WORDS_PER_S = 3.3
MIN_READING_MS = 1200


def reading_ms(text: str) -> int:
    """EDITORIAL.md reading floor: words / 3.3 per second, never under 1.2 s."""
    return max(MIN_READING_MS, math.ceil(len(text.split()) / WORDS_PER_S * 1000))


def to_frames(ms: int, fps: int) -> int:
    """Round half up, so a boundary never lands a frame early."""
    return math.floor(ms * fps / 1000 + 0.5)


def source_hash(source: TimingSource) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


class TokenClock:
    """When each script token is spoken: aligned from a manifest, else 385 ms per token."""

    def __init__(self, script: ScriptPlan, narration: NarrationManifest | None = None) -> None:
        self.timing_source: TimingSource = "estimated" if narration is None else "aligned"
        self._spans: dict[tuple[str, int], tuple[int, int]] = {}
        self.narration_end_ms = 0
        if narration is None:
            self._estimate(script)
        else:
            self._align(script, narration)

    def _estimate(self, script: ScriptPlan) -> None:
        cursor = 0
        for segment in script.segments:
            for k in range(len(segment.tokens)):
                self._spans[segment.segment_id, k] = (cursor, cursor + ESTIMATED_TOKEN_MS)
                cursor += ESTIMATED_TOKEN_MS
        self.narration_end_ms = cursor

    def _align(self, script: ScriptPlan, narration: NarrationManifest) -> None:
        order = {s.segment_id: i for i, s in enumerate(script.segments)}
        takes = sorted(
            narration.takes, key=lambda t: min(order.get(s, len(order)) for s in t.segment_ids)
        )
        # A manifest with take offsets is authoritative; without them takes are laid end to end.
        explicit = any(t.start_ms for t in takes)
        offset = 0
        words: dict[tuple[str, int], tuple[int, int]] = {}
        take_start: dict[str, int] = {}
        for take in takes:
            start = take.start_ms if explicit else offset
            for sid in take.segment_ids:
                take_start[sid] = start
            if take.alignment is not None:
                for w in take.alignment.words:
                    words[w.segment_id, w.token_index] = (start + w.start_ms, start + w.end_ms)
            offset = start + take.duration_ms
        for segment in script.segments:
            previous_end = take_start.get(segment.segment_id, 0)
            for k in range(len(segment.tokens)):
                span = words.get((segment.segment_id, k))
                if span is None:
                    span = (previous_end, previous_end + ESTIMATED_TOKEN_MS)
                self._spans[segment.segment_id, k] = span
                previous_end = span[1]
        self.narration_end_ms = max(offset, narration.total_duration_ms)

    def start_ms(self, segment_id: str, token_index: int) -> int:
        return self._spans[segment_id, token_index][0]

    def end_ms(self, segment_id: str, token_index: int) -> int:
        return self._spans[segment_id, token_index][1]

    def anchor_ms(self, cue: Cue) -> int:
        if cue.relation == "before":
            return max(0, self.start_ms(cue.segment_id, cue.token_start) - BEFORE_MS)
        if cue.relation == "after":
            return self.end_ms(cue.segment_id, cue.token_end) + AFTER_MS
        return self.start_ms(cue.segment_id, cue.token_start)


@dataclass(frozen=True)
class TimedAction:
    beat_id: str
    index: int
    action: str
    start_ms: int
    end_ms: int
    targets: tuple[str, ...]


@dataclass(frozen=True)
class TimedScene:
    scene_id: str
    start_ms: int
    end_ms: int
    actions: tuple[TimedAction, ...]


@dataclass(frozen=True)
class _Shown:
    entity_id: str
    text: str
    from_ms: int
    until_ms: int | None
    cut_by: str | None


def resolve_timing(
    spec: VisualSpec,
    clock: TokenClock,
    texts: Mapping[str, Mapping[str, str]],
    durations: Mapping[str, Durations] | None = None,
) -> tuple[TimedScene, ...]:
    """Absolute ms spans for every action and scene, or timing issues for texts cut short."""
    scenes: list[TimedScene] = []
    shown: dict[str, list[_Shown]] = {}
    start = 0
    for i, scene in enumerate(spec.scenes):
        if i > 0:
            first = clock.anchor_ms(scene.beats[0].cue)
            start = max(first - SCENE_LEAD_MS, scenes[-1].end_ms)
            scenes[-1] = TimedScene(
                scenes[-1].scene_id, scenes[-1].start_ms, start, scenes[-1].actions
            )
        actions, shown[scene.scene_id] = _scene_actions(
            scene,
            clock,
            start,
            texts.get(scene.scene_id, {}),
            (durations or {}).get(scene.scene_id),
        )
        last_end = max((a.end_ms for a in actions), default=start)
        scenes.append(TimedScene(scene.scene_id, start, last_end, actions))
    final = scenes[-1]
    end = max(final.end_ms, clock.narration_end_ms) + TAIL_MS
    scenes[-1] = TimedScene(final.scene_id, final.start_ms, end, final.actions)
    issues = _reading_floor_issues(spec, scenes, shown)
    if issues:
        raise EpisodeInvalidError(issues)
    return tuple(scenes)


def _scene_actions(
    scene: Scene,
    clock: TokenClock,
    scene_start: int,
    texts: Mapping[str, str],
    overrides: Durations | None = None,
) -> tuple[tuple[TimedAction, ...], list[_Shown]]:
    """Spans per action; with overrides (a source scene) actions run one after another."""
    actions: list[TimedAction] = []
    cursor = scene_start
    shown: list[_Shown] = [
        _Shown(eid, texts[eid], scene_start, None, None)
        for eid in scene.initial_visible
        if eid in texts
    ]
    unread: list[str] = [s.text for s in shown]
    for beat in scene.beats:
        anchor = max(clock.anchor_ms(beat.cue), scene_start)
        for k, action in enumerate(beat.actions):
            duration = DURATION_MS[beat.cue.duration_class]
            if overrides is not None:
                anchor = max(anchor, cursor)
                duration = overrides.get((beat.beat_id, k), duration)
            targets: tuple[str, ...] = tuple(getattr(action, "targets", ()))
            if action.action == "hold":
                # A hold reads what appeared since the last hold as one passage, then pauses.
                duration += (reading_ms(" ".join(unread)) if unread else 0) + INSPECTION_MS
                unread.clear()
            elif action.action == "reveal":
                for eid in targets:
                    if eid in texts:
                        shown.append(_Shown(eid, texts[eid], anchor, None, None))
                        unread.append(texts[eid])
            elif isinstance(action, AnnotateAction):
                eid = synthetic_id(f"annot{k}", beat.beat_id)
                shown.append(_Shown(eid, action.text, anchor, None, None))
                unread.append(action.text)
            elif action.action == "hide":
                for eid in targets:
                    shown = [
                        _Shown(s.entity_id, s.text, s.from_ms, anchor, beat.beat_id)
                        if s.entity_id == eid and s.until_ms is None
                        else s
                        for s in shown
                    ]
            actions.append(
                TimedAction(beat.beat_id, k, action.action, anchor, anchor + duration, targets)
            )
            cursor = anchor + duration
    return tuple(actions), shown


def _reading_floor_issues(
    spec: VisualSpec, scenes: list[TimedScene], shown: dict[str, list[_Shown]]
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for i, scene in enumerate(spec.scenes):
        timed = scenes[i]
        next_beat = spec.scenes[i + 1].beats[0].beat_id if i + 1 < len(spec.scenes) else None
        for s in shown[scene.scene_id]:
            until = timed.end_ms if s.until_ms is None else s.until_ms
            available = until - s.from_ms
            needed = reading_ms(s.text) + INSPECTION_MS
            if available >= needed:
                continue
            if s.cut_by is not None:
                move = f"move beat {s.cut_by} later"
            elif next_beat is not None:
                move = f"move beat {next_beat} later"
            else:
                move = f"add a hold to scene {scene.scene_id}"
            issues.append(
                ContractIssue(
                    kind="timing",
                    where=f"VisualSpec.scenes[{i}]",
                    message=(
                        f"scene {scene.scene_id}: {s.entity_id} ({s.text!r}) needs {needed} ms on "
                        f"screen but has {available} ms."
                    ),
                    fix=f"{move}, split scene {scene.scene_id}, or shorten the label.",
                    ids=(scene.scene_id, s.entity_id),
                )
            )
    return issues


def scene_frames(scenes: tuple[TimedScene, ...], fps: int) -> tuple[tuple[int, int], ...]:
    """(start_frame, duration_frames) per scene: contiguous, each at least one frame long."""
    bounds = [0]
    for scene in scenes:
        bounds.append(max(bounds[-1] + 1, to_frames(scene.end_ms, fps)))
    return tuple((bounds[i], bounds[i + 1] - bounds[i]) for i in range(len(scenes)))
