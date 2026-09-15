"""Deterministic shot planner: one shot per story beat, camera preset by scene kind."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from content_factory.schemas.scenes import StoryPlan, VisualBeat
from content_factory.schemas.shots import (
    CameraKeyframe,
    CameraPreset,
    CameraSpec,
    CharacterSpec,
    EnvironmentSpec,
    LibraryPose,
    LightingPreset,
    LightingSpec,
    SegmentClipPose,
    ShotPlan,
    ShotSpec,
    Transform,
)
from content_factory.shots.framing import (
    Framing,
    achieved_body_fraction,
    body_fraction_for,
    solve_framing,
    solve_framing_tracking,
    standing_extent,
)
from content_factory.shots.presets import (
    PLANNER_VERSION,
    SCENE_KIND_PRESET,
    anchor_frames_for,
    camera_keyframes,
)
from content_factory.shots.prompt_compile import (
    progression_sentence,
    state_sentence,
)

CLIPS_DIR = Path("/mnt/fast/models/blender-assets/clips")

BODY_FRACTION = 0.62
"""Target height of the subject in frame. Above the measured 0.33 cliff where the image model
stops reading the pose skeleton, and low enough to leave the pair room to move."""

ESTIMATE_OPTIMISM = 0.025
"""How much the framing estimate flatters itself, measured against rendered layout boxes.

The estimate models a figure as a hip plus a standing head; the render measures the mesh's own
bounding box. On the two staged portrait shots that were checked frame by frame the estimate came
in 0.016 and 0.022 of frame height above what the render delivered, always in the same direction.
So the cliff is tested with this added on, or a shot estimated at 0.34 renders at 0.318 and is
called legible while the image model is already ignoring it."""

POSE_CLIFF_FRACTION = 0.33
"""Below this the pose skeleton stops being read at all. Measured, not chosen: at 33 % of frame
height the image model ignored the conditioning and invented its own scene. A shot that solves
under it has thrown away the reason it was staged from a real take, so it is re-solved."""

UNIFORM_KIND_ROTATION: tuple[CameraPreset, ...] = (
    CameraPreset.slow_push_in,
    CameraPreset.static,
    CameraPreset.pan_right,
    CameraPreset.crane_down,
    CameraPreset.slow_pull_out,
    CameraPreset.orbit_left,
)
"""Framings to rotate through when every beat is the same scene kind, in beat order.

Ordered as a cut is: open moving in, hold, move across, come down, pull back out, and around.
Six because six is the length of a picture story on this repo's own lanes and a rotation that
divides evenly into a shorter one still varies it.
"""


# A lens per camera preset, so a lane still gets some variety of framing across its shots even
# though every staged shot is solved the same way.
_LENS_BY_PRESET: dict[CameraPreset, float] = {
    CameraPreset.static: 50.0,
    CameraPreset.slow_push_in: 40.0,
    CameraPreset.slow_pull_out: 40.0,
    CameraPreset.pan_left: 35.0,
    CameraPreset.pan_right: 35.0,
    CameraPreset.orbit_left: 65.0,
    CameraPreset.orbit_right: 65.0,
    CameraPreset.crane_up: 45.0,
    CameraPreset.crane_down: 45.0,
}


def _azimuth_for(shot_id: str) -> float:
    """A camera angle that varies per shot but is the same every run."""
    digest = hashlib.sha256(shot_id.encode()).digest()
    return -150.0 + (digest[0] / 255.0) * 300.0


"""Where the baked cf.clip.v2 clips live. A match is only stageable if its clip is on disk."""

DEFAULT_BEAT_MS = 4000
DEFAULT_CHARACTER_ASSET = "man_01"
DEFAULT_APPEARANCE = "a person in plain unbranded clothes, no logos, no props"
"""What the preset planner's one staged figure looks like when the operator wrote nothing.

The mesh carries a body and nothing else, and the Blender compiler renders that body's depth and
normals — so an undescribed figure is an *untextured* figure, and the image model draws exactly
what it is shown: ten anchors of a grey mannequin in a T-pose (measured on `picture-story`,
2026-09-10). The preset planner has no source for a description, which is precisely why it needs
a default rather than an empty field. Deliberately dull, for the same reason as
``prompt_compile.DEFAULT_VISUAL_SUBJECT``: the planner must not invent a character the operator
did not ask for. Anything real goes in the shot plan.
"""

DEFAULT_CHARACTER_HEIGHT_M = 1.75
LTX_MIN_FRAMES = 9
LTX_MAX_FRAMES = 257


def snap_ltx_length(frames: int, *, lo: int = LTX_MIN_FRAMES, hi: int = LTX_MAX_FRAMES) -> int:
    """Nearest 8k+1 frame count within [lo, hi] (LTX-2.5 generates 8k+1 frames)."""

    k = max(1, round((frames - 1) / 8))
    snapped = 8 * k + 1
    return min(max(snapped, lo), hi)


def _beat_duration_ms(beat: VisualBeat) -> int:
    if beat.planned_duration_ms is not None:
        return beat.planned_duration_ms
    if beat.measured_start_ms is not None and beat.measured_end_ms is not None:
        return max(200, beat.measured_end_ms - beat.measured_start_ms)
    return DEFAULT_BEAT_MS


def _scene_kind_for(story: StoryPlan, beat_id: str) -> str:
    for scene in story.scenes:
        if scene.beat_id == beat_id:
            return scene.kind
    return "default"


def _short_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _count_word(n: int) -> str:
    return {1: "figure", 2: "pair"}.get(n, f"{n} figures")


def _clip_verb(match: dict) -> str:
    """What the retrieved clip shows, in an everyday word, from the lexicon that found it."""
    from content_factory.reference.lexicon import phrase_for

    tags = [str(t) for t in match.get("matched_interaction") or () if t]
    for tag in sorted(tags):
        phrase = phrase_for(f"interaction:{tag}")
        if phrase:
            return phrase
    if tags:
        return sorted(tags)[0].replace("_", " ")
    return "move together"


def plan_shots_from_story(
    story: StoryPlan,
    *,
    width: int,
    height: int,
    fps: int = 24,
    snap_to_ltx_length: bool = True,
    character_asset: str = DEFAULT_CHARACTER_ASSET,
    with_character: bool = True,
    lighting_preset: LightingPreset = "studio",
) -> ShotPlan:
    """One shot per beat, in beat order."""
    if fps not in (24, 25, 30, 60):
        msg = f"unsupported fps {fps}"
        raise ValueError(msg)
    beats = sorted(story.beats, key=lambda b: b.order)
    characters: tuple[CharacterSpec, ...] = (
        (
            CharacterSpec(
                id="subject",
                asset=character_asset,
                transform=Transform(),
                pose=LibraryPose(name="idle"),
                appearance=DEFAULT_APPEARANCE,
            ),
        )
        if with_character
        else ()
    )
    # The ShotSpec defaults, named here because the description is compiled from them: what the
    # prompt says about light and place has to be what Blender actually stages.
    lighting = LightingSpec(preset=lighting_preset)
    environment = EnvironmentSpec()
    specs: list[ShotSpec] = []
    # The preset comes from the scene kind, which is right when the kinds differ and produces one
    # film of N identical shots when they do not.
    kinds = [_scene_kind_for(story, b.beat_id) for b in beats]
    uniform = len(set(kinds)) <= 1 and len(beats) > 1
    for i, beat in enumerate(beats):
        frames = round(_beat_duration_ms(beat) / 1000 * fps)
        frames = snap_ltx_length(frames) if snap_to_ltx_length else min(max(frames, 1), 600)
        kind = kinds[i]
        preset = (
            UNIFORM_KIND_ROTATION[i % len(UNIFORM_KIND_ROTATION)]
            if uniform
            else SCENE_KIND_PRESET.get(kind, CameraPreset.static)
        )
        specs.append(
            ShotSpec(
                shot_id=f"shot_{_short_hash(story.plan_id, beat.beat_id)}",
                order=i,
                beat_id=beat.beat_id,
                frame_count=frames,
                fps=fps,  # type: ignore[arg-type]
                width=width,
                height=height,
                camera=CameraSpec(
                    preset=preset,
                    keyframes=camera_keyframes(
                        preset,
                        frames,
                        subject_height_m=DEFAULT_CHARACTER_HEIGHT_M,
                        width=width,
                        height=height,
                    ),
                ),
                characters=characters,
                anchor_frames=anchor_frames_for(preset, frames),
                # Never the beat's own words.
                motion_prompt=progression_sentence(preset=preset)[:2000],
                description=state_sentence(
                    preset=preset,
                    lighting_preset=lighting.preset,
                    environment=environment,
                    characters=characters,
                    visual_subject=story.visual_subject,
                )[:2000],
            )
        )
    return ShotPlan(
        plan_id=f"shp_{_short_hash(story.plan_id, story.content_hash(), 'shots')}",
        deliverable_id=story.deliverable_id,
        story_plan_hash=story.content_hash(),
        planner="story_presets",
        planner_version=PLANNER_VERSION,
        shots=tuple(specs),
    )


def load_fixture_plan(path: Path) -> ShotPlan:
    """A hand-authored ShotPlan (the ``planner="fixture"`` path)."""
    return ShotPlan.model_validate_json(path.read_text(encoding="utf-8"))


def plan_shots_from_reference(
    story: StoryPlan,
    selection: dict,
    *,
    width: int,
    height: int,
    fps: int = 24,
    snap_to_ltx_length: bool = True,
    with_character: bool = True,
    cast: tuple[tuple[str, str], ...] = (("man", "man_01"), ("woman", "woman_01")),
) -> ShotPlan:
    """One shot per beat, each staged from the reference clip retrieval chose for that beat."""
    if fps not in (24, 25, 30, 60):
        msg = f"unsupported fps {fps}"
        raise ValueError(msg)

    chosen: dict[str, tuple[str, str]] = {}
    for entry in selection.get("beats", []):
        match_set = entry.get("match_set") or {}
        for match in match_set.get("matches", []):
            clip = match.get("clip_id", "")
            # Only a retargeted mocap clip can drive a rig. Everything else in the library is
            # reference to look at, not something to be staged from.
            if clip and (CLIPS_DIR / f"{clip}.json").is_file():
                chosen[str(entry["beat_id"])] = (clip, _clip_verb(match))
                break

    fallback = plan_shots_from_story(
        story,
        width=width,
        height=height,
        fps=fps,
        snap_to_ltx_length=snap_to_ltx_length,
        with_character=with_character,
    )
    # `characters: none` is a lane saying it stages nobody, and it has to mean that here too.
    if not chosen or not with_character:
        return fallback.model_copy(update={"planner": "reference"})

    specs: list[ShotSpec] = []
    for spec in fallback.shots:
        picked = chosen.get(spec.beat_id or "")
        if picked is None:
            specs.append(spec)
            continue
        clip, verb = picked
        doc = json.loads((CLIPS_DIR / f"{clip}.json").read_text())
        # As many characters as the clip has performers, never more.
        actors = [str(a["actor_id"]) for a in doc.get("actors", [])] or ["a"]
        characters = tuple(
            CharacterSpec(
                id=cid,
                asset=asset,
                transform=Transform(),
                pose=SegmentClipPose(name=clip, actor=actor),
                seg_id=i + 1,
            )
            for i, ((cid, asset), actor) in enumerate(zip(cast, actors, strict=False))
        )
        # The camera has to be re-solved, not inherited.
        lens = _LENS_BY_PRESET.get(spec.camera.preset, 50.0)
        azimuth = _azimuth_for(spec.shot_id)
        clip_fps = float(doc.get("fps", spec.fps))
        # The frames that have to be legible: the ones handed to the image model, plus both ends
        # of the shot, because that is where a static camera loses a cast that walks.
        shot_frames = tuple(sorted({0, spec.frame_count - 1, *spec.anchor_frames}))
        measure = {
            "sample_frames": shot_frames,
            "actor_ids": tuple(actors),
            "fps": spec.fps,
            "width": width,
            "height": height,
            "sensor_width_mm": spec.camera.sensor_width_mm,
        }
        framing = solve_framing(
            doc,
            width=width,
            height=height,
            lens_mm=lens,
            sensor_width_mm=spec.camera.sensor_width_mm,
            body_fraction=BODY_FRACTION,
            azimuth_deg=azimuth,
        )
        keyframes: tuple[CameraKeyframe, ...] = (_keyframe(0, framing),)
        # Decide on the measurement, not on the solve's own target.
        worst = _worst_body_fraction(doc, keyframes, **measure)
        track_note = ""
        # Below the cliff the pose skeleton is ignored, so staging from a real take has bought
        # nothing.
        if _under_cliff(worst):
            # The anchor frames are the ones handed to the image model, so they are the ones that
            # have to be legible.
            tracked = solve_framing_tracking(
                doc,
                tuple(round(f * clip_fps / spec.fps) for f in shot_frames),
                actor_ids=tuple(actors),
                width=width,
                height=height,
                lens_mm=lens,
                sensor_width_mm=spec.camera.sensor_width_mm,
                body_fraction=BODY_FRACTION,
                azimuth_deg=azimuth,
            )
            candidate = tuple(_keyframe(f, t) for f, t in zip(shot_frames, tracked, strict=True))
            improved = _worst_body_fraction(doc, candidate, **measure)
            # Only if it is actually better.
            if improved > worst:
                keyframes, worst = candidate, improved
                track_note = f", tracking {len(actors)} over {len(keyframes)} keyframes"
        camera = spec.camera.model_copy(update={"keyframes": keyframes})
        # The clip is what the shot is *of*, so it names the action.
        action = f"the {_count_word(len(characters))} {verb} exactly as in the captured take"
        note = f"staged from {clip}, framed at {worst:.2f} body height{track_note}"
        specs.append(
            spec.model_copy(
                update={
                    "characters": characters,
                    "camera": camera,
                    "action": action[:600],
                    "staging_note": note[:600],
                    "motion_prompt": progression_sentence(preset=spec.camera.preset, action=action)[
                        :2000
                    ],
                    "description": state_sentence(
                        preset=spec.camera.preset,
                        lighting_preset=spec.lighting.preset,
                        environment=spec.environment,
                        characters=characters,
                        visual_subject=story.visual_subject,
                    )[:2000],
                }
            )
        )
    return ShotPlan(
        plan_id=f"shp_{_short_hash(story.plan_id, story.content_hash(), 'reference')}",
        deliverable_id=story.deliverable_id,
        story_plan_hash=story.content_hash(),
        planner="reference",
        planner_version=PLANNER_VERSION,
        shots=tuple(specs),
    )


def _under_cliff(body_fraction: float) -> bool:
    """Whether a measured framing is too small for the pose skeleton to be read."""
    return body_fraction < POSE_CLIFF_FRACTION + ESTIMATE_OPTIMISM


def _keyframe(frame_index: int, framing: Framing) -> CameraKeyframe:
    """A camera keyframe from a solved framing."""
    return CameraKeyframe(
        frame_index=frame_index,
        position=framing.position,
        look_at=framing.look_at,
        lens_mm=framing.lens_mm,
        focus_distance=framing.distance_m,
    )


def _worst_body_fraction(
    doc: dict,
    keyframes: tuple[CameraKeyframe, ...],
    *,
    sample_frames: tuple[int, ...],
    actor_ids: tuple[str, ...],
    fps: int,
    width: int,
    height: int,
    sensor_width_mm: float,
) -> float:
    """The smallest the cast gets at any of ``sample_frames``, as a fraction of frame height."""
    clip_fps = float(doc.get("fps", fps))
    ordered = sorted(keyframes, key=lambda k: k.frame_index)
    worst = 1.0
    for frame in sample_frames:
        at = [k for k in ordered if k.frame_index <= frame] or ordered[:1]
        kf = at[-1]
        worst = min(
            worst,
            achieved_body_fraction(
                doc,
                round(frame * clip_fps / fps),
                actor_ids=actor_ids,
                lens_mm=kf.lens_mm,
                camera_position=kf.position,
                width=width,
                height=height,
                sensor_width_mm=sensor_width_mm,
            ),
        )
    return worst


def underframed_shots(plan: ShotPlan) -> tuple[tuple[str, float], ...]:
    """``(shot_id, worst_body_fraction)`` for staged shots whose subject is under the cliff."""
    out: list[tuple[str, float]] = []
    for spec in plan.shots:
        if not spec.characters or not spec.camera.keyframes:
            continue
        sample = tuple(
            sorted(
                {0, spec.frame_count - 1, *spec.anchor_frames}
                | {k.frame_index for k in spec.camera.keyframes}
            )
        )
        poses = [c.pose for c in spec.characters if isinstance(c.pose, SegmentClipPose)]
        path = CLIPS_DIR / f"{poses[0].name}.json" if poses else None
        if path is None or not path.is_file():
            # No clip to read a hip height out of, so the cast is modelled standing where it was
            # placed.
            centre, _radius, top = standing_extent(
                tuple(c.transform.position for c in spec.characters)
            )
            worst = min(
                body_fraction_for(
                    centre,
                    top,
                    lens_mm=kf.lens_mm,
                    camera_position=kf.position,
                    width=spec.width,
                    height=spec.height,
                    sensor_width_mm=spec.camera.sensor_width_mm,
                )
                for kf in spec.camera.keyframes
            )
            if _under_cliff(worst):
                out.append((spec.shot_id, worst))
            continue
        worst = _worst_body_fraction(
            json.loads(path.read_text()),
            spec.camera.keyframes,
            sample_frames=sample,
            actor_ids=tuple(dict.fromkeys(p.actor for p in poses)),
            fps=spec.fps,
            width=spec.width,
            height=spec.height,
            sensor_width_mm=spec.camera.sensor_width_mm,
        )
        if _under_cliff(worst):
            out.append((spec.shot_id, worst))
    return tuple(sorted(out))
