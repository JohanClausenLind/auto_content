"""The lanes with no voice in them, end to end on mocks — and the held cut nothing may smooth.

Three lanes exist for films that carry no speech and none of them could finish a run:

* `silent-video` declared a caveat saying so. `mix_audio` built its bed on a narration stem read
  from per-beat segment files that only `voice_over` or `synthesize_narration` write, so a lane
  with no voice stage died inside the mix with the music already chosen and the foley already
  generated.
* `photo-sequence-video` had no picture at all. Its frames are approved PNGs under
  `sequence/frames`; there is no `generate_video` in it and no `interpolate` either, on purpose,
  and `compose_video` reached `_silent_picture`, found no mp4 and raised.
* `compose_video`'s plain path muxed `audio/narration-mastered.wav` unconditionally and then read
  narration segments to size the speech QC — a check that means nothing for a music bed.

And separately: `generate_video`'s `motion: hold` exists to guarantee that every frame on screen is
a drawing a person approved. `interpolate` ran rife over it anyway and invented frames nobody drew.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from content_factory.config import get_settings
from content_factory.runners.local import LocalRunError, run_workflow
from content_factory.schemas.dag import Stage
from content_factory.schemas.review import FrameReviewBatch

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch):
    """Mocks everywhere a model would otherwise be loaded. Nothing here touches the GPU."""
    monkeypatch.setenv("CF__IMAGE_SEQUENCES__BACKEND", "mock")
    monkeypatch.setenv("CF__VIDEO__BACKEND", "mock")
    monkeypatch.setenv("CF__CONTROLS__COMPILER", "motion_plan")
    monkeypatch.setenv("CF__SHOTS__PLANNER", "story_presets")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _accept_every_frame(project_dir: Path, deliverable: str) -> int:
    """What ``content-factory frames review --accept-all`` writes, without the CLI.

    The gate is a real gate: a verdict binds to the digests of the exact images reviewed. A test
    that wants to run past it has to record one, the same way a person does.
    """
    base = project_dir / "deliverables" / deliverable / "reviews" / "frames"
    batch = FrameReviewBatch.model_validate_json((base / "batch.json").read_text())
    decided = batch.model_copy(
        update={
            "reviewer": "operator",
            "reviewed_at": dt.datetime.now(dt.UTC),
            "frames": tuple(f.model_copy(update={"verdict": "accept"}) for f in batch.frames),
        }
    )
    (base / "verdict.json").write_text(decided.model_dump_json(indent=1))
    return len(decided.frames)


def test_silent_video_runs_end_to_end_with_a_bed_and_no_voice(tmp_path: Path) -> None:
    project = tmp_path / "silent"
    report = run_workflow(
        "silent-video",
        project_dir=project,
        # Two beats rather than six: this test's subject is the audio path, not the render count.
        story="fixtures/story/love_story_reel.json",
        subject="a quiet harbour at first light",
        params={
            # The lane pins mmaudio, which is right for a film and needs a GPU. The deterministic
            # SFX stand-in writes the same bed shape.
            Stage.sound_design: {"backend": "mock"},
            # rife needs the postchain skill env. `none` is a shipped option, not a test hack:
            # smoothing a slow silent piece is a choice, and this run does not make it.
            Stage.interpolate: {"engine": "none"},
        },
        log=lambda _m: None,
    )
    assert report["passed"]
    by = {s["stage"]: s["facts"] for s in report["stages"]}
    deliverable = report["deliverable_id"]
    ddir = project / "deliverables" / deliverable

    # The mix ran with no narration at all: a music base, the effects bed on top, mastered.
    assert by["mix_audio"]["narrated"] is False
    assert by["mix_audio"]["seconds"] > 0
    assert (ddir / "audio" / "narration-mastered.wav").exists()
    assert not (ddir / "audio" / "narration-stem.wav").exists()  # nothing spoken, nothing stemmed
    assert not list((ddir / "audio").glob("*.segment.json"))

    # The cut carries that bed and says it is not narrated.
    compose = json.loads((ddir / "exports" / "compose.json").read_text())
    assert compose["narrated"] is False and compose["audio"] == "bed"
    assert by["compose_video"]["narrated"] is False
    # No speech QC facts, because there is no speech to check.
    assert "integrated_lufs" not in by["compose_video"]

    from content_factory.qc.media import ffprobe

    streams = {s["codec_type"] for s in ffprobe(ddir / "exports" / "final.mp4")["streams"]}
    assert streams == {"video", "audio"}


def test_a_lane_with_no_sound_stages_at_all_writes_a_video_only_cut(tmp_path: Path) -> None:
    """The other direction of "the sound is optional": drop the sound stages and the cut comes out
    with no audio stream, rather than with a silent one for the delivery checks to measure."""
    from content_factory.qc.media import ffprobe

    project = tmp_path / "mute"
    report = run_workflow(
        "silent-video",
        project_dir=project,
        story="fixtures/story/love_story_reel.json",
        subject="a quiet harbour at first light",
        until_stage="smooth",
        params={Stage.interpolate: {"engine": "none"}},
        log=lambda _m: None,
    )
    assert report["passed"]
    ddir = project / "deliverables" / report["deliverable_id"]
    # The cut has not run, so nothing may be sitting at the deliverable's name yet.
    # `exports/final.mp4` used to be where the post chain concatenated its clips as well, so a run
    # that died before the cut left thirty seconds of silent, uncaptioned footage under the one
    # filename every consumer reads — this audit took it for the film (`silent-video`,
    # 2026-09-10). The post chain writes `postchain.mp4` now; with `engine: none` and no chain on
    # disk it writes neither, which is also fine. What must not exist is `final.mp4`.
    assert not (ddir / "exports" / "final.mp4").exists()
    from content_factory.runners.local import make_context, run_stages

    ctx = make_context(project_dir=project, brief={"topic": "a quiet harbour at first light"})
    out = run_stages([Stage.compose_video], ctx, log=lambda _m: None)
    assert out["passed"]
    compose = json.loads((ddir / "exports" / "compose.json").read_text())
    assert compose["narrated"] is False and compose["audio"] == "none"
    streams = {s["codec_type"] for s in ffprobe(ddir / "exports" / "final.mp4")["streams"]}
    assert streams == {"video"}


_BEATS_IN_REEL = json.loads(Path("fixtures/story/love_story_reel.json").read_text())["beats"]
"""The lane draws one picture per beat, so the fixture decides the count rather than this file."""


def test_photo_sequence_video_gets_a_picture_and_finishes(tmp_path: Path) -> None:
    """The lane's frames ARE the picture. Before this, compose_video raised "no picture yet"."""
    project = tmp_path / "sequence"
    anchor_params = {Stage.generate_anchor: {"backend": "mock"}}

    # The frame-review gate is a real gate, so the run is two passes with a person in between.
    with pytest.raises(LocalRunError) as blocked:
        run_workflow(
            "photo-sequence-video",
            project_dir=project,
            story="fixtures/story/love_story_reel.json",
            subject="a quiet harbour at first light",
            params=anchor_params,
            log=lambda _m: None,
        )
    assert blocked.value.stage is Stage.review_frames
    deliverable = blocked.value.report["deliverable_id"]
    # Two, not one: `review_frames` gates the frames its lane actually wired into it. This
    # lane's `drift -> frames_gate` wire carries the keyframes under `sequence/frames`, which are
    # also the compose_video cuts; the anchor manifest is the fallback for the Blender/scene
    # lanes, whose per-shot anchors are their frames.
    #
    # Two because `love_story_reel` has two beats. The count used to be eight for every story ever
    # given to this lane -- the frame count came from the builtin motion plan, and so did the edit
    # instruction for each frame ("move hands to the plotted position"), so the lane drew the same
    # eight pictures of the fixture's subject whatever it was asked for.
    assert _accept_every_frame(project, deliverable) == len(_BEATS_IN_REEL)

    report = run_workflow(
        "photo-sequence-video",
        project_dir=project,
        story="fixtures/story/love_story_reel.json",
        subject="a quiet harbour at first light",
        from_stage="frames_gate",
        params=anchor_params,
        log=lambda _m: None,
    )
    assert report["passed"]
    ddir = project / "deliverables" / deliverable
    picture = ddir / "exports" / "generated.mp4"
    assert picture.exists(), "the approved PNGs are cut into a picture"
    compose = json.loads((ddir / "exports" / "compose.json").read_text())
    assert compose["narrated"] is False and compose["audio"] == "none"

    from content_factory.qc.media import ffprobe

    info = ffprobe(ddir / "exports" / "final.mp4")
    assert {s["codec_type"] for s in info["streams"]} == {"video"}
    # One drawing per beat, each held for that beat's own planned length. It used to be cut at the
    # flipbook rate instead — eight frames a second — so a five-beat story planned at eighteen
    # seconds came out as a 0.6-second film.
    planned = sum(b["planned_duration_ms"] for b in _BEATS_IN_REEL) / 1000
    assert abs(float(info["format"]["duration"]) - planned) < 0.2


def test_a_held_cut_is_never_interpolated_even_when_the_lane_asks_for_rife(
    tmp_path: Path,
) -> None:
    """`motion: hold` guarantees every frame on screen is a drawing that was approved. rife over it
    invents frames nobody drew and nobody reviewed — silently, because it runs happily on a concat
    of stills. So the hold marker wins over the engine widget."""
    from content_factory.runners.local import make_context, run_stages
    from content_factory.workflows.stages import stage_interpolate

    project = tmp_path / "held"
    report = run_workflow(
        "silent-video",
        project_dir=project,
        story="fixtures/story/love_story_reel.json",
        subject="a quiet harbour at first light",
        until_stage="motion",
        params={Stage.generate_video: {"motion": "hold"}},
        log=lambda _m: None,
    )
    assert report["passed"]
    ddir = project / "deliverables" / report["deliverable_id"]
    assert (ddir / "exports" / "held.done.json").exists()

    ctx = make_context(project_dir=project, brief={"topic": "a quiet harbour at first light"})
    object.__setattr__(ctx, "params", {"engine": "rife", "factor": "2x"})
    out = stage_interpolate(ctx)
    assert out.facts["skipped"] == "held cut" and out.facts["engine"] == "none"
    assert out.facts["requested_engine"] == "rife"
    assert out.facts["clips"] == 0
    # Nothing was smoothed, so there is no interpolated frame anywhere under the run.
    assert not list(ddir.rglob("interpolated/frames/*.png"))
    assert not list(ddir.rglob("interpolated"))
    # And the run still has its picture: the held cut is the final picture.
    assert out.facts["picture"] == "generated.mp4"
    assert run_stages([Stage.compose_video], ctx, log=lambda _m: None)["passed"]
