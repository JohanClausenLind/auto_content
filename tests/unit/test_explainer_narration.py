"""Gates E1 and E2 offline: brief, verify, align, cache key, mixed takes, invalidation, stems."""

from __future__ import annotations

import hashlib
import json
import math
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from content_factory.explainer.compile import compile_episode
from content_factory.explainer.deps import invalidated_by_takes, node_id
from content_factory.explainer.errors import EpisodeInvalidError
from content_factory.explainer.mix import DUCK_DEPTH_DB, duck_curve, mix_episode
from content_factory.explainer.narration import (
    SECTION_GAP_MS,
    SEGMENT_GAP_MS,
    STEM_RATE_HZ,
    align_take,
    assemble_narration,
    brief_markdown,
    recording_brief,
    spoken_words_for_alignment,
    synth_cache_key,
    verify_take,
)
from content_factory.explainer.timing import ESTIMATED_TOKEN_MS, to_frames
from content_factory.schemas.explainer import (
    CategoryCoverage,
    CoverageSpan,
    EvidencePack,
    ExplainerRenderBundle,
    NarrationManifest,
    ReviewedArtifact,
    ReviewerIdentity,
    ReviewReport,
    ScriptPlan,
    ScriptSegment,
    TimeInterval,
    TokenRef,
    VisualSpec,
    VoiceSpec,
    tokenize,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "explainer"
VOICE = VoiceSpec(kind="preset", voice_id="af_heart", model_id="kokoro", model_revision="82M")
SHA = hashlib.sha256(b"fixture").hexdigest()
BARS, SIXTY_SCENE, CARD = "scn_latency_bars", "scn_big_sixty", "scn_amdahl_card"
R1, R2, R3 = "rev_review_01", "rev_review_02", "rev_review_03"


def _trio(name: str) -> tuple[EvidencePack, ScriptPlan, VisualSpec]:
    d = FIXTURES / name
    return (
        EvidencePack.model_validate_json((d / "pack.json").read_text()),
        ScriptPlan.model_validate_json((d / "script.json").read_text()),
        VisualSpec.model_validate_json((d / "spec.json").read_text()),
    )


def _speak(path: Path, words: Sequence[str], duration_ms: int, *, rate: int = STEM_RATE_HZ) -> Path:
    """A tone the length of the line, with the words it 'says' beside it for the fake ASR."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(duration_ms * rate / 1000)) / rate
    samples = 0.3 * np.sin(2 * math.pi * 220 * t)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((samples * 32767).astype("<i2").tobytes())
    path.with_suffix(".json").write_text(json.dumps(list(words)))
    return path


def _duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as wf:
        return round(wf.getnframes() * 1000 / wf.getframerate())


def fake_asr(path: Path) -> list[str]:
    return json.loads(path.with_suffix(".json").read_text())


def fake_aligner(audio: Path, words: Sequence[str]) -> dict[str, Any]:
    """Words spread evenly over the audio, all confident; index 3 unsure when there are 5+ words."""
    duration = _duration_ms(audio)
    step = (duration - 100) / max(1, len(words))
    return {
        "words": [
            {
                "index": i,
                "start_ms": int(50 + i * step),
                "end_ms": int(50 + (i + 1) * step),
                "score": 0.1 if i == 3 and len(words) >= 5 else 0.95,
            }
            for i in range(len(words))
        ],
        "unmatched": [],
        "aligner": "fake",
        "aligner_version": "0",
        "model_id": "test",
    }


class CountingSynth:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, segment: ScriptSegment, voice: VoiceSpec, path: Path) -> None:
        self.calls.append(segment.segment_id)
        _speak(path, segment.tokens, len(segment.tokens) * ESTIMATED_TOKEN_MS)


def _segment(segment_id: str, text: str, **fields: Any) -> ScriptSegment:
    return ScriptSegment(
        segment_id=segment_id,
        section=fields.pop("section", "build_model"),
        spoken_text=text,
        tokens=tokenize(text),
        **fields,
    )


def _with_display_text(script: ScriptPlan, segment_id: str, text: str) -> ScriptPlan:
    segments = tuple(
        s.model_copy(update={"display_text": text}) if s.segment_id == segment_id else s
        for s in script.segments
    )
    return ScriptPlan.model_validate(script.model_copy(update={"segments": segments}).model_dump())


def _review(report_id: str, scene_id: str) -> ReviewReport:
    return ReviewReport(
        report_id=report_id,
        artifact=ReviewedArtifact(kind="mp4", sha256=SHA),
        timeline_id="tl_timeline0",
        reviewer=ReviewerIdentity(
            model_id="reviewer", prompt_sha256=SHA, rubric_version="1", modalities=("video",)
        ),
        coverage=(
            CoverageSpan(
                scene_id=scene_id, interval=TimeInterval(start_ms=0, end_ms=1000), sampled_ms=(0,)
            ),
        ),
        categories=(CategoryCoverage(category="readability", covered_by="frames"),),
        disposition="pass",
        created_at="2026-09-22",
    )


def _assemble(
    script: ScriptPlan, out: Path, takes: dict[str, Path] | None = None
) -> tuple[NarrationManifest, Path, CountingSynth]:
    synth = CountingSynth()
    manifest, stem = assemble_narration(
        script,
        takes or {},
        synth=synth,
        voice=VOICE,
        out_dir=out,
        asr=fake_asr,
        aligner=fake_aligner,
        created_at="2026-09-22T00:00:00Z",
    )
    return manifest, stem, synth


# --- E2: the synthesis cache key ---


def test_cache_key_ignores_display_text() -> None:
    plain = _segment("seg_key_00001", "Your program takes 100 milliseconds.")
    shown = plain.model_copy(update={"display_text": "100 ms"})
    assert synth_cache_key(plain, VOICE) == synth_cache_key(shown, VOICE)


def test_cache_key_changes_with_spoken_text_voice_and_seed() -> None:
    a = _segment("seg_key_00001", "Your program takes 100 milliseconds.")
    b = _segment("seg_key_00001", "Your program takes 60 milliseconds.")
    other_voice = VOICE.model_copy(update={"voice_id": "am_adam"})
    keys = {
        synth_cache_key(a, VOICE),
        synth_cache_key(b, VOICE),
        synth_cache_key(a, other_voice),
        synth_cache_key(a, VOICE, seed=1),
    }
    assert len(keys) == 4


def test_display_text_change_resynthesizes_nothing(tmp_path: Path) -> None:
    _pack, script, _spec = _trio("amdahl")
    first, _, synth = _assemble(script, tmp_path)
    assert synth.calls == [s.segment_id for s in script.segments]
    changed = _with_display_text(script, "seg_cold_open", "100 ms, then 60 ms")
    second, _, again = _assemble(changed, tmp_path)
    assert again.calls == []
    assert [t.audio_sha256 for t in second.takes] == [t.audio_sha256 for t in first.takes]
    assert second.script_hash != first.script_hash


# --- verify (stage 1) ---


def test_dropped_word_is_named_with_its_position(tmp_path: Path) -> None:
    segment = _segment("seg_verify_001", "Your program takes 100 milliseconds.")
    take = _speak(tmp_path / "take.wav", ["Your", "program", "100", "milliseconds"], 2000)
    verdict = verify_take(take, segment, asr=fake_asr)
    assert not verdict.accepted
    assert verdict.similarity < 0.98
    assert [(d.kind, d.script_words, d.position) for d in verdict.deviations] == [
        ("delete", ("takes",), 2)
    ]


def test_numbers_heard_as_digits_or_words_match(tmp_path: Path) -> None:
    segment = _segment("seg_verify_002", "It still takes 60.")
    take = _speak(tmp_path / "take.wav", ["it", "still", "takes", "sixty"], 1500)
    verdict = verify_take(take, segment, asr=fake_asr)
    assert verdict.accepted
    assert verdict.deviations == ()


def test_mismatched_recording_is_refused_with_every_deviation(tmp_path: Path) -> None:
    _pack, script, _spec = _trio("amdahl")
    bad = _speak(tmp_path / "bad.wav", ["It", "still", "takes", "sixty"], 2000)
    with pytest.raises(EpisodeInvalidError) as raised:
        _assemble(script, tmp_path / "out", {"seg_cold_open": bad})
    (issue,) = raised.value.issues
    assert issue.kind == "narration_mismatch"
    assert "re-record segment seg_cold_open" in issue.fix
    assert "delete at token 0" in issue.message


# --- spoken words and alignment (stage 2) ---


def test_spoken_words_spell_numbers_symbols_and_units() -> None:
    tokens = ("takes", "100", "milliseconds.", "80%", "8.4", "1967,", "100ms", "2x", "—", "2,000")
    assert spoken_words_for_alignment(tokens) == [
        *("takes", "one", "hundred", "milliseconds", "eighty", "percent"),
        *("eight", "point", "four", "nineteen", "sixty", "seven"),
        *("one", "hundred", "milliseconds", "two", "times", "two", "thousand"),
    ]


def test_align_take_maps_spoken_words_back_to_tokens(tmp_path: Path) -> None:
    segment = _segment("seg_align_0001", "Takes 100 milliseconds — done.")
    take = _speak(tmp_path / "take.wav", segment.tokens, 2000)
    alignment = align_take(take, [segment], aligner=fake_aligner)
    by_token = {w.token_index: w for w in alignment.words}
    assert sorted(by_token) == [0, 1, 2, 4]
    assert by_token[1].start_ms < by_token[1].end_ms
    assert by_token[1].end_ms <= by_token[2].start_ms
    assert alignment.unmatched == (TokenRef(segment_id="seg_align_0001", token_index=3),)
    assert [t.token_index for t in alignment.low_confidence] == [2]
    assert all(w.end_ms <= 2000 for w in alignment.words)


# --- assembly ---


def test_assembled_stem_places_takes_with_segment_and_section_gaps(tmp_path: Path) -> None:
    _pack, script, _spec = _trio("amdahl")
    recorded = _speak(
        tmp_path / "rec.wav", script.segment("seg_synthesis").tokens, 7000, rate=24000
    )
    manifest, stem = _assemble(script, tmp_path / "out", {"seg_synthesis": recorded})[:2]
    kinds = {t.segment_ids[0]: t.kind for t in manifest.takes}
    assert kinds["seg_synthesis"] == "recorded"
    assert kinds["seg_cold_open"] == "synthesized"
    cursor = 0
    previous = None
    for take, segment in zip(manifest.takes, script.segments, strict=True):
        if previous is not None:
            cursor += SEGMENT_GAP_MS if segment.section == previous else SECTION_GAP_MS
        assert take.start_ms == cursor
        assert take.alignment is not None and len(take.alignment.words) == len(segment.tokens)
        assert take.transcript_similarity == 1.0
        cursor += take.duration_ms
        previous = segment.section
    assert manifest.total_duration_ms == cursor == _duration_ms(stem)
    assert manifest.stem_sha256 == hashlib.sha256(stem.read_bytes()).hexdigest()
    last = manifest.takes[-1]
    assert (last.sample_rate_hz, last.duration_ms) == (24000, 7000)
    assert last.audio_sha256 == hashlib.sha256(recorded.read_bytes()).hexdigest()


# --- E1: a longer take moves only what comes after it ---


def _compile(
    pack: EvidencePack, script: ScriptPlan, spec: VisualSpec, narration: NarrationManifest
) -> ExplainerRenderBundle:
    return compile_episode(pack, script, spec, narration=narration)


def test_longer_take_moves_only_scenes_cued_at_or_after_its_segment(tmp_path: Path) -> None:
    pack, script, spec = _trio("amdahl")
    before_manifest, _, _ = _assemble(script, tmp_path / "a")
    original = next(t for t in before_manifest.takes if t.segment_ids == ("seg_change_variable",))
    longer = _speak(
        tmp_path / "longer.wav",
        script.segment("seg_change_variable").tokens,
        original.duration_ms + 800,
    )
    after_manifest, _, _ = _assemble(script, tmp_path / "b", {"seg_change_variable": longer})
    before = _compile(pack, script, spec, before_manifest).timeline
    after = _compile(pack, script, spec, after_manifest).timeline
    starts_before = {s.scene_id: s.start_frame for s in before.scenes}
    starts_after = {s.scene_id: s.start_frame for s in after.scenes}
    assert starts_after[BARS] == starts_before[BARS] == 0
    assert starts_after[SIXTY_SCENE] > starts_before[SIXTY_SCENE]
    assert starts_after[CARD] - starts_before[CARD] == to_frames(800, before.fps)
    reviews = (_review(R1, BARS), _review(R2, SIXTY_SCENE), _review(R3, CARD))
    invalidated = invalidated_by_takes(
        before_manifest, after_manifest, script, spec, reviews, before=before, after=after
    )
    assert invalidated == {
        node_id("segment", "seg_change_variable"),
        node_id("scene", BARS),
        node_id("scene", SIXTY_SCENE),
        node_id("scene", CARD),
        node_id("review", R1),
        node_id("review", R2),
        node_id("review", R3),
    }


def test_retaking_the_last_cued_segment_invalidates_only_its_scene(tmp_path: Path) -> None:
    pack, script, spec = _trio("amdahl")
    before_manifest, _, _ = _assemble(script, tmp_path / "a")
    original = next(t for t in before_manifest.takes if t.segment_ids == ("seg_show_limits",))
    retake = _speak(
        tmp_path / "retake.wav",
        script.segment("seg_show_limits").tokens,
        original.duration_ms + 100,
    )
    after_manifest, _, _ = _assemble(script, tmp_path / "b", {"seg_show_limits": retake})
    before = _compile(pack, script, spec, before_manifest).timeline
    after = _compile(pack, script, spec, after_manifest).timeline
    reviews = (_review(R1, BARS), _review(R2, SIXTY_SCENE), _review(R3, CARD))
    invalidated = invalidated_by_takes(
        before_manifest, after_manifest, script, spec, reviews, before=before, after=after
    )
    assert invalidated == {
        node_id("segment", "seg_show_limits"),
        node_id("scene", CARD),
        node_id("review", R3),
    }


def test_unchanged_takes_invalidate_nothing(tmp_path: Path) -> None:
    pack, script, spec = _trio("amdahl")
    manifest, _, _ = _assemble(script, tmp_path)
    timeline = _compile(pack, script, spec, manifest).timeline
    reviews = (_review(R1, BARS),)
    assert (
        invalidated_by_takes(
            manifest, manifest, script, spec, reviews, before=timeline, after=timeline
        )
        == frozenset()
    )


# --- recording brief ---


def test_sixty_brief_reports_its_total_against_the_hypothesis_range() -> None:
    pack, script, _spec = _trio("sixty")
    brief = recording_brief(script, pack)
    assert [p.role for p in brief.passages] == ["hook", "observation", "conclusion"]
    assert brief.passages[0].segment_ids == ("seg_coldopen1",)
    assert brief.passages[-1].segment_ids == ("seg_synth0001",)
    assert brief.total_words == sum(p.word_count for p in brief.passages)
    assert brief.total_seconds == round(brief.total_words / 2.6, 1)
    assert brief.in_hypothesis_range is (60 <= brief.total_seconds <= 90)
    text = brief_markdown(brief)
    verdict = "inside" if brief.in_hypothesis_range else "outside"
    assert f"about {brief.total_seconds:.0f} s at 2.6 words/s, {verdict} the 60-90 s" in text
    assert brief.memo_prompt is not None and brief.memo_prompt in text


def test_brief_falls_back_to_the_first_run_or_change_segment(tmp_path: Path) -> None:
    pack, script, _spec = _trio("amdahl")
    assert not any(c.consequential for c in pack.claims)
    brief = recording_brief(script, pack, memo=False)
    observation = next(p for p in brief.passages if p.role == "observation")
    assert observation.segment_ids == ("seg_change_variable",)
    assert brief.memo_prompt is None


def test_brief_picks_the_segment_with_the_most_consequential_claims() -> None:
    pack, script, _spec = _trio("amdahl")
    claims = tuple(
        c.model_copy(update={"consequential": c.claim_id == "clm_amdahl_1967"}) for c in pack.claims
    )
    marked = EvidencePack.model_validate(pack.model_copy(update={"claims": claims}).model_dump())
    brief = recording_brief(script, marked)
    observation = next(p for p in brief.passages if p.role == "observation")
    assert observation.segment_ids == ("seg_show_limits",)
    assert observation.claims == (marked.claim("clm_amdahl_1967").statement,)


# --- stems and the master ---


def test_duck_curve_holds_the_depth_under_speech_and_recovers_after() -> None:
    rate = 48000
    voice = np.zeros(3 * rate, dtype=np.float32)
    t = np.arange(rate) / rate
    voice[rate // 2 : rate // 2 + rate] = 0.3 * np.sin(2 * math.pi * 220 * t)
    gain = duck_curve(voice, rate)
    assert gain[int(0.2 * rate)] == pytest.approx(1.0)
    assert gain[int(1.4 * rate)] == pytest.approx(10 ** (DUCK_DEPTH_DB / 20), abs=0.02)
    assert gain[int(2.9 * rate)] > 0.85


def test_mix_episode_writes_three_stems_and_a_measured_master(tmp_path: Path) -> None:
    rate = STEM_RATE_HZ
    t = np.arange(rate) / rate
    burst = 0.3 * np.sin(2 * math.pi * 220 * t)
    voice = np.concatenate([np.zeros(rate), burst, np.zeros(rate), burst, np.zeros(rate)])
    stem = tmp_path / "stem.wav"
    _write(stem, voice, rate)
    music = tmp_path / "music.wav"
    _write(music, 0.5 * np.sin(2 * math.pi * 110 * t), rate)
    click = tmp_path / "click.wav"
    _write(click, 0.8 * np.sin(2 * math.pi * 1000 * t[: rate // 10]), rate)
    result = mix_episode(stem, music, [(2500, click)], tmp_path / "mix")
    for path in (result.narration_stem, result.music_stem, result.sfx_stem, result.master):
        assert path.exists() and _duration_ms(path) == 5000
    assert not (tmp_path / "mix" / "premix.wav").exists()
    report = result.loudness
    assert math.isfinite(report.integrated_lufs) and math.isfinite(report.true_peak_dbtp)
    assert report.target_lufs == -14.0 and report.target_true_peak_dbtp == -1.0
    assert report.true_peak_dbtp <= -0.9
    bed = _read(result.music_stem)
    under_speech = np.sqrt((bed[int(1.5 * rate) : int(1.9 * rate)] ** 2).mean())
    in_the_gap = np.sqrt((bed[int(0.1 * rate) : int(0.5 * rate)] ** 2).mean())
    assert 20 * math.log10(under_speech / in_the_gap) == pytest.approx(DUCK_DEPTH_DB, abs=1.0)
    effects = _read(result.sfx_stem)
    assert np.abs(effects[int(2.55 * rate)]) > 0.1 and np.abs(effects[: int(2.4 * rate)]).max() == 0


def test_recorded_speech_under_a_bed_masters_to_the_delivery_loudness(tmp_path: Path) -> None:
    speech = _read(FIXTURES / "narration" / "kokoro_sixty.wav")
    stem = tmp_path / "stem.wav"
    _write(stem, np.tile(np.concatenate([speech, np.zeros(4800)]), 10), 16000)
    music = tmp_path / "music.wav"
    t = np.arange(STEM_RATE_HZ) / STEM_RATE_HZ
    _write(music, 0.5 * np.sin(2 * math.pi * 110 * t), STEM_RATE_HZ)
    report = mix_episode(stem, music, [], tmp_path / "mix").loudness
    assert report.integrated_lufs == pytest.approx(-14.0, abs=0.3)
    assert report.true_peak_dbtp <= -1.0


def _write(path: Path, samples: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def _read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
