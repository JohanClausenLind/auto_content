"""Shot text to model prompts: deterministic clause tables, one compiler, one version."""

from __future__ import annotations

from content_factory.schemas.scenes import StoryPlan
from content_factory.schemas.shots import (
    CameraPreset,
    CharacterSpec,
    EnvironmentSpec,
    ShotSpec,
)
from content_factory.style.editorial import video_prompt

PROMPT_COMPILER_VERSION = "0.1.0"
"""Bumped whenever a table below changes wording. It rides in the clip ``input_hash``, so a
reworded clause regenerates the clips it changes and nothing else."""

DEFAULT_VISUAL_SUBJECT = "an unadorned figure study"
"""What a story that never named its world gets. Deliberately dull rather than invented: the
planner must not make up a setting the operator did not ask for."""

FRAMING_CLAUSE: dict[CameraPreset, str] = {
    CameraPreset.static: "a locked-off medium wide shot",
    CameraPreset.slow_push_in: "a wide shot, the subject small and centred",
    CameraPreset.slow_pull_out: "a close shot, the subject filling the frame",
    CameraPreset.pan_left: "a medium shot from the subject's right",
    CameraPreset.pan_right: "a medium shot from the subject's left",
    CameraPreset.orbit_left: "a three-quarter medium shot from the front right",
    CameraPreset.orbit_right: "a three-quarter medium shot from the front left",
    CameraPreset.crane_up: "a low medium wide shot from near ground level",
    CameraPreset.crane_down: "a high medium wide shot looking down",
}
"""The first frame of each preset, which is the frame the anchor is generated for. A push-in opens
wide and a pull-out opens close, so the state sentence has to be read off the *start* of the move
rather than off the preset's name."""

END_FRAMING_CLAUSE: dict[CameraPreset, str] = {
    CameraPreset.static: "the framing is unchanged",
    CameraPreset.slow_push_in: "the framing has closed to a medium shot",
    CameraPreset.slow_pull_out: "the framing has opened to a wide shot",
    CameraPreset.pan_left: "the framing has arrived on the subject's left",
    CameraPreset.pan_right: "the framing has arrived on the subject's right",
    CameraPreset.orbit_left: "the view has come round to the front left",
    CameraPreset.orbit_right: "the view has come round to the front right",
    CameraPreset.crane_up: "the view is now above the subject",
    CameraPreset.crane_down: "the view is now level with the ground",
}

CAMERA_MOVE_CLAUSE: dict[CameraPreset, str] = {
    CameraPreset.static: "the camera is locked off and does not move",
    CameraPreset.slow_push_in: "the camera pushes slowly in",
    CameraPreset.slow_pull_out: "the camera pulls slowly out",
    CameraPreset.pan_left: "the camera pans steadily to the left",
    CameraPreset.pan_right: "the camera pans steadily to the right",
    CameraPreset.orbit_left: "the camera orbits slowly left around the subject",
    CameraPreset.orbit_right: "the camera orbits slowly right around the subject",
    CameraPreset.crane_up: "the camera cranes slowly upward",
    CameraPreset.crane_down: "the camera cranes slowly downward",
}

LIGHTING_CLAUSE: dict[str, str] = {
    "studio": "even studio light from a single soft key, shadows contained",
    "exterior_day": "flat overcast daylight, no hard shadows",
    "exterior_dusk": "low warm dusk light raking across the scene, long shadows",
    "interior_warm": "warm interior light from a practical source out of frame",
}

HELD_ACTION = "the subject holds the pose without moving"
"""The honest default. The preset planner has no action to state — a story beat carries narration,
not staging — so a shot says it is still rather than borrowing a sentence written to be spoken."""


def _clean(text: str) -> str:
    return " ".join(text.split()).rstrip(" .")


def subject_clause(characters: tuple[CharacterSpec, ...]) -> str:
    """The figures in frame, and how they look when the plan says."""
    if not characters:
        return "no figures"
    described = [_clean(c.appearance) for c in characters if c.appearance]
    counted = {1: "one figure", 2: "two figures"}.get(len(characters))
    head = counted or f"{len(characters)} figures"
    if not described:
        return head
    return f"{head}, {', '.join(described)}"


def environment_clause(environment: EnvironmentSpec, *, has_figures: bool = True) -> str:
    """Where the shot is, from the staged geometry rather than from a guess."""
    if environment.walls is not None:
        return "inside a bare room with plain walls"
    if not has_figures:
        return ""
    if environment.ground.enabled:
        return "standing on open level ground"
    return "against an empty backdrop with no floor"


def state_sentence(
    *,
    preset: CameraPreset,
    lighting_preset: str,
    environment: EnvironmentSpec,
    characters: tuple[CharacterSpec, ...] = (),
    visual_subject: str | None = None,
) -> str:
    """One instant: what the anchor frame looks like, standing still."""
    # "of no figures" is a sentence about an absence, and an image model draws what a prompt names.
    parts = [
        FRAMING_CLAUSE[preset],
        f"of {subject_clause(characters)}" if characters else "",
        _clean(visual_subject or DEFAULT_VISUAL_SUBJECT),
        environment_clause(environment, has_figures=bool(characters)),
        LIGHTING_CLAUSE.get(lighting_preset, LIGHTING_CLAUSE["studio"]),
    ]
    return ", ".join(_clean(p) for p in parts if _clean(p)) + "."


def progression_sentence(
    *, preset: CameraPreset, action: str = "", end_state: str | None = None
) -> str:
    """What changes between the first frame and the last, camera included."""
    clauses = [_clean(action) or HELD_ACTION, CAMERA_MOVE_CLAUSE[preset]]
    if end_state:
        clauses.append(_clean(end_state))
    return "; ".join(clauses) + "."


def compile_video_prompt(shot: ShotSpec, story: StoryPlan | None = None) -> str:
    """The seven-component cinematography paragraph for one shot."""
    preset = shot.camera.preset
    props = (
        "nothing else in the frame moves"
        if not shot.props
        else f"the {len(shot.props)} staged props stay where they are"
    )
    return video_prompt(
        action=_clean(shot.action) or _clean(shot.motion_prompt) or HELD_ACTION,
        object_motion=props,
        appearance=subject_clause(shot.characters),
        environment=(
            f"{_clean((story.visual_subject if story else None) or DEFAULT_VISUAL_SUBJECT)}, "
            f"{environment_clause(shot.environment)}"
        ),
        camera=CAMERA_MOVE_CLAUSE[preset],
        lighting=LIGHTING_CLAUSE.get(shot.lighting.preset, LIGHTING_CLAUSE["studio"]),
        end_state=_clean(shot.end_state) if shot.end_state else END_FRAMING_CLAUSE[preset],
    )
