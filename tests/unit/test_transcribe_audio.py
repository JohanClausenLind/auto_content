"""A recording becomes a film: the transcript, the beats cut out of it, and the voice.

These are the pieces that let a lane start from sound instead of from something written, so what
they pin is the chain of claims each one makes to the next:

* the transcript says where every word is in the audio;
* the story's beats are spans of those words, and each beat's duration is measured, not planned;
* the per-beat narration is cut out of the one recording at those word boundaries, so the same
  contracts the synthesized path produces come out the other end — which is what lets the
  restoration chain, the captions, the mix and the mux stay exactly as they were.

The transcriber under test is ``fixture``: it takes a transcript the operator already has and
apportions it across the recording. No model is downloaded, nothing leaves the machine, and the
run is identical every time — the properties a core-suite test needs. What faster-whisper adds is
measured word boundaries, and it is exercised by hand (see STATUS), not here.
"""

from __future__ import annotations

import itertools
import json
import wave
from pathlib import Path

import pytest

from content_factory.audio.takes import TakeError
from content_factory.audio.transcribe import (
    beat_spans,
    sentences,
    split_beats,
    story_plan_from_transcript,
    transcribe,
)
from content_factory.runners.local import make_context
from content_factory.schemas.audio import NarrationSegment, SpeechTranscript, TimingSource
from content_factory.schemas.scenes import WordTiming
from content_factory.workflows.stages import (
    stage_align_words,
    stage_compile_captions,
    stage_lock_script,
    stage_plan_story,
    stage_transcribe_audio,
    stage_voice_over,
)

TALK = (
    "My grandmother kept a shop on the corner. It smelled of cardboard and oranges. "
    "Every Thursday she counted the till twice. She never once told me why. "
    "I found the second ledger after she died. It was all in her hand."
)


def _recording(path: Path, *, seconds: float = 12.0, rate: int = 24000) -> Path:
    """A PCM WAV of the right length. What it sounds like does not matter to the fixture path.

    Not silence: a file of zeros is a legitimate thing for a QC stage to complain about later, and
    a low tone keeps this test about transcription rather than about the artifact detector.
    """
    import math

    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(
            b"".join(
                int(6000 * math.sin(2 * math.pi * 110 * i / rate)).to_bytes(
                    2, "little", signed=True
                )
                for i in range(frames)
            )
        )
    return path


def _transcript(text: str = TALK, *, gap_ms: int = 350, hold_ms: int = 300) -> SpeechTranscript:
    words = []
    cursor = 0
    for word in text.split():
        words.append(WordTiming(word=word, start_ms=cursor, end_ms=cursor + hold_ms))
        cursor += gap_ms
    return SpeechTranscript(
        transcript_id="tsc_" + "a" * 16,
        audio_sha256="b" * 64,
        sample_rate_hz=24000,
        duration_ms=cursor + 500,
        engine="fixture",
        text=text,
        words=tuple(words),
        timing_source=TimingSource.asr,
    )


# --- the pure parts ---------------------------------------------------------------------------


def test_sentences_come_from_the_punctuation_the_transcriber_emitted() -> None:
    parts = sentences(_transcript())
    assert len(parts) == 6
    assert parts[0][0].startswith("My grandmother")
    # Spans are in order and never overlap.
    for before, after in itertools.pairwise(parts):
        assert before[2] <= after[1]


def test_beats_are_divided_by_where_the_speech_is_not_by_sentence_count() -> None:
    """Three beats over six evenly spaced sentences is two sentences each — and the test that
    matters is the *durations*, because a picture that holds for a twenty-second digression and one
    that holds for a three-word aside are the same amount of drawing."""
    transcript = _transcript()
    spans = split_beats(transcript, beats=3)
    assert len(spans) == 3
    lengths = [end - start for _t, start, end in spans]
    assert max(lengths) - min(lengths) <= max(lengths) * 0.25, lengths
    # Every word of the recording is still in exactly one beat, in order.
    assert " ".join(text for text, _s, _e in spans).split() == TALK.split()


def test_a_transcript_with_fewer_sentences_than_beats_is_not_invented_into_more() -> None:
    transcript = _transcript("One thing happened. Then another.")
    assert len(split_beats(transcript, beats=6)) == 2


def test_an_unpunctuated_monologue_still_gets_its_pictures() -> None:
    transcript = _transcript("so then we walked down to the water and nobody said anything at all")
    spans = split_beats(transcript, beats=3)
    assert len(spans) == 3
    assert all(end > start for _t, start, end in spans)


def test_the_plan_measures_each_beat_rather_than_planning_it() -> None:
    transcript = _transcript()
    plan = story_plan_from_transcript(transcript, deliverable_id="dlv_test00000001", beats=3)
    assert len(plan.beats) == 3
    for beat in plan.beats:
        assert beat.measured_start_ms is not None and beat.measured_end_ms is not None
        # planned_duration_ms is what the shot planner reads, so it has to carry the measurement
        # too — a plan with only the measured pair gives every drawing the default length.
        assert beat.planned_duration_ms == beat.measured_end_ms - beat.measured_start_ms
        assert beat.words, "a beat with no words cannot have its audio cut out of the recording"
    assert len(plan.scenes) == 3
    # One scene kind, chosen for its camera move and because it needs no stored asset: the picture
    # for this beat does not exist yet.
    assert {s.kind for s in plan.scenes} == {"callout"}


def test_beat_spans_locate_each_beat_in_the_recording() -> None:
    transcript = _transcript()
    plan = story_plan_from_transcript(transcript, deliverable_id="dlv_test00000001", beats=3)
    spans = beat_spans(transcript, plan)
    assert len(spans) == 3
    ordered = [spans[b.beat_id] for b in sorted(plan.beats, key=lambda b: b.order)]
    for (_s0, e0), (s1, _e1) in itertools.pairwise(ordered):
        assert e0 <= s1, "beats must not overlap in the recording"


def test_a_beat_whose_words_were_tidied_up_still_finds_its_audio() -> None:
    """An editor who rewrites one word must not cost the beat its audio: the walk skips a word it
    cannot match instead of giving up on the beat."""
    transcript = _transcript()
    plan = story_plan_from_transcript(transcript, deliverable_id="dlv_test00000001", beats=3)
    edited = plan.model_copy(
        update={
            "beats": tuple(
                b.model_copy(update={"display_text": b.display_text.replace("grandmother", "gran")})
                for b in plan.beats
            )
        }
    )
    assert beat_spans(transcript, edited) == beat_spans(transcript, plan)


def test_a_beat_the_recording_never_says_is_reported_by_name() -> None:
    transcript = _transcript()
    plan = story_plan_from_transcript(transcript, deliverable_id="dlv_test00000001", beats=1)
    invented = plan.model_copy(
        update={
            "beats": (
                plan.beats[0].model_copy(
                    update={
                        "display_text": "quarterly revenue, northern region",
                        "measured_start_ms": None,
                        "measured_end_ms": None,
                        "words": (),
                    }
                ),
            )
        }
    )
    with pytest.raises(TakeError, match="beat_000000001"):
        beat_spans(transcript, invented)


def test_the_fixture_transcriber_says_its_timings_are_estimated(tmp_path: Path) -> None:
    wav = _recording(tmp_path / "talk.wav", seconds=6)
    result = transcribe(wav, engine="fixture", fixture_text=TALK)
    assert result.timing_source is TimingSource.estimated
    assert result.engine == "fixture"
    assert len(result.words) == len(TALK.split())
    assert result.words[-1].end_ms <= result.duration_ms
    assert result.duration_ms == pytest.approx(6000, abs=50)


def test_the_fixture_transcriber_refuses_to_invent_words(tmp_path: Path) -> None:
    wav = _recording(tmp_path / "talk.wav", seconds=2)
    with pytest.raises(TakeError, match="needs the transcript text"):
        transcribe(wav, engine="fixture")


# --- the stages -------------------------------------------------------------------------------


def _run_to_transcript(tmp_path: Path, *, name: str = "talk.wav", seconds: float = 12.0):
    ctx = make_context(project_dir=tmp_path / "prj")
    _recording(ctx.project_dir / "uploads" / name, seconds=seconds)
    params = {"engine": "fixture", "transcript": TALK}
    from dataclasses import replace

    out = stage_transcribe_audio(replace(ctx, params=params))
    return ctx, out


def test_transcribe_audio_reads_the_recording_out_of_the_uploads_folder(tmp_path: Path) -> None:
    ctx, out = _run_to_transcript(tmp_path)
    audio = ctx.ddir() / "audio"
    assert (audio / "source.wav").is_file(), "the recording is normalised once, here"
    assert (audio / "transcript.txt").read_text().startswith("My grandmother")
    doc = SpeechTranscript.model_validate_json((audio / "transcript.json").read_text())
    assert doc.text.split() == TALK.split()
    assert out.facts["words"] == len(TALK.split())
    assert out.facts["sentences"] == 6
    assert out.facts["source"] == "talk.wav"
    assert out.facts["cache_hit"] is False


def test_transcribing_the_same_recording_twice_transcribes_it_once(tmp_path: Path) -> None:
    from dataclasses import replace

    ctx, first = _run_to_transcript(tmp_path)
    second = stage_transcribe_audio(replace(ctx, params={"engine": "fixture", "transcript": TALK}))
    assert second.facts["cache_hit"] is True
    assert second.outputs_hash == first.outputs_hash


def test_a_lane_with_no_recording_says_where_to_put_one(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    with pytest.raises(RuntimeError, match="--input"):
        stage_transcribe_audio(ctx)


def _recording_in_an_mp4(path: Path, *, seconds: float = 12.0, moving: bool = False) -> Path:
    """Sound in a video container: what a phone, a voice-memo app or a meeting tool hands over.

    ``moving`` makes it an actual film instead — the same container, and the other answer."""
    import subprocess

    picture = (
        f"testsrc=size=320x240:rate=25:duration={seconds}"
        if moving
        else f"color=c=black:s=320x240:r=25:d={seconds}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", picture,
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    return path


def test_a_recording_that_arrived_as_an_mp4_is_read_rather_than_refused(tmp_path: Path) -> None:
    """The operator's file is an MP4 because their recorder writes MP4, and the picture in it is
    an hour of black. Refusing it for its container was the pipeline declining to read a file it
    reads perfectly well — and the folder is where they were told to put a recording."""
    from dataclasses import replace

    ctx = make_context(project_dir=tmp_path / "prj")
    _recording_in_an_mp4(ctx.project_dir / "uploads" / "voice-memo.mp4")

    out = stage_transcribe_audio(replace(ctx, params={"engine": "fixture", "transcript": TALK}))

    assert out.facts["source"] == "voice-memo.mp4"
    # The stage read a file its own suffix list calls video, so the log carries the measurement
    # that decided otherwise rather than leaving it to be rediscovered.
    assert "black" in str(out.facts["picture"])
    assert (ctx.ddir() / "audio" / "source.wav").is_file(), "normalised like any other recording"


def test_a_film_in_the_uploads_folder_is_still_passed_over_and_said_so(tmp_path: Path) -> None:
    """The container is not the licence — the picture is. A clip with something in it is not this
    lane's material, and throwing its picture away silently would be the worse answer."""
    from dataclasses import replace

    ctx = make_context(project_dir=tmp_path / "prj")
    _recording_in_an_mp4(ctx.project_dir / "uploads" / "holiday.mp4", moving=True)

    with pytest.raises(RuntimeError, match=r"holiday\.mp4 is there, but carries picture"):
        stage_transcribe_audio(replace(ctx, params={"engine": "fixture", "transcript": TALK}))


def test_a_plain_recording_beside_a_clip_wins_without_the_clip_being_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder holding an interview and a clip is not ambiguous, and decoding the clip to find
    that out is work done to learn nothing."""
    from dataclasses import replace

    from content_factory.workflows import stages

    ctx = make_context(project_dir=tmp_path / "prj")
    _recording(ctx.project_dir / "uploads" / "talk.wav", seconds=12)
    _recording_in_an_mp4(ctx.project_dir / "uploads" / "holiday.mp4", moving=True, seconds=2)
    monkeypatch.setattr(
        stages, "_blank_pictured", lambda paths: pytest.fail("the clip should not be probed")
    )

    out = stage_transcribe_audio(replace(ctx, params={"engine": "fixture", "transcript": TALK}))

    assert out.facts["source"] == "talk.wav"
    assert "picture" not in out.facts


def test_two_recordings_is_a_refusal_and_not_a_coin_flip(tmp_path: Path) -> None:
    from dataclasses import replace

    ctx = make_context(project_dir=tmp_path / "prj")
    _recording(ctx.project_dir / "uploads" / "interview-a.wav", seconds=3)
    _recording(ctx.project_dir / "uploads" / "interview-b.wav", seconds=3)
    with pytest.raises(RuntimeError, match="2 recordings"):
        stage_transcribe_audio(replace(ctx, params={"engine": "fixture", "transcript": TALK}))


def test_plan_story_plans_the_film_out_of_what_was_said(tmp_path: Path) -> None:
    from dataclasses import replace

    ctx, _out = _run_to_transcript(tmp_path)
    out = stage_plan_story(replace(ctx, params={"beats": "3"}))
    assert out.facts["planned_from"] == "transcript"
    assert out.facts["beats"] == 3
    plan = json.loads((ctx.project_dir / "story" / "plan.json").read_text())
    said = " ".join(b["display_text"] for b in plan["beats"]).split()
    assert said == TALK.split(), "the beats are the recording, not a paraphrase of it"


def test_a_named_story_fixture_still_wins_over_a_transcript(tmp_path: Path) -> None:
    """Precedence, stated: an operator who names a plan means it. The transcript only wins over
    the *fallbacks* — the script writer and the demo fixture."""
    from dataclasses import replace

    ctx, _out = _run_to_transcript(tmp_path)
    out = stage_plan_story(
        replace(ctx, params={"story": "fixtures/story/last_train.json", "beats": "3"})
    )
    assert "planned_from" not in out.facts


def test_the_voice_is_cut_out_of_the_one_recording(tmp_path: Path) -> None:
    """The whole point: no aligner runs twice, and what lands on disk is the same per-beat layout
    the recorded-take path writes, so everything downstream cannot tell the difference."""
    from dataclasses import replace

    ctx, _out = _run_to_transcript(tmp_path)
    stage_plan_story(replace(ctx, params={"beats": "3"}))
    stage_lock_script(ctx)
    out = stage_voice_over(replace(ctx, params={"source": "recording"}))
    assert out.facts["segments"] == 3
    assert out.facts["cut"] == 3
    assert out.facts["source"] == "recording"
    audio = ctx.ddir() / "audio"
    for index in (1, 2, 3):
        beat = f"beat_{index:09d}"
        assert (audio / f"{beat}.wav").is_file()
        segment = NarrationSegment.model_validate_json((audio / f"{beat}.segment.json").read_text())
        assert segment.voice.provider == "human", "nothing was synthesized"
        assert segment.words, "a segment without word timings cannot be captioned"
        assert segment.words[-1].end_ms <= segment.duration_ms
    # And a rerun cuts nothing again.
    again = stage_voice_over(replace(ctx, params={"source": "recording"}))
    assert again.facts["cut"] == 0


def test_the_captions_of_a_transcribed_recording_pass_their_own_alignment(tmp_path: Path) -> None:
    """The check that would fail if a beat's words overlapped or disagreed with its script — the
    two ways a transcript-driven segment could be wrong and still exist."""
    from dataclasses import replace

    ctx, _out = _run_to_transcript(tmp_path)
    stage_plan_story(replace(ctx, params={"beats": "3"}))
    stage_lock_script(ctx)
    stage_voice_over(replace(ctx, params={"source": "recording"}))
    assert stage_align_words(ctx).facts["reports"] == 3
    cues = stage_compile_captions(ctx)
    assert cues.facts["cues"] >= 3
    assert (ctx.ddir() / "captions" / "captions.srt").read_text().count("-->") >= 3


def test_voice_over_in_recording_mode_names_the_stage_that_has_to_run_first(tmp_path: Path) -> None:
    from dataclasses import replace

    ctx = make_context(project_dir=tmp_path / "prj")
    stage_plan_story(ctx)
    stage_lock_script(ctx)
    with pytest.raises(RuntimeError, match="transcribe_audio"):
        stage_voice_over(replace(ctx, params={"source": "recording"}))
