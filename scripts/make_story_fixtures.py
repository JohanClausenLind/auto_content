"""Generate the shot plan and the script for a two-hander film, from a scenario.

    uv run python scripts/make_story_fixtures.py            # every scenario
    uv run python scripts/make_story_fixtures.py love_story # just one

A scenario is data: who is in it, how far apart they start and end, what the camera does, what is
said and for how long. Everything else — the gait, the reach, the camera arc, the shot ids — is
arithmetic, so a new film is a dozen lines here rather than a hand-authored 30-shot JSON.

Why the poses are authored rather than prompted: the *only* thing that keeps a generated character
walking the same way from drawing to drawing is a skeleton that came from somewhere real. Each shot
poses the two MPFB rigs explicitly (bone names verified against the baked ``t_pose.json``), Blender
renders the OpenPose skeleton and the layout boxes, and HiDream draws that exact stance. The gait is
arithmetic, not luck.

Axes, read off the baked ``t_pose.json`` rather than guessed: on the MPFB default rig an arm
raises and lowers about the bone's **local Z** (that pose is +30 deg Z on ``upperarm01``, mirrored),
and a limb swings forwards and backwards about **local X** (its elbow bend is -37 deg X). The rig's
rest pose already holds the arms out from the body, so every pose here starts by bringing them down
and composes the swing on top — quaternion multiplication, not replacement.

Sign convention: if the first render walks backwards, flip ``SWING_SIGN`` — one constant, one
re-render (the whole 30-shot control pass takes about 90 seconds), no re-prompting.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from content_factory.schemas.scenes import ImageScene, StoryPlan, TextRef, VisualBeat
from content_factory.schemas.sequences import ControlKind
from content_factory.schemas.shots import (
    BonePose,
    BoneRotation,
    CameraKeyframe,
    CameraPreset,
    CameraSpec,
    CharacterSpec,
    EnvironmentSpec,
    GroundSpec,
    LightingSpec,
    RenderSpec,
    ShotPlan,
    ShotSpec,
    Transform,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

FPS = 24
SHOT_FRAMES = 49  # 2.04 s; LTX-2.5 generates 8k+1 frames
WIDTH, HEIGHT = 1024, 576
SWING_SIGN = 1.0
SENSOR_MM = 36.0  # CameraSpec.sensor_width_mm default
BODY_MARGIN_M = 1.3  # two bodies seen from above are about this wide in total
# A person's OVERHEAD footprint, which is what a camera looking straight down actually sees — not
# their height. Getting this wrong is the mistake that made every shot in this film wrong: the rule
# used 1.9 m, a person's head-to-foot length, which is the dimension you see from the *side*. From
# above, Blender's own layout boxes measure 0.56-0.72 m front-to-back (0.46-1.05 m across, wider
# when the arms are extended). Sizing an overhead frame with a side-on constant made every camera
# about 3.2x too high, so the figures came out a third of the size intended, and the image model
# treated skeletons that small as scene decoration rather than as the subject.
BODY_PLAN_M = 0.75  # 0.72 m measured at its widest, plus a little headroom
# The floor under how small a person may be drawn, as a fraction of the frame's short side.
MIN_BODY_FRACTION = 0.22

# Only the keyframe of each shot is rendered and drawn: one Blender frame and one HiDream image per
# shot. LTX-2.5 animates the two seconds that follow each still.
PASSES = (
    ControlKind.rough_rgb,
    ControlKind.depth,
    ControlKind.normals,
    ControlKind.segmentation,
    ControlKind.pose_skeleton,
    ControlKind.layout_boxes,
)


# --- pose arithmetic ----------------------------------------------------------------------------


Quat = tuple[float, float, float, float]
Vec3 = tuple[float, float, float]


def qx(deg: float) -> Quat:
    """Rotation about the bone's local X: limb swing, elbow bend."""
    half = math.radians(deg) / 2.0
    return (math.cos(half), math.sin(half), 0.0, 0.0)


def qz(deg: float) -> Quat:
    """Rotation about the bone's local Z: raising and lowering an arm."""
    half = math.radians(deg) / 2.0
    return (math.cos(half), 0.0, 0.0, math.sin(half))


def qmul(a: Quat, b: Quat) -> Quat:
    """Hamilton product: ``a`` then ``b`` in the bone's own frame."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def bone(q: Quat) -> BoneRotation:
    return BoneRotation(rotation_quaternion=tuple(round(c, 6) for c in q))  # type: ignore[arg-type]


# No arms-down correction: the MPFB rest pose already hangs the arms at the sides. An earlier
# version rotated them 80 deg about local Z to "bring them down", diagnosed from a top-down clay
# render where foreshortening made the shoulders look splayed. Seen in profile the rest pose is
# plainly correct, and that rotation was what pushed every figure into a starfish; composing the
# reach after it also sent the arms backwards. Rest is the baseline; only the swing is applied.


def rot_x(deg: float) -> BoneRotation:
    return bone(qx(deg))


def _arms(swing_l_deg: float, swing_r_deg: float, elbow_deg: float) -> dict[str, BoneRotation]:
    """Arms swung from their resting position about local X. Positive is forwards, verified in a
    profile render: +75 deg reaches ahead of the body, -75 deg reaches behind it."""
    return {
        "upperarm01.L": rot_x(swing_l_deg),
        "upperarm01.R": rot_x(swing_r_deg),
        "lowerarm01.L": rot_x(elbow_deg),
        "lowerarm01.R": rot_x(elbow_deg),
    }


def stride(
    phase: float, *, leg_deg: float = 26.0, arm_deg: float = 18.0
) -> dict[str, BoneRotation]:
    """One frame of a walk: legs swing in antiphase, arms counter-swing, the recovery knee bends.
    ``phase`` is the position in the gait cycle in [0, 1)."""
    s = SWING_SIGN * math.sin(2 * math.pi * phase)
    return {
        "upperleg01.L": rot_x(leg_deg * s),
        "upperleg01.R": rot_x(-leg_deg * s),
        "upperleg02.L": rot_x(-max(0.0, -s) * 34.0),
        "upperleg02.R": rot_x(-max(0.0, s) * 34.0),
        **_arms(-arm_deg * s, arm_deg * s, -12.0),
    }


def standing() -> dict[str, BoneRotation]:
    """The rest pose is a person standing with their arms down; only a slight elbow break is
    needed to stop it reading as a mannequin."""
    return _arms(0.0, 0.0, -8.0)


def reaching(amount: float) -> dict[str, BoneRotation]:
    """Both arms lifting towards the other person; ``amount`` in [0, 1] is how far the reach has
    travelled — 1.0 is hands meeting at chest height."""
    lift = 75.0 * amount
    return {
        # Elbows stay slightly broken through the reach so the arms do not read as poles.
        **_arms(lift, lift, -10.0 - 8.0 * amount),
    }


def lerp(a: float, b: float, t: float) -> float:
    return round(a + (b - a) * t, 4)


def orbit_camera(index: int, count: int, subject_z: float = 0.95) -> tuple[Vec3, Vec3, float]:
    """(position, look_at, lens) for shot ``index`` of an orbiting camera.

    Azimuth sweeps most of a circle so consecutive shots are genuinely different angles; elevation,
    distance and lens each move on their own cycle so the sweep does not read as one mechanical
    dolly. The look-at stays on the subject's chest, which is what keeps thirty different cameras
    reading as thirty views of one thing rather than thirty unrelated pictures.
    """
    t = index / max(1, count - 1)
    azimuth = math.radians(-150.0 + 300.0 * t)
    elevation = math.radians(6.0 + 26.0 * (0.5 - 0.5 * math.cos(2 * math.pi * t * 1.5)))
    distance = 3.2 + 3.6 * (0.5 - 0.5 * math.cos(2 * math.pi * t * 0.8))
    lens = 32.0 + 46.0 * (0.5 - 0.5 * math.cos(2 * math.pi * t * 1.2))
    horizontal = distance * math.cos(elevation)
    position = (
        round(horizontal * math.sin(azimuth), 4),
        round(-horizontal * math.cos(azimuth), 4),
        round(subject_z + distance * math.sin(elevation), 4),
    )
    return position, (0.0, 0.0, subject_z), round(lens, 2)


def camera_height(gap: float, fill: float, lens_mm: float) -> float:
    """How high a straight-down camera must sit to hold both people at ``fill`` of the frame.

    A camera at height h with focal length f sees a strip ``SENSOR_MM * h / f`` metres wide. Two
    constraints, and the looser one wins: the pair plus a body's width has to fit across the frame,
    and a body seen end-on from above is nearly two metres long, so it has to fit *down* the frame
    too — the check that a width-only rule misses, which is how a close shot ends up beheading both
    of them.
    """
    fill = max(0.05, fill)
    across = (gap + BODY_MARGIN_M) / fill
    down = (BODY_PLAN_M / fill) * (WIDTH / HEIGHT)
    return round(max(1.6, max(across, down) * lens_mm / SENSOR_MM), 3)


def max_height_for_readable_bodies(lens_mm: float) -> float:
    """The highest the camera may sit before a person is too small for the skeleton to be read."""
    return round(BODY_PLAN_M * lens_mm * (WIDTH / HEIGHT) / (SENSOR_MM * MIN_BODY_FRACTION), 3)


# --- scenarios ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Act:
    """A stretch of shots with one behaviour. ``kind`` selects the pose program."""

    kind: str  # walk | stand | reach
    shots: int
    gap_from: float  # metres between the two people at the first shot of the act
    gap_to: float
    # How much of the frame width the pair should span. The camera height follows from this and
    # the gap, so a shot cannot accidentally push its subjects off the edge: 0.5 leaves the plaza
    # around them, 0.85 is close enough that the hands are the subject.
    fill: float
    description: str
    motion: str


@dataclass(frozen=True)
class Scenario:
    slug: str
    # One sentence naming the world. It leads the anchor prompt, ahead of the per-shot staging.
    # Without it the anchor stage falls back to the *campaign's* brief topic, which for a local
    # run is the fixture campaign — every drawing of this film was being asked for over the top of
    # "How much of Sweden's electricity came from wind in 2025?".
    subject: str
    # Short code used in ids. It ends up in the take filenames the person recording sees, so
    # "love_04.wav" beats "bea_love_svoice04.wav".
    code: str
    title: str
    shots_name: str
    story_name: str
    characters: tuple[tuple[str, str], tuple[str, str]]  # (id, asset) x2
    acts: tuple[Act, ...]
    lines: tuple[tuple[str, int], ...]  # (spoken line, planned ms)
    lighting: LightingSpec
    ground: GroundSpec
    background: tuple[float, float, float]
    lens_from: float = 28.0
    lens_to: float = 55.0
    caption_line: int = 0
    alt_text: str = "Overhead: two people and the distance between them."
    seed_base: int = 1000
    reach_program: str = "together"  # together | apart
    # topdown: the overhead formula the two-handers use. orbit: a camera that moves around the
    # subject, changing azimuth, elevation, distance and lens every shot. Thirty near-identical
    # cameras score nothing on variety, and a running figure has no reason to be seen from above.
    camera_program: str = "topdown"
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def shots(self) -> int:
        return sum(a.shots for a in self.acts)


def _reel(base: Scenario, shots_per_act: tuple[int, ...]) -> Scenario:
    """A short cut of a film for comparing art directions: the same acts, the same staging
    arithmetic, fewer shots each. Eight drawings still tell approach → stop → hands, and at ~5.5
    minutes a drawing that is 45 minutes per style instead of nearly three hours."""
    from dataclasses import replace

    acts = tuple(replace(act, shots=n) for act, n in zip(base.acts, shots_per_act, strict=True))
    return replace(
        base,
        slug=f"{base.slug}_reel",
        code=f"{base.code}r",
        shots_name=f"{base.shots_name}_reel",
        story_name=f"{base.story_name}_reel",
        acts=acts,
        lines=base.lines[:2],
    )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        slug="love_story",
        subject=(
            "Two people cross a rain-wet stone plaza at dusk in a northern old town, "
            "and their hands meet."
        ),
        code="love",
        title="Hand-drawn love story",
        shots_name="love_story_topdown",
        story_name="love_story",
        characters=(("man", "man_01"), ("woman", "woman_01")),
        acts=(
            Act(
                "walk",
                16,
                3.0,
                1.6,
                0.72,
                (
                    "Straight down on a rain-wet stone plaza at dusk. Two people, far apart, "
                    "walking towards each other."
                ),
                (
                    "seen from directly above, both figures keep walking steadily towards each "
                    "other, long shadows sliding across wet stone"
                ),
            ),
            Act(
                "stand",
                6,
                1.9,
                1.4,
                0.62,
                "Straight down. The two have stopped an arm's length apart, facing each other.",
                (
                    "seen from directly above, both figures stand still and breathe, only clothes "
                    "and hair moving in the wind"
                ),
            ),
            Act(
                "reach",
                8,
                1.3,
                1.3,
                0.84,
                "Straight down, close. Two hands rising towards each other, then holding.",
                (
                    "seen from directly above, the two hands rise slowly towards each other and "
                    "close into a hold, fingers interlacing"
                ),
            ),
        ),
        lines=(
            ("I saw you before I knew you.", 6000),
            ("Every evening, the same square. The same twelve steps.", 9000),
            ("Tonight I did not walk past.", 6000),
            ("Hey. You're so beautiful.", 5000),
            ("You didn't say anything. You just held out your hand.", 9000),
            ("And I took it.", 5000),
        ),
        lighting=LightingSpec(
            preset="exterior_dusk", key_azimuth_deg=210.0, key_elevation_deg=18.0
        ),
        ground=GroundSpec(enabled=True, size=60.0, color=(0.29, 0.30, 0.33)),
        background=(0.06, 0.07, 0.10),
        caption_line=3,
        alt_text="Overhead: two people crossing a plaza towards each other at dusk.",
    ),
    Scenario(
        slug="last_train",
        subject=("Two people on an empty railway platform late at night under sodium light."),
        code="train",
        title="The last train",
        shots_name="last_train_topdown",
        story_name="last_train",
        characters=(("waiting", "woman_02"), ("arriving", "man_02")),
        acts=(
            Act(
                "stand",
                6,
                2.9,
                2.9,
                0.70,
                (
                    "Straight down on an empty platform under sodium light. One person stands "
                    "still; the other is far up the platform."
                ),
                (
                    "seen from directly above, one figure waits without moving as light flickers, "
                    "the other is a distant shape"
                ),
            ),
            Act(
                "walk",
                14,
                2.9,
                1.4,
                0.72,
                (
                    "Straight down. One walks the length of the platform; the other turns towards "
                    "them."
                ),
                (
                    "seen from directly above, one figure walks the length of the platform, the "
                    "other turns to face them"
                ),
            ),
            Act(
                "reach",
                6,
                1.5,
                1.3,
                0.82,
                "Straight down, close. Hands find each other before either of them speaks.",
                "seen from directly above, two hands find each other, then hold",
            ),
        ),
        lines=(
            ("The last train was at eleven.", 5000),
            ("I told myself I would not wait.", 6000),
            ("I waited.", 4000),
            ("You came up the platform without hurrying.", 7000),
            ("Like you already knew I would still be there.", 7000),
        ),
        lighting=LightingSpec(preset="interior_warm", key_azimuth_deg=95.0, key_elevation_deg=70.0),
        ground=GroundSpec(enabled=True, size=80.0, color=(0.24, 0.23, 0.22)),
        background=(0.04, 0.04, 0.05),
        lens_from=24.0,
        lens_to=48.0,
        caption_line=2,
        alt_text=(
            "Overhead: an empty station platform, one person waiting, one walking towards them."
        ),
        seed_base=2000,
    ),
    Scenario(
        slug="letting_go",
        subject=("Two people on a wide tidal beach at midday, letting go of each other's hands."),
        code="sand",
        title="Letting go",
        shots_name="letting_go_topdown",
        story_name="letting_go",
        characters=(("one", "woman_01"), ("other", "man_01")),
        acts=(
            Act(
                "reach",
                6,
                1.3,
                1.3,
                0.86,
                "Straight down, close, on pale sand. Two hands still holding.",
                "seen from directly above, two joined hands stay still, fingers loosening a little",
            ),
            Act(
                "stand",
                4,
                1.35,
                1.9,
                0.66,
                "Straight down. They have let go and are standing apart.",
                (
                    "seen from directly above, the two figures stand apart, hands falling to "
                    "their sides"
                ),
            ),
            Act(
                "walk",
                14,
                1.8,
                2.6,
                0.68,
                "Straight down on a wide beach at midday. Two people walking away from each other.",
                (
                    "seen from directly above, the two figures walk steadily away from each "
                    "other, footprints filling with water"
                ),
            ),
        ),
        lines=(
            ("We stayed like that longer than we needed to.", 7000),
            ("Neither of us wanted to be the one who moved first.", 8000),
            ("So we both did.", 4000),
            ("The tide took the footprints before I reached the road.", 8000),
        ),
        lighting=LightingSpec(preset="exterior_day", key_azimuth_deg=35.0, key_elevation_deg=75.0),
        ground=GroundSpec(enabled=True, size=90.0, color=(0.72, 0.68, 0.58)),
        background=(0.78, 0.82, 0.85),
        lens_from=45.0,
        lens_to=24.0,
        caption_line=2,
        alt_text=(
            "Overhead: two people on wet sand, letting go of each other's hands and walking apart."
        ),
        seed_base=3000,
        reach_program="apart",
    ),
)


def run_cycle_scenario() -> Scenario:
    """One person running, thirty shots, a different camera on each.

    Deliberately not overhead: the earlier films fought the fact that OpenPose skeletons are
    read as front-on, and a running figure has no reason to be seen from above. The cameras orbit,
    track and change height instead, which is also the only way to score anything on camera
    variety — thirty near-identical cameras is a slideshow with extra steps.
    """
    from dataclasses import replace

    base = SCENARIOS[0]
    return replace(
        base,
        slug="runner",
        code="run",
        subject=(
            # An overcast track, not the dusk wet cobbles this started as. Measured on one staged
            # shot: the dusk brief came back with 37 % of its pixels crushed under luma 12 against
            # the frame review's 35 % threshold and 26 % midtones against its 30 %, because a dark
            # street lit by practical lamps is a genuinely high-contrast subject and the model
            # grades it harder. The same style on a flat overcast track lands at 18 % crushed and
            # 46 % midtones. The brief was the tone problem, not the pipeline.
            "A lone runner on an empty concrete running track under a flat overcast morning sky, "
            "seen from a moving camera."
        ),
        title="Runner",
        shots_name="runner_varied",
        story_name="runner",
        characters=(("runner", "man_01"), ("runner_shadow", "man_02")),
        acts=(
            Act(
                "walk",
                30,
                1.9,
                1.9,
                0.62,
                "A runner mid-stride on wet stone, the city behind them.",
                "the runner keeps running at the same pace, coat and hair moving with it",
            ),
        ),
        lines=(("I ran because standing still was worse.", 6000),),
        caption_line=0,
        alt_text="A runner crossing a wet plaza at dusk, seen from a moving camera.",
        seed_base=4000,
        camera_program="orbit",
    )


# A reel of the love story for art-direction comparison: 3 walking, 2 standing, 3 reaching.
SCENARIOS = (*SCENARIOS, _reel(SCENARIOS[0], (3, 2, 3)), run_cycle_scenario())


# --- building -------------------------------------------------------------------------------------


def _acts_by_shot(scenario: Scenario) -> list[tuple[Act, float]]:
    """One (act, progress-through-act) per shot, in order."""
    out: list[tuple[Act, float]] = []
    for act in scenario.acts:
        for i in range(act.shots):
            out.append((act, i / max(1, act.shots - 1) if act.shots > 1 else 0.0))
    return out


def build_shots(scenario: Scenario) -> ShotPlan:
    shots: list[ShotSpec] = []
    schedule = _acts_by_shot(scenario)
    (id_a, asset_a), (id_b, asset_b) = scenario.characters
    for i, (act, t) in enumerate(schedule):
        gap = lerp(act.gap_from, act.gap_to, t)
        if scenario.camera_program == "orbit":
            position, look_at, lens = orbit_camera(i, scenario.shots)
            keyframes = (
                CameraKeyframe(
                    frame_index=0,
                    position=position,
                    look_at=look_at,
                    lens_mm=lens,
                    focus_distance=round(math.dist(position, look_at), 3),
                ),
            )
            shots.append(
                ShotSpec(
                    shot_id=f"sht_{scenario.code}_{i:04d}",
                    order=i,
                    beat_id=None,
                    seed=scenario.seed_base + i,
                    frame_count=SHOT_FRAMES,
                    fps=FPS,
                    width=WIDTH,
                    height=HEIGHT,
                    camera=CameraSpec(preset=CameraPreset.static, keyframes=keyframes),
                    characters=(
                        CharacterSpec(
                            id=scenario.characters[0][0],
                            asset=scenario.characters[0][1],
                            transform=Transform(position=(0.0, 0.0, 0.0), yaw_deg=90.0),
                            pose=BonePose(
                                bones=stride((i * 0.28) % 1.0, leg_deg=34.0, arm_deg=26.0)
                            ),
                            seg_id=1,
                        ),
                    ),
                    environment=EnvironmentSpec(
                        ground=scenario.ground, background_color=scenario.background
                    ),
                    lighting=scenario.lighting,
                    render=RenderSpec(passes=PASSES, frames=(0,)),
                    anchor_frames=(0,),
                    motion_prompt=act.motion,
                    description=f"{act.description} Camera {i + 1} of {scenario.shots}.",
                )
            )
            continue
        lens = lerp(scenario.lens_from, scenario.lens_to, i / max(1, scenario.shots - 1))
        height = camera_height(gap, act.fill, lens)
        ceiling = max_height_for_readable_bodies(lens)
        if height > ceiling:
            drawn = BODY_PLAN_M / (SENSOR_MM * height / lens * HEIGHT / WIDTH)
            msg = (
                f"{scenario.slug} shot {i}: framing a {gap:.1f} m gap needs the camera at "
                f"{height:.1f} m, where a person is only {drawn:.0%} of the frame — below the "
                f"{MIN_BODY_FRACTION:.0%} the image model needs to read the skeleton. Stage them "
                f"closer together or shorten the lens."
            )
            raise ValueError(msg)
        if act.kind == "walk":
            phase = (i * 0.5) % 1.0  # half a gait cycle between drawings: a readable stride change
            pose_a, pose_b = stride(phase), stride((phase + 0.5) % 1.0)
        elif act.kind == "stand":
            pose_a = pose_b = standing()
        else:
            amount = 1.0 - t if scenario.reach_program == "apart" else t
            pose_a = pose_b = reaching(round(amount, 4))
        shots.append(
            ShotSpec(
                shot_id=f"sht_{scenario.code}_{i:04d}",
                order=i,
                beat_id=None,
                seed=scenario.seed_base + i,
                frame_count=SHOT_FRAMES,
                fps=FPS,
                width=WIDTH,
                height=HEIGHT,
                camera=CameraSpec(
                    preset=CameraPreset.static,
                    keyframes=(
                        # Straight down: the camera sits above the midpoint and looks at the ground
                        # between them. A hair of drift keeps the still from feeling like a diagram.
                        CameraKeyframe(
                            frame_index=0,
                            position=(0.0, -0.15, height),
                            look_at=(0.0, 0.0, 0.9),
                            lens_mm=lens,
                            focus_distance=round(height, 3),
                        ),
                        CameraKeyframe(
                            frame_index=SHOT_FRAMES - 1,
                            position=(0.0, 0.15, round(height - 0.12, 3)),
                            look_at=(0.0, 0.0, 0.9),
                            lens_mm=lens,
                            focus_distance=round(height - 0.12, 3),
                        ),
                    ),
                ),
                characters=(
                    # Facing, verified in a profile render: yaw -90 faces -X, yaw +90 faces +X.
                    # The one standing on the -X side must therefore be yawed +90 to look at the
                    # other. These signs were swapped, so the pair stood back to back in every
                    # frame of every film and the reach extended away from the person it was for.
                    CharacterSpec(
                        id=id_a,
                        asset=asset_a,
                        transform=Transform(position=(-gap / 2, 0.0, 0.0), yaw_deg=90.0),
                        pose=BonePose(bones=pose_a),
                        seg_id=1,
                    ),
                    CharacterSpec(
                        id=id_b,
                        asset=asset_b,
                        transform=Transform(position=(gap / 2, 0.0, 0.0), yaw_deg=-90.0),
                        pose=BonePose(bones=pose_b),
                        seg_id=2,
                    ),
                ),
                environment=EnvironmentSpec(
                    ground=scenario.ground, background_color=scenario.background
                ),
                lighting=scenario.lighting,
                render=RenderSpec(passes=PASSES, frames=(0,)),
                anchor_frames=(0,),
                motion_prompt=act.motion,
                description=f"{act.description} They are {gap:.2f} m apart.",
            )
        )
    return ShotPlan(
        plan_id=f"shp_{scenario.slug}_plan"[:36],
        deliverable_id=f"dlv_{scenario.slug}_film"[:36],
        planner="fixture",
        planner_version="0.1.0",
        shots=tuple(shots),
    )


def build_story(scenario: Scenario) -> StoryPlan:
    """The narration track. The beat id is the take's filename, so whoever records reads from this
    list and saves ``<beat_id>.wav``. Planned durations hold the timeline until real takes are
    aligned, after which the measured words drive it."""
    beats = tuple(
        VisualBeat(
            beat_id=f"bea_{scenario.code}_take{i:02d}",
            order=i,
            display_text=text,
            planned_duration_ms=ms,
        )
        for i, (text, ms) in enumerate(scenario.lines)
    )
    scenes = tuple(
        ImageScene(
            scene_id=f"scn_{scenario.code}_scene{i:02d}",
            beat_id=beat.beat_id,
            asset_id=f"ast_{scenario.code}_still{i:02d}",
            alt_text=scenario.alt_text,
            caption=TextRef(text=beat.display_text) if i == scenario.caption_line else None,
            motion="none",
        )
        for i, beat in enumerate(beats)
    )
    return StoryPlan(
        plan_id=f"plan_{scenario.slug}_v1"[:36],
        deliverable_id=f"dlv_{scenario.slug}_film"[:36],
        fps=FPS,
        width=WIDTH,
        height=HEIGHT,
        beats=beats,
        scenes=scenes,
    )


def _write(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json.loads(model.model_dump_json()), indent=1, sort_keys=True) + "\n"
    )


def emit(scenario: Scenario) -> dict:
    plan = build_shots(scenario)
    story = build_story(scenario)
    shots_path = REPO_ROOT / "fixtures" / "shots" / f"{scenario.shots_name}.json"
    story_path = REPO_ROOT / "fixtures" / "story" / f"{scenario.story_name}.json"
    _write(shots_path, plan)
    _write(story_path, story)
    return {
        "scenario": scenario.slug,
        "subject": scenario.subject,
        "shots": len(plan.shots),
        "stills": sum(len(s.anchor_frames) for s in plan.shots),
        "seconds": round(len(plan.shots) * SHOT_FRAMES / FPS, 1),
        "beats": len(story.beats),
        "takes": [b.beat_id for b in story.beats],
        "shots_fixture": str(shots_path.relative_to(REPO_ROOT)),
        "story_fixture": str(story_path.relative_to(REPO_ROOT)),
    }


def main(argv: list[str]) -> int:
    wanted = argv or [s.slug for s in SCENARIOS]
    known = {s.slug: s for s in SCENARIOS}
    unknown = [w for w in wanted if w not in known]
    if unknown:
        print(f"unknown scenario(s) {unknown}; known: {sorted(known)}", file=sys.stderr)
        return 2
    for slug in wanted:
        print(json.dumps(emit(known[slug])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
