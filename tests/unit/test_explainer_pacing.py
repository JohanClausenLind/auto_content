"""The voice waits for the picture: pauses where reading time outlasts the narration."""

from __future__ import annotations

import hashlib
import wave
from pathlib import Path

import numpy as np

from content_factory.explainer.narration import STEM_RATE_HZ, pace_stem
from content_factory.explainer.timing import (
    INSPECTION_MS,
    TokenClock,
    paced_manifest,
    reading_ms,
    reading_pauses,
    resolve_timing,
)
from content_factory.schemas.explainer import (
    AlignedWord,
    Alignment,
    Beat,
    Cue,
    Entity,
    HoldAction,
    NarrationManifest,
    NarrationTake,
    Scene,
    ScriptPlan,
    ScriptSegment,
    TargetAction,
    TextItem,
    TextTemplate,
    TitlePromise,
    VisualSpec,
    VoiceSpec,
    tokenize,
)

SHA = hashlib.sha256(b"pacing").hexdigest()
WORD_MS = 300
GAP_MS = 250
FIRST = "seg_pacefirst"
SECOND = "seg_pacesecond"
CAPTION = "A caption long enough that reading it outlasts the words spoken under it by far"


def _script(first: str, second: str) -> ScriptPlan:
    segments = tuple(
        ScriptSegment(segment_id=sid, section="cold_open", spoken_text=text, tokens=tokenize(text))
        for sid, text in ((FIRST, first), (SECOND, second))
    )
    return ScriptPlan(
        script_id="scr_pacing01",
        channel_id="ch_explain01",
        pack_id="pack_pacing01",
        pack_hash=SHA,
        question="Does the voice wait?",
        contribution="Pauses sized from reading time.",
        promises=(
            TitlePromise(title="Pacing", thumbnail_promise="wait", claim_ids=("clm_pacing01",)),
        ),
        segments=segments,
        locked_at="2026-09-26T00:00:00Z",
    )


def _narration(script: ScriptPlan) -> NarrationManifest:
    takes, cursor = [], 0
    for segment in script.segments:
        words = tuple(
            AlignedWord(
                segment_id=segment.segment_id,
                token_index=k,
                start_ms=k * WORD_MS,
                end_ms=k * WORD_MS + WORD_MS - 40,
                confidence=1.0,
            )
            for k in range(len(segment.tokens))
        )
        duration = len(words) * WORD_MS
        takes.append(
            NarrationTake(
                take_id=f"take_{segment.segment_id[4:]}",
                segment_ids=(segment.segment_id,),
                kind="synthesized",
                audio_sha256=SHA,
                start_ms=cursor,
                duration_ms=duration,
                sample_rate_hz=STEM_RATE_HZ,
                alignment=Alignment(aligner="even", aligner_version="1", words=words),
            )
        )
        cursor += duration + GAP_MS
    return NarrationManifest(
        manifest_id="nar_pacing0001",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        voice=VoiceSpec(kind="designed", voice_id="test"),
        takes=tuple(takes),
        total_duration_ms=cursor - GAP_MS,
        created_at="2026-09-26T00:00:00Z",
    )


def _beat(beat_id: str, segment_id: str, token: int, *actions: TargetAction | HoldAction) -> Beat:
    cue = Cue(segment_id=segment_id, token_start=token, token_end=token, duration_class="short")
    return Beat(beat_id=beat_id, cue=cue, actions=actions)


def _scene(scene_id: str, entity_id: str, text: str, beats: tuple[Beat, ...]) -> Scene:
    template = TextTemplate(
        template="text", variant="statement", items=(TextItem(entity_id=entity_id, text=text),)
    )
    return Scene(
        scene_id=scene_id,
        section="cold_open",
        purpose="Pace it",
        template=template,
        beats=beats,
    )


def _spec(next_cue: tuple[str, int]) -> VisualSpec:
    reveal = TargetAction(action="reveal", targets=("ent_caption1",))
    first = _scene(
        "scn_paceone1",
        "ent_caption1",
        CAPTION,
        (
            _beat("bt_paceshow1", FIRST, 0, reveal),
            _beat("bt_pacehold1", FIRST, 1, HoldAction(action="hold")),
        ),
    )
    second = _scene(
        "scn_pacetwo1",
        "ent_second01",
        "Next",
        (
            _beat(
                "bt_pacenext1",
                *next_cue,
                TargetAction(action="reveal", targets=("ent_second01",)),
                HoldAction(action="hold"),
            ),
        ),
    )
    return VisualSpec(
        spec_id="spec_pacing01",
        script_id="scr_pacing01",
        script_hash=SHA,
        pack_hash=SHA,
        design_system_version=1,
        entities=(
            Entity(entity_id="ent_caption1", label="Caption", kind="text"),
            Entity(entity_id="ent_second01", label="Next", kind="text"),
        ),
        scenes=(first, second),
    )


TEXTS = {"scn_paceone1": {"ent_caption1": CAPTION}, "scn_pacetwo1": {"ent_second01": "Next"}}


def _first_action_lag(spec: VisualSpec, script: ScriptPlan, narration: NarrationManifest) -> int:
    clock = TokenClock(script, narration)
    second = resolve_timing(spec, clock, TEXTS)[1]
    return second.actions[0].start_ms - clock.anchor_ms(spec.scenes[1].beats[0].cue)


def test_the_voice_pauses_before_the_next_segment_until_the_caption_is_read() -> None:
    script = _script("Read this caption.", "Then the next scene starts.")
    narration, spec = _narration(script), _spec((SECOND, 0))
    assert _first_action_lag(spec, script, narration) > 0
    pauses = reading_pauses(spec, script, narration, TEXTS)
    caption_read = reading_ms(CAPTION) + INSPECTION_MS
    assert pauses == {(SECOND, 0): caption_read - (3 * WORD_MS + GAP_MS)}
    assert _first_action_lag(spec, script, paced_manifest(narration, pauses, script)) == 0


def test_a_scene_change_inside_a_segment_pauses_at_the_sentence_break() -> None:
    script = _script("Read this caption. Then the next scene starts.", "Done.")
    narration, spec = _narration(script), _spec((FIRST, 4))
    pauses = reading_pauses(spec, script, narration, TEXTS)
    assert list(pauses) == [(FIRST, 3)]
    paced = paced_manifest(narration, pauses, script)
    assert _first_action_lag(spec, script, paced) == 0
    # Words before the break keep their times; the break and everything after move together.
    before, after = paced.takes[0].alignment.words, narration.takes[0].alignment.words  # type: ignore[union-attr]
    assert before[2].start_ms == after[2].start_ms
    assert before[3].start_ms == after[3].start_ms + pauses[FIRST, 3]
    assert paced.takes[1].start_ms == narration.takes[1].start_ms + pauses[FIRST, 3]


def test_no_pause_is_cut_into_the_middle_of_a_sentence() -> None:
    script = _script("Read this caption and then the next scene starts.", "Done.")
    narration, spec = _narration(script), _spec((FIRST, 5))
    assert reading_pauses(spec, script, narration, TEXTS) == {}
    assert _first_action_lag(spec, script, narration) > 0


def test_pacing_nothing_returns_the_same_manifest() -> None:
    script = _script("Read this caption.", "Then the next scene starts.")
    narration = _narration(script)
    assert paced_manifest(narration, {}, script) is narration
    paced = paced_manifest(narration, {(SECOND, 0): 500}, script)
    assert paced.manifest_id != narration.manifest_id
    assert paced.total_duration_ms == narration.total_duration_ms + 500


def test_the_paced_stem_carries_the_same_audio_with_silence_at_each_pause(tmp_path: Path) -> None:
    script = _script("Read this caption. Then the next scene starts.", "Done.")
    narration = _narration(script)
    level = np.zeros(narration.total_duration_ms * STEM_RATE_HZ // 1000, dtype=np.float32)
    for take in narration.takes:
        a, b = (
            ms * STEM_RATE_HZ // 1000 for ms in (take.start_ms, take.start_ms + take.duration_ms)
        )
        level[a:b] = 0.25
    stem = tmp_path / "stem.wav"
    _write(stem, level)
    pauses = {(FIRST, 3): 700, (SECOND, 0): 400}
    paced = paced_manifest(narration, pauses, script)
    pace_stem(stem, narration, paced, pauses, tmp_path / "paced.wav")
    laid = _read(tmp_path / "paced.wav")
    ms = lambda at: int(at * STEM_RATE_HZ / 1000)  # noqa: E731
    assert len(laid) == ms(paced.total_duration_ms)
    cut = (2 * WORD_MS + WORD_MS - 40 + 3 * WORD_MS) // 2
    assert np.abs(laid[ms(cut + 10) : ms(cut + 690)]).max() == 0
    assert np.abs(laid[ms(cut + 710) : ms(cut + 800)]).min() > 0.2
    second = paced.takes[1].start_ms
    assert np.abs(laid[ms(second - 600) : ms(second - 10)]).max() == 0
    assert np.abs(laid[ms(second + 10) : ms(second + 200)]).min() > 0.2
    assert np.count_nonzero(np.abs(laid) > 0.2) == np.count_nonzero(level > 0.2)


def _write(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(STEM_RATE_HZ)
        wf.writeframes((samples * 32767).astype("<i2").tobytes())


def _read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2") / 32768.0
