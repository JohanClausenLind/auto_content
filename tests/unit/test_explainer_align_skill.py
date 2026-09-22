"""The align skill on the Kokoro fixture: word times in order, inside the audio, near Kokoro's."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest

from content_factory.explainer.narration import ALIGN_MODEL_DIR, ALIGN_SKILL, align_take
from content_factory.schemas.explainer import ScriptSegment, tokenize

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "explainer" / "narration"
KOKORO_TOLERANCE_MS = 200

pytestmark = pytest.mark.skipif(
    not (ALIGN_SKILL / ".venv").is_dir() or not (ALIGN_MODEL_DIR / "model.safetensors").exists(),
    reason="align skill environment or wav2vec2 weights not installed",
)


def test_kokoro_sentence_aligns_in_order_inside_the_audio() -> None:
    facts = json.loads((FIXTURE / "kokoro_sixty.json").read_text())
    segment = ScriptSegment(
        segment_id="seg_kokoro_060",
        section="cold_open",
        spoken_text="It still takes 60.",
        tokens=tokenize("It still takes 60."),
    )
    alignment = align_take(FIXTURE / "kokoro_sixty.wav", [segment])
    assert alignment.aligner == "torchaudio.forced_align"
    assert alignment.model_id == "facebook/wav2vec2-base-960h"
    assert alignment.unmatched == () and alignment.low_confidence == ()
    words = alignment.words
    assert [w.token_index for w in words] == [0, 1, 2, 3]
    assert all(w.start_ms < w.end_ms for w in words)
    assert all(a.end_ms <= b.start_ms for a, b in pairwise(words))
    assert words[0].start_ms >= 0 and words[-1].end_ms <= facts["duration_ms"]
    spoken = [t for t in facts["kokoro_tokens_ms"] if t["text"] != "."]
    for word, kokoro in zip(words, spoken, strict=True):
        assert abs(word.start_ms - kokoro["start_ms"]) <= KOKORO_TOLERANCE_MS
