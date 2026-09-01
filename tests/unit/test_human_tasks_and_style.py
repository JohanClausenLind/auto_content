from __future__ import annotations

from datetime import date
from itertools import pairwise
from pathlib import Path

from content_factory.audio.normalize import normalize_for_speech
from content_factory.audio.tts import MockTTS
from content_factory.human_tasks.validation import validate_take
from content_factory.schemas.audio import NarrationRequest, VoiceIdentity
from content_factory.style.exploration import (
    ExplorationPolicy,
    Observation,
    StyleExperiment,
    should_explore,
)

SCRIPT = "Wind supplied about a fifth of Sweden's electricity in 2025."
VOICE = VoiceIdentity(provider="mock", voice_id="human-take", model_revision="mock-1")


def _wav(tmp_path: Path, text: str) -> Path:
    r = MockTTS().synthesize(
        NarrationRequest(
            beat_id="beat_take000001",
            display_text=text,
            spoken_text=normalize_for_speech(text),
            voice=VOICE,
        )
    )
    p = tmp_path / "take.wav"
    p.write_bytes(r.audio)
    return p


def test_take_validation_accept_improvise_and_rerecord(tmp_path: Path) -> None:
    wav = _wav(tmp_path, SCRIPT)
    exact = validate_take(wav, SCRIPT, transcriber=lambda _p: SCRIPT)
    assert exact.verdict == "accept" and exact.matches_script and exact.similarity >= 0.98
    improvised = validate_take(
        wav,
        SCRIPT,
        transcriber=lambda _p: "Wind supplied roughly a fifth of Sweden's electricity in 2025.",
    )
    assert improvised.verdict == "offer_accept_as_performed" and improvised.matches_script
    assert any(line.startswith("+") for line in improvised.diff) and any(
        line.startswith("-") for line in improvised.diff
    )
    wrong = validate_take(
        wav,
        SCRIPT,
        transcriber=lambda _p: "Totally different words about coffee and mornings, nothing else.",
    )
    assert wrong.verdict == "request_rerecord" and not wrong.matches_script
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not audio at all")
    assert validate_take(junk, SCRIPT, transcriber=lambda _p: SCRIPT).verdict == "reject_format"


def test_exploration_cadence_is_jittered_and_never_consecutive() -> None:
    policy = ExplorationPolicy(rate=0.15, seed="chan-a")
    picks = []
    last = None
    for i in range(400):
        if should_explore(policy, i, last_exploration_index=last):
            picks.append(i)
            last = i
    assert 30 <= len(picks) <= 90  # ~15% with jitter
    gaps = [b - a for a, b in pairwise(picks)]
    assert min(gaps) >= 2  # never two explorations in a row
    assert len(set(gaps)) > 3  # not mechanical
    assert picks != [p for p in range(0, 400, 7)][: len(picks)]


def test_experiment_lifecycle_adopt_requires_samples_and_labels_observational() -> None:
    policy = ExplorationPolicy(min_samples_before_adopt=5, cooldown_days=21)
    exp = StyleExperiment("exp1", "thumbnail", "family-bold", "family-classic", policy)
    d = date(2026, 9, 1)
    for i in range(3):
        exp.record(Observation(f"p{i}", "candidate", 0.10 + 0.01 * i, True, d))
        exp.record(Observation(f"b{i}", "baseline", 0.06, True, d))
    verdict = exp.evaluate(d)
    assert verdict["state"] in {"OBSERVE", "RETEST"} and "keep observing" in verdict["conclusion"]
    assert not exp.can_retest(d + __import__("datetime").timedelta(days=5))
    assert exp.can_retest(d + __import__("datetime").timedelta(days=21))
    for i in range(3, 6):
        exp.record(Observation(f"p{i}", "candidate", 0.11, True, d))
        exp.record(Observation(f"b{i}", "baseline", 0.062, True, d))
    verdict = exp.evaluate(d)
    assert verdict["state"] == "ADOPT"
    assert "observational" in verdict["conclusion"] and verdict["label"] == "observational"
    assert verdict["intervals"]["pooled_spread"] is not None


def test_guardrail_breach_retires_and_noise_is_inconclusive() -> None:
    policy = ExplorationPolicy(min_samples_before_adopt=4)
    exp = StyleExperiment("exp2", "hook", "fam-x", "fam-y", policy)
    d = date(2026, 9, 1)
    for i in range(4):
        exp.record(Observation(f"p{i}", "candidate", 0.08 + (0.001 * (i % 2)), True, d))
        exp.record(Observation(f"b{i}", "baseline", 0.079 + (0.001 * ((i + 1) % 2)), True, d))
    assert exp.evaluate(d)["state"] == "INCONCLUSIVE"
    exp2 = StyleExperiment("exp3", "hook", "fam-x", "fam-y", policy)
    exp2.record(Observation("p0", "candidate", 0.2, False, d))  # guardrail breached
    assert exp2.evaluate(d)["state"] == "RETIRE"
