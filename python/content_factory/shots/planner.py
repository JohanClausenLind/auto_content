"""Deterministic shot planner: one shot per story beat, camera preset by scene kind.

This is the first-version planner. An LLM-planned variant (``planner="llm"``) is deliberately not
implemented yet; when it is, it produces the same ``ShotPlan`` contract and is validated the same
way, so nothing downstream changes.
"""

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
    """A camera angle that varies per shot but is the same every run.

    Derived from the shot id rather than a counter, so inserting a beat does not reframe the shots
    after it, and rather than randomly, so a rerun of the same plan is the same film.
    """
    digest = hashlib.sha256(shot_id.encode()).digest()
    return -150.0 + (digest[0] / 255.0) * 300.0


"""Where the baked cf.clip.v2 clips live. A match is only stageable if its clip is on disk."""

DEFAULT_BEAT_MS = 4000
DEFAULT_CHARACTER_ASSET = "man_01"
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
    """What the retrieved clip shows, in an everyday word, from the lexicon that found it.

    The match already carries the closed-vocabulary interaction tags the query hit, and the
    committed lexicon maps those back to the words a person would use. Going through the table
    rather than prettifying the tag keeps one spelling per tag and keeps it in the file that is
    hashed into every answer. Falls back to the bare tag with its underscores opened out, and to a
    neutral clause when the match names no interaction at all.
    """
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
) -> ShotPlan:
    """One shot per beat, in beat order. Pure: same story and arguments -> same plan."""
    if fps not in (24, 25, 30, 60):
        msg = f"unsupported fps {fps}"
        raise ValueError(msg)
    beats = sorted(story.beats, key=lambda b: b.order)
    character = CharacterSpec(
        id="subject",
        asset=character_asset,
        transform=Transform(),
        pose=LibraryPose(name="idle"),
    )
    # The ShotSpec defaults, named here because the description is compiled from them: what the
    # prompt says about light and place has to be what Blender actually stages, or the image model
    # is told one scene and conditioned on another.
    lighting = LightingSpec()
    environment = EnvironmentSpec()
    specs: list[ShotSpec] = []
    for i, beat in enumerate(beats):
        frames = round(_beat_duration_ms(beat) / 1000 * fps)
        frames = snap_ltx_length(frames) if snap_to_ltx_length else min(max(frames, 1), 600)
        kind = _scene_kind_for(story, beat.beat_id)
        preset = SCENE_KIND_PRESET.get(kind, CameraPreset.static)
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
                characters=(character,),
                anchor_frames=anchor_frames_for(preset, frames),
                # Never the beat's own words. ``display_text`` is narration: it is written to be
                # spoken over a picture, and handed to an image model it asks for an illustration
                # of an argument. Both strings are compiled from the staging instead.
                motion_prompt=progression_sentence(preset=preset)[:2000],
                description=state_sentence(
                    preset=preset,
                    lighting_preset=lighting.preset,
                    environment=environment,
                    characters=(character,),
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
    cast: tuple[tuple[str, str], ...] = (("man", "man_01"), ("woman", "woman_01")),
) -> ShotPlan:
    """One shot per beat, each staged from the reference clip retrieval chose for that beat.

    This is the step that makes the reference library change a film rather than only describe one.
    A beat whose best match is a baked ``cf.clip.v2`` mocap clip gets one character per performer
    in that clip - two for a two-person take, so the contact on screen is the contact that was
    captured; one for a solo take such as a run. A beat with no such match falls back to the preset
    planner's single idle character and says so in its description, because inventing a two-person
    staging from nothing is what produced the interpenetrating hands this whole path exists to
    avoid.

    ``selection`` is the document ``Stage.find_reference`` writes. Pure: the same story and the same
    selection give the same plan.
    """
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
    )
    if not chosen:
        return fallback.model_copy(update={"planner": "reference"})

    specs: list[ShotSpec] = []
    for spec in fallback.shots:
        picked = chosen.get(spec.beat_id or "")
        if picked is None:
            specs.append(spec)
            continue
        clip, verb = picked
        doc = json.loads((CLIPS_DIR / f"{clip}.json").read_text())
        # As many characters as the clip has performers, never more. A solo clip - the running
        # trials are solo, because no two-person take in the library holds a sustained run - can
        # answer for actor "a" and nothing else, and handing a character an actor id the clip does
        # not carry fails at render time inside Blender rather than here.
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
        # The camera has to be re-solved, not inherited. The preset planner aims at one subject
        # standing at the origin; a mocap clip moves the pair, so an inherited camera lets a
        # character walk through the near plane, which degenerates the depth pass and kills
        # postprocess on a NaN. Measured on cmu_20_21_02, which travels 2.2 m.
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
        # Decide on the measurement, not on the solve's own target. They disagree, and the gap is
        # the whole problem: one camera covering everywhere the actors ever go is aimed at the
        # middle of the path, so at the ends of it the bodies are further away and smaller than the
        # solve nominally asked for. On cmu_16_36 in a square frame the solve reports 0.33 and the
        # first frame actually delivers 0.26. Deciding on the number that gets reported means the
        # planner cannot claim a shot is legible and stage an illegible one.
        worst = _worst_body_fraction(doc, keyframes, **measure)
        track_note = ""
        # Below the cliff the pose skeleton is ignored, so staging from a real take has bought
        # nothing. Track instead: keyframe the camera on where the bodies are, holding the azimuth
        # so the shot keeps one look. Measured over the 57-clip library at 0.62 target height - no
        # clip needs this in 16:9, and 30 of 57 do in 9:16, because a portrait frame is narrow
        # enough that holding two bodies apart pushes the camera back on its own.
        if _under_cliff(worst):
            # The anchor frames are the ones handed to the image model, so they are the ones that
            # have to be legible. The ends are added because a camera keyframed only at frame 0
            # holds still while the bodies walk away from it - and towards the near plane, which is
            # the NaN this module already exists to prevent. The whole cast is tracked together,
            # never one half of a pair, or the partner leaves frame.
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
            # Only if it is actually better. Tracking is the right move for a pair that walks and
            # no help at all for a pair that stands too far apart to fit the frame, and in that
            # second case a moving camera would be churn dressed up as a fix.
            if improved > worst:
                keyframes, worst = candidate, improved
                track_note = f", tracking {len(actors)} over {len(keyframes)} keyframes"
        camera = spec.camera.model_copy(update={"keyframes": keyframes})
        # The clip is what the shot is *of*, so it names the action. What the retrieval measured —
        # the clip id, the solved body fraction, whether the camera tracks — moves to
        # ``staging_note``, which no prompt reads: an image model handed "staged from
        # cmu_20_21_02, framed at 0.62 body height" draws whatever it makes of that.
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
    """A camera keyframe from a solved framing. The focus distance is the solved distance, so
    depth of field lands on the subject rather than on whatever the preset last guessed."""
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
    """The smallest the cast gets at any of ``sample_frames``, as a fraction of frame height.

    Sampling the keyframes alone is not enough, and a render proved it: a static camera solved for
    everywhere a pair walks reported 0.42 at frame 0 and the rendered boxes fell to 0.216 by the
    end of the shot, because the pair had walked away from a camera that never moved. So the ends
    and the anchors are sampled too, whether or not a keyframe sits on them.

    Each sample takes the camera from the last keyframe at or before it, which is exact where it
    matters: a static camera holds one position for every frame, and a tracking camera is
    keyframed on the anchors and the ends, so those samples land on keyframes.
    """
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
    """``(shot_id, worst_body_fraction)`` for staged shots whose subject is under the cliff.

    Measured off the finished plan rather than predicted, so it holds for any planner. Empty is
    the good answer. A shot in here will render its control passes and be ignored by the image
    model, which is worth knowing before paying for the retarget: the usual cause is a portrait
    frame too narrow to hold two bodies apart at a legible size, and the fix is a wider frame or a
    clip where the pair stands closer, neither of which a camera solve can do anything about.
    """
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
            # placed. This is the preset planner's shots, and they need checking most: their camera
            # distances are fixed multiples of subject height with no aspect in the calculation, so
            # a vertical frame puts every one of them under the cliff.
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
